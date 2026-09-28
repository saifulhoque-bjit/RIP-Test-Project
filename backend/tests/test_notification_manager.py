"""Unit tests for NotificationWebSocketManager."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.websockets.notification_manager import NotificationWebSocketManager


class TestNotificationWebSocketManagerLifecycle:
    async def test_startup_pings_redis_and_captures_loop(self):
        manager = NotificationWebSocketManager()
        fake_redis = AsyncMock()
        with patch(
            "app.websockets.notification_manager.create_async_redis", return_value=fake_redis
        ):
            await manager.startup()

        fake_redis.ping.assert_awaited_once()
        assert manager._redis is fake_redis
        assert manager._loop is asyncio.get_running_loop()

    async def test_shutdown_cancels_listeners_and_closes_redis(self):
        manager = NotificationWebSocketManager()
        manager._redis = AsyncMock()

        async def _noop():
            await asyncio.sleep(10)

        task = asyncio.create_task(_noop())
        manager._listeners["user-1"] = task

        await manager.shutdown()

        assert task.cancelled()
        manager._redis.aclose.assert_awaited_once()

    async def test_shutdown_with_no_redis_is_a_noop(self):
        manager = NotificationWebSocketManager()
        await manager.shutdown()  # must not raise

    async def test_shutdown_swallows_redis_close_error(self):
        manager = NotificationWebSocketManager()
        manager._redis = AsyncMock()
        manager._redis.aclose.side_effect = RuntimeError("boom")

        await manager.shutdown()  # must not raise


class TestNotificationWebSocketManagerConnections:
    async def test_connect_registers_and_starts_listener(self):
        manager = NotificationWebSocketManager()
        ws = AsyncMock()

        with patch.object(manager, "_listen_loop", AsyncMock()):
            await manager.connect(ws, "user-1")

        ws.accept.assert_awaited_once()
        assert ws in manager._connections["user-1"]
        assert "user-1" in manager._listeners

    async def test_disconnect_removes_and_cancels_listener_when_empty(self):
        manager = NotificationWebSocketManager()
        ws = AsyncMock()
        with patch.object(manager, "_listen_loop", AsyncMock()):
            await manager.connect(ws, "user-1")
        listener = manager._listeners["user-1"]

        await manager.disconnect(ws, "user-1")

        assert "user-1" not in manager._connections
        assert "user-1" not in manager._listeners
        assert listener.cancelled() or listener.cancel()

    async def test_disconnect_keeps_listener_when_others_remain(self):
        manager = NotificationWebSocketManager()
        ws1, ws2 = AsyncMock(), AsyncMock()
        with patch.object(manager, "_listen_loop", AsyncMock()):
            await manager.connect(ws1, "user-1")
            await manager.connect(ws2, "user-1")

        await manager.disconnect(ws1, "user-1")

        assert "user-1" in manager._listeners
        assert ws2 in manager._connections["user-1"]


class TestNotificationWebSocketManagerBroadcast:
    async def test_broadcast_no_connections_is_a_noop(self):
        manager = NotificationWebSocketManager()
        await manager.broadcast("no-such-user", {"event": "notification.created"})

    async def test_broadcast_sends_concurrently_to_all_connections(self):
        manager = NotificationWebSocketManager()
        ws1, ws2 = AsyncMock(), AsyncMock()
        manager._connections["user-1"] = {ws1, ws2}

        await manager.broadcast("user-1", {"event": "notification.created"})

        ws1.send_text.assert_awaited_once()
        ws2.send_text.assert_awaited_once()

    async def test_broadcast_drops_dead_connection(self):
        manager = NotificationWebSocketManager()
        dead_ws = AsyncMock()
        dead_ws.send_text.side_effect = RuntimeError("closed")
        manager._connections["user-1"] = {dead_ws}

        with patch.object(manager, "disconnect", AsyncMock()) as mock_disconnect:
            await manager.broadcast("user-1", {"event": "notification.created"})

        mock_disconnect.assert_awaited_once_with(dead_ws, "user-1")

    async def test_safe_send_returns_true_on_success(self):
        manager = NotificationWebSocketManager()
        ws = AsyncMock()

        assert await manager._safe_send(ws, "{}") is True

    async def test_safe_send_returns_false_on_exception(self):
        manager = NotificationWebSocketManager()
        ws = AsyncMock()
        ws.send_text.side_effect = RuntimeError("closed")

        assert await manager._safe_send(ws, "{}") is False

    async def test_safe_send_returns_false_on_timeout(self):
        manager = NotificationWebSocketManager()
        ws = AsyncMock()

        async def _hang(_payload):
            await asyncio.sleep(10)

        ws.send_text.side_effect = _hang

        with patch("app.websockets.notification_manager._SEND_TIMEOUT", 0.01):
            assert await manager._safe_send(ws, "{}") is False


class TestNotificationWebSocketManagerPublish:
    async def test_publish_uses_shared_redis_when_present(self):
        manager = NotificationWebSocketManager()
        manager._redis = AsyncMock()

        await manager.publish("user-1", "{}")

        manager._redis.publish.assert_awaited_once()

    async def test_publish_opens_ephemeral_connection_when_no_shared_redis(self):
        manager = NotificationWebSocketManager()
        ephemeral = AsyncMock()
        with patch(
            "app.websockets.notification_manager.create_async_redis", return_value=ephemeral
        ):
            await manager.publish("user-1", "{}")

        ephemeral.publish.assert_awaited_once()
        ephemeral.aclose.assert_awaited_once()

    async def test_publish_threadsafe_uses_owning_loop(self):
        """`publish_threadsafe` blocks the calling thread on `future.result()` —
        it must be invoked from a thread other than the one running the owning
        loop, or the loop can never process the scheduled coroutine."""
        manager = NotificationWebSocketManager()
        manager._redis = AsyncMock()
        manager._loop = asyncio.get_running_loop()

        with patch.object(manager, "publish", AsyncMock()) as mock_publish:
            await asyncio.to_thread(manager.publish_threadsafe, "user-1", "{}")

        mock_publish.assert_called_once_with("user-1", "{}")

    def test_publish_threadsafe_falls_back_to_fresh_loop_without_shared_state(self):
        manager = NotificationWebSocketManager()
        with patch.object(manager, "publish", AsyncMock()) as mock_publish:
            manager.publish_threadsafe("user-1", "{}")

        mock_publish.assert_called_once_with("user-1", "{}")


class TestNotificationWebSocketManagerListener:
    async def test_drain_pubsub_broadcasts_valid_message(self):
        manager = NotificationWebSocketManager()
        pubsub = AsyncMock()
        pubsub.get_message.side_effect = [
            {"type": "message", "data": json.dumps({"event": "notification.created"})},
            asyncio.CancelledError(),
        ]

        with patch.object(manager, "broadcast", AsyncMock()) as mock_broadcast:
            with pytest.raises(asyncio.CancelledError):
                await manager._drain_pubsub(pubsub, "user-1")

        mock_broadcast.assert_awaited_once_with("user-1", {"event": "notification.created"})

    async def test_drain_pubsub_skips_invalid_json(self):
        manager = NotificationWebSocketManager()
        pubsub = AsyncMock()
        pubsub.get_message.side_effect = [
            {"type": "message", "data": "not json"},
            asyncio.CancelledError(),
        ]

        with patch.object(manager, "broadcast", AsyncMock()) as mock_broadcast:
            with pytest.raises(asyncio.CancelledError):
                await manager._drain_pubsub(pubsub, "user-1")

        mock_broadcast.assert_not_awaited()

    async def test_listen_loop_subscribes_and_breaks_when_no_clients(self):
        manager = NotificationWebSocketManager()
        manager._redis = MagicMock()
        pubsub = AsyncMock()
        manager._redis.pubsub.return_value = pubsub

        with patch.object(manager, "_drain_pubsub", AsyncMock(side_effect=RuntimeError("dropped"))):
            await manager._listen_loop("user-1")

        pubsub.subscribe.assert_awaited_once()
        pubsub.unsubscribe.assert_awaited_once()
        pubsub.aclose.assert_awaited_once()

    async def test_listen_loop_reraises_cancelled_error(self):
        manager = NotificationWebSocketManager()
        manager._redis = MagicMock()
        pubsub = AsyncMock()
        manager._redis.pubsub.return_value = pubsub

        with patch.object(
            manager, "_drain_pubsub", AsyncMock(side_effect=asyncio.CancelledError())
        ):
            with pytest.raises(asyncio.CancelledError):
                await manager._listen_loop("user-1")
