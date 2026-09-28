"""Unit tests for the notification WebSocket endpoint and connection manager.

No real WebSocket/Postgres/Redis connection is used: ``WebSocket`` is a
``MagicMock``/``AsyncMock`` stub exposing only the methods the handler calls,
following the framework-object mocking rule in
``.github/instructions/tests.instructions.md``.
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from app.websockets.notification_manager import NotificationWebSocketManager
from app.websockets.notification_ws import (
    _authenticate,
    _decode_token,
    _resolve_user_id,
    notifications_ws,
)


def _make_ws() -> MagicMock:
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    ws.send_text = AsyncMock()
    ws.receive_text = AsyncMock(side_effect=asyncio.CancelledError())
    return ws


class TestDecodeToken:
    def test_returns_payload_on_success(self) -> None:
        with patch(
            "app.websockets.notification_ws.decode_cognito_token",
            return_value={"sub": "abc"},
        ):
            assert _decode_token("valid") == {"sub": "abc"}

    def test_returns_none_on_invalid_token(self) -> None:
        with patch(
            "app.websockets.notification_ws.decode_cognito_token",
            side_effect=ValueError("bad token"),
        ):
            assert _decode_token("bad") is None


class TestAuthenticate:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_token_or_cookie(self) -> None:
        assert await _authenticate(None, None) is None

    @pytest.mark.asyncio
    async def test_runs_decode_off_the_event_loop(self) -> None:
        with patch(
            "app.websockets.notification_ws._decode_token", return_value={"sub": "u1"}
        ) as mock_decode:
            result = await _authenticate("token-value", None)
        assert result == {"sub": "u1"}
        mock_decode.assert_called_once_with("token-value")


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


class TestNotificationsWsAuthRejection:
    @pytest.mark.asyncio
    async def test_closes_4001_when_no_token(self) -> None:
        ws = _make_ws()

        await notifications_ws(ws, token=None, access_token=None)

        ws.accept.assert_called_once()
        ws.close.assert_called_once_with(code=4001, reason="Unauthorized")

    @pytest.mark.asyncio
    async def test_closes_4001_when_token_invalid(self) -> None:
        ws = _make_ws()
        with patch(
            "app.websockets.notification_ws._decode_token",
            return_value=None,
        ):
            await notifications_ws(ws, token="bad-token", access_token=None)

        ws.close.assert_called_once_with(code=4001, reason="Unauthorized")

    @pytest.mark.asyncio
    async def test_closes_4004_when_user_not_found(self) -> None:
        ws = _make_ws()
        with (
            patch(
                "app.websockets.notification_ws._decode_token",
                return_value={"sub": "cognito-sub-1"},
            ),
            patch(
                "app.websockets.notification_ws._resolve_user_id",
                return_value=None,
            ),
        ):
            await notifications_ws(ws, token="good-token", access_token=None)

        ws.close.assert_called_once_with(code=4004, reason="User not found")


class TestBroadcastConcurrentFanout:
    @pytest.mark.asyncio
    async def test_sends_to_all_connections_even_if_one_fails(self) -> None:
        manager = NotificationWebSocketManager()
        user_id = "user-1"
        good_ws = _make_ws()
        bad_ws = _make_ws()
        bad_ws.send_text = AsyncMock(side_effect=RuntimeError("connection reset"))
        manager._connections[user_id] = {good_ws, bad_ws}

        await manager.broadcast(user_id, {"event": "notification.new"})

        good_ws.send_text.assert_called_once()
        bad_ws.send_text.assert_called_once()
        # The dead connection was dropped; the healthy one stays registered.
        assert good_ws in manager._connections.get(user_id, set())
        assert bad_ws not in manager._connections.get(user_id, set())


class TestPublishThreadsafe:
    """Regression tests for the cross-event-loop publish bug.

    ``publish_threadsafe`` is the sync entry point services call. These tests
    prove it actually runs ``publish()`` on the loop that owns it (not on a
    throwaway loop created in the calling thread), and that it still works
    when there is no owning loop at all (the Celery-worker case).
    """

    def test_schedules_onto_owning_loop_when_present(self) -> None:
        manager = NotificationWebSocketManager()
        received: list[tuple[str, str, asyncio.AbstractEventLoop]] = []

        async def fake_publish(user_id: str, payload: str) -> None:
            received.append((user_id, payload, asyncio.get_running_loop()))

        manager.publish = fake_publish  # type: ignore[method-assign]

        owning_loop = asyncio.new_event_loop()
        thread = threading.Thread(target=owning_loop.run_forever, daemon=True)
        thread.start()
        manager._loop = owning_loop
        manager._redis = MagicMock()  # only needs to be non-None here

        try:
            # Called from this test's thread, which has no running loop of
            # its own — simulates a FastAPI worker thread calling in.
            manager.publish_threadsafe("user-1", "payload")
        finally:
            owning_loop.call_soon_threadsafe(owning_loop.stop)
            thread.join(timeout=2)
            owning_loop.close()

        assert len(received) == 1
        user_id, payload, exec_loop = received[0]
        assert (user_id, payload) == ("user-1", "payload")
        assert exec_loop is owning_loop

    def test_falls_back_to_ephemeral_loop_when_no_owning_loop(self) -> None:
        manager = NotificationWebSocketManager()
        received: list[tuple[str, str]] = []

        async def fake_publish(user_id: str, payload: str) -> None:
            received.append((user_id, payload))

        manager.publish = fake_publish  # type: ignore[method-assign]
        manager._loop = None
        manager._redis = None

        manager.publish_threadsafe("user-2", "payload-2")

        assert received == [("user-2", "payload-2")]

    def test_falls_back_when_called_from_inside_a_running_loop(self) -> None:
        """Regression: a Celery task body driven by ``_run_async``
        (``asyncio.run()``) has its own loop already running when it calls a
        sync notifier mid-coroutine (see ``incremental_task.py``'s
        ``_notify_incremental_status`` calls). With no owning loop set,
        ``publish_threadsafe`` used to call ``asyncio.run()`` directly, which
        raises ``RuntimeError: asyncio.run() cannot be called from a running
        event loop`` and got silently swallowed by
        ``NotificationService._publish_event``'s ``except Exception`` — so the
        WebSocket push never happened. It must instead complete the publish
        (e.g. by dispatching to a worker thread) without raising.
        """
        manager = NotificationWebSocketManager()
        received: list[tuple[str, str]] = []

        async def fake_publish(user_id: str, payload: str) -> None:
            received.append((user_id, payload))

        manager.publish = fake_publish  # type: ignore[method-assign]
        manager._loop = None
        manager._redis = None

        async def _call_from_running_loop() -> None:
            manager.publish_threadsafe("user-3", "payload-3")

        asyncio.run(_call_from_running_loop())

        assert received == [("user-3", "payload-3")]
