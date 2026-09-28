"""Unit tests for the /projects/{project_id}/activity-logs route handler.

Handler is plain ``def`` (no genuine async I/O — see
``app/routes/v1/activity_logs.py``), so it's called directly without
``await``. ``service`` is a ``MagicMock`` standing in for the injected
``ActivityLogService``; ``uow``/``pagination``/``_current_user``/``_project``
are inert placeholders the route only forwards, matching the style of
``tests/test_notification_routes.py``.
"""

from __future__ import annotations

from unittest.mock import MagicMock
import uuid

from app.core.enums.activity_type import ActivityType
from app.core.messages import MSG_ACTIVITY_LOG_LIST_FETCHED
from app.routes.v1.activity_logs import list_activity_logs
from app.schemas.activity_log_schema import ActivityLogListResponse
from tests.conftest import make_project, make_user


class TestListActivityLogs:
    def test_calls_service_and_wraps_response(self) -> None:
        project_id = uuid.uuid4()
        user = make_user()
        project = make_project()
        service = MagicMock()
        expected = ActivityLogListResponse(items=[], total=0, skip=0, limit=20)
        service.list_activities.return_value = expected
        pagination = MagicMock(skip=0, limit=20)
        uow = object()

        result = list_activity_logs(
            project_id=project_id,
            uow=uow,
            pagination=pagination,
            service=service,
            _current_user=user,
            _project=project,
        )

        assert result.success is True
        assert result.message == MSG_ACTIVITY_LOG_LIST_FETCHED
        assert result.data is expected
        service.list_activities.assert_called_once_with(
            project_id, skip=0, limit=20, activity_type=None, uow=uow
        )

    def test_forwards_activity_type_filter(self) -> None:
        project_id = uuid.uuid4()
        user = make_user()
        project = make_project()
        service = MagicMock()
        expected = ActivityLogListResponse(items=[], total=0, skip=0, limit=20)
        service.list_activities.return_value = expected
        pagination = MagicMock(skip=0, limit=20)
        uow = object()

        result = list_activity_logs(
            project_id=project_id,
            uow=uow,
            pagination=pagination,
            service=service,
            _current_user=user,
            _project=project,
            activity_type=ActivityType.INCREMENTAL_CHANGE_REJECTED,
        )

        assert result.success is True
        service.list_activities.assert_called_once_with(
            project_id,
            skip=0,
            limit=20,
            activity_type=ActivityType.INCREMENTAL_CHANGE_REJECTED,
            uow=uow,
        )
