"""SQLAlchemy ORM model for JiraIntegration.

Stores per-project Jira Cloud configuration: connection details, issue type
preferences, and cached traceability custom-field IDs. The API token is
encrypted at rest using Fernet symmetric encryption (see app/utils/encryption.py).
"""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class JiraIntegration(Base):
    __tablename__ = "jira_integrations"

    # ── Identity ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # ── Ownership ──────────────────────────────────────────────────────────
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── Jira connection ────────────────────────────────────────────────────
    jira_base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    jira_project_key: Mapped[str] = mapped_column(String(32), nullable=False)
    jira_board_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    jira_user_email: Mapped[str] = mapped_column(String(320), nullable=False)
    jira_api_token_encrypted: Mapped[str] = mapped_column(String(1024), nullable=False)

    # ── Issue type config ──────────────────────────────────────────────────
    issue_type_name: Mapped[str] = mapped_column(String(64), nullable=False, default="Story")
    epic_issue_type_name: Mapped[str] = mapped_column(String(64), nullable=False, default="Epic")

    # ── Auto-provisioned traceability field IDs ────────────────────────────
    # Cached after first sync: {"rip_id": "customfield_10200", ...}
    traceability_field_ids: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # ── Workflow config ────────────────────────────────────────────────────
    deprecated_transition_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # ── State ──────────────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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

    # ── Indexes ────────────────────────────────────────────────────────────
    __table_args__ = (Index("ix_jira_integrations_project_id", "project_id", unique=True),)
