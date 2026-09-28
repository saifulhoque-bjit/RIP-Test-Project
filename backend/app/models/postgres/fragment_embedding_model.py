"""SQLAlchemy ORM model for FragmentEmbedding.

Each row stores the pgvector embedding for a single fragment (document chunk).
Keeping embeddings in a dedicated table instead of the ``sources`` table
provides:

- Better normalisation: one vector per chunk, not one per file.
- Improved query performance: similarity searches scan only this table.
- Independent lifecycle: embeddings can be regenerated without touching source
  metadata.

Column reference
────────────────
id          — PK (UUID)
project_id  — FK → projects.id (denormalised for efficient project-scoped search)
source_id   — FK → sources.id (the parent source file)
fragment_id — Neo4j Fragment node UUID (stored as text; not a PG FK)
embedding   — 1536-dim pgvector vector
created_at  — auto timestamp
updated_at  — auto-updated timestamp
"""

from __future__ import annotations

from datetime import datetime
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.db.base import Base

_VECTOR_DIM = settings.EMBEDDING_VECTOR_DIM


class FragmentEmbedding(Base):
    __tablename__ = "fragment_embeddings"

    # ── Identity ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True
    )

    # ── Scope ──────────────────────────────────────────────────────────────
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Fragment reference ─────────────────────────────────────────────────
    # Fragment nodes live in Neo4j; their UUID is stored here as plain text.
    fragment_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    # ── Vector ─────────────────────────────────────────────────────────────
    # 1536-dim matches OpenAI text-embedding-3-small / text-embedding-ada-002.
    embedding: Mapped[list[float]] = mapped_column(Vector(_VECTOR_DIM), nullable=False)

    # ── Audit ─────────────────────────────────────────────────────────────
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
    source: Mapped[app.models.postgres.source_model.Source] = relationship(  # type: ignore[name-defined]
        "Source", foreign_keys=[source_id], viewonly=True
    )

    # ── Indexes ───────────────────────────────────────────────────────────
    __table_args__ = (
        # Unique constraint: one embedding per fragment (allows upsert semantics).
        # Use (source_id, fragment_id) as the natural business key.
        Index("uq_fragment_embeddings_source_fragment", "source_id", "fragment_id", unique=True),
        # IVFFlat ANN index created manually in migration 0005.
        # Declared here for documentation; Alembic will not auto-detect it
        # because it is created CONCURRENTLY outside a transaction.
    )
