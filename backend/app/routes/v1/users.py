"""Route handlers for /users — v1.

Endpoint summary
────────────────
Self-service (any authenticated user):
    GET    /users/me                          — full profile with roles & permissions
    PUT    /users/me                          — update display name
    DELETE /users/me                          — soft-deactivate own account

Admin-only (requires ``admin`` role; scoped to the caller's own tenant
unless the caller is a ``super_admin``):
    GET    /users/                            — paginated user list, with each
                                                 user's projects (owned/assigned)
                                                 and roles (no permissions);
                                                 super_admin may filter by
                                                 tenant_id, or omit it for
                                                 every tenant
    GET    /users/{user_id}                   — get any user by ID
    POST   /users/{user_id}/roles             — assign a role (only a
                                                 super_admin may grant super_admin)
    DELETE /users/{user_id}/roles/{role_name} — revoke a role
    PATCH  /users/{user_id}/roles             — replace the full role set in
                                                 one call (one or more roles)
    POST   /users/{user_id}/project-assignments — assign the user to one or
                                                 more projects in one call,
                                                 each with its own role(s)
    DELETE /users/{user_id}                   — permanently remove a user:
                                                 deletes their Cognito identity
                                                 and soft-deletes the Postgres
                                                 row, freeing their email for
                                                 reuse; cannot target yourself
    PATCH  /users/{user_id}/status             — activate or deactivate a user;
                                                 cannot target yourself

Design rules
────────────
- Zero business logic here — all decisions live in UserService.
- Annotated aliases declared once at module level; reused across handlers.
- Exception handling is centralised in app/core/exception_handlers.py.
"""

from __future__ import annotations

from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, Query, status

from app.core.constants import ROLE_SUPER_ADMIN
from app.core.messages import (
    DESC_USER_LIST_TENANT_FILTER,
    DESC_USER_LIST_TENANT_FILTER_TITLE,
    MSG_USER_DEACTIVATED_SUCCESS,
    MSG_USER_PROJECTS_ASSIGNED,
    MSG_USER_REMOVED,
    MSG_USER_ROLES_UPDATED,
    MSG_USER_STATUS_UPDATED,
    MSG_USER_TOTAL,
    SUMMARY_USER_ASSIGN_PROJECTS,
    SUMMARY_USER_ASSIGN_ROLE,
    SUMMARY_USER_DEACTIVATE_ME,
    SUMMARY_USER_GET,
    SUMMARY_USER_GET_ME,
    SUMMARY_USER_LIST,
    SUMMARY_USER_REMOVE,
    SUMMARY_USER_REVOKE_ROLE,
    SUMMARY_USER_UPDATE_ME,
    SUMMARY_USER_UPDATE_ROLES,
    SUMMARY_USER_UPDATE_STATUS,
)
from app.db.unit_of_work import UnitOfWork
from app.deps import get_current_db_user, get_uow, require_roles
from app.models.postgres.user_model import User
from app.schemas.project_member_schema import (
    UserProjectAssignmentOut,
    UserProjectAssignmentsRequest,
)
from app.schemas.user_schema import (
    AssignRoleRequest,
    UpdateUserRolesRequest,
    UserListItemOut,
    UserProfileOut,
    UserStatusUpdateRequest,
    UserUpdateRequest,
)
from app.services.auth_service import CognitoAuthService
from app.services.project_member_service import ProjectMemberService
from app.services.user_service import UserService
from app.utils.logger import get_logger
from app.utils.response import ApiResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/users", tags=["Users"])

# ── Annotated dependency aliases ────────────────────────────────────────────

CurrentUser = Annotated[User, Depends(get_current_db_user)]
CurrentUow = Annotated[UnitOfWork, Depends(get_uow)]
CurrentAdmin = Annotated[User, Depends(require_roles("admin"))]

SkipQuery = Annotated[int, Query(ge=0)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]
TenantIdFilter = Annotated[
    uuid.UUID | None,
    Query(
        title=DESC_USER_LIST_TENANT_FILTER_TITLE,
        description=DESC_USER_LIST_TENANT_FILTER,
    ),
]


# ── Self-service endpoints ───────────────────────────────────────────────────


@router.get(
    "/me",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_USER_GET_ME,
)
def get_me(
    current_user: CurrentUser,
) -> ApiResponse[UserProfileOut]:
    """GET /users/me — return the caller's full profile including roles and permissions."""
    return ApiResponse.ok(data=UserProfileOut.model_validate(current_user))


