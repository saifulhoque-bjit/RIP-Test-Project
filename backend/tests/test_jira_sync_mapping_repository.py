"""Unit tests for JiraSyncMappingRepository."""

from __future__ import annotations

from unittest.mock import MagicMock
import uuid

from app.models.postgres.jira_sync_mapping_model import JiraSyncMapping
from app.repositories.postgres.jira_sync_mapping_repository import JiraSyncMappingRepository


def _make_repo() -> tuple[JiraSyncMappingRepository, MagicMock]:
    session = MagicMock()
    return JiraSyncMappingRepository(session), session


class TestListByIntegration:
    def test_returns_rows(self):
        repo, session = _make_repo()
        rows = [MagicMock(spec=JiraSyncMapping)]
        session.query.return_value.filter.return_value.all.return_value = rows

        assert repo.list_by_integration(uuid.uuid4()) == rows


class TestGetByRipEntity:
    def test_returns_mapping(self):
        repo, session = _make_repo()
        mapping = MagicMock(spec=JiraSyncMapping)
        session.query.return_value.filter.return_value.first.return_value = mapping

        result = repo.get_by_rip_entity(uuid.uuid4(), "module", uuid.uuid4())

        assert result is mapping

    def test_returns_none_when_missing(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.first.return_value = None

        assert repo.get_by_rip_entity(uuid.uuid4(), "module", uuid.uuid4()) is None


class TestListSyncedEntityIds:
    def test_returns_set_of_ids(self):
        repo, session = _make_repo()
        id1, id2 = uuid.uuid4(), uuid.uuid4()
        session.query.return_value.filter.return_value.all.return_value = [(id1,), (id2,)]

        result = repo.list_synced_entity_ids(uuid.uuid4())

        assert result == {id1, id2}


class TestMarkDeprecated:
    def test_empty_entity_ids_short_circuits(self):
        repo, session = _make_repo()

        assert repo.mark_deprecated(uuid.uuid4(), []) == 0
        session.query.assert_not_called()

    def test_returns_updated_count(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.update.return_value = 3

        result = repo.mark_deprecated(uuid.uuid4(), [uuid.uuid4()])

        assert result == 3
