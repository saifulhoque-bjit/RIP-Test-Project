"""Unit tests for ``publish_task_event_sync``'s dual-channel Redis fan-out.

No real Postgres/Redis connection is used: ``UnitOfWork`` and the sync Redis
client are ``MagicMock`` stubs, following the framework-object mocking rule
in ``.github/instructions/tests.instructions.md``.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from app.websockets.manager import (
    ProjectTaskWebSocketManager,
    project_status_channel,
    project_tasks_channel,
    publish_task_event_sync,
)


def _make_uow(
    *,
    owner_id: uuid.UUID | None,
    task_type: str = "source_process",
    status: str = "running",
    progress: int | None = 50,
    stage: str | None = None,
    error: str | None = None,
) -> MagicMock:
    uow = MagicMock()
    uow.__enter__ = MagicMock(return_value=uow)
    uow.__exit__ = MagicMock(return_value=False)

    project = MagicMock(owner_id=owner_id)
    uow.projects.get_by_uuid.return_value = project

    task = MagicMock(
        celery_task_id="celery-1",
        task_type=task_type,
        status=status,
        progress=progress,
        stage=stage,
        error=error,
    )
    uow.project_tasks.update_status.return_value = task
    return uow


class TestPublishTaskEventSyncDualChannel:
    def test_publishes_to_both_project_and_owner_channels(self) -> None:
        project_id = str(uuid.uuid4())
        task_db_id = str(uuid.uuid4())
        owner_id = uuid.uuid4()
        uow = _make_uow(owner_id=owner_id)
        redis_client = MagicMock()

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=uow),
            patch("app.websockets.manager._get_sync_redis_client", return_value=redis_client),
        ):
            publish_task_event_sync(
                task_db_id=task_db_id,
                celery_task_id="celery-1",
                task_type="source_process",
                project_id=project_id,
                status="running",
                progress=50,
            )

        assert redis_client.publish.call_count == 2
        (project_channel, project_payload), _ = redis_client.publish.call_args_list[0]
        (owner_channel, owner_payload), _ = redis_client.publish.call_args_list[1]

        assert project_channel == project_tasks_channel(project_id)
        assert owner_channel == project_status_channel(str(owner_id))
        # Same event body on both channels — no re-derivation.
        assert json.loads(project_payload) == json.loads(owner_payload)

    def test_skips_owner_channel_when_project_has_no_owner(self) -> None:
        project_id = str(uuid.uuid4())
        task_db_id = str(uuid.uuid4())
        uow = _make_uow(owner_id=None)
        redis_client = MagicMock()

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=uow),
            patch("app.websockets.manager._get_sync_redis_client", return_value=redis_client),
        ):
            publish_task_event_sync(
                task_db_id=task_db_id,
                celery_task_id="celery-1",
                task_type="source_process",
                project_id=project_id,
                status="running",
                progress=50,
            )

        redis_client.publish.assert_called_once()
        (project_channel, _), _ = redis_client.publish.call_args_list[0]
        assert project_channel == project_tasks_channel(project_id)

    def test_skips_owner_channel_when_project_lookup_fails(self) -> None:
        """A DB failure resolving the project must not block the per-project publish."""
        project_id = str(uuid.uuid4())
        task_db_id = str(uuid.uuid4())
        uow = MagicMock()
        uow.__enter__ = MagicMock(return_value=uow)
        uow.__exit__ = MagicMock(return_value=False)
        uow.projects.get_by_uuid.side_effect = RuntimeError("db down")
        redis_client = MagicMock()

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=uow),
            patch("app.websockets.manager._get_sync_redis_client", return_value=redis_client),
        ):
            publish_task_event_sync(
                task_db_id=task_db_id,
                celery_task_id="celery-1",
                task_type="source_process",
                project_id=project_id,
                status="running",
                progress=50,
            )

        redis_client.publish.assert_called_once()
        (project_channel, _), _ = redis_client.publish.call_args_list[0]
        assert project_channel == project_tasks_channel(project_id)

    def test_broadcasts_persisted_status_when_sticky_guard_ignored_the_write(self) -> None:
        """update_status silently keeps a cancelled row's status unchanged
        (see its sticky-cancelled guard) — the Redis broadcast and the
        task-event history record must reflect what actually landed, not the
        requested "running"/"retry.N" that was dropped. Otherwise a worker
        that hasn't noticed a cancel yet can still make a cancelled run look
        alive again on the UI even though Postgres is correct."""
        project_id = str(uuid.uuid4())
        task_db_id = str(uuid.uuid4())
        owner_id = uuid.uuid4()
        uow = _make_uow(
            owner_id=owner_id,
            status="cancelled",
            progress=100,
            stage="source_code.cancelled",
            error=None,
        )
        redis_client = MagicMock()

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=uow),
            patch("app.websockets.manager._get_sync_redis_client", return_value=redis_client),
        ):
            publish_task_event_sync(
                task_db_id=task_db_id,
                celery_task_id="celery-1",
                task_type="source_process",
                project_id=project_id,
                status="running",  # requested, but the row is already cancelled
                progress=55,
                stage="retry.1",
                error="Attempt 1 failed: boom. Retrying in 60s...",
            )

        (project_channel, project_payload), _ = redis_client.publish.call_args_list[0]
        event = json.loads(project_payload)
        assert project_channel == project_tasks_channel(project_id)
        assert event["status"] == "cancelled"
        assert event["progress"] == 100
        assert event["stage"] == "source_code.cancelled"
        assert event["error"] is None

        recorded = uow.task_events.record.call_args.kwargs
        assert recorded["status"] == "cancelled"
        assert recorded["progress"] == 100
        assert recorded["stage"] == "source_code.cancelled"
        assert recorded["error"] is None


class TestProjectTaskWebSocketManager:
    async def test_startup_pings_redis(self):
        manager = ProjectTaskWebSocketManager()
        fake_redis = AsyncMock()
        with patch("app.websockets.manager.create_async_redis", return_value=fake_redis):
            await manager.startup()

        fake_redis.ping.assert_awaited_once()
        assert manager._redis is fake_redis

    async def test_shutdown_cancels_listeners_and_closes_redis(self):
        manager = ProjectTaskWebSocketManager()
        manager._redis = AsyncMock()

        async def _noop():
            await asyncio.sleep(10)

        task = asyncio.create_task(_noop())
        manager._listeners["proj-1"] = task

        await manager.shutdown()

        assert task.cancelled()
        manager._redis.aclose.assert_awaited_once()

    async def test_shutdown_with_no_listeners_still_closes_redis(self):
        manager = ProjectTaskWebSocketManager()
        manager._redis = AsyncMock()

        await manager.shutdown()

        manager._redis.aclose.assert_awaited_once()

    async def test_shutdown_swallows_redis_close_error(self):
        manager = ProjectTaskWebSocketManager()
        manager._redis = AsyncMock()
        manager._redis.aclose.side_effect = RuntimeError("boom")

        await manager.shutdown()  # must not raise

    async def test_connect_registers_websocket_and_starts_listener(self):
        manager = ProjectTaskWebSocketManager()
        ws = AsyncMock()

        with patch.object(manager, "_listen_loop", AsyncMock()):
            await manager.connect(ws, "proj-1")

        ws.accept.assert_awaited_once()
        assert ws in manager._connections["proj-1"]
        assert "proj-1" in manager._listeners

    async def test_connect_second_client_does_not_start_second_listener(self):
        manager = ProjectTaskWebSocketManager()
        ws1, ws2 = AsyncMock(), AsyncMock()

        with patch.object(manager, "_listen_loop", AsyncMock()):
            await manager.connect(ws1, "proj-1")
            first_listener = manager._listeners["proj-1"]
            await manager.connect(ws2, "proj-1")

        assert manager._listeners["proj-1"] is first_listener
        assert len(manager._connections["proj-1"]) == 2

    async def test_disconnect_removes_websocket_and_cancels_listener_when_empty(self):
        manager = ProjectTaskWebSocketManager()
        ws = AsyncMock()
        with patch.object(manager, "_listen_loop", AsyncMock()):
            await manager.connect(ws, "proj-1")
        listener = manager._listeners["proj-1"]

        await manager.disconnect(ws, "proj-1")

        assert "proj-1" not in manager._connections
        assert "proj-1" not in manager._listeners
        assert listener.cancelled() or listener.cancel()

    async def test_disconnect_keeps_listener_when_other_clients_remain(self):
        manager = ProjectTaskWebSocketManager()
        ws1, ws2 = AsyncMock(), AsyncMock()
        with patch.object(manager, "_listen_loop", AsyncMock()):
            await manager.connect(ws1, "proj-1")
            await manager.connect(ws2, "proj-1")

        await manager.disconnect(ws1, "proj-1")

        assert "proj-1" in manager._listeners
        assert ws2 in manager._connections["proj-1"]

    async def test_broadcast_sends_to_all_connected_clients(self):
        manager = ProjectTaskWebSocketManager()
        ws1, ws2 = AsyncMock(), AsyncMock()
        manager._connections["proj-1"] = {ws1, ws2}

        await manager.broadcast("proj-1", {"event": "task.update"})

        ws1.send_text.assert_awaited_once()
        ws2.send_text.assert_awaited_once()

    async def test_broadcast_drops_dead_connection(self):
        manager = ProjectTaskWebSocketManager()
        dead_ws = AsyncMock()
        dead_ws.send_text.side_effect = RuntimeError("closed")
        manager._connections["proj-1"] = {dead_ws}

        with patch.object(manager, "disconnect", AsyncMock()) as mock_disconnect:
            await manager.broadcast("proj-1", {"event": "task.update"})

        mock_disconnect.assert_awaited_once_with(dead_ws, "proj-1")

    async def test_broadcast_no_connections_is_a_noop(self):
        manager = ProjectTaskWebSocketManager()
        await manager.broadcast("no-such-project", {"event": "task.update"})  # must not raise

    async def test_drain_pubsub_broadcasts_valid_message(self):
        manager = ProjectTaskWebSocketManager()
        pubsub = AsyncMock()
        pubsub.get_message.side_effect = [
            {"type": "message", "data": json.dumps({"event": "task.update"})},
            asyncio.CancelledError(),
        ]

        with patch.object(manager, "broadcast", AsyncMock()) as mock_broadcast:
            with pytest.raises(asyncio.CancelledError):
                await manager._drain_pubsub(pubsub, "proj-1")

        mock_broadcast.assert_awaited_once_with("proj-1", {"event": "task.update"})

    async def test_drain_pubsub_skips_invalid_json(self):
        manager = ProjectTaskWebSocketManager()
        pubsub = AsyncMock()
        pubsub.get_message.side_effect = [
            {"type": "message", "data": "not json"},
            asyncio.CancelledError(),
        ]

        with patch.object(manager, "broadcast", AsyncMock()) as mock_broadcast:
            with pytest.raises(asyncio.CancelledError):
                await manager._drain_pubsub(pubsub, "proj-1")

        mock_broadcast.assert_not_awaited()

    async def test_drain_pubsub_ignores_non_message_type(self):
        manager = ProjectTaskWebSocketManager()
        pubsub = AsyncMock()
        pubsub.get_message.side_effect = [
            {"type": "subscribe", "data": None},
            asyncio.CancelledError(),
        ]

        with patch.object(manager, "broadcast", AsyncMock()) as mock_broadcast:
            with pytest.raises(asyncio.CancelledError):
                await manager._drain_pubsub(pubsub, "proj-1")

        mock_broadcast.assert_not_awaited()

    async def test_listen_loop_subscribes_and_breaks_when_no_clients(self):
        manager = ProjectTaskWebSocketManager()
        manager._redis = MagicMock()
        pubsub = AsyncMock()
        manager._redis.pubsub.return_value = pubsub

        with patch.object(manager, "_drain_pubsub", AsyncMock(side_effect=RuntimeError("dropped"))):
            await manager._listen_loop("proj-1")

        pubsub.subscribe.assert_awaited_once()
        pubsub.unsubscribe.assert_awaited_once()
        pubsub.aclose.assert_awaited_once()

    async def test_listen_loop_reraises_cancelled_error(self):
        manager = ProjectTaskWebSocketManager()
        manager._redis = MagicMock()
        pubsub = AsyncMock()
        manager._redis.pubsub.return_value = pubsub

        with patch.object(
            manager, "_drain_pubsub", AsyncMock(side_effect=asyncio.CancelledError())
        ):
            with pytest.raises(asyncio.CancelledError):
                await manager._listen_loop("proj-1")
