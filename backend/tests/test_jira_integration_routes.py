"""Unit tests for Jira integration routes.

Handlers are called directly with a ``MagicMock`` service standing in for the
injected ``JiraIntegrationService`` / ``JiraSyncService``; ``uow`` is an inert
placeholder the route only forwards.  Rate-limited handlers
(``create_integration``, ``sync_execute``) receive a real minimal
``starlette.Request`` because the SlowAPI wrapper inspects it — mirroring
``tests/test_auth_routes.py``.  Async handlers use ``AsyncMock`` service methods.
"""

from __future__ import annotations

from datetime import UTC
from unittest.mock import AsyncMock, MagicMock
import uuid

from starlette.requests import Request

# Aliased so pytest does not collect the route handler itself as a test case.
from app.routes.v1.jira_integrations import (
    create_integration,
    delete_integration,
    get_integration,
    get_issue_types,
    get_sync_history_detail,
    list_sync_history,
    sync_execute,
    sync_preview,
    test_connection as call_test_connection,
    update_integration,
)
from app.schemas.jira_integration_schema import (
    JiraConnectionTestResponse,
    JiraIntegrationCreate,
    JiraIntegrationResponse,
    JiraIntegrationUpdate,
    JiraIssueTypeListResponse,
    JiraSyncExecuteRequest,
    JiraSyncHistoryListResponse,
    JiraSyncHistoryResponse,
    JiraSyncPreviewResponse,
)
from tests.conftest import make_user


def _make_request() -> Request:
    return Request(
        scope={
            "type": "http",
            "method": "POST",
            "path": "/",
            "query_string": b"",
            "headers": [],
        }
    )


def _integration_response(project_id: uuid.UUID) -> JiraIntegrationResponse:
    from datetime import datetime

    now = datetime.now(tz=UTC)
    return JiraIntegrationResponse(
        id=uuid.uuid4(),
        project_id=project_id,
        jira_base_url="https://acme.atlassian.net",
        jira_project_key="MER",
        jira_user_email="bot@acme.com",
        jira_token_env_var="TEST_JIRA_TOKEN",
        issue_type_name="Story",
        epic_issue_type_name="Epic",
        is_active=True,
        created_at=now,
        updated_at=now,
    )


class TestConfigCrudRoutes:
    async def test_create_integration_wraps_response(self) -> None:
        project_id = uuid.uuid4()
        service = MagicMock()
        expected = _integration_response(project_id)
        service.create_integration = AsyncMock(return_value=expected)
        payload = JiraIntegrationCreate(
            jira_base_url="https://acme.atlassian.net",
            jira_project_key="MER",
            jira_user_email="bot@acme.com",
            api_token="test-token-secret",
        )
        user = make_user()
        uow = object()

        result = await create_integration(
            _make_request(),
            project_id=project_id,
            payload=payload,
            current_user=user,
            uow=uow,
            service=service,
        )

        assert result.success is True
        assert result.data is expected
        service.create_integration.assert_called_once_with(
            project_id=project_id,
            payload=payload,
            uow=uow,
            user_id=user.id,
            user_roles=user.role_names,
        )

    def test_get_integration_wraps_response(self) -> None:
        project_id = uuid.uuid4()
        service = MagicMock()
        expected = _integration_response(project_id)
        service.get_integration.return_value = expected
        uow = object()

        result = get_integration(
            project_id=project_id, current_user=make_user(), uow=uow, service=service
        )

        assert result.data is expected
        service.get_integration.assert_called_once_with(project_id=project_id, uow=uow)

    async def test_update_integration_forwards_payload(self) -> None:
        project_id = uuid.uuid4()
        service = MagicMock()
        expected = _integration_response(project_id)
        service.update_integration = AsyncMock(return_value=expected)
        payload = JiraIntegrationUpdate(issue_type_name="Task")
        uow = object()

        result = await update_integration(
            project_id=project_id,
            payload=payload,
            current_user=make_user(),
            uow=uow,
            service=service,
        )

        assert result.data is expected
        service.update_integration.assert_called_once_with(
            project_id=project_id, payload=payload, uow=uow
        )

    def test_delete_integration_returns_none_payload(self) -> None:
        project_id = uuid.uuid4()
        service = MagicMock()
        uow = object()

        result = delete_integration(
            project_id=project_id, current_user=make_user(), uow=uow, service=service
        )

        assert result.success is True
        assert result.data is None
        service.delete_integration.assert_called_once_with(project_id=project_id, uow=uow)


