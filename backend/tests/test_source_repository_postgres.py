"""Unit tests for the Postgres SourceRepository."""

from __future__ import annotations

from unittest.mock import MagicMock
import uuid

from app.models.postgres.source_model import Source
from app.repositories.postgres.source_repository import SourceRepository


def _make_repo() -> tuple[SourceRepository, MagicMock]:
    session = MagicMock()
    return SourceRepository(session), session


class TestGetByUuid:
    def test_returns_source(self):
        repo, session = _make_repo()
        source = MagicMock(spec=Source)
        session.query.return_value.options.return_value.filter.return_value.first.return_value = (
            source
        )

        assert repo.get_by_uuid(uuid.uuid4()) is source

    def test_returns_none_when_missing(self):
        repo, session = _make_repo()
        session.query.return_value.options.return_value.filter.return_value.first.return_value = (
            None
        )

        assert repo.get_by_uuid(uuid.uuid4()) is None


class TestGetByChecksum:
    def test_returns_source(self):
        repo, session = _make_repo()
        source = MagicMock(spec=Source)
        chain = session.query.return_value.outerjoin.return_value.filter.return_value
        chain.first.return_value = source

        result = repo.get_by_checksum("abc123", uuid.uuid4())

        assert result is source

    def test_returns_none_when_no_match(self):
        repo, session = _make_repo()
        chain = session.query.return_value.outerjoin.return_value.filter.return_value
        chain.first.return_value = None

        assert repo.get_by_checksum("abc123", uuid.uuid4()) is None


class TestGetByFilename:
    def test_returns_source(self):
        repo, session = _make_repo()
        source = MagicMock(spec=Source)
        chain = session.query.return_value.outerjoin.return_value.filter.return_value
        chain.first.return_value = source

        result = repo.get_by_filename("requirements.pdf", uuid.uuid4())

        assert result is source

    def test_returns_none_when_no_match(self):
        repo, session = _make_repo()
        chain = session.query.return_value.outerjoin.return_value.filter.return_value
        chain.first.return_value = None

        assert repo.get_by_filename("requirements.pdf", uuid.uuid4()) is None


class TestGetPaginated:
    def test_no_filters(self):
        repo, session = _make_repo()
        query = session.query.return_value.filter.return_value
        query.count.return_value = 2
        items = [MagicMock(spec=Source), MagicMock(spec=Source)]
        query.options.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = items

        result_items, total = repo.get_paginated(uuid.uuid4())

        assert result_items == items
        assert total == 2

    def test_applies_status_file_type_and_upload_type_filters(self):
        repo, session = _make_repo()
        base_query = session.query.return_value.filter.return_value
        filtered = base_query.filter.return_value.filter.return_value.filter.return_value
        filtered.count.return_value = 0
        filtered.options.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []

        repo.get_paginated(uuid.uuid4(), status="uploaded", file_type="pdf", upload_type="single")

        assert base_query.filter.called


class TestGetManyByUuids:
    def test_returns_matching_sources(self):
        repo, session = _make_repo()
        sources = [MagicMock(spec=Source)]
        session.query.return_value.options.return_value.filter.return_value.all.return_value = (
            sources
        )

        result = repo.get_many_by_uuids([uuid.uuid4()])

        assert result == sources


class TestGetByIngestionIds:
    def test_empty_ids_short_circuits(self):
        repo, session = _make_repo()

        result = repo.get_by_ingestion_ids([])

        assert result == []
        session.query.assert_not_called()

    def test_returns_matching_sources(self):
        repo, session = _make_repo()
        sources = [MagicMock(spec=Source)]
        chain = session.query.return_value.options.return_value.filter.return_value
        chain.order_by.return_value.all.return_value = sources

        result = repo.get_by_ingestion_ids([uuid.uuid4()])

        assert result == sources


class TestGetIdsByProject:
    def test_returns_id_list(self):
        repo, session = _make_repo()
        ids = [uuid.uuid4(), uuid.uuid4()]
        rows = [MagicMock(id=i) for i in ids]
        session.query.return_value.filter.return_value.all.return_value = rows

        result = repo.get_ids_by_project(uuid.uuid4())

        assert result == ids


class TestExistsByProjectAndSourceType:
    def test_returns_true_when_match_exists(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.first.return_value = MagicMock(spec=Source)

        assert repo.exists_by_project_and_source_type(uuid.uuid4(), "rfp") is True

    def test_returns_false_when_no_match(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.first.return_value = None

        assert repo.exists_by_project_and_source_type(uuid.uuid4(), "rfp") is False


class TestDeleteByProjectId:
    def test_returns_rowcount(self):
        repo, session = _make_repo()
        session.execute.return_value.rowcount = 3

        assert repo.delete_by_project_id(uuid.uuid4()) == 3


class TestGetStorageKeysByProject:
    def test_returns_keys(self):
        repo, session = _make_repo()
        rows = [MagicMock(storage_key="key1"), MagicMock(storage_key="key2")]
        session.query.return_value.filter.return_value.all.return_value = rows

        result = repo.get_storage_keys_by_project(uuid.uuid4())

        assert result == ["key1", "key2"]


class TestCreate:
    def test_flushes_and_refreshes(self):
        repo, session = _make_repo()
        source = MagicMock(spec=Source)

        result = repo.create(source)

        session.add.assert_called_once_with(source)
        session.flush.assert_called_once()
        session.refresh.assert_called_once_with(source)
        assert result is source


class TestGetSourceCountsByProject:
    def test_empty_project_ids_returns_empty_dict(self):
        repo, session = _make_repo()

        assert repo.get_source_counts_by_project([]) == {}

    def test_fills_zero_for_projects_with_no_active_sources(self):
        repo, session = _make_repo()
        p1, p2 = uuid.uuid4(), uuid.uuid4()
        session.query.return_value.filter.return_value.group_by.return_value.all.return_value = [
            (p1, 3)
        ]

        result = repo.get_source_counts_by_project([p1, p2])

        assert result == {p1: 3, p2: 0}
