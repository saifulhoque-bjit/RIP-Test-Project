"""Service for a tenant's per-provider LLM configuration: enable/disable,
API key storage, and connection testing.

All methods are synchronous except :meth:`test_provider` (the connection
test itself makes a real outbound call), matching the pattern used by
:class:`~app.services.jira_integration_service.JiraIntegrationService`.
"""

from __future__ import annotations

from datetime import UTC, datetime
import uuid

from app.clients.llm_factory import BALANCE_SUPPORTED_PROVIDERS, get_balance, probe_api_key
from app.core.constants import ROLE_SUPER_ADMIN
from app.core.enums.llm_provider import LLMProvider
from app.core.exceptions import (
    ForbiddenError,
    NotFoundError,
    ServiceError,
    ValidationError,
)
from app.core.messages import (
    MSG_TENANT_LLM_PROVIDER_ACTIVATE_REQUIRES_API_KEY,
    MSG_TENANT_LLM_PROVIDER_BALANCE_NOT_SUPPORTED,
    MSG_TENANT_LLM_PROVIDER_FORBIDDEN_OTHER_TENANT,
    MSG_TENANT_LLM_PROVIDER_NO_API_KEY,
    MSG_TENANT_LLM_PROVIDER_NOT_FOUND,
    MSG_TENANT_NOT_FOUND,
)
from app.db.unit_of_work import UnitOfWork
from app.models.postgres.tenant_llm_provider_model import TenantLLMProvider
from app.models.postgres.user_model import User
from app.schemas.tenant_schema import (
    TenantLLMProviderBalanceResponse,
    TenantLLMProviderOut,
    TenantLLMProviderTestResponse,
)
from app.utils.encryption import decrypt_llm_api_key, encrypt_llm_api_key
from app.utils.logger import get_logger

logger = get_logger(__name__)


