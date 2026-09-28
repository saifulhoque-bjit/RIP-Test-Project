"""Unit tests for TenantRepository."""

from __future__ import annotations

from unittest.mock import MagicMock
import uuid

from app.models.postgres.tenant_model import Tenant
from app.repositories.postgres.tenant_repository import TenantRepository


class TestGetByName:
    def test_returns_tenant_when_found(self) -> None:
        tenant = Tenant(id=uuid.uuid4(), name="Acme")
        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = tenant

        repo = TenantRepository(session)
        result = repo.get_by_name("Acme")

        assert result is tenant
        session.query.assert_called_once_with(Tenant)

    def test_returns_none_when_not_found(self) -> None:
        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = None

        repo = TenantRepository(session)
        result = repo.get_by_name("missing")

        assert result is None


class TestGetByCode:
    def test_returns_tenant_when_found(self) -> None:
        tenant = Tenant(id=uuid.uuid4(), name="Acme", code="ACME")
        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = tenant

        repo = TenantRepository(session)
        result = repo.get_by_code("ACME")

        assert result is tenant

    def test_returns_none_when_not_found(self) -> None:
        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = None

        repo = TenantRepository(session)

        assert repo.get_by_code("missing") is None


class TestIncrementProjectSequence:
    def test_returns_code_and_incremented_sequence(self) -> None:
        session = MagicMock()
        session.execute.return_value.first.return_value = ("ACME", 3)

        repo = TenantRepository(session)
        result = repo.increment_project_sequence(uuid.uuid4())

        assert result == ("ACME", 3)

    def test_returns_none_when_tenant_missing(self) -> None:
        session = MagicMock()
        session.execute.return_value.first.return_value = None

        repo = TenantRepository(session)

        assert repo.increment_project_sequence(uuid.uuid4()) is None
