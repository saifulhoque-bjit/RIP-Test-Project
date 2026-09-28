"""Notification type enum used for in-app notification classification."""

from __future__ import annotations

from enum import Enum


class NotificationType(str, Enum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


# Human-readable labels for API responses.
NOTIFICATION_TYPE_DISPLAY_LABELS: dict[str, str] = {
    NotificationType.INFO.value: "Info",
    NotificationType.SUCCESS.value: "Success",
    NotificationType.WARNING.value: "Warning",
    NotificationType.ERROR.value: "Error",
}
