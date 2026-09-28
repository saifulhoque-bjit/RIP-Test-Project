"""WebSocket connection manager for per-user in-app notifications.

Architecture
────────────
A single WebSocket endpoint ``/ws/notifications`` maintains one Redis pub/sub
listener per authenticated user.  When a notification is created by any service
or Celery worker, it publishes to the user's Redis channel:

    notifications:{user_id}

The manager forwards the event to every WebSocket connection open for that user
(supporting multi-tab browser sessions).

Channel naming
──────────────
    notifications:{user_id}        — user_id is the PostgreSQL users.id UUID

Event schema (published by NotificationService._publish)
─────────────────────────────────────────────────────────
    {
        "event":        "notification.new",
        "notification": { ...NotificationResponse fields... },
        "timestamp":    "ISO-8601"
    }

Thread safety
─────────────
All mutable state (_connections, _listeners) is manipulated only from the
FastAPI async event loop.  asyncio.Lock guards per-user connection sets.

Cross-loop / cross-thread publish safety
─────────────────────────────────────────
``self._redis`` is created in ``startup()`` on the main FastAPI/uvicorn event
loop. Services that publish are plain sync methods (see
``.github/instructions/services.instructions.md``), so FastAPI may run the
calling code in a worker thread — spinning up an unrelated event loop there
(e.g. via ``asyncio.run()``) and using it to call ``self._redis`` would reuse
a connection/pool across event loops, which ``redis.asyncio`` does not
support safely. ``publish_threadsafe()`` is the sync, thread-safe entry point
for this: it schedules the publish coroutine back onto the loop that actually
owns ``self._redis`` via ``asyncio.run_coroutine_threadsafe``, regardless of
which thread calls it. Only when no such loop exists (a Celery worker process,
which never runs the FastAPI lifespan) does it fall back to running a
one-off loop with an ephemeral connection. See
``.github/instructions/eventing.instructions.md`` for the general rule.

Concurrent fan-out
──────────────────
``broadcast()`` sends to every connection for a user concurrently
(``asyncio.gather`` + a per-send timeout via ``_safe_send``), not
sequentially — a single slow/dead tab must never delay delivery to the
user's other tabs/devices. See ``.github/instructions/eventing.instructions.md``.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
from typing import Any

from fastapi import WebSocket
import redis.asyncio as aioredis

from app.core.config import settings
from app.core.redis_client import create_async_redis
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ── Redis channel naming ───────────────────────────────────────────────────

_NOTIFICATION_CHANNEL_PREFIX = "notifications:"

# Max time to wait on a single WebSocket send before treating it as dead.
_SEND_TIMEOUT = 5.0


def notification_channel(user_id: str) -> str:
    """Return the Redis pub/sub channel for a user's in-app notifications."""
    return f"{_NOTIFICATION_CHANNEL_PREFIX}{user_id}"


# ── Manager ────────────────────────────────────────────────────────────────


