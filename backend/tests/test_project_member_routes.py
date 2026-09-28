"""Unit tests for routes/v1/project_members.py.

Tests verify that route handlers:
- Perform the tenant-admin/super_admin membership-management guard
  (``_assert_can_manage_members``) before touching the service.
- Delegate to ``ProjectMemberService`` with the parsed request data.
- Wrap results in ``ApiResponse[T]``.
- Are gated by the expected ``require_roles`` dependency.

``ProjectMemberService`` and ``ProjectService.assert_tenant_admin_or_super_admin``
are mocked entirely — business logic is covered by
test_project_member_service.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
import typing
from unittest.mock import MagicMock, patch
import uuid

import pytest

from app.core.constants import ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.core.enums.project_member_role import ProjectMemberRole
from app.core.exceptions import NotFoundError
from app.core.messages import MSG_PROJECT_MEMBER_ASSIGNED, MSG_PROJECT_MEMBER_REMOVED
from app.routes.v1 import project_members as routes_module
from app.routes.v1.project_members import assign_member, list_members, remove_member
from app.schemas.project_member_schema import ProjectMemberCreateRequest, ProjectMemberOut
from tests.conftest import make_project, make_user

# ── Helpers ────────────────────────────────────────────────────────────────


def _member_out(**kwargs) -> ProjectMemberOut:
    defaults = {
        "user_id": uuid.uuid4(),
        "email": "member@example.com",
        "name": "Member Name",
        "roles": [ProjectMemberRole.MEMBER],
        "assigned_by": uuid.uuid4(),
        "assigned_at": datetime.now(tz=UTC),
    }
    defaults.update(kwargs)
    return ProjectMemberOut(**defaults)


def _extract_dependency_roles(func: typing.Callable, param_name: str) -> tuple[str, ...]:
    """Return the roles tuple a ``require_roles(...)``-gated parameter enforces.

    Resolves the ``Annotated[User, Depends(require_roles(...))]`` alias on
    *func*'s *param_name* parameter and reads the roles captured in the
    ``require_roles`` closure — verifies the correct dependency is wired
    without re-implementing/re-testing `require_roles`'s own logic (that's
    covered by tests/test_deps.py).
    """
    hints = typing.get_type_hints(func, include_extras=True)
    annotated = hints[param_name]
    depends = annotated.__metadata__[0]
    dependency_fn = depends.dependency
    free_vars = dependency_fn.__code__.co_freevars
    closure = dependency_fn.__closure__
    for name, cell in zip(free_vars, closure, strict=False):
        if name == "roles":
            return cell.cell_contents
    raise AssertionError(f"no 'roles' closure var found on {param_name}'s dependency")


# ── Dependency wiring ────────────────────────────────────────────────────


class TestDependencyWiring:
    def test_assign_member_gated_by_admin_or_super_admin(self) -> None:
        roles = _extract_dependency_roles(assign_member, "current_admin")
        assert set(roles) == {ROLE_SUPER_ADMIN, ROLE_ADMIN}

    def test_list_members_gated_by_admin_or_super_admin(self) -> None:
        roles = _extract_dependency_roles(list_members, "current_admin")
        assert set(roles) == {ROLE_SUPER_ADMIN, ROLE_ADMIN}

    def test_remove_member_gated_by_admin_or_super_admin(self) -> None:
        roles = _extract_dependency_roles(remove_member, "current_admin")
        assert set(roles) == {ROLE_SUPER_ADMIN, ROLE_ADMIN}


# ── assign_member ────────────────────────────────────────────────────────


class TestAssignMember:
    def test_assign_member_delegates_to_service(self) -> None:
        admin = make_user()
        uow = MagicMock()
        project_id = uuid.uuid4()
        body = ProjectMemberCreateRequest(user_id=uuid.uuid4(), roles=[ProjectMemberRole.MEMBER])
        member = _member_out()

        mock_service_instance = MagicMock()
        mock_service_instance.assign_member = MagicMock(return_value=member)

        with (
            patch.object(routes_module, "_assert_can_manage_members") as mock_guard,
            patch.object(
                routes_module, "ProjectMemberService", return_value=mock_service_instance
            ) as mock_service_cls,
        ):
            result = assign_member(
                project_id=project_id,
                body=body,
                current_admin=admin,
                uow=uow,
            )

        mock_guard.assert_called_once_with(project_id, admin, uow)
        mock_service_cls.assert_called_once_with()
        mock_service_instance.assign_member.assert_called_once_with(
            project_id=project_id,
            target_user_id=body.user_id,
            roles=[r.value for r in body.roles],
            assigned_by_id=admin.id,
            uow=uow,
        )
        assert result.success is True
        assert result.message == MSG_PROJECT_MEMBER_ASSIGNED
        assert result.data == member

    def test_assign_member_propagates_guard_failure(self) -> None:
        """If the requester isn't the tenant admin/super_admin, the guard's
        exception must propagate — the service is never reached."""
        admin = make_user()
        uow = MagicMock()
        project_id = uuid.uuid4()
        body = ProjectMemberCreateRequest(user_id=uuid.uuid4(), roles=[ProjectMemberRole.MEMBER])

        with (
            patch.object(
                routes_module,
                "_assert_can_manage_members",
                side_effect=NotFoundError("nope"),
            ),
            patch.object(routes_module, "ProjectMemberService") as mock_service_cls,
            pytest.raises(NotFoundError),
        ):
            assign_member(project_id=project_id, body=body, current_admin=admin, uow=uow)

        mock_service_cls.assert_not_called()


# ── list_members ─────────────────────────────────────────────────────────


class TestListMembers:
    def test_list_members_delegates_to_service(self) -> None:
        admin = make_user()
        uow = MagicMock()
        project_id = uuid.uuid4()
        members = [_member_out(), _member_out(email="other@example.com")]

        mock_service_instance = MagicMock()
        mock_service_instance.list_members = MagicMock(return_value=members)

        with (
            patch.object(routes_module, "_assert_can_manage_members") as mock_guard,
            patch.object(routes_module, "ProjectMemberService", return_value=mock_service_instance),
        ):
            result = list_members(project_id=project_id, current_admin=admin, uow=uow)

        mock_guard.assert_called_once_with(project_id, admin, uow)
        mock_service_instance.list_members.assert_called_once_with(project_id=project_id, uow=uow)
        assert result.success is True
        assert result.data == members


# ── remove_member ────────────────────────────────────────────────────────


class TestRemoveMember:
    def test_remove_member_delegates_to_service(self) -> None:
        admin = make_user()
        uow = MagicMock()
        project_id = uuid.uuid4()
        user_id = uuid.uuid4()

        mock_service_instance = MagicMock()
        mock_service_instance.remove_member = MagicMock(return_value=None)

        with (
            patch.object(routes_module, "_assert_can_manage_members") as mock_guard,
            patch.object(routes_module, "ProjectMemberService", return_value=mock_service_instance),
        ):
            result = remove_member(
                project_id=project_id, user_id=user_id, current_admin=admin, uow=uow
            )

        mock_guard.assert_called_once_with(project_id, admin, uow)
        mock_service_instance.remove_member.assert_called_once_with(
            project_id=project_id, target_user_id=user_id, uow=uow
        )
        assert result.success is True
        assert result.message == MSG_PROJECT_MEMBER_REMOVED
        assert result.data is None


# ── _assert_can_manage_members ──────────────────────────────────────────


class TestAssertCanManageMembers:
    def test_raises_not_found_when_project_missing(self) -> None:
        admin = make_user()
        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = None
        project_id = uuid.uuid4()

        with pytest.raises(NotFoundError):
            routes_module._assert_can_manage_members(project_id, admin, uow)

    def test_delegates_to_project_service_guard(self) -> None:
        admin = make_user()
        project = make_project()
        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = project

        with patch.object(
            routes_module.ProjectService, "assert_tenant_admin_or_super_admin"
        ) as mock_assert:
            routes_module._assert_can_manage_members(project.id, admin, uow)

        mock_assert.assert_called_once_with(
            project=project,
            requester_roles=admin.role_names,
            requester_tenant_id=admin.tenant_id,
        )
