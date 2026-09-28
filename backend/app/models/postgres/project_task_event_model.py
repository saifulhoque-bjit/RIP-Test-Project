"""SQLAlchemy ORM model for ProjectTaskEvent.

Every state transition of a ProjectTask is recorded here as an immutable
append-only row.  The parent ``project_tasks`` row always reflects the
*current* state; this table provides the complete, queryable history.

Design rationale
────────────────
- Append-only: rows are never updated or deleted (except via CASCADE when the
  parent task is deleted).
- Denormalised ``project_id`` and ``task_type`` columns allow efficient
  project-scoped queries without a join to ``project_tasks``.
- One event is written atomically with every ``project_tasks`` status update,
  so the history is always consistent with the current row.

Typical event sequence for a source-processing task
────────────────────────────────────────────────────
  queued       → task created, Celery not yet dispatched
  processing   → Celery worker picked up the task
  ...          → intermediate progress events (same status, higher progress)
  completed    → all sources processed successfully
  failed       → unrecoverable error

Usage
─────
Query the full timeline for one task::

    SELECT * FROM project_task_events
    WHERE task_id = :task_id
    ORDER BY created_at ASC;

Query all failed events across a project::

    SELECT * FROM project_task_events
    WHERE project_id = :project_id AND status = 'failed'
    ORDER BY created_at DESC;
"""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProjectTaskEvent(Base):
    __tablename__ = "project_task_events"

    # ── Identity ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # ── Parent task reference ──────────────────────────────────────────────
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("project_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )

    # ── Denormalised for efficient project-scoped queries ──────────────────
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)

    # ── State snapshot ─────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stage: Mapped[str | None] = mapped_column(String(256), nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Immutable timestamp ────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # ── Indexes ────────────────────────────────────────────────────────────
    __table_args__ = (
        # Primary access pattern: timeline for one task
        Index("ix_project_task_events_task_id_created_at", "task_id", "created_at"),
        # Secondary: all events for a project (dashboard, audit)
        Index("ix_project_task_events_project_id_created_at", "project_id", "created_at"),
        # Filter by status within a project (e.g. find all failures)
        Index("ix_project_task_events_project_id_status", "project_id", "status"),
    )
