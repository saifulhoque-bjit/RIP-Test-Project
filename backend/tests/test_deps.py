"""Unit tests for FastAPI dependency functions in app.deps.

Strategy:
- ``get_current_user`` is tested by calling it directly with mocked
  credentials / cookies and a patched ``decode_cognito_token``.
- ``get_current_db_user`` delegates to UserService; that is mocked.
- ``require_roles`` is tested with a mocked user carrying various role sets.
- No real HTTP server is needed — we call the dependency functions directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from fastapi.security import HTTPAuthorizationCredentials
from jose import ExpiredSignatureError, JWTError
import pytest

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.models.postgres.role_model import Role
from app.models.postgres.user_model import User

# ── Helpers ────────────────────────────────────────────────────────────────


def _make_user(roles: list[str] | None = None) -> User:
    """Return an instrumented User with optional role names."""
    u = User()
    u.id = uuid.uuid4()
    u.cognito_sub = "sub-123"
    u.email = "alice@example.com"
    u.name = "Alice"
    u.is_active = True
    u.is_verified = True
    u.created_at = datetime.now(tz=UTC)
    u.updated_at = datetime.now(tz=UTC)

    u.roles = []
    for role_name in roles or []:
        r = Role()
        r.id = uuid.uuid4()
        r.name = role_name
        u.roles.append(r)
    return u


def _bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


_VALID_CLAIMS = {
    "sub": "sub-123",
    "email": "alice@example.com",
    "name": "Alice",
    "email_verified": True,
    "token_use": "access",
}


# ── get_current_user ───────────────────────────────────────────────────────


class TestGetCurrentUser:
    def test_accepts_bearer_token(self) -> None:
        from app.deps import get_current_user

        with patch("app.deps.decode_cognito_token", return_value=_VALID_CLAIMS):
            result = get_current_user(
                credentials=_bearer("valid-token"),
                cookie_access_token=None,
            )
        assert result["sub"] == "sub-123"

    def test_accepts_cookie_token(self) -> None:
        from app.deps import get_current_user

        with patch("app.deps.decode_cognito_token", return_value=_VALID_CLAIMS):
            result = get_current_user(
                credentials=None,
                cookie_access_token="cookie-token",
            )
        assert result["email"] == "alice@example.com"

    def test_cookie_takes_priority_over_bearer(self) -> None:
        from app.deps import get_current_user

        with patch("app.deps.decode_cognito_token", return_value=_VALID_CLAIMS) as mock_decode:
            get_current_user(
                credentials=_bearer("bearer-token"),
                cookie_access_token="cookie-token",
            )
        # Should decode the cookie token (first non-empty value wins)
        mock_decode.assert_called_once_with("cookie-token")

    def test_missing_token_raises_401(self) -> None:
        from app.deps import get_current_user

        with pytest.raises(UnauthorizedError) as exc:
            get_current_user(credentials=None, cookie_access_token=None)
        assert exc.value.status_code == 401

    def test_expired_token_raises_401(self) -> None:
        from app.deps import get_current_user

        with patch(
            "app.deps.decode_cognito_token",
            side_effect=ExpiredSignatureError("expired"),
        ):
            with pytest.raises(UnauthorizedError) as exc:
                get_current_user(
                    credentials=_bearer("expired-token"),
                    cookie_access_token=None,
                )
        assert exc.value.status_code == 401
        assert "expired" in exc.value.message.lower()

    def test_invalid_token_raises_401(self) -> None:
        from app.deps import get_current_user

        with patch(
            "app.deps.decode_cognito_token",
            side_effect=JWTError("bad token"),
        ):
            with pytest.raises(UnauthorizedError) as exc:
                get_current_user(
                    credentials=_bearer("garbage"),
                    cookie_access_token=None,
                )
        assert exc.value.status_code == 401


# ── get_current_db_user ────────────────────────────────────────────────────


class TestGetCurrentDbUser:
    def test_returns_user_on_valid_claims(self) -> None:
        from app.deps import get_current_db_user

        user = _make_user(["viewer"])
        mock_svc = MagicMock()
        mock_svc.get_authenticated_user.return_value = user

        with patch("app.deps.UserService", return_value=mock_svc):
            result = get_current_db_user(claims=_VALID_CLAIMS)

        assert result is user

    def test_deactivated_user_raises_403(self) -> None:
        from app.deps import get_current_db_user

        mock_svc = MagicMock()
        mock_svc.get_authenticated_user.side_effect = ForbiddenError("deactivated")

        with patch("app.deps.UserService", return_value=mock_svc):
            with pytest.raises(ForbiddenError) as exc:
                get_current_db_user(claims=_VALID_CLAIMS)

        assert exc.value.status_code == 403

    def test_unexpected_exception_raises_500(self) -> None:
        from app.deps import get_current_db_user

        mock_svc = MagicMock()
        mock_svc.get_authenticated_user.side_effect = RuntimeError("DB down")

        with patch("app.deps.UserService", return_value=mock_svc):
            # Unexpected exceptions bubble up to the centralized handler — not caught in deps
            with pytest.raises(RuntimeError, match="DB down"):
                get_current_db_user(claims=_VALID_CLAIMS)


# ── require_roles ──────────────────────────────────────────────────────────


class TestRequireRoles:
    def test_allows_user_with_matching_role(self) -> None:
        from app.deps import require_roles

        user = _make_user(["admin"])
        dep = require_roles("admin")
        result = dep(user=user)
        assert result is user

    def test_allows_user_with_one_of_multiple_roles(self) -> None:
        from app.deps import require_roles

        user = _make_user(["editor"])
        dep = require_roles("admin", "editor")
        result = dep(user=user)
        assert result is user

    def test_raises_403_when_user_lacks_required_role(self) -> None:
        from app.deps import require_roles

        user = _make_user(["viewer"])
        dep = require_roles("admin")
        with pytest.raises(ForbiddenError) as exc:
            dep(user=user)
        assert exc.value.status_code == 403

    def test_raises_403_when_user_has_no_roles(self) -> None:
        from app.deps import require_roles

        user = _make_user([])
        dep = require_roles("admin")
        with pytest.raises(ForbiddenError) as exc:
            dep(user=user)
        assert exc.value.status_code == 403

    def test_error_message_contains_required_roles(self) -> None:
        from app.deps import require_roles

        user = _make_user(["viewer"])
        dep = require_roles("admin", "superuser")
        with pytest.raises(ForbiddenError) as exc:
            dep(user=user)
        assert "admin" in exc.value.message
        assert "superuser" in exc.value.message


class TestRequireExactRoles:
    def test_super_admin_does_not_bypass(self) -> None:
        from app.deps import require_exact_roles

        user = _make_user(["super_admin"])
        dep = require_exact_roles("admin")
        with pytest.raises(ForbiddenError):
            dep(user=user)

    def test_allows_when_role_matches_exactly(self) -> None:
        from app.deps import require_exact_roles

        user = _make_user(["admin"])
        dep = require_exact_roles("admin")
        assert dep(user=user) is user

    def test_raises_when_no_matching_role(self) -> None:
        from app.deps import require_exact_roles

        user = _make_user(["viewer"])
        dep = require_exact_roles("admin")
        with pytest.raises(ForbiddenError):
            dep(user=user)


class TestRequirePermissions:
    def _user_with_permissions(self, permission_names: list[str]) -> User:
        from app.models.postgres.permission_model import Permission

        user = _make_user(["member"])
        role = user.roles[0]
        role.permissions = []
        for name in permission_names:
            perm = Permission()
            perm.id = uuid.uuid4()
            perm.name = name
            role.permissions.append(perm)
        return user

    def test_allows_when_all_permissions_present(self) -> None:
        from app.deps import require_permissions

        user = self._user_with_permissions(["project:view", "project:update"])
        dep = require_permissions("project:view", "project:update")
        assert dep(user=user) is user

    def test_raises_when_permission_missing(self) -> None:
        from app.deps import require_permissions

        user = self._user_with_permissions(["project:view"])
        dep = require_permissions("project:view", "project:update")
        with pytest.raises(ForbiddenError) as exc:
            dep(user=user)
        assert "project:update" in exc.value.message


class TestRequireProjectAccess:
    def test_raises_not_found_when_project_missing(self) -> None:
        from app.core.exceptions import NotFoundError
        from app.deps import require_project_access

        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = None
        dep = require_project_access("read")

        with pytest.raises(NotFoundError):
            dep(project_id=uuid.uuid4(), user=_make_user(["member"]), uow=uow)

    def test_delegates_to_assert_project_access_when_found(self) -> None:
        from app.deps import require_project_access

        uow = MagicMock()
        project = MagicMock()
        uow.projects.get_by_uuid.return_value = project
        user = _make_user(["member"])
        dep = require_project_access("read")

        with patch("app.deps.ProjectService.assert_project_access") as mock_assert:
            result = dep(project_id=uuid.uuid4(), user=user, uow=uow)

        assert result is project
        mock_assert.assert_called_once()


class TestGetUow:
    def test_yields_uow_instance(self) -> None:
        from app.deps import get_uow

        cm = MagicMock()
        with patch("app.deps.UnitOfWork", return_value=cm):
            gen = get_uow()
            yielded = next(gen)
            assert yielded is cm.__enter__.return_value
            with pytest.raises(StopIteration):
                next(gen)


class TestGetAsyncUow:
    async def test_yields_async_uow_instance(self) -> None:
        from app.deps import get_async_uow

        cm = MagicMock()
        cm.__aenter__ = AsyncMock()
        cm.__aexit__ = AsyncMock(return_value=None)

        with patch("app.deps.AsyncUnitOfWork", return_value=cm):
            gen = get_async_uow()
            yielded = await gen.__anext__()
            assert yielded is cm.__aenter__.return_value
            with pytest.raises(StopAsyncIteration):
                await gen.__anext__()


class TestGetNeo4jProjectRepo:
    def test_builds_repo_with_driver(self) -> None:
        from app.deps import get_neo4j_project_repo

        driver = MagicMock()
        with patch("app.deps.get_neo4j_driver", return_value=driver):
            repo = get_neo4j_project_repo()

        assert repo._driver is driver


class TestGetModuleFeatureRepo:
    def test_builds_repo_with_driver(self) -> None:
        from app.deps import get_module_feature_repo

        driver = MagicMock()
        with patch("app.deps.get_neo4j_driver", return_value=driver):
            repo = get_module_feature_repo()

        assert repo._driver is driver


class TestGetUserStoryRepo:
    def test_builds_repo_with_driver(self) -> None:
        from app.deps import get_user_story_repo

        driver = MagicMock()
        with patch("app.deps.get_neo4j_driver", return_value=driver):
            repo = get_user_story_repo()

        assert repo._driver is driver


class TestGetProjectService:
    def test_builds_service_with_injected_repo(self) -> None:
        from app.deps import get_project_service

        repo = MagicMock()
        module_feature_repo = MagicMock()
        user_story_repo = MagicMock()
        with patch("app.services.setting_service.get_neo4j_driver", return_value=MagicMock()):
            service = get_project_service(repo, module_feature_repo, user_story_repo)

        assert service._neo4j is repo
        assert service._module_feature_repo is module_feature_repo
        assert service._user_story_repo is user_story_repo


class TestGetProjectTaskService:
    def test_returns_new_instance(self) -> None:
        from app.deps import get_project_task_service

        service = get_project_task_service()

        assert service is not None


class TestGetJiraIntegrationService:
    def test_returns_new_instance(self) -> None:
        from app.deps import get_jira_integration_service

        service = get_jira_integration_service()

        assert service is not None


class TestGetJiraSyncService:
    def test_builds_service_with_neo4j_repos(self) -> None:
        from app.deps import get_jira_sync_service

        with patch("app.deps.get_neo4j_driver", return_value=MagicMock()):
            service = get_jira_sync_service()

        assert service is not None


class TestGetExportService:
    def test_builds_service_with_neo4j_repos(self) -> None:
        from app.deps import get_export_service

        with patch("app.deps.get_neo4j_driver", return_value=MagicMock()):
            service = get_export_service()

        assert service is not None


class TestGetTapSyncService:
    def test_builds_service_with_neo4j_repos(self) -> None:
        from app.deps import get_tap_sync_service

        with patch("app.deps.get_neo4j_driver", return_value=MagicMock()):
            service = get_tap_sync_service()

        assert service is not None
