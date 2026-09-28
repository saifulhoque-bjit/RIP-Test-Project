"""Unit tests for the Postgres UserRepository."""

from __future__ import annotations

from unittest.mock import MagicMock
import uuid

from app.models.postgres.user_model import User
from app.repositories.postgres.user_repository import UserRepository


def _make_repo() -> tuple[UserRepository, MagicMock]:
    session = MagicMock()
    return UserRepository(session), session


class TestGetByCognitoSub:
    def test_returns_user(self):
        repo, session = _make_repo()
        user = MagicMock(spec=User)
        session.query.return_value.filter.return_value.first.return_value = user

        assert repo.get_by_cognito_sub("sub-1") is user

    def test_returns_none_when_missing(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.first.return_value = None

        assert repo.get_by_cognito_sub("missing") is None


class TestGetByEmail:
    def test_returns_user(self):
        repo, session = _make_repo()
        user = MagicMock(spec=User)
        session.query.return_value.filter.return_value.first.return_value = user

        assert repo.get_by_email("a@example.com") is user

    def test_returns_none_when_missing(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.first.return_value = None

        assert repo.get_by_email("missing@example.com") is None


class TestGetByIds:
    def test_returns_matching_users(self):
        repo, session = _make_repo()
        users = [MagicMock(spec=User), MagicMock(spec=User)]
        session.query.return_value.filter.return_value.all.return_value = users
        ids = [uuid.uuid4(), uuid.uuid4()]

        assert repo.get_by_ids(ids) == users

    def test_returns_empty_list_without_querying_for_no_ids(self):
        repo, session = _make_repo()

        assert repo.get_by_ids([]) == []
        session.query.assert_not_called()


class TestGetPaginated:
    def test_no_tenant_filter(self):
        repo, session = _make_repo()
        query = session.query.return_value.filter.return_value
        query.count.return_value = 2
        items = [MagicMock(spec=User), MagicMock(spec=User)]
        query.offset.return_value.limit.return_value.all.return_value = items

        result_items, total = repo.get_paginated()

        assert result_items == items
        assert total == 2

    def test_filters_by_tenant_when_given(self):
        repo, session = _make_repo()
        base_query = session.query.return_value.filter.return_value
        tenant_query = base_query.filter.return_value
        tenant_query.count.return_value = 1
        tenant_query.offset.return_value.limit.return_value.all.return_value = []

        repo.get_paginated(tenant_id=uuid.uuid4())

        base_query.filter.assert_called_once()
