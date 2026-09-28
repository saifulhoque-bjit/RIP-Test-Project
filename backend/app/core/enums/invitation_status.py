"""Canonical invitation lifecycle statuses, persisted in PostgreSQL."""

from __future__ import annotations

from enum import Enum


class InvitationStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


INVITATION_STATUS_VALUES: tuple[str, ...] = tuple(status.value for status in InvitationStatus)

# Human-readable labels for API responses.
INVITATION_STATUS_DISPLAY_LABELS: dict[str, str] = {
    InvitationStatus.PENDING.value: "Pending",
    InvitationStatus.ACCEPTED.value: "Accepted",
    InvitationStatus.REVOKED.value: "Revoked",
    InvitationStatus.EXPIRED.value: "Expired",
}
