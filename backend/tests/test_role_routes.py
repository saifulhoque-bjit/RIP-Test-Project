"""Unit tests for routes/v1/roles.py.

Tests verify that route handlers:
- Delegate to ``RoleService`` (or ``uow.permissions`` for the fixed
  permission catalogue) with the parsed request data.
- Wrap results in ``ApiResponse[T]``.
- Are gated by the ``require_roles(ROLE_SUPER_ADMIN)`` dependency.

``RoleService`` is mocked entirely — business logic is covered by
test_role_service.py.
"""

from __future__ import annotations

import typing
from unittest.mock import MagicMock, patch
import uuid

from app.core.constants import ROLE_SUPER_ADMIN
from app.core.messages import MSG_ROLE_CREATED, MSG_ROLE_DELETED, MSG_ROLE_UPDATED
from app.models.postgres.permission_model import Permission
from app.models.postgres.role_model import Role
from app.routes.v1 import roles as routes_module
from app.routes.v1.roles import (
    create_role,
    delete_role,
    get_role,
    list_permissions,
    list_roles,
    update_role,
)
from app.schemas.user_schema import RoleCreateRequest, RoleUpdateRequest
from tests.conftest import make_user

# ── Helpers ────────────────────────────────────────────────────────────────


def _permission(**kwargs) -> Permission:
    p = Permission()
    p.id = uuid.uuid4()
    p.name = kwargs.get("name", "project:view")
    p.resource = kwargs.get("resource", "project")
    p.action = kwargs.get("action", "view")
    p.description = kwargs.get("description")
    return p


def _role(**kwargs) -> Role:
    r = Role()
    r.id = kwargs.get("id", uuid.uuid4())
    r.name = kwargs.get("name", "custom_role")
    r.display_name = kwargs.get("display_name", "Custom Role")
    r.description = kwargs.get("description")
    r.permissions = kwargs.get("permissions", [])
    return r


