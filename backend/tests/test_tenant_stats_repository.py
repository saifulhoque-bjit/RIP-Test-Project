"""Unit tests for TenantStatsRepository."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.repositories.postgres.tenant_stats_repository import TenantStatsRepository


def _make_repo() -> tuple[TenantStatsRepository, MagicMock]:
    session = MagicMock()
    return TenantStatsRepository(session), session


class TestCountTotalTenants:
    def test_returns_count(self):
        repo, session = _make_repo()
        session.query.return_value.scalar.return_value = 5

        assert repo.count_total_tenants() == 5

    def test_none_scalar_defaults_to_zero(self):
        repo, session = _make_repo()
        session.query.return_value.scalar.return_value = None

        assert repo.count_total_tenants() == 0


class TestCountByStatus:
    def test_returns_count_for_status(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.scalar.return_value = 3

        assert repo.count_by_status("active") == 3

    def test_none_scalar_defaults_to_zero(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.scalar.return_value = None

        assert repo.count_by_status("inactive") == 0


class TestCountPendingClientAdminInvitations:
    def test_returns_count(self):
        repo, session = _make_repo()
        session.query.return_value.join.return_value.filter.return_value.scalar.return_value = 4

        assert repo.count_pending_client_admin_invitations() == 4

    def test_none_scalar_defaults_to_zero(self):
        repo, session = _make_repo()
        session.query.return_value.join.return_value.filter.return_value.scalar.return_value = None

        assert repo.count_pending_client_admin_invitations() == 0
