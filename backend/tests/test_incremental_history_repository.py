"""Unit tests for IncrementalHistoryRepository."""

from __future__ import annotations

from unittest.mock import MagicMock
import uuid

from app.models.postgres.incremental_history_model import IncrementalHistory
from app.repositories.postgres.incremental_history_repository import IncrementalHistoryRepository


def _make_repo() -> tuple[IncrementalHistoryRepository, MagicMock]:
    session = MagicMock()
    return IncrementalHistoryRepository(session), session


class TestCreate:
    def test_adds_flushes_and_refreshes(self):
        repo, session = _make_repo()
        project_id = uuid.uuid4()

        result = repo.create(project_id=project_id, updates_json=[{"a": 1}])

        session.add.assert_called_once()
        session.flush.assert_called_once()
        session.refresh.assert_called_once_with(result)
        assert result.project_id == project_id
        assert result.updates_json == [{"a": 1}]


class TestListByProject:
    def test_returns_ordered_limited_rows(self):
        repo, session = _make_repo()
        rows = [MagicMock(spec=IncrementalHistory)]
        chain = session.query.return_value.filter.return_value.order_by.return_value
        chain.limit.return_value.all.return_value = rows

        result = repo.list_by_project(uuid.uuid4(), limit=10)

        assert result == rows
        chain.limit.assert_called_once_with(10)


class TestGetById:
    def test_returns_record(self):
        repo, session = _make_repo()
        record = MagicMock(spec=IncrementalHistory)
        session.query.return_value.filter.return_value.first.return_value = record

        assert repo.get_by_id(uuid.uuid4()) is record

    def test_returns_none_when_missing(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.first.return_value = None

        assert repo.get_by_id(uuid.uuid4()) is None
