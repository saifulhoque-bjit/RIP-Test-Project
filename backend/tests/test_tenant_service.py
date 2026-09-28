"""Unit tests for TenantService.

Strategy:
- All repository interactions are replaced by MagicMock so no DB is needed.
- The UnitOfWork is constructed manually with mock repositories injected.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.constants import ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.core.enums.llm_provider import LLMProvider
from app.core.enums.tenant_status import TenantStatus
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ServiceError
from app.db.unit_of_work import UnitOfWork
from app.models.postgres.tenant_llm_provider_model import TenantLLMProvider
from app.models.postgres.tenant_model import Tenant
from app.services.tenant_service import TenantService
from tests.conftest import make_user


def _make_requester(*, role_name: str, tenant_id: uuid.UUID | None):
    role = MagicMock()
    role.name = role_name
    return make_user(tenant_id=tenant_id, roles=[role])


def _make_uow() -> MagicMock:
    uow = MagicMock(spec=UnitOfWork)
    uow.tenants = MagicMock()
    # Default to "no clash" — the common case — so tests only need to set
    # these when they're specifically exercising the conflict path.
    uow.tenants.get_by_name.return_value = None
    uow.tenants.get_by_contact_email.return_value = None
    uow.tenants.get_by_code.return_value = None
    return uow


def _make_tenant(
    *,
    tenant_id: uuid.UUID | None = None,
    name: str = "Acme",
    code: str = "ACME",
    status: str = "active",
) -> Tenant:
    t = Tenant()
    t.id = tenant_id or uuid.uuid4()
    t.name = name
    t.code = code
    t.contact_email = "jane@acme.test"
    t.status = status
    t.created_at = datetime.now(tz=UTC)
    t.updated_at = datetime.now(tz=UTC)
    return t


class TestGetForUser:
    def test_returns_tenant_when_assigned_and_found(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        tenant = _make_tenant(tenant_id=tenant_id)
        uow.tenants.get.return_value = tenant

        result = TenantService(uow).get_for_user(tenant_id)

        assert result is tenant
        uow.tenants.get.assert_called_once_with(tenant_id)

    def test_raises_not_found_when_user_has_no_tenant(self) -> None:
        uow = _make_uow()

        with pytest.raises(NotFoundError):
            TenantService(uow).get_for_user(None)

        uow.tenants.get.assert_not_called()

    def test_raises_not_found_when_tenant_row_missing(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        uow.tenants.get.return_value = None

        with pytest.raises(NotFoundError):
            TenantService(uow).get_for_user(tenant_id)


class TestGetScoped:
    def test_super_admin_may_get_any_tenant(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        tenant = _make_tenant(tenant_id=tenant_id)
        uow.tenants.get.return_value = tenant
        requester = _make_requester(role_name=ROLE_SUPER_ADMIN, tenant_id=uuid.uuid4())

        result = TenantService(uow).get_scoped(tenant_id, requester=requester)

        assert result is tenant

    def test_admin_may_get_own_tenant(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        tenant = _make_tenant(tenant_id=tenant_id)
        uow.tenants.get.return_value = tenant
        requester = _make_requester(role_name=ROLE_ADMIN, tenant_id=tenant_id)

        result = TenantService(uow).get_scoped(tenant_id, requester=requester)

        assert result is tenant

    def test_admin_forbidden_from_other_tenant(self) -> None:
        uow = _make_uow()
        requester = _make_requester(role_name=ROLE_ADMIN, tenant_id=uuid.uuid4())

        with pytest.raises(ForbiddenError):
            TenantService(uow).get_scoped(uuid.uuid4(), requester=requester)

        uow.tenants.get.assert_not_called()


class TestListForRequester:
    def test_super_admin_lists_all_tenants_paginated(self) -> None:
        uow = _make_uow()
        tenants = [_make_tenant(), _make_tenant(name="Beta")]
        uow.tenants.get_paginated.return_value = (tenants, 2)
        requester = _make_requester(role_name=ROLE_SUPER_ADMIN, tenant_id=uuid.uuid4())

        result, total = TenantService(uow).list_for_requester(requester=requester, skip=0, limit=20)

        assert result == tenants
        assert total == 2
        uow.tenants.get_paginated.assert_called_once_with(skip=0, limit=20)

    def test_admin_lists_only_own_tenant(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        tenant = _make_tenant(tenant_id=tenant_id)
        uow.tenants.get.return_value = tenant
        requester = _make_requester(role_name=ROLE_ADMIN, tenant_id=tenant_id)

        result, total = TenantService(uow).list_for_requester(requester=requester)

        assert result == [tenant]
        assert total == 1
        uow.tenants.get_paginated.assert_not_called()

    def test_admin_with_no_tenant_gets_empty_list(self) -> None:
        uow = _make_uow()
        requester = _make_requester(role_name=ROLE_ADMIN, tenant_id=None)

        result, total = TenantService(uow).list_for_requester(requester=requester)

        assert result == []
        assert total == 0
        uow.tenants.get.assert_not_called()

    def test_admin_own_tenant_pagination_past_first_page_is_empty(self) -> None:
        uow = _make_uow()
        tenant_id = uuid.uuid4()
        uow.tenants.get.return_value = _make_tenant(tenant_id=tenant_id)
        requester = _make_requester(role_name=ROLE_ADMIN, tenant_id=tenant_id)

        result, total = TenantService(uow).list_for_requester(requester=requester, skip=1)

        assert result == []
        assert total == 1


class TestGetProjectCountsByTenantIds:
    def test_delegates_to_project_stats_repository(self) -> None:
        uow = _make_uow()
        uow.project_stats = MagicMock()
        tenant_id = uuid.uuid4()
        uow.project_stats.count_projects_by_tenant_ids.return_value = {tenant_id: 4}

        result = TenantService(uow).get_project_counts_by_tenant_ids([tenant_id])

        assert result == {tenant_id: 4}
        uow.project_stats.count_projects_by_tenant_ids.assert_called_once_with([tenant_id])


class TestCreate:
    def test_builds_deduped_provider_rows(self) -> None:
        uow = _make_uow()

        result = TenantService(uow).create(
            name="Acme",
            contact_email="jane@acme.test",
            providers=[LLMProvider.ANTHROPIC, LLMProvider.DEEPSEEK, LLMProvider.ANTHROPIC],
        )

        assert [p.provider for p in result.llm_providers] == ["anthropic", "deepseek"]
        assert all(p.is_active is False for p in result.llm_providers)
        uow.commit.assert_called_once()

    def test_generates_code_from_name(self) -> None:
        uow = _make_uow()

        result = TenantService(uow).create(name="Acme Corp", contact_email="jane@acme.test")

        assert result.code == "AC"

    def test_single_word_name_uses_leading_characters(self) -> None:
        uow = _make_uow()

        result = TenantService(uow).create(name="Acme", contact_email="jane@acme.test")

        assert result.code == "ACME"

    def test_appends_numeric_suffix_on_code_collision(self) -> None:
        uow = _make_uow()
        uow.tenants.get_by_code.side_effect = lambda code: _make_tenant() if code == "AC" else None

        result = TenantService(uow).create(name="Acme Corp", contact_email="jane@acme.test")

        assert result.code == "AC2"

    def test_raises_service_error_when_code_generation_exhausted(self) -> None:
        uow = _make_uow()
        uow.tenants.get_by_code.return_value = _make_tenant()

        with pytest.raises(ServiceError):
            TenantService(uow).create(name="Acme Corp", contact_email="jane@acme.test")

        uow.add.assert_not_called()

    def test_regenerates_code_on_race_condition_integrity_error(self) -> None:
        """A collision between the get_by_code precheck and the flush (two
        concurrent creates for the same name) must not surface as a
        client-facing conflict — the caller never supplied a code — so the
        service regenerates and retries instead of raising."""
        uow = _make_uow()
        code_conflict = IntegrityError(
            "INSERT", {}, Exception("duplicate key value violates ix_tenants_code")
        )
        uow.flush.side_effect = [code_conflict, None]

        result = TenantService(uow).create(name="Acme Corp", contact_email="jane@acme.test")

        assert uow.flush.call_count == 2
        uow.commit.assert_called_once()
        assert result.code == "AC"

    def test_with_no_providers_defaults_to_empty(self) -> None:
        uow = _make_uow()

        result = TenantService(uow).create(name="Acme", contact_email="jane@acme.test")

        assert result.llm_providers == []

    def test_notifies_the_tenants_contact_email(self) -> None:
        """The dispatch is owned by the service, not the route — see
        tests/test_tenant_routes.py for the route-level split of concerns.
        Email content itself is EmailService's responsibility — see
        tests/test_email_service.py."""
        uow = _make_uow()

        with patch("app.services.tenant_service.EmailService.send_tenant_created") as mock_send:
            result = TenantService(uow).create(name="Acme", contact_email="jane@acme.test")

        mock_send.assert_called_once_with(result)
        assert result.name == "Acme"


class TestUpdate:
    def test_replaces_provider_set_when_provided(self) -> None:
        uow = _make_uow()
        tenant = _make_tenant()
        tenant.llm_providers = [TenantLLMProvider(provider="anthropic")]
        uow.tenants.get.return_value = tenant

        result = TenantService(uow).update(
            tenant.id, providers=[LLMProvider.GOOGLE, LLMProvider.OPENAI]
        )

        assert [p.provider for p in result.llm_providers] == ["google", "openai"]

    def test_omitted_providers_leaves_existing_set_untouched(self) -> None:
        uow = _make_uow()
        tenant = _make_tenant()
        existing = [TenantLLMProvider(provider="anthropic")]
        tenant.llm_providers = existing
        uow.tenants.get.return_value = tenant

        result = TenantService(uow).update(tenant.id, name="New Name")

        assert list(result.llm_providers) == existing

    def test_empty_list_clears_providers(self) -> None:
        uow = _make_uow()
        tenant = _make_tenant()
        tenant.llm_providers = [TenantLLMProvider(provider="anthropic")]
        uow.tenants.get.return_value = tenant

        result = TenantService(uow).update(tenant.id, providers=[])

        assert result.llm_providers == []

    def test_resubmitting_same_provider_keeps_existing_row_untouched(self) -> None:
        """Regression test: re-submitting a provider that's already enabled must
        not delete-and-recreate its row — that used to both wipe the stored
        API key/verification state and trip ``uq_tenant_llm_provider`` with a
        real IntegrityError (mis-surfaced as a tenant-name conflict)."""
        uow = _make_uow()
        tenant = _make_tenant()
        existing_row = TenantLLMProvider(provider="anthropic", is_active=True)
        existing_row.api_key_encrypted = "encrypted-secret"
        tenant.llm_providers = [existing_row]
        uow.tenants.get.return_value = tenant

        result = TenantService(uow).update(tenant.id, providers=[LLMProvider.ANTHROPIC])

        assert list(result.llm_providers) == [existing_row]
        assert result.llm_providers[0].api_key_encrypted == "encrypted-secret"

    def test_switching_providers_drops_old_and_adds_new_rows(self) -> None:
        uow = _make_uow()
        tenant = _make_tenant()
        kept_row = TenantLLMProvider(provider="anthropic", is_active=True)
        dropped_row = TenantLLMProvider(provider="deepseek", is_active=True)
        tenant.llm_providers = [kept_row, dropped_row]
        uow.tenants.get.return_value = tenant

        result = TenantService(uow).update(
            tenant.id, providers=[LLMProvider.ANTHROPIC, LLMProvider.GOOGLE]
        )

        assert kept_row in result.llm_providers
        assert dropped_row not in result.llm_providers
        assert {p.provider for p in result.llm_providers} == {"anthropic", "google"}

    def test_newly_added_provider_defaults_to_inactive(self) -> None:
        """Regression test: a provider added via PATCH/PUT has no API key yet,
        so its row must start inactive — matching TenantService.create's
        _build_provider_rows and TenantLLMProviderService.update_provider's
        invariant that a provider can't be active without a stored key."""
        uow = _make_uow()
        tenant = _make_tenant()
        tenant.llm_providers = []
        uow.tenants.get.return_value = tenant

        result = TenantService(uow).update(tenant.id, providers=[LLMProvider.ANTHROPIC])

        assert len(result.llm_providers) == 1
        assert result.llm_providers[0].is_active is False

    def test_unrelated_integrity_error_is_not_mislabeled_as_name_conflict(self) -> None:
        """Regression test: an IntegrityError from an unrelated constraint (e.g.
        the provider-set unique index) must propagate as-is, not get mapped
        to the generic 'tenant name already exists' message."""
        uow = _make_uow()
        tenant = _make_tenant()
        uow.tenants.get.return_value = tenant

        unrelated_error = IntegrityError("INSERT", {}, Exception("uq_tenant_llm_provider violated"))
        uow.flush.side_effect = unrelated_error

        with pytest.raises(IntegrityError):
            TenantService(uow).update(tenant.id, address="New Address")

    def test_re_adding_a_soft_deleted_provider_creates_a_fresh_row(self) -> None:
        """Regression test: a soft-deleted row (deleted_at set, via
        TenantLLMProviderService.delete_provider) must not be treated as
        "already configured" — otherwise re-adding that provider here would
        silently no-op instead of creating a fresh row."""
        uow = _make_uow()
        tenant = _make_tenant()
        deleted_row = TenantLLMProvider(
            provider="anthropic", is_active=False, deleted_at=datetime.now(tz=UTC)
        )
        tenant.llm_providers = [deleted_row]
        uow.tenants.get.return_value = tenant

        result = TenantService(uow).update(tenant.id, providers=[LLMProvider.ANTHROPIC])

        assert deleted_row in result.llm_providers
        fresh_rows = [p for p in result.llm_providers if p is not deleted_row]
        assert len(fresh_rows) == 1
        assert fresh_rows[0].provider == "anthropic"
        assert fresh_rows[0].deleted_at is None

    def test_real_name_conflict_from_integrity_error_still_maps_correctly(self) -> None:
        uow = _make_uow()
        tenant = _make_tenant()
        uow.tenants.get.return_value = tenant

        name_conflict_error = IntegrityError(
            "INSERT", {}, Exception("duplicate key value violates ix_tenants_name")
        )
        uow.flush.side_effect = name_conflict_error

        with pytest.raises(ConflictError):
            TenantService(uow).update(tenant.id, address="New Address")


