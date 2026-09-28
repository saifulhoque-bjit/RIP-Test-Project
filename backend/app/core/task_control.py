"""Cooperative-cancellation flag for background tasks.

Celery's own ``revoke()`` only stops a task that is still sitting in the
broker queue — this deployment runs the ``-P threads`` worker pool
(``app/core/celery_app.py``), so there is no OS process to signal once a
task has started, and ``revoke(terminate=True)`` cannot actually stop it.
Every long-running task and pipeline loop therefore checks a cheap Redis flag
at its own checkpoints (task entry, per-module, per-MFU, per generate/critic
cycle) and exits early once it sees the flag set.

Keyed by ``request_id`` (the value every task/subtask spawned from one API
call shares — see ``ProjectTask.request_id``), not by individual Celery task
id, since cancelling "a request" is the unit this feature operates on and a
single flag check then covers every task in that request's subtree.

Uses the app-level ``REDIS_URL`` client (db 0, the same one the WebSocket
pub/sub managers use) rather than the Celery broker/result-backend DBs, to
avoid colliding with ``module_done_count:*`` / ``module_completion_lock:*`` /
``celery-task-meta-*`` keys that already live there
(``app/workers/source_code_task.py``).
"""

from __future__ import annotations

import threading

import redis as sync_redis

from app.core.config import settings
from app.core.constants import LOCK_TTL_SECONDS, TASK_CANCEL_FLAG_TTL_SECONDS
from app.core.redis_client import create_sync_redis
from app.utils.logger import get_logger

logger = get_logger(__name__)

_KEY_PREFIX = "taskctl:cancelled:"

# Module-level client, reused across calls instead of opening a fresh
# connection (+ pool + TCP handshake) on every check. is_request_cancelled is
# polled from inside LLM streaming loops (app/utils/cancellable_llm.py) —
# potentially thousands of times per stream — so a per-call client turns
# every poll into a connect/query/close round trip. create_sync_redis already
# configures keepalive/retry_on_timeout/health-check, so one long-lived
# client recovers from drops on its own; no manual invalidation needed.
#
# Workers run with `-P threads` (see celery_app.py), so multiple threads in
# one process can call _get_client() concurrently on cold start — the lock
# prevents each of them from independently seeing `_client is None` and
# creating (and leaking) its own throwaway client/connection-pool. Same
# double-checked-locking shape as the JWKS cache in app/core/security.py.
_client: sync_redis.Redis | None = None
_client_lock = threading.Lock()


def _get_client() -> sync_redis.Redis:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = create_sync_redis(settings.REDIS_URL)
    return _client


def _key(request_id: object) -> str:
    return f"{_KEY_PREFIX}{request_id}"


def mark_request_cancelled(request_id: object) -> None:
    """Set the cancellation flag for *request_id*.

    Checked cooperatively by every task/loop belonging to that request.
    Never raises — a Redis outage must not prevent the caller from also
    writing the "cancelled" status to Postgres and revoking queued tasks.
    """
    try:
        _get_client().set(_key(request_id), "1", ex=TASK_CANCEL_FLAG_TTL_SECONDS)
    except Exception:
        logger.warning(
            "mark_request_cancelled: failed for request_id=%s", request_id, exc_info=True
        )


def is_request_cancelled(request_id: object) -> bool:
    """Return True if *request_id* has been flagged for cancellation.

    Fails open (returns False) on a Redis error — a transient Redis outage
    must not itself abort in-flight work.
    """
    try:
        return bool(_get_client().exists(_key(request_id)))
    except Exception:
        logger.warning("is_request_cancelled: failed for request_id=%s", request_id, exc_info=True)
        return False


def clear_request_cancelled(request_id: object) -> None:
    """Best-effort cleanup once a request reaches a terminal state normally."""
    try:
        _get_client().delete(_key(request_id))
    except Exception:
        logger.warning(
            "clear_request_cancelled: failed for request_id=%s", request_id, exc_info=True
        )


_CLAIM_KEY_PREFIX = "taskctl:claimed:"


def _claim_key(task_id: object) -> str:
    return f"{_CLAIM_KEY_PREFIX}{task_id}"


def claim_task_execution(task_id: object, ttl_seconds: int = LOCK_TTL_SECONDS) -> bool:
    """Atomically claim *task_id* for execution; return True on the first claim.

    Guards against Celery's at-least-once broker redelivery (worker crash or
    ack timeout) starting a second, independent run of a task that never
    retries at the Celery level — e.g. ``_parse_code_task``
    (``app/workers/source_code_task.py``), which downloads/extracts from S3
    and processes every module again from scratch if simply re-invoked. A
    redelivered invocation carries the same *task_id* (the task's own
    ``ProjectTask`` id, passed as an explicit argument) as the original, so it
    finds the claim already held and can skip instead of duplicating that
    work. *ttl_seconds* defaults to the same 54h ceiling already used for the
    module-completion lock in that file, since that's the legitimate upper
    bound on how long a claim should stay valid.

    Fails open (returns True, i.e. "proceed") on a Redis error — a transient
    Redis outage must not itself block a legitimate run; this is the same
    trade-off :func:`is_request_cancelled` already makes.
    """
    try:
        return bool(_get_client().set(_claim_key(task_id), "1", nx=True, ex=ttl_seconds))
    except Exception:
        logger.warning("claim_task_execution: failed for task_id=%s", task_id, exc_info=True)
        return True


_DELIVERY_KEY_PREFIX = "taskctl:deliveries:"


def _delivery_key(task_id: object) -> str:
    return f"{_DELIVERY_KEY_PREFIX}{task_id}"


def register_delivery_within_limit(
    task_id: object, max_deliveries: int, ttl_seconds: int = LOCK_TTL_SECONDS
) -> bool:
    """Atomically record one more delivery of *task_id*; True while still within bound.

    Poison-loop guard for a task that intentionally allows exactly one
    Celery-level retry (``max_retries=1``): a worker killed mid-task
    (OOM/spot reclaim) never reaches its own ``except`` blocks to call
    ``self.retry()``, so ``task_reject_on_worker_lost`` redelivers the same
    *task_id* at the broker level without ever incrementing
    ``self.request.retries``. An always-OOM module would otherwise loop
    forever. Counting total deliveries — whatever the cause, worker-loss
    redelivery or an application-level retry — and refusing once the count
    exceeds *max_deliveries* closes that gap. Keyed by the task's own id,
    which stays the same across both kinds of redelivery.

    Fails open (returns True) on a Redis error, same trade-off as
    :func:`claim_task_execution`.
    """
    try:
        client = _get_client()
        key = _delivery_key(task_id)
        count = client.incr(key)
        if count == 1:
            client.expire(key, ttl_seconds)
        return count <= max_deliveries
    except Exception:
        logger.warning(
            "register_delivery_within_limit: failed for task_id=%s", task_id, exc_info=True
        )
        return True
