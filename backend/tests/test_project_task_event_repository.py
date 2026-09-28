"""Unit tests for ProjectTaskEventRepository."""

from __future__ import annotations

from unittest.mock import MagicMock
import uuid

from app.models.postgres.project_task_event_model import ProjectTaskEvent
from app.repositories.postgres.project_task_event_repository import ProjectTaskEventRepository


def _make_repo() -> tuple[ProjectTaskEventRepository, MagicMock]:
    session = MagicMock()
    return ProjectTaskEventRepository(session), session


class TestRecord:
    def test_adds_and_flushes(self):
        repo, session = _make_repo()
        task_id, project_id = uuid.uuid4(), uuid.uuid4()

        result = repo.record(
            task_id=task_id,
            project_id=project_id,
            task_type="source_process",
            status="running",
            progress=50,
        )

        session.add.assert_called_once()
        session.flush.assert_called_once()
        assert result.task_id == task_id
        assert result.status == "running"


class TestListByTask:
    def test_returns_ordered_events(self):
        repo, session = _make_repo()
        events = [MagicMock(spec=ProjectTaskEvent)]
        session.query.return_value.filter.return_value.order_by.return_value.all.return_value = (
            events
        )

        result = repo.list_by_task(uuid.uuid4())

        assert result == events


class TestListFirstEventsPerStatusByTasks:
    def test_empty_task_ids_short_circuits(self):
        repo, session = _make_repo()

        result = repo.list_first_events_per_status_by_tasks([])

        assert result == []
        session.query.assert_not_called()

    def test_returns_events(self):
        repo, session = _make_repo()
        events = [MagicMock(spec=ProjectTaskEvent)]
        session.query.return_value.order_by.return_value.all.return_value = events

        result = repo.list_first_events_per_status_by_tasks([uuid.uuid4()])

        assert result == events


class TestListByProject:
    def test_no_status_filter(self):
        repo, session = _make_repo()
        events = [MagicMock(spec=ProjectTaskEvent)]
        chain = session.query.return_value.filter.return_value
        chain.order_by.return_value.limit.return_value.all.return_value = events

        result = repo.list_by_project(uuid.uuid4())

        assert result == events

    def test_filters_by_status_when_given(self):
        repo, session = _make_repo()
        base_query = session.query.return_value.filter.return_value
        status_query = base_query.filter.return_value
        status_query.order_by.return_value.limit.return_value.all.return_value = []

        repo.list_by_project(uuid.uuid4(), status="failed", limit=10)

        base_query.filter.assert_called_once()
        status_query.order_by.return_value.limit.assert_called_once_with(10)
