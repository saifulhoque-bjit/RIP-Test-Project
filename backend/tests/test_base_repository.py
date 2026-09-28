"""Unit tests for the generic Postgres BaseRepository."""

from __future__ import annotations

from unittest.mock import MagicMock
import uuid

from app.models.postgres.source_model import Source
from app.repositories.postgres.base_repository import BaseRepository

# `Source` is a real mapped model with a `project_id` column — needed because
# `delete_by_project_id` builds a real SQLAlchemy `delete()` construct that
# requires an actual mapped class, not a plain Python stand-in.
_FakeModel = Source


def _make_repo() -> tuple[BaseRepository, MagicMock]:
    session = MagicMock()
    return BaseRepository(session, _FakeModel), session


class TestGet:
    def test_returns_record(self):
        repo, session = _make_repo()
        record = MagicMock()
        session.get.return_value = record

        assert repo.get("id-1") is record
        session.get.assert_called_once_with(_FakeModel, "id-1")


class TestGetAll:
    def test_returns_all_records(self):
        repo, session = _make_repo()
        records = [MagicMock(), MagicMock()]
        session.query.return_value.all.return_value = records

        assert repo.get_all() == records


class TestGetPaginated:
    def test_returns_items_and_total(self):
        repo, session = _make_repo()
        query = session.query.return_value
        query.count.return_value = 5
        query.offset.return_value.limit.return_value.all.return_value = ["a", "b"]

        items, total = repo.get_paginated(skip=1, limit=2)

        assert items == ["a", "b"]
        assert total == 5
        query.offset.assert_called_once_with(1)
        query.offset.return_value.limit.assert_called_once_with(2)


class TestAdd:
    def test_stages_entity(self):
        repo, session = _make_repo()
        entity = MagicMock()

        result = repo.add(entity)

        session.add.assert_called_once_with(entity)
        assert result is entity


class TestDelete:
    def test_marks_entity_for_deletion(self):
        repo, session = _make_repo()
        entity = MagicMock()

        repo.delete(entity)

        session.delete.assert_called_once_with(entity)


class TestDeleteByProjectId:
    def test_returns_rowcount(self):
        repo, session = _make_repo()
        session.execute.return_value.rowcount = 4

        result = repo.delete_by_project_id(uuid.uuid4())

        assert result == 4
        session.execute.assert_called_once()
