"""WebSocket endpoint for the cross-project owner status dashboard.

Single endpoint
───────────────
    WS /ws/projects/pipelines?token=<jwt>

Connection flow
───────────────
1. Client connects with a valid JWT in the ``token`` query param or
   ``access_token`` cookie.
2. Server verifies the JWT and resolves the DB user by ``cognito_sub``.
3. A ``tasks.current`` message is sent with all non-terminal tasks across
   every project the user owns, so the dashboard can hydrate immediately
   after a page refresh — same per-task shape as
   ``GET /projects/{project_id}/tasks`` (each item carries its own
   ``project_id`` so the client can bucket by project).
4. The server subscribes to the Redis channel ``project:status:{user_id}``
   (every project owned by this user publishes here — see
   ``app/websockets/manager.py::publish_task_event_sync``) and forwards
   every ``task.update`` event to the client.
5. A heartbeat ping is sent every 30 s to keep the connection alive.

Scope
─────
Covers only projects owned by the connected user — the same
owner-or-admin rule used everywhere else is not needed here because the
Redis channel and the on-connect snapshot are both already scoped by
``owner_id``, not by a client-supplied project id (contrast with
``app/websockets/source_ws.py``, which must authorize a client-supplied
``project_id``).

Close codes
───────────
    4001 — Unauthorized (missing / invalid / expired token)
    4004 — User not found (DB record missing for Cognito sub)

Message types sent by server
─────────────────────────────
    { "event": "tasks.current", "tasks": [...] }
    { "event": "task.update",   "task_id": "...", "project_id": "...", ... }
    { "event": "heartbeat",     "timestamp": "..." }
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
from app.websockets.project_status_manager import project_status_manager

logger = get_logger(__name__)

project_status_ws_router = APIRouter(tags=["WebSocket"])

_HEARTBEAT_INTERVAL = 30  # seconds


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
    cache miss/key rotation, so it must run off the event loop — see
    ``app/websockets/notification_ws.py::_authenticate`` for the same pattern.
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


def _fetch_current_tasks(owner_id: UUID) -> dict:
    """Fetch the on-connect task snapshot across all owned projects (sync)."""
    from app.db.unit_of_work import UnitOfWork  # noqa: PLC0415
    from app.services.project_task_service import ProjectTaskService  # noqa: PLC0415

    with UnitOfWork() as uow:
        tasks = ProjectTaskService().list_active_tasks_by_owner(uow, owner_id)

    return {
        "event": "tasks.current",
        "tasks": tasks,
    }


async def _send_current_tasks(websocket: WebSocket, owner_id: UUID) -> None:
    """Send all non-terminal tasks across the owner's projects as ``tasks.current``."""
    try:
        snapshot = await run_in_threadpool(_fetch_current_tasks, owner_id)
        await websocket.send_text(json.dumps(snapshot, default=str))
    except Exception as exc:
        logger.warning(
            "_send_current_tasks (project status) failed: owner_id=%s error=%s",
            owner_id,
            exc,
        )


@project_status_ws_router.websocket("/ws/projects/pipelines")
async def project_status_ws(
    websocket: WebSocket,
    token: str | None = Query(default=None),
    access_token: str | None = Cookie(default=None),
) -> None:
    """Unified WebSocket handler for the cross-project owner status dashboard."""

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
        owner_id: UUID = resolved_user_id
    except Exception:
        await websocket.accept()
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # ── Accept connection and send current task state ─────────────────────
    owner_id_str = str(owner_id)
    await project_status_manager.connect(websocket, owner_id_str)
    await _send_current_tasks(websocket, owner_id)

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
        await project_status_manager.disconnect(websocket, owner_id_str)


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
