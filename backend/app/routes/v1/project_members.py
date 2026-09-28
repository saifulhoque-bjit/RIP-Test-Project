"""Route handlers for /projects/{project_id}/members — v1.

Endpoint summary
────────────────
Client Admin (own tenant) or super_admin:
    POST   /projects/{project_id}/members            — set a user's role
                                                         (Member); replaces
                                                         their full role set
    GET    /projects/{project_id}/members             — list project members,
                                                          one entry per user with
                                                          all roles they hold
    DELETE /projects/{project_id}/members/{user_id}   — remove a project member
                                                          (revokes every role)

Design rules
────────────
- Zero business logic here — all decisions live in ProjectMemberService.
- Assigning members is a Client-Admin responsibility, not the project
  owner's — enforced via ProjectService.assert_tenant_admin_or_super_admin,
  which has no owner bypass (see app/services/project_service.py).
- Exception handling is centralised in app/core/exception_handlers.py.
"""

from __future__ import annotations

from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, status

from app.core.constants import ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.core.exceptions import NotFoundError
from app.core.messages import (
    MSG_PROJECT_MEMBER_ASSIGNED,
    MSG_PROJECT_MEMBER_REMOVED,
    MSG_PROJECT_NOT_FOUND,
    SUMMARY_PROJECT_MEMBER_ASSIGN,
    SUMMARY_PROJECT_MEMBER_LIST,
    SUMMARY_PROJECT_MEMBER_REMOVE,
)
from app.db.unit_of_work import UnitOfWork
from app.deps import get_uow, require_roles
from app.models.postgres.user_model import User
from app.schemas.project_member_schema import ProjectMemberCreateRequest, ProjectMemberOut
from app.services.project_member_service import ProjectMemberService
from app.services.project_service import ProjectService
from app.utils.response import ApiResponse

router = APIRouter(prefix="/projects/{project_id}/members", tags=["Project Members"])

CurrentUow = Annotated[UnitOfWork, Depends(get_uow)]
CurrentAdmin = Annotated[User, Depends(require_roles(ROLE_SUPER_ADMIN, ROLE_ADMIN))]


def _assert_can_manage_members(project_id: uuid.UUID, admin: User, uow: UnitOfWork) -> None:
    project = uow.projects.get_by_uuid(project_id)
    if project is None:
        raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))
    ProjectService.assert_tenant_admin_or_super_admin(
        project=project,
        requester_roles=admin.role_names,
        requester_tenant_id=admin.tenant_id,
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary=SUMMARY_PROJECT_MEMBER_ASSIGN,
)
def assign_member(
    project_id: uuid.UUID,
    body: ProjectMemberCreateRequest,
    current_admin: CurrentAdmin,
    uow: CurrentUow,
) -> ApiResponse[ProjectMemberOut]:
    """POST /projects/{project_id}/members — set a user's role(s) on this project."""
    _assert_can_manage_members(project_id, current_admin, uow)
    member = ProjectMemberService().assign_member(
        project_id=project_id,
        target_user_id=body.user_id,
        roles=[r.value for r in body.roles],
        assigned_by_id=current_admin.id,
        uow=uow,
    )
    return ApiResponse.ok(data=member, message=MSG_PROJECT_MEMBER_ASSIGNED)


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_PROJECT_MEMBER_LIST,
)
def list_members(
    project_id: uuid.UUID,
    current_admin: CurrentAdmin,
    uow: CurrentUow,
) -> ApiResponse[list[ProjectMemberOut]]:
    """GET /projects/{project_id}/members — list project members."""
    _assert_can_manage_members(project_id, current_admin, uow)
    members = ProjectMemberService().list_members(project_id=project_id, uow=uow)
    return ApiResponse.ok(data=members)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_PROJECT_MEMBER_REMOVE,
)
def remove_member(
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    current_admin: CurrentAdmin,
    uow: CurrentUow,
) -> ApiResponse[None]:
    """DELETE /projects/{project_id}/members/{user_id} — remove a project member."""
    _assert_can_manage_members(project_id, current_admin, uow)
    ProjectMemberService().remove_member(project_id=project_id, target_user_id=user_id, uow=uow)
    return ApiResponse.ok(message=MSG_PROJECT_MEMBER_REMOVED)
