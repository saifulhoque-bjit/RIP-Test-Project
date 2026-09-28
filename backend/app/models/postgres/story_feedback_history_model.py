"""SQLAlchemy ORM model for StoryFeedbackHistory.

Captures the full input and output of every targeted story-patch run
(POST /api/v1/projects/{project_id}/user-stories/regenerate-by-feedback)
for audit, debugging, and frontend history display.

Column reference
────────────────
id                   — PK (UUID)
project_id           — FK → projects.id (CASCADE delete)
task_db_id           — FK → project_tasks.id (SET NULL); the originating task
story_feedbacks_json — JSONB: the input list of story + feedback items sent to the AI
revised_stories_json — JSONB: the output revised_stories array returned by the AI
ai_status            — VARCHAR(32): "PASS" | "FAIL" | "UNKNOWN"
ai_feedback          — TEXT: critic feedback / suggested-fix text (empty on PASS)
created_at           — auto timestamp (UTC)
updated_at           — auto-updated timestamp (UTC)
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.postgres.project_model import Project
    from app.models.postgres.project_task_model import ProjectTask


class StoryFeedbackHistory(Base):
    __tablename__ = "story_feedback_histories"

    # ── Identity ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # ── Project scope ──────────────────────────────────────────────────────
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Originating task (nullable — preserved even if task row is deleted) ─
    task_db_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("project_tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Input payload ──────────────────────────────────────────────────────
    story_feedbacks_json: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Input list of story + feedback items sent to the AI patch pipeline",
    )

    # ── Output payload ─────────────────────────────────────────────────────
    revised_stories_json: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="AI-revised story list (revised_stories) from the patch output",
    )

    # ── AI run metadata ────────────────────────────────────────────────────
    ai_status: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment="PASS | FAIL | UNKNOWN — critic gate result",
    )
    ai_feedback: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Critic suggested-fix text; empty string on PASS",
    )

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

    # ── Relationships ──────────────────────────────────────────────────────
    project: Mapped[Project] = relationship("Project", foreign_keys=[project_id], viewonly=True)
    task: Mapped[ProjectTask | None] = relationship(
        "ProjectTask", foreign_keys=[task_db_id], viewonly=True
    )

    # ── Indexes ────────────────────────────────────────────────────────────
    __table_args__ = (
        Index(
            "ix_story_feedback_histories_project_id_created_at",
            "project_id",
            "created_at",
        ),
    )
