"""SQLAlchemy ORM model for Source.

A single ``sources`` table covers both single-file and bulk uploads.
The ``upload_type`` column distinguishes between the two.  Bulk files
share a ``batch_id`` so every file in a batch can be queried together.

Soft-delete is implemented via ``is_deleted``/``deleted_at`` so rows are
never physically removed and audit history is preserved.

Batch-level metadata (description, source_language, the technology-stack
fields, is_incremental, user_message) lives on :class:`SourceIngestion`
instead of here — it describes the whole upload batch, not an individual
file, so duplicating it onto every ``Source`` row in a batch was redundant.
Access it via ``source.source_ingestion``.

Column reference
────────────────
id              — PK (UUID)
project_id      — FK → projects.id (NOT NULL; every source belongs to a project)
original_name   — original filename as supplied by the client
storage_key     — S3 object key  (NOT NULL after successful upload)
storage_url     — pre-signed CDN / S3 URL (refreshed on read)
file_size_bytes — file size in bytes (NOT NULL)
mime_type       — IANA MIME type (NOT NULL)
file_type       — derived extension label  e.g. PDF, DOCX, PNG (NOT NULL)
upload_type     — 'single' | 'bulk'
batch_id        — groups files from the same bulk-upload request (nullable)
source_ingestion_id — FK → source_ingestions.id (nullable); links this source to its ingestion batch rollup
source_type     — rfp | source_code | meeting_notes | requirement_update (client-supplied; nullable for pre-existing rows)
status          — uploaded | queued | running | ready_for_review | failed
processing_error — last processing failure message (nullable)
retry_count     — number of times the processing task has been retried
checksum_sha256 — SHA-256 hex digest for dedup / integrity checking
is_deleted      — soft-delete flag
deleted_at      — soft-delete timestamp (UTC)
created_by      — FK → users.id (NOT NULL)
created_at      — auto timestamp
updated_at      — auto-updated timestamp
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums.source_status import SourceProcessingStatus
from app.core.enums.source_type import SourceType
from app.db.base import Base

if TYPE_CHECKING:
    from app.models.postgres.project_model import Project
    from app.models.postgres.source_ingestion_model import SourceIngestion
    from app.models.postgres.user_model import User


class Source(Base):
    __tablename__ = "sources"

    # ── Identity ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True
    )

    # ── Project scope ──────────────────────────────────────────────────────
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── File metadata ──────────────────────────────────────────────────────
    original_name: Mapped[str] = mapped_column(String(512), nullable=False)
    relative_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    storage_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    storage_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    file_type: Mapped[str] = mapped_column(String(32), nullable=False)  # PDF, DOCX, PNG …

    # ── Upload classification ──────────────────────────────────────────────
    upload_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="single"
    )  # 'single' | 'bulk'
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )  # groups files from the same bulk request
    source_ingestion_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_ingestions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Categorization ──────────────────────────────────────────────────────
    source_type: Mapped[str | None] = mapped_column(
        SAEnum(
            SourceType,
            name="source_type_enum",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=True,
    )  # rfp | source_code | meeting_notes | requirement_update

    # ── Processing lifecycle ───────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        SAEnum(
            SourceProcessingStatus,
            name="source_processing_status_enum",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        default=SourceProcessingStatus.UPLOADED.value,
    )  # uploaded | queued | running | ready_for_review | failed
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── Integrity ─────────────────────────────────────────────────────────
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # ── Optional metadata ─────────────────────────────────────────────────
    link_url: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # original source URL (GitHub, SharePoint, Nextcloud)

    # ── Soft delete ───────────────────────────────────────────────────────
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # ── Audit ─────────────────────────────────────────────────────────────
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # ── Relationships ─────────────────────────────────────────────────────
    project: Mapped[Project] = relationship("Project", foreign_keys=[project_id], viewonly=True)
    uploader: Mapped[User] = relationship("User", foreign_keys=[created_by], viewonly=True)
    source_ingestion: Mapped[SourceIngestion | None] = relationship(
        "SourceIngestion", foreign_keys=[source_ingestion_id], viewonly=True
    )

    # ── Composite indexes ─────────────────────────────────────────────────
    __table_args__ = (
        # Accelerates the common filter: list non-deleted sources for a project
        Index("ix_sources_project_id_is_deleted", "project_id", "is_deleted"),
        # Accelerates status-filtered queries scoped to a project
        Index("ix_sources_project_id_status", "project_id", "status"),
    )
