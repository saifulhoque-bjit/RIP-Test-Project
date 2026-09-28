"""Unit tests for ProjectMemberService."""

from __future__ import annotations

from datetime import UTC, datetime
import uuid

import pytest

from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.models.postgres.project_member_model import ProjectMember
from app.services.project_member_service import ProjectMemberService
from tests.conftest import make_project, make_user


def _make_member(
    *, project_id, user, role: str, assigned_by=None, assigned_at=None
) -> ProjectMember:
    member = ProjectMember(
        project_id=project_id,
        user_id=user.id,
        role=role,
        assigned_by=assigned_by,
    )
    member.assigned_at = assigned_at or datetime.now(tz=UTC)
    member.user = user
    return member


class TestAssignMember:
    def test_project_not_found(self, uow):
        uow.projects.get_by_uuid.return_value = None

        with pytest.raises(NotFoundError):
            ProjectMemberService().assign_member(
                project_id=uuid.uuid4(),
                target_user_id=uuid.uuid4(),
                roles=["member"],
                assigned_by_id=uuid.uuid4(),
                uow=uow,
            )

    def test_target_user_not_found(self, uow):
        uow.projects.get_by_uuid.return_value = make_project()
        uow.users.get.return_value = None

        with pytest.raises(NotFoundError):
            ProjectMemberService().assign_member(
                project_id=uuid.uuid4(),
                target_user_id=uuid.uuid4(),
                roles=["member"],
                assigned_by_id=uuid.uuid4(),
                uow=uow,
            )

    def test_tenant_mismatch_rejected(self, uow):
        tenant_id = uuid.uuid4()
        project = make_project(tenant_id=tenant_id)
        target_user = make_user(tenant_id=uuid.uuid4())
        uow.projects.get_by_uuid.return_value = project
        uow.users.get.return_value = target_user

        with pytest.raises(ValidationError):
            ProjectMemberService().assign_member(
                project_id=project.id,
                target_user_id=target_user.id,
                roles=["member"],
                assigned_by_id=uuid.uuid4(),
                uow=uow,
            )

    def test_assigns_single_role_in_same_tenant(self, uow):
        tenant_id = uuid.uuid4()
        project = make_project(tenant_id=tenant_id)
        target_user = make_user(tenant_id=tenant_id, email="member@example.com")
        assigned_by_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = project
        uow.users.get.return_value = target_user
        member = _make_member(
            project_id=project.id, user=target_user, role="member", assigned_by=assigned_by_id
        )
        uow.project_members.replace_roles.return_value = [member]

        result = ProjectMemberService().assign_member(
            project_id=project.id,
            target_user_id=target_user.id,
            roles=["member"],
            assigned_by_id=assigned_by_id,
            uow=uow,
        )

        assert result.user_id == target_user.id
        assert result.email == "member@example.com"
        assert result.roles == ["member"]
        uow.project_members.replace_roles.assert_called_once_with(
            project_id=project.id,
            user_id=target_user.id,
            roles=["member"],
            assigned_by=assigned_by_id,
        )
        uow.commit.assert_called_once()

    def test_allows_untenanted_project(self, uow):
        """A project with tenant_id=None accepts any target user (MVP single-tenant deployments)."""
        project = make_project(tenant_id=None)
        target_user = make_user(tenant_id=uuid.uuid4())
        uow.projects.get_by_uuid.return_value = project
        uow.users.get.return_value = target_user
        uow.project_members.replace_roles.return_value = [
            _make_member(project_id=project.id, user=target_user, role="member")
        ]

        result = ProjectMemberService().assign_member(
            project_id=project.id,
            target_user_id=target_user.id,
            roles=["member"],
            assigned_by_id=uuid.uuid4(),
            uow=uow,
        )

        assert result.roles == ["member"]


def _stub_replace_roles(uow, target_user) -> None:
    def _replace(*, project_id, user_id, roles, assigned_by):
        return [
            _make_member(project_id=project_id, user=target_user, role=role, assigned_by=assigned_by)
            for role in roles
        ]

    uow.project_members.replace_roles.side_effect = _replace


