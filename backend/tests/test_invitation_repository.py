"""Unit tests for InvitationRepository."""

from __future__ import annotations

from unittest.mock import MagicMock
import uuid

from app.models.postgres.invitation_model import Invitation
from app.repositories.postgres.invitation_repository import InvitationRepository


def _make_repo() -> tuple[InvitationRepository, MagicMock]:
    session = MagicMock()
    return InvitationRepository(session), session


class TestGetByTokenHash:
    def test_returns_invitation(self):
        repo, session = _make_repo()
        invitation = MagicMock(spec=Invitation)
        session.query.return_value.filter.return_value.first.return_value = invitation

        assert repo.get_by_token_hash("hash-1") is invitation

    def test_returns_none_when_missing(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.first.return_value = None

        assert repo.get_by_token_hash("missing") is None


class TestGetPendingByTenantAndEmail:
    def test_returns_invitation(self):
        repo, session = _make_repo()
        invitation = MagicMock(spec=Invitation)
        session.query.return_value.filter.return_value.first.return_value = invitation

        result = repo.get_pending_by_tenant_and_email(uuid.uuid4(), "a@example.com")

        assert result is invitation


class TestGetByIdAndTenant:
    def test_returns_invitation(self):
        repo, session = _make_repo()
        invitation = MagicMock(spec=Invitation)
        session.query.return_value.filter.return_value.first.return_value = invitation

        result = repo.get_by_id_and_tenant(uuid.uuid4(), uuid.uuid4())

        assert result is invitation


class TestCountByRoleId:
    def test_returns_count(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.count.return_value = 4

        assert repo.count_by_role_id(uuid.uuid4()) == 4


class TestListByTenant:
    def test_returns_all_statuses_when_status_not_given(self):
        repo, session = _make_repo()
        query = session.query.return_value.filter.return_value
        query.count.return_value = 2
        items = [MagicMock(spec=Invitation), MagicMock(spec=Invitation)]
        query.order_by.return_value.offset.return_value.limit.return_value.all.return_value = items

        result_items, total = repo.list_by_tenant(uuid.uuid4())

        assert result_items == items
        assert total == 2

    def test_filters_by_status_when_given(self):
        repo, session = _make_repo()
        base_query = session.query.return_value.filter.return_value
        status_query = base_query.filter.return_value
        status_query.count.return_value = 1
        status_query.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []

        repo.list_by_tenant(uuid.uuid4(), status="pending")

        base_query.filter.assert_called_once()