def _extract_dependency_roles(func: typing.Callable, param_name: str) -> tuple[str, ...]:
    """Return the roles tuple a ``require_roles(...)``-gated parameter enforces.

    See tests/test_project_member_routes.py's identical helper for rationale
    — resolves the ``Annotated[User, Depends(require_roles(...))]`` alias and
    reads the closed-over roles, without re-testing `require_roles` itself
    (covered by tests/test_deps.py).
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
    def test_list_roles_gated_by_super_admin(self) -> None:
        roles = _extract_dependency_roles(list_roles, "_admin")
        assert set(roles) == {ROLE_SUPER_ADMIN}

    def test_create_role_gated_by_super_admin(self) -> None:
        roles = _extract_dependency_roles(create_role, "_admin")
        assert set(roles) == {ROLE_SUPER_ADMIN}

    def test_get_role_gated_by_super_admin(self) -> None:
        roles = _extract_dependency_roles(get_role, "_admin")
        assert set(roles) == {ROLE_SUPER_ADMIN}

    def test_update_role_gated_by_super_admin(self) -> None:
        roles = _extract_dependency_roles(update_role, "_admin")
        assert set(roles) == {ROLE_SUPER_ADMIN}

    def test_delete_role_gated_by_super_admin(self) -> None:
        roles = _extract_dependency_roles(delete_role, "_admin")
        assert set(roles) == {ROLE_SUPER_ADMIN}

    def test_list_permissions_gated_by_super_admin(self) -> None:
        roles = _extract_dependency_roles(list_permissions, "_admin")
        assert set(roles) == {ROLE_SUPER_ADMIN}


# ── list_roles ───────────────────────────────────────────────────────────


class TestListRoles:
    def test_list_roles_delegates_to_service(self) -> None:
        admin = make_user()
        uow = MagicMock()
        role = _role()

        mock_service_instance = MagicMock()
        mock_service_instance.list_roles = MagicMock(return_value=[role])

        with patch.object(
            routes_module, "RoleService", return_value=mock_service_instance
        ) as mock_service_cls:
            result = list_roles(_admin=admin, uow=uow)

        mock_service_cls.assert_called_once_with()
        mock_service_instance.list_roles.assert_called_once_with(uow)
        assert result.success is True
        assert len(result.data) == 1
        assert result.data[0].name == "custom_role"


# ── create_role ──────────────────────────────────────────────────────────


class TestCreateRole:
    def test_create_role_delegates_to_service(self) -> None:
        admin = make_user()
        uow = MagicMock()
        body = RoleCreateRequest(
            name="reviewer",
            display_name="Reviewer",
            description="Reviews things",
            permission_names=["project:view"],
        )
        role = _role(name="reviewer", display_name="Reviewer")

        mock_service_instance = MagicMock()
        mock_service_instance.create_role = MagicMock(return_value=role)

        with patch.object(routes_module, "RoleService", return_value=mock_service_instance):
            result = create_role(body=body, _admin=admin, uow=uow)

        mock_service_instance.create_role.assert_called_once_with(
            name=body.name,
            display_name=body.display_name,
            description=body.description,
            permission_names=body.permission_names,
            uow=uow,
        )
        assert result.success is True
        assert result.message == MSG_ROLE_CREATED
        assert result.data.name == "reviewer"


# ── get_role ─────────────────────────────────────────────────────────────


class TestGetRole:
    def test_get_role_delegates_to_service(self) -> None:
        admin = make_user()
        uow = MagicMock()
        role = _role()

        mock_service_instance = MagicMock()
        mock_service_instance.get_role = MagicMock(return_value=role)

        with patch.object(routes_module, "RoleService", return_value=mock_service_instance):
            result = get_role(role_id=role.id, _admin=admin, uow=uow)

        mock_service_instance.get_role.assert_called_once_with(role.id, uow)
        assert result.success is True
        assert result.data.id == role.id


# ── update_role ──────────────────────────────────────────────────────────


class TestUpdateRole:
    def test_update_role_delegates_to_service(self) -> None:
        admin = make_user()
        uow = MagicMock()
        role_id = uuid.uuid4()
        body = RoleUpdateRequest(
            display_name="Renamed", description="new desc", permission_names=["project:view"]
        )
        role = _role(id=role_id, display_name="Renamed")

        mock_service_instance = MagicMock()
        mock_service_instance.update_role = MagicMock(return_value=role)

        with patch.object(routes_module, "RoleService", return_value=mock_service_instance):
            result = update_role(role_id=role_id, body=body, _admin=admin, uow=uow)

        mock_service_instance.update_role.assert_called_once_with(
            role_id=role_id,
            display_name=body.display_name,
            description=body.description,
            permission_names=body.permission_names,
            uow=uow,
        )
        assert result.success is True
        assert result.message == MSG_ROLE_UPDATED
        assert result.data.display_name == "Renamed"


# ── delete_role ──────────────────────────────────────────────────────────


class TestDeleteRole:
    def test_delete_role_delegates_to_service(self) -> None:
        admin = make_user()
        uow = MagicMock()
        role_id = uuid.uuid4()

        mock_service_instance = MagicMock()
        mock_service_instance.delete_role = MagicMock(return_value=None)

        with patch.object(routes_module, "RoleService", return_value=mock_service_instance):
            result = delete_role(role_id=role_id, _admin=admin, uow=uow)

        mock_service_instance.delete_role.assert_called_once_with(role_id, uow)
        assert result.success is True
        assert result.message == MSG_ROLE_DELETED
        assert result.data is None


# ── list_permissions ─────────────────────────────────────────────────────


class TestListPermissions:
    def test_list_permissions_delegates_to_uow(self) -> None:
        admin = make_user()
        uow = MagicMock()
        permission = _permission()
        uow.permissions.get_all = MagicMock(return_value=[permission])

        result = list_permissions(_admin=admin, uow=uow)

        uow.permissions.get_all.assert_called_once_with()
        assert result.success is True
        assert len(result.data) == 1
        assert result.data[0].name == "project:view"
