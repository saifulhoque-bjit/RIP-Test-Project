"""Unit tests for tenant routes."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
import uuid

from app.core.enums.llm_provider import LLMProvider
from app.core.enums.tenant_status import TenantStatus
from app.models.postgres.tenant_model import Tenant
from app.routes.v1.tenants import (
    create_tenant,
    get_my_tenant,
    get_tenant_stats,
    list_tenants,
    replace_tenant,
)
from app.schemas.tenant_schema import TenantCreateRequest, TenantStatsResponse
from tests.conftest import make_user


def _make_tenant() -> Tenant:
    t = Tenant()
    t.id = uuid.uuid4()
    t.name = "Acme"
    t.code = "ACME"
    t.contact_email = "jane@acme.test"
    t.status = "active"
    t.created_at = datetime.now(tz=UTC)
    t.updated_at = datetime.now(tz=UTC)
    return t


def test_get_my_tenant_success() -> None:
    user = make_user()
    user.tenant_id = uuid.uuid4()
    tenant = _make_tenant()

    with patch("app.routes.v1.tenants.TenantService.get_for_user", return_value=tenant) as mock_get:
        result = get_my_tenant(current_user=user, uow=object())

    assert result.success is True
    assert result.data is not None
    assert result.data.name == "Acme"
    mock_get.assert_called_once_with(user.tenant_id)


class TestCreateTenant:
    def test_creates_tenant_via_service_and_wraps_response(self) -> None:
        """Route-level concern only: request parsing → one service call →
        ApiResponse wrapping. The notification-email side effect is owned by
        TenantService.create itself — see tests/test_tenant_service.py."""
        admin = make_user()
        tenant = _make_tenant()
        body = TenantCreateRequest(
            name="Acme",
            contact_email="jane@example.com",
            status=TenantStatus.ACTIVE,
            providers=["anthropic"],
        )

        with patch(
            "app.routes.v1.tenants.TenantService.create", return_value=tenant
        ) as mock_create:
            result = create_tenant(body=body, uow=MagicMock(), _admin=admin)

        assert result.success is True
        assert result.data.name == "Acme"
        mock_create.assert_called_once_with(
            name="Acme",
            contact_email="jane@example.com",
            address=None,
            status=TenantStatus.ACTIVE,
            providers=[LLMProvider.ANTHROPIC],
        )


def test_replace_tenant_calls_service_and_wraps_response() -> None:
    admin = make_user()
    tenant = _make_tenant()
    body = TenantCreateRequest(
        name="Acme",
        contact_email="jane@example.com",
        status=TenantStatus.SUSPENDED,
        providers=["anthropic"],
    )

    with patch(
        "app.routes.v1.tenants.TenantService.replace", return_value=tenant
    ) as mock_replace:
        result = replace_tenant(tenant_id=tenant.id, body=body, uow=MagicMock(), _admin=admin)

    assert result.success is True
    assert result.data.name == "Acme"
    mock_replace.assert_called_once_with(
        tenant.id,
        name="Acme",
        contact_email="jane@example.com",
        address=None,
        status=TenantStatus.SUSPENDED,
        providers=[LLMProvider.ANTHROPIC],
    )


def test_list_tenants_includes_project_count_per_tenant() -> None:
    admin = make_user()
    tenant_with_projects = _make_tenant()
    tenant_without_projects = _make_tenant()
    tenant_without_projects.id = uuid.uuid4()

    with (
        patch(
            "app.routes.v1.tenants.TenantService.list_for_requester",
            return_value=([tenant_with_projects, tenant_without_projects], 2),
        ) as mock_list,
        patch(
            "app.routes.v1.tenants.TenantService.get_project_counts_by_tenant_ids",
            return_value={tenant_with_projects.id: 5},
        ) as mock_counts,
    ):
        result = list_tenants(uow=MagicMock(), current_user=admin, skip=0, limit=20)

    assert result.success is True
    assert result.data.items[0].total_number_of_project == 5
    assert result.data.items[1].total_number_of_project == 0
    mock_list.assert_called_once_with(requester=admin, skip=0, limit=20)
    mock_counts.assert_called_once_with([tenant_with_projects.id, tenant_without_projects.id])


def test_get_tenant_stats_success() -> None:
    admin = make_user()
    stats = TenantStatsResponse(
        total_tenants=10,
        active_tenants=6,
        pending_invitation_tenants=2,
        pending_invitation_client_admin=3,
        deactivated_tenants=2,
        total_projects=42,
    )

    with patch(
        "app.routes.v1.tenants.TenantService.get_stats", return_value=stats
    ) as mock_get_stats:
        result = get_tenant_stats(uow=MagicMock(), _admin=admin)

    assert result.success is True
    assert result.data == stats
    mock_get_stats.assert_called_once_with()
