"""SQLAlchemy ORM model for TapAckHistory.

Append-only audit of every inbound acknowledgement TAP sends to RIP. When TAP
finishes accepting a pushed batch it calls RIP's ``/ack`` endpoint echoing the
originating ``sync_id`` and the entities it accepted; RIP records one row here
and flips the ``is_tap_synced`` flag on those RIP entities.

``sync_id`` is a plain (un-constrained) UUID rather than a hard FK to
``tap_sync_history.id`` on purpose: TAP is an external caller and may send a
stale or unrecognised id, which the service records as ``unknown`` instead of
failing the insert.
"""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TapAckHistory(Base):
    __tablename__ = "tap_ack_history"

    # ── Identity ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # ── Parent references ──────────────────────────────────────────────────
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )

    # ── Correlation to the originating push ────────────────────────────────
    sync_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # the tap_sync_history.id TAP echoed back (nullable: may be unknown)

    # ── Ack payload ────────────────────────────────────────────────────────
    acked_entities: Mapped[list | None] = mapped_column(
        JSONB, nullable=True
    )  # [{"rip_entity_type": "user_story", "rip_entity_id": "..."}, ...]

    summary: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True
    )  # {"acked": 5, "unknown": 0, "errors": 0}

    error_details: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    source: Mapped[str] = mapped_column(String(32), nullable=False, default="tap")

    # ── Timestamps ─────────────────────────────────────────────────────────
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # ── Indexes ────────────────────────────────────────────────────────────
    __table_args__ = (
        Index(
            "ix_tap_ack_history_project_created",
            "project_id",
            "created_at",
        ),
    )
