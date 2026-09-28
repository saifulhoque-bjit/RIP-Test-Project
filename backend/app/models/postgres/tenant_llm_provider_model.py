"""TenantLLMProvider ORM model — a tenant's per-provider LLM configuration.

Tenant ──< TenantLLMProvider (one tenant, many configured providers)

A row's existence means the tenant has configured that provider (at minimum
via ``Tenant.llm_providers`` bulk create/update); ``is_active`` is the
explicit enable/disable toggle a tenant admin flips independently of whether
an API key is stored, so a key can be kept on file while temporarily
disabled. ``api_key_encrypted`` holds the tenant-supplied key (Fernet, see
``app.utils.encryption.encrypt_llm_api_key``/``decrypt_llm_api_key``) used to
test connectivity and, eventually, to call the provider on the tenant's
behalf; ``is_verified``/``last_tested_at``/``last_test_error`` record the
outcome of the most recent connection test (see
``TenantLLMProviderService.test_provider``).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.postgres.tenant_model import Tenant


class TenantLLMProvider(Base):
    """One LLM provider's configuration for a tenant.

    Uniqueness on ``(tenant_id, provider)`` is enforced in Postgres via a
    partial unique index scoped to ``WHERE deleted_at IS NULL`` (see
    migration 0036), not a table-level ``UniqueConstraint`` — a soft-deleted
    row must not block the tenant from configuring that provider again.
    """

    __tablename__ = "tenant_llm_providers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    api_key_encrypted: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    # Soft-delete: set to the current timestamp instead of hard-deleting the
    # row, so a removed provider's key/verification history stays intact.
    # See the partial unique index note above.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None, index=True
    )

    tenant: Mapped[Tenant] = relationship(
        "Tenant",
        back_populates="llm_providers",
    )
