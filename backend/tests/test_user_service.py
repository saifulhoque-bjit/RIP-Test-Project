"""Unit tests for UserService.

Strategy:
- All repository interactions are replaced by MagicMock so no DB is needed.
- The UnitOfWork is constructed manually with mock repositories injected.
- Every public method and significant branch is covered.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
import uuid

import pytest

from app.core.enums.notification_type import NotificationType
from app.core.enums.tenant_status import TenantStatus
from app.core.exceptions import ForbiddenError, NotFoundError
from app.db.unit_of_work import UnitOfWork
from app.models.postgres.role_model import Role
from app.models.postgres.tenant_model import Tenant
from app.models.postgres.user_model import User
from app.services.user_service import UserService

# ── Helpers ────────────────────────────────────────────────────────────────


def _make_user(
    *,
    cognito_sub: str = "sub-abc",
    email: str = "alice@example.com",
    username: str | None = "alice@example.com",
    name: str | None = "Alice",
    is_active: bool = True,
    is_verified: bool = False,
    roles: list | None = None,
    tenant_id: uuid.UUID | None = None,
) -> User:
    """Build a detached User instance with SQLAlchemy instrumentation."""
    u = User()
    u.id = uuid.uuid4()
    u.cognito_sub = cognito_sub
    u.email = email
    u.username = username
    u.name = name
    u.is_active = is_active
    u.is_verified = is_verified
    u.roles = roles if roles is not None else []
    u.tenant_id = tenant_id
    u.created_at = datetime.now(tz=UTC)
    u.updated_at = datetime.now(tz=UTC)
    return u


def _make_tenant(*, status: str = TenantStatus.ACTIVE.value) -> Tenant:
    """Build a detached Tenant instance."""
    t = Tenant()
    t.id = uuid.uuid4()
    t.status = status
    return t


def _make_role(name: str = "viewer") -> Role:
    """Build a detached Role instance."""
    r = Role()
    r.id = uuid.uuid4()
    r.name = name
    r.display_name = name.title()
    r.description = f"{name} role"
    r.created_at = datetime.now(tz=UTC)
    return r


def _make_uow() -> MagicMock:
    """Return a fully-mocked UnitOfWork."""
    uow = MagicMock(spec=UnitOfWork)
    uow.users = MagicMock()
    uow.roles = MagicMock()
    uow.projects = MagicMock()
    uow.projects.get_projects_by_user_ids.return_value = {}
    uow.add = MagicMock()
    uow.flush = MagicMock()
    uow.refresh = MagicMock()
    uow.commit = MagicMock()
    uow.rollback = MagicMock()
    uow.session = MagicMock()
    return uow


# ── get_or_create_by_cognito_sub ───────────────────────────────────────────


class TestGetOrCreateByCognitoSub:
    def test_creates_new_user_when_not_found(self) -> None:
        uow = _make_uow()
        uow.users.get_by_cognito_sub.return_value = None
        uow.users.get_by_email.return_value = None
        uow.roles.get_by_name.return_value = None  # no viewer role

        def _refresh(entity):
            if not entity.id:
                entity.id = uuid.uuid4()

        uow.refresh.side_effect = _refresh

        svc = UserService(uow)
        user = svc.get_or_create_by_cognito_sub(
            sub="new-sub",
            email="new@example.com",
            name="New User",
            is_verified=False,
        )

        uow.add.assert_called()
        uow.flush.assert_called()
        uow.commit.assert_called()
        assert user.email == "new@example.com"
        assert user.cognito_sub == "new-sub"

    def test_assigns_default_viewer_role_on_create(self) -> None:
        uow = _make_uow()
        uow.users.get_by_cognito_sub.return_value = None
        uow.users.get_by_email.return_value = None
        viewer_role = _make_role("viewer")
        uow.roles.get_by_name.return_value = viewer_role

        created_user: User | None = None

        def _add(entity):
            nonlocal created_user
            if isinstance(entity, User):
                created_user = entity

        uow.add.side_effect = _add

        svc = UserService(uow)
        svc.get_or_create_by_cognito_sub(
            sub="s1", email="u@example.com", name="User", is_verified=True
        )

        assert created_user is not None
        assert viewer_role in created_user.roles

    def test_no_default_role_assigned_when_viewer_missing(self) -> None:
        uow = _make_uow()
        uow.users.get_by_cognito_sub.return_value = None
        uow.users.get_by_email.return_value = None
        uow.roles.get_by_name.return_value = None  # viewer role absent

        created_user: User | None = None

        def _add(entity):
            nonlocal created_user
            if isinstance(entity, User):
                created_user = entity

        uow.add.side_effect = _add

        svc = UserService(uow)
        svc.get_or_create_by_cognito_sub(
            sub="s2", email="b@example.com", name=None, is_verified=False
        )

        assert created_user is not None
        assert created_user.roles == []

    def test_returns_existing_user_without_overwriting_populated_fields(self) -> None:
        existing = _make_user(name="Old Name", is_verified=False)
        uow = _make_uow()
        uow.users.get_by_cognito_sub.return_value = existing

        svc = UserService(uow)
        result = svc.get_or_create_by_cognito_sub(
            sub=existing.cognito_sub,
            email=existing.email,
            name="New Name",
            is_verified=True,
            username="new-username",
        )

        assert result.name == "Old Name"
        assert result.username == "alice@example.com"
        assert result.is_verified is True
        uow.commit.assert_called_once()

    def test_backfills_missing_sub_when_found_by_email(self) -> None:
        existing = _make_user(cognito_sub="")
        uow = _make_uow()
        uow.users.get_by_cognito_sub.return_value = None
        uow.users.get_by_email.return_value = existing

        svc = UserService(uow)
        svc.get_or_create_by_cognito_sub(
            sub="new-sub",
            email=existing.email,
            name=existing.name,
            is_verified=True,
        )

        assert existing.cognito_sub == "new-sub"

    def test_backfills_missing_fields_only(self) -> None:
        existing = _make_user(
            cognito_sub="sub-1",
            email="",
            username=None,
            name=None,
            is_verified=False,
        )
        uow = _make_uow()
        uow.users.get_by_cognito_sub.return_value = existing

        svc = UserService(uow)
        result = svc.get_or_create_by_cognito_sub(
            sub="sub-1",
            email="filled@example.com",
            username="filled-user",
            name="Filled Name",
            is_verified=True,
        )

        assert result.email == "filled@example.com"
        assert result.username == "filled-user"
        assert result.name == "Filled Name"
        assert result.is_verified is True
        uow.add.assert_called_once_with(existing)
        uow.commit.assert_called_once()

    def test_raises_forbidden_when_user_is_deactivated(self) -> None:
        deactivated = _make_user(is_active=False)
        uow = _make_uow()
        uow.users.get_by_cognito_sub.return_value = deactivated

        svc = UserService(uow)
        with pytest.raises(ForbiddenError, match="deactivated"):
            svc.get_or_create_by_cognito_sub(
                sub=deactivated.cognito_sub,
                email=deactivated.email,
                name=None,
                is_verified=False,
            )

    def test_does_not_update_when_name_unchanged(self) -> None:
        existing = _make_user(name="Same Name", is_verified=True)
        uow = _make_uow()
        uow.users.get_by_cognito_sub.return_value = existing

        svc = UserService(uow)
        svc.get_or_create_by_cognito_sub(
            sub=existing.cognito_sub,
            email=existing.email,
            name="Same Name",
            is_verified=True,
        )

        # unchanged record must return without persistence calls
        uow.add.assert_not_called()
        uow.flush.assert_not_called()
        uow.commit.assert_not_called()


# ── get_authenticated_user ──────────────────────────────────────────────────


class TestGetAuthenticatedUser:
    def test_returns_existing_user_by_cognito_sub(self) -> None:
        existing = _make_user()
        uow = _make_uow()
        uow.users.get_by_cognito_sub.return_value = existing

        svc = UserService(uow)
        result = svc.get_authenticated_user(
            sub=existing.cognito_sub,
            email=existing.email,
            name=existing.name,
            is_verified=True,
        )

        assert result is existing
        uow.users.get_by_email.assert_not_called()

    def test_falls_back_to_email_when_sub_not_found(self) -> None:
        existing = _make_user()
        uow = _make_uow()
        uow.users.get_by_cognito_sub.return_value = None
        uow.users.get_by_email.return_value = existing

        svc = UserService(uow)
        result = svc.get_authenticated_user(
            sub="unseen-sub", email=existing.email, name=None, is_verified=True
        )

        assert result is existing

    def test_raises_forbidden_when_no_local_user_exists(self) -> None:
        """Cognito auth succeeding is not enough — no accepted invitation
        means no local row, and this must not create one (unlike
        get_or_create_by_cognito_sub)."""
        uow = _make_uow()
        uow.users.get_by_cognito_sub.return_value = None
        uow.users.get_by_email.return_value = None

        svc = UserService(uow)
        with pytest.raises(ForbiddenError, match="Invalid credentials"):
            svc.get_authenticated_user(
                sub="never-invited-sub",
                email="never-invited@example.com",
                name="Nobody",
                is_verified=True,
            )

        uow.add.assert_not_called()
        uow.flush.assert_not_called()
        uow.commit.assert_not_called()

    def test_raises_forbidden_when_user_is_deactivated(self) -> None:
        deactivated = _make_user(is_active=False)
        uow = _make_uow()
        uow.users.get_by_cognito_sub.return_value = deactivated

        svc = UserService(uow)
        with pytest.raises(ForbiddenError, match="deactivated"):
            svc.get_authenticated_user(
                sub=deactivated.cognito_sub,
                email=deactivated.email,
                name=None,
                is_verified=False,
            )

    def test_raises_forbidden_when_tenant_is_deactivated(self) -> None:
        user = _make_user(tenant_id=uuid.uuid4())
        user.tenant = _make_tenant(status=TenantStatus.INACTIVE.value)
        uow = _make_uow()
        uow.users.get_by_cognito_sub.return_value = user

        svc = UserService(uow)
        with pytest.raises(ForbiddenError, match="deactivated"):
            svc.get_authenticated_user(
                sub=user.cognito_sub,
                email=user.email,
                name=None,
                is_verified=False,
            )

    def test_allows_login_when_tenant_is_active(self) -> None:
        user = _make_user(tenant_id=uuid.uuid4())
        user.tenant = _make_tenant(status=TenantStatus.ACTIVE.value)
        uow = _make_uow()
        uow.users.get_by_cognito_sub.return_value = user

        svc = UserService(uow)
        result = svc.get_authenticated_user(
            sub=user.cognito_sub, email=user.email, name=None, is_verified=False
        )

        assert result is user

    def test_backfills_missing_fields_without_creating(self) -> None:
        existing = _make_user(email="", username=None, name=None, is_verified=False)
        uow = _make_uow()
        uow.users.get_by_cognito_sub.return_value = existing

        svc = UserService(uow)
        result = svc.get_authenticated_user(
            sub=existing.cognito_sub,
            email="filled@example.com",
            username="filled-user",
            name="Filled Name",
            is_verified=True,
        )

        assert result.email == "filled@example.com"
        assert result.username == "filled-user"
        assert result.name == "Filled Name"
        assert result.is_verified is True
        uow.add.assert_called_once_with(existing)
        uow.commit.assert_called_once()


# ── get_by_id ──────────────────────────────────────────────────────────────


class TestGetById:
    def test_returns_user_when_found(self) -> None:
        user = _make_user()
        uow = _make_uow()
        uow.users.get.return_value = user

        svc = UserService(uow)
        result = svc.get_by_id(user.id)

        assert result is user
        uow.users.get.assert_called_once_with(user.id)

    def test_raises_not_found_when_missing(self) -> None:
        uow = _make_uow()
        uow.users.get.return_value = None

        svc = UserService(uow)
        with pytest.raises(NotFoundError):
            svc.get_by_id(uuid.uuid4())


# ── list_users ─────────────────────────────────────────────────────────────


class TestListUsers:
    def test_returns_paginated_result(self) -> None:
        users = [_make_user(email=f"u{i}@example.com") for i in range(3)]
        uow = _make_uow()
        uow.users.get_paginated.return_value = (users, 3)

        svc = UserService(uow)
        result, total = svc.list_users(skip=0, limit=10)

        assert total == 3
        assert len(result) == 3
        uow.users.get_paginated.assert_called_once_with(skip=0, limit=10, tenant_id=None)

    def test_scopes_to_tenant_when_provided(self) -> None:
        uow = _make_uow()
        uow.users.get_paginated.return_value = ([], 0)
        tenant_id = uuid.uuid4()

        svc = UserService(uow)
        svc.list_users(skip=0, limit=10, tenant_id=tenant_id)

        uow.users.get_paginated.assert_called_once_with(skip=0, limit=10, tenant_id=tenant_id)

    def test_empty_list(self) -> None:
        uow = _make_uow()
        uow.users.get_paginated.return_value = ([], 0)

        svc = UserService(uow)
        result, total = svc.list_users()

        assert total == 0
        assert result == []


# ── list_users_with_projects ────────────────────────────────────────────────


class TestListUsersWithProjects:
    def test_attaches_owned_and_assigned_projects(self) -> None:
        from tests.conftest import make_project

        owner = _make_user(email="owner@example.com", roles=[_make_role(name="admin")])
        member = _make_user(email="member@example.com", roles=[_make_role(name="member")])
        uow = _make_uow()
        uow.users.get_paginated.return_value = ([owner, member], 2)
        owned_project = make_project(name="Alpha", owner_id=owner.id)
        assigned_project = make_project(name="Beta")
        uow.projects.get_projects_by_user_ids.return_value = {
            owner.id: [(owned_project, True, [])],
            member.id: [(assigned_project, False, ["member"])],
        }

        svc = UserService(uow)
        result, total = svc.list_users_with_projects(skip=0, limit=10)

        assert total == 2
        uow.projects.get_projects_by_user_ids.assert_called_once_with([owner.id, member.id])

        owner_item = next(u for u in result if u.email == "owner@example.com")
        assert [p.name for p in owner_item.projects] == ["Alpha"]
        assert owner_item.projects[0].is_owner is True
        assert owner_item.projects[0].roles == ["admin"]
        assert owner_item.roles[0].name == "admin"

        member_item = next(u for u in result if u.email == "member@example.com")
        assert [p.name for p in member_item.projects] == ["Beta"]
        assert member_item.projects[0].is_owner is False
        assert member_item.projects[0].roles == ["member"]

    def test_owner_who_also_holds_a_role_reports_both(self) -> None:
        from tests.conftest import make_project

        user = _make_user(email="owner-member@example.com", roles=[_make_role(name="admin")])
        uow = _make_uow()
        uow.users.get_paginated.return_value = ([user], 1)
        project = make_project(name="Delta", owner_id=user.id)
        uow.projects.get_projects_by_user_ids.return_value = {
            user.id: [(project, True, ["member"])],
        }

        svc = UserService(uow)
        result, total = svc.list_users_with_projects(skip=0, limit=10)

        assert result[0].projects[0].is_owner is True
        assert sorted(result[0].projects[0].roles) == ["admin", "member"]

    def test_super_admin_owner_reports_super_admin_role(self) -> None:
        from tests.conftest import make_project

        user = _make_user(email="super@example.com", roles=[_make_role(name="super_admin")])
        uow = _make_uow()
        uow.users.get_paginated.return_value = ([user], 1)
        project = make_project(name="Epsilon", owner_id=user.id)
        uow.projects.get_projects_by_user_ids.return_value = {
            user.id: [(project, True, [])],
        }

        svc = UserService(uow)
        result, total = svc.list_users_with_projects(skip=0, limit=10)

        assert result[0].projects[0].roles == ["super_admin"]

    def test_roles_omit_permissions_field(self) -> None:
        role = _make_role(name="member")
        user = _make_user(roles=[role])
        uow = _make_uow()
        uow.users.get_paginated.return_value = ([user], 1)

        svc = UserService(uow)
        result, _ = svc.list_users_with_projects()

        assert not hasattr(result[0].roles[0], "permissions")

    def test_user_with_no_projects_gets_empty_list(self) -> None:
        user = _make_user()
        uow = _make_uow()
        uow.users.get_paginated.return_value = ([user], 1)

        svc = UserService(uow)
        result, _ = svc.list_users_with_projects()

        assert result[0].projects == []


# ── get_by_id_scoped ────────────────────────────────────────────────────────


class TestGetByIdScoped:
    def test_returns_user_when_no_tenant_scoping(self) -> None:
        user = _make_user()
        uow = _make_uow()
        uow.users.get.return_value = user

        svc = UserService(uow)
        result = svc.get_by_id_scoped(user.id, requester_tenant_id=None)

        assert result is user

    def test_returns_user_when_same_tenant(self) -> None:
        tenant_id = uuid.uuid4()
        user = _make_user(tenant_id=tenant_id)
        uow = _make_uow()
        uow.users.get.return_value = user

        svc = UserService(uow)
        result = svc.get_by_id_scoped(user.id, requester_tenant_id=tenant_id)

        assert result is user

    def test_raises_not_found_when_different_tenant(self) -> None:
        user = _make_user(tenant_id=uuid.uuid4())
        uow = _make_uow()
        uow.users.get.return_value = user

        svc = UserService(uow)
        with pytest.raises(NotFoundError):
            svc.get_by_id_scoped(user.id, requester_tenant_id=uuid.uuid4())


# ── update_profile ─────────────────────────────────────────────────────────


class TestUpdateProfile:
    def test_updates_name_and_commits(self) -> None:
        user = _make_user(name="Old")
        uow = _make_uow()
        uow.users.get.return_value = user

        svc = UserService(uow)
        result = svc.update_profile(user.id, name="New Name")

        assert result.name == "New Name"
        uow.add.assert_called_once_with(user)
        uow.commit.assert_called_once()
        uow.refresh.assert_called_once_with(user)

    def test_raises_not_found_for_unknown_id(self) -> None:
        uow = _make_uow()
        uow.users.get.return_value = None

        svc = UserService(uow)
        with pytest.raises(NotFoundError):
            svc.update_profile(uuid.uuid4(), name="X")


# ── deactivate ─────────────────────────────────────────────────────────────


class TestDeactivate:
    def test_sets_is_active_false_and_commits(self) -> None:
        user = _make_user(is_active=True)
        uow = _make_uow()
        uow.users.get.return_value = user

        svc = UserService(uow)
        result = svc.deactivate(user.id)

        assert result.is_active is False
        uow.commit.assert_called_once()

    def test_raises_not_found_for_unknown_id(self) -> None:
        uow = _make_uow()
        uow.users.get.return_value = None

        svc = UserService(uow)
        with pytest.raises(NotFoundError):
            svc.deactivate(uuid.uuid4())


# ── set_active_status ────────────────────────────────────────────────────────


class TestSetActiveStatus:
    @pytest.fixture(autouse=True)
    def _mock_publish_notification(self):
        """Prevent set_active_status's bell-notification call from opening a
        real UnitOfWork/Redis publish — patched at ``app.services.user_service``
        since it's imported at module level there.
        """
        with patch("app.services.user_service.publish_notification") as mock:
            yield mock

    def test_deactivates_active_user(self, _mock_publish_notification) -> None:
        user = _make_user(is_active=True, email="alice@example.com", name="Alice")
        uow = _make_uow()
        uow.users.get.return_value = user

        svc = UserService(uow)
        with patch("app.services.user_service.EmailService.send_account_status_changed") as mock_email:
            result = svc.set_active_status(user.id, False, requester_id=uuid.uuid4())

        assert result.is_active is False
        uow.commit.assert_called_once()
        mock_email.assert_called_once_with(
            recipient="alice@example.com", name="Alice", is_active=False
        )
        _mock_publish_notification.assert_called_once_with(
            user_id=user.id,
            title="Account Deactivated",
            message="Your account has been deactivated by an administrator.",
            notification_type=NotificationType.WARNING,
            data={"is_active": False},
        )

    def test_reactivates_inactive_user(self, _mock_publish_notification) -> None:
        user = _make_user(is_active=False, email="alice@example.com", name="Alice")
        uow = _make_uow()
        uow.users.get.return_value = user

        svc = UserService(uow)
        with patch("app.services.user_service.EmailService.send_account_status_changed") as mock_email:
            result = svc.set_active_status(user.id, True, requester_id=uuid.uuid4())

        assert result.is_active is True
        uow.commit.assert_called_once()
        mock_email.assert_called_once_with(
            recipient="alice@example.com", name="Alice", is_active=True
        )
        _mock_publish_notification.assert_called_once_with(
            user_id=user.id,
            title="Account Reactivated",
            message="Your account has been reactivated. You can sign in and resume your work.",
            notification_type=NotificationType.SUCCESS,
            data={"is_active": True},
        )

    def test_raises_forbidden_when_targeting_self(self) -> None:
        admin = _make_user(is_active=True)
        uow = _make_uow()
        uow.users.get.return_value = admin

        svc = UserService(uow)
        with pytest.raises(ForbiddenError):
            svc.set_active_status(admin.id, False, requester_id=admin.id)

        uow.commit.assert_not_called()

    def test_raises_forbidden_when_target_is_removed(self) -> None:
        user = _make_user(is_active=False)
        user.deleted_at = datetime.now(tz=UTC)
        uow = _make_uow()
        uow.users.get.return_value = user

        svc = UserService(uow)
        with pytest.raises(ForbiddenError):
            svc.set_active_status(user.id, True, requester_id=uuid.uuid4())

        uow.commit.assert_not_called()

    def test_raises_not_found_for_unknown_id(self) -> None:
        uow = _make_uow()
        uow.users.get.return_value = None

        svc = UserService(uow)
        with pytest.raises(NotFoundError):
            svc.set_active_status(uuid.uuid4(), True, requester_id=uuid.uuid4())

    def test_raises_not_found_when_target_user_in_other_tenant(self) -> None:
        user = _make_user(tenant_id=uuid.uuid4())
        uow = _make_uow()
        uow.users.get.return_value = user

        svc = UserService(uow)
        with pytest.raises(NotFoundError):
            svc.set_active_status(
                user.id, False, requester_id=uuid.uuid4(), requester_tenant_id=uuid.uuid4()
            )


# ── remove_user / assert_can_remove ─────────────────────────────────────────


class TestAssertCanRemove:
    def test_returns_target_user(self) -> None:
        user = _make_user()
        uow = _make_uow()
        uow.users.get.return_value = user

        svc = UserService(uow)
        result = svc.assert_can_remove(user.id, requester_id=uuid.uuid4())

        assert result is user

    def test_raises_forbidden_when_removing_self(self) -> None:
        admin = _make_user()
        uow = _make_uow()
        uow.users.get.return_value = admin

        svc = UserService(uow)
        with pytest.raises(ForbiddenError):
            svc.assert_can_remove(admin.id, requester_id=admin.id)

    def test_raises_not_found_when_target_user_in_other_tenant(self) -> None:
        user = _make_user(tenant_id=uuid.uuid4())
        uow = _make_uow()
        uow.users.get.return_value = user

        svc = UserService(uow)
        with pytest.raises(NotFoundError):
            svc.assert_can_remove(
                user.id, requester_id=uuid.uuid4(), requester_tenant_id=uuid.uuid4()
            )


class TestRemoveUser:
    def test_soft_deletes_and_commits(self) -> None:
        user = _make_user(is_active=True)
        uow = _make_uow()
        uow.users.get.return_value = user

        svc = UserService(uow)
        result = svc.remove_user(user.id, requester_id=uuid.uuid4())

        assert result.is_active is False
        assert result.deleted_at is not None
        uow.commit.assert_called_once()

    def test_raises_forbidden_when_removing_self(self) -> None:
        admin = _make_user()
        uow = _make_uow()
        uow.users.get.return_value = admin

        svc = UserService(uow)
        with pytest.raises(ForbiddenError):
            svc.remove_user(admin.id, requester_id=admin.id)

        uow.commit.assert_not_called()

    def test_raises_not_found_for_unknown_id(self) -> None:
        uow = _make_uow()
        uow.users.get.return_value = None

        svc = UserService(uow)
        with pytest.raises(NotFoundError):
            svc.remove_user(uuid.uuid4(), requester_id=uuid.uuid4())

    def test_raises_not_found_when_target_user_in_other_tenant(self) -> None:
        user = _make_user(tenant_id=uuid.uuid4())
        uow = _make_uow()
        uow.users.get.return_value = user

        svc = UserService(uow)
        with pytest.raises(NotFoundError):
            svc.remove_user(user.id, requester_id=uuid.uuid4(), requester_tenant_id=uuid.uuid4())


# ── assign_role ────────────────────────────────────────────────────────────


class TestAssignRole:
    def test_assigns_new_role_to_user(self) -> None:
        user = _make_user(roles=[])
        role = _make_role("editor")
        uow = _make_uow()
        uow.users.get.return_value = user
        uow.roles.get_by_name.return_value = role
        uow.session.query.return_value.filter_by.return_value.first.return_value = None

        svc = UserService(uow)
        result = svc.assign_role(user.id, "editor")

        assert role in result.roles
        uow.flush.assert_called()
        uow.commit.assert_called()

    def test_idempotent_when_role_already_assigned(self) -> None:
        role = _make_role("editor")
        user = _make_user(roles=[role])
        uow = _make_uow()
        uow.users.get.return_value = user
        uow.roles.get_by_name.return_value = role

        svc = UserService(uow)
        result = svc.assign_role(user.id, "editor")

        # No flush/commit when role already present
        uow.flush.assert_not_called()
        uow.commit.assert_not_called()
        # Role still present exactly once
        assert sum(1 for r in result.roles if r.name == "editor") == 1

    def test_raises_not_found_when_role_missing(self) -> None:
        user = _make_user()
        uow = _make_uow()
        uow.users.get.return_value = user
        uow.roles.get_by_name.return_value = None

        svc = UserService(uow)
        with pytest.raises(NotFoundError, match="editor"):
            svc.assign_role(user.id, "editor")

    def test_raises_not_found_when_user_missing(self) -> None:
        uow = _make_uow()
        uow.users.get.return_value = None

        svc = UserService(uow)
        with pytest.raises(NotFoundError):
            svc.assign_role(uuid.uuid4(), "editor")

    def test_assigns_audit_column_when_assigned_by_provided(self) -> None:
        user = _make_user(roles=[])
        role = _make_role("admin")
        assigner_id = uuid.uuid4()
        junction = MagicMock()
        uow = _make_uow()
        uow.users.get.return_value = user
        uow.roles.get_by_name.return_value = role
        uow.session.query.return_value.filter_by.return_value.first.return_value = junction

        svc = UserService(uow)
        svc.assign_role(user.id, "admin", assigned_by_id=assigner_id)

        assert junction.assigned_by == assigner_id

    def test_non_super_admin_cannot_grant_super_admin_role(self) -> None:
        user = _make_user(roles=[])
        uow = _make_uow()
        uow.users.get.return_value = user

        svc = UserService(uow)
        with pytest.raises(ForbiddenError):
            svc.assign_role(user.id, "super_admin", requester_roles=["admin"])

        uow.commit.assert_not_called()

    def test_super_admin_can_grant_super_admin_role(self) -> None:
        user = _make_user(roles=[])
        role = _make_role("super_admin")
        uow = _make_uow()
        uow.users.get.return_value = user
        uow.roles.get_by_name.return_value = role
        uow.session.query.return_value.filter_by.return_value.first.return_value = None

        svc = UserService(uow)
        result = svc.assign_role(user.id, "super_admin", requester_roles=["super_admin"])

        assert role in result.roles

    def test_raises_not_found_when_target_user_in_other_tenant(self) -> None:
        user = _make_user(roles=[], tenant_id=uuid.uuid4())
        uow = _make_uow()
        uow.users.get.return_value = user

        svc = UserService(uow)
        with pytest.raises(NotFoundError):
            svc.assign_role(
                user.id,
                "editor",
                requester_roles=["admin"],
                requester_tenant_id=uuid.uuid4(),
            )


# ── revoke_role ────────────────────────────────────────────────────────────


class TestRevokeRole:
    def test_removes_role_from_user(self) -> None:
        role = _make_role("editor")
        user = _make_user(roles=[role])
        uow = _make_uow()
        uow.users.get.return_value = user
        uow.roles.get_by_name.return_value = role

        svc = UserService(uow)
        result = svc.revoke_role(user.id, "editor")

        assert role not in result.roles
        uow.commit.assert_called_once()

    def test_idempotent_when_role_not_present(self) -> None:
        role = _make_role("editor")
        user = _make_user(roles=[])
        uow = _make_uow()
        uow.users.get.return_value = user
        uow.roles.get_by_name.return_value = role

        svc = UserService(uow)
        result = svc.revoke_role(user.id, "editor")

        assert result.roles == []
        uow.commit.assert_called_once()

    def test_raises_not_found_when_role_missing(self) -> None:
        user = _make_user()
        uow = _make_uow()
        uow.users.get.return_value = user
        uow.roles.get_by_name.return_value = None

        svc = UserService(uow)
        with pytest.raises(NotFoundError, match="viewer"):
            svc.revoke_role(user.id, "viewer")

    def test_raises_not_found_when_user_missing(self) -> None:
        uow = _make_uow()
        uow.users.get.return_value = None

        svc = UserService(uow)
        with pytest.raises(NotFoundError):
            svc.revoke_role(uuid.uuid4(), "editor")

    def test_raises_not_found_when_target_user_in_other_tenant(self) -> None:
        role = _make_role("editor")
        user = _make_user(roles=[role], tenant_id=uuid.uuid4())
        uow = _make_uow()
        uow.users.get.return_value = user

        svc = UserService(uow)
        with pytest.raises(NotFoundError):
            svc.revoke_role(user.id, "editor", requester_tenant_id=uuid.uuid4())


# ── update_roles ───────────────────────────────────────────────────────────


class TestUpdateRoles:
    def test_replaces_full_role_set(self) -> None:
        editor = _make_role("editor")
        reviewer = _make_role("reviewer")
        user = _make_user(roles=[editor])
        uow = _make_uow()
        uow.users.get.return_value = user
        uow.roles.get_by_name.side_effect = lambda name: {"reviewer": reviewer}[name]
        uow.session.query.return_value.filter.return_value.all.return_value = []

        svc = UserService(uow)
        result = svc.update_roles(user.id, ["reviewer"])

        assert result.roles == [reviewer]
        uow.commit.assert_called_once()

    def test_keeps_already_held_roles_and_adds_new_ones(self) -> None:
        editor = _make_role("editor")
        reviewer = _make_role("reviewer")
        user = _make_user(roles=[editor])
        uow = _make_uow()
        uow.users.get.return_value = user
        roles = {"editor": editor, "reviewer": reviewer}
        uow.roles.get_by_name.side_effect = lambda name: roles[name]
        uow.session.query.return_value.filter.return_value.all.return_value = []

        svc = UserService(uow)
        result = svc.update_roles(user.id, ["editor", "reviewer"])

        assert result.roles == [editor, reviewer]

    def test_raises_not_found_when_any_role_missing(self) -> None:
        user = _make_user()
        uow = _make_uow()
        uow.users.get.return_value = user
        uow.roles.get_by_name.return_value = None

        svc = UserService(uow)
        with pytest.raises(NotFoundError, match="editor"):
            svc.update_roles(user.id, ["editor"])

    def test_raises_not_found_when_user_missing(self) -> None:
        uow = _make_uow()
        uow.users.get.return_value = None

        svc = UserService(uow)
        with pytest.raises(NotFoundError):
            svc.update_roles(uuid.uuid4(), ["editor"])

    def test_non_super_admin_cannot_grant_super_admin_via_update(self) -> None:
        user = _make_user(roles=[])
        role = _make_role("super_admin")
        uow = _make_uow()
        uow.users.get.return_value = user
        uow.roles.get_by_name.return_value = role

        svc = UserService(uow)
        with pytest.raises(ForbiddenError):
            svc.update_roles(user.id, ["super_admin"], requester_roles=["admin"])

        uow.commit.assert_not_called()

    def test_non_super_admin_can_resend_already_held_admin_role(self) -> None:
        """Re-listing a role the target already holds is not an escalation,
        even if the requester couldn't have granted it fresh."""
        admin_role = _make_role("admin")
        user = _make_user(roles=[admin_role])
        uow = _make_uow()
        uow.users.get.return_value = user
        uow.roles.get_by_name.return_value = admin_role

        svc = UserService(uow)
        result = svc.update_roles(user.id, ["admin"], requester_roles=["admin"])

        assert result.roles == [admin_role]

    def test_assigns_audit_column_for_newly_added_roles_only(self) -> None:
        editor = _make_role("editor")
        reviewer = _make_role("reviewer")
        assigner_id = uuid.uuid4()
        junction = MagicMock()
        user = _make_user(roles=[editor])
        uow = _make_uow()
        uow.users.get.return_value = user
        roles = {"editor": editor, "reviewer": reviewer}
        uow.roles.get_by_name.side_effect = lambda name: roles[name]
        uow.session.query.return_value.filter.return_value.all.return_value = [junction]

        svc = UserService(uow)
        svc.update_roles(user.id, ["editor", "reviewer"], assigned_by_id=assigner_id)

        assert junction.assigned_by == assigner_id

    def test_raises_not_found_when_target_user_in_other_tenant(self) -> None:
        role = _make_role("editor")
        user = _make_user(roles=[role], tenant_id=uuid.uuid4())
        uow = _make_uow()
        uow.users.get.return_value = user

        svc = UserService(uow)
        with pytest.raises(NotFoundError):
            svc.update_roles(
                user.id,
                ["editor"],
                requester_roles=["admin"],
                requester_tenant_id=uuid.uuid4(),
            )
