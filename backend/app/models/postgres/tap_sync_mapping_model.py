"""SQLAlchemy ORM model for TapSyncMapping.

Bridges a RIP entity (module/feature/user_story) to its counterpart in TAP.

Sync lifecycle (differs from Jira — the flag flips on acknowledgement, not on
push):
    push  → row created/updated with ``sync_status = "pending_ack"`` and the
            originating ``last_push_sync_id``.
    ack   → TAP echoes the ``sync_id``; the row moves to ``"acked"`` and the
            RIP Neo4j node's ``is_tap_synced`` flag is set true.

``tap_entity_id`` is nullable because TAP's own identifier for the entity may
not be known at push time (the TAP contract is still being finalised).
"""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TapSyncMapping(Base):
    __tablename__ = "tap_sync_mappings"

    # ── Identity ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # ── RIP entity reference ───────────────────────────────────────────────
    rip_entity_type: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # "module" | "feature" | "user_story"

    rip_entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    rip_entity_code: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )  # e.g. "U.S 1.1.1", "FEA-001"

    rip_parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # immediate RIP parent id: module_id for a feature, feature_id for a story.
    # Lets the inbound ack flip a feature's flag (which needs its module_id)
    # without re-walking the Neo4j hierarchy.

    # ── TAP entity reference ───────────────────────────────────────────────
    tap_entity_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )  # TAP's own id for the entity (may be unknown until TAP returns it)

    # ── Change detection ───────────────────────────────────────────────────
    rip_content_hash: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )  # SHA-256 of RIP-owned fields at last push

    rip_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # ── Sync-id correlation ────────────────────────────────────────────────
    last_push_sync_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # the tap_sync_history.id of the push that last touched this row

    # ── Sync state ─────────────────────────────────────────────────────────
    sync_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending_ack"
    )  # "pending_ack" | "acked" | "error"

    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Timestamps ─────────────────────────────────────────────────────────
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # ── Constraints & indexes ──────────────────────────────────────────────
    __table_args__ = (
        UniqueConstraint(
            "rip_entity_type",
            "rip_entity_id",
            name="uq_tap_sync_mapping_entity",
        ),
        Index("ix_tap_sync_mappings_push_sync_id", "last_push_sync_id"),
    )
