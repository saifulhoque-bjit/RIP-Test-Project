"""Unit tests for JiraSyncHistoryRepository."""

from __future__ import annotations

from unittest.mock import MagicMock
import uuid

from app.models.postgres.jira_sync_history_model import JiraSyncHistory
from app.repositories.postgres.jira_sync_history_repository import JiraSyncHistoryRepository


def _make_repo() -> tuple[JiraSyncHistoryRepository, MagicMock]:
    session = MagicMock()
    return JiraSyncHistoryRepository(session), session


class TestListByProject:
    def test_returns_items_and_total(self):
        repo, session = _make_repo()
        query = session.query.return_value.filter.return_value.order_by.return_value
        query.count.return_value = 2
        items = [MagicMock(spec=JiraSyncHistory)]
        query.offset.return_value.limit.return_value.all.return_value = items

        result_items, total = repo.list_by_project(uuid.uuid4(), skip=0, limit=20)

        assert result_items == items
        assert total == 2


class TestGetLatestByProject:
    def test_returns_latest_row(self):
        repo, session = _make_repo()
        row = MagicMock(spec=JiraSyncHistory)
        session.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
            row
        )

        assert repo.get_latest_by_project(uuid.uuid4()) is row

    def test_returns_none_when_missing(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
            None
        )

        assert repo.get_latest_by_project(uuid.uuid4()) is None