@router.put(
    "/me",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_USER_UPDATE_ME,
)
def update_me(
    body: UserUpdateRequest,
    current_user: CurrentUser,
    uow: CurrentUow,
) -> ApiResponse[UserProfileOut]:
    """PUT /users/me — update the caller's display name."""
    updated = UserService(uow).update_profile(current_user.id, name=body.name)
    return ApiResponse.ok(data=UserProfileOut.model_validate(updated))


@router.delete(
    "/me",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_USER_DEACTIVATE_ME,
)
def deactivate_me(
    current_user: CurrentUser,
    uow: CurrentUow,
) -> ApiResponse[None]:
    """DELETE /users/me — soft-deactivate the caller's account (sets ``is_active = False``)."""
    UserService(uow).deactivate(current_user.id)
    return ApiResponse.ok(message=MSG_USER_DEACTIVATED_SUCCESS)


# ── Admin endpoints ──────────────────────────────────────────────────────────


@router.get(
    "/",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_USER_LIST,
)
def list_users(
    uow: CurrentUow,
    current_admin: CurrentAdmin,
    skip: SkipQuery = 0,
    limit: LimitQuery = 20,
    tenant_id: TenantIdFilter = None,
) -> ApiResponse[list[UserListItemOut]]:
    """GET /users/ — return a paginated list of users (admin only; scoped to
    the caller's own tenant unless the caller is a super_admin).

    ``tenant_id`` lets a super_admin narrow the list to one tenant (omit it
    to see every tenant); a plain admin's ``tenant_id`` is ignored since they
    are always scoped to their own tenant.

    Each entry includes the projects the user owns or is assigned to, and
    roles without their permission set (see ``GET /roles`` for that)."""
    tenant_filter = (
        tenant_id if ROLE_SUPER_ADMIN in current_admin.role_names else current_admin.tenant_id
    )
    data, total = UserService(uow).list_users_with_projects(
        skip=skip, limit=limit, tenant_id=tenant_filter
    )
    return ApiResponse.ok(data=data, message=MSG_USER_TOTAL.format(total=total))


@router.get(
    "/{user_id}",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_USER_GET,
)
def get_user(
    user_id: uuid.UUID,
    uow: CurrentUow,
    current_admin: CurrentAdmin,
) -> ApiResponse[UserProfileOut]:
    """GET /users/{user_id} — return a user by ID (admin only; 404 if the
    user belongs to another tenant, unless the caller is a super_admin)."""
    tenant_id = None if ROLE_SUPER_ADMIN in current_admin.role_names else current_admin.tenant_id
    user = UserService(uow).get_by_id_scoped(user_id, requester_tenant_id=tenant_id)
    return ApiResponse.ok(data=UserProfileOut.model_validate(user))


@router.post(
    "/{user_id}/roles",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_USER_ASSIGN_ROLE,
)
def assign_role(
    user_id: uuid.UUID,
    body: AssignRoleRequest,
    current_admin: CurrentAdmin,
    uow: CurrentUow,
) -> ApiResponse[UserProfileOut]:
    """POST /users/{user_id}/roles — assign a named role to the target user
    (admin only, scoped to the caller's own tenant; only a super_admin may
    grant the super_admin role)."""
    tenant_id = None if ROLE_SUPER_ADMIN in current_admin.role_names else current_admin.tenant_id
    user = UserService(uow).assign_role(
        user_id,
        body.role_name,
        assigned_by_id=current_admin.id,
        requester_roles=current_admin.role_names,
        requester_tenant_id=tenant_id,
    )
    return ApiResponse.ok(data=UserProfileOut.model_validate(user))


@router.delete(
    "/{user_id}/roles/{role_name}",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_USER_REVOKE_ROLE,
)
def revoke_role(
    user_id: uuid.UUID,
    role_name: str,
    uow: CurrentUow,
    current_admin: CurrentAdmin,
) -> ApiResponse[UserProfileOut]:
    """DELETE /users/{user_id}/roles/{role_name} — revoke a named role
    (admin only, scoped to the caller's own tenant)."""
    tenant_id = None if ROLE_SUPER_ADMIN in current_admin.role_names else current_admin.tenant_id
    user = UserService(uow).revoke_role(user_id, role_name, requester_tenant_id=tenant_id)
    return ApiResponse.ok(data=UserProfileOut.model_validate(user))