class TenantLLMProviderService:
    """Per-tenant LLM provider enable/disable, API key storage, and connection testing."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    @staticmethod
    def _mask_key(ciphertext: str | None) -> str:
        """Mask the *encrypted* value for display — never decrypts just to build a hint.

        Shows first 6 and last 6 characters of the stored ciphertext, purely
        as a "something is saved and it changed" indicator (mirrors
        ``JiraIntegrationService._mask_token``).
        """
        if not ciphertext or len(ciphertext) <= 12:
            return "****"
        return f"{ciphertext[:6]}...{ciphertext[-6:]}"

    def _get_tenant(self, tenant_id: uuid.UUID):
        tenant = self._uow.tenants.get(tenant_id)
        if tenant is None:
            raise NotFoundError(MSG_TENANT_NOT_FOUND.format(tenant_id=tenant_id))
        return tenant

    @staticmethod
    def _assert_tenant_access(tenant_id: uuid.UUID, requester: User) -> None:
        is_super_admin = any(role.name == ROLE_SUPER_ADMIN for role in requester.roles)
        if not is_super_admin and requester.tenant_id != tenant_id:
            raise ForbiddenError(MSG_TENANT_LLM_PROVIDER_FORBIDDEN_OTHER_TENANT)

    @staticmethod
    def _find_row(tenant, provider: LLMProvider) -> TenantLLMProvider | None:
        return next(
            (
                r
                for r in tenant.llm_providers
                if r.provider == provider.value and r.deleted_at is None
            ),
            None,
        )

    def _to_out(self, provider: LLMProvider, row: TenantLLMProvider) -> TenantLLMProviderOut:
        return TenantLLMProviderOut(
            id=row.id,
            provider=provider,
            is_active=row.is_active,
            has_api_key=row.api_key_encrypted is not None,
            api_key_hint=self._mask_key(row.api_key_encrypted),
            is_verified=row.is_verified,
            last_tested_at=row.last_tested_at,
            last_test_error=row.last_test_error,
        )

    # ── Queries ────────────────────────────────────────────────────────────

    def get_active_api_key(
        self, tenant_id: uuid.UUID, provider: str, *, require_verified: bool = False
    ) -> str | None:
        """Return the decrypted API key for tenant_id's active row matching provider.

        Returns None if the tenant has no row for that provider, the row is
        disabled, no key is stored, or (when ``require_verified`` is True)
        the stored key hasn't passed :meth:`test_provider` — callers should
        treat that the same as "no key configured" and fall back to whatever
        default the LLM client resolves to. No requester/access check: this
        is for internal system use (LLM pipeline dispatch resolving a key for
        a project the caller has already authorized), not a user-facing
        endpoint.
        """
        tenant = self._uow.tenants.get(tenant_id)
        if tenant is None:
            return None
        row = next(
            (
                r
                for r in tenant.llm_providers
                if r.provider == provider and r.is_active and r.deleted_at is None
            ),
            None,
        )
        if row is None or not row.api_key_encrypted:
            return None
        if require_verified and not row.is_verified:
            return None
        try:
            return decrypt_llm_api_key(row.api_key_encrypted)
        except ServiceError:
            logger.warning(
                "TenantLLMProviderService.get_active_api_key: decrypt failed tenant_id=%s provider=%s",
                tenant_id,
                provider,
                exc_info=True,
            )
            return None

    def list_providers(
        self, tenant_id: uuid.UUID, *, requester: User
    ) -> list[TenantLLMProviderOut]:
        """Return only the providers *tenant_id* has actually configured.

        One entry per non-deleted ``tenant_llm_providers`` row that exists for
        this tenant — a tenant with one configured provider gets one entry,
        two configured providers gets two, etc. Providers never configured
        (no row via :meth:`update_provider`) or soft-deleted (via
        :meth:`delete_provider`) are omitted entirely.
        """
        self._assert_tenant_access(tenant_id, requester)
        tenant = self._get_tenant(tenant_id)
        return [
            self._to_out(LLMProvider(row.provider), row)
            for row in tenant.llm_providers
            if row.deleted_at is None
        ]

    # ── Mutations ──────────────────────────────────────────────────────────

    def update_provider(
        self,
        tenant_id: uuid.UUID,
        provider: LLMProvider,
        *,
        api_key: str | None,
        is_active: bool | None,
        requester: User,
    ) -> TenantLLMProviderOut:
        """Set/replace the API key and/or flip the enable toggle for one provider.

        ``None`` for either field leaves it unchanged. Setting ``api_key``
        resets any prior connection-test result — a new key needs re-testing.

        Raises:
            ValidationError: If this update would leave the provider active
                with no API key configured at all (an "active" provider that
                can't actually be used).
        """
        self._assert_tenant_access(tenant_id, requester)
        tenant = self._get_tenant(tenant_id)
        row = self._find_row(tenant, provider)
        if row is None:
            row = TenantLLMProvider(tenant_id=tenant.id, provider=provider.value, is_active=False)
            tenant.llm_providers.append(row)

        if api_key is not None:
            row.api_key_encrypted = encrypt_llm_api_key(api_key)
            row.is_verified = False
            row.last_tested_at = None
            row.last_test_error = None
        if is_active is not None:
            row.is_active = is_active

        if row.is_active and not row.api_key_encrypted:
            raise ValidationError(
                MSG_TENANT_LLM_PROVIDER_ACTIVATE_REQUIRES_API_KEY.format(provider=provider.value)
            )

        self._uow.flush()
        self._uow.refresh(row)
        self._uow.commit()
        logger.info(
            "Tenant LLM provider config updated tenant_id=%s provider=%s", tenant_id, provider.value
        )
        return self._to_out(provider, row)

    def delete_provider(
        self, tenant_id: uuid.UUID, provider: LLMProvider, *, requester: User
    ) -> None:
        """Soft-delete *provider*'s configuration for *tenant_id*.

        Sets ``deleted_at``/``is_active=False`` rather than removing the row,
        so the stored key/verification history is preserved. The provider can
        be configured again afterwards (:meth:`update_provider` creates a
        fresh row rather than reusing the deleted one).

        Raises:
            NotFoundError: If the tenant has no (non-deleted) row for this
                provider.
        """
        self._assert_tenant_access(tenant_id, requester)
        tenant = self._get_tenant(tenant_id)
        row = self._find_row(tenant, provider)
        if row is None:
            raise NotFoundError(MSG_TENANT_LLM_PROVIDER_NOT_FOUND.format(provider=provider.value))

        row.deleted_at = datetime.now(UTC)
        row.is_active = False

        self._uow.flush()
        self._uow.commit()
        logger.info(
            "Tenant LLM provider config deleted tenant_id=%s provider=%s", tenant_id, provider.value
        )

    # ── Connection test ────────────────────────────────────────────────────

    async def test_provider(
        self, tenant_id: uuid.UUID, provider: LLMProvider, *, requester: User
    ) -> TenantLLMProviderTestResponse:
        """Test the stored API key for *provider* against the real provider API.

        Persists the outcome (``is_verified``/``last_tested_at``/
        ``last_test_error``) regardless of success or failure.
        """
        self._assert_tenant_access(tenant_id, requester)
        tenant = self._get_tenant(tenant_id)
        row = self._find_row(tenant, provider)
        if row is None or not row.api_key_encrypted:
            raise ValidationError(
                MSG_TENANT_LLM_PROVIDER_NO_API_KEY.format(provider=provider.value)
            )

        api_key = decrypt_llm_api_key(row.api_key_encrypted)
        tested_at = datetime.now(UTC)
        try:
            await probe_api_key(provider.value, api_key)
            row.is_verified = True
            row.last_test_error = None
        except Exception as exc:
            logger.warning(
                "LLM provider key test failed tenant_id=%s provider=%s error=%s",
                tenant_id,
                provider.value,
                exc,
            )
            row.is_verified = False
            row.last_test_error = str(exc)[:500]
        row.last_tested_at = tested_at

        self._uow.flush()
        self._uow.commit()

        return TenantLLMProviderTestResponse(
            provider=provider,
            verified=row.is_verified,
            tested_at=tested_at,
            error=row.last_test_error,
        )

    # ── Balance ────────────────────────────────────────────────────────────

    async def get_provider_balance(
        self, tenant_id: uuid.UUID, provider: LLMProvider, *, requester: User
    ) -> TenantLLMProviderBalanceResponse:
        """Fetch the remaining account balance for *provider* from the real provider API.

        Only providers with a genuine, documented balance endpoint reachable
        via a plain API key are supported (currently DeepSeek only) —
        Anthropic/OpenAI/Google require org/console-level billing access, so
        those raise :class:`ValidationError` rather than returning fabricated
        data. Unlike :meth:`test_provider`, a failed lookup is not persisted
        anywhere — this is a read-only, on-demand call.
        """
        self._assert_tenant_access(tenant_id, requester)
        tenant = self._get_tenant(tenant_id)
        row = self._find_row(tenant, provider)
        if row is None or not row.api_key_encrypted:
            raise ValidationError(
                MSG_TENANT_LLM_PROVIDER_NO_API_KEY.format(provider=provider.value)
            )
        if provider.value not in BALANCE_SUPPORTED_PROVIDERS:
            raise ValidationError(
                MSG_TENANT_LLM_PROVIDER_BALANCE_NOT_SUPPORTED.format(provider=provider.value)
            )

        api_key = decrypt_llm_api_key(row.api_key_encrypted)
        balance = await get_balance(provider.value, api_key)

        return TenantLLMProviderBalanceResponse(
            provider=provider,
            balance=balance.balance,
            currency=balance.currency,
            fetched_at=datetime.now(UTC),
        )
