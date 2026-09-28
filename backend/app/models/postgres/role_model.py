"""Role ORM model and RolePermission association table.

Relationships
─────────────
Role ──< UserRole >── User        (via user_model.UserRole)
Role ──< RolePermission >── Permission
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
import uuid

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.postgres.permission_model import Permission
    from app.models.postgres.user_model import User


class RolePermission(Base):
    """Association table linking roles to permissions."""

    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    )


class Role(Base):
    """Authorization role — a named set of permissions.

    Built-in roles seeded at startup: ``super_admin``, ``admin`` (Client
    Admin), ``member``. Custom roles can be added at runtime via the admin
    API (super_admin only, see app/services/role_service.py).
    """

    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    # Stable identifier used in code/authorization checks (ROLE_ADMIN etc.)
    # — never shown to end users directly.
    name: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=False,
    )
    # Human-friendly label for UI display (e.g. "Client Admin" for the
    # ``admin`` role) — distinct from ``name`` so the internal identifier can
    # stay stable while the label is freely rephrased.
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # primaryjoin/secondaryjoin must be explicit because user_roles has TWO
    # FKs to users (user_id and assigned_by), which would cause
    # AmbiguousForeignKeysError without the explicit join conditions.
    users: Mapped[list[User]] = relationship(
        "User",
        secondary="user_roles",
        primaryjoin="Role.id == foreign(UserRole.role_id)",
        secondaryjoin="User.id == foreign(UserRole.user_id)",
        back_populates="roles",
        lazy="selectin",
    )
    permissions: Mapped[list[Permission]] = relationship(
        "Permission",
        secondary="role_permissions",
        back_populates="roles",
        lazy="selectin",
    )
