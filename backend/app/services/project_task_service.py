"""Service layer for ProjectTask lifecycle management.

Centralises the two operations every task-dispatching flow needs:

1. ``create_task`` — write a ``queued`` ProjectTask row to PostgreSQL
   *before* the Celery task is dispatched so the frontend can query its status
   immediately.
2. ``set_celery_task_id`` — bind the Celery async-result UUID to the row after
   ``apply_async`` returns.

Both methods own their UnitOfWork internally so callers do not need to pass a
session, making the service safe to use from synchronous helpers (e.g.
``_enqueue_processing`` in ``source_service.py``) as well as async service
methods.
"""

from __future__ import annotations

import uuid
from uuid import UUID

from app.core import task_control
from app.core.constants import SOURCE_TYPE_SOURCE_CODE, TASK_STATUS_CANCELLED
from app.core.exceptions import NotFoundError
from app.db.unit_of_work import UnitOfWork
from app.models.postgres.project_task_model import CreateTaskParams, ProjectTask
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Re-export so callers only need to import from this module.
__all__ = ["CreateTaskParams", "ProjectTaskService"]


class ProjectTaskService:
    """Reusable service for creating and updating ProjectTask rows."""

    def create_task(
        self,
        *,
        project_id: UUID,
        user_id: UUID | None,
        params: CreateTaskParams,
        request_id: UUID | None = None,
        parent_task_id: UUID | None = None,
    ) -> tuple[UUID, str]:
        """Create a ProjectTask row using the supplied *params*.

        Generates the task UUID here so it can be forwarded to the Celery
        worker as ``task_db_id`` before the async-result ID is known.

        Args:
            project_id: The project this task belongs to.
            user_id:    The user who triggered the task (``None`` for system
                        tasks).
            params:     A :class:`CreateTaskParams` instance containing all
                        task-specific fields.
            request_id: Groups this task with every other task spawned by the
                        same API call, for request-level cancellation.
                        Defaults to this task's own id (single-task request)
                        when omitted — pass the upload batch id explicitly
                        for multi-task requests like bulk upload.
            parent_task_id: The task that dispatched this one, if any.

        Returns:
            ``(task_id, task_db_id)`` — the raw UUID and its string form.
            Pass ``task_db_id`` to Celery; keep ``task_id`` to call
            :meth:`set_celery_task_id` after dispatch.
        """
        task_id = uuid.uuid4()
        with UnitOfWork() as uow:
            uow.project_tasks.create_task(
                task_id=task_id,
                project_id=project_id,
                user_id=user_id,
                params=params,
                request_id=request_id,
                parent_task_id=parent_task_id,
            )
            # Record the initial "queued" event atomically with the task row.
            uow.task_events.record(
                task_id=task_id,
                project_id=project_id,
                task_type=params.task_type,
                status=params.status,
                progress=params.progress,
                stage=params.stage,
                meta=params.meta,
                error=params.error,
            )
            uow.commit()

        task_db_id = str(task_id)
        logger.debug(
            "ProjectTask created: task_db_id=%s task_type=%s project_id=%s request_id=%s",
            task_db_id,
            params.task_type,
            project_id,
            request_id or task_id,
        )
        return task_id, task_db_id

    def set_celery_task_id(self, task_id: UUID, celery_task_id: str) -> None:
        """Persist the Celery async-result ID back on the task row.

        Called immediately after ``apply_async`` returns so the row is
        complete before any worker status updates arrive.
        """
        with UnitOfWork() as uow:
            uow.project_tasks.set_celery_task_id(task_id, celery_task_id)
            uow.commit()

        logger.debug(
            "ProjectTask celery_task_id set: task_id=%s celery_task_id=%s",
            task_id,
            celery_task_id,
        )

    def cancel_task(self, task_id: UUID) -> dict:
        """Cancel the request that *task_id* belongs to.

        Every task/subtask spawned by one API call shares a ``request_id``
        (see ``ProjectTask.request_id``); cancelling "a task" means
        cancelling the operation it belongs to, so this resolves the task's
        request and delegates to :meth:`cancel_request`.

        Raises:
            NotFoundError: if *task_id* does not exist.
        """
        with UnitOfWork() as uow:
            task = uow.project_tasks.get_by_id(task_id)
            if task is None:
                raise NotFoundError(f"ProjectTask not found: {task_id}")
            request_id = task.request_id
        return self.cancel_request(request_id)

    def cancel_request(self, request_id: UUID) -> dict:
        """Cancel every active task sharing *request_id*, completing the cancel here.

        Cancellation is applied synchronously and is authoritative: by the time
        this returns, every task of the request reads ``cancelled`` and a
        source-code run's output has been rolled back. The caller never waits
        on a worker.

        That matters because a source-code run cannot be interrupted mid-LLM:
        its calls are blocking, non-streaming ``litellm.completion`` calls with
        a 30-minute request timeout, so a worker may not reach its next
        checkpoint for a very long time. Deferring the status change to the
        worker is what previously left the UI sitting on "cancelling"
        indefinitely.

        What still happens asynchronously is only the *stopping*:

        - The Redis cooperative-cancel flag is set, so the surviving worker
          skips persistence at its next checkpoint and stops dispatching
          further work.
        - Each task's Celery id is revoked — a no-op for anything already
          running (this deployment's thread-pool workers can't hard-kill a
          running task; see ``app/core/task_control.py``), but stops a task
          still sitting in the broker queue from ever starting.

        The orphaned worker cannot undo any of this: ``cancelled`` is terminal
        and terminal statuses are sticky (see
        ``ProjectTaskRepository.update_status``).

        Returns a summary dict: ``{"request_id", "cancelled_count"}``.
        """
        return self.cancel_sibling_tasks(request_id)

    def cancel_sibling_tasks(self, request_id: UUID, *, exclude_task_id: UUID | None = None) -> dict:
        """Cancel every active task sharing *request_id*, optionally excluding one.

        This is the implementation :meth:`cancel_request` delegates to
        (``exclude_task_id=None`` — cancel everything). It also backs the RFP
        pipeline's fail-fast path for a non-retryable LLM error (e.g. provider
        billing/credit exhaustion): that caller is already finalizing its own
        task as ``FAILED`` (not ``CANCELLED``), so it passes its own
        ``task_db_id`` as *exclude_task_id* to stop every *other* active task
        sharing the request — such as ``generate_modules_and_features_task``,
        auto-dispatched from ``parse_document_task`` — without double-touching
        (and overwriting the FAILED status of) itself.

        See :meth:`cancel_request` for the synchronous cancellation contract
        (Redis flag + Celery revoke + status rollback). Returns a summary
        dict: ``{"request_id", "cancelled_count"}``.
        """
        from app.core.celery_app import celery_app  # noqa: PLC0415

        task_control.mark_request_cancelled(request_id)

        with UnitOfWork() as uow:
            active = uow.project_tasks.list_active_by_request_id(request_id)
            if exclude_task_id is not None:
                active = [task for task in active if task.id != exclude_task_id]
            # Resolved inside the session — the finalizers below run after it closes.
            sources_by_task = {
                str(task.id): self._sources_for_task(uow, task) for task in active
            }
            for task in active:
                if task.celery_task_id:
                    try:
                        celery_app.control.revoke(task.celery_task_id)
                    except Exception:
                        logger.warning(
                            "cancel_sibling_tasks: revoke failed for celery_task_id=%s",
                            task.celery_task_id,
                            exc_info=True,
                        )
            uow.commit()

        for task in active:
            source_ids, is_source_code = sources_by_task.get(str(task.id), ([], False))
            if is_source_code:
                # Stamps statuses and defers the backlog delete to a worker.
                self._rollback_source_code_run(task, source_ids)
            else:
                self._mark_cancelled(task, source_ids)

        logger.info(
            "cancel_sibling_tasks: request_id=%s excluded=%s cancelled_count=%d",
            request_id,
            exclude_task_id,
            len(active),
        )
        return {"request_id": str(request_id), "cancelled_count": len(active)}

    def cancel_project(self, project_id: UUID) -> dict:
        """Cancel every active request for *project_id* (all task_types).

        Returns ``{"request_ids": [...], "cancelled_count": N}``.
        """
        with UnitOfWork() as uow:
            active = uow.project_tasks.list_active_by_project(project_id)
        request_ids = {task.request_id for task in active}

        total = 0
        for request_id in request_ids:
            result = self.cancel_request(request_id)
            total += result["cancelled_count"]

        return {
            "request_ids": [str(r) for r in request_ids],
            "cancelled_count": total,
        }

    @staticmethod
    def _sources_for_task(uow: UnitOfWork, task: ProjectTask) -> tuple[list[str], bool]:
        """Return ``(source_ids, is_source_code)`` for an ingestion task.

        Every ``source_process`` run owns Source/SourceIngestion rows that must
        be stamped cancelled — otherwise the Pipelines row keeps reading
        "running" forever even though the task is terminal. Only a run over
        ``source_code`` sources additionally has a generated backlog to roll
        back.

        Ids come from the task's ``meta["source_ids"]``, written when the run
        is enqueued (``source_service.py::_enqueue_processing``). A task type
        that owns no sources (regeneration, feedback patches) returns
        ``([], False)`` and is simply marked cancelled.
        """
        if task.task_type != "source_process":
            return [], False

        source_ids = [str(sid) for sid in ((task.meta or {}).get("source_ids") or [])]
        is_source_code = False
        for source_id in source_ids:
            try:
                source = uow.sources.get_by_uuid(UUID(source_id))
            except Exception:
                logger.warning(
                    "cancel_request: could not resolve source_id=%s for task_id=%s",
                    source_id,
                    task.id,
                    exc_info=True,
                )
                continue
            if source is not None and source.source_type == SOURCE_TYPE_SOURCE_CODE:
                is_source_code = True
        return source_ids, is_source_code

    @staticmethod
    def _rollback_source_code_run(task: ProjectTask, source_ids: list[str]) -> None:
        """Stop and roll back a source-code run, synchronously.

        Delegates to the worker module's canonical routine so the cancel path
        and the worker's own checkpoints can never drift: it emits the
        ``cancelled`` task event (which also writes the ProjectTask row),
        marks Source/SourceIngestion cancelled with zeroed counts, and
        dispatches the backlog delete and temp-folder cleanup to workers.
        Never raises.

        The backlog delete is *dispatched*, not awaited — see
        ``_finalize_source_code_cancellation``. This method therefore does no
        Neo4j I/O and returns as fast as the Postgres writes allow.
        """
        from app.workers.source_code_task import (  # noqa: PLC0415
            _finalize_source_code_cancellation,
        )

        _finalize_source_code_cancellation(
            request_id=str(task.request_id) if task.request_id else None,
            task_db_id=str(task.id),
            project_id=str(task.project_id),
            source_ids=source_ids,
        )

    @staticmethod
    def _mark_cancelled(task: ProjectTask, source_ids: list[str]) -> None:
        """Move a task to ``cancelled`` and stamp any sources it owns.

        ``emit_task_event`` writes the ProjectTask row as well as publishing,
        so it is both the status change and the notification.

        Marking the sources matters as much as the task: the Pipelines table
        renders the SourceIngestion status, not the task's. Flipping only the
        task leaves the run reading "running" in the UI forever while the
        task itself is terminal — the run looks alive and can never be
        cancelled again, because it is no longer active.

        Order mirrors ``_finalize_source_code_cancellation``: the sources are
        stamped last, once the task is already terminal.
        """
        from app.workers._task_helpers import (  # noqa: PLC0415
            emit_task_event,
            mark_sources_and_ingestion_cancelled,
        )

        emit_task_event(
            task_db_id=str(task.id),
            project_id=str(task.project_id),
            task_type=task.task_type,
            status=TASK_STATUS_CANCELLED,
            stage="cancelled",
            progress=task.progress,
        )

        if source_ids:
            mark_sources_and_ingestion_cancelled(
                source_ids=source_ids,
                project_id=str(task.project_id),
                task_type=task.task_type,
                stage="cancelled",
                task_db_id=str(task.id),
            )

    def list_tasks_with_events(
        self,
        uow: UnitOfWork,
        project_id: UUID,
        *,
        active: bool = False,
    ) -> list[dict]:
        """Return project tasks with their unique-status event history.

        Fetches tasks for the given project, then bulk-loads the first event
        recorded for each distinct status on each task.  Events are grouped
        by ``task_id`` and attached to the matching task dict so the response
        is built in two DB queries regardless of the number of tasks.

        Args:
            uow:        The active :class:`~app.db.unit_of_work.UnitOfWork`
                        from the request's dependency injection.
            project_id: UUID of the project to query.
            active:     When ``True``, return only non-terminal tasks.

        Returns:
            A list of task dicts, each containing an ``events`` list with one
            entry per unique status (in the order: queued → processing →
            completed/failed).
        """
        if active:
            tasks = uow.project_tasks.list_active_by_project(project_id)
        else:
            tasks = uow.project_tasks.list_by_project(project_id)

        return self._serialize_with_events(uow, tasks)

    def list_active_tasks_by_owner(
        self,
        uow: UnitOfWork,
        owner_id: UUID,
    ) -> list[dict]:
        """Return non-terminal tasks across every project owned by *owner_id*.

        Same per-task shape as :meth:`list_tasks_with_events` (each item
        already carries its own ``project_id``), but scoped to a user's
        entire project portfolio in one query instead of one project. Backs
        the ``/ws/projects/pipelines`` dashboard WebSocket's on-connect snapshot.
        """
        tasks = uow.project_tasks.list_active_by_owner(owner_id)
        return self._serialize_with_events(uow, tasks)

    @staticmethod
    def _serialize_with_events(uow: UnitOfWork, tasks: list[ProjectTask]) -> list[dict]:
        """Attach unique-status event history to *tasks* and serialize to dicts.

        Shared by :meth:`list_tasks_with_events` and
        :meth:`list_active_tasks_by_owner` so the two call sites can never
        drift in shape.
        """
        task_ids = [t.id for t in tasks]
        events = uow.task_events.list_first_events_per_status_by_tasks(task_ids)

        # Group events by task_id — one entry per unique status
        events_by_task: dict[UUID, list[dict]] = {}
        for e in events:
            events_by_task.setdefault(e.task_id, []).append(
                {
                    "status": e.status,
                    "progress": e.progress,
                    "stage": e.stage,
                    "meta": e.meta,
                    "error": e.error,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
            )

        return [
            {
                "task_id": str(t.id),
                "celery_task_id": t.celery_task_id,
                "task_type": t.task_type,
                "project_id": str(t.project_id),
                "user_id": str(t.user_id) if t.user_id else None,
                "status": t.status,
                "progress": t.progress,
                "stage": t.stage,
                "meta": t.meta,
                "error": t.error,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
                "events": events_by_task.get(t.id, []),
            }
            for t in tasks
        ]
