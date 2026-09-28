"""SQLAlchemy ORM model for JiraSyncMapping.

Bridges a RIP entity (module/feature/user_story) to the Jira issue it was
synced to.  The ``rip_content_hash`` and ``rip_version`` columns enable
change detection so re-sync can skip unchanged items.
"""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class JiraSyncMapping(Base):
    __tablename__ = "jira_sync_mappings"

    # ── Identity ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # ── Parent integration ─────────────────────────────────────────────────
    integration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jira_integrations.id", ondelete="CASCADE"),
        nullable=False,
    )

    # ── RIP entity reference ───────────────────────────────────────────────
    rip_entity_type: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # "module" | "feature" | "user_story"

    rip_entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    rip_entity_code: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )  # e.g. "U.S 1.1.1", "FEA-001"

    # ── Jira issue reference ───────────────────────────────────────────────
    jira_issue_key: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. "MER-42"

    jira_issue_id: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # Jira internal numeric ID

    # ── Change detection ───────────────────────────────────────────────────
    rip_content_hash: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )  # SHA-256 of RIP-owned fields at last sync

    rip_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # ── Sync state ─────────────────────────────────────────────────────────
    sync_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="synced"
    )  # "synced" | "deprecated" | "error"

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
            "integration_id",
            "rip_entity_type",
            "rip_entity_id",
            name="uq_jira_sync_mapping_entity",
        ),
        Index("ix_jira_sync_mappings_jira_issue_key", "jira_issue_key"),
        Index(
            "ix_jira_sync_mappings_integration_type",
            "integration_id",
            "rip_entity_type",
        ),
    )
