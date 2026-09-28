"""Service layer for in-app notification management.

Responsibilities
────────────────
1. ``create_notification``  — persist a new notification row in PostgreSQL
                              and push it to the user's Redis channel so any
                              open WebSocket connections receive it instantly.
2. ``list_notifications``   — return a paginated feed for the notification bell.
3. ``get_unread_count``     — return the badge counter for the bell icon.
4. ``mark_as_read``         — acknowledge a single notification and broadcast
                              ``notification.read`` so other open tabs/devices
                              for the same user stay in sync.
5. ``mark_all_read``        — bulk-clear the unread badge and broadcast
                              ``notification.read_all`` for the same reason.

Sync by design
───────────────
Every method here does plain synchronous Postgres work via ``UnitOfWork`` —
there is no genuine async I/O to benefit from ``async def`` (see
``.github/instructions/services.instructions.md``). Route handlers call
these methods directly as plain ``def`` and FastAPI runs them in its
threadpool, matching ``app/routes/v1/users.py``.

Publishing
──────────
``create_notification`` calls ``_publish`` after the DB commit so that the
WebSocket event always matches what is already persisted. Publishing goes
through ``NotificationWebSocketManager.publish_threadsafe`` — a plain sync,
thread-safe method that schedules the actual Redis publish onto whichever
event loop owns the shared connection (see that method's docstring). This
service does **not** bridge into async here via ``_run_async``/
``asyncio.run()``: doing so would spin up an unrelated event loop in the
calling thread and use it to drive a `redis.asyncio` connection created on a
*different* loop (the FastAPI lifespan's), which is unsafe — see
``.github/instructions/eventing.instructions.md``. Redis failures are caught
and logged — they must never block notification creation.

The module-level helper ``publish_notification`` is a convenience wrapper for
code that constructs ``NotificationService()`` only to call ``create_notification``
(e.g., Celery task helpers).
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
from uuid import UUID

from app.core.enums.notification_type import NotificationType
from app.core.exceptions import NotFoundError
from app.core.messages import MSG_NOTIFICATION_NOT_FOUND
from app.db.unit_of_work import UnitOfWork
from app.schemas.notification_schema import (
    NotificationListResponse,
    NotificationResponse,
    NotificationUnreadCountResponse,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


class NotificationService:
    """Reusable service for creating and querying in-app notifications."""

    # ── Write operations ───────────────────────────────────────────────────

    def create_notification(
        self,
        *,
        user_id: UUID,
        title: str,
        message: str,
        notification_type: NotificationType = NotificationType.INFO,
        data: dict | None = None,
    ) -> NotificationResponse:
        """Create and immediately push a notification to the target user.

        Opens its own UnitOfWork so callers (service methods, Celery helpers)
        do not need to pass a session.  The Redis publish happens *after* the
        DB commit so the WebSocket event always reflects persisted data.
        """
        with UnitOfWork() as uow:
            notification = uow.notifications.create(
                user_id=user_id,
                title=title,
                message=message,
                notification_type=notification_type.value,
                data=data,
            )
            uow.commit()
            response = NotificationResponse.model_validate(notification)

        self._publish(str(user_id), response)
        return response

    # ── Read operations ────────────────────────────────────────────────────

    def list_notifications(
        self,
        user_id: UUID,
        *,
        skip: int = 0,
        limit: int = 20,
        is_read: bool | None = None,
        notification_type: NotificationType | None = None,
        uow: UnitOfWork,
    ) -> NotificationListResponse:
        """Return a paginated notification feed for the authenticated user.

        ``is_read``/``notification_type`` narrow the feed when given; omit
        either to return notifications of all read-states/types.
        """
        items, total = uow.notifications.list_by_user(
            user_id,
            skip=skip,
            limit=limit,
            is_read=is_read,
            notification_type=notification_type.value if notification_type else None,
        )
        return NotificationListResponse(
            items=[NotificationResponse.model_validate(n) for n in items],
            total=total,
            skip=skip,
            limit=limit,
        )

    def get_unread_count(self, user_id: UUID, uow: UnitOfWork) -> NotificationUnreadCountResponse:
        """Return the unread notification count for the bell-icon badge."""
        count = uow.notifications.count_unread(user_id)
        return NotificationUnreadCountResponse(unread_count=count)

    # ── State mutations ────────────────────────────────────────────────────

    def mark_as_read(
        self,
        user_id: UUID,
        notification_id: UUID,
        uow: UnitOfWork,
    ) -> NotificationResponse:
        """Mark a single notification as read.

        Raises :class:`~app.core.exceptions.NotFoundError` when the
        notification does not exist or belongs to a different user.
        """
        notification = uow.notifications.mark_as_read(notification_id, user_id)
        if notification is None:
            raise NotFoundError(MSG_NOTIFICATION_NOT_FOUND.format(notification_id=notification_id))
        uow.commit()
        response = NotificationResponse.model_validate(notification)
        self._publish_event(
            str(user_id), "notification.read", {"notification": response.model_dump(mode="json")}
        )
        return response

    def mark_all_read(self, user_id: UUID, uow: UnitOfWork) -> NotificationUnreadCountResponse:
        """Mark all unread notifications as read and return the new badge count (0)."""
        uow.notifications.mark_all_read(user_id)
        uow.commit()
        self._publish_event(str(user_id), "notification.read_all", {})
        return NotificationUnreadCountResponse(unread_count=0)

    # ── Internal: Redis publish ────────────────────────────────────────────

    def _publish(self, user_id: str, notification: NotificationResponse) -> None:
        """Publish a ``notification.new`` event to the user's Redis channel."""
        self._publish_event(
            user_id, "notification.new", {"notification": notification.model_dump(mode="json")}
        )

    def _publish_event(self, user_id: str, event_name: str, extra: dict) -> None:
        """Publish a JSON event to the user's Redis notification channel.

        Sync and thread-safe end to end (via
        ``NotificationWebSocketManager.publish_threadsafe`` — see that
        method's docstring for why this must not be bridged through
        ``asyncio.run()``/``_run_async`` here). Failures are caught and
        logged so a Redis outage never prevents the calling DB operation
        (already committed) from completing successfully.
        """
        try:
            from app.websockets.notification_manager import (  # noqa: PLC0415
                notification_manager,
            )

            event = {
                "event": event_name,
                "timestamp": datetime.now(UTC).isoformat(),
                **extra,
            }
            notification_manager.publish_threadsafe(user_id, json.dumps(event, default=str))
        except Exception as exc:
            logger.warning(
                "NotificationService: Redis publish failed user_id=%s event=%s error=%s",
                user_id,
                event_name,
                exc,
            )


# ── Module-level convenience helper ───────────────────────────────────────────


def publish_notification(
    *,
    user_id: UUID,
    title: str,
    message: str,
    notification_type: NotificationType = NotificationType.INFO,
    data: dict | None = None,
) -> NotificationResponse:
    """Create and push a notification from anywhere in the codebase.

    Intended for use in Celery task helpers, event handlers, or any code that
    needs to send a notification without holding an existing UnitOfWork.

    Example::

        from app.services.notification_service import publish_notification
        from app.core.enums.notification_type import NotificationType

        publish_notification(
            user_id=user_id,
            title="Source Processing Complete",
            message="All 3 files have been processed successfully.",
            notification_type=NotificationType.SUCCESS,
            data={"project_id": str(project_id)},
        )

    ``create_notification`` is a plain sync method (see module docstring), so
    this is just a thin convenience wrapper — no async bridging needed here.
    """
    return NotificationService().create_notification(
        user_id=user_id,
        title=title,
        message=message,
        notification_type=notification_type,
        data=data,
    )
