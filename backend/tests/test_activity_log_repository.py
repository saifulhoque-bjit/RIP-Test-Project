"""Unit tests for ActivityLogRepository."""

from __future__ import annotations

from unittest.mock import MagicMock
import uuid

from app.models.postgres.activity_log_model import ActivityLog
from app.repositories.postgres.activity_log_repository import ActivityLogRepository


def _make_repo() -> tuple[ActivityLogRepository, MagicMock]:
    session = MagicMock()
    return ActivityLogRepository(session), session


class TestListByProject:
    def test_returns_items_and_total(self) -> None:
        repo, session = _make_repo()
        project_id = uuid.uuid4()
        items = [MagicMock(spec=ActivityLog), MagicMock(spec=ActivityLog)]
        query = session.query.return_value
        query.filter.return_value.order_by.return_value = query
        query.count.return_value = 5
        query.offset.return_value.limit.return_value.all.return_value = items

        result_items, total = repo.list_by_project(project_id, skip=10, limit=2)

        assert result_items == items
        assert total == 5
        session.query.assert_called_once_with(ActivityLog)
        query.offset.assert_called_once_with(10)
        query.offset.return_value.limit.assert_called_once_with(2)

    def test_applies_activity_type_filter_when_given(self) -> None:
        repo, session = _make_repo()
        project_id = uuid.uuid4()
        query = session.query.return_value
        base_filter = query.filter.return_value
        type_filter = base_filter.filter.return_value
        type_filter.order_by.return_value = type_filter
        type_filter.count.return_value = 0
        type_filter.offset.return_value.limit.return_value.all.return_value = []

        repo.list_by_project(project_id, activity_type="rfp_modules_generated")

        base_filter.filter.assert_called_once()

    def test_omits_type_filter_when_not_given(self) -> None:
        repo, session = _make_repo()
        project_id = uuid.uuid4()
        query = session.query.return_value
        base_filter = query.filter.return_value
        base_filter.order_by.return_value = base_filter
        base_filter.count.return_value = 0
        base_filter.offset.return_value.limit.return_value.all.return_value = []

        repo.list_by_project(project_id)

        base_filter.filter.assert_not_called()


class TestCreate:
    def test_adds_and_flushes_new_activity_log(self) -> None:
        repo, session = _make_repo()
        project_id = uuid.uuid4()
        actor_user_id = uuid.uuid4()

        activity_log = repo.create(
            project_id=project_id,
            actor_user_id=actor_user_id,
            activity_type="rfp_modules_generated",
            summary="Modules & Features Generated",
            message="Generated 5 Modules, 20 Features",
            data={"total_modules": 5, "total_features": 20},
        )

        assert activity_log.project_id == project_id
        assert activity_log.actor_user_id == actor_user_id
        assert activity_log.activity_type == "rfp_modules_generated"
        assert activity_log.summary == "Modules & Features Generated"
        assert activity_log.message == "Generated 5 Modules, 20 Features"
        assert activity_log.data == {"total_modules": 5, "total_features": 20}
        session.add.assert_called_once_with(activity_log)
        session.flush.assert_called_once()
        session.refresh.assert_called_once_with(activity_log)

    def test_allows_null_actor_and_data(self) -> None:
        repo, session = _make_repo()
        project_id = uuid.uuid4()

        activity_log = repo.create(
            project_id=project_id,
            actor_user_id=None,
            activity_type="incremental_changeset_ingested",
            summary="Incremental Change-Set Ingested",
            message="Ingested change-set: 0 added, 0 updated, 0 deleted",
        )

        assert activity_log.actor_user_id is None
        assert activity_log.data is None