@router.patch(
    "/{user_id}/status",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_USER_UPDATE_STATUS,
)
def update_user_status(
    user_id: uuid.UUID,
    body: UserStatusUpdateRequest,
    current_admin: CurrentAdmin,
    uow: CurrentUow,
) -> ApiResponse[UserProfileOut]:
    """PATCH /users/{user_id}/status — activate or deactivate a user (admin
    only, scoped to the caller's own tenant; cannot target yourself)."""
    tenant_id = None if ROLE_SUPER_ADMIN in current_admin.role_names else current_admin.tenant_id
    user = UserService(uow).set_active_status(
        user_id,
        body.is_active,
        requester_id=current_admin.id,
        requester_tenant_id=tenant_id,
    )
    return ApiResponse.ok(data=UserProfileOut.model_validate(user), message=MSG_USER_STATUS_UPDATED)


@router.patch(
    "/{user_id}/roles",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_USER_UPDATE_ROLES,
)
def update_user_roles(
    user_id: uuid.UUID,
    body: UpdateUserRolesRequest,
    current_admin: CurrentAdmin,
    uow: CurrentUow,
) -> ApiResponse[UserProfileOut]:
    """PATCH /users/{user_id}/roles — replace the user's full role set with
    one or more roles in a single call (admin only, scoped to the caller's
    own tenant; only a super_admin may grant super_admin or admin)."""
    tenant_id = None if ROLE_SUPER_ADMIN in current_admin.role_names else current_admin.tenant_id
    user = UserService(uow).update_roles(
        user_id,
        body.role_names,
        assigned_by_id=current_admin.id,
        requester_roles=current_admin.role_names,
        requester_tenant_id=tenant_id,
    )
    return ApiResponse.ok(data=UserProfileOut.model_validate(user), message=MSG_USER_ROLES_UPDATED)


@router.post(
    "/{user_id}/project-assignments",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_USER_ASSIGN_PROJECTS,
)
def assign_user_projects(
    user_id: uuid.UUID,
    body: UserProjectAssignmentsRequest,
    current_admin: CurrentAdmin,
    uow: CurrentUow,
) -> ApiResponse[list[UserProjectAssignmentOut]]:
    """POST /users/{user_id}/project-assignments — replace the target user's
    full set of project assignments in a single call, each with the
    ``member`` role.

    ``body.assignments`` is the complete desired state, within the caller's
    tenant scope: each listed project's role set fully replaces whatever
    that user previously held on it, and any project the user was
    previously assigned to (in the caller's tenant, or any tenant for a
    super_admin) but that is omitted here is fully unassigned. Pass an empty
    list to unassign the user from every project in scope. Requires
    ``super_admin``, or the tenant-scoped Client Admin of every listed
    project — the whole request is rejected (no partial assignment) if the
    caller lacks access to any one of them.
    """
    result = ProjectMemberService().assign_projects_for_user(
        target_user_id=user_id,
        assignments=[(a.project_id, [r.value for r in a.roles]) for a in body.assignments],
        assigned_by_id=current_admin.id,
        requester_roles=current_admin.role_names,
        requester_tenant_id=current_admin.tenant_id,
        uow=uow,
    )
    return ApiResponse.ok(data=result, message=MSG_USER_PROJECTS_ASSIGNED)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_USER_REMOVE,
)
async def remove_user(
    user_id: uuid.UUID,
    current_admin: CurrentAdmin,
    uow: CurrentUow,
) -> ApiResponse[None]:
    """DELETE /users/{user_id} — permanently remove a user (admin only,
    scoped to the caller's own tenant; cannot target yourself).

    Deletes the target's Cognito identity first, then soft-deletes the
    Postgres row — freeing the email/cognito_sub for a future invite or
    registration while preserving everything that references the user
    (project ownership, source uploads, role-assignment history).
    """
    tenant_id = None if ROLE_SUPER_ADMIN in current_admin.role_names else current_admin.tenant_id
    service = UserService(uow)
    target = service.assert_can_remove(
        user_id, requester_id=current_admin.id, requester_tenant_id=tenant_id
    )

    await CognitoAuthService().admin_delete_user(email=target.email)
    service.remove_user(user_id, requester_id=current_admin.id, requester_tenant_id=tenant_id)

    return ApiResponse.ok(message=MSG_USER_REMOVED)