class NotificationWebSocketManager:
    """Manages WebSocket connections and Redis pub/sub for per-user notifications."""

    def __init__(self) -> None:
        # user_id (str) → {websocket, …}
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        # user_id (str) → asyncio.Task running the listener loop
        self._listeners: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._redis: aioredis.Redis | None = None
        # The event loop that owns ``self._redis`` (set in ``startup()``).
        # Used by ``publish_threadsafe()`` to safely call back into this loop
        # from any other thread.
        self._loop: asyncio.AbstractEventLoop | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def startup(self) -> None:
        """Create and verify the async Redis connection. Called from lifespan."""
        self._loop = asyncio.get_running_loop()
        self._redis = create_async_redis(settings.REDIS_URL)
        await self._redis.ping()
        logger.info("NotificationWebSocketManager: Redis connected")

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
                logger.warning("NotificationWebSocketManager: error closing Redis", exc_info=True)
        logger.info("NotificationWebSocketManager: shutdown complete")

    # ── Connection management ──────────────────────────────────────────────

    async def connect(self, websocket: WebSocket, user_id: str) -> None:
        """Accept a WebSocket and start listening for the user's Redis channel."""
        await websocket.accept()
        async with self._lock:
            self._connections[user_id].add(websocket)
            if user_id not in self._listeners:
                self._listeners[user_id] = asyncio.create_task(self._listen_loop(user_id))
        logger.info(
            "Notification WS connected: user_id=%s total=%d",
            user_id,
            len(self._connections[user_id]),
        )

    async def disconnect(self, websocket: WebSocket, user_id: str) -> None:
        """Remove a WebSocket; cancel the Redis listener when no clients remain."""
        async with self._lock:
            self._connections[user_id].discard(websocket)
            if not self._connections[user_id]:
                listener = self._listeners.pop(user_id, None)
                if listener is not None:
                    listener.cancel()
                del self._connections[user_id]
        logger.info("Notification WS disconnected: user_id=%s", user_id)

    async def broadcast(self, user_id: str, message: dict[str, Any]) -> None:
        """Send *message* to every WebSocket connection open for *user_id*.

        Sends concurrently (not one-at-a-time) so a single slow or dead
        connection (e.g. a stale browser tab) cannot delay delivery to the
        user's other open tabs/devices.
        """
        payload = json.dumps(message, default=str)
        connections = list(self._connections.get(user_id, set()))
        if not connections:
            return

        results = await asyncio.gather(
            *(self._safe_send(ws, payload) for ws in connections),
            return_exceptions=True,
        )
        dead = [ws for ws, ok in zip(connections, results, strict=False) if ok is not True]
        for ws in dead:
            await self.disconnect(ws, user_id)

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

    async def publish(self, user_id: str, payload: str) -> None:
        """Publish a raw JSON *payload* to the user's Redis channel.

        Used by ``NotificationService._publish`` so notification creation
        reuses this manager's async Redis connection instead of maintaining a
        separate sync client. In the FastAPI process ``self._redis`` is
        already open (set by ``startup()`` in the lifespan hook); Celery
        worker processes never call ``startup()``, so the connection is
        lazily created here on first use instead.
        """
        if self._redis is not None:
            await self._redis.publish(notification_channel(user_id), payload)
            return

        # No shared connection (e.g. called from a Celery worker process,
        # which never runs the FastAPI lifespan's startup()). Each call to
        # this method may run in a fresh event loop (see
        # ``app.workers._task_helpers._run_async``), so a connection can't be
        # cached across calls here — open one, use it, close it.
        redis = create_async_redis(settings.REDIS_URL)
        try:
            await redis.publish(notification_channel(user_id), payload)
        finally:
            await redis.aclose()

    def publish_threadsafe(self, user_id: str, payload: str) -> None:
        """Sync, thread-safe entry point for publishing from any thread/process.

        Callers (e.g. ``NotificationService``) are plain sync methods that
        FastAPI may run in a worker thread — a fresh ``asyncio.run()`` there
        must never be used to drive ``self._redis``, since it was created on
        a different event loop (see the module docstring's "Cross-loop /
        cross-thread publish safety" section).

        - FastAPI process (``self._loop``/``self._redis`` set by lifespan
          ``startup()``): schedule ``publish()`` onto the loop that actually
          owns the shared connection, via ``asyncio.run_coroutine_threadsafe``,
          and block this thread on the result with a bounded timeout.
        - Any process that never ran the lifespan (a Celery worker): no
          shared loop/connection exists, so this runs a one-off loop that
          opens, uses, and closes an ephemeral connection (``publish()``'s
          existing fallback branch).
        - A Celery task that reaches this call from inside its own
          already-running loop (e.g. a sync notifier invoked mid-coroutine
          from a task body driven by ``app.workers._task_helpers._run_async``,
          as ``incremental_task.py`` does): ``asyncio.run()`` cannot nest into
          a loop that's already running on this thread, so the publish is
          dispatched to a short-lived worker thread that runs its own
          ``asyncio.run()`` instead.
        """
        if self._loop is not None and self._redis is not None:
            future = asyncio.run_coroutine_threadsafe(self.publish(user_id, payload), self._loop)
            future.result(timeout=_SEND_TIMEOUT)
            return

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.publish(user_id, payload))
            return

        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(asyncio.run, self.publish(user_id, payload)).result(
                timeout=_SEND_TIMEOUT
            )

    # ── Redis listener ─────────────────────────────────────────────────────

    async def _drain_pubsub(self, pubsub: Any, user_id: str) -> None:
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
            await self.broadcast(user_id, event)

    async def _listen_loop(self, user_id: str) -> None:
        """Subscribe to the user's Redis channel and fan-out events.

        Automatically reconnects with exponential back-off on server-side
        disconnects (common behind NAT / firewalls on cloud deployments).
        """
        channel = notification_channel(user_id)
        retry_delay = 1.0
        max_retry_delay = 60.0

        while True:
            pubsub = self._redis.pubsub()
            try:
                await pubsub.subscribe(channel)
                logger.debug("Notification WS listener started: channel=%s", channel)
                retry_delay = 1.0
                await self._drain_pubsub(pubsub, user_id)
            except asyncio.CancelledError:
                logger.debug("Notification WS listener cancelled: channel=%s", channel)
                raise
            except Exception as exc:
                logger.warning(
                    "Notification WS listener error: channel=%s error=%s — retrying in %.1fs",
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

            if not self._connections.get(user_id):
                logger.debug(
                    "Notification WS listener: no clients left for channel=%s, stopping",
                    channel,
                )
                break

            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)


# ── Module-level singleton ─────────────────────────────────────────────────

notification_manager = NotificationWebSocketManager()
