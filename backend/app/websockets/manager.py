"""Unified WebSocket connection manager backed by Redis pub/sub.

Architecture
────────────
``/ws/projects/{project_id}`` is the real-time channel for one project's
task events between the backend and the browser.  All task types
(source processing, module regeneration, story generation, story
regeneration) publish to the same per-project Redis channel:

    project:tasks:{project_id}

Celery workers call ``publish_task_event_sync`` which:

  1. Updates the ``project_tasks`` row in PostgreSQL (progress + status).
  2. Publishes the unified JSON event to the per-project Redis channel so
     all clients connected to that project receive it immediately, *and*
     to the owning user's ``project:status:{owner_id}`` channel (same
     event, no re-derivation) so a single ``/ws/projects/pipelines`` dashboard
     connection sees status changes across that user's whole project
     portfolio — see ``app/websockets/project_status_manager.py`` and
     ``app/websockets/project_status_ws.py``.

Event schema
────────────
    {
        "event":          "task.update",
        "task_id":        "<ProjectTask.id UUID>",
        "celery_task_id": "<Celery task UUID>",
        "task_type":      "source_process | module_regeneration | story_generation | story_regeneration",
        "project_id":     "<UUID>",
        "status":         "source_process: queued | processing | running | ready_for_review | failed"
                          " (module_regeneration/story_generation/story_regeneration: queued | running | completed | failed)",
        "progress":       0-100,
        "stage":          "free-text pipeline stage, e.g. source.process.queued | documents.parsing.started | user_story.generation.completed",
        "meta":           {...},
        "error":          "...",
        "timestamp":      "ISO-8601"
    }

On connect the WebSocket handler sends a ``tasks.current`` envelope
containing all non-terminal tasks retrieved from PostgreSQL so the
client can recover after a page refresh or storage clear.

Thread safety
─────────────
All mutable state (_connections) is manipulated only from the FastAPI
async event loop.  asyncio.Lock guards the per-project connection sets.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime
import json
from typing import Any
from uuid import UUID

from fastapi import WebSocket
import redis as sync_redis
import redis.asyncio as aioredis

from app.core.config import settings
from app.core.redis_client import create_async_redis, create_sync_redis
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ── Redis channel naming ───────────────────────────────────────────────────

_PROJECT_TASKS_CHANNEL_PREFIX = "project:tasks:"
_PROJECT_STATUS_CHANNEL_PREFIX = "project:status:"


def project_tasks_channel(project_id: str) -> str:
    """Return the Redis pub/sub channel for all task events in a project."""
    return f"{_PROJECT_TASKS_CHANNEL_PREFIX}{project_id}"


def project_status_channel(owner_id: str) -> str:
    """Return the Redis pub/sub channel for a project owner's dashboard feed.

    Every ``task.update`` event for any project owned by *owner_id* is also
    published here (in addition to its per-project ``project:tasks:{id}``
    channel) so ``/ws/projects/pipelines`` can fan out live status across a
    user's whole project portfolio from a single connection, instead of the
    client opening one ``/ws/projects/{project_id}`` socket per project. See
    ``app/websockets/project_status_manager.py``.
    """
    return f"{_PROJECT_STATUS_CHANNEL_PREFIX}{owner_id}"


# ── Manager ────────────────────────────────────────────────────────────────


class ProjectTaskWebSocketManager:
    """Manages WebSocket connections and Redis pub/sub for project task events."""

    def __init__(self) -> None:
        # project_id → {websocket, …}
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        # project_id → asyncio.Task running the listener loop
        self._listeners: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._redis: aioredis.Redis | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def startup(self) -> None:
        """Create and verify the async Redis connection. Called from lifespan."""
        self._redis = create_async_redis(settings.REDIS_URL)
        await self._redis.ping()
        logger.info("ProjectTaskWebSocketManager: Redis connected")

    async def shutdown(self) -> None:
        """Cancel all listener tasks and close Redis. Called from lifespan."""
        tasks = list(self._listeners.values())
        for task in tasks:
            task.cancel()
        if tasks:
            # Wait for listener cleanup (pubsub unsubscribe/close) to run
            # before tearing down the shared Redis connection below, so
            # cancellation doesn't race the connection close and leave
            # "Task was destroyed but it is pending" warnings on shutdown.
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                logger.warning("ProjectTaskWebSocketManager: error closing Redis", exc_info=True)
        logger.info("ProjectTaskWebSocketManager: shutdown complete")

    # ── Connection management ──────────────────────────────────────────────

    async def connect(self, websocket: WebSocket, project_id: str) -> None:
        """Register a new WebSocket connection for a project."""
        await websocket.accept()
        async with self._lock:
            self._connections[project_id].add(websocket)
            if project_id not in self._listeners:
                self._listeners[project_id] = asyncio.create_task(self._listen_loop(project_id))
        logger.info(
            "WS connected: project_id=%s total=%d",
            project_id,
            len(self._connections[project_id]),
        )

    async def disconnect(self, websocket: WebSocket, project_id: str) -> None:
        """Remove a WebSocket connection; cancel listener when no clients remain."""
        async with self._lock:
            self._connections[project_id].discard(websocket)
            if not self._connections[project_id]:
                listener = self._listeners.pop(project_id, None)
                if listener is not None:
                    listener.cancel()
                del self._connections[project_id]
        logger.info("WS disconnected: project_id=%s", project_id)

    async def broadcast(self, project_id: str, message: dict[str, Any]) -> None:
        """Send *message* to every WebSocket client watching *project_id*."""
        payload = json.dumps(message, default=str)
        dead: list[WebSocket] = []
        for ws in self._connections.get(project_id, set()):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws, project_id)

    # ── Redis listener ─────────────────────────────────────────────────────

    async def _drain_pubsub(self, pubsub: Any, project_id: str) -> None:
        """Read messages from *pubsub* until the connection drops or is cancelled.

        Polls with a short timeout and sends a PING every 60 s when idle so that
        managed-Redis / proxy firewalls with a ~2-hour idle-TCP cutoff do not
        silently close the pub/sub connection.
        """
        _POLL_TIMEOUT = 1.0  # seconds to wait per get_message call
        _PING_EVERY = 60.0  # send a keepalive PING after this many idle seconds

        idle_seconds = 0.0
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=_POLL_TIMEOUT
            )
            if message is None:
                idle_seconds += _POLL_TIMEOUT
                if idle_seconds >= _PING_EVERY:
                    await pubsub.ping()
                    idle_seconds = 0.0
                continue

            idle_seconds = 0.0
            if message.get("type") != "message":
                continue
            try:
                event = json.loads(message["data"])
            except (json.JSONDecodeError, TypeError):
                continue
            await self.broadcast(project_id, event)

    async def _listen_loop(self, project_id: str) -> None:
        """Subscribe to the project's Redis channel and fan-out events.

        Automatically reconnects with exponential back-off when the server
        closes the connection (common behind firewalls/NAT on cloud deployments
        where idle TCP connections are killed after an inactivity timeout).
        """
        channel = project_tasks_channel(project_id)
        retry_delay = 1.0
        max_retry_delay = 60.0

        while True:
            pubsub = self._redis.pubsub()
            try:
                await pubsub.subscribe(channel)
                logger.debug("WS listener started: channel=%s", channel)
                retry_delay = 1.0  # reset on successful (re)connect
                await self._drain_pubsub(pubsub, project_id)
            except asyncio.CancelledError:
                logger.debug("WS listener cancelled: channel=%s", channel)
                raise
            except Exception as exc:
                logger.warning(
                    "WS listener error: channel=%s error=%s — retrying in %.1fs",
                    channel,
                    exc,
                    retry_delay,
                )
            finally:
                try:
                    await pubsub.unsubscribe(channel)
                    await pubsub.aclose()
                except Exception:
                    pass

            # Stop retrying if all clients have disconnected in the meantime.
            if not self._connections.get(project_id):
                logger.debug("WS listener: no clients left for channel=%s, stopping", channel)
                break

            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)


# ── Module-level singleton ─────────────────────────────────────────────────

manager = ProjectTaskWebSocketManager()

# ── Sync Redis client for Celery workers ───────────────────────────────────
# A single module-level connection pool shared across all calls within one
# worker process.  redis-py pools are thread-safe, so threaded workers
# (-P threads) share it safely.  Forked workers each get their own copy.

_sync_redis_client: sync_redis.Redis | None = None


def _get_sync_redis_client() -> sync_redis.Redis:
    global _sync_redis_client
    if _sync_redis_client is None:
        _sync_redis_client = create_sync_redis(settings.REDIS_URL)
    return _sync_redis_client


# ── Sync helpers called from Celery workers ────────────────────────────────


def _build_task_event(
    *,
    task_id: str,
    celery_task_id: str | None,
    task_type: str,
    project_id: str,
    status: str,
    progress: int | None = None,
    stage: str | None = None,
    meta: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Build the canonical unified task event payload."""
    return {
        "event": "task.update",
        "task_id": task_id,
        "celery_task_id": celery_task_id,
        "task_type": task_type,
        "project_id": project_id,
        "status": status,
        "progress": progress,
        "stage": stage,
        "meta": meta,
        "error": error,
        "timestamp": datetime.now(UTC).isoformat(),
    }


