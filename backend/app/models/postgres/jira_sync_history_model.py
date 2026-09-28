"""SQLAlchemy ORM model for JiraSyncHistory.

Append-only audit trail of every sync run.  Each row captures a summary
(created/updated/deprecated/held counts), the list of released and held
entity IDs, and per-item error details for partial failures.
"""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class JiraSyncHistory(Base):
    __tablename__ = "jira_sync_history"

    # ── Identity ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # ── Parent references ──────────────────────────────────────────────────
    integration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jira_integrations.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    triggered_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── Sync result ────────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="in_progress"
    )  # "in_progress" | "completed" | "failed" | "partial"

    summary: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True
    )  # {"created": 3, "updated": 2, "deprecated": 0, "skipped": 12, "held": 1, "errors": 0}

    items_released: Mapped[list | None] = mapped_column(
        JSONB, nullable=True
    )  # list of RIP entity IDs that were included

    items_held: Mapped[list | None] = mapped_column(
        JSONB, nullable=True
    )  # list of RIP entity IDs that were excluded

    error_details: Mapped[list | None] = mapped_column(
        JSONB, nullable=True
    )  # per-item errors for partial failures

    # ── Timestamps ─────────────────────────────────────────────────────────
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # ── Indexes ────────────────────────────────────────────────────────────
    __table_args__ = (
        Index(
            "ix_jira_sync_history_project_created",
            "project_id",
            "created_at",
        ),
    )
