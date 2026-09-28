"""SQLAlchemy ORM model for ProjectTask.

Tracks the lifecycle of every long-running background task that is
triggered by the four task-initiating API endpoints:

    POST /api/v1/sources/upload/bulk           → task_type = "source_process"
    POST /api/v1/projects/{project_id}/modules/regenerate → task_type = "module_regeneration"
    PATCH /api/v1/projects/{project_id}/modules/status (→ Approved)
                                                → task_type = "story_generation"
    POST /api/v1/projects/{project_id}/user-stories/regenerate
                                                → task_type = "story_regeneration"

Column reference
────────────────
id              — PK (UUID), used as the stable front-end task reference
project_id      — FK → projects.id (CASCADE delete)
user_id         — FK → users.id (SET NULL on delete); who triggered the task
task_type       — one of the four TaskType values above
celery_task_id  — the Celery async-result UUID (unique, nullable until dispatched)
status          — queued | running | cancelled | <terminal> | failed,
                  where <terminal> is task_type-specific: source_process and
                  module_regeneration terminate at ready_for_review;
                  story_generation, story_regeneration, and
                  story_feedback_patch terminate at completed — see
                  app/utils/openapi.py's WS path docs for the full
                  per-task_type table
progress        — 0-100 completion percentage (updated by workers)
stage           — free-text pipeline stage label for UX display
meta            — JSONB bag: source_ids, module counts, etc.
error           — last error message (truncated to 4 000 chars)
request_id      — shared by every task/subtask spawned from one API call;
                  the unit cancellation operates on. Equal to ``id`` for
                  single-task requests, or to the upload batch id for
                  ``sources/upload/bulk``.
parent_task_id  — the task that dispatched this one, if any (FK → this
                  table, SET NULL on delete)
created_at      — auto timestamp (UTC)
updated_at      — auto-updated timestamp (UTC)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.postgres.project_model import Project


@dataclass
class CreateTaskParams:
    """Parameter object for creating a new ProjectTask row.

    Carries every task-specific field that is *not* part of ownership
    (``project_id`` / ``user_id``), keeping callers self-documenting and
    the repository / service signatures stable as new columns are added.

    Attributes:
        task_type:  One of ``source_process``, ``module_regeneration``,
                    ``story_generation``, ``story_regeneration``.
        status:     Initial row status.  Defaults to ``queued`` and should
                    not normally be overridden at creation time.
        progress:   Initial progress percentage (0–100).  Defaults to ``0``.
        stage:      Human-readable pipeline stage label surfaced in the UI.
        meta:       Arbitrary JSONB bag (e.g. ``{"source_ids": ["..."]}``)。
        error:      Initial error message; ``None`` for new tasks.
    """

    task_type: str
    status: str = "queued"
    progress: int = 0
    stage: str | None = None
    meta: dict = field(default_factory=dict)
    error: str | None = None


class ProjectTask(Base):
    __tablename__ = "project_tasks"

    # ── Identity ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # ── Ownership ──────────────────────────────────────────────────────────
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── Task identity ──────────────────────────────────────────────────────
    task_type: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # source_process | module_regeneration | story_generation | story_regeneration

    celery_task_id: Mapped[str | None] = mapped_column(String(256), nullable=True, unique=True)

    # ── Cancellation grouping ──────────────────────────────────────────────
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("project_tasks.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── Progress tracking ──────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="queued"
    )  # queued | running | completed/ready_for_review | failed (task_type-specific — see module docstring)

    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    stage: Mapped[str | None] = mapped_column(String(256), nullable=True)

    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

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
    project: Mapped[Project] = relationship("Project", foreign_keys=[project_id], lazy="select")

    # ── Indexes ────────────────────────────────────────────────────────────
    __table_args__ = (
        Index("ix_project_tasks_project_id_status", "project_id", "status"),
        Index("ix_project_tasks_celery_task_id", "celery_task_id"),
        Index("ix_project_tasks_request_id", "request_id"),
        Index("ix_project_tasks_parent_task_id", "parent_task_id"),
    )
