"""Pydantic schemas for the Notification domain.

Schema hierarchy
────────────────
NotificationResponse        — single notification read representation
NotificationListResponse    — paginated collection wrapper
NotificationUnreadCountResponse — scalar unread count
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.core.enums.notification_type import NotificationType


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    title: str
    message: str
    notification_type: NotificationType
    is_read: bool
    data: dict | None
    created_at: datetime
    updated_at: datetime


class NotificationListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[NotificationResponse]
    total: int
    skip: int
    limit: int


class NotificationUnreadCountResponse(BaseModel):
    unread_count: int
