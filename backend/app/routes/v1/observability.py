"""Observability endpoints for queue monitoring and system health.

Provides real-time visibility into Celery task queues, active workers,
and system health status for debugging and operational monitoring.

``GET /queue/status`` (admin or super_admin) returns the raw health payload;
``GET /queue/dashboard`` (super_admin only) returns the same underlying data
condensed with stalled-queue alerts for a super-admin dashboard view.

Every handler is plain ``def`` — ``app.utils.queue_monitoring`` functions are
all sync (Redis/Celery ``inspect()`` calls); there is no genuine async I/O
here, so FastAPI runs these in its threadpool automatically (see
``.github/instructions/services.instructions.md``).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.constants import ROLE_SUPER_ADMIN
from app.core.exceptions import ServiceUnavailableError, ValidationError
from app.deps import require_roles
from app.schemas.observability_schema import DeadLetterReplayRequest, DeadLetterReplayResponse
from app.utils.logger import get_logger
from app.utils.queue_monitoring import (
    CeleryHealthStatus,
    QueueDashboardSummary,
    get_celery_health_status,
    get_dead_letter_tasks,
    get_queue_dashboard_summary,
    replay_failed_task,
)
from app.utils.response import ApiResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/queue", tags=["Observability"])
AdminOnly = Annotated[object, Depends(require_roles("admin"))]
SuperAdminOnly = Annotated[object, Depends(require_roles(ROLE_SUPER_ADMIN))]


@router.get(
    "/status",
    summary="Get queue and Celery health status",
    description="Returns real-time queue depths, active workers, and broker availability. Admin only.",
)
def get_queue_status(
    # Require admin role for observability endpoints
    _: AdminOnly = None,
) -> ApiResponse[CeleryHealthStatus]:
    """Get comprehensive queue and Celery health status.

    Returns queue depths for all configured queues, active worker count,
    registered task count, and broker availability status.

    **Access Control:** Requires admin role.

    Returns
    -------
    ApiResponse[CeleryHealthStatus]
        Contains queue depths, worker info, and broker status.
    """
    status = get_celery_health_status()
    logger.info(
        f"Queue status check: broker={status.broker_available}, "
        f"workers={status.active_workers}, "
        f"tasks={status.registered_tasks}"
    )
    return ApiResponse.ok(
        data=status,
        message="Queue status retrieved successfully",
    )


@router.get(
    "/dashboard",
    summary="Get super-admin queue/worker dashboard summary",
    description=(
        "Returns all queue depths and every responding worker's status, plus derived "
        "stalled-queue alerts. Super admin only."
    ),
)
def get_queue_dashboard(
    _: SuperAdminOnly = None,
) -> ApiResponse[QueueDashboardSummary]:
    """Get the condensed queue/worker dashboard view for super admins.

    Same underlying data as ``GET /queue/status`` (one ``inspect()``/Redis
    round trip) plus ``total_queue_depth`` and ``stalled_queues`` — queues
    with pending tasks that no currently-responding worker is subscribed to.

    **Access Control:** Requires super_admin role.

    Returns
    -------
    ApiResponse[QueueDashboardSummary]
    """
    summary = get_queue_dashboard_summary()
    logger.info(
        "Queue dashboard check: workers=%s total_depth=%s stalled=%s",
        summary.active_workers,
        summary.total_queue_depth,
        summary.stalled_queues,
    )
    return ApiResponse.ok(
        data=summary,
        message="Queue dashboard summary retrieved successfully",
    )


@router.get(
    "/dead-letter",
    summary="Get failed tasks from dead-letter queue",
    description="Returns recently failed or rejected tasks. Admin only.",
)
def get_dead_letter_queue(
    max_items: int = 50,
    _: AdminOnly = None,
) -> ApiResponse[list[dict]]:
    """Retrieve recently failed tasks from the dead-letter queue.

    Useful for debugging task failures and understanding why tasks were rejected
    or failed during execution.

    **Access Control:** Requires admin role.

    Query Parameters
    ----------------
    max_items : int
        Maximum number of dead-letter tasks to return (default: 50, max: 500).

    Returns
    -------
    ApiResponse[list[dict]]
        List of failed task records with task_id, status, exception, and traceback.
    """
    max_items = min(max_items, 500)  # Cap at 500 to prevent excessive output
    dlq_tasks = get_dead_letter_tasks(max_items=max_items)
    logger.info(f"Retrieved {len(dlq_tasks)} dead-letter tasks")
    return ApiResponse.ok(
        data=dlq_tasks,
        message=f"Retrieved {len(dlq_tasks)} dead-letter tasks",
    )


@router.post(
    "/dead-letter/replay",
    summary="Replay a failed task",
    description=(
        "Re-queues a failed task for retry using the configured Celery route queue. Admin only."
    ),
)
def replay_dead_letter_task(
    payload: DeadLetterReplayRequest,
    _: AdminOnly = None,
) -> ApiResponse[DeadLetterReplayResponse]:
    """Replay a failed task by publishing it again to Celery.

    The caller provides the original task name and arguments. Queue can be
    omitted to reuse Celery route configuration.
    """
    try:
        replay_result = replay_failed_task(
            task_name=payload.task_name,
            args=payload.args,
            kwargs=payload.kwargs,
            queue=payload.queue,
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    except RuntimeError as exc:
        raise ServiceUnavailableError(str(exc)) from exc

    logger.info(
        "Replayed dead-letter task: task_name=%s replay_task_id=%s queue=%s",
        replay_result.get("task_name"),
        replay_result.get("replay_task_id"),
        replay_result.get("queue"),
    )

    return ApiResponse.ok(
        data=DeadLetterReplayResponse(**replay_result),
        message="Dead-letter task replayed successfully",
    )
