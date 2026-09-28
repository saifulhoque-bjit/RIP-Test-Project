"""Business logic for project-scoped membership (Member assignment).

Assigning a user to a project is a Client-Admin (or super_admin)
responsibility — see ``ProjectService.assert_tenant_admin_or_super_admin``,
enforced at the route layer before these methods run.
"""

from __future__ import annotations

from uuid import UUID

from app.core.constants import ROLE_SUPER_ADMIN
from app.core.exceptions import NotFoundError, ValidationError
from app.core.messages import (
    MSG_PROJECT_MEMBER_NOT_FOUND,
    MSG_PROJECT_MEMBER_TENANT_MISMATCH,
    MSG_PROJECT_NOT_FOUND,
    MSG_USER_NOT_FOUND,
)
from app.db.unit_of_work import UnitOfWork
from app.models.postgres.project_member_model import ProjectMember
from app.schemas.project_member_schema import (
    ProjectMemberOut,
    UserProjectAssignmentOut,
)
from app.services.project_service import ProjectService


class ProjectMemberService:
    """Orchestrates assigning/listing/removing project-scoped memberships."""

    @staticmethod
    def _build_response(members: list[ProjectMember]) -> ProjectMemberOut:
        """Build one grouped entry from all role rows a user holds on a project.

        ``assigned_at``/``assigned_by`` reflect the earliest-granted role —
        i.e. when the user first joined the project in any capacity.
        """
        first = min(members, key=lambda m: m.assigned_at)
        return ProjectMemberOut(
            user_id=first.user_id,
            email=first.user.email,
            name=first.user.name,
            roles=sorted(m.role for m in members),
            assigned_by=first.assigned_by,
            assigned_at=first.assigned_at,
        )

    def assign_member(
        self,
        project_id: UUID,
        target_user_id: UUID,
        roles: list[str],
        assigned_by_id: UUID,
        uow: UnitOfWork,
    ) -> ProjectMemberOut:
        """Set the exact role set (``roles``) for *target_user_id* on *project_id*.

        Replaces any previously-held roles for this user on this project —
        a role missing from *roles* is revoked, one already held is left
        untouched, one newly listed is granted.

        Raises:
            NotFoundError:   If the project or target user doesn't exist.
            ValidationError: If the target user isn't in the project's tenant.
        """
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))

        target_user = uow.users.get(target_user_id)
        if target_user is None:
            raise NotFoundError(MSG_USER_NOT_FOUND.format(user_id=target_user_id))
        if project.tenant_id is not None and target_user.tenant_id != project.tenant_id:
            raise ValidationError(MSG_PROJECT_MEMBER_TENANT_MISMATCH)

        members = uow.project_members.replace_roles(
            project_id=project_id,
            user_id=target_user_id,
            roles=roles,
            assigned_by=assigned_by_id,
        )
        uow.flush()
        for member in members:
            uow.refresh(member)
        uow.commit()
        return self._build_response(members)

    @staticmethod
    def _remove_unlisted_assignments(
        target_user_id: UUID,
        requested_ids: set[UUID],
        requester_roles: list[str],
        requester_tenant_id: UUID | None,
        uow: UnitOfWork,
    ) -> None:
        """Unassign *target_user_id* from every project they currently hold
        a role on, within the requester's tenant scope, that isn't in
        *requested_ids*."""
        is_super_admin = ROLE_SUPER_ADMIN in requester_roles
        current = uow.project_members.list_project_tenants_for_user(target_user_id)
        for current_project_id, current_tenant_id in current:
            if current_project_id in requested_ids:
                continue
            if not is_super_admin and current_tenant_id != requester_tenant_id:
                continue
            uow.project_members.delete_by_project_and_user(current_project_id, target_user_id)

    def assign_projects_for_user(
        self,
        target_user_id: UUID,
        assignments: list[tuple[UUID, list[str]]],
        assigned_by_id: UUID,
        requester_roles: list[str],
        requester_tenant_id: UUID | None,
        uow: UnitOfWork,
    ) -> list[UserProjectAssignmentOut]:
        """Replace *target_user_id*'s full set of project assignments in one
        call, each with the ``member`` role.

        ``assignments`` is the complete desired state, within the
        requester's tenant scope: any project the user previously held a
        role on, that the requester is authorized to manage (their own
        tenant, or any tenant for a super_admin), but that is *not* listed
        here, is fully unassigned. Projects outside the requester's tenant
        scope are left untouched even if omitted. Pass an empty list to
        unassign the user from every project in scope.

        Validated up front for every listed project before anything is
        written, so the whole request is all-or-nothing — a failure on the
        third project never leaves the first two partially assigned:

        Raises:
            NotFoundError:   If the target user, or any listed project, doesn't exist.
            ForbiddenError:  If the requester may not manage members for any
                listed project (not a super_admin, and not the tenant-scoped
                admin of that project — see
                ``ProjectService.assert_tenant_admin_or_super_admin``).
            ValidationError: If the target user isn't in the same tenant as
                any listed project.
        """
        target_user = uow.users.get(target_user_id)
        if target_user is None:
            raise NotFoundError(MSG_USER_NOT_FOUND.format(user_id=target_user_id))

        projects = {}
        for project_id, _roles in assignments:
            project = uow.projects.get_by_uuid(project_id)
            if project is None:
                raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))
            ProjectService.assert_tenant_admin_or_super_admin(
                project=project,
                requester_roles=requester_roles,
                requester_tenant_id=requester_tenant_id,
            )
            if project.tenant_id is not None and target_user.tenant_id != project.tenant_id:
                raise ValidationError(MSG_PROJECT_MEMBER_TENANT_MISMATCH)
            projects[project_id] = project

        requested_ids = {project_id for project_id, _roles in assignments}
        self._remove_unlisted_assignments(
            target_user_id=target_user_id,
            requested_ids=requested_ids,
            requester_roles=requester_roles,
            requester_tenant_id=requester_tenant_id,
            uow=uow,
        )

        results: list[UserProjectAssignmentOut] = []
        for project_id, roles in assignments:
            members = uow.project_members.replace_roles(
                project_id=project_id,
                user_id=target_user_id,
                roles=roles,
                assigned_by=assigned_by_id,
            )
            uow.flush()
            for member in members:
                uow.refresh(member)
            first = min(members, key=lambda m: m.assigned_at)
            results.append(
                UserProjectAssignmentOut(
                    project_id=project_id,
                    project_name=projects[project_id].name,
                    roles=sorted(m.role for m in members),
                    assigned_by=first.assigned_by,
                    assigned_at=first.assigned_at,
                )
            )
        uow.commit()
        return results

    def list_members(self, project_id: UUID, uow: UnitOfWork) -> list[ProjectMemberOut]:
        """Return every project member (grouped: one entry per user, with all their roles).

        404s if the project itself doesn't exist.
        """
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))

        members = uow.project_members.list_by_project(project_id)
        grouped: dict[UUID, list[ProjectMember]] = {}
        for member in members:
            grouped.setdefault(member.user_id, []).append(member)
        return [self._build_response(rows) for rows in grouped.values()]

    def remove_member(self, project_id: UUID, target_user_id: UUID, uow: UnitOfWork) -> None:
        """Remove *target_user_id* from *project_id* entirely (all roles revoked).

        Raises:
            NotFoundError: If the project doesn't exist, or the user isn't a member.
        """
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))
        removed = uow.project_members.delete_by_project_and_user(project_id, target_user_id)
        if not removed:
            raise NotFoundError(MSG_PROJECT_MEMBER_NOT_FOUND.format(user_id=target_user_id))
        uow.commit()
