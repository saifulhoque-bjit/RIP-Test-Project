"""Repository for ProjectTask — persisted task-tracking rows."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.core.constants import (
    SOURCE_STATUS_COMPLETED,
    SOURCE_STATUS_FAILED,
    SOURCE_STATUS_READY_FOR_REVIEW,
    TASK_STATUS_CANCELLED,
)
from app.models.postgres.project_model import Project
from app.models.postgres.project_task_model import CreateTaskParams, ProjectTask
from app.repositories.postgres.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Terminal statuses — rows in these states are not "active". Shared across all
# ProjectTask.task_type values: source_process and module_regeneration tasks
# terminate at "ready_for_review"; story_generation, story_regeneration, and
# story_feedback_patch tasks terminate at "completed".
_TERMINAL_STATUSES = frozenset(
    {
        SOURCE_STATUS_COMPLETED,
        SOURCE_STATUS_READY_FOR_REVIEW,
        SOURCE_STATUS_FAILED,
        TASK_STATUS_CANCELLED,
    }
)


class ProjectTaskRepository(BaseRepository[ProjectTask]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, ProjectTask)

    # ── Single-row lookups ─────────────────────────────────────────────────

    def get_by_id(self, task_id: UUID) -> ProjectTask | None:
        """Return a ProjectTask by its UUID, or None."""
        return self._session.query(ProjectTask).filter(ProjectTask.id == task_id).first()

    def get_by_celery_task_id(self, celery_task_id: str) -> ProjectTask | None:
        """Return the ProjectTask whose celery_task_id matches, or None."""
        return (
            self._session.query(ProjectTask)
            .filter(ProjectTask.celery_task_id == celery_task_id)
            .first()
        )

    # ── Project-scoped queries ─────────────────────────────────────────────

    def list_by_project(
        self,
        project_id: UUID,
        *,
        limit: int = 50,
    ) -> list[ProjectTask]:
        """Return the most recent tasks for a project (all statuses)."""
        return (
            self._session.query(ProjectTask)
            .filter(ProjectTask.project_id == project_id)
            .order_by(ProjectTask.created_at.desc())
            .limit(limit)
            .all()
        )

    def list_active_by_project(self, project_id: UUID) -> list[ProjectTask]:
        """Return non-terminal tasks for a project (queued or processing)."""
        return (
            self._session.query(ProjectTask)
            .filter(
                ProjectTask.project_id == project_id,
                ProjectTask.status.notin_(_TERMINAL_STATUSES),
            )
            .order_by(ProjectTask.created_at.desc())
            .all()
        )

    def list_active_by_request_id(self, request_id: UUID) -> list[ProjectTask]:
        """Return non-terminal tasks sharing *request_id* (one API call's fan-out)."""
        return (
            self._session.query(ProjectTask)
            .filter(
                ProjectTask.request_id == request_id,
                ProjectTask.status.notin_(_TERMINAL_STATUSES),
            )
            .order_by(ProjectTask.created_at.desc())
            .all()
        )

    def list_active_by_owner(self, owner_id: UUID) -> list[ProjectTask]:
        """Return non-terminal tasks across every active project owned by *owner_id*.

        Backs the ``/ws/projects/pipelines`` dashboard snapshot, which needs one
        query covering all of a user's projects rather than N per-project
        round trips.
        """
        return (
            self._session.query(ProjectTask)
            .join(Project, Project.id == ProjectTask.project_id)
            .filter(
                Project.owner_id == owner_id,
                Project.deleted_at.is_(None),
                ProjectTask.status.notin_(_TERMINAL_STATUSES),
            )
            .order_by(ProjectTask.created_at.desc())
            .all()
        )

    # ── Updates ────────────────────────────────────────────────────────────

    def update_status(
        self,
        task_id: UUID,
        *,
        status: str,
        progress: int | None = None,
        stage: str | None = None,
        error: str | None = None,
        meta: dict | None = None,
    ) -> ProjectTask | None:
        """Patch status (and optional fields) on a task row.

        Returns the row, or None if the task_id was not found. The caller is
        responsible for committing the session.

        ``cancelled`` is sticky — a cancelled task is never moved to another
        status, and a late event cannot rewrite its stage/progress either.
        Cancellation depends on this: cancelling a source-code run cannot
        interrupt its current LLM call (blocking, non-streaming, 30-minute
        timeout), so the worker keeps running and keeps emitting progress
        events for a while afterwards. Every one of those lands here, and
        without this guard the first would flip the row back to ``running``
        and the run would look alive again.

        Deliberately narrower than ``_TERMINAL_STATUSES``: only ``cancelled``
        is protected, because other terminal values have legitimate onward
        transitions (e.g. ``ready_for_review`` → ``completed``).

        ``meta`` is merged into the existing bag, not replaced wholesale.
        ``create_task`` seeds it with ``{"source_ids": [...]}``
        (``ProjectTaskService._sources_for_task`` depends on that surviving
        for the life of the run to know which Source/SourceIngestion rows a
        cancel must also stamp) but every per-module/progress event from a
        worker also calls this with its own small ``meta`` payload (e.g.
        ``{"source_id": ..., "total_modules": ...}``) — a plain overwrite
        would silently erase ``source_ids`` the moment the first such event
        landed, long before anyone could cancel the run.
        """
        task = self.get_by_id(task_id)
        if task is None:
            return None
        if task.status == TASK_STATUS_CANCELLED and status != TASK_STATUS_CANCELLED:
            logger.debug(
                "update_status: ignoring %s -> %s for task_id=%s (task is cancelled)",
                task.status,
                status,
                task_id,
            )
            return task
        task.status = status
        if progress is not None:
            task.progress = progress
        if stage is not None:
            task.stage = stage
        if error is not None:
            task.error = error[:4000]
        if meta is not None:
            task.meta = {**(task.meta or {}), **meta}
        return task

    def create_task(
        self,
        *,
        task_id: UUID,
        project_id: UUID,
        user_id: UUID | None,
        params: CreateTaskParams,
        request_id: UUID | None = None,
        parent_task_id: UUID | None = None,
    ) -> ProjectTask:
        """Insert a new ProjectTask row using *params* and flush to populate DB defaults.

        ``request_id`` groups this task with every other task spawned by the
        same API call, for request-level cancellation; it defaults to
        ``task_id`` itself for single-task requests. ``parent_task_id``
        records which task (if any) dispatched this one.

        The caller is responsible for committing the session.
        Returns the ORM instance with all DB-generated fields populated.
        """
        task = ProjectTask(
            id=task_id,
            project_id=project_id,
            user_id=user_id,
            task_type=params.task_type,
            request_id=request_id or task_id,
            parent_task_id=parent_task_id,
            status=params.status,
            progress=params.progress,
            stage=params.stage,
            meta=params.meta,
            error=params.error,
        )
        self._session.add(task)
        self._session.flush()
        self._session.refresh(task)
        return task

    def set_celery_task_id(self, task_id: UUID, celery_task_id: str) -> None:
        """Persist the Celery async-result ID on the task row.

        No-op if *task_id* is not found.
        The caller is responsible for committing the session.
        """
        task = self.get_by_id(task_id)
        if task is not None:
            task.celery_task_id = celery_task_id
