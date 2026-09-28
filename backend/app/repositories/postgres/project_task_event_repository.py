"""Repository for ProjectTaskEvent — append-only task state history."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session, aliased

from app.models.postgres.project_task_event_model import ProjectTaskEvent
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ProjectTaskEventRepository:
    """Append-only store for task state-change events.

    Every call to :meth:`record` inserts a new row and flushes it within
    the open session.  The caller is responsible for committing.
    Rows are never updated or deleted directly — CASCADE handles cleanup
    when the parent ``project_tasks`` row is removed.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        *,
        task_id: UUID,
        project_id: UUID,
        task_type: str,
        status: str,
        progress: int = 0,
        stage: str | None = None,
        meta: dict | None = None,
        error: str | None = None,
    ) -> ProjectTaskEvent:
        """Insert one immutable event row for a task state transition.

        Flushes within the current transaction so the row gets a DB-generated
        ``id`` and ``created_at``.  The caller is responsible for committing.

        Args:
            task_id:    FK to ``project_tasks.id``.
            project_id: Denormalised project reference (no join needed for
                        project-scoped history queries).
            task_type:  Denormalised task type label.
            status:     Status at this point in time.
            progress:   Progress percentage (0–100) at this point.
            stage:      Pipeline stage label at this point.
            meta:       JSONB snapshot of task metadata at this point.
            error:      Error message if the task failed at this point.

        Returns:
            The persisted :class:`~app.models.postgres.project_task_event_model.ProjectTaskEvent`
            instance with ``id`` and ``created_at`` populated.
        """
        event = ProjectTaskEvent(
            task_id=task_id,
            project_id=project_id,
            task_type=task_type,
            status=status,
            progress=progress,
            stage=stage,
            meta=meta,
            error=error,
        )
        self._session.add(event)
        self._session.flush()
        logger.debug(
            "TaskEvent recorded: task_id=%s status=%s stage=%s",
            task_id,
            status,
            stage,
        )
        return event

    def list_by_task(self, task_id: UUID) -> list[ProjectTaskEvent]:
        """Return the full event timeline for a task, oldest first."""
        return (
            self._session.query(ProjectTaskEvent)
            .filter(ProjectTaskEvent.task_id == task_id)
            .order_by(ProjectTaskEvent.created_at.asc())
            .all()
        )

    def list_first_events_per_status_by_tasks(self, task_ids: list[UUID]) -> list[ProjectTaskEvent]:
        """Return the first event recorded for each unique status per task.

        Uses PostgreSQL ``DISTINCT ON (task_id, status)`` ordered by
        ``created_at ASC`` so the *earliest* occurrence of each status is
        selected.  This gives a clean timeline of when each task first
        entered each status — one entry per (task_id, status) pair.

        Args:
            task_ids: List of task UUIDs to fetch events for.

        Returns:
            A flat list of :class:`ProjectTaskEvent` instances — at most
            one per (task_id, status) combination.
        """
        if not task_ids:
            return []
        # Inner: DISTINCT ON (task_id, status) requires ORDER BY to start with
        # those same columns, so created_at comes third here to pick the
        # *earliest* occurrence of each status per task.
        inner = (
            self._session.query(ProjectTaskEvent)
            .distinct(ProjectTaskEvent.task_id, ProjectTaskEvent.status)
            .filter(ProjectTaskEvent.task_id.in_(task_ids))
            .order_by(
                ProjectTaskEvent.task_id,
                ProjectTaskEvent.status,
                ProjectTaskEvent.created_at.asc(),
            )
            .subquery()
        )
        # Outer: re-order the already-distinct rows by task_id, created_at,
        # status so callers see a chronological timeline within each task.
        alias = aliased(ProjectTaskEvent, inner)
        return (
            self._session.query(alias)
            .order_by(inner.c.task_id, inner.c.created_at.asc(), inner.c.status)
            .all()
        )

    def list_by_project(
        self,
        project_id: UUID,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[ProjectTaskEvent]:
        """Return recent events for a project, optionally filtered by status."""
        query = self._session.query(ProjectTaskEvent).filter(
            ProjectTaskEvent.project_id == project_id
        )
        if status is not None:
            query = query.filter(ProjectTaskEvent.status == status)
        return query.order_by(ProjectTaskEvent.created_at.desc()).limit(limit).all()