def publish_task_event_sync(
    *,
    task_db_id: str,
    celery_task_id: str | None,
    task_type: str,
    project_id: str,
    status: str,
    progress: int | None = None,
    stage: str | None = None,
    meta: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Update the ProjectTask row in PostgreSQL **and** publish to Redis.

    Called from Celery workers (sync context).  Failures are caught and
    logged so a Redis/DB outage never aborts task processing.
    """
    # ── Step 1: Update PostgreSQL + append history event (single transaction) ─
    resolved_celery_id = celery_task_id  # fallback if DB step fails
    owner_id: str | None = None
    # Fallback to the requested values if the DB step below fails before
    # resolving the row (e.g. project lookup errors) — overwritten with the
    # row's actual persisted values once `task` resolves, so a write that
    # ProjectTaskRepository.update_status's sticky-cancelled guard silently
    # ignored can never be broadcast/logged as if it had landed.
    effective_status = status
    effective_progress = progress
    effective_stage = stage
    effective_error = error
    try:
        from app.db.unit_of_work import UnitOfWork  # noqa: PLC0415

        with UnitOfWork() as uow:
            # Resolved here (same open transaction) so Step 2 can also fan
            # this event out to the project owner's dashboard channel —
            # see ``project_status_channel``.
            project = uow.projects.get_by_uuid(UUID(project_id))
            owner_id = str(project.owner_id) if project and project.owner_id else None

            task = uow.project_tasks.update_status(
                UUID(task_db_id),
                status=status,
                progress=progress,
                stage=stage,
                error=error,
                meta=meta,
            )
            # Resolve celery_task_id from the stored row when the caller did
            # not supply it.  All internal helpers pass None — this ensures
            # the Redis event always carries the real Celery task UUID that
            # was bound by set_celery_task_id() after dispatch.
            resolved_celery_id = celery_task_id or (task.celery_task_id if task else None)
            if task is None:
                # Row is gone (e.g. wiped/reset out from under a stale retry) —
                # recording an event would violate the task_id FK. Skip the DB
                # write; Step 2 still publishes to Redis for any live dashboard.
                logger.warning(
                    "publish_task_event_sync: task_db_id=%s not found in "
                    "project_tasks; skipping status/history update.",
                    task_db_id,
                )
            else:
                # update_status silently ignores the write once the row is
                # already "cancelled" (see its sticky-cancelled guard) — read
                # back what actually landed so the history entry and the
                # Redis broadcast below both reflect reality instead of a
                # late "running"/"retry.N" request that was dropped.
                effective_status = task.status
                effective_progress = task.progress
                effective_stage = task.stage
                effective_error = task.error

                # Record an immutable history snapshot atomically with the status
                # update.  The event row is appended in the *same* transaction so
                # there can never be a status change without a corresponding history
                # entry — even if the Redis publish below fails.
                uow.task_events.record(
                    task_id=UUID(task_db_id),
                    project_id=UUID(project_id),
                    task_type=task_type,
                    status=effective_status,
                    progress=effective_progress or 0,
                    stage=effective_stage,
                    meta=meta,
                    error=effective_error,
                )
            uow.commit()

    except Exception as exc:
        logger.error(
            "publish_task_event_sync: DB update failed task_db_id=%s error=%s",
            task_db_id,
            exc,
            exc_info=True,
        )

    # ── Step 2: Publish to Redis ───────────────────────────────────────────
    try:
        r = _get_sync_redis_client()
        event = _build_task_event(
            task_id=task_db_id,
            celery_task_id=resolved_celery_id,
            task_type=task_type,
            project_id=project_id,
            status=effective_status,
            progress=effective_progress,
            stage=effective_stage,
            meta=meta,
            error=effective_error,
        )
        payload = json.dumps(event, default=str)
        r.publish(project_tasks_channel(project_id), payload)
        if owner_id is not None:
            # Same event, fanned out to the owner's cross-project dashboard
            # feed (/ws/projects/pipelines) in addition to this project's own
            # channel — see ``project_status_channel``.
            r.publish(project_status_channel(owner_id), payload)

    except Exception as exc:
        logger.error(
            "publish_task_event_sync: Redis publish failed task_db_id=%s error=%s",
            task_db_id,
            exc,
            exc_info=True,
        )
