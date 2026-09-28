"""Route handlers for /roles and /permissions — v1.

Endpoint summary
────────────────
super_admin only:
    GET    /roles                — list all roles with their permissions
    POST   /roles                — create a custom role
    GET    /roles/{role_id}      — get a role by ID
    PATCH  /roles/{role_id}      — update a role's description/permissions
    DELETE /roles/{role_id}      — delete a custom role (built-in roles are immutable)
    GET    /permissions          — list the fixed permission catalogue

Design rules
────────────
- Zero business logic here — all decisions live in RoleService.
- Dynamic role creation lets a super_admin compose new roles from the
  existing, code-enforced permission catalogue — see
  app/services/role_service.py for what's dynamic vs fixed.
- Exception handling is centralised in app/core/exception_handlers.py.
"""

from __future__ import annotations

from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, status

from app.core.constants import ROLE_SUPER_ADMIN
from app.core.messages import (
    MSG_ROLE_CREATED,
    MSG_ROLE_DELETED,
    MSG_ROLE_UPDATED,
    SUMMARY_PERMISSION_LIST,
    SUMMARY_ROLE_CREATE,
    SUMMARY_ROLE_DELETE,
    SUMMARY_ROLE_GET,
    SUMMARY_ROLE_LIST,
    SUMMARY_ROLE_UPDATE,
)
from app.db.unit_of_work import UnitOfWork
from app.deps import get_uow, require_roles
from app.models.postgres.user_model import User
from app.schemas.user_schema import (
    PermissionOut,
    RoleCreateRequest,
    RoleOut,
    RoleUpdateRequest,
)
from app.services.role_service import RoleService
from app.utils.response import ApiResponse

roles_router = APIRouter(prefix="/roles", tags=["Roles"])
permissions_router = APIRouter(prefix="/permissions", tags=["Roles"])

CurrentUow = Annotated[UnitOfWork, Depends(get_uow)]
CurrentSuperAdmin = Annotated[User, Depends(require_roles(ROLE_SUPER_ADMIN))]


@roles_router.get("", status_code=status.HTTP_200_OK, summary=SUMMARY_ROLE_LIST)
def list_roles(_admin: CurrentSuperAdmin, uow: CurrentUow) -> ApiResponse[list[RoleOut]]:
    """GET /roles — list all roles with their permissions."""
    roles = RoleService().list_roles(uow)
    return ApiResponse.ok(data=[RoleOut.model_validate(r) for r in roles])


@roles_router.post("", status_code=status.HTTP_201_CREATED, summary=SUMMARY_ROLE_CREATE)
def create_role(
    body: RoleCreateRequest, _admin: CurrentSuperAdmin, uow: CurrentUow
) -> ApiResponse[RoleOut]:
    """POST /roles — create a custom role with a chosen set of permissions."""
    role = RoleService().create_role(
        name=body.name,
        display_name=body.display_name,
        description=body.description,
        permission_names=body.permission_names,
        uow=uow,
    )
    return ApiResponse.ok(data=RoleOut.model_validate(role), message=MSG_ROLE_CREATED)


@roles_router.get("/{role_id}", status_code=status.HTTP_200_OK, summary=SUMMARY_ROLE_GET)
def get_role(
    role_id: uuid.UUID, _admin: CurrentSuperAdmin, uow: CurrentUow
) -> ApiResponse[RoleOut]:
    """GET /roles/{role_id} — get a role by ID."""
    role = RoleService().get_role(role_id, uow)
    return ApiResponse.ok(data=RoleOut.model_validate(role))


@roles_router.patch("/{role_id}", status_code=status.HTTP_200_OK, summary=SUMMARY_ROLE_UPDATE)
def update_role(
    role_id: uuid.UUID,
    body: RoleUpdateRequest,
    _admin: CurrentSuperAdmin,
    uow: CurrentUow,
) -> ApiResponse[RoleOut]:
    """PATCH /roles/{role_id} — update a role's display name, description, and/or permission set."""
    role = RoleService().update_role(
        role_id=role_id,
        display_name=body.display_name,
        description=body.description,
        permission_names=body.permission_names,
        uow=uow,
    )
    return ApiResponse.ok(data=RoleOut.model_validate(role), message=MSG_ROLE_UPDATED)


@roles_router.delete("/{role_id}", status_code=status.HTTP_200_OK, summary=SUMMARY_ROLE_DELETE)
def delete_role(
    role_id: uuid.UUID, _admin: CurrentSuperAdmin, uow: CurrentUow
) -> ApiResponse[None]:
    """DELETE /roles/{role_id} — delete a custom role (built-in roles are immutable)."""
    RoleService().delete_role(role_id, uow)
    return ApiResponse.ok(message=MSG_ROLE_DELETED)


@permissions_router.get("", status_code=status.HTTP_200_OK, summary=SUMMARY_PERMISSION_LIST)
def list_permissions(
    _admin: CurrentSuperAdmin, uow: CurrentUow
) -> ApiResponse[list[PermissionOut]]:
    """GET /permissions — list the fixed permission catalogue."""
    permissions = uow.permissions.get_all()
    return ApiResponse.ok(data=[PermissionOut.model_validate(p) for p in permissions])
