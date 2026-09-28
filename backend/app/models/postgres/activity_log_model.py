"""SQLAlchemy ORM model for project ActivityLog.

Each row is one immutable, human-readable entry in a project's activity
feed — e.g. "Generated 5 Modules, 20 Features" or "Approved change: User
Story updated". Entries are written by `app.services.activity_log_service
.record_activity` from Celery task completion points and request-path
services, and read via:

    REST API — GET /api/v1/projects/{project_id}/activity-logs

Column reference
────────────────
id             — PK (UUID)
project_id     — FK → projects.id (CASCADE delete)
actor_user_id  — FK → users.id (SET NULL on delete); the user who triggered
                 the underlying action, when resolvable
activity_type  — ActivityType enum value (e.g. rfp_modules_generated)
summary        — short title (≤ 255 chars), e.g. "Modules & Features Generated"
message        — full rendered sentence, e.g. "Generated 5 Modules, 20 Features"
data           — JSONB bag of structured counts/ids for drill-down
created_at     — auto timestamp (UTC); rows are never updated after creation
"""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    # ── Identity ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # ── Scope / actor ──────────────────────────────────────────────────────
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── Classification / content ──────────────────────────────────────────
    activity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    # ── Domain metadata ────────────────────────────────────────────────────
    data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # ── Timestamps ─────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # ── Indexes ────────────────────────────────────────────────────────────
    __table_args__ = (
        Index("ix_activity_logs_project_id", "project_id"),
        Index("ix_activity_logs_created_at", "created_at"),
        Index("ix_activity_logs_activity_type", "activity_type"),
        # Primary feed query: activity for a project, newest first
        Index("ix_activity_logs_project_id_created_at", "project_id", "created_at"),
    )
