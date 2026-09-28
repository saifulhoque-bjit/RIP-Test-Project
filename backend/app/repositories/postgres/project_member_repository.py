"""ProjectMember repository — typed query helpers on top of BaseRepository."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.postgres.project_member_model import ProjectMember
from app.models.postgres.project_model import Project
from app.repositories.postgres.base_repository import BaseRepository


class ProjectMemberRepository(BaseRepository[ProjectMember]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, ProjectMember)

    def list_project_tenants_for_user(self, user_id: UUID) -> list[tuple[UUID, UUID | None]]:
        """Return ``(project_id, tenant_id)`` for every project this user
        holds any role on — one row per distinct project, regardless of how
        many roles they hold there."""
        return (
            self._session.query(ProjectMember.project_id, Project.tenant_id)
            .join(Project, Project.id == ProjectMember.project_id)
            .filter(ProjectMember.user_id == user_id)
            .distinct()
            .all()
        )

    def list_roles_for_user(self, project_id: UUID, user_id: UUID) -> list[ProjectMember]:
        """Return every role row this user holds on this project (0, 1, or more)."""
        return (
            self._session.query(ProjectMember)
            .filter(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
            .all()
        )

    def list_by_project(self, project_id: UUID) -> list[ProjectMember]:
        """Return every membership row for a project (one row per role held)."""
        return (
            self._session.query(ProjectMember)
            .filter(ProjectMember.project_id == project_id)
            .order_by(ProjectMember.assigned_at.asc())
            .all()
        )

    def replace_roles(
        self,
        project_id: UUID,
        user_id: UUID,
        roles: list[str],
        assigned_by: UUID | None,
    ) -> list[ProjectMember]:
        """Set the exact role set for this (project, user) pair.

        Roles no longer in *roles* are revoked (row deleted); roles already
        held are left untouched (original ``assigned_at``/``assigned_by``
        preserved); newly granted roles get a fresh row. Returns the
        resulting rows, in the order of *roles*.
        """
        existing = {m.role: m for m in self.list_roles_for_user(project_id, user_id)}
        desired = dict.fromkeys(roles)  # dedupe, preserve order

        for role, member in existing.items():
            if role not in desired:
                self._session.delete(member)

        result: list[ProjectMember] = []
        for role in desired:
            member = existing.get(role)
            if member is None:
                member = ProjectMember(
                    project_id=project_id,
                    user_id=user_id,
                    role=role,
                    assigned_by=assigned_by,
                )
                self._session.add(member)
            result.append(member)
        return result

    def delete_by_project_and_user(self, project_id: UUID, user_id: UUID) -> bool:
        """Remove every role row for this (project, user) pair. Returns False if none existed."""
        members = self.list_roles_for_user(project_id, user_id)
        if not members:
            return False
        for member in members:
            self._session.delete(member)
        return True
