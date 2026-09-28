"""Unit tests for NotificationRepository."""

from __future__ import annotations

from unittest.mock import MagicMock
import uuid

from app.models.postgres.notification_model import Notification
from app.repositories.postgres.notification_repository import NotificationRepository


def _make_repo() -> tuple[NotificationRepository, MagicMock]:
    session = MagicMock()
    return NotificationRepository(session), session


class TestListByUser:
    def test_returns_items_and_total(self) -> None:
        repo, session = _make_repo()
        user_id = uuid.uuid4()
        items = [MagicMock(spec=Notification), MagicMock(spec=Notification)]
        query = session.query.return_value
        query.filter.return_value.order_by.return_value = query
        query.count.return_value = 5
        query.offset.return_value.limit.return_value.all.return_value = items

        result_items, total = repo.list_by_user(user_id, skip=10, limit=2)

        assert result_items == items
        assert total == 5
        session.query.assert_called_once_with(Notification)
        query.offset.assert_called_once_with(10)
        query.offset.return_value.limit.assert_called_once_with(2)

    def test_applies_is_read_filter_when_given(self) -> None:
        repo, session = _make_repo()
        user_id = uuid.uuid4()
        query = session.query.return_value
        base_filter = query.filter.return_value
        is_read_filter = base_filter.filter.return_value
        is_read_filter.order_by.return_value = is_read_filter
        is_read_filter.count.return_value = 0
        is_read_filter.offset.return_value.limit.return_value.all.return_value = []

        repo.list_by_user(user_id, is_read=False)

        base_filter.filter.assert_called_once()

    def test_applies_notification_type_filter_when_given(self) -> None:
        repo, session = _make_repo()
        user_id = uuid.uuid4()
        query = session.query.return_value
        base_filter = query.filter.return_value
        type_filter = base_filter.filter.return_value
        type_filter.order_by.return_value = type_filter
        type_filter.count.return_value = 0
        type_filter.offset.return_value.limit.return_value.all.return_value = []

        repo.list_by_user(user_id, notification_type="warning")

        base_filter.filter.assert_called_once()


class TestCountUnread:
    def test_returns_scalar_count(self) -> None:
        repo, session = _make_repo()
        user_id = uuid.uuid4()
        session.query.return_value.filter.return_value.scalar.return_value = 3

        result = repo.count_unread(user_id)

        assert result == 3

    def test_returns_zero_when_scalar_none(self) -> None:
        repo, session = _make_repo()
        user_id = uuid.uuid4()
        session.query.return_value.filter.return_value.scalar.return_value = None

        result = repo.count_unread(user_id)

        assert result == 0


class TestGetByIdAndUser:
    def test_returns_notification_when_found(self) -> None:
        repo, session = _make_repo()
        notification = MagicMock(spec=Notification)
        session.query.return_value.filter.return_value.first.return_value = notification

        result = repo.get_by_id_and_user(uuid.uuid4(), uuid.uuid4())

        assert result is notification

    def test_returns_none_when_not_found(self) -> None:
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.first.return_value = None

        result = repo.get_by_id_and_user(uuid.uuid4(), uuid.uuid4())

        assert result is None


class TestMarkAsRead:
    def test_sets_is_read_true_when_found(self) -> None:
        repo, session = _make_repo()
        notification = MagicMock(spec=Notification)
        notification.is_read = False
        session.query.return_value.filter.return_value.first.return_value = notification

        result = repo.mark_as_read(uuid.uuid4(), uuid.uuid4())

        assert result is notification
        assert notification.is_read is True

    def test_returns_none_when_wrong_user_or_missing(self) -> None:
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.first.return_value = None

        result = repo.mark_as_read(uuid.uuid4(), uuid.uuid4())

        assert result is None


class TestMarkAllRead:
    def test_bulk_updates_unread_rows(self) -> None:
        repo, session = _make_repo()
        user_id = uuid.uuid4()
        session.query.return_value.filter.return_value.update.return_value = 4

        result = repo.mark_all_read(user_id)

        assert result == 4
        session.query.return_value.filter.return_value.update.assert_called_once_with(
            {"is_read": True}, synchronize_session="fetch"
        )


class TestCreate:
    def test_adds_and_flushes_new_notification(self) -> None:
        repo, session = _make_repo()
        user_id = uuid.uuid4()

        notification = repo.create(
            user_id=user_id,
            title="Hello",
            message="World",
            notification_type="info",
        )

        assert notification.user_id == user_id
        assert notification.title == "Hello"
        assert notification.message == "World"
        assert notification.notification_type == "info"
        session.add.assert_called_once_with(notification)
        session.flush.assert_called_once()
        session.refresh.assert_called_once_with(notification)
