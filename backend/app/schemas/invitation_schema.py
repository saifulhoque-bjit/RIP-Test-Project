"""Pydantic v2 schemas for invitation endpoints."""

from __future__ import annotations

from datetime import datetime
import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.core.enums.invitation_status import InvitationStatus


class InvitationCreateRequest(BaseModel):
    """Payload for POST /tenants/{tenant_id}/invitations.

    ``role`` is a plain string, not a fixed enum, but is always required and
    restricted to exactly one value depending on who's calling: a
    super_admin must set it to ``admin``; a Client Admin must set it to
    ``member``. Neither may choose the other's role, and there is no
    default — both rules are enforced in the route, not here, since they
    depend on who's calling (see app/routes/v1/tenant_invitations.py).
    """

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    name: str = Field(..., min_length=1, max_length=200)
    role: str | None = Field(default=None, min_length=1, max_length=64)


class InvitationOut(BaseModel):
    """Serialized invitation record returned to the inviting admin."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    status: InvitationStatus
    expires_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class InvitationListResponse(BaseModel):
    """Paginated invitation list wrapper, matching TenantListResponse's shape."""

    model_config = ConfigDict(from_attributes=True)

    items: list[InvitationOut]
    total: int
    skip: int
    limit: int


class InvitationPublicOut(BaseModel):
    """Public, minimal view returned by GET /invitations/{token}.

    Deliberately excludes internal IDs — only what the accept-invitation
    page needs to prefill (email, tenant name, expiry).
    """

    email: str
    tenant_name: str
    expires_at: datetime

    model_config = {"from_attributes": True}


class InvitationAcceptRequest(BaseModel):
    """Payload for POST /invitations/{token}/accept."""

    model_config = ConfigDict(extra="forbid")

    password: str = Field(..., min_length=8)
    confirm_password: str = Field(..., min_length=8)

    @model_validator(mode="after")
    def validate_passwords_match(self) -> InvitationAcceptRequest:
        if self.password != self.confirm_password:
            raise ValueError("password and confirm_password must match.")
        return self
