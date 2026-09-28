"""Unit tests for the cross-project owner status WebSocket endpoint/manager.

No real WebSocket/Postgres/Redis connection is used: ``WebSocket`` is a
``MagicMock``/``AsyncMock`` stub exposing only the methods the handler calls,
following the framework-object mocking rule in
``.github/instructions/tests.instructions.md``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from app.websockets.manager import project_status_channel
from app.websockets.project_status_manager import ProjectStatusWebSocketManager
from app.websockets.project_status_ws import (
    _fetch_current_tasks,
    _resolve_user_id,
    project_status_ws,
)


class TestRoutingDoesNotCollideWithProjectIdRoute:
    """Regression test for a real routing hazard, not a hypothetical one.

    ``/ws/projects/pipelines`` has the exact same shape as the pre-existing
    ``/ws/projects/{project_id}`` (both are two path segments under ``/ws``).
    Starlette matches WebSocket routes by registration order, not by
    specificity, and the default path converter for ``{project_id}`` matches
    any single segment — including the literal string "pipelines". If
    ``ws_router`` (source_ws.py) were ever registered before
    ``project_status_ws_router`` in ``app/main.py`` again, connections to
    ``/ws/projects/pipelines`` would silently be swallowed by the
    ``{project_id}`` handler with ``project_id="pipelines"`` instead of
    reaching this endpoint.
    """

    def test_pipelines_path_resolves_to_the_pipelines_handler(self) -> None:
        from starlette.routing import Match

        from app.main import app

        scope = {"type": "websocket", "path": "/ws/projects/pipelines", "headers": []}
        matched_endpoint = None
        for route in app.router.routes:
            match, _ = route.matches(scope)
            if match == Match.FULL:
                matched_endpoint = route.endpoint
                break

        assert matched_endpoint is project_status_ws


def _make_ws() -> MagicMock:
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    ws.send_text = AsyncMock()
    ws.receive_text = AsyncMock(side_effect=asyncio.CancelledError())
    return ws


class TestProjectStatusChannel:
    def test_channel_name_is_scoped_by_owner_id(self) -> None:
        assert project_status_channel("owner-1") == "project:status:owner-1"


class TestResolveUserId:
    def test_returns_user_id_when_found(self) -> None:
        user_id = uuid.uuid4()
        mock_user = MagicMock(id=user_id)
        uow = MagicMock()
        uow.__enter__ = MagicMock(return_value=uow)
        uow.__exit__ = MagicMock(return_value=False)
        uow.users.get_by_cognito_sub.return_value = mock_user

        with patch("app.db.unit_of_work.UnitOfWork", return_value=uow):
            result = _resolve_user_id("cognito-sub-1")

        assert result == user_id

    def test_returns_none_when_not_found(self) -> None:
        uow = MagicMock()
        uow.__enter__ = MagicMock(return_value=uow)
        uow.__exit__ = MagicMock(return_value=False)
        uow.users.get_by_cognito_sub.return_value = None

        with patch("app.db.unit_of_work.UnitOfWork", return_value=uow):
            result = _resolve_user_id("cognito-sub-1")

        assert result is None


class TestFetchCurrentTasks:
    def test_wraps_owner_scoped_tasks_in_snapshot_envelope(self) -> None:
        owner_id = uuid.uuid4()
        uow = MagicMock()
        uow.__enter__ = MagicMock(return_value=uow)
        uow.__exit__ = MagicMock(return_value=False)
        fake_tasks = [{"task_id": "t1", "project_id": "p1"}]

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=uow),
            patch(
                "app.services.project_task_service.ProjectTaskService.list_active_tasks_by_owner",
                return_value=fake_tasks,
            ) as mock_list,
        ):
            snapshot = _fetch_current_tasks(owner_id)

        mock_list.assert_called_once_with(uow, owner_id)
        assert snapshot == {"event": "tasks.current", "tasks": fake_tasks}


class TestProjectStatusWsAuthRejection:
    @pytest.mark.asyncio
    async def test_closes_4001_when_no_token(self) -> None:
        ws = _make_ws()

        await project_status_ws(ws, token=None, access_token=None)

        ws.accept.assert_called_once()
        ws.close.assert_called_once_with(code=4001, reason="Unauthorized")

    @pytest.mark.asyncio
    async def test_closes_4001_when_token_invalid(self) -> None:
        ws = _make_ws()
        with patch(
            "app.websockets.project_status_ws._decode_token",
            return_value=None,
        ):
            await project_status_ws(ws, token="bad-token", access_token=None)

        ws.close.assert_called_once_with(code=4001, reason="Unauthorized")

    @pytest.mark.asyncio
    async def test_closes_4004_when_user_not_found(self) -> None:
        ws = _make_ws()
        with (
            patch(
                "app.websockets.project_status_ws._decode_token",
                return_value={"sub": "cognito-sub-1"},
            ),
            patch(
                "app.websockets.project_status_ws._resolve_user_id",
                return_value=None,
            ),
        ):
            await project_status_ws(ws, token="good-token", access_token=None)

        ws.close.assert_called_once_with(code=4004, reason="User not found")


class TestBroadcastConcurrentFanout:
    @pytest.mark.asyncio
    async def test_sends_to_all_connections_even_if_one_fails(self) -> None:
        manager = ProjectStatusWebSocketManager()
        owner_id = "owner-1"
        good_ws = _make_ws()
        bad_ws = _make_ws()
        bad_ws.send_text = AsyncMock(side_effect=RuntimeError("connection reset"))
        manager._connections[owner_id] = {good_ws, bad_ws}

        await manager.broadcast(owner_id, {"event": "task.update"})

        good_ws.send_text.assert_called_once()
        bad_ws.send_text.assert_called_once()
        # The dead connection was dropped; the healthy one stays registered.
        assert good_ws in manager._connections.get(owner_id, set())
        assert bad_ws not in manager._connections.get(owner_id, set())
