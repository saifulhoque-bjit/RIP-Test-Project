"""SQLAlchemy ORM model for IncrementalHistory.

Captures the full output of each incremental backlog update run and persists
it for audit, rollback investigation, and frontend display.

Column reference
────────────────
id                              — PK (UUID)
project_id                      — FK → projects.id (CASCADE delete)
updates_json                    — JSONB array of update entries
adds_json                       — JSONB array of add entries
delete_json                     — JSONB array of delete entries
flag_json                       — JSONB array of flag entries
persona_glossary_additions_json — JSONB array of persona/glossary additions
meeting_summary_json            — JSONB meeting summary object
created_at                      — auto timestamp (UTC)
updated_at                      — auto-updated timestamp (UTC)
deleted_at                      — soft-delete timestamp (UTC, nullable)
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.postgres.project_model import Project


class IncrementalHistory(Base):
    __tablename__ = "incremental_histories"

    # ── Identity ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # ── Project scope ──────────────────────────────────────────────────────
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Payload ────────────────────────────────────────────────────────────
    updates_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    adds_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    delete_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    flag_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    persona_glossary_additions_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    meeting_summary_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # ── Timestamps ─────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Relationships ──────────────────────────────────────────────────────
    project: Mapped[Project] = relationship("Project", foreign_keys=[project_id], viewonly=True)

    # ── Indexes ────────────────────────────────────────────────────────────
    __table_args__ = (
        Index("ix_incremental_histories_project_id_created_at", "project_id", "created_at"),
    )
