"""Unit tests for ProjectStatsRepository."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock
import uuid

from app.repositories.postgres.project_stats_repository import ProjectStatsRepository


def _make_repo() -> tuple[ProjectStatsRepository, MagicMock]:
    session = MagicMock()
    return ProjectStatsRepository(session), session


class TestCountActiveProjects:
    def test_unscoped(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.scalar.return_value = 5

        assert repo.count_active_projects() == 5

    def test_scoped_to_project_ids(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.filter.return_value.scalar.return_value = 2

        assert repo.count_active_projects(project_ids=[uuid.uuid4()]) == 2

    def test_none_scalar_defaults_to_zero(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.scalar.return_value = None

        assert repo.count_active_projects() == 0


class TestCountTotalProjects:
    def test_unscoped(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.scalar.return_value = 9

        assert repo.count_total_projects() == 9

    def test_scoped_to_project_ids(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.filter.return_value.scalar.return_value = 3

        assert repo.count_total_projects(project_ids=[uuid.uuid4()]) == 3


class TestCountProjectsByTenantIds:
    def test_empty_input_returns_empty_dict_without_querying(self):
        repo, session = _make_repo()

        assert repo.count_projects_by_tenant_ids([]) == {}
        session.query.assert_not_called()

    def test_returns_dict_keyed_by_tenant_id(self):
        repo, session = _make_repo()
        tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
        session.query.return_value.filter.return_value.group_by.return_value.all.return_value = [
            (tenant_a, 3),
            (tenant_b, 0),
        ]

        result = repo.count_projects_by_tenant_ids([tenant_a, tenant_b])

        assert result == {tenant_a: 3, tenant_b: 0}

    def test_tenant_with_no_projects_is_absent_from_result(self):
        repo, session = _make_repo()
        tenant_id = uuid.uuid4()
        session.query.return_value.filter.return_value.group_by.return_value.all.return_value = []

        assert repo.count_projects_by_tenant_ids([tenant_id]) == {}


class TestCountActiveProjectsSince:
    def test_returns_count(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.scalar.return_value = 4

        result = repo.count_active_projects_since(datetime.now(UTC))

        assert result == 4

    def test_scoped_to_project_ids(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.filter.return_value.scalar.return_value = 1

        result = repo.count_active_projects_since(datetime.now(UTC), project_ids=[uuid.uuid4()])

        assert result == 1


class TestCountRunningPipelines:
    def test_unscoped(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.scalar.return_value = 7

        assert repo.count_running_pipelines() == 7

    def test_scoped_to_project_ids(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.filter.return_value.scalar.return_value = 2

        assert repo.count_running_pipelines(project_ids=[uuid.uuid4()]) == 2


class TestGetPipelineCompletionStats:
    def test_no_completed_tasks_returns_all_none(self):
        repo, session = _make_repo()
        avg_query = session.query.return_value.filter.return_value
        avg_query.scalar.return_value = None
        longest_query = session.query.return_value.join.return_value.filter.return_value
        longest_query.order_by.return_value.first.return_value = None

        result = repo.get_pipeline_completion_stats()

        assert result == {
            "avg_seconds": None,
            "longest_project_id": None,
            "longest_project_name": None,
            "longest_duration_seconds": None,
        }

    def test_returns_avg_and_longest(self):
        repo, session = _make_repo()
        avg_query = session.query.return_value.filter.return_value
        avg_query.scalar.return_value = 120.5

        longest_row = MagicMock(
            project_id=uuid.uuid4(), project_name="Alpha", duration_seconds=999.0
        )
        longest_query = session.query.return_value.join.return_value.filter.return_value
        longest_query.order_by.return_value.first.return_value = longest_row

        result = repo.get_pipeline_completion_stats()

        assert result["avg_seconds"] == 120.5
        assert result["longest_project_id"] == longest_row.project_id
        assert result["longest_project_name"] == "Alpha"
        assert result["longest_duration_seconds"] == 999.0

    def test_scoped_to_project_ids(self):
        repo, session = _make_repo()
        avg_query = session.query.return_value.filter.return_value
        avg_query.filter.return_value.scalar.return_value = 50.0
        longest_query = session.query.return_value.join.return_value.filter.return_value
        longest_query.filter.return_value.order_by.return_value.first.return_value = None

        result = repo.get_pipeline_completion_stats(project_ids=[uuid.uuid4()])

        assert result["avg_seconds"] == 50.0
