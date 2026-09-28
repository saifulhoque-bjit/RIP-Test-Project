"""Unit tests for NotificationService.

Strategy:
- ``UnitOfWork()`` (the parameterless constructor NotificationService opens
  internally in ``create_notification``) is patched to return a mock context
  manager exposing a mocked ``notifications`` repository — mirrors
  ``tests/conftest.py``'s ``_make_task_uow_mock`` pattern.
- All service methods are plain sync (see the module docstring in
  ``app/services/notification_service.py``), so tests call them directly
  without ``await``/``pytest.mark.asyncio``.
- Redis publish (``NotificationWebSocketManager.publish_threadsafe``) is a
  plain sync, thread-safe method — patched with a plain ``MagicMock`` (not
  ``AsyncMock``) so no real Redis connection or event loop is required.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
import uuid

import pytest

from app.core.enums.notification_type import NotificationType
from app.core.exceptions import NotFoundError
from app.db.unit_of_work import UnitOfWork
from app.models.postgres.notification_model import Notification
from app.services.notification_service import NotificationService, publish_notification


def _make_notification(
    *,
    user_id: uuid.UUID | None = None,
    title: str = "Title",
    message: str = "Message",
    notification_type: str = "info",
    is_read: bool = False,
    data: dict | None = None,
) -> Notification:
    n = Notification()
    n.id = uuid.uuid4()
    n.user_id = user_id or uuid.uuid4()
    n.title = title
    n.message = message
    n.notification_type = notification_type
    n.is_read = is_read
    n.data = data
    n.created_at = datetime.now(tz=UTC)
    n.updated_at = datetime.now(tz=UTC)
    return n


def _make_uow() -> MagicMock:
    """Return a fully-mocked UnitOfWork with a mocked notifications repo."""
    uow = MagicMock(spec=UnitOfWork)
    uow.notifications = MagicMock()
    uow.commit = MagicMock()
    return uow


def _make_uow_context_manager(uow: MagicMock) -> MagicMock:
    """Wrap *uow* so ``with UnitOfWork() as uow:`` works when patched in."""
    ctor = MagicMock(return_value=uow)
    uow.__enter__ = MagicMock(return_value=uow)
    uow.__exit__ = MagicMock(return_value=False)
    return ctor


class TestCreateNotification:
    def test_persists_and_publishes(self) -> None:
        uow = _make_uow()
        notification = _make_notification(title="Hi", message="World")
        uow.notifications.create.return_value = notification
        ctor = _make_uow_context_manager(uow)

        with (
            patch("app.services.notification_service.UnitOfWork", ctor),
            patch(
                "app.websockets.notification_manager.notification_manager.publish_threadsafe",
                new=MagicMock(),
            ) as mock_publish,
        ):
            result = NotificationService().create_notification(
                user_id=notification.user_id,
                title="Hi",
                message="World",
                notification_type=NotificationType.SUCCESS,
            )

        assert result.title == "Hi"
        assert result.message == "World"
        uow.notifications.create.assert_called_once_with(
            user_id=notification.user_id,
            title="Hi",
            message="World",
            notification_type="success",
            data=None,
        )
        uow.commit.assert_called_once()
        mock_publish.assert_called_once()
        published_user_id, published_payload = mock_publish.call_args.args
        assert published_user_id == str(notification.user_id)
        assert '"event": "notification.new"' in published_payload

    def test_publish_failure_does_not_raise(self) -> None:
        """A Redis outage must never prevent notification creation."""
        uow = _make_uow()
        notification = _make_notification()
        uow.notifications.create.return_value = notification
        ctor = _make_uow_context_manager(uow)

        with (
            patch("app.services.notification_service.UnitOfWork", ctor),
            patch(
                "app.websockets.notification_manager.notification_manager.publish_threadsafe",
                new=MagicMock(side_effect=ConnectionError("redis down")),
            ),
        ):
            result = NotificationService().create_notification(
                user_id=notification.user_id,
                title=notification.title,
                message=notification.message,
            )

        assert result.id == notification.id


class TestListNotifications:
    def test_returns_paginated_feed(self) -> None:
        uow = _make_uow()
        items = [_make_notification(), _make_notification()]
        uow.notifications.list_by_user.return_value = (items, 2)

        result = NotificationService().list_notifications(uuid.uuid4(), skip=0, limit=20, uow=uow)

        assert result.total == 2
        assert len(result.items) == 2
        assert result.skip == 0
        assert result.limit == 20

    def test_forwards_is_read_and_type_filters_to_repository(self) -> None:
        uow = _make_uow()
        uow.notifications.list_by_user.return_value = ([], 0)
        user_id = uuid.uuid4()

        NotificationService().list_notifications(
            user_id,
            skip=0,
            limit=20,
            is_read=True,
            notification_type=NotificationType.ERROR,
            uow=uow,
        )

        uow.notifications.list_by_user.assert_called_once_with(
            user_id, skip=0, limit=20, is_read=True, notification_type="error"
        )


class TestGetUnreadCount:
    def test_returns_count(self) -> None:
        uow = _make_uow()
        uow.notifications.count_unread.return_value = 7

        result = NotificationService().get_unread_count(uuid.uuid4(), uow=uow)

        assert result.unread_count == 7


class TestMarkAsRead:
    def test_marks_and_commits(self) -> None:
        uow = _make_uow()
        notification = _make_notification(is_read=True)
        uow.notifications.mark_as_read.return_value = notification

        with patch(
            "app.websockets.notification_manager.notification_manager.publish_threadsafe",
            new=MagicMock(),
        ) as mock_publish:
            result = NotificationService().mark_as_read(
                notification.user_id, notification.id, uow=uow
            )

        assert result.is_read is True
        uow.commit.assert_called_once()
        mock_publish.assert_called_once()
        published_user_id, published_payload = mock_publish.call_args.args
        assert published_user_id == str(notification.user_id)
        assert '"event": "notification.read"' in published_payload

    def test_raises_not_found_when_missing(self) -> None:
        uow = _make_uow()
        uow.notifications.mark_as_read.return_value = None

        with pytest.raises(NotFoundError):
            NotificationService().mark_as_read(uuid.uuid4(), uuid.uuid4(), uow=uow)
        uow.commit.assert_not_called()


class TestMarkAllRead:
    def test_marks_all_and_returns_zero(self) -> None:
        uow = _make_uow()
        user_id = uuid.uuid4()

        with patch(
            "app.websockets.notification_manager.notification_manager.publish_threadsafe",
            new=MagicMock(),
        ) as mock_publish:
            result = NotificationService().mark_all_read(user_id, uow=uow)

        assert result.unread_count == 0
        uow.notifications.mark_all_read.assert_called_once()
        uow.commit.assert_called_once()
        mock_publish.assert_called_once()
        published_user_id, published_payload = mock_publish.call_args.args
        assert published_user_id == str(user_id)
        assert '"event": "notification.read_all"' in published_payload


class TestPublishNotificationHelper:
    def test_delegates_to_create_notification(self) -> None:
        notification = _make_notification()
        with patch(
            "app.services.notification_service.NotificationService.create_notification",
            return_value=notification,
        ) as mock_create:
            result = publish_notification(
                user_id=notification.user_id,
                title=notification.title,
                message=notification.message,
            )

        assert result is notification
        mock_create.assert_called_once()
