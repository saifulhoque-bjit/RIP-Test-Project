"""Unit tests for app.workers.jira_sync_task (tasks.sync_to_jira)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import uuid

from app.workers.jira_sync_task import sync_to_jira_task


def _make_uow_cm(uow: MagicMock) -> MagicMock:
    cm = MagicMock()
    cm.__enter__.return_value = uow
    cm.__exit__.return_value = None
    return cm


class TestSyncToJiraTask:
    def test_success_returns_status_and_summary(self):
        uow = MagicMock()
        history = SimpleNamespace(status="completed", summary={"created": 2})

        with (
            patch("app.workers.jira_sync_task.get_neo4j_driver", return_value=MagicMock()),
            patch("app.workers.jira_sync_task.ModuleFeatureRepository"),
            patch("app.workers.jira_sync_task.UserStoryRepository"),
            patch("app.workers.jira_sync_task.JiraSyncService"),
            patch("app.workers.jira_sync_task.UnitOfWork", return_value=_make_uow_cm(uow)),
            patch("app.workers.jira_sync_task._run_async", return_value=history),
        ):
            result = sync_to_jira_task.run(
                str(uuid.uuid4()), [str(uuid.uuid4())], [], str(uuid.uuid4())
            )

        assert result == {"status": "completed", "summary": {"created": 2}}
        uow.commit.assert_called_once()

    def test_failure_records_sync_history_when_integration_found(self):
        first_uow = MagicMock()
        failure_uow = MagicMock()
        integration = SimpleNamespace(id=uuid.uuid4())
        failure_uow.jira_integrations.get_active_by_project_id.return_value = integration

        with (
            patch("app.workers.jira_sync_task.get_neo4j_driver", return_value=MagicMock()),
            patch("app.workers.jira_sync_task.ModuleFeatureRepository"),
            patch("app.workers.jira_sync_task.UserStoryRepository"),
            patch("app.workers.jira_sync_task.JiraSyncService"),
            patch(
                "app.workers.jira_sync_task.UnitOfWork",
                side_effect=[_make_uow_cm(first_uow), _make_uow_cm(failure_uow)],
            ),
            patch(
                "app.workers.jira_sync_task._run_async", side_effect=RuntimeError("jira api down")
            ),
        ):
            try:
                sync_to_jira_task.run(str(uuid.uuid4()), [], [], str(uuid.uuid4()))
            except RuntimeError:
                pass

        failure_uow.jira_sync_history.add.assert_called_once()
        added = failure_uow.jira_sync_history.add.call_args[0][0]
        assert added.status == "failed"
        failure_uow.commit.assert_called_once()

    def test_failure_with_no_integration_skips_history_record(self):
        first_uow = MagicMock()
        failure_uow = MagicMock()
        failure_uow.jira_integrations.get_active_by_project_id.return_value = None

        with (
            patch("app.workers.jira_sync_task.get_neo4j_driver", return_value=MagicMock()),
            patch("app.workers.jira_sync_task.ModuleFeatureRepository"),
            patch("app.workers.jira_sync_task.UserStoryRepository"),
            patch("app.workers.jira_sync_task.JiraSyncService"),
            patch(
                "app.workers.jira_sync_task.UnitOfWork",
                side_effect=[_make_uow_cm(first_uow), _make_uow_cm(failure_uow)],
            ),
            patch(
                "app.workers.jira_sync_task._run_async", side_effect=RuntimeError("jira api down")
            ),
        ):
            try:
                sync_to_jira_task.run(str(uuid.uuid4()), [], [], str(uuid.uuid4()))
            except RuntimeError:
                pass

        failure_uow.jira_sync_history.add.assert_not_called()

    def test_failure_recording_itself_raising_is_swallowed(self):
        first_uow = MagicMock()

        with (
            patch("app.workers.jira_sync_task.get_neo4j_driver", return_value=MagicMock()),
            patch("app.workers.jira_sync_task.ModuleFeatureRepository"),
            patch("app.workers.jira_sync_task.UserStoryRepository"),
            patch("app.workers.jira_sync_task.JiraSyncService"),
            patch(
                "app.workers.jira_sync_task.UnitOfWork",
                side_effect=[_make_uow_cm(first_uow), RuntimeError("db down too")],
            ),
            patch(
                "app.workers.jira_sync_task._run_async", side_effect=RuntimeError("jira api down")
            ),
        ):
            try:
                sync_to_jira_task.run(str(uuid.uuid4()), [], [], str(uuid.uuid4()))
            except RuntimeError as exc:
                assert "jira api down" in str(exc)
