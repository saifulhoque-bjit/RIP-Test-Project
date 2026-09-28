"""Unit tests for TenantLLMProviderService.

Strategy:
- All repository interactions are replaced by MagicMock so no DB is needed.
- Encryption and the outbound provider probe are patched so no real Fernet
  key or network call is required.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from app.clients.llm_factory import LLMBalance
from app.core.constants import ROLE_SUPER_ADMIN
from app.core.enums.llm_provider import LLMProvider
from app.core.exceptions import (
    AIServiceError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.db.unit_of_work import UnitOfWork
from app.models.postgres.tenant_llm_provider_model import TenantLLMProvider
from app.models.postgres.tenant_model import Tenant
from app.services.tenant_llm_provider_service import TenantLLMProviderService
from tests.conftest import make_user


def _make_uow() -> MagicMock:
    uow = MagicMock(spec=UnitOfWork)
    uow.tenants = MagicMock()
    uow.source_ingestions = MagicMock()
    return uow


def _make_tenant(*, tenant_id: uuid.UUID | None = None, providers: list | None = None) -> Tenant:
    t = Tenant()
    t.id = tenant_id or uuid.uuid4()
    t.name = "Acme"
    t.llm_providers = providers if providers is not None else []
    return t


def _make_row(**overrides) -> TenantLLMProvider:
    row = TenantLLMProvider(
        id=uuid.uuid4(),
        provider="anthropic",
        is_active=True,
        api_key_encrypted=None,
        is_verified=False,
        last_tested_at=None,
        last_test_error=None,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


class TestListProviders:
    def test_returns_empty_when_tenant_has_configured_nothing(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        requester = make_user(tenant_id=tenant_id)
        uow.tenants.get.return_value = _make_tenant(tenant_id=tenant_id, providers=[])

        result = TenantLLMProviderService(uow).list_providers(tenant_id, requester=requester)

        assert result == []

    def test_returns_only_configured_providers(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        requester = make_user(tenant_id=tenant_id)
        row = _make_row(provider="openai", is_active=True, api_key_encrypted="ciphertext123456")
        uow.tenants.get.return_value = _make_tenant(tenant_id=tenant_id, providers=[row])

        result = TenantLLMProviderService(uow).list_providers(tenant_id, requester=requester)

        assert len(result) == 1
        openai_out = result[0]
        assert openai_out.id == row.id
        assert openai_out.provider == LLMProvider.OPENAI
        assert openai_out.is_active is True
        assert openai_out.has_api_key is True
        assert openai_out.api_key_hint == "cipher...123456"

    def test_returns_two_when_two_configured(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        requester = make_user(tenant_id=tenant_id)
        rows = [_make_row(provider="anthropic"), _make_row(provider="google")]
        uow.tenants.get.return_value = _make_tenant(tenant_id=tenant_id, providers=rows)

        result = TenantLLMProviderService(uow).list_providers(tenant_id, requester=requester)

        assert len(result) == 2
        assert {item.provider for item in result} == {LLMProvider.ANTHROPIC, LLMProvider.GOOGLE}

    def test_forbidden_for_non_super_admin_other_tenant(self) -> None:
        uow = _make_uow()
        requester = make_user(tenant_id=uuid.uuid4())

        with pytest.raises(ForbiddenError):
            TenantLLMProviderService(uow).list_providers(uuid.uuid4(), requester=requester)

        uow.tenants.get.assert_not_called()

    def test_super_admin_may_list_any_tenant(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        role = MagicMock()
        role.name = ROLE_SUPER_ADMIN
        requester = make_user(tenant_id=uuid.uuid4(), roles=[role])
        row = _make_row(provider="deepseek")
        uow.tenants.get.return_value = _make_tenant(tenant_id=tenant_id, providers=[row])

        result = TenantLLMProviderService(uow).list_providers(tenant_id, requester=requester)

        assert len(result) == 1

    def test_not_found_when_tenant_missing(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        requester = make_user(tenant_id=tenant_id)
        uow.tenants.get.return_value = None

        with pytest.raises(NotFoundError):
            TenantLLMProviderService(uow).list_providers(tenant_id, requester=requester)


class TestGetActiveApiKey:
    def test_returns_key_when_active_and_unverified_by_default(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        row = _make_row(
            provider="anthropic", is_active=True, api_key_encrypted="ciphertext123456", is_verified=False
        )
        uow.tenants.get.return_value = _make_tenant(tenant_id=tenant_id, providers=[row])

        with patch(
            "app.services.tenant_llm_provider_service.decrypt_llm_api_key",
            return_value="sk-live-key",
        ):
            result = TenantLLMProviderService(uow).get_active_api_key(tenant_id, "anthropic")

        assert result == "sk-live-key"

    def test_require_verified_returns_none_when_key_not_verified(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        row = _make_row(
            provider="anthropic", is_active=True, api_key_encrypted="ciphertext123456", is_verified=False
        )
        uow.tenants.get.return_value = _make_tenant(tenant_id=tenant_id, providers=[row])

        result = TenantLLMProviderService(uow).get_active_api_key(
            tenant_id, "anthropic", require_verified=True
        )

        assert result is None

    def test_require_verified_returns_key_when_verified(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        row = _make_row(
            provider="anthropic", is_active=True, api_key_encrypted="ciphertext123456", is_verified=True
        )
        uow.tenants.get.return_value = _make_tenant(tenant_id=tenant_id, providers=[row])

        with patch(
            "app.services.tenant_llm_provider_service.decrypt_llm_api_key",
            return_value="sk-live-key",
        ):
            result = TenantLLMProviderService(uow).get_active_api_key(
                tenant_id, "anthropic", require_verified=True
            )

        assert result == "sk-live-key"

    def test_returns_none_when_no_row_for_provider(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        uow.tenants.get.return_value = _make_tenant(tenant_id=tenant_id, providers=[])

        result = TenantLLMProviderService(uow).get_active_api_key(tenant_id, "anthropic")

        assert result is None

    def test_returns_none_when_tenant_missing(self) -> None:
        uow = _make_uow()
        uow.tenants.get.return_value = None

        result = TenantLLMProviderService(uow).get_active_api_key(uuid.uuid4(), "anthropic")

        assert result is None


class TestUpdateProvider:
    def test_creates_row_when_absent_and_encrypts_key(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        requester = make_user(tenant_id=tenant_id)
        tenant = _make_tenant(tenant_id=tenant_id, providers=[])
        uow.tenants.get.return_value = tenant

        def _refresh(obj):  # populate the DB-assigned id the mock flush skips
            obj.id = uuid.uuid4()

        uow.refresh.side_effect = _refresh

        with patch(
            "app.services.tenant_llm_provider_service.encrypt_llm_api_key",
            return_value="encrypted-value",
        ):
            result = TenantLLMProviderService(uow).update_provider(
                tenant_id,
                LLMProvider.ANTHROPIC,
                api_key="sk-real-secret",
                is_active=True,
                requester=requester,
            )

        assert len(tenant.llm_providers) == 1
        assert tenant.llm_providers[0].api_key_encrypted == "encrypted-value"
        assert result.is_active is True
        assert result.has_api_key is True
        uow.commit.assert_called_once()

    def test_setting_new_key_resets_verification(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        requester = make_user(tenant_id=tenant_id)
        row = _make_row(
            is_verified=True,
            last_tested_at=datetime.now(tz=UTC),
            last_test_error=None,
            api_key_encrypted="old-cipher",
        )
        tenant = _make_tenant(tenant_id=tenant_id, providers=[row])
        uow.tenants.get.return_value = tenant

        with patch(
            "app.services.tenant_llm_provider_service.encrypt_llm_api_key",
            return_value="new-cipher",
        ):
            TenantLLMProviderService(uow).update_provider(
                tenant_id,
                LLMProvider.ANTHROPIC,
                api_key="new-secret",
                is_active=None,
                requester=requester,
            )

        assert row.api_key_encrypted == "new-cipher"
        assert row.is_verified is False
        assert row.last_tested_at is None

    def test_is_active_only_leaves_key_untouched(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        requester = make_user(tenant_id=tenant_id)
        row = _make_row(is_active=True, api_key_encrypted="existing-cipher")
        tenant = _make_tenant(tenant_id=tenant_id, providers=[row])
        uow.tenants.get.return_value = tenant

        result = TenantLLMProviderService(uow).update_provider(
            tenant_id,
            LLMProvider.ANTHROPIC,
            api_key=None,
            is_active=False,
            requester=requester,
        )

        assert row.api_key_encrypted == "existing-cipher"
        assert result.is_active is False

    def test_forbidden_for_non_super_admin_other_tenant(self) -> None:
        uow = _make_uow()
        requester = make_user(tenant_id=uuid.uuid4())

        with pytest.raises(ForbiddenError):
            TenantLLMProviderService(uow).update_provider(
                uuid.uuid4(),
                LLMProvider.ANTHROPIC,
                api_key="x",
                is_active=None,
                requester=requester,
            )

    def test_activating_fresh_provider_without_api_key_raises_validation_error(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        requester = make_user(tenant_id=tenant_id)
        tenant = _make_tenant(tenant_id=tenant_id, providers=[])
        uow.tenants.get.return_value = tenant

        with pytest.raises(ValidationError):
            TenantLLMProviderService(uow).update_provider(
                tenant_id,
                LLMProvider.ANTHROPIC,
                api_key=None,
                is_active=True,
                requester=requester,
            )

        uow.flush.assert_not_called()
        uow.commit.assert_not_called()

    def test_activating_existing_row_without_any_stored_key_raises_validation_error(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        requester = make_user(tenant_id=tenant_id)
        row = _make_row(is_active=False, api_key_encrypted=None)
        tenant = _make_tenant(tenant_id=tenant_id, providers=[row])
        uow.tenants.get.return_value = tenant

        with pytest.raises(ValidationError):
            TenantLLMProviderService(uow).update_provider(
                tenant_id,
                LLMProvider.ANTHROPIC,
                api_key=None,
                is_active=True,
                requester=requester,
            )


class TestTestProvider:
    async def test_raises_validation_error_when_no_api_key(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        requester = make_user(tenant_id=tenant_id)
        uow.tenants.get.return_value = _make_tenant(tenant_id=tenant_id, providers=[])

        with pytest.raises(ValidationError):
            await TenantLLMProviderService(uow).test_provider(
                tenant_id, LLMProvider.ANTHROPIC, requester=requester
            )

    async def test_success_marks_verified(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        requester = make_user(tenant_id=tenant_id)
        row = _make_row(api_key_encrypted="cipher", is_verified=False)
        uow.tenants.get.return_value = _make_tenant(tenant_id=tenant_id, providers=[row])

        with (
            patch(
                "app.services.tenant_llm_provider_service.decrypt_llm_api_key",
                return_value="plain-key",
            ),
            patch(
                "app.services.tenant_llm_provider_service.probe_api_key", new_callable=AsyncMock
            ) as mock_probe,
        ):
            result = await TenantLLMProviderService(uow).test_provider(
                tenant_id, LLMProvider.ANTHROPIC, requester=requester
            )

        mock_probe.assert_awaited_once_with("anthropic", "plain-key")
        assert result.verified is True
        assert row.is_verified is True
        assert row.last_test_error is None
        uow.commit.assert_called_once()

    async def test_failure_marks_unverified_with_error(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        requester = make_user(tenant_id=tenant_id)
        row = _make_row(api_key_encrypted="cipher", is_verified=True)
        uow.tenants.get.return_value = _make_tenant(tenant_id=tenant_id, providers=[row])

        with (
            patch(
                "app.services.tenant_llm_provider_service.decrypt_llm_api_key",
                return_value="plain-key",
            ),
            patch(
                "app.services.tenant_llm_provider_service.probe_api_key",
                new_callable=AsyncMock,
                side_effect=RuntimeError("invalid api key"),
            ),
        ):
            result = await TenantLLMProviderService(uow).test_provider(
                tenant_id, LLMProvider.ANTHROPIC, requester=requester
            )

        assert result.verified is False
        assert result.error == "invalid api key"
        assert row.is_verified is False
        assert row.last_test_error == "invalid api key"

    async def test_forbidden_for_non_super_admin_other_tenant(self) -> None:
        uow = _make_uow()
        requester = make_user(tenant_id=uuid.uuid4())

        with pytest.raises(ForbiddenError):
            await TenantLLMProviderService(uow).test_provider(
                uuid.uuid4(), LLMProvider.ANTHROPIC, requester=requester
            )


class TestGetProviderBalance:
    async def test_raises_validation_error_when_no_api_key(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        requester = make_user(tenant_id=tenant_id)
        uow.tenants.get.return_value = _make_tenant(tenant_id=tenant_id, providers=[])

        with pytest.raises(ValidationError):
            await TenantLLMProviderService(uow).get_provider_balance(
                tenant_id, LLMProvider.DEEPSEEK, requester=requester
            )

    async def test_raises_validation_error_for_unsupported_provider(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        requester = make_user(tenant_id=tenant_id)
        row = _make_row(provider="anthropic", api_key_encrypted="cipher")
        uow.tenants.get.return_value = _make_tenant(tenant_id=tenant_id, providers=[row])

        with pytest.raises(ValidationError):
            await TenantLLMProviderService(uow).get_provider_balance(
                tenant_id, LLMProvider.ANTHROPIC, requester=requester
            )

    async def test_success_returns_balance(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        requester = make_user(tenant_id=tenant_id)
        row = _make_row(provider="deepseek", api_key_encrypted="cipher")
        uow.tenants.get.return_value = _make_tenant(tenant_id=tenant_id, providers=[row])

        with (
            patch(
                "app.services.tenant_llm_provider_service.decrypt_llm_api_key",
                return_value="plain-key",
            ),
            patch(
                "app.services.tenant_llm_provider_service.get_balance",
                new_callable=AsyncMock,
                return_value=LLMBalance(balance=42.5, currency="USD"),
            ) as mock_get_balance,
        ):
            result = await TenantLLMProviderService(uow).get_provider_balance(
                tenant_id, LLMProvider.DEEPSEEK, requester=requester
            )

        mock_get_balance.assert_awaited_once_with("deepseek", "plain-key")
        assert result.balance == 42.5
        assert result.currency == "USD"
        assert result.provider == LLMProvider.DEEPSEEK

    async def test_propagates_error_from_provider_call(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        requester = make_user(tenant_id=tenant_id)
        row = _make_row(provider="deepseek", api_key_encrypted="cipher")
        uow.tenants.get.return_value = _make_tenant(tenant_id=tenant_id, providers=[row])

        with (
            patch(
                "app.services.tenant_llm_provider_service.decrypt_llm_api_key",
                return_value="plain-key",
            ),
            patch(
                "app.services.tenant_llm_provider_service.get_balance",
                new_callable=AsyncMock,
                side_effect=AIServiceError("DeepSeek balance endpoint returned 401"),
            ),
            pytest.raises(AIServiceError),
        ):
            await TenantLLMProviderService(uow).get_provider_balance(
                tenant_id, LLMProvider.DEEPSEEK, requester=requester
            )

    async def test_forbidden_for_non_super_admin_other_tenant(self) -> None:
        uow = _make_uow()
        requester = make_user(tenant_id=uuid.uuid4())

        with pytest.raises(ForbiddenError):
            await TenantLLMProviderService(uow).get_provider_balance(
                uuid.uuid4(), LLMProvider.DEEPSEEK, requester=requester
            )


class TestDeleteProvider:
    def test_soft_deletes_row_and_deactivates(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        requester = make_user(tenant_id=tenant_id)
        row = _make_row(is_active=True, api_key_encrypted="cipher")
        tenant = _make_tenant(tenant_id=tenant_id, providers=[row])
        uow.tenants.get.return_value = tenant

        TenantLLMProviderService(uow).delete_provider(
            tenant_id, LLMProvider.ANTHROPIC, requester=requester
        )

        assert row.deleted_at is not None
        assert row.is_active is False
        # Row stays in the collection — soft delete, not a hard delete.
        assert row in tenant.llm_providers
        uow.commit.assert_called_once()

    def test_not_found_when_no_row_configured(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        requester = make_user(tenant_id=tenant_id)
        tenant = _make_tenant(tenant_id=tenant_id, providers=[])
        uow.tenants.get.return_value = tenant

        with pytest.raises(NotFoundError):
            TenantLLMProviderService(uow).delete_provider(
                tenant_id, LLMProvider.ANTHROPIC, requester=requester
            )

        uow.commit.assert_not_called()

    def test_not_found_when_row_already_deleted(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        requester = make_user(tenant_id=tenant_id)
        row = _make_row(deleted_at=datetime.now(tz=UTC))
        tenant = _make_tenant(tenant_id=tenant_id, providers=[row])
        uow.tenants.get.return_value = tenant

        with pytest.raises(NotFoundError):
            TenantLLMProviderService(uow).delete_provider(
                tenant_id, LLMProvider.ANTHROPIC, requester=requester
            )

    def test_forbidden_for_non_super_admin_other_tenant(self) -> None:
        uow = _make_uow()
        requester = make_user(tenant_id=uuid.uuid4())

        with pytest.raises(ForbiddenError):
            TenantLLMProviderService(uow).delete_provider(
                uuid.uuid4(), LLMProvider.ANTHROPIC, requester=requester
            )

        uow.tenants.get.assert_not_called()

    def test_deleted_provider_excluded_from_list_and_reconfigurable(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        requester = make_user(tenant_id=tenant_id)
        row = _make_row(is_active=True, api_key_encrypted="cipher")
        tenant = _make_tenant(tenant_id=tenant_id, providers=[row])
        uow.tenants.get.return_value = tenant

        TenantLLMProviderService(uow).delete_provider(
            tenant_id, LLMProvider.ANTHROPIC, requester=requester
        )

        assert TenantLLMProviderService(uow).list_providers(tenant_id, requester=requester) == []

        def _refresh(obj):
            obj.id = uuid.uuid4()

        uow.refresh.side_effect = _refresh
        with patch(
            "app.services.tenant_llm_provider_service.encrypt_llm_api_key",
            return_value="new-cipher",
        ):
            result = TenantLLMProviderService(uow).update_provider(
                tenant_id,
                LLMProvider.ANTHROPIC,
                api_key="sk-new",
                is_active=True,
                requester=requester,
            )

        # A fresh row is created rather than reviving the deleted one.
        assert len(tenant.llm_providers) == 2
        assert result.has_api_key is True
