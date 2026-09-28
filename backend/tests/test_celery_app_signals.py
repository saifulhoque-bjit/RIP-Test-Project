"""Unit tests for the Celery signal handlers in app.core.celery_app.

These are plain functions registered via ``@signal.connect`` — callable
directly with mocked deferred imports, no real Celery worker needed.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.core.celery_app import (
    _attach_log_context_to_task,
    _bind_log_context_from_task,
    _mark_project_task_cancelled_on_revoke,
    _reset_forked_child_connections,
    _unbind_log_context_from_task,
    _use_app_json_logging,
)


class TestUseAppJsonLogging:
    def test_configures_root_json_logging(self):
        with patch("app.utils.logger.configure_root_json_logging") as mock_configure:
            _use_app_json_logging(loglevel="INFO")

        mock_configure.assert_called_once_with("INFO")

    def test_defaults_to_info_when_no_loglevel(self):
        import logging

        with patch("app.utils.logger.configure_root_json_logging") as mock_configure:
            _use_app_json_logging(loglevel=None)

        mock_configure.assert_called_once_with(logging.INFO)


class TestAttachLogContextToTask:
    def test_noop_when_headers_missing(self):
        _attach_log_context_to_task(headers=None)  # must not raise

    def test_stamps_correlation_id_and_context(self):
        headers: dict = {}
        with (
            patch("app.utils.correlation.get_correlation_id", return_value="corr-1"),
            patch("app.utils.log_context.get_log_context", return_value={"project_id": "p1"}),
        ):
            _attach_log_context_to_task(headers=headers)

        assert headers["log_context"]["correlation_id"] == "corr-1"
        assert headers["log_context"]["project_id"] == "p1"


class TestBindLogContextFromTask:
    def test_restores_context_from_task_request(self):
        task = SimpleNamespace(
            request=SimpleNamespace(log_context={"correlation_id": "corr-1", "project_id": "p1"})
        )
        with (
            patch("app.utils.correlation.set_correlation_id") as mock_set_corr,
            patch("app.utils.log_context.set_log_context") as mock_set_ctx,
        ):
            _bind_log_context_from_task(task=task)

        mock_set_corr.assert_called_once_with("corr-1")
        mock_set_ctx.assert_called_once_with({"project_id": "p1"})

    def test_defaults_to_none_when_no_request_context(self):
        task = SimpleNamespace(request=None)
        with (
            patch("app.utils.correlation.set_correlation_id") as mock_set_corr,
            patch("app.utils.log_context.set_log_context") as mock_set_ctx,
        ):
            _bind_log_context_from_task(task=task)

        mock_set_corr.assert_called_once_with("none")
        mock_set_ctx.assert_called_once_with({})


class TestUnbindLogContextFromTask:
    def test_clears_context(self):
        with (
            patch("app.utils.correlation.set_correlation_id") as mock_set_corr,
            patch("app.utils.log_context.set_log_context") as mock_set_ctx,
        ):
            _unbind_log_context_from_task()

        mock_set_corr.assert_called_once_with("none")
        mock_set_ctx.assert_called_once_with({})


class TestMarkProjectTaskCancelledOnRevoke:
    def test_noop_when_no_celery_task_id(self):
        with patch("app.db.unit_of_work.UnitOfWork") as mock_uow_cls:
            _mark_project_task_cancelled_on_revoke(request=SimpleNamespace(id=None))

        mock_uow_cls.assert_not_called()

    def test_noop_when_task_not_found(self):
        uow = MagicMock()
        uow.project_tasks.get_by_celery_task_id.return_value = None
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
            patch(
                "app.services.project_task_service.ProjectTaskService._mark_cancelled"
            ) as mock_mark_cancelled,
        ):
            _mark_project_task_cancelled_on_revoke(request=SimpleNamespace(id="celery-1"))

        mock_mark_cancelled.assert_not_called()

    def test_noop_when_already_cancelled(self):
        from app.core.constants import TASK_STATUS_CANCELLED

        task = SimpleNamespace(
            id="task-1",
            project_id="proj-1",
            task_type="source_process",
            status=TASK_STATUS_CANCELLED,
        )
        uow = MagicMock()
        uow.project_tasks.get_by_celery_task_id.return_value = task
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
            patch(
                "app.services.project_task_service.ProjectTaskService._mark_cancelled"
            ) as mock_mark_cancelled,
        ):
            _mark_project_task_cancelled_on_revoke(request=SimpleNamespace(id="celery-1"))

        mock_mark_cancelled.assert_not_called()

    def test_marks_task_and_non_source_code_sources_cancelled(self):
        task = SimpleNamespace(
            id="task-1", project_id="proj-1", task_type="document", status="running", meta={}
        )
        uow = MagicMock()
        uow.project_tasks.get_by_celery_task_id.return_value = task
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
            patch(
                "app.services.project_task_service.ProjectTaskService._sources_for_task",
                return_value=(["src-1"], False),
            ),
            patch(
                "app.services.project_task_service.ProjectTaskService._mark_cancelled"
            ) as mock_mark_cancelled,
            patch(
                "app.services.project_task_service.ProjectTaskService._rollback_source_code_run"
            ) as mock_rollback,
        ):
            _mark_project_task_cancelled_on_revoke(request=SimpleNamespace(id="celery-1"))

        uow.project_tasks.update_status.assert_called_once()
        uow.commit.assert_called_once()
        mock_mark_cancelled.assert_called_once_with(task, ["src-1"])
        mock_rollback.assert_not_called()

    def test_rolls_back_source_code_run_when_revoked(self):
        task = SimpleNamespace(
            id="task-1",
            project_id="proj-1",
            task_type="source_process",
            status="running",
            meta={"source_ids": ["src-1"]},
            request_id="req-1",
        )
        uow = MagicMock()
        uow.project_tasks.get_by_celery_task_id.return_value = task
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
            patch(
                "app.services.project_task_service.ProjectTaskService._sources_for_task",
                return_value=(["src-1"], True),
            ),
            patch(
                "app.services.project_task_service.ProjectTaskService._mark_cancelled"
            ) as mock_mark_cancelled,
            patch(
                "app.services.project_task_service.ProjectTaskService._rollback_source_code_run"
            ) as mock_rollback,
        ):
            _mark_project_task_cancelled_on_revoke(request=SimpleNamespace(id="celery-1"))

        uow.project_tasks.update_status.assert_called_once()
        uow.commit.assert_called_once()
        mock_rollback.assert_called_once_with(task, ["src-1"])
        mock_mark_cancelled.assert_not_called()


class TestResetForkedChildConnections:
    def test_disposes_engines_and_resets_neo4j_driver(self):
        with (
            patch("app.db.neo4j.reset_driver_after_fork") as mock_reset,
            patch("app.db.session.dispose_engines_after_fork") as mock_dispose,
        ):
            _reset_forked_child_connections()

        mock_dispose.assert_called_once()
        mock_reset.assert_called_once()
