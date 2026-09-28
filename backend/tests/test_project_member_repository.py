"""Unit tests for the Postgres ProjectMemberRepository."""

from __future__ import annotations

from unittest.mock import MagicMock
import uuid

from app.models.postgres.project_member_model import ProjectMember
from app.repositories.postgres.project_member_repository import ProjectMemberRepository


def _make_repo() -> tuple[ProjectMemberRepository, MagicMock]:
    session = MagicMock()
    return ProjectMemberRepository(session), session


def _make_member(role: str, member_id=None) -> MagicMock:
    return MagicMock(spec=ProjectMember, role=role, id=member_id or uuid.uuid4())


class TestListRolesForUser:
    def test_returns_matching_rows(self):
        repo, session = _make_repo()
        rows = [_make_member("admin")]
        session.query.return_value.filter.return_value.all.return_value = rows

        result = repo.list_roles_for_user(uuid.uuid4(), uuid.uuid4())

        assert result == rows

    def test_returns_empty_list_when_none(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.all.return_value = []

        assert repo.list_roles_for_user(uuid.uuid4(), uuid.uuid4()) == []


class TestListProjectTenantsForUser:
    def test_returns_project_tenant_pairs(self):
        repo, session = _make_repo()
        project_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        rows = [(project_id, tenant_id)]
        chain = session.query.return_value.join.return_value.filter.return_value.distinct
        chain.return_value.all.return_value = rows

        result = repo.list_project_tenants_for_user(uuid.uuid4())

        assert result == rows

    def test_returns_empty_list_when_no_assignments(self):
        repo, session = _make_repo()
        chain = session.query.return_value.join.return_value.filter.return_value.distinct
        chain.return_value.all.return_value = []

        assert repo.list_project_tenants_for_user(uuid.uuid4()) == []


class TestListByProject:
    def test_returns_ordered_rows(self):
        repo, session = _make_repo()
        rows = [_make_member("member"), _make_member("admin")]
        session.query.return_value.filter.return_value.order_by.return_value.all.return_value = rows

        result = repo.list_by_project(uuid.uuid4())

        assert result == rows


class TestReplaceRoles:
    def test_revokes_role_not_in_new_set(self):
        repo, session = _make_repo()
        stale = _make_member("member")
        session.query.return_value.filter.return_value.all.return_value = [stale]

        repo.replace_roles(uuid.uuid4(), uuid.uuid4(), ["admin"], assigned_by=None)

        session.delete.assert_called_once_with(stale)

    def test_keeps_existing_role_untouched(self):
        repo, session = _make_repo()
        existing = _make_member("admin")
        session.query.return_value.filter.return_value.all.return_value = [existing]

        result = repo.replace_roles(uuid.uuid4(), uuid.uuid4(), ["admin"], assigned_by=None)

        session.add.assert_not_called()
        session.delete.assert_not_called()
        assert result == [existing]

    def test_creates_new_role(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.all.return_value = []
        project_id, user_id, assigned_by = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

        result = repo.replace_roles(project_id, user_id, ["member"], assigned_by)

        session.add.assert_called_once()
        added_member = session.add.call_args[0][0]
        assert added_member.project_id == project_id
        assert added_member.user_id == user_id
        assert added_member.role == "member"
        assert added_member.assigned_by == assigned_by
        assert result == [added_member]

    def test_deduplicates_roles_preserving_order(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.all.return_value = []

        result = repo.replace_roles(uuid.uuid4(), uuid.uuid4(), ["admin", "admin", "member"], None)

        assert len(result) == 2
        assert session.add.call_count == 2


class TestDeleteByProjectAndUser:
    def test_returns_false_when_no_rows(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.all.return_value = []

        assert repo.delete_by_project_and_user(uuid.uuid4(), uuid.uuid4()) is False

    def test_deletes_all_matching_rows(self):
        repo, session = _make_repo()
        members = [_make_member("admin"), _make_member("member")]
        session.query.return_value.filter.return_value.all.return_value = members

        result = repo.delete_by_project_and_user(uuid.uuid4(), uuid.uuid4())

        assert result is True
        assert session.delete.call_count == 2
