"""Unit tests for the Postgres ProjectRepository."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock
import uuid

from app.models.postgres.project_model import Project
from app.repositories.postgres.project_repository import ProjectRepository


def _make_repo() -> tuple[ProjectRepository, MagicMock]:
    session = MagicMock()
    return ProjectRepository(session), session


class TestGetByUuid:
    def test_returns_project(self):
        repo, session = _make_repo()
        project = MagicMock(spec=Project)
        session.query.return_value.filter.return_value.first.return_value = project

        result = repo.get_by_uuid(uuid.uuid4())

        assert result is project

    def test_returns_none_when_missing(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.first.return_value = None

        assert repo.get_by_uuid(uuid.uuid4()) is None


class TestListIdsByTenant:
    def test_returns_id_list(self):
        repo, session = _make_repo()
        tenant_id = uuid.uuid4()
        ids = [uuid.uuid4(), uuid.uuid4()]
        session.execute.return_value.scalars.return_value.all.return_value = ids

        result = repo.list_ids_by_tenant(tenant_id)

        assert result == ids


class TestListIdsOwnedOrMember:
    def test_returns_id_list(self):
        repo, session = _make_repo()
        ids = [uuid.uuid4()]
        session.execute.return_value.scalars.return_value.all.return_value = ids

        result = repo.list_ids_owned_or_member(uuid.uuid4())

        assert result == ids


class TestGetByNameAndOwner:
    def test_returns_project(self):
        repo, session = _make_repo()
        project = MagicMock(spec=Project)
        session.query.return_value.filter.return_value.first.return_value = project

        result = repo.get_by_name_and_owner("Alpha", uuid.uuid4())

        assert result is project

    def test_returns_none_when_missing(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.first.return_value = None

        assert repo.get_by_name_and_owner("Missing", uuid.uuid4()) is None


class TestGetPaginated:
    def test_empty_rows_returns_empty_list_and_zero_total(self):
        repo, session = _make_repo()
        session.execute.return_value.all.return_value = []

        items, total = repo.get_paginated()

        assert items == []
        assert total == 0

    def test_returns_items_and_total_from_window_function(self):
        repo, session = _make_repo()
        project1, project2 = MagicMock(spec=Project), MagicMock(spec=Project)
        session.execute.return_value.all.return_value = [(project1, 7), (project2, 7)]

        items, total = repo.get_paginated(skip=0, limit=20)

        assert items == [project1, project2]
        assert total == 7

    def test_owner_and_member_filter_combination(self):
        repo, session = _make_repo()
        session.execute.return_value.all.return_value = []

        repo.get_paginated(owner_id=uuid.uuid4(), member_user_id=uuid.uuid4())

        session.execute.assert_called_once()

    def test_tenant_and_search_filters(self):
        repo, session = _make_repo()
        session.execute.return_value.all.return_value = []

        repo.get_paginated(tenant_id=uuid.uuid4(), search="alpha")

        session.execute.assert_called_once()


class TestListForScope:
    def test_returns_items_without_windowing(self):
        repo, session = _make_repo()
        project1, project2 = MagicMock(spec=Project), MagicMock(spec=Project)
        session.execute.return_value.scalars.return_value.all.return_value = [project1, project2]

        result = repo.list_for_scope(tenant_id=uuid.uuid4())

        assert result == [project1, project2]

    def test_empty_result(self):
        repo, session = _make_repo()
        session.execute.return_value.scalars.return_value.all.return_value = []

        assert repo.list_for_scope() == []

    def test_owner_and_member_filter_combination(self):
        repo, session = _make_repo()
        session.execute.return_value.scalars.return_value.all.return_value = []

        repo.list_for_scope(owner_id=uuid.uuid4(), member_user_id=uuid.uuid4())

        session.execute.assert_called_once()

    def test_tenant_and_search_filters(self):
        repo, session = _make_repo()
        session.execute.return_value.scalars.return_value.all.return_value = []

        repo.list_for_scope(tenant_id=uuid.uuid4(), search="alpha")

        session.execute.assert_called_once()


class TestGetProjectsByUserIds:
    def test_empty_user_ids_returns_empty_dict_values(self):
        repo, session = _make_repo()

        result = repo.get_projects_by_user_ids([])

        assert result == {}

    def test_owned_and_member_projects_merged_per_user(self):
        repo, session = _make_repo()
        user_id = uuid.uuid4()
        project_id = uuid.uuid4()

        owned_project = MagicMock(spec=Project, id=project_id, owner_id=user_id)
        session.query.return_value.filter.return_value.all.return_value = [owned_project]

        member_project = MagicMock(spec=Project, id=uuid.uuid4())
        session.query.return_value.join.return_value.filter.return_value.all.return_value = [
            (user_id, "member", member_project)
        ]

        result = repo.get_projects_by_user_ids([user_id])

        entries = result[user_id]
        assert len(entries) == 2
        owned_entry = next(e for e in entries if e[0] is owned_project)
        assert owned_entry[1] is True
        member_entry = next(e for e in entries if e[0] is member_project)
        assert member_entry[1] is False
        assert member_entry[2] == ["member"]

    def test_owner_also_a_member_keeps_both_facts(self):
        repo, session = _make_repo()
        user_id = uuid.uuid4()
        project = MagicMock(spec=Project, id=uuid.uuid4(), owner_id=user_id)

        session.query.return_value.filter.return_value.all.return_value = [project]
        session.query.return_value.join.return_value.filter.return_value.all.return_value = [
            (user_id, "admin", project)
        ]

        result = repo.get_projects_by_user_ids([user_id])

        entry = result[user_id][0]
        assert entry[0] is project
        assert entry[1] is True
        assert entry[2] == ["admin"]


class TestCreateAndUpdate:
    def test_create_flushes_and_refreshes(self):
        repo, session = _make_repo()
        project = MagicMock(spec=Project)

        result = repo.create(project)

        session.add.assert_called_once_with(project)
        session.flush.assert_called_once()
        session.refresh.assert_called_once_with(project)
        assert result is project

    def test_update_flushes_and_refreshes(self):
        repo, session = _make_repo()
        project = MagicMock(spec=Project)

        result = repo.update(project)

        session.flush.assert_called_once()
        session.refresh.assert_called_once_with(project)
        assert result is project


class TestUpdateFields:
    def test_patches_fields_on_existing_project(self):
        repo, session = _make_repo()
        project = MagicMock(spec=Project)
        session.query.return_value.filter.return_value.first.return_value = project
        ts = datetime.now(tz=UTC)

        result = repo.update_fields(uuid.uuid4(), last_activity_at=ts)

        assert result is project
        assert project.last_activity_at == ts

    def test_returns_none_when_project_missing(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.first.return_value = None

        result = repo.update_fields(uuid.uuid4(), last_activity_at=datetime.now(tz=UTC))

        assert result is None


class TestNextUntenantedCodeSequence:
    def test_returns_next_sequence_value(self):
        repo, session = _make_repo()
        session.execute.return_value.scalar_one.return_value = 7

        result = repo.next_untenanted_code_sequence()

        assert result == 7
        session.execute.assert_called_once()