class TestConnectionRoutes:
    async def test_test_connection_wraps_response(self) -> None:
        project_id = uuid.uuid4()
        service = MagicMock()
        expected = JiraConnectionTestResponse(connected=True, display_name="Bot")
        service.test_connection = AsyncMock(return_value=expected)
        uow = object()

        result = await call_test_connection(
            project_id=project_id, current_user=make_user(), uow=uow, service=service
        )

        assert result.data is expected
        service.test_connection.assert_awaited_once_with(project_id=project_id, uow=uow)

    async def test_get_issue_types_wraps_response(self) -> None:
        project_id = uuid.uuid4()
        service = MagicMock()
        expected = JiraIssueTypeListResponse(items=[])
        service.get_issue_types = AsyncMock(return_value=expected)
        uow = object()

        result = await get_issue_types(
            project_id=project_id, current_user=make_user(), uow=uow, service=service
        )

        assert result.data is expected
        service.get_issue_types.assert_awaited_once_with(project_id=project_id, uow=uow)


class TestSyncRoutes:
    async def test_sync_preview_wraps_response(self) -> None:
        project_id = uuid.uuid4()
        service = MagicMock()
        expected = JiraSyncPreviewResponse(new_count=1)
        service.compute_sync_preview = AsyncMock(return_value=expected)
        uow = object()

        result = await sync_preview(
            project_id=project_id, current_user=make_user(), uow=uow, service=service
        )

        assert result.data is expected
        service.compute_sync_preview.assert_awaited_once_with(project_id=project_id, uow=uow)

    async def test_sync_execute_validates_and_queues_task(self) -> None:
        project_id = uuid.uuid4()
        integration_service = MagicMock()  # JiraIntegrationService — validates integration exists
        sync_service = MagicMock()  # JiraSyncService — processes sync

        # Create payload with new schema format
        payload = JiraSyncExecuteRequest(
            modules=[
                {
                    "module_id": uuid.uuid4(),
                    "module_code": "1",
                    "module_name": "Test Module",
                    "module_description": "Test module description",
                    "features": [
                        {
                            "feature_id": uuid.uuid4(),
                            "feature_code": "1.1",
                            "feature_name": "Test Feature",
                            "feature_description": "Test feature description",
                            "user_stories": [],
                        }
                    ],
                }
            ]
        )
        user = make_user()
        uow = object()

        from app.schemas.jira_integration_schema import JiraSyncExecuteResponse, JiraSyncResultItem

        expected_response = JiraSyncExecuteResponse(
            created=1,
            updated=0,
            deprecated=0,
            total_synced=1,
            message="Synced 1 items to JIRA",
            created_items=[
                JiraSyncResultItem(
                    rip_entity_id=payload.modules[0].module_id,
                    rip_entity_type="module",
                    rip_entity_code="1",
                    title="Test Module",
                    jira_key="COMP-123",
                    jira_id="10001",
                    status="created",
                )
            ],
        )
        from unittest.mock import AsyncMock

        sync_service.execute_sync_with_hierarchy = AsyncMock(return_value=expected_response)

        result = await sync_execute(
            _make_request(),
            project_id=project_id,
            payload=payload,
            current_user=user,
            uow=uow,
            integration_service=integration_service,
            sync_service=sync_service,
        )

        assert result.success is True
        assert result.data.total_synced == 1
        assert result.data.created == 1
        # Integration existence is validated before executing.
        integration_service.get_integration.assert_called_once_with(project_id=project_id, uow=uow)
        sync_service.execute_sync_with_hierarchy.assert_called_once()

    def test_list_sync_history_forwards_pagination(self) -> None:
        project_id = uuid.uuid4()
        service = MagicMock()
        expected = JiraSyncHistoryListResponse(total=0, skip=0, limit=20, items=[])
        service.get_sync_history.return_value = expected
        pagination = MagicMock(skip=0, limit=20)
        uow = object()

        result = list_sync_history(
            project_id=project_id,
            pagination=pagination,
            current_user=make_user(),
            uow=uow,
            service=service,
        )

        assert result.data is expected
        service.get_sync_history.assert_called_once_with(
            project_id=project_id, skip=0, limit=20, uow=uow
        )

    def test_get_sync_history_detail_wraps_response(self) -> None:
        from datetime import datetime

        project_id = uuid.uuid4()
        sync_id = uuid.uuid4()
        service = MagicMock()
        now = datetime.now(tz=UTC)
        expected = JiraSyncHistoryResponse(
            id=sync_id,
            integration_id=uuid.uuid4(),
            project_id=project_id,
            status="completed",
            started_at=now,
            created_at=now,
        )
        service.get_sync_history_detail.return_value = expected
        uow = object()

        result = get_sync_history_detail(
            project_id=project_id,
            sync_id=sync_id,
            current_user=make_user(),
            uow=uow,
            service=service,
        )

        assert result.data is expected
        service.get_sync_history_detail.assert_called_once_with(sync_id=sync_id, uow=uow)
