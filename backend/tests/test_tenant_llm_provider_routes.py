"""Unit tests for tenant LLM provider routes.

Handlers are called directly with a mocked ``TenantLLMProviderService``.
``test_tenant_llm_provider`` is rate-limited so it receives a real minimal
``starlette.Request`` (SlowAPI inspects it) — mirroring
``tests/test_jira_integration_routes.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
import typing
from unittest.mock import AsyncMock, patch
import uuid

from starlette.requests import Request

from app.core.constants import ROLE_ADMIN, ROLE_MEMBER, ROLE_SUPER_ADMIN
from app.core.enums.llm_provider import LLMProvider

# Aliased so pytest does not collect the route handler itself as a test case.
from app.routes.v1.tenants import (
    delete_tenant_llm_provider,
    get_tenant_llm_provider_balance,
    list_tenant_llm_providers,
    test_tenant_llm_provider as call_test_tenant_llm_provider,
    update_tenant_llm_provider,
)
from app.schemas.tenant_schema import (
    TenantLLMProviderBalanceResponse,
    TenantLLMProviderOut,
    TenantLLMProviderTestResponse,
    TenantLLMProviderUpdateRequest,
)
from tests.conftest import make_user


def _extract_dependency_roles(func: typing.Callable, param_name: str) -> tuple[str, ...]:
    """Return the roles tuple a ``require_roles(...)``-gated parameter enforces.

    See tests/test_role_routes.py's identical helper for rationale — resolves
    the ``Annotated[User, Depends(require_roles(...))]`` alias and reads the
    closed-over roles, without re-testing `require_roles` itself (covered by
    tests/test_deps.py).
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


def _make_request() -> Request:
    return Request(
        scope={
            "type": "http",
            "method": "POST",
            "path": "/",
            "query_string": b"",
            "headers": [],
        }
    )


def _provider_out(
    provider: LLMProvider = LLMProvider.ANTHROPIC, **overrides
) -> TenantLLMProviderOut:
    defaults = dict(
        id=uuid.uuid4(),
        provider=provider,
        is_active=True,
        has_api_key=True,
        api_key_hint="abcdef...123456",
        is_verified=False,
        last_tested_at=None,
        last_test_error=None,
    )
    defaults.update(overrides)
    return TenantLLMProviderOut(**defaults)


class TestDependencyWiring:
    def test_list_tenant_llm_providers_gated_by_member_admin_super_admin(self) -> None:
        roles = _extract_dependency_roles(list_tenant_llm_providers, "current_user")
        assert set(roles) == {ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_MEMBER}

    def test_update_tenant_llm_provider_gated_by_admin_super_admin_only(self) -> None:
        roles = _extract_dependency_roles(update_tenant_llm_provider, "current_user")
        assert set(roles) == {ROLE_SUPER_ADMIN, ROLE_ADMIN}

    def test_test_tenant_llm_provider_gated_by_admin_super_admin_only(self) -> None:
        roles = _extract_dependency_roles(call_test_tenant_llm_provider, "current_user")
        assert set(roles) == {ROLE_SUPER_ADMIN, ROLE_ADMIN}

    def test_delete_tenant_llm_provider_gated_by_admin_super_admin_only(self) -> None:
        roles = _extract_dependency_roles(delete_tenant_llm_provider, "current_user")
        assert set(roles) == {ROLE_SUPER_ADMIN, ROLE_ADMIN}

    def test_get_tenant_llm_provider_balance_gated_by_admin_super_admin_only(self) -> None:
        roles = _extract_dependency_roles(get_tenant_llm_provider_balance, "current_user")
        assert set(roles) == {ROLE_SUPER_ADMIN, ROLE_ADMIN}


def test_list_tenant_llm_providers_success() -> None:
    tenant_id = uuid.uuid4()
    user = make_user(tenant_id=tenant_id)
    items = [_provider_out()]

    with patch(
        "app.routes.v1.tenants.TenantLLMProviderService.list_providers", return_value=items
    ) as mock_list:
        result = list_tenant_llm_providers(tenant_id=tenant_id, uow=object(), current_user=user)

    assert result.success is True
    assert result.data.items == items
    mock_list.assert_called_once_with(tenant_id, requester=user)


async def test_update_tenant_llm_provider_success() -> None:
    tenant_id = uuid.uuid4()
    user = make_user(tenant_id=tenant_id)
    body = TenantLLMProviderUpdateRequest(api_key="sk-secret", is_active=True)
    updated = _provider_out()

    with patch(
        "app.routes.v1.tenants.TenantLLMProviderService.update_provider", return_value=updated
    ) as mock_update:
        result = await update_tenant_llm_provider(
            request=_make_request(),
            tenant_id=tenant_id,
            provider=LLMProvider.ANTHROPIC,
            body=body,
            uow=object(),
            current_user=user,
        )

    assert result.success is True
    assert result.data is updated
    mock_update.assert_called_once_with(
        tenant_id,
        LLMProvider.ANTHROPIC,
        api_key="sk-secret",
        is_active=True,
        requester=user,
    )


async def test_test_tenant_llm_provider_success() -> None:
    tenant_id = uuid.uuid4()
    user = make_user(tenant_id=tenant_id)
    expected = TenantLLMProviderTestResponse(
        provider=LLMProvider.ANTHROPIC,
        verified=True,
        tested_at=datetime.now(tz=UTC),
        error=None,
    )

    with patch(
        "app.routes.v1.tenants.TenantLLMProviderService.test_provider",
        new_callable=AsyncMock,
        return_value=expected,
    ) as mock_test:
        result = await call_test_tenant_llm_provider(
            request=_make_request(),
            tenant_id=tenant_id,
            provider=LLMProvider.ANTHROPIC,
            uow=object(),
            current_user=user,
        )

    assert result.success is True
    assert result.data is expected
    mock_test.assert_awaited_once_with(tenant_id, LLMProvider.ANTHROPIC, requester=user)


async def test_get_tenant_llm_provider_balance_success() -> None:
    tenant_id = uuid.uuid4()
    user = make_user(tenant_id=tenant_id)
    expected = TenantLLMProviderBalanceResponse(
        provider=LLMProvider.DEEPSEEK,
        balance=42.5,
        currency="USD",
        fetched_at=datetime.now(tz=UTC),
    )

    with patch(
        "app.routes.v1.tenants.TenantLLMProviderService.get_provider_balance",
        new_callable=AsyncMock,
        return_value=expected,
    ) as mock_get_balance:
        result = await get_tenant_llm_provider_balance(
            request=_make_request(),
            tenant_id=tenant_id,
            provider=LLMProvider.DEEPSEEK,
            uow=object(),
            current_user=user,
        )

    assert result.success is True
    assert result.data is expected
    mock_get_balance.assert_awaited_once_with(tenant_id, LLMProvider.DEEPSEEK, requester=user)


async def test_delete_tenant_llm_provider_success() -> None:
    tenant_id = uuid.uuid4()
    user = make_user(tenant_id=tenant_id)

    with patch(
        "app.routes.v1.tenants.TenantLLMProviderService.delete_provider", return_value=None
    ) as mock_delete:
        result = await delete_tenant_llm_provider(
            request=_make_request(),
            tenant_id=tenant_id,
            provider=LLMProvider.ANTHROPIC,
            uow=object(),
            current_user=user,
        )

    assert result.success is True
    mock_delete.assert_called_once_with(tenant_id, LLMProvider.ANTHROPIC, requester=user)
