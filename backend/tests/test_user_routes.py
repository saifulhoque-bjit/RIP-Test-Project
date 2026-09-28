"""Unit tests for user routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
import uuid

import pytest

from app.core.constants import ROLE_SUPER_ADMIN
from app.models.postgres.role_model import Role
from app.routes.v1.users import (
    assign_role,
    assign_user_projects,
    deactivate_me,
    get_me,
    get_user,
    list_users,
    remove_user,
    revoke_role,
    update_me,
    update_user_roles,
    update_user_status,
)
from app.schemas.project_member_schema import (
    UserProjectAssignmentItem,
    UserProjectAssignmentsRequest,
)
from app.schemas.user_schema import (
    AssignRoleRequest,
    UpdateUserRolesRequest,
    UserStatusUpdateRequest,
    UserUpdateRequest,
)
from tests.conftest import make_user


def test_self_service_endpoints() -> None:
    user = make_user(name="Self", email="self@example.com")

    me_result = get_me(current_user=user)
    assert me_result.success is True
    assert me_result.data is not None
    assert me_result.data.email == "self@example.com"

    with patch("app.routes.v1.users.UserService.update_profile", return_value=user) as mock_update:
        update_result = update_me(UserUpdateRequest(name="New Name"), user, object())
    assert update_result.success is True
    mock_update.assert_called_once()

    with patch("app.routes.v1.users.UserService.deactivate", return_value=None) as mock_deactivate:
        deactivate_result = deactivate_me(user, object())
    assert deactivate_result.success is True
    mock_deactivate.assert_called_once_with(user.id)


def test_admin_user_endpoints() -> None:
    admin = make_user(email="admin@example.com", roles=[], tenant_id=uuid.uuid4())
    user = make_user(email="member@example.com")

    with patch(
        "app.routes.v1.users.UserService.list_users_with_projects", return_value=([user], 1)
    ) as mock_list:
        list_result = list_users(skip=0, limit=20, uow=object(), current_admin=admin)
    assert list_result.success is True
    assert list_result.data is not None
    assert len(list_result.data) == 1
    mock_list.assert_called_once_with(skip=0, limit=20, tenant_id=admin.tenant_id)

    with patch(
        "app.routes.v1.users.UserService.list_users_with_projects", return_value=([user], 1)
    ) as mock_list:
        list_users(skip=0, limit=20, uow=object(), current_admin=admin, tenant_id=uuid.uuid4())
    # A plain admin's tenant_id param is ignored — always scoped to their own tenant.
    mock_list.assert_called_once_with(skip=0, limit=20, tenant_id=admin.tenant_id)

    with patch("app.routes.v1.users.UserService.get_by_id_scoped", return_value=user) as mock_get:
        get_result = get_user(user_id=uuid.uuid4(), uow=object(), current_admin=admin)
    assert get_result.success is True
    mock_get.assert_called_once()

    with patch("app.routes.v1.users.UserService.assign_role", return_value=user) as mock_assign:
        assign_result = assign_role(
            user_id=uuid.uuid4(),
            body=AssignRoleRequest(role_name="viewer"),
            current_admin=admin,
            uow=object(),
        )
    assert assign_result.success is True
    mock_assign.assert_called_once()

    with patch("app.routes.v1.users.UserService.revoke_role", return_value=user) as mock_revoke:
        revoke_result = revoke_role(
            user_id=uuid.uuid4(),
            role_name="viewer",
            uow=object(),
            current_admin=admin,
        )
    assert revoke_result.success is True
    mock_revoke.assert_called_once()

    with patch(
        "app.routes.v1.users.UserService.update_roles", return_value=user
    ) as mock_update_roles:
        update_roles_result = update_user_roles(
            user_id=uuid.uuid4(),
            body=UpdateUserRolesRequest(role_names=["viewer", "reviewer"]),
            current_admin=admin,
            uow=object(),
        )
    assert update_roles_result.success is True
    mock_update_roles.assert_called_once()

    with patch(
        "app.routes.v1.users.UserService.set_active_status", return_value=user
    ) as mock_set_status:
        status_result = update_user_status(
            user_id=uuid.uuid4(),
            body=UserStatusUpdateRequest(is_active=False),
            current_admin=admin,
            uow=object(),
        )
    assert status_result.success is True
    mock_set_status.assert_called_once()


def test_assign_user_projects_delegates_full_replace_to_service() -> None:
    admin = make_user(email="admin@example.com", roles=[], tenant_id=uuid.uuid4())
    target_user_id = uuid.uuid4()
    project_id = uuid.uuid4()
    body = UserProjectAssignmentsRequest(
        assignments=[UserProjectAssignmentItem(project_id=project_id, roles=["member"])]
    )

    with patch(
        "app.routes.v1.users.ProjectMemberService.assign_projects_for_user", return_value=[]
    ) as mock_assign:
        result = assign_user_projects(
            user_id=target_user_id,
            body=body,
            current_admin=admin,
            uow=object(),
        )

    assert result.success is True
    mock_assign.assert_called_once_with(
        target_user_id=target_user_id,
        assignments=[(project_id, ["member"])],
        assigned_by_id=admin.id,
        requester_roles=admin.role_names,
        requester_tenant_id=admin.tenant_id,
        uow=mock_assign.call_args.kwargs["uow"],
    )


def test_assign_user_projects_accepts_empty_assignments_to_unassign_all() -> None:
    admin = make_user(email="admin@example.com", roles=[], tenant_id=uuid.uuid4())
    body = UserProjectAssignmentsRequest(assignments=[])

    with patch(
        "app.routes.v1.users.ProjectMemberService.assign_projects_for_user", return_value=[]
    ) as mock_assign:
        result = assign_user_projects(
            user_id=uuid.uuid4(),
            body=body,
            current_admin=admin,
            uow=object(),
        )

    assert result.success is True
    assert mock_assign.call_args.kwargs["assignments"] == []


def test_list_users_super_admin_can_filter_by_tenant() -> None:
    super_admin = make_user(email="root@example.com", roles=[Role(name=ROLE_SUPER_ADMIN)])
    user = make_user(email="member@example.com")
    chosen_tenant_id = uuid.uuid4()

    with patch(
        "app.routes.v1.users.UserService.list_users_with_projects", return_value=([user], 1)
    ) as mock_list:
        list_users(
            skip=0, limit=20, uow=object(), current_admin=super_admin, tenant_id=chosen_tenant_id
        )
    mock_list.assert_called_once_with(skip=0, limit=20, tenant_id=chosen_tenant_id)

    with patch(
        "app.routes.v1.users.UserService.list_users_with_projects", return_value=([user], 1)
    ) as mock_list:
        list_users(skip=0, limit=20, uow=object(), current_admin=super_admin)
    # Omitted tenant_id => every tenant, matching the pre-existing super_admin behavior.
    mock_list.assert_called_once_with(skip=0, limit=20, tenant_id=None)


@pytest.mark.asyncio
async def test_remove_user_deletes_cognito_identity_before_soft_deleting() -> None:
    admin = make_user(email="admin@example.com", roles=[], tenant_id=uuid.uuid4())
    target = make_user(email="removed@example.com")

    with (
        patch(
            "app.routes.v1.users.UserService.assert_can_remove", return_value=target
        ) as mock_assert_can_remove,
        patch("app.routes.v1.users.UserService.remove_user", return_value=target) as mock_remove,
        patch(
            "app.routes.v1.users.CognitoAuthService.admin_delete_user",
            new_callable=AsyncMock,
        ) as mock_cognito_delete,
    ):
        result = await remove_user(user_id=target.id, current_admin=admin, uow=object())

    assert result.success is True
    mock_assert_can_remove.assert_called_once()
    mock_cognito_delete.assert_called_once_with(email=target.email)
    mock_remove.assert_called_once()
