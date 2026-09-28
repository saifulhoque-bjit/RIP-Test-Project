"""Unit tests for ProjectStatusWebSocketManager."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.websockets.project_status_manager import ProjectStatusWebSocketManager


class TestProjectStatusWebSocketManagerLifecycle:
    async def test_startup_pings_redis(self):
        manager = ProjectStatusWebSocketManager()
        fake_redis = AsyncMock()
        with patch(
            "app.websockets.project_status_manager.create_async_redis", return_value=fake_redis
        ):
            await manager.startup()

        fake_redis.ping.assert_awaited_once()
        assert manager._redis is fake_redis

    async def test_shutdown_cancels_listeners_and_closes_redis(self):
        manager = ProjectStatusWebSocketManager()
        manager._redis = AsyncMock()

        async def _noop():
            await asyncio.sleep(10)

        task = asyncio.create_task(_noop())
        manager._listeners["owner-1"] = task

        await manager.shutdown()

        assert task.cancelled()
        manager._redis.aclose.assert_awaited_once()

    async def test_shutdown_with_no_redis_is_a_noop(self):
        manager = ProjectStatusWebSocketManager()
        await manager.shutdown()

    async def test_shutdown_swallows_redis_close_error(self):
        manager = ProjectStatusWebSocketManager()
        manager._redis = AsyncMock()
        manager._redis.aclose.side_effect = RuntimeError("boom")

        await manager.shutdown()  # must not raise


class TestProjectStatusWebSocketManagerConnections:
    async def test_connect_registers_and_starts_listener(self):
        manager = ProjectStatusWebSocketManager()
        ws = AsyncMock()

        with patch.object(manager, "_listen_loop", AsyncMock()):
            await manager.connect(ws, "owner-1")

        ws.accept.assert_awaited_once()
        assert ws in manager._connections["owner-1"]
        assert "owner-1" in manager._listeners

    async def test_disconnect_removes_and_cancels_listener_when_empty(self):
        manager = ProjectStatusWebSocketManager()
        ws = AsyncMock()
        with patch.object(manager, "_listen_loop", AsyncMock()):
            await manager.connect(ws, "owner-1")
        listener = manager._listeners["owner-1"]

        await manager.disconnect(ws, "owner-1")

        assert "owner-1" not in manager._connections
        assert "owner-1" not in manager._listeners
        assert listener.cancelled() or listener.cancel()

    async def test_disconnect_keeps_listener_when_others_remain(self):
        manager = ProjectStatusWebSocketManager()
        ws1, ws2 = AsyncMock(), AsyncMock()
        with patch.object(manager, "_listen_loop", AsyncMock()):
            await manager.connect(ws1, "owner-1")
            await manager.connect(ws2, "owner-1")

        await manager.disconnect(ws1, "owner-1")

        assert "owner-1" in manager._listeners
        assert ws2 in manager._connections["owner-1"]


class TestProjectStatusWebSocketManagerBroadcast:
    async def test_broadcast_no_connections_is_a_noop(self):
        manager = ProjectStatusWebSocketManager()
        await manager.broadcast("no-such-owner", {"event": "task.update"})

    async def test_broadcast_sends_concurrently_to_all_connections(self):
        manager = ProjectStatusWebSocketManager()
        ws1, ws2 = AsyncMock(), AsyncMock()
        manager._connections["owner-1"] = {ws1, ws2}

        await manager.broadcast("owner-1", {"event": "task.update"})

        ws1.send_text.assert_awaited_once()
        ws2.send_text.assert_awaited_once()

    async def test_broadcast_drops_dead_connection(self):
        manager = ProjectStatusWebSocketManager()
        dead_ws = AsyncMock()
        dead_ws.send_text.side_effect = RuntimeError("closed")
        manager._connections["owner-1"] = {dead_ws}

        with patch.object(manager, "disconnect", AsyncMock()) as mock_disconnect:
            await manager.broadcast("owner-1", {"event": "task.update"})

        mock_disconnect.assert_awaited_once_with(dead_ws, "owner-1")

    async def test_safe_send_returns_true_on_success(self):
        manager = ProjectStatusWebSocketManager()
        ws = AsyncMock()

        assert await manager._safe_send(ws, "{}") is True

    async def test_safe_send_returns_false_on_exception(self):
        manager = ProjectStatusWebSocketManager()
        ws = AsyncMock()
        ws.send_text.side_effect = RuntimeError("closed")

        assert await manager._safe_send(ws, "{}") is False


class TestProjectStatusWebSocketManagerListener:
    async def test_drain_pubsub_broadcasts_valid_message(self):
        manager = ProjectStatusWebSocketManager()
        pubsub = AsyncMock()
        pubsub.get_message.side_effect = [
            {"type": "message", "data": json.dumps({"event": "task.update"})},
            asyncio.CancelledError(),
        ]

        with patch.object(manager, "broadcast", AsyncMock()) as mock_broadcast:
            with pytest.raises(asyncio.CancelledError):
                await manager._drain_pubsub(pubsub, "owner-1")

        mock_broadcast.assert_awaited_once_with("owner-1", {"event": "task.update"})

    async def test_drain_pubsub_skips_invalid_json(self):
        manager = ProjectStatusWebSocketManager()
        pubsub = AsyncMock()
        pubsub.get_message.side_effect = [
            {"type": "message", "data": "not json"},
            asyncio.CancelledError(),
        ]

        with patch.object(manager, "broadcast", AsyncMock()) as mock_broadcast:
            with pytest.raises(asyncio.CancelledError):
                await manager._drain_pubsub(pubsub, "owner-1")

        mock_broadcast.assert_not_awaited()

    async def test_listen_loop_subscribes_and_breaks_when_no_clients(self):
        manager = ProjectStatusWebSocketManager()
        manager._redis = MagicMock()
        pubsub = AsyncMock()
        manager._redis.pubsub.return_value = pubsub

        with patch.object(manager, "_drain_pubsub", AsyncMock(side_effect=RuntimeError("dropped"))):
            await manager._listen_loop("owner-1")

        pubsub.subscribe.assert_awaited_once()
        pubsub.unsubscribe.assert_awaited_once()
        pubsub.aclose.assert_awaited_once()

    async def test_listen_loop_reraises_cancelled_error(self):
        manager = ProjectStatusWebSocketManager()
        manager._redis = MagicMock()
        pubsub = AsyncMock()
        manager._redis.pubsub.return_value = pubsub

        with patch.object(
            manager, "_drain_pubsub", AsyncMock(side_effect=asyncio.CancelledError())
        ):
            with pytest.raises(asyncio.CancelledError):
                await manager._listen_loop("owner-1")
