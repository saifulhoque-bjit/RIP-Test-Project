"""WebSocket connection manager for the cross-project owner status dashboard.

Architecture
────────────
A single WebSocket endpoint ``/ws/projects/pipelines`` maintains one Redis
pub/sub listener per authenticated user, so a dashboard listing all of a
user's projects can show live status without opening one
``/ws/projects/{project_id}`` connection per project.

``publish_task_event_sync`` in ``app/websockets/manager.py`` already fans out
every ``task.update`` event to this channel (in addition to the per-project
one) using the sync Celery-worker Redis client — this manager only owns the
*read* side (subscribe + fan out to connected browsers), mirroring
``ProjectTaskWebSocketManager`` in ``app/websockets/manager.py``, which also
has no publish-from-FastAPI-process path since only Celery workers publish
task events.

Channel naming
──────────────
    project:status:{owner_id}   — owner_id is the PostgreSQL users.id UUID
    (defined once in ``app/websockets/manager.py::project_status_channel``)

Thread safety
─────────────
All mutable state (``_connections``, ``_listeners``) is manipulated only
from the FastAPI async event loop.  ``asyncio.Lock`` guards per-user
connection sets.

Concurrent fan-out
──────────────────
``broadcast()`` sends to every connection for a user concurrently
(``asyncio.gather`` + a per-send timeout via ``_safe_send``), not
sequentially — see ``.github/instructions/eventing.instructions.md`` and
``NotificationWebSocketManager.broadcast`` for the reference pattern this
copies.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
import json
from typing import Any

from fastapi import WebSocket
import redis.asyncio as aioredis

from app.core.config import settings
from app.core.redis_client import create_async_redis
from app.utils.logger import get_logger
from app.websockets.manager import project_status_channel

logger = get_logger(__name__)

# Max time to wait on a single WebSocket send before treating it as dead.
_SEND_TIMEOUT = 5.0


class ProjectStatusWebSocketManager:
    """Manages WebSocket connections and Redis pub/sub for the owner status feed."""

    def __init__(self) -> None:
        # owner_id (str) → {websocket, …}
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        # owner_id (str) → asyncio.Task running the listener loop
        self._listeners: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._redis: aioredis.Redis | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def startup(self) -> None:
        """Create and verify the async Redis connection. Called from lifespan."""
        self._redis = create_async_redis(settings.REDIS_URL)
        await self._redis.ping()
        logger.info("ProjectStatusWebSocketManager: Redis connected")

    async def shutdown(self) -> None:
        """Cancel all listener tasks and close Redis. Called from lifespan."""
        tasks = list(self._listeners.values())
        for task in tasks:
            task.cancel()
        if tasks:
            # Let listener cleanup (pubsub unsubscribe/close) finish before
            # tearing down the shared Redis connection below, so cancellation
            # doesn't race the connection close.
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                logger.warning("ProjectStatusWebSocketManager: error closing Redis", exc_info=True)
        logger.info("ProjectStatusWebSocketManager: shutdown complete")

    # ── Connection management ──────────────────────────────────────────────

    async def connect(self, websocket: WebSocket, owner_id: str) -> None:
        """Accept a WebSocket and start listening for the owner's Redis channel."""
        await websocket.accept()
        async with self._lock:
            self._connections[owner_id].add(websocket)
            if owner_id not in self._listeners:
                self._listeners[owner_id] = asyncio.create_task(self._listen_loop(owner_id))
        logger.info(
            "Project status WS connected: owner_id=%s total=%d",
            owner_id,
            len(self._connections[owner_id]),
        )

    async def disconnect(self, websocket: WebSocket, owner_id: str) -> None:
        """Remove a WebSocket; cancel the Redis listener when no clients remain."""
        async with self._lock:
            self._connections[owner_id].discard(websocket)
            if not self._connections[owner_id]:
                listener = self._listeners.pop(owner_id, None)
                if listener is not None:
                    listener.cancel()
                del self._connections[owner_id]
        logger.info("Project status WS disconnected: owner_id=%s", owner_id)

    async def broadcast(self, owner_id: str, message: dict[str, Any]) -> None:
        """Send *message* to every WebSocket connection open for *owner_id*.

        Sends concurrently (not one-at-a-time) so a single slow or dead
        connection (e.g. a stale browser tab) cannot delay delivery to the
        user's other open tabs/devices.
        """
        payload = json.dumps(message, default=str)
        connections = list(self._connections.get(owner_id, set()))
        if not connections:
            return

        results = await asyncio.gather(
            *(self._safe_send(ws, payload) for ws in connections),
            return_exceptions=True,
        )
        dead = [ws for ws, ok in zip(connections, results, strict=False) if ok is not True]
        for ws in dead:
            await self.disconnect(ws, owner_id)

    async def _safe_send(self, websocket: WebSocket, payload: str) -> bool:
        """Send *payload* to a single connection with a bounded timeout.

        Returns ``True`` on success, ``False`` on failure/timeout so the
        caller can identify and drop dead connections without one slow send
        blocking the others.
        """
        try:
            await asyncio.wait_for(websocket.send_text(payload), timeout=_SEND_TIMEOUT)
            return True
        except Exception:
            return False

    # ── Redis listener ─────────────────────────────────────────────────────

    async def _drain_pubsub(self, pubsub: Any, owner_id: str) -> None:
        """Read messages from *pubsub* until cancelled or the connection drops.

        Sends a keepalive PING every 60 s to prevent idle-TCP cutoffs on
        managed Redis / proxy deployments.
        """
        _POLL_TIMEOUT = 1.0
        _PING_EVERY = 60.0

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
            await self.broadcast(owner_id, event)

    async def _listen_loop(self, owner_id: str) -> None:
        """Subscribe to the owner's Redis channel and fan-out events.

        Automatically reconnects with exponential back-off on server-side
        disconnects (common behind NAT / firewalls on cloud deployments).
        """
        channel = project_status_channel(owner_id)
        retry_delay = 1.0
        max_retry_delay = 60.0

        while True:
            pubsub = self._redis.pubsub()
            try:
                await pubsub.subscribe(channel)
                logger.debug("Project status WS listener started: channel=%s", channel)
                retry_delay = 1.0
                await self._drain_pubsub(pubsub, owner_id)
            except asyncio.CancelledError:
                logger.debug("Project status WS listener cancelled: channel=%s", channel)
                raise
            except Exception as exc:
                logger.warning(
                    "Project status WS listener error: channel=%s error=%s — retrying in %.1fs",
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

            if not self._connections.get(owner_id):
                logger.debug(
                    "Project status WS listener: no clients left for channel=%s, stopping",
                    channel,
                )
                break

            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)


# ── Module-level singleton ─────────────────────────────────────────────────

project_status_manager = ProjectStatusWebSocketManager()
