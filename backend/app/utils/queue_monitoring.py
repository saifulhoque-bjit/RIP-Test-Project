"""Queue and task observability utilities for Celery task monitoring.

Provides low-level access to queue depths, task statistics, and health checks
for Celery-based background task processing. Used by observability endpoints
and health checks.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from app.core.celery_app import celery_app
from app.core.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Queue names for task routing
QUEUE_NAMES = [
    "routing",
    "document_parsing",
    "parsing",
    "source_code_parsing",
    "source_code_processing",
    "source_code_persistence",
    "source_code_feature_regeneration",
    "source_code_cleanup",
    "module_feature_generation",
    "module_feature_regeneration",
    "user_story_generation",
    "user_story_feedback_regeneration",
    "incremental_update",
    "notifications",
    "neo4j_sync",
    "celery",  # default queue
]


@dataclass
class QueueStats:
    """Statistics for a single queue."""

    name: str
    depth: int  # Number of pending tasks in queue


@dataclass
class WorkerInfo:
    """Status of a single Celery node (a ``-n`` value, e.g. ``i4_worker1``).

    This is the real unit of "a worker" in this deployment — both server
    (8 nodes spread across 4 instances) and local (the same 8 nodes
    co-located on one box) run the identical set of named nodes, each
    consuming a fixed set of queues with its own concurrency pool. Node
    identity and queue assignment come from ``inspect()`` broadcasts over
    the broker, so this is location-transparent: it reports the same thing
    regardless of which physical host a node happens to run on.
    """

    name: str
    queues: list[str]
    concurrency: int | None
    pool_implementation: str | None
    active_tasks: int
    processed_tasks: int


@dataclass
class CeleryHealthStatus:
    """Overall Celery health status."""

    broker_available: bool
    active_workers: int  # count of distinct Celery nodes that responded
    total_concurrency: int  # sum of each node's --concurrency (parallel task capacity)
    registered_tasks: int
    queues: list[QueueStats]
    workers: list[WorkerInfo]


@dataclass
class QueueDashboardSummary:
    """Super-admin dashboard view: :class:`CeleryHealthStatus` plus derived alerts.

    Built from the same single ``inspect()``/Redis round trip as
    :func:`get_celery_health_status` — no extra broker calls.
    """

    broker_available: bool
    active_workers: int
    total_concurrency: int
    registered_tasks: int
    total_queue_depth: int
    stalled_queues: list[str]  # depth > 0 but no responding worker consumes them
    queues: list[QueueStats]
    workers: list[WorkerInfo]


# Default celery.control.inspect() timeout (1.0s) is tuned for a single
# same-host worker. With several nodes replying over the broker — especially
# across separate server instances — a slow/busy node can miss that window
# and silently drop out of the report. Give replies more room.
_INSPECT_TIMEOUT_SECONDS = 5.0


def get_queue_depths(queue_names: list[str]) -> dict[str, int]:
    """Get pending-task counts for every queue in a single pipelined round trip.

    Parameters
    ----------
    queue_names : list[str]
        Names of the queues to check (e.g., 'parsing', 'module_feature_generation').

    Returns
    -------
    dict[str, int]
        Queue name -> pending count. Every requested name is present;
        -1 for all of them if the broker connection itself failed.
    """
    try:
        from app.core.redis_client import create_sync_redis

        redis_client = create_sync_redis(settings.CELERY_BROKER_URL)
        try:
            pipe = redis_client.pipeline()
            for name in queue_names:
                pipe.llen(name)
            depths = pipe.execute()
        finally:
            redis_client.close()
        return dict(zip(queue_names, depths, strict=False))
    except Exception as e:
        logger.warning(f"Failed to get queue depths: {e}")
        return dict.fromkeys(queue_names, -1)


def _safe_inspect() -> Any:
    """Build a Celery control inspector, or None if the broker is unreachable."""
    try:
        return celery_app.control.inspect(timeout=_INSPECT_TIMEOUT_SECONDS)
    except Exception as e:
        logger.warning(f"Failed to create Celery inspector: {e}")
        return None


def get_worker_details(inspector: Any) -> list[WorkerInfo]:
    """Fetch per-node status: queues consumed, concurrency, and current load.

    Uses ``active_queues()`` (what a node is actually subscribed to right
    now) rather than the static task-routing table, so it reflects reality
    even if a node was started with a different ``-Q`` than expected.
    """
    if inspector is None:
        return []

    try:
        stats = inspector.stats() or {}
        active = inspector.active() or {}
        active_queues = inspector.active_queues() or {}
    except Exception as e:
        logger.warning(f"Failed to fetch worker details: {e}")
        return []

    workers: list[WorkerInfo] = []
    for name, worker_stats in stats.items():
        pool_info = (worker_stats or {}).get("pool") or {}
        queue_entries = active_queues.get(name) or []
        queues = sorted(
            {
                entry.get("name")
                for entry in queue_entries
                if isinstance(entry, dict) and entry.get("name")
            }
        )
        total_processed = (worker_stats or {}).get("total") or {}
        workers.append(
            WorkerInfo(
                name=name,
                queues=queues,
                concurrency=pool_info.get("max-concurrency"),
                pool_implementation=pool_info.get("implementation"),
                active_tasks=len(active.get(name) or []),
                processed_tasks=sum(total_processed.values())
                if isinstance(total_processed, dict)
                else 0,
            )
        )
    return sorted(workers, key=lambda w: w.name)


def is_broker_available() -> bool:
    """Check if the Celery broker (Redis) is accessible.

    Returns
    -------
    bool
        True if broker is available, False otherwise.
    """
    try:
        with celery_app.connection() as conn:
            conn.connect()
        return True
    except Exception as e:
        logger.warning(f"Broker health check failed: {e}")
        return False


def get_celery_health_status() -> CeleryHealthStatus:
    """Get comprehensive Celery health and queue status.

    ``active_workers`` is the count of distinct Celery *nodes* that replied
    (each ``-n`` process — 8 in this deployment, whether spread across 4
    server instances or co-located on one local box) — not thread/pool
    concurrency. ``total_concurrency`` is the sum of each node's own
    ``--concurrency``, i.e. how many tasks can actually run in parallel
    right now. ``workers`` breaks both down per node with its real queue
    assignment and current load.

    Returns
    -------
    CeleryHealthStatus
        Object containing broker availability, per-node worker detail, and
        queue statistics.
    """
    inspector = _safe_inspect()

    if inspector is None:
        workers: list[WorkerInfo] = []
        active_workers = -1
        total_concurrency = -1
        registered_tasks = -1
    else:
        workers = get_worker_details(inspector)
        active_workers = len(workers)
        total_concurrency = sum(worker.concurrency or 0 for worker in workers)
        try:
            registered = inspector.registered() or {}
            registered_tasks = len({task for tasks in registered.values() for task in tasks})
        except Exception as e:
            logger.warning(f"Failed to get registered tasks count: {e}")
            registered_tasks = -1

    depths = get_queue_depths(QUEUE_NAMES)
    queues = [QueueStats(name=name, depth=depths.get(name, -1)) for name in QUEUE_NAMES]

    return CeleryHealthStatus(
        broker_available=is_broker_available(),
        active_workers=active_workers,
        total_concurrency=total_concurrency,
        registered_tasks=registered_tasks,
        queues=queues,
        workers=workers,
    )


def get_queue_dashboard_summary() -> QueueDashboardSummary:
    """Condensed queue/worker health view for a super-admin dashboard.

    Wraps :func:`get_celery_health_status` and adds ``total_queue_depth`` and
    ``stalled_queues`` (queues with pending tasks but no responding worker
    currently subscribed to them) — both derived from data already fetched,
    so this adds no extra broker round trips.

    Returns
    -------
    QueueDashboardSummary
    """
    status = get_celery_health_status()

    consumed_queues = {name for worker in status.workers for name in worker.queues}
    stalled_queues = [
        queue.name
        for queue in status.queues
        if queue.depth > 0 and queue.name not in consumed_queues
    ]
    total_queue_depth = sum(queue.depth for queue in status.queues if queue.depth >= 0)

    return QueueDashboardSummary(
        broker_available=status.broker_available,
        active_workers=status.active_workers,
        total_concurrency=status.total_concurrency,
        registered_tasks=status.registered_tasks,
        total_queue_depth=total_queue_depth,
        stalled_queues=stalled_queues,
        queues=status.queues,
        workers=status.workers,
    )


def get_dead_letter_tasks(max_items: int = 100) -> list[dict[str, Any]]:
    """Retrieve recently failed tasks from dead-letter store (if available).

    This attempts to retrieve failed task information from the Celery result backend
    or Redis directly. Returns empty list if DLQ is not configured or unavailable.

    Parameters
    ----------
    max_items : int
        Maximum number of dead-letter tasks to return.

    Returns
    -------
    list[dict]
        List of failed task records with task_id, task_name, exception, and timestamp.
        Empty list if DLQ is unavailable.
    """
    try:
        from app.core.redis_client import create_sync_redis

        redis_client = create_sync_redis(settings.CELERY_RESULT_BACKEND)
        dlq_tasks: list[dict[str, Any]] = []

        # Use scan_iter instead of KEYS to avoid blocking Redis in production.
        for key in redis_client.scan_iter(match="celery-task-meta-*", count=500):
            if len(dlq_tasks) >= max_items:
                break
            try:
                payload = _read_result_backend_record(redis_client, key)
                if payload.get("status") not in ("FAILURE", "REJECTED"):
                    continue

                raw_traceback = payload.get("traceback", "N/A")
                traceback_text = (
                    raw_traceback if isinstance(raw_traceback, str) else str(raw_traceback)
                )
                dlq_tasks.append(
                    {
                        "task_id": key.replace("celery-task-meta-", ""),
                        "task_name": payload.get("name"),
                        "status": payload.get("status"),
                        "result": payload.get("result", "N/A"),
                        "traceback": traceback_text[:500],
                        "date_done": payload.get("date_done"),
                        "args": payload.get("args")
                        if isinstance(payload.get("args"), list)
                        else [],
                        "kwargs": payload.get("kwargs")
                        if isinstance(payload.get("kwargs"), dict)
                        else {},
                    }
                )
            except Exception as e:
                logger.debug(f"Failed to parse task result key {key}: {e}")

        redis_client.close()
        return dlq_tasks
    except Exception as e:
        logger.warning(f"Failed to retrieve dead-letter tasks: {e}")
        return []


def replay_failed_task(
    task_name: str,
    args: list[Any] | None = None,
    kwargs: dict[str, Any] | None = None,
    queue: str | None = None,
) -> dict[str, str]:
    """Re-queue a failed task for replay.

    Parameters
    ----------
    task_name : str
        Fully-qualified Celery task name (for example: ``tasks.process_source``).
    args : list[Any] | None
        Positional arguments for replayed task.
    kwargs : dict[str, Any] | None
        Keyword arguments for replayed task.
    queue : str | None
        Optional queue override. If omitted, uses configured route queue.

    Returns
    -------
    dict[str, str]
        Contains the new task id, routed queue, and task name.

    Raises
    ------
    ValueError
        If task_name/queue are invalid.
    RuntimeError
        If broker publish fails.
    """
    task_name = task_name.strip()
    if not task_name:
        raise ValueError("task_name must be provided")

    allowed_tasks = set(_get_task_routes().keys())
    if task_name not in allowed_tasks:
        raise ValueError(f"task_name '{task_name}' is not replayable")

    resolved_queue = queue or _route_queue_for_task(task_name)
    if resolved_queue is not None and resolved_queue not in QUEUE_NAMES:
        raise ValueError(f"queue '{resolved_queue}' is not recognized")

    try:
        result = celery_app.send_task(
            task_name,
            args=args or [],
            kwargs=kwargs or {},
            queue=resolved_queue,
        )
    except Exception as exc:
        raise RuntimeError(f"failed to publish replay task: {exc}") from exc

    return {
        "task_name": task_name,
        "replay_task_id": result.id,
        "queue": resolved_queue or "celery",
    }


def _route_queue_for_task(task_name: str) -> str | None:
    """Return configured queue for task name from Celery routing config."""
    route = _get_task_routes().get(task_name)
    if isinstance(route, dict):
        queue = route.get("queue")
        return str(queue) if queue else None
    return None


def _get_task_routes() -> dict[str, Any]:
    """Safely read task route map from Celery config."""
    try:
        routes = celery_app.conf.get("task_routes", {})
    except Exception:
        routes = {}
    return routes if isinstance(routes, dict) else {}


def _read_result_backend_record(redis_client: Any, key: str) -> dict[str, Any]:
    """Read a Celery result-backend record across Redis value types."""
    record_type = redis_client.type(key)

    if record_type == "hash":
        return redis_client.hgetall(key) or {}

    if record_type == "string":
        raw_value = redis_client.get(key)
        if not raw_value:
            return {}
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
        return {}

    return {}
