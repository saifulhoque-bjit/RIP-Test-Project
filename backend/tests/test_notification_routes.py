"""Unit tests for notification routes.

Handlers are plain ``def`` (no genuine async I/O — see
``app/routes/v1/notifications.py``), so they're called directly without
``await``. ``service`` is a ``MagicMock`` standing in for the injected
``NotificationService``; ``uow``/``pagination`` are inert placeholders the
route only forwards, matching the style of ``tests/test_user_routes.py``.
"""

from __future__ import annotations

from unittest.mock import MagicMock
import uuid

from app.core.enums.notification_type import NotificationType
from app.routes.v1.notifications import (
    get_unread_count,
    list_notifications,
    mark_all_read,
    mark_as_read,
)
from app.schemas.notification_schema import (
    NotificationListResponse,
    NotificationUnreadCountResponse,
)
from tests.conftest import make_user


class TestListNotifications:
    def test_calls_service_and_wraps_response(self) -> None:
        user = make_user()
        service = MagicMock()
        expected = NotificationListResponse(items=[], total=0, skip=0, limit=20)
        service.list_notifications.return_value = expected
        pagination = MagicMock(skip=0, limit=20)
        uow = object()

        result = list_notifications(
            current_user=user, uow=uow, pagination=pagination, service=service
        )

        assert result.success is True
        assert result.data is expected
        service.list_notifications.assert_called_once_with(
            user.id, skip=0, limit=20, is_read=None, notification_type=None, uow=uow
        )

    def test_forwards_is_read_and_type_filters(self) -> None:
        user = make_user()
        service = MagicMock()
        expected = NotificationListResponse(items=[], total=0, skip=0, limit=20)
        service.list_notifications.return_value = expected
        pagination = MagicMock(skip=0, limit=20)
        uow = object()

        result = list_notifications(
            current_user=user,
            uow=uow,
            pagination=pagination,
            service=service,
            is_read=False,
            notification_type=NotificationType.WARNING,
        )

        assert result.success is True
        service.list_notifications.assert_called_once_with(
            user.id,
            skip=0,
            limit=20,
            is_read=False,
            notification_type=NotificationType.WARNING,
            uow=uow,
        )


class TestGetUnreadCount:
    def test_calls_service_and_wraps_response(self) -> None:
        user = make_user()
        service = MagicMock()
        expected = NotificationUnreadCountResponse(unread_count=3)
        service.get_unread_count.return_value = expected
        uow = object()

        result = get_unread_count(current_user=user, uow=uow, service=service)

        assert result.success is True
        assert result.data is expected
        service.get_unread_count.assert_called_once_with(user.id, uow=uow)


class TestMarkAllRead:
    def test_calls_service_and_wraps_response(self) -> None:
        user = make_user()
        service = MagicMock()
        expected = NotificationUnreadCountResponse(unread_count=0)
        service.mark_all_read.return_value = expected
        uow = object()

        result = mark_all_read(current_user=user, uow=uow, service=service)

        assert result.success is True
        assert result.data is expected
        service.mark_all_read.assert_called_once_with(user.id, uow=uow)


class TestMarkAsRead:
    def test_calls_service_and_wraps_response(self) -> None:
        user = make_user()
        service = MagicMock()
        notification_id = uuid.uuid4()
        expected = MagicMock()
        service.mark_as_read.return_value = expected
        uow = object()

        result = mark_as_read(
            notification_id=notification_id, current_user=user, uow=uow, service=service
        )

        assert result.success is True
        assert result.data is expected
        service.mark_as_read.assert_called_once_with(user.id, notification_id, uow=uow)
