"""Tenant ORM model.

Multi-tenant scoping of projects/sources/etc. is a future release — this
table exists so the Super Admin seed has a tenant to attach to without a
later breaking schema change (see ``users.tenant_id``).
"""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums.tenant_status import TenantStatus
from app.db.base import Base

# Imported at runtime (not just TYPE_CHECKING): the "TenantLLMProvider"
# string in the relationship() below is resolved from SQLAlchemy's mapper
# registry, which only contains classes that have actually been imported
# somewhere. Tenant gets imported very broadly (any UnitOfWork user), so
# importing it here is what guarantees TenantLLMProvider is always
# registered wherever Tenant is.
from app.models.postgres.tenant_llm_provider_model import TenantLLMProvider  # noqa: F401


class Tenant(Base):
    """A customer organization."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True, index=True)
    # Short, human-readable identifier derived from ``name`` (e.g. "Acme Corp"
    # -> "ACME"), assigned once at creation and immutable — see
    # TenantService._generate_unique_tenant_code. Used as the prefix for this
    # tenant's Project.code values.
    code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    # Per-tenant counter backing Project.code (e.g. "ACME-0001"), incremented
    # atomically by TenantRepository.increment_project_sequence on every
    # project creation for this tenant.
    project_sequence: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # ── Contact ──────────────────────────────────────────────────────────────
    # contact_email is nullable at the DB level (legacy rows predating this
    # column have none) but required by TenantCreateRequest for every tenant
    # created going forward. unique=True still allows multiple NULLs in
    # Postgres, so legacy rows without a contact_email don't collide.
    contact_email: Mapped[str | None] = mapped_column(
        String(320), nullable=True, unique=True, index=True
    )
    address: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=TenantStatus.ACTIVE.value
    )  # pending_invitation | active | inactive | suspended

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # The set of LLM providers enabled for this tenant. Replaced as a whole
    # collection on create/update (see TenantService) — reassigning
    # ``tenant.llm_providers`` deletes the old rows and inserts the new ones
    # via the delete-orphan cascade, so callers never touch child rows
    # directly.
    llm_providers: Mapped[list[TenantLLMProvider]] = relationship(
        "TenantLLMProvider",
        back_populates="tenant",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def providers(self) -> list[str]:
        """Configured provider values (active or inactive), for TenantOut
        (``from_attributes``) to read. Excludes soft-deleted rows. See
        ``GET /tenants/{tenant_id}/llm-providers`` for per-provider
        ``is_active``/verification detail."""
        return [p.provider for p in self.llm_providers if p.deleted_at is None]