class TestReplace:
    def test_replaces_all_fields(self) -> None:
        uow = _make_uow()
        tenant = _make_tenant(name="Old Name", status="active")
        tenant.address = "Old Address"
        uow.tenants.get.return_value = tenant

        result = TenantService(uow).replace(
            tenant.id,
            name="New Name",
            contact_email=tenant.contact_email,
            address="New Address",
            status=TenantStatus.SUSPENDED,
            providers=[],
        )

        assert result.name == "New Name"
        assert result.address == "New Address"
        assert result.status == TenantStatus.SUSPENDED.value

    def test_omitted_address_clears_existing_value(self) -> None:
        """Regression test: unlike update(), replace() must clear address
        when the caller passes None — update() can't distinguish "not
        provided" from "explicitly cleared" for a scalar field, but PUT's
        full-replacement semantics require it."""
        uow = _make_uow()
        tenant = _make_tenant()
        tenant.address = "Existing Address"
        uow.tenants.get.return_value = tenant

        result = TenantService(uow).replace(
            tenant.id,
            name=tenant.name,
            contact_email=tenant.contact_email,
            address=None,
            status=TenantStatus.ACTIVE,
            providers=[],
        )

        assert result.address is None

    def test_empty_providers_clears_existing_providers(self) -> None:
        uow = _make_uow()
        tenant = _make_tenant()
        tenant.llm_providers = [TenantLLMProvider(provider="anthropic", is_active=True)]
        uow.tenants.get.return_value = tenant

        result = TenantService(uow).replace(
            tenant.id,
            name=tenant.name,
            contact_email=tenant.contact_email,
            address=None,
            status=TenantStatus.ACTIVE,
            providers=[],
        )

        assert result.llm_providers == []

    def test_raises_conflict_on_duplicate_name(self) -> None:
        uow = _make_uow()
        tenant = _make_tenant(name="Old Name")
        uow.tenants.get.return_value = tenant
        uow.tenants.get_by_name.return_value = MagicMock(id=uuid.uuid4())

        with pytest.raises(ConflictError):
            TenantService(uow).replace(
                tenant.id,
                name="Taken Name",
                contact_email=tenant.contact_email,
                address=None,
                status=TenantStatus.ACTIVE,
                providers=[],
            )

    def test_raises_conflict_on_duplicate_email(self) -> None:
        uow = _make_uow()
        tenant = _make_tenant()
        uow.tenants.get.return_value = tenant
        uow.tenants.get_by_contact_email.return_value = MagicMock(id=uuid.uuid4())

        with pytest.raises(ConflictError):
            TenantService(uow).replace(
                tenant.id,
                name=tenant.name,
                contact_email="taken@example.com",
                address=None,
                status=TenantStatus.ACTIVE,
                providers=[],
            )

    def test_raises_not_found_for_unknown_tenant(self) -> None:
        uow = _make_uow()
        uow.tenants.get.return_value = None

        with pytest.raises(NotFoundError):
            TenantService(uow).replace(
                uuid.uuid4(),
                name="Acme",
                contact_email="jane@acme.test",
                address=None,
                status=TenantStatus.ACTIVE,
                providers=[],
            )


class TestGetStats:
    def test_aggregates_counts_from_tenant_and_project_stats(self) -> None:
        uow = _make_uow()
        uow.tenant_stats = MagicMock()
        uow.tenant_stats.count_total_tenants.return_value = 10
        uow.tenant_stats.count_by_status.side_effect = lambda status: {
            "active": 6,
            "pending_invitation": 2,
            "inactive": 2,
        }[status]
        uow.tenant_stats.count_pending_client_admin_invitations.return_value = 3
        uow.project_stats = MagicMock()
        uow.project_stats.count_total_projects.return_value = 42

        result = TenantService(uow).get_stats()

        assert result.total_tenants == 10
        assert result.active_tenants == 6
        assert result.pending_invitation_tenants == 2
        assert result.pending_invitation_client_admin == 3
        assert result.deactivated_tenants == 2
        assert result.total_projects == 42
        uow.project_stats.count_total_projects.assert_called_once_with()
        uow.tenant_stats.count_pending_client_admin_invitations.assert_called_once_with()