class TestAssignProjectsForUser:
    def test_target_user_not_found(self, uow):
        uow.users.get.return_value = None

        with pytest.raises(NotFoundError):
            ProjectMemberService().assign_projects_for_user(
                target_user_id=uuid.uuid4(),
                assignments=[(uuid.uuid4(), ["member"])],
                assigned_by_id=uuid.uuid4(),
                requester_roles=["super_admin"],
                requester_tenant_id=None,
                uow=uow,
            )

    def test_project_not_found(self, uow):
        uow.users.get.return_value = make_user()
        uow.projects.get_by_uuid.return_value = None

        with pytest.raises(NotFoundError):
            ProjectMemberService().assign_projects_for_user(
                target_user_id=uuid.uuid4(),
                assignments=[(uuid.uuid4(), ["member"])],
                assigned_by_id=uuid.uuid4(),
                requester_roles=["super_admin"],
                requester_tenant_id=None,
                uow=uow,
            )

    def test_guard_failure_propagates(self, uow):
        tenant_id = uuid.uuid4()
        other_tenant_id = uuid.uuid4()
        project = make_project(tenant_id=tenant_id)
        uow.users.get.return_value = make_user(tenant_id=tenant_id)
        uow.projects.get_by_uuid.return_value = project

        with pytest.raises(ForbiddenError):
            ProjectMemberService().assign_projects_for_user(
                target_user_id=uuid.uuid4(),
                assignments=[(project.id, ["member"])],
                assigned_by_id=uuid.uuid4(),
                requester_roles=["admin"],
                requester_tenant_id=other_tenant_id,
                uow=uow,
            )

    def test_tenant_mismatch_rejected(self, uow):
        tenant_id = uuid.uuid4()
        project = make_project(tenant_id=tenant_id)
        target_user = make_user(tenant_id=uuid.uuid4())
        uow.users.get.return_value = target_user
        uow.projects.get_by_uuid.return_value = project

        with pytest.raises(ValidationError):
            ProjectMemberService().assign_projects_for_user(
                target_user_id=target_user.id,
                assignments=[(project.id, ["member"])],
                assigned_by_id=uuid.uuid4(),
                requester_roles=["super_admin"],
                requester_tenant_id=None,
                uow=uow,
            )

    def test_assigns_multiple_projects_no_removal(self, uow):
        tenant_id = uuid.uuid4()
        target_user = make_user(tenant_id=tenant_id)
        project_a = make_project(tenant_id=tenant_id)
        project_b = make_project(tenant_id=tenant_id)
        uow.users.get.return_value = target_user
        uow.projects.get_by_uuid.side_effect = lambda pid: {
            project_a.id: project_a,
            project_b.id: project_b,
        }[pid]
        uow.project_members.list_project_tenants_for_user.return_value = [
            (project_a.id, tenant_id),
            (project_b.id, tenant_id),
        ]
        _stub_replace_roles(uow, target_user)

        result = ProjectMemberService().assign_projects_for_user(
            target_user_id=target_user.id,
            assignments=[(project_a.id, ["member"]), (project_b.id, ["member"])],
            assigned_by_id=uuid.uuid4(),
            requester_roles=["super_admin"],
            requester_tenant_id=None,
            uow=uow,
        )

        assert {r.project_id for r in result} == {project_a.id, project_b.id}
        uow.project_members.delete_by_project_and_user.assert_not_called()
        uow.commit.assert_called_once()

    def test_removes_project_omitted_from_request(self, uow):
        """Case 1: resending a smaller assignment list removes the dropped project(s)."""
        tenant_id = uuid.uuid4()
        target_user = make_user(tenant_id=tenant_id)
        kept_project = make_project(tenant_id=tenant_id)
        removed_project_id = uuid.uuid4()
        uow.users.get.return_value = target_user
        uow.projects.get_by_uuid.return_value = kept_project
        uow.project_members.list_project_tenants_for_user.return_value = [
            (kept_project.id, tenant_id),
            (removed_project_id, tenant_id),
        ]
        _stub_replace_roles(uow, target_user)

        ProjectMemberService().assign_projects_for_user(
            target_user_id=target_user.id,
            assignments=[(kept_project.id, ["member"])],
            assigned_by_id=uuid.uuid4(),
            requester_roles=["super_admin"],
            requester_tenant_id=None,
            uow=uow,
        )

        uow.project_members.delete_by_project_and_user.assert_called_once_with(
            removed_project_id, target_user.id
        )

    def test_add_and_remove_in_same_call(self, uow):
        """Case 2: a request can drop one project and add a new one at once."""
        tenant_id = uuid.uuid4()
        target_user = make_user(tenant_id=tenant_id)
        project_kept = make_project(tenant_id=tenant_id)
        project_new = make_project(tenant_id=tenant_id)
        removed_project_id = uuid.uuid4()
        uow.users.get.return_value = target_user
        uow.projects.get_by_uuid.side_effect = lambda pid: {
            project_kept.id: project_kept,
            project_new.id: project_new,
        }[pid]
        uow.project_members.list_project_tenants_for_user.return_value = [
            (project_kept.id, tenant_id),
            (removed_project_id, tenant_id),
        ]
        _stub_replace_roles(uow, target_user)

        result = ProjectMemberService().assign_projects_for_user(
            target_user_id=target_user.id,
            assignments=[(project_kept.id, ["member"]), (project_new.id, ["member"])],
            assigned_by_id=uuid.uuid4(),
            requester_roles=["super_admin"],
            requester_tenant_id=None,
            uow=uow,
        )

        assert {r.project_id for r in result} == {project_kept.id, project_new.id}
        uow.project_members.delete_by_project_and_user.assert_called_once_with(
            removed_project_id, target_user.id
        )

    def test_tenant_admin_does_not_remove_project_outside_own_tenant(self, uow):
        own_tenant_id = uuid.uuid4()
        other_tenant_id = uuid.uuid4()
        target_user = make_user(tenant_id=own_tenant_id)
        project = make_project(tenant_id=own_tenant_id)
        other_tenant_project_id = uuid.uuid4()
        uow.users.get.return_value = target_user
        uow.projects.get_by_uuid.return_value = project
        uow.project_members.list_project_tenants_for_user.return_value = [
            (project.id, own_tenant_id),
            (other_tenant_project_id, other_tenant_id),
        ]
        _stub_replace_roles(uow, target_user)

        ProjectMemberService().assign_projects_for_user(
            target_user_id=target_user.id,
            assignments=[(project.id, ["member"])],
            assigned_by_id=uuid.uuid4(),
            requester_roles=["admin"],
            requester_tenant_id=own_tenant_id,
            uow=uow,
        )

        uow.project_members.delete_by_project_and_user.assert_not_called()

    def test_super_admin_removes_across_tenants(self, uow):
        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()
        target_user = make_user(tenant_id=tenant_a)
        uow.users.get.return_value = target_user
        project_id_a = uuid.uuid4()
        project_id_b = uuid.uuid4()
        uow.project_members.list_project_tenants_for_user.return_value = [
            (project_id_a, tenant_a),
            (project_id_b, tenant_b),
        ]

        result = ProjectMemberService().assign_projects_for_user(
            target_user_id=target_user.id,
            assignments=[],
            assigned_by_id=uuid.uuid4(),
            requester_roles=["super_admin"],
            requester_tenant_id=None,
            uow=uow,
        )

        assert result == []
        assert uow.project_members.delete_by_project_and_user.call_count == 2
        uow.project_members.delete_by_project_and_user.assert_any_call(project_id_a, target_user.id)
        uow.project_members.delete_by_project_and_user.assert_any_call(project_id_b, target_user.id)

    def test_empty_assignments_list_unassigns_everything_in_scope(self, uow):
        tenant_id = uuid.uuid4()
        other_tenant_id = uuid.uuid4()
        target_user = make_user(tenant_id=tenant_id)
        uow.users.get.return_value = target_user
        in_scope_project_id = uuid.uuid4()
        out_of_scope_project_id = uuid.uuid4()
        uow.project_members.list_project_tenants_for_user.return_value = [
            (in_scope_project_id, tenant_id),
            (out_of_scope_project_id, other_tenant_id),
        ]

        result = ProjectMemberService().assign_projects_for_user(
            target_user_id=target_user.id,
            assignments=[],
            assigned_by_id=uuid.uuid4(),
            requester_roles=["admin"],
            requester_tenant_id=tenant_id,
            uow=uow,
        )

        assert result == []
        uow.project_members.delete_by_project_and_user.assert_called_once_with(
            in_scope_project_id, target_user.id
        )
        uow.commit.assert_called_once()


