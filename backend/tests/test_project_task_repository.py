"""Unit tests for ProjectTaskRepository."""

from __future__ import annotations

from unittest.mock import MagicMock
import uuid

from app.models.postgres.project_task_model import CreateTaskParams, ProjectTask
from app.repositories.postgres.project_task_repository import ProjectTaskRepository


def _make_repo() -> tuple[ProjectTaskRepository, MagicMock]:
    session = MagicMock()
    return ProjectTaskRepository(session), session


class TestGetById:
    def test_returns_task(self):
        repo, session = _make_repo()
        task = MagicMock(spec=ProjectTask)
        session.query.return_value.filter.return_value.first.return_value = task

        assert repo.get_by_id(uuid.uuid4()) is task

    def test_returns_none_when_missing(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.first.return_value = None

        assert repo.get_by_id(uuid.uuid4()) is None


class TestGetByCeleryTaskId:
    def test_returns_task(self):
        repo, session = _make_repo()
        task = MagicMock(spec=ProjectTask)
        session.query.return_value.filter.return_value.first.return_value = task

        assert repo.get_by_celery_task_id("celery-1") is task


class TestListByProject:
    def test_returns_ordered_limited_rows(self):
        repo, session = _make_repo()
        rows = [MagicMock(spec=ProjectTask)]
        chain = session.query.return_value.filter.return_value.order_by.return_value
        chain.limit.return_value.all.return_value = rows

        result = repo.list_by_project(uuid.uuid4(), limit=10)

        assert result == rows
        chain.limit.assert_called_once_with(10)


class TestListActiveByProject:
    def test_returns_rows(self):
        repo, session = _make_repo()
        rows = [MagicMock(spec=ProjectTask)]
        session.query.return_value.filter.return_value.order_by.return_value.all.return_value = rows

        result = repo.list_active_by_project(uuid.uuid4())

        assert result == rows


class TestListActiveByRequestId:
    def test_returns_rows(self):
        repo, session = _make_repo()
        rows = [MagicMock(spec=ProjectTask)]
        session.query.return_value.filter.return_value.order_by.return_value.all.return_value = rows

        result = repo.list_active_by_request_id(uuid.uuid4())

        assert result == rows


class TestListActiveByOwner:
    def test_returns_rows(self):
        repo, session = _make_repo()
        rows = [MagicMock(spec=ProjectTask)]
        chain = session.query.return_value.join.return_value.filter.return_value
        chain.order_by.return_value.all.return_value = rows

        result = repo.list_active_by_owner(uuid.uuid4())

        assert result == rows


class TestUpdateStatus:
    def test_returns_none_when_task_missing(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.first.return_value = None

        assert repo.update_status(uuid.uuid4(), status="running") is None

    def test_updates_all_provided_fields(self):
        repo, session = _make_repo()
        task = MagicMock(spec=ProjectTask)
        session.query.return_value.filter.return_value.first.return_value = task

        result = repo.update_status(
            uuid.uuid4(),
            status="running",
            progress=50,
            stage="parsing",
            error="x" * 5000,
            meta={"a": 1},
        )

        assert result is task
        assert task.status == "running"
        assert task.progress == 50
        assert task.stage == "parsing"
        assert len(task.error) == 4000
        assert task.meta == {"a": 1}

    def test_leaves_unprovided_fields_untouched(self):
        repo, session = _make_repo()
        task = MagicMock(spec=ProjectTask)
        session.query.return_value.filter.return_value.first.return_value = task

        repo.update_status(uuid.uuid4(), status="running")

        assert task.status == "running"

    def test_merges_meta_instead_of_replacing(self):
        """Regression: a later progress event's meta must not erase source_ids.

        ``create_task`` seeds ``meta={"source_ids": [...]}`` so a later cancel
        can find which Source/SourceIngestion rows to stamp
        (``ProjectTaskService._sources_for_task``). Every worker progress
        event also calls this with its own small meta payload (e.g.
        ``{"source_id": ..., "total_modules": ...}``) — a plain overwrite
        would silently erase ``source_ids`` on the very first such event,
        long before anyone could cancel the run.
        """
        repo, session = _make_repo()
        task = MagicMock(spec=ProjectTask)
        task.meta = {"source_ids": ["s1", "s2"]}
        session.query.return_value.filter.return_value.first.return_value = task

        repo.update_status(
            uuid.uuid4(), status="running", meta={"source_id": "s1", "total_modules": 3}
        )

        assert task.meta == {
            "source_ids": ["s1", "s2"],
            "source_id": "s1",
            "total_modules": 3,
        }


class TestCreateTask:
    def test_creates_and_flushes(self):
        repo, session = _make_repo()
        task_id = uuid.uuid4()
        project_id = uuid.uuid4()
        params = CreateTaskParams(task_type="source_process")

        result = repo.create_task(
            task_id=task_id, project_id=project_id, user_id=None, params=params
        )

        session.add.assert_called_once()
        session.flush.assert_called_once()
        session.refresh.assert_called_once_with(result)
        assert result.id == task_id
        assert result.request_id == task_id  # defaults to task_id

    def test_request_id_defaults_to_task_id_when_omitted(self):
        repo, session = _make_repo()
        task_id = uuid.uuid4()
        params = CreateTaskParams(task_type="source_process")

        result = repo.create_task(
            task_id=task_id, project_id=uuid.uuid4(), user_id=None, params=params
        )

        assert result.request_id == task_id

    def test_explicit_request_id_preserved(self):
        repo, session = _make_repo()
        task_id = uuid.uuid4()
        request_id = uuid.uuid4()
        params = CreateTaskParams(task_type="source_process")

        result = repo.create_task(
            task_id=task_id,
            project_id=uuid.uuid4(),
            user_id=None,
            params=params,
            request_id=request_id,
        )

        assert result.request_id == request_id


class TestSetCeleryTaskId:
    def test_sets_when_task_found(self):
        repo, session = _make_repo()
        task = MagicMock(spec=ProjectTask)
        session.query.return_value.filter.return_value.first.return_value = task

        repo.set_celery_task_id(uuid.uuid4(), "celery-1")

        assert task.celery_task_id == "celery-1"

    def test_noop_when_task_missing(self):
        repo, session = _make_repo()
        session.query.return_value.filter.return_value.first.return_value = None

        repo.set_celery_task_id(uuid.uuid4(), "celery-1")  # must not raise
