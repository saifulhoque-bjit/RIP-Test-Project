"""Unit tests for auth utility helpers in app.utils.auth."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from jose import JWTError
import pytest

from app.schemas.auth_schema import TokenData
from app.utils.auth import attach_user_roles


def _make_tokens() -> TokenData:
    return TokenData(
        access_token="access-token",
        id_token="id-token",
        refresh_token="refresh-token",
        expires_in=3600,
    )


class TestAttachUserRoles:
    def test_login_enriches_tokens_from_existing_user(self) -> None:
        tokens = _make_tokens()

        perm_read = MagicMock()
        perm_read.name = "tests:read"
        role_viewer = MagicMock()
        role_viewer.name = "viewer"
        role_viewer.permissions = [perm_read]

        db_user = MagicMock()
        db_user.username = "alice@example.com"
        db_user.email = "alice@example.com"
        db_user.roles = [role_viewer]

        uow = MagicMock()
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        mock_service = MagicMock()
        mock_service.get_authenticated_user.return_value = db_user

        claims = {
            "sub": "sub-123",
            "email": "alice@example.com",
            "name": "Alice",
            "email_verified": True,
        }

        with (
            patch("app.utils.auth.decode_cognito_token", return_value=claims),
            patch("app.utils.auth.UnitOfWork", return_value=cm),
            patch("app.utils.auth.UserService", return_value=mock_service),
        ):
            attach_user_roles(tokens, "fallback@example.com")

        mock_service.get_authenticated_user.assert_called_once_with(
            sub="sub-123",
            email="alice@example.com",
            name="Alice",
            is_verified=True,
            username=None,
        )
        assert tokens.roles == ["viewer"]
        assert tokens.permissions == ["tests:read"]
        assert tokens.cognito_username == "alice@example.com"
        assert tokens.email == "alice@example.com"

    def test_fallback_to_email_lookup_when_id_token_cannot_be_decoded(self) -> None:
        """sub="" (decode failed) still resolves via get_authenticated_user's
        own email fallback — see UserService.get_authenticated_user."""
        tokens = _make_tokens()

        perm_write = MagicMock()
        perm_write.name = "tests:write"
        role_editor = MagicMock()
        role_editor.name = "editor"
        role_editor.permissions = [perm_write]

        db_user = MagicMock()
        db_user.username = "fallback-example-com"
        db_user.email = "fallback@example.com"
        db_user.roles = [role_editor]

        uow = MagicMock()
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        mock_service = MagicMock()
        mock_service.get_authenticated_user.return_value = db_user

        with (
            patch(
                "app.utils.auth.decode_cognito_token",
                side_effect=JWTError("bad token"),
            ),
            patch("app.utils.auth.UnitOfWork", return_value=cm),
            patch("app.utils.auth.UserService", return_value=mock_service),
        ):
            attach_user_roles(tokens, "fallback@example.com")

        mock_service.get_authenticated_user.assert_called_once_with(
            sub="",
            email="fallback@example.com",
            name=None,
            is_verified=True,
            username=None,
        )
        assert tokens.roles == ["editor"]
        assert tokens.permissions == ["tests:write"]
        assert tokens.cognito_username == "fallback-example-com"
        assert tokens.email == "fallback@example.com"

    def test_raises_forbidden_when_no_local_user_exists(self) -> None:
        """Cognito auth alone must not grant access without an accepted
        invitation (or, for Super Admin, startup seeding)."""
        from app.core.exceptions import ForbiddenError

        tokens = _make_tokens()
        uow = MagicMock()
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        mock_service = MagicMock()
        mock_service.get_authenticated_user.side_effect = ForbiddenError("not provisioned")

        claims = {
            "sub": "sub-999",
            "email": "uninvited@example.com",
            "name": "Uninvited",
            "email_verified": True,
        }

        with (
            patch("app.utils.auth.decode_cognito_token", return_value=claims),
            patch("app.utils.auth.UnitOfWork", return_value=cm),
            patch("app.utils.auth.UserService", return_value=mock_service),
        ):
            with pytest.raises(ForbiddenError):
                attach_user_roles(tokens, "uninvited@example.com")
