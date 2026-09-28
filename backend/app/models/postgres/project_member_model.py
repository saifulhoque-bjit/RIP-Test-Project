"""ProjectMember ORM model — per-project role assignment.

Lets a Client Admin scope a Member to specific projects, distinct from
``UserRole`` (tenant-wide role) and from project ownership.

Relationships
─────────────
Project ──< ProjectMember >── User
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
    from app.models.postgres.project_model import Project
    from app.models.postgres.user_model import User


class ProjectMember(Base):
    """Association linking a user to a project with a project-scoped role.

    Composite primary key ``(project_id, user_id, role)`` — retained from
    when a user could hold more than one role on the same project; today
    ``"member"`` is the only project-scoped role. See
    ``ProjectMemberRepository.replace_roles`` for how the role set for a
    (project, user) pair is granted/revoked in one call.
    """

    __tablename__ = "project_members"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    # "member" — see app.core.enums.project_member_role.
    role: Mapped[str] = mapped_column(String(32), primary_key=True)
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

    project: Mapped[Project] = relationship(
        "Project",
        foreign_keys=[project_id],
        lazy="raise",
    )
    # "joined" (not "raise") — ProjectMemberService always needs the user's
    # email/name to build ProjectMemberOut, and this is a small association
    # table, so an eager join has no meaningful N+1 risk.
    user: Mapped[User] = relationship(
        "User",
        foreign_keys=[user_id],
        lazy="joined",
    )
