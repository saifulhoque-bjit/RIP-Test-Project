"""Unit tests for TapSyncMappingRepository."""

from __future__ import annotations

from unittest.mock import MagicMock
import uuid

from app.models.postgres.tap_sync_mapping_model import TapSyncMapping
from app.repositories.postgres.tap_sync_mapping_repository import TapSyncMappingRepository


def _make_repo() -> tuple[TapSyncMappingRepository, MagicMock]:
    session = MagicMock()
    return TapSyncMappingRepository(session), session


class TestDeleteByRipEntityIds:
    def test_empty_ids_short_circuits(self):
        repo, session = _make_repo()

        assert repo.delete_by_rip_entity_ids([]) == 0
        session.execute.assert_not_called()

    def test_returns_rowcount(self):
        repo, session = _make_repo()
        session.execute.return_value.rowcount = 2

        assert repo.delete_by_rip_entity_ids([uuid.uuid4()]) == 2


class TestGetByRipEntity:
    def test_returns_mapping(self):
        repo, session = _make_repo()
        mapping = MagicMock(spec=TapSyncMapping)
        session.query.return_value.filter.return_value.first.return_value = mapping

        result = repo.get_by_rip_entity("module", uuid.uuid4())

        assert result is mapping

    def test_returns_none_when_missing(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.first.return_value = None

        assert repo.get_by_rip_entity("module", uuid.uuid4()) is None


class TestListByPushSyncId:
    def test_returns_rows(self):
        repo, session = _make_repo()
        rows = [MagicMock(spec=TapSyncMapping)]
        session.query.return_value.filter.return_value.all.return_value = rows

        assert repo.list_by_push_sync_id(uuid.uuid4()) == rows
