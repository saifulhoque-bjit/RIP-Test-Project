"""SQLAlchemy ORM model for TapSyncHistory.

Append-only audit of every RIP → TAP sync run. RIP does **not** push the
hierarchy directly to TAP — it stages the payload here, notifies TAP with a
lightweight ping (``sync_id`` + a pull-back URL), and TAP fetches the actual
data whenever it is ready via the guarded ``GET .../sync/{sync_id}/data``
endpoint. This row's ``id`` doubles as that **sync_id**; TAP echoes it back on
the acknowledgement callback so the ack can be correlated to its originating
sync.
"""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TapSyncHistory(Base):
    __tablename__ = "tap_sync_history"

    # ── Identity (this id IS the sync_id echoed by TAP on ack) ──────────────
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # ── Parent references ──────────────────────────────────────────────────
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

    # ── Staged payload (served to TAP on pull) ─────────────────────────────
    payload: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True
    )  # {"modules": [...]} — the exact hierarchy snapshot TAP will pull

    # ── Sync result ────────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending_pull"
    )  # "pending_pull" | "notify_failed" | "acked" | "ack_failed"

    summary: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True
    )  # {"created": 3, "updated": 2, "skipped": 12}

    items_released: Mapped[list | None] = mapped_column(
        JSONB, nullable=True
    )  # list of RIP entity IDs that were staged
    items_held: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    error_details: Mapped[list | None] = mapped_column(
        JSONB, nullable=True
    )  # non-fatal notify-ping failure, if any

    # ── Timestamps ─────────────────────────────────────────────────────────
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pulled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # stamped whenever TAP fetches the payload (may be pulled more than once)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # ── Indexes ────────────────────────────────────────────────────────────
    __table_args__ = (
        Index(
            "ix_tap_sync_history_project_created",
            "project_id",
            "created_at",
        ),
    )
