"""WebSocket endpoint for per-user in-app notifications.

Single endpoint
───────────────
    WS /ws/notifications?token=<jwt>

Connection flow
───────────────
1. Client connects with a valid JWT in the ``token`` query param or
   ``access_token`` cookie.
2. Server verifies the JWT and resolves the DB user by ``cognito_sub``.
3. A ``notifications.current`` message is sent with recent notifications and
   the current unread count so the client can hydrate the bell immediately
   after a page refresh or storage clear.
4. The server subscribes to the Redis channel ``notifications:{user_id}``
   and forwards every ``notification.new`` event to the client.
5. A heartbeat ping is sent every 30 s to keep the connection alive.

Close codes
───────────
    4001 — Unauthorized (missing / invalid / expired token)
    4004 — User not found (DB record missing for Cognito sub)

Message types sent by server
─────────────────────────────
    { "event": "notifications.current", "notifications": [...], "unread_count": N }
    { "event": "notification.new",      "notification": {...}, "timestamp": "..." }
    { "event": "notification.read",     "notification": {...}, "timestamp": "..." }
    { "event": "notification.read_all", "timestamp": "..." }
    { "event": "heartbeat",             "timestamp": "..." }

``notification.read``/``notification.read_all`` are published by
``NotificationService.mark_as_read``/``mark_all_read`` so other open
tabs/devices for the same user stay in sync without re-polling the REST API.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from uuid import UUID

from fastapi import APIRouter, Cookie, Query, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from app.core.security import decode_cognito_token
from app.utils.logger import get_logger
from app.websockets.notification_manager import notification_manager

logger = get_logger(__name__)

notification_ws_router = APIRouter(tags=["WebSocket"])

_HEARTBEAT_INTERVAL = 30  # seconds
_INITIAL_FEED_LIMIT = 50  # number of notifications sent on connect


def _decode_token(raw: str) -> dict | None:
    """Return Cognito JWT payload, or None if invalid/expired."""
    try:
        return decode_cognito_token(raw)
    except Exception:
        return None


async def _authenticate(
    token: str | None,
    access_token_cookie: str | None,
) -> dict | None:
    """Return Cognito JWT payload if a valid token is present, else None.

    ``decode_cognito_token`` does a synchronous ``httpx.get`` JWKS fetch on a
    cache miss/key rotation, so it must run off the event loop — otherwise a
    single slow JWKS fetch would stall every other concurrent request/
    connection on this worker.
    """
    raw = access_token_cookie or token
    if not raw:
        return None
    return await run_in_threadpool(_decode_token, raw)


def _resolve_user_id(cognito_sub: str) -> UUID | None:
    """Look up the DB user id for *cognito_sub*, or None if not found."""
    from app.db.unit_of_work import UnitOfWork  # noqa: PLC0415

    with UnitOfWork() as uow:
        user = uow.users.get_by_cognito_sub(cognito_sub)
        return user.id if user is not None else None


def _fetch_current_notifications(user_id: UUID) -> dict:
    """Fetch the on-connect notification snapshot (sync — plain DB reads).

    Uses the same service layer as ``GET /api/v1/notifications`` so the
    on-connect snapshot matches the REST API exactly.
    """
    from app.db.unit_of_work import UnitOfWork  # noqa: PLC0415
    from app.services.notification_service import NotificationService  # noqa: PLC0415

    with UnitOfWork() as uow:
        service = NotificationService()
        list_response = service.list_notifications(
            user_id, skip=0, limit=_INITIAL_FEED_LIMIT, uow=uow
        )
        count_response = service.get_unread_count(user_id, uow=uow)

    return {
        "event": "notifications.current",
        "notifications": [n.model_dump(mode="json") for n in list_response.items],
        "unread_count": count_response.unread_count,
    }


async def _send_current_notifications(
    websocket: WebSocket,
    user_id: UUID,
) -> None:
    """Send recent notifications + unread count as ``notifications.current``."""
    try:
        snapshot = await run_in_threadpool(_fetch_current_notifications, user_id)
        await websocket.send_text(json.dumps(snapshot, default=str))
    except Exception as exc:
        logger.warning(
            "_send_current_notifications failed: user_id=%s error=%s",
            user_id,
            exc,
        )


@notification_ws_router.websocket("/ws/notifications")
async def notifications_ws(
    websocket: WebSocket,
    token: str | None = Query(default=None),
    access_token: str | None = Cookie(default=None),
) -> None:
    """Unified WebSocket handler for per-user in-app notification events."""

    # ── Authentication ─────────────────────────────────────────────────────
    payload = await _authenticate(token, access_token)
    if payload is None:
        await websocket.accept()
        await websocket.close(code=4001, reason="Unauthorized")
        return

    cognito_sub: str = payload.get("sub", "")
    if not cognito_sub:
        await websocket.accept()
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # ── Resolve DB user by cognito_sub ─────────────────────────────────────
    try:
        resolved_user_id = await run_in_threadpool(_resolve_user_id, cognito_sub)
        if resolved_user_id is None:
            await websocket.accept()
            await websocket.close(code=4004, reason="User not found")
            return
        user_id: UUID = resolved_user_id
    except Exception:
        await websocket.accept()
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # ── Accept connection and send current notification state ──────────────
    user_id_str = str(user_id)
    await notification_manager.connect(websocket, user_id_str)
    await _send_current_notifications(websocket, user_id)

    # ── Heartbeat + receive loop ───────────────────────────────────────────
    try:
        heartbeat_task = asyncio.create_task(_heartbeat_loop(websocket))
        try:
            while True:
                # Drain inbound frames — client-to-server messages are ignored
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            heartbeat_task.cancel()
    finally:
        await notification_manager.disconnect(websocket, user_id_str)


async def _heartbeat_loop(websocket: WebSocket) -> None:
    """Send periodic heartbeat pings to keep the connection alive."""
    try:
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
            await websocket.send_text(
                json.dumps(
                    {
                        "event": "heartbeat",
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                )
            )
    except (asyncio.CancelledError, Exception):
        pass
