"""Unit tests for FragmentEmbeddingRepository."""

from __future__ import annotations

from unittest.mock import MagicMock
import uuid

from app.repositories.postgres.fragment_embedding_repository import FragmentEmbeddingRepository


def _make_repo() -> tuple[FragmentEmbeddingRepository, MagicMock]:
    session = MagicMock()
    return FragmentEmbeddingRepository(session), session


class TestBulkUpsert:
    def test_empty_records_short_circuits(self):
        repo, session = _make_repo()

        result = repo.bulk_upsert(uuid.uuid4(), uuid.uuid4(), [])

        assert result == 0
        session.execute.assert_not_called()

    def test_upserts_and_returns_row_count(self):
        repo, session = _make_repo()

        result = repo.bulk_upsert(
            uuid.uuid4(), uuid.uuid4(), [("frag-1", [0.1, 0.2]), ("frag-2", [0.3, 0.4])]
        )

        assert result == 2
        session.execute.assert_called_once()


class TestDeleteBySourceId:
    def test_returns_rowcount(self):
        repo, session = _make_repo()
        session.execute.return_value.rowcount = 3

        assert repo.delete_by_source_id(uuid.uuid4()) == 3


class TestDeleteByProjectId:
    def test_returns_rowcount(self):
        repo, session = _make_repo()
        session.execute.return_value.rowcount = 5

        assert repo.delete_by_project_id(uuid.uuid4()) == 5
