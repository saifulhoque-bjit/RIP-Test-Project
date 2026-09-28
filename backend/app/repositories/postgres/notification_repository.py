"""Repository for Notification — per-user in-app notification rows."""

from __future__ import annotations

import uuid
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.postgres.notification_model import Notification
from app.repositories.postgres.base_repository import BaseRepository


class NotificationRepository(BaseRepository[Notification]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Notification)

    # ── Feed queries ───────────────────────────────────────────────────────

    def list_by_user(
        self,
        user_id: UUID,
        *,
        skip: int = 0,
        limit: int = 20,
        is_read: bool | None = None,
        notification_type: str | None = None,
    ) -> tuple[list[Notification], int]:
        """Return a paginated feed of notifications for *user_id*.

        ``is_read`` and ``notification_type`` narrow the feed when given
        (e.g. the bell icon's "unread only" toggle); ``None`` means no
        filtering on that field. Results are ordered newest-first. The total
        count is returned as the second element so callers can build
        pagination metadata without an extra query.
        """
        query = self._session.query(Notification).filter(Notification.user_id == user_id)
        if is_read is not None:
            query = query.filter(Notification.is_read.is_(is_read))
        if notification_type is not None:
            query = query.filter(Notification.notification_type == notification_type)
        query = query.order_by(Notification.created_at.desc())
        total = query.count()
        items = query.offset(skip).limit(limit).all()
        return items, total

    def count_unread(self, user_id: UUID) -> int:
        """Return the number of unread notifications for *user_id*."""
        return (
            self._session.query(func.count(Notification.id))
            .filter(
                Notification.user_id == user_id,
                Notification.is_read.is_(False),
            )
            .scalar()
            or 0
        )

    # ── Ownership-scoped single-row lookups ────────────────────────────────

    def get_by_id_and_user(self, notification_id: UUID, user_id: UUID) -> Notification | None:
        """Return a notification that belongs to *user_id*, or None."""
        return (
            self._session.query(Notification)
            .filter(
                Notification.id == notification_id,
                Notification.user_id == user_id,
            )
            .first()
        )

    # ── State mutations ────────────────────────────────────────────────────

    def mark_as_read(self, notification_id: UUID, user_id: UUID) -> Notification | None:
        """Set ``is_read = True`` on the matching row and return it.

        Returns ``None`` when no notification exists for this id+user pair.
        The caller is responsible for committing the session.
        """
        notification = self.get_by_id_and_user(notification_id, user_id)
        if notification is not None:
            notification.is_read = True
        return notification

    def mark_all_read(self, user_id: UUID) -> int:
        """Bulk-update all unread notifications for *user_id* to ``is_read = True``.

        Returns the number of rows updated.
        The caller is responsible for committing the session.
        """
        return (
            self._session.query(Notification)
            .filter(
                Notification.user_id == user_id,
                Notification.is_read.is_(False),
            )
            .update({"is_read": True}, synchronize_session="fetch")
        )

    # ── Creation ───────────────────────────────────────────────────────────

    def create(
        self,
        *,
        user_id: UUID,
        title: str,
        message: str,
        notification_type: str,
        data: dict | None = None,
    ) -> Notification:
        """Insert a new Notification row and flush to populate DB defaults.

        The caller is responsible for committing the session.
        Returns the ORM instance with all DB-generated fields populated.
        """
        notification = Notification(
            id=uuid.uuid4(),
            user_id=user_id,
            title=title,
            message=message,
            notification_type=notification_type,
            data=data,
        )
        self._session.add(notification)
        self._session.flush()
        self._session.refresh(notification)
        return notification
