"""Pydantic v2 schemas for tenant endpoints."""

from __future__ import annotations

from datetime import datetime
import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.core.enums.llm_provider import LLMProvider
from app.core.enums.tenant_status import TenantStatus


class TenantOut(BaseModel):
    """Serialized tenant record returned by tenant endpoints."""

    id: uuid.UUID
    name: str
    code: str
    contact_email: str | None = None
    address: str | None = None
    status: TenantStatus
    providers: list[LLMProvider] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TenantListItem(TenantOut):
    """One row of GET /tenants/ — adds the project count computed alongside
    the list query (not part of ``TenantOut`` since single-tenant endpoints
    like GET /tenants/{id} don't need it)."""

    total_number_of_project: int = Field(ge=0)


class TenantListResponse(BaseModel):
    """Paginated tenant list wrapper, matching ProjectListResponse's shape."""

    model_config = ConfigDict(from_attributes=True)

    items: list[TenantListItem]
    total: int
    skip: int
    limit: int


class TenantStatsResponse(BaseModel):
    """Response for GET /tenants/stats — platform-wide tenant/project counts."""

    total_tenants: int
    active_tenants: int
    # Deprecated: counts tenants still in `pending_invitation` status (i.e.
    # awaiting their first Client Admin invite to be accepted). Superseded by
    # `pending_invitation_client_admin` (a direct count of pending Client
    # Admin invitations) — kept only so existing clients don't break; slated
    # for removal.
    pending_invitation_tenants: int
    pending_invitation_client_admin: int
    deactivated_tenants: int
    total_projects: int


class TenantCreateRequest(BaseModel):
    """Payload for POST /tenants."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=200)
    contact_email: EmailStr
    address: str | None = None
    status: TenantStatus = TenantStatus.ACTIVE
    providers: list[LLMProvider] = Field(..., min_length=1)


class TenantUpdateRequest(BaseModel):
    """Partial-update payload for PATCH /tenants/{tenant_id}.

    Every field is optional so callers can send only the fields they intend
    to change; at least one must be provided.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    contact_email: EmailStr | None = None
    address: str | None = None
    status: TenantStatus | None = None
    providers: list[LLMProvider] | None = Field(
        default=None,
        description="Replaces the full set of enabled providers when provided (including an empty list).",
    )

    @model_validator(mode="after")
    def validate_at_least_one_updatable_field(self) -> TenantUpdateRequest:
        if not self.model_fields_set:
            raise ValueError(
                "At least one field must be provided: name, contact_email, "
                "address, status, providers"
            )
        return self


# ── Per-provider config (enable/disable, API key, connection test) ─────────


class TenantLLMProviderOut(BaseModel):
    """One provider's configuration status for a tenant.

    Only providers the tenant has actually configured (via
    ``PATCH .../llm-providers/{provider}``) appear — one entry per
    ``tenant_llm_providers`` row, not one per supported ``LLMProvider``.
    """

    id: uuid.UUID = Field(description="tenant_llm_providers row id")
    provider: LLMProvider
    is_active: bool
    has_api_key: bool
    api_key_hint: str = Field(default="****", description="Masked hint — never the real key")
    is_verified: bool
    last_tested_at: datetime | None = None
    last_test_error: str | None = None


class TenantLLMProviderListResponse(BaseModel):
    """Response for GET /tenants/{tenant_id}/llm-providers."""

    items: list[TenantLLMProviderOut]


class TenantLLMProviderUpdateRequest(BaseModel):
    """Partial-update payload for PATCH /tenants/{tenant_id}/llm-providers/{provider}.

    Every field is optional so callers can send only the field they intend to
    change (``None``/omitted = leave unchanged); at least one must be
    provided. Setting ``api_key`` invalidates any prior connection-test
    result for that provider (a new key needs re-testing).
    """

    model_config = ConfigDict(extra="forbid")

    api_key: str | None = Field(default=None, min_length=1, max_length=512)
    is_active: bool | None = None

    @field_validator("api_key")
    @classmethod
    def _strip_api_key(cls, value: str | None) -> str | None:
        """Trim incidental whitespace (trailing newline from a pasted key, etc.)."""
        if value is None:
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("api_key must not be blank")
        return stripped

    @model_validator(mode="after")
    def validate_at_least_one_updatable_field(self) -> TenantLLMProviderUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided: api_key, is_active")
        return self


class TenantLLMProviderTestResponse(BaseModel):
    """Response for POST /tenants/{tenant_id}/llm-providers/{provider}/test."""

    provider: LLMProvider
    verified: bool
    tested_at: datetime
    error: str | None = None


class TenantLLMProviderBalanceResponse(BaseModel):
    """Response for GET /tenants/{tenant_id}/llm-providers/{provider}/balance.

    Only returned for providers with a genuine, documented balance endpoint
    reachable via a plain API key (currently DeepSeek only) — other
    providers raise a 400 instead of returning fabricated data.
    """

    provider: LLMProvider
    balance: float
    currency: str
    fetched_at: datetime
