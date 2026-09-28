"""Invitation ORM model.

Tracks an admin-issued invitation from creation through acceptance. The
Cognito identity is created up-front (at invite time, ``SUPPRESS``ed) so the
accept step only needs to set a permanent password — no Cognito
``NEW_PASSWORD_REQUIRED`` challenge flow required.

Relationships
─────────────
Invitation >── Tenant   (the tenant the invitee is being added to)
Invitation >── Role     (the role granted on acceptance)
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
import uuid

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums.invitation_status import InvitationStatus
from app.db.base import Base

if TYPE_CHECKING:
    from app.models.postgres.role_model import Role
    from app.models.postgres.tenant_model import Tenant


class Invitation(Base):
    """A pending (or resolved) invitation to join a tenant with a given role."""

    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    email: Mapped[str] = mapped_column(String(320), index=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roles.id"),
        nullable=False,
    )
    # Cognito identity created at invite time (AdminCreateUser, SUPPRESSed).
    cognito_sub: Mapped[str] = mapped_column(String(128), nullable=False)
    invited_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # SHA-256 hex digest of the raw token emailed to the invitee. The raw
    # token itself is never persisted.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=InvitationStatus.PENDING.value
    )  # pending | accepted | revoked | expired
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    tenant: Mapped[Tenant] = relationship("Tenant", lazy="select")
    role: Mapped[Role] = relationship("Role", lazy="select")
