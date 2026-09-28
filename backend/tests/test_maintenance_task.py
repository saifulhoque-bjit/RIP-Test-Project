"""Unit tests for app.workers.maintenance_task."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import uuid

from app.workers.maintenance_task import detect_stale_ingestions


def _make_ingestion(run_code: str = "RUN-1001") -> MagicMock:
    ingestion = MagicMock()
    ingestion.id = uuid.uuid4()
    ingestion.project_id = uuid.uuid4()
    ingestion.run_code = run_code
    return ingestion


def _make_uow_cm(uow: MagicMock) -> MagicMock:
    cm = MagicMock()
    cm.__enter__.return_value = uow
    cm.__exit__.return_value = None
    return cm


def _make_service(*, stale=(), advisory_stale=()) -> MagicMock:
    service = MagicMock()
    service.fail_stale_running_ingestions.return_value = list(stale)
    service.list_advisory_stale_source_code_ingestions.return_value = list(advisory_stale)
    return service


class TestDetectStaleIngestions:
    def test_no_stale_ingestions_is_a_noop(self):
        uow = MagicMock()
        service = _make_service()

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=_make_uow_cm(uow)),
            patch(
                "app.services.source_ingestion_service.SourceIngestionService",
                return_value=service,
            ),
            patch("app.services.notification_service.publish_notification") as mock_notify,
        ):
            result = detect_stale_ingestions.run()

        assert result == {"stale_count": 0, "advisory_stale_count": 0}
        uow.commit.assert_called_once()
        mock_notify.assert_not_called()

    def test_fails_stale_ingestion_and_notifies_owner(self):
        ingestion = _make_ingestion()
        owner_id = uuid.uuid4()
        project = MagicMock()
        project.owner_id = owner_id

        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = project
        service = _make_service(stale=[ingestion])

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=_make_uow_cm(uow)),
            patch(
                "app.services.source_ingestion_service.SourceIngestionService",
                return_value=service,
            ),
            patch("app.services.notification_service.publish_notification") as mock_notify,
        ):
            result = detect_stale_ingestions.run()

        assert result == {"stale_count": 1, "advisory_stale_count": 0}
        uow.commit.assert_called_once()
        mock_notify.assert_called_once()
        _, kwargs = mock_notify.call_args
        assert kwargs["user_id"] == owner_id
        assert kwargs["data"] == {
            "project_id": str(ingestion.project_id),
            "source_ingestion_id": str(ingestion.id),
        }

    def test_skips_notification_when_project_not_found(self):
        ingestion = _make_ingestion()

        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = None
        service = _make_service(stale=[ingestion])

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=_make_uow_cm(uow)),
            patch(
                "app.services.source_ingestion_service.SourceIngestionService",
                return_value=service,
            ),
            patch("app.services.notification_service.publish_notification") as mock_notify,
        ):
            result = detect_stale_ingestions.run()

        assert result == {"stale_count": 1, "advisory_stale_count": 0}
        mock_notify.assert_not_called()

    def test_notification_failure_does_not_raise(self):
        ingestion = _make_ingestion()
        project = MagicMock()
        project.owner_id = uuid.uuid4()

        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = project
        service = _make_service(stale=[ingestion])

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=_make_uow_cm(uow)),
            patch(
                "app.services.source_ingestion_service.SourceIngestionService",
                return_value=service,
            ),
            patch(
                "app.services.notification_service.publish_notification",
                side_effect=RuntimeError("redis down"),
            ),
        ):
            result = detect_stale_ingestions.run()

        assert result == {"stale_count": 1, "advisory_stale_count": 0}

    def test_multiple_stale_ingestions_each_notify_independently(self):
        first = _make_ingestion(run_code="RUN-1001")
        second = _make_ingestion(run_code="RUN-1002")
        project = MagicMock()
        project.owner_id = uuid.uuid4()

        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = project
        service = _make_service(stale=[first, second])

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=_make_uow_cm(uow)),
            patch(
                "app.services.source_ingestion_service.SourceIngestionService",
                return_value=service,
            ),
            patch("app.services.notification_service.publish_notification") as mock_notify,
        ):
            result = detect_stale_ingestions.run()

        assert result == {"stale_count": 2, "advisory_stale_count": 0}
        assert mock_notify.call_count == 2

    def test_advisory_stale_source_code_is_logged_but_not_failed_or_notified(self):
        advisory = _make_ingestion(run_code="RUN-2001")

        uow = MagicMock()
        service = _make_service(advisory_stale=[advisory])

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=_make_uow_cm(uow)),
            patch(
                "app.services.source_ingestion_service.SourceIngestionService",
                return_value=service,
            ),
            patch("app.services.notification_service.publish_notification") as mock_notify,
        ):
            result = detect_stale_ingestions.run()

        assert result == {"stale_count": 0, "advisory_stale_count": 1}
        mock_notify.assert_not_called()
        uow.source_ingestions.update_fields.assert_not_called()

    def test_stale_and_advisory_stale_both_reported_independently(self):
        stale = _make_ingestion(run_code="RUN-1001")
        advisory = _make_ingestion(run_code="RUN-2001")
        project = MagicMock()
        project.owner_id = uuid.uuid4()

        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = project
        service = _make_service(stale=[stale], advisory_stale=[advisory])

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=_make_uow_cm(uow)),
            patch(
                "app.services.source_ingestion_service.SourceIngestionService",
                return_value=service,
            ),
            patch("app.services.notification_service.publish_notification") as mock_notify,
        ):
            result = detect_stale_ingestions.run()

        assert result == {"stale_count": 1, "advisory_stale_count": 1}
        # Only the auto-failed ingestion gets a notification — the advisory
        # one is log-only.
        mock_notify.assert_called_once()
