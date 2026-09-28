"""Unit tests for RoleService — dynamic role management (super_admin only)."""

from __future__ import annotations

import uuid

import pytest

from app.core.constants import ROLE_ADMIN, ROLE_MEMBER, ROLE_SUPER_ADMIN
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.postgres.permission_model import Permission
from app.models.postgres.role_model import Role
from app.services.role_service import RoleService


def _make_role(
    *, name: str, display_name: str | None = None, description: str | None = None, permissions=None
) -> Role:
    role = Role(name=name, display_name=display_name or name.title(), description=description)
    role.id = uuid.uuid4()
    role.permissions = permissions or []
    return role


def _make_permission(name: str) -> Permission:
    resource, action = name.split(":")
    perm = Permission(name=name, resource=resource, action=action)
    perm.id = uuid.uuid4()
    return perm


class TestListRoles:
    def test_returns_all_roles(self, uow):
        roles = [_make_role(name="member"), _make_role(name="custom-role")]
        uow.roles.get_all.return_value = roles

        result = RoleService().list_roles(uow)

        assert result == roles


class TestGetRole:
    def test_not_found(self, uow):
        uow.roles.get.return_value = None

        with pytest.raises(NotFoundError):
            RoleService().get_role(uuid.uuid4(), uow)

    def test_returns_role(self, uow):
        role = _make_role(name="member")
        uow.roles.get.return_value = role

        assert RoleService().get_role(role.id, uow) is role


class TestCreateRole:
    def test_rejects_duplicate_name(self, uow):
        uow.roles.get_by_name.return_value = _make_role(name="billing-viewer")

        with pytest.raises(ConflictError):
            RoleService().create_role(
                name="billing-viewer",
                display_name="Billing Viewer",
                description=None,
                permission_names=[],
                uow=uow,
            )

    def test_rejects_unknown_permission(self, uow):
        uow.roles.get_by_name.return_value = None
        uow.permissions.get_by_name.return_value = None

        with pytest.raises(ValidationError):
            RoleService().create_role(
                name="billing-viewer",
                display_name="Billing Viewer",
                description=None,
                permission_names=["billing:view"],
                uow=uow,
            )

    def test_creates_role_with_resolved_permissions(self, uow):
        uow.roles.get_by_name.return_value = None
        perm = _make_permission("project:view")
        uow.permissions.get_by_name.return_value = perm

        role = RoleService().create_role(
            name="billing-viewer",
            display_name="Billing Viewer",
            description="Read-only billing role",
            permission_names=["project:view"],
            uow=uow,
        )

        assert role.name == "billing-viewer"
        assert role.display_name == "Billing Viewer"
        assert role.permissions == [perm]
        uow.add.assert_called_once_with(role)
        uow.commit.assert_called_once()


class TestUpdateRole:
    def test_not_found(self, uow):
        uow.roles.get.return_value = None

        with pytest.raises(NotFoundError):
            RoleService().update_role(
                uuid.uuid4(), display_name=None, description="x", permission_names=None, uow=uow
            )

    def test_updates_description_only(self, uow):
        role = _make_role(name="member", description="old")
        uow.roles.get.return_value = role

        updated = RoleService().update_role(
            role.id,
            display_name=None,
            description="new description",
            permission_names=None,
            uow=uow,
        )

        assert updated.description == "new description"

    def test_updates_display_name(self, uow):
        role = _make_role(name="member", display_name="Member")
        uow.roles.get.return_value = role

        updated = RoleService().update_role(
            role.id, display_name="Team Member", description=None, permission_names=None, uow=uow
        )

        assert updated.display_name == "Team Member"

    def test_replaces_permission_set(self, uow):
        old_perm = _make_permission("project:view")
        role = _make_role(name="member", permissions=[old_perm])
        uow.roles.get.return_value = role
        new_perm = _make_permission("story:approve")
        uow.permissions.get_by_name.return_value = new_perm

        updated = RoleService().update_role(
            role.id,
            display_name=None,
            description=None,
            permission_names=["story:approve"],
            uow=uow,
        )

        assert updated.permissions == [new_perm]

    @pytest.mark.parametrize("builtin_name", [ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_MEMBER])
    def test_builtin_role_description_and_permissions_still_editable(self, uow, builtin_name):
        role = _make_role(name=builtin_name)
        uow.roles.get.return_value = role

        updated = RoleService().update_role(
            role.id,
            display_name="Updated Label",
            description="updated",
            permission_names=[],
            uow=uow,
        )

        assert updated.description == "updated"
        assert updated.display_name == "Updated Label"


class TestDeleteRole:
    def test_not_found(self, uow):
        uow.roles.get.return_value = None

        with pytest.raises(NotFoundError):
            RoleService().delete_role(uuid.uuid4(), uow)

    def test_deletes_custom_role(self, uow):
        role = _make_role(name="billing-viewer")
        uow.roles.get.return_value = role

        RoleService().delete_role(role.id, uow)

        uow.roles.delete.assert_called_once_with(role)
        uow.commit.assert_called_once()

    @pytest.mark.parametrize("builtin_name", [ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_MEMBER])
    def test_builtin_role_cannot_be_deleted(self, uow, builtin_name):
        role = _make_role(name=builtin_name)
        uow.roles.get.return_value = role

        with pytest.raises(ValidationError):
            RoleService().delete_role(role.id, uow)

        uow.roles.delete.assert_not_called()
