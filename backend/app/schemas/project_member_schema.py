"""Pydantic v2 schemas for project-membership endpoints."""

from __future__ import annotations

from datetime import datetime
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums.project_member_role import ProjectMemberRole


class ProjectMemberCreateRequest(BaseModel):
    """Payload for POST /projects/{project_id}/members.

    ``roles`` replaces the full role set for (project, user) in one call —
    resending a smaller list revokes the roles no longer included.
    """

    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    roles: list[ProjectMemberRole] = Field(..., min_length=1)

    @field_validator("roles")
    @classmethod
    def _dedupe_roles(cls, value: list[ProjectMemberRole]) -> list[ProjectMemberRole]:
        return list(dict.fromkeys(value))


class ProjectMemberOut(BaseModel):
    """Serialized project-membership entry — one per user, with all roles
    they hold on this project, and the user's email/name for display."""

    user_id: uuid.UUID
    email: str
    name: str | None = None
    roles: list[ProjectMemberRole]
    assigned_by: uuid.UUID | None = None
    assigned_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserProjectAssignmentItem(BaseModel):
    """One (project, role-set) pair within a bulk assignment request."""

    model_config = ConfigDict(extra="forbid")

    project_id: uuid.UUID
    roles: list[ProjectMemberRole] = Field(..., min_length=1)

    @field_validator("roles")
    @classmethod
    def _dedupe_roles(cls, value: list[ProjectMemberRole]) -> list[ProjectMemberRole]:
        return list(dict.fromkeys(value))


class UserProjectAssignmentsRequest(BaseModel):
    """Payload for POST /users/{user_id}/project-assignments.

    ``assignments`` is the user's *complete* desired set of project
    assignments, within the requester's tenant scope: each listed project's
    role set fully replaces whatever that user previously held there, and
    any project the user was previously assigned to (that the requester is
    authorized to manage — their own tenant, or any tenant for a
    super_admin) but that is *not* listed here is fully unassigned. Pass an
    empty list to unassign the user from every project in scope.
    """

    model_config = ConfigDict(extra="forbid")

    assignments: list[UserProjectAssignmentItem] = Field(default_factory=list)

    @field_validator("assignments")
    @classmethod
    def _unique_project_ids(
        cls, value: list[UserProjectAssignmentItem]
    ) -> list[UserProjectAssignmentItem]:
        seen: set[uuid.UUID] = set()
        for item in value:
            if item.project_id in seen:
                raise ValueError(f"Duplicate project_id in assignments: {item.project_id}")
            seen.add(item.project_id)
        return value


class UserProjectAssignmentOut(BaseModel):
    """One project's membership result within a bulk assignment response."""

    project_id: uuid.UUID
    project_name: str
    roles: list[ProjectMemberRole]
    assigned_by: uuid.UUID | None = None
    assigned_at: datetime

    model_config = ConfigDict(from_attributes=True)
