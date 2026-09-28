"""Unit tests for ProjectRepositoryAsync."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
import uuid

from app.models.postgres.project_model import Project
from app.repositories.postgres.project_repository_async import ProjectRepositoryAsync


def _make_repo() -> tuple[ProjectRepositoryAsync, AsyncMock]:
    session = AsyncMock()
    return ProjectRepositoryAsync(session), session


class TestGetByUuid:
    async def test_returns_project(self):
        repo, session = _make_repo()
        project = MagicMock(spec=Project)
        session.scalar.return_value = project

        result = await repo.get_by_uuid(uuid.uuid4())

        assert result is project

    async def test_returns_none_when_missing(self):
        repo, session = _make_repo()
        session.scalar.return_value = None

        assert await repo.get_by_uuid(uuid.uuid4()) is None


class TestGetByNameAndOwner:
    async def test_returns_project(self):
        repo, session = _make_repo()
        project = MagicMock(spec=Project)
        session.scalar.return_value = project

        result = await repo.get_by_name_and_owner("Alpha", uuid.uuid4())

        assert result is project


class TestGetPaginated:
    async def test_empty_rows_returns_empty_list_and_zero_total(self):
        repo, session = _make_repo()
        result = MagicMock()
        result.all.return_value = []
        session.execute.return_value = result

        items, total = await repo.get_paginated()

        assert items == []
        assert total == 0

    async def test_returns_items_and_total(self):
        repo, session = _make_repo()
        project = MagicMock(spec=Project)
        result = MagicMock()
        result.all.return_value = [(project, 3)]
        session.execute.return_value = result

        items, total = await repo.get_paginated(owner_id=uuid.uuid4(), search="alpha")

        assert items == [project]
        assert total == 3


class TestCreateAndUpdate:
    async def test_create_flushes_and_refreshes(self):
        repo, session = _make_repo()
        project = MagicMock(spec=Project)

        result = await repo.create(project)

        session.add.assert_called_once_with(project)
        session.flush.assert_awaited_once()
        session.refresh.assert_awaited_once_with(project)
        assert result is project

    async def test_update_flushes_and_refreshes(self):
        repo, session = _make_repo()
        project = MagicMock(spec=Project)

        result = await repo.update(project)

        session.flush.assert_awaited_once()
        session.refresh.assert_awaited_once_with(project)
        assert result is project
