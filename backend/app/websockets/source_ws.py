"""Unified WebSocket endpoint for real-time project task events.

Single endpoint
───────────────
    WS /ws/projects/{project_id}?token=<jwt>

Connection flow
───────────────
1. Client connects with a valid JWT in the ``token`` query param or
   ``access_token`` cookie.
2. Server verifies the JWT, resolves the DB user by ``cognito_sub``, and
   checks the project exists **and** the requester has ``"read"`` access
   (owner, admin/super_admin, or an assigned ProjectMember) — the exact same
   rule as ``GET /projects/{project_id}``
   (``ProjectService.assert_project_access``), reused here rather than
   re-implemented so the two can never drift apart.
3. A ``tasks.current`` message is sent with all non-terminal tasks so the
   client can restore its UI state after a page refresh / storage clear.
   Each task includes an ``events`` history array and its ``meta`` (e.g.
   ``user_story_ids`` for feedback-patch tasks), matching the shape of
   ``GET /projects/{project_id}/tasks``.
4. The server subscribes to the Redis channel ``project:tasks:{project_id}``
   and forwards every ``task.update`` event to the client.
5. A heartbeat ping is sent every 30 s to keep the connection alive.

Close codes
───────────
    4001 — Unauthorized (missing / invalid / expired token)
    4003 — Forbidden (authenticated, but neither the project owner nor an admin)
    4004 — User/project not found, or invalid project_id
           (matches ``app/websockets/notification_ws.py``'s use of 4004 for
           "DB user not found")

Message types sent by server
─────────────────────────────
    { "event": "tasks.current", "project_id": "...", "tasks": [...] }
    { "event": "task.update",   "task_id": "...",    ... }
    { "event": "heartbeat",     "timestamp": "..." }
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from uuid import UUID

from fastapi import APIRouter, Cookie, Query, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.security import decode_cognito_token
from app.utils.logger import get_logger
from app.websockets.manager import manager

logger = get_logger(__name__)

ws_router = APIRouter(tags=["WebSocket"])

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


def _authorize_project_access(cognito_sub: str, project_id: UUID) -> None:
    """Resolve the requester and enforce project access, or raise.

    Applies the exact same ``"read"``-level rule as
    ``GET /projects/{project_id}`` (``ProjectService.assert_project_access``)
    so a WebSocket subscriber can never see more than the REST API would
    allow — reused rather than re-implemented so the two rules can't drift
    apart (see ``.github/instructions/eventing.instructions.md``).

    Raises ``NotFoundError`` if the DB user or the project doesn't exist, or
    ``ForbiddenError`` if the requester has no ``"read"`` access (owner,
    admin/super_admin, or an assigned ProjectMember). Runs as one synchronous
    DB round trip so it can be dispatched via ``run_in_threadpool`` without
    touching the event loop.
    """
    from app.core.messages import MSG_PROJECT_NOT_FOUND  # noqa: PLC0415
    from app.db.unit_of_work import UnitOfWork  # noqa: PLC0415
    from app.services.project_service import ProjectService  # noqa: PLC0415

    with UnitOfWork() as uow:
        user = uow.users.get_by_cognito_sub(cognito_sub)
        if user is None:
            raise NotFoundError("User not found")
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))
        ProjectService.assert_project_access(
            project, user.id, user.role_names, user.tenant_id, "read", uow
        )


async def _send_current_tasks(
    websocket: WebSocket,
    project_id: str,
) -> None:
    """Send all non-terminal tasks for *project_id* as ``tasks.current``.

    Reuses ``ProjectTaskService.list_tasks_with_events`` so the on-connect
    snapshot has the exact same shape as ``GET /projects/{project_id}/tasks``
    — including the ``events`` history and ``meta`` (e.g. ``user_story_ids``
    for feedback-patch tasks). This lets the frontend correlate a task with
    the user stories it targets even after a page refresh or storage clear.
    """
    try:
        snapshot = await run_in_threadpool(_fetch_current_tasks, project_id)
        await websocket.send_text(json.dumps(snapshot, default=str))
    except Exception as exc:
        logger.warning("_send_current_tasks failed: project_id=%s error=%s", project_id, exc)


def _fetch_current_tasks(project_id: str) -> dict:
    """Fetch the on-connect task snapshot (sync — plain DB reads)."""
    from app.db.unit_of_work import UnitOfWork  # noqa: PLC0415
    from app.services.project_task_service import ProjectTaskService  # noqa: PLC0415

    with UnitOfWork() as uow:
        payload = ProjectTaskService().list_tasks_with_events(uow, UUID(project_id), active=True)
    return {
        "event": "tasks.current",
        "project_id": project_id,
        "tasks": payload,
    }


@ws_router.websocket("/ws/projects/{project_id}")
async def project_tasks_ws(
    websocket: WebSocket,
    project_id: str,
    token: str | None = Query(default=None),
    access_token: str | None = Cookie(default=None),
) -> None:
    """Unified WebSocket handler for all project task events."""

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

    # ── Parse project_id and authorize (existence + ownership-or-admin) ────
    try:
        project_uuid = UUID(project_id)
    except ValueError:
        await websocket.accept()
        await websocket.close(code=4004, reason="Invalid project_id")
        return

    try:
        await run_in_threadpool(_authorize_project_access, cognito_sub, project_uuid)
    except NotFoundError as exc:
        await websocket.accept()
        await websocket.close(code=4004, reason=str(exc))
        return
    except ForbiddenError:
        await websocket.accept()
        await websocket.close(code=4003, reason="Forbidden")
        return
    except Exception:
        await websocket.accept()
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # ── Accept connection and send current task state ─────────────────────
    await manager.connect(websocket, project_id)
    await _send_current_tasks(websocket, project_id)

    # ── Heartbeat + receive loop ───────────────────────────────────────────
    try:
        heartbeat_task = asyncio.create_task(_heartbeat_loop(websocket))
        try:
            while True:
                # Keep alive — we don't process inbound frames but must drain them
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            heartbeat_task.cancel()
    finally:
        await manager.disconnect(websocket, project_id)


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
