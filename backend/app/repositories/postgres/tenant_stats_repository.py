"""Read-only repository for tenant dashboard / stats aggregations.

Responsibilities:
- Cross-cutting count queries required by the tenant stats endpoint
- Returns plain primitives (no ORM object assembly)

NOT responsible for:
- Any write operations
- Business logic (e.g. deciding which status counts as "active")
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.constants import ROLE_ADMIN
from app.core.enums.invitation_status import InvitationStatus
from app.models.postgres.invitation_model import Invitation
from app.models.postgres.role_model import Role
from app.models.postgres.tenant_model import Tenant


class TenantStatsRepository:
    """Aggregation queries for the tenant stats endpoint.

    Kept separate from :class:`TenantRepository` so that repository stays
    focused on single-model CRUD/lookups, matching the
    ``ProjectStatsRepository`` / ``ProjectRepository`` split.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def count_total_tenants(self) -> int:
        """Count every tenant row, regardless of status."""
        return self._session.query(func.count(Tenant.id)).scalar() or 0

    def count_by_status(self, status: str) -> int:
        """Count tenants whose ``status`` matches *status* exactly."""
        return (
            self._session.query(func.count(Tenant.id)).filter(Tenant.status == status).scalar()
            or 0
        )

    def count_pending_client_admin_invitations(self) -> int:
        """Count still-pending invitations for the Client Admin (``admin``) role, across all tenants."""
        return (
            self._session.query(func.count(Invitation.id))
            .join(Role, Role.id == Invitation.role_id)
            .filter(
                Invitation.status == InvitationStatus.PENDING.value,
                Role.name == ROLE_ADMIN,
            )
            .scalar()
            or 0
        )
