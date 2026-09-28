"""Unit tests for the Super Admin bootstrap seed (app.db.seed_super_admin).

Strategy: patch UnitOfWork / CognitoAuthService / UserService at their
import site in ``app.db.seed_super_admin`` (per repo convention — lazy
imports are patched where used, not where defined). No real DB or AWS calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from app.db.seed_super_admin import seed_super_admin


def _make_uow_cm() -> MagicMock:
    """Build a UnitOfWork mock usable as ``with UnitOfWork() as uow:``."""
    uow = MagicMock()
    uow.__enter__ = MagicMock(return_value=uow)
    uow.__exit__ = MagicMock(return_value=False)
    return uow


class TestSeedSuperAdminDisabled:
    @pytest.mark.asyncio
    async def test_noop_when_seeding_disabled(self) -> None:
        with (
            patch("app.db.seed_super_admin.settings") as mock_settings,
            patch("app.db.seed_super_admin.UnitOfWork") as mock_uow_cls,
        ):
            mock_settings.SEED_SUPER_ADMIN_ENABLED = False
            await seed_super_admin()

        mock_uow_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_when_credentials_unset(self) -> None:
        with (
            patch("app.db.seed_super_admin.settings") as mock_settings,
            patch("app.db.seed_super_admin.UnitOfWork") as mock_uow_cls,
        ):
            mock_settings.SEED_SUPER_ADMIN_ENABLED = True
            mock_settings.SUPER_ADMIN_EMAIL = ""
            mock_settings.SUPER_ADMIN_PASSWORD = ""
            await seed_super_admin()

        mock_uow_cls.assert_not_called()


class TestSeedSuperAdminFirstRun:
    @pytest.mark.asyncio
    async def test_creates_cognito_user_and_assigns_role_without_tenant(self) -> None:
        uow = _make_uow_cm()

        fake_user = MagicMock()
        fake_user.id = uuid.uuid4()
        fake_user.tenant_id = None

        cognito_instance = MagicMock()
        cognito_instance.admin_get_user_sub = AsyncMock(return_value=None)
        cognito_instance.admin_create_user = AsyncMock(return_value="sub-new-1")

        user_service_instance = MagicMock()
        user_service_instance.get_or_create_by_cognito_sub = MagicMock(return_value=fake_user)
        user_service_instance.assign_role = MagicMock(return_value=fake_user)
        user_service_instance.revoke_role = MagicMock(return_value=fake_user)

        with (
            patch("app.db.seed_super_admin.settings") as mock_settings,
            patch("app.db.seed_super_admin.UnitOfWork", return_value=uow),
            patch("app.db.seed_super_admin.CognitoAuthService", return_value=cognito_instance),
            patch("app.db.seed_super_admin.UserService", return_value=user_service_instance),
        ):
            mock_settings.SEED_SUPER_ADMIN_ENABLED = True
            mock_settings.SUPER_ADMIN_EMAIL = "super@example.com"
            mock_settings.SUPER_ADMIN_PASSWORD = "Sup3rStrong!Pass"
            mock_settings.SUPER_ADMIN_NAME = "Super Admin"

            await seed_super_admin()

        cognito_instance.admin_create_user.assert_awaited_once_with(
            email="super@example.com",
            password="Sup3rStrong!Pass",
            name="Super Admin",
        )
        user_service_instance.get_or_create_by_cognito_sub.assert_called_once()
        assert (
            user_service_instance.get_or_create_by_cognito_sub.call_args.kwargs[
                "assign_default_role"
            ]
            is False
        )
        user_service_instance.assign_role.assert_called_once_with(fake_user.id, "super_admin")
        user_service_instance.revoke_role.assert_called_once_with(fake_user.id, "member")
        # Super Admin is platform-wide — no tenant is ever assigned.
        assert fake_user.tenant_id is None


class TestSeedSuperAdminIdempotent:
    @pytest.mark.asyncio
    async def test_second_run_skips_cognito_creation_and_leaves_tenant_unset(self) -> None:
        uow = _make_uow_cm()

        fake_user = MagicMock()
        fake_user.id = uuid.uuid4()
        fake_user.tenant_id = None

        cognito_instance = MagicMock()
        cognito_instance.admin_get_user_sub = AsyncMock(return_value="sub-existing-1")
        cognito_instance.admin_create_user = AsyncMock()

        user_service_instance = MagicMock()
        user_service_instance.get_or_create_by_cognito_sub = MagicMock(return_value=fake_user)
        user_service_instance.assign_role = MagicMock(return_value=fake_user)
        user_service_instance.revoke_role = MagicMock(return_value=fake_user)

        with (
            patch("app.db.seed_super_admin.settings") as mock_settings,
            patch("app.db.seed_super_admin.UnitOfWork", return_value=uow),
            patch("app.db.seed_super_admin.CognitoAuthService", return_value=cognito_instance),
            patch("app.db.seed_super_admin.UserService", return_value=user_service_instance),
        ):
            mock_settings.SEED_SUPER_ADMIN_ENABLED = True
            mock_settings.SUPER_ADMIN_EMAIL = "super@example.com"
            mock_settings.SUPER_ADMIN_PASSWORD = "Sup3rStrong!Pass"
            mock_settings.SUPER_ADMIN_NAME = "Super Admin"

            await seed_super_admin()

        cognito_instance.admin_create_user.assert_not_called()
        assert (
            user_service_instance.get_or_create_by_cognito_sub.call_args.kwargs[
                "assign_default_role"
            ]
            is False
        )
        user_service_instance.assign_role.assert_called_once_with(fake_user.id, "super_admin")
        user_service_instance.revoke_role.assert_called_once_with(fake_user.id, "member")
        assert fake_user.tenant_id is None
