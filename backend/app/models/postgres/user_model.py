"""User ORM model and UserRole association table.

The ``User`` record is the authoritative PostgreSQL mirror of a Cognito
identity.  It is created on first authenticated request (lazy sync) or eagerly
at registration time.

Relationships
─────────────
User ──< UserRole >── Role   (many-to-many with audit columns)
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.postgres.role_model import Role
    from app.models.postgres.tenant_model import Tenant


class UserRole(Base):
    """Association table linking users to roles.

    Stores audit metadata (who assigned the role and when).
    """

    __tablename__ = "user_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class User(Base):
    """Application user — synchronized from Cognito JWT claims.

    ``cognito_sub`` is the stable Cognito identifier and is used as the
    join key.  ``email`` is kept in sync as a convenient search field.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    # Uniqueness on cognito_sub/email is enforced in Postgres via partial
    # unique indexes scoped to WHERE deleted_at IS NULL (see migration 0021),
    # not the `unique=True` shorthand — a soft-deleted row must not block a
    # future user from reusing the same identity/email.
    cognito_sub: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
    )
    username: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    is_verified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    # Nullable in the MVP: single-tenant deployments have no tenant assigned.
    # Kept on the User (not a many-to-many) since one user belongs to exactly
    # one tenant for the foreseeable roadmap; revisit if that changes.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="SET NULL"),
        nullable=True,
    )
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
    # Soft-delete: set to the current timestamp instead of hard-deleting the
    # row, so anything referencing this user (project ownership, source
    # uploads, role-assignment audit trail) stays intact. Frees the user's
    # email/cognito_sub for reuse by a future invite or registration — see
    # the partial unique indexes on email/cognito_sub (migration 0021).
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None, index=True
    )

    tenant: Mapped[Tenant | None] = relationship("Tenant", lazy="select")

    # Uses selectin loading so roles (and their permissions) are always
    # available without triggering lazy loads after session close.
    # primaryjoin/secondaryjoin must be explicit because user_roles has TWO
    # FKs to users (user_id and assigned_by), which makes SQLAlchemy unable
    # to determine which one to use for the join automatically.
    roles: Mapped[list[Role]] = relationship(
        "Role",
        secondary="user_roles",
        primaryjoin="User.id == foreign(UserRole.user_id)",
        secondaryjoin="Role.id == foreign(UserRole.role_id)",
        back_populates="users",
        lazy="selectin",
    )

    @property
    def role_names(self) -> list[str]:
        """Return the names of all roles assigned to this user.

        Centralises the ``[r.name for r in self.roles]`` transformation so
        that callers (routes, services) depend on a single, consistent
        extraction point rather than duplicating the list-comprehension.
        """
        return [r.name for r in self.roles]
