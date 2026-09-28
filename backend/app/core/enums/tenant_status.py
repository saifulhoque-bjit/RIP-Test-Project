"""Canonical tenant lifecycle statuses, persisted in PostgreSQL."""

from __future__ import annotations

from enum import Enum


class TenantStatus(str, Enum):
    PENDING_INVITATION = "pending_invitation"
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"


TENANT_STATUS_VALUES: tuple[str, ...] = tuple(status.value for status in TenantStatus)

# Human-readable labels for API responses.
TENANT_STATUS_DISPLAY_LABELS: dict[str, str] = {
    TenantStatus.PENDING_INVITATION.value: "Pending invitation",
    TenantStatus.ACTIVE.value: "Active",
    TenantStatus.INACTIVE.value: "Inactive",
    TenantStatus.SUSPENDED.value: "Suspended",
}
