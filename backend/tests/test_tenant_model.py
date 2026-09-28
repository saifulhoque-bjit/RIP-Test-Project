"""Unit tests for the Tenant ORM model's computed properties."""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.postgres.tenant_llm_provider_model import TenantLLMProvider
from app.models.postgres.tenant_model import Tenant


class TestProvidersProperty:
    def test_includes_active_and_inactive_configured_providers(self) -> None:
        tenant = Tenant()
        tenant.llm_providers = [
            TenantLLMProvider(provider="anthropic", is_active=True),
            TenantLLMProvider(provider="deepseek", is_active=False),
        ]

        assert set(tenant.providers) == {"anthropic", "deepseek"}

    def test_excludes_soft_deleted_rows(self) -> None:
        tenant = Tenant()
        tenant.llm_providers = [
            TenantLLMProvider(provider="anthropic", is_active=True),
            TenantLLMProvider(
                provider="deepseek", is_active=False, deleted_at=datetime.now(tz=UTC)
            ),
        ]

        assert tenant.providers == ["anthropic"]

    def test_empty_when_no_providers_configured(self) -> None:
        tenant = Tenant()
        tenant.llm_providers = []

        assert tenant.providers == []