class TestListMembers:
    def test_project_not_found(self, uow):
        uow.projects.get_by_uuid.return_value = None

        with pytest.raises(NotFoundError):
            ProjectMemberService().list_members(uuid.uuid4(), uow)

    def test_returns_one_entry_per_user(self, uow):
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        member_user = make_user(email="a@example.com")
        member_user2 = make_user(email="b@example.com")
        uow.project_members.list_by_project.return_value = [
            _make_member(project_id=project.id, user=member_user, role="member"),
            _make_member(project_id=project.id, user=member_user2, role="member"),
        ]

        result = ProjectMemberService().list_members(project.id, uow)

        assert len(result) == 2
        assert {m.email for m in result} == {"a@example.com", "b@example.com"}


class TestRemoveMember:
    def test_project_not_found(self, uow):
        uow.projects.get_by_uuid.return_value = None

        with pytest.raises(NotFoundError):
            ProjectMemberService().remove_member(uuid.uuid4(), uuid.uuid4(), uow)

    def test_member_not_found(self, uow):
        uow.projects.get_by_uuid.return_value = make_project()
        uow.project_members.delete_by_project_and_user.return_value = False

        with pytest.raises(NotFoundError):
            ProjectMemberService().remove_member(uuid.uuid4(), uuid.uuid4(), uow)

    def test_removes_existing_member(self, uow):
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        uow.project_members.delete_by_project_and_user.return_value = True

        ProjectMemberService().remove_member(project.id, uuid.uuid4(), uow)

        uow.commit.assert_called_once()
