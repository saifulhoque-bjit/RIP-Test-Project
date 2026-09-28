"""Route handlers for project background-task tracking — v1.

Endpoint summary
────────────────
Any authenticated user:
    GET /projects/{project_id}/tasks          — list all tasks with status history
    GET /projects/{project_id}/tasks?active=1 — list only non-terminal tasks
    DELETE /projects/{project_id}/tasks       — cancel every active task for a project
    DELETE /tasks/{task_id}                   — cancel the request a task belongs to
    DELETE /tasks/by-request/{request_id}     — cancel a request by its own id
                                                 (e.g. the `batch_id` returned by
                                                 `POST /sources/upload/bulk`)

Used by the frontend to restore WebSocket state after a page refresh,
multi-tab reconnect, or session storage clear.  Each task in the response
includes an ``events`` list with one entry per unique status the task has
passed through (queued → processing → completed/failed), letting the client
render a progress timeline without a separate request.

Cancellation semantics
───────────────────────
Every task/subtask spawned by one API call shares a ``request_id`` (see
``ProjectTask.request_id``), so cancelling "a task" cancels the whole
operation it belongs to, not just that one row.

The status change is immediate and final: the row is written ``cancelled``
synchronously, and a source-code run's generated backlog is rolled back in
the same call. Stopping the work is what remains cooperative — this
deployment's Celery workers run the thread pool, which cannot hard-terminate
a task that has already started, and the source-code pipeline's LLM calls are
blocking and non-streaming, so a running task may keep going for some time.
It is contained rather than waited on: it skips persistence at its next
checkpoint (see ``app/core/task_control.py``), and ``cancelled`` is sticky, so
nothing it does afterwards can revive the run.

Every handler here is a plain ``def``, deliberately — none of them awaits
anything, and the cancel path in particular does a lot of blocking I/O
inline (Postgres, a Neo4j backlog delete, Redis, a Celery revoke broadcast).
Declared ``async def``, all of that would run *on the event loop*, freezing
the whole API — including the WebSocket consumer that delivers the
``cancelled`` event the cancel just published, so the UI could not observe
the cancellation until the call it is waiting on had already finished. As
plain ``def`` FastAPI runs them in its threadpool instead. See the
sync-vs-async rule in ``CLAUDE.md``; ``test_project_tasks_cancel_routes.py``
guards this.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.db.unit_of_work import UnitOfWork
from app.deps import get_current_db_user, get_project_task_service, get_uow
from app.models.postgres.user_model import User
from app.services.project_task_service import ProjectTaskService
from app.utils.logger import get_logger
from app.utils.response import ApiResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/projects", tags=["Project Tasks"])
tasks_router = APIRouter(prefix="/tasks", tags=["Project Tasks"])


# ── Annotated dependency aliases ───────────────────────────────────────────

CurrentUser = Annotated[User, Depends(get_current_db_user)]
CurrentUow = Annotated[UnitOfWork, Depends(get_uow)]
ActiveFilter = Annotated[
    bool, Query(description="When true, return only non-terminal (queued / processing) tasks.")
]
TaskService = Annotated[ProjectTaskService, Depends(get_project_task_service)]


# ── Endpoints ──────────────────────────────────────────────────────────────


@router.get(
    "/{project_id}/tasks",
    status_code=status.HTTP_200_OK,
    summary="List background tasks for a project",
    description=(
        "Returns up to 50 recent tasks for the project. "
        "Each task includes an `events` array showing the first occurrence "
        "of each unique status (queued, processing, completed, failed) — "
        "useful for rendering a progress timeline. "
        "Pass `active=true` to receive only non-terminal tasks."
    ),
)
def list_project_tasks(
    project_id: UUID,
    active: ActiveFilter = False,
    uow: CurrentUow = None,
    _current_user: CurrentUser = None,
    service: TaskService = None,
) -> ApiResponse:
    """GET /projects/{project_id}/tasks — list background tasks with event history."""
    tasks = service.list_tasks_with_events(uow, project_id, active=active)
    return ApiResponse.ok(data={"tasks": tasks, "total": len(tasks)})


@router.delete(
    "/{project_id}/tasks",
    status_code=status.HTTP_200_OK,
    summary="Cancel every active background task for a project",
    description=(
        "Cancels every non-terminal task across every request for this "
        "project — the broadest cancellation scope. Tasks read `cancelled` as "
        "soon as this returns; queued Celery tasks are revoked outright, and a "
        "task already running has its results discarded rather than being "
        "waited on."
    ),
)
def cancel_project_tasks(
    project_id: UUID,
    _current_user: CurrentUser = None,
    service: TaskService = None,
) -> ApiResponse:
    """DELETE /projects/{project_id}/tasks — cancel all active tasks for a project."""
    result = service.cancel_project(project_id)
    return ApiResponse.ok(data=result)


@tasks_router.delete(
    "/{task_id}",
    status_code=status.HTTP_200_OK,
    summary="Cancel a task's request",
    description=(
        "Cancels the whole request that `task_id` belongs to (every "
        "task/subtask spawned by the same original API call), not just that "
        "single row. Cancellation is applied synchronously: the row(s) read "
        "`cancelled` as soon as this returns, and a source-code run's "
        "generated backlog is rolled back before it does. Queued Celery tasks "
        "are revoked outright; a task already running is left to finish its "
        "current step, but its results are discarded and it cannot move the "
        "run out of `cancelled`."
    ),
)
def cancel_task(
    task_id: UUID,
    _current_user: CurrentUser = None,
    service: TaskService = None,
) -> ApiResponse:
    """DELETE /tasks/{task_id} — cancel the request a task belongs to."""
    result = service.cancel_task(task_id)
    return ApiResponse.ok(data=result)


@tasks_router.delete(
    "/by-request/{request_id}",
    status_code=status.HTTP_200_OK,
    summary="Cancel a request by its own id",
    description=(
        "Cancels every task/subtask sharing `request_id`. For "
        "`POST /sources/upload/bulk`, `request_id` is the `batch_id` "
        "returned in that endpoint's response — use this when the client "
        "never learned the underlying task id (bulk upload does not return "
        "one)."
    ),
)
def cancel_task_request(
    request_id: UUID,
    _current_user: CurrentUser = None,
    service: TaskService = None,
) -> ApiResponse:
    """DELETE /tasks/by-request/{request_id} — cancel a request by its own id."""
    result = service.cancel_request(request_id)
    return ApiResponse.ok(data=result)
