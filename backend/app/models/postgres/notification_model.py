"""SQLAlchemy ORM model for in-app Notification.

Each row represents a single notification targeted at one user.
Notifications are created by background workers or services and
delivered to the frontend through:

    1. REST API  — GET /api/v1/notifications
    2. WebSocket — WS /ws/notifications (real-time push on creation)

Column reference
────────────────
id                — PK (UUID), stable reference for the frontend
user_id           — FK → users.id (CASCADE delete); the recipient
title             — short subject line (≤ 255 chars)
message           — full notification body
notification_type — info | success | warning | error
is_read           — False until the user explicitly marks it read
data              — JSONB bag for domain-specific metadata
                    (e.g. project_id, task_id, source_ids)
created_at        — auto timestamp (UTC)
updated_at        — auto-updated timestamp (UTC)
"""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Notification(Base):
    __tablename__ = "notifications"

    # ── Identity ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # ── Ownership ──────────────────────────────────────────────────────────
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # ── Content ────────────────────────────────────────────────────────────
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    # ── Classification ─────────────────────────────────────────────────────
    notification_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="info"
    )  # info | success | warning | error

    # ── State ──────────────────────────────────────────────────────────────
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── Domain metadata ────────────────────────────────────────────────────
    data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

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
    __table_args__ = (
        # Primary lookup: all notifications for a user (feed query)
        Index("ix_notifications_user_id", "user_id"),
        # Unread-count query: avoid full-scan on is_read filter
        Index("ix_notifications_user_id_is_read", "user_id", "is_read"),
        # Feed pagination needs DESC ordering by created_at
        Index("ix_notifications_created_at", "created_at"),
    )
