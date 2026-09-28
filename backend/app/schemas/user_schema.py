"""Pydantic v2 schemas for user profile, role, and permission endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# Project creation is admin/super_admin-only (see POST /projects), so every
# project owner necessarily holds one of those two tenant-wide roles —
# ProjectSummaryOut.roles surfaces that role for an owner instead of leaving
# an uninformative blank, alongside any ProjectMember role(s) held.
ProjectSummaryRole = Literal["admin", "super_admin", "member"]


# ── Permission ─────────────────────────────────────────────────────────────


class PermissionOut(BaseModel):
    """Serialized representation of a single permission."""

    id: uuid.UUID
    name: str
    resource: str
    action: str
    description: str | None = None

    model_config = {"from_attributes": True}


# ── Role ───────────────────────────────────────────────────────────────────


class RoleOut(BaseModel):
    """Serialized role with its full permission set."""

    id: uuid.UUID
    name: str
    display_name: str
    description: str | None = None
    permissions: list[PermissionOut] = []

    model_config = {"from_attributes": True}


class RoleBasicOut(BaseModel):
    """Serialized role without its permission set — used in user listings
    where permissions aren't needed (see ``GET /users/``)."""

    id: uuid.UUID
    name: str
    display_name: str
    description: str | None = None

    model_config = {"from_attributes": True}


# ── User profile ────────────────────────────────────────────────────────────


class UserProfileOut(BaseModel):
    """Full user profile returned by /users/me and admin user endpoints."""

    id: uuid.UUID
    cognito_sub: str
    email: EmailStr
    name: str | None = None
    is_active: bool
    is_verified: bool
    tenant_id: uuid.UUID | None = None
    roles: list[RoleOut] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectSummaryOut(BaseModel):
    """Minimal project reference shown in a user listing.

    ``is_owner`` reflects project ownership (``Project.owner_id``) — not a
    role by itself. ``roles`` is the set of roles that actually explain the
    user's access to this project: their tenant-wide ``"admin"``/
    ``"super_admin"`` role if they own it (project creation is
    admin/super_admin-only, so an owner always holds one of these), plus the
    ``ProjectMember`` ``"member"`` role if explicitly granted. A plain member
    with no ownership sees just their ProjectMember role; an admin who owns a
    project sees ``["admin"]`` (plus ``"member"`` too, if also explicitly
    assigned).
    """

    id: uuid.UUID
    name: str
    status: str
    is_owner: bool
    roles: list[ProjectSummaryRole] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class UserListItemOut(BaseModel):
    """User summary returned by ``GET /users/`` — roles without their
    permission set, plus the projects this user owns or is assigned to."""

    id: uuid.UUID
    cognito_sub: str
    email: EmailStr
    name: str | None = None
    is_active: bool
    is_verified: bool
    tenant_id: uuid.UUID | None = None
    roles: list[RoleBasicOut] = []
    projects: list[ProjectSummaryOut] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Request payloads ────────────────────────────────────────────────────────


class UserUpdateRequest(BaseModel):
    """Payload for PUT /users/me."""

    name: str


class AssignRoleRequest(BaseModel):
    """Payload for POST /users/{user_id}/roles."""

    role_name: str


class UserStatusUpdateRequest(BaseModel):
    """Payload for PATCH /users/{user_id}/status."""

    model_config = ConfigDict(extra="forbid")

    is_active: bool = Field(description="True to activate the user, false to deactivate them.")


class UpdateUserRolesRequest(BaseModel):
    """Payload for PATCH /users/{user_id}/roles.

    ``role_names`` replaces the user's full tenant-level role set in one
    call — resending a smaller list revokes the roles no longer included;
    roles already held are left untouched (unlike POST, which only adds).
    """

    model_config = ConfigDict(extra="forbid")

    role_names: list[str] = Field(..., min_length=1)

    @field_validator("role_names")
    @classmethod
    def _dedupe_role_names(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


# ── Role management (dynamic RBAC, super_admin only) ────────────────────────


class RoleCreateRequest(BaseModel):
    """Payload for POST /roles — create a custom role."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=255)
    permission_names: list[str] = Field(default_factory=list)


class RoleUpdateRequest(BaseModel):
    """Payload for PATCH /roles/{role_id}.

    All fields are optional; ``permission_names``, when provided, replaces
    the role's full permission set rather than merging with it.
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    permission_names: list[str] | None = None
