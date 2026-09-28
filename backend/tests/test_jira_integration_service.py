"""Unit tests for JiraIntegrationService (config CRUD, connection, issue types).

The ``UnitOfWork`` is a ``MagicMock`` — the service receives it as a parameter,
so no real DB session is opened.  ``JiraCloudClient`` is patched at the service
import site for the async connection/issue-type paths.  The Jira API token is
injected through ``monkeypatch.setenv`` since the service resolves it from the
process environment (never from the DB).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from app.core.exceptions import ConflictError, NotFoundError
from app.models.postgres.jira_integration_model import JiraIntegration
from app.schemas.jira_integration_schema import (
    JiraIntegrationCreate,
    JiraIntegrationUpdate,
)
from app.services.jira_integration_service import JiraIntegrationService


def _make_integration(**overrides) -> JiraIntegration:
    integ = JiraIntegration()
    integ.id = overrides.get("id", uuid.uuid4())
    integ.project_id = overrides.get("project_id", uuid.uuid4())
    integ.created_by_id = overrides.get("created_by_id", uuid.uuid4())
    integ.jira_base_url = overrides.get("jira_base_url", "https://acme.atlassian.net")
    integ.jira_project_key = overrides.get("jira_project_key", "MER")
    integ.jira_board_id = overrides.get("jira_board_id")
    integ.jira_user_email = overrides.get("jira_user_email", "bot@acme.com")
    integ.jira_api_token_encrypted = overrides.get(
        "jira_api_token_encrypted", "encrypted_token_cipher_text"
    )
    integ.issue_type_name = overrides.get("issue_type_name", "Story")
    integ.epic_issue_type_name = overrides.get("epic_issue_type_name", "Epic")
    integ.traceability_field_ids = overrides.get("traceability_field_ids")
    integ.deprecated_transition_id = overrides.get("deprecated_transition_id")
    integ.is_active = overrides.get("is_active", True)
    integ.last_synced_at = overrides.get("last_synced_at")
    now = datetime.now(tz=UTC)
    integ.created_at = overrides.get("created_at", now)
    integ.updated_at = overrides.get("updated_at", now)
    return integ


def _make_uow() -> MagicMock:
    uow = MagicMock()
    uow.jira_integrations = MagicMock()
    uow.projects = MagicMock()
    # Default: project exists and is owned by the caller (ownership check passes)
    mock_project = MagicMock()
    mock_project.owner_id = None  # set per test via uow.projects.get_by_uuid.return_value
    uow.projects.get_by_uuid.return_value = mock_project
    uow.flush = MagicMock()
    uow.refresh = MagicMock()
    return uow


def _create_payload(**overrides) -> JiraIntegrationCreate:
    data = {
        "jira_base_url": "https://acme.atlassian.net/",
        "jira_project_key": "MER",
        "jira_user_email": "bot@acme.com",
        "api_token": "test-token-12345",
    }
    data.update(overrides)
    return JiraIntegrationCreate(**data)


class TestCreateIntegration:
    async def test_creates_when_absent(self) -> None:
        uow = _make_uow()
        uow.jira_integrations.get_by_project_id.return_value = None

        def _refresh(obj):  # populate DB-side defaults the mock flush skips
            obj.id = uuid.uuid4()
            obj.is_active = True
            obj.traceability_field_ids = None
            obj.last_synced_at = None
            obj.created_at = datetime.now(tz=UTC)
            obj.updated_at = datetime.now(tz=UTC)

        uow.refresh.side_effect = _refresh
        project_id = uuid.uuid4()
        user_id = uuid.uuid4()
        # ownership check: project.owner_id == user_id → passes
        uow.projects.get_by_uuid.return_value.owner_id = user_id

        with (
            patch(
                "app.services.jira_integration_service.encrypt_token",
                return_value="encrypted_cipher",
            ),
            patch.object(JiraIntegrationService, "_validate_against_jira", new_callable=AsyncMock),
        ):
            result = await JiraIntegrationService().create_integration(
                project_id=project_id,
                payload=_create_payload(),
                uow=uow,
                user_id=user_id,
                user_roles=[],
            )

        assert result.project_id == project_id
        # Trailing slash on base_url is stripped before persisting
        assert result.jira_base_url == "https://acme.atlassian.net"
        uow.jira_integrations.add.assert_called_once()
        uow.flush.assert_called_once()

    async def test_conflict_when_integration_exists(self) -> None:
        uow = _make_uow()
        user_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value.owner_id = user_id
        uow.jira_integrations.get_by_project_id.return_value = _make_integration(is_active=True)

        with pytest.raises(ConflictError):
            await JiraIntegrationService().create_integration(
                project_id=uuid.uuid4(),
                payload=_create_payload(),
                uow=uow,
                user_id=user_id,
                user_roles=[],
            )

    async def test_reactivates_deactivated_integration(self) -> None:
        uow = _make_uow()
        user_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value.owner_id = user_id
        old_integ = _make_integration(is_active=False)
        uow.jira_integrations.get_by_project_id.return_value = old_integ

        def _refresh(obj):
            obj.created_at = datetime.now(tz=UTC)
            obj.updated_at = datetime.now(tz=UTC)

        uow.refresh.side_effect = _refresh

        with (
            patch(
                "app.services.jira_integration_service.encrypt_token", return_value="new_encrypted"
            ),
            patch.object(JiraIntegrationService, "_validate_against_jira", new_callable=AsyncMock),
        ):
            result = await JiraIntegrationService().create_integration(
                project_id=uuid.uuid4(),
                payload=_create_payload(),
                uow=uow,
                user_id=user_id,
                user_roles=[],
            )

        assert result.is_active is True
        uow.flush.assert_called_once()


class TestGetIntegration:
    def test_returns_active_integration(self) -> None:
        uow = _make_uow()
        integ = _make_integration()
        uow.jira_integrations.get_active_by_project_id.return_value = integ

        result = JiraIntegrationService().get_integration(project_id=integ.project_id, uow=uow)

        assert result.id == integ.id

    def test_not_found_raises(self) -> None:
        uow = _make_uow()
        uow.jira_integrations.get_active_by_project_id.return_value = None

        with pytest.raises(NotFoundError):
            JiraIntegrationService().get_integration(project_id=uuid.uuid4(), uow=uow)


class TestUpdateIntegration:
    async def test_applies_only_set_fields(self) -> None:
        # issue_type_name is not a connection field → no Jira re-validation
        uow = _make_uow()
        integ = _make_integration(issue_type_name="Story")
        uow.jira_integrations.get_active_by_project_id.return_value = integ

        result = await JiraIntegrationService().update_integration(
            project_id=integ.project_id,
            payload=JiraIntegrationUpdate(issue_type_name="Task"),
            uow=uow,
        )

        assert result.issue_type_name == "Task"
        assert integ.jira_project_key == "MER"  # untouched
        uow.flush.assert_called_once()

    async def test_strips_trailing_slash_on_base_url_update(self) -> None:
        # jira_base_url IS a connection field → Jira re-validation runs (patched)
        uow = _make_uow()
        integ = _make_integration()
        uow.jira_integrations.get_active_by_project_id.return_value = integ

        with (
            patch.object(JiraIntegrationService, "_validate_against_jira", new_callable=AsyncMock),
            patch("app.services.jira_integration_service.decrypt_token", return_value="tok"),
        ):
            result = await JiraIntegrationService().update_integration(
                project_id=integ.project_id,
                payload=JiraIntegrationUpdate(jira_base_url="https://new.atlassian.net/"),
                uow=uow,
            )

        assert result.jira_base_url == "https://new.atlassian.net"

    async def test_not_found_raises(self) -> None:
        uow = _make_uow()
        uow.jira_integrations.get_active_by_project_id.return_value = None

        with pytest.raises(NotFoundError):
            await JiraIntegrationService().update_integration(
                project_id=uuid.uuid4(),
                payload=JiraIntegrationUpdate(issue_type_name="Story"),
                uow=uow,
            )


class TestDeleteIntegration:
    def test_soft_deletes_by_deactivating(self) -> None:
        uow = _make_uow()
        integ = _make_integration(is_active=True)
        uow.jira_integrations.get_active_by_project_id.return_value = integ

        JiraIntegrationService().delete_integration(project_id=integ.project_id, uow=uow)

        assert integ.is_active is False
        uow.flush.assert_called_once()

    def test_not_found_raises(self) -> None:
        uow = _make_uow()
        uow.jira_integrations.get_active_by_project_id.return_value = None

        with pytest.raises(NotFoundError):
            JiraIntegrationService().delete_integration(project_id=uuid.uuid4(), uow=uow)


class TestConnectionAndDiscovery:
    async def test_test_connection_success(self) -> None:
        uow = _make_uow()
        uow.jira_integrations.get_active_by_project_id.return_value = _make_integration()

        mock_client = MagicMock()
        mock_client.test_connection = AsyncMock(
            return_value={"displayName": "Bot", "emailAddress": "bot@acme.com"}
        )
        with (
            patch(
                "app.services.jira_integration_service.decrypt_token",
                return_value="plaintext_token",
            ),
            patch(
                "app.services.jira_integration_service.JiraCloudClient", return_value=mock_client
            ),
        ):
            result = await JiraIntegrationService().test_connection(
                project_id=uuid.uuid4(), uow=uow
            )

        assert result.connected is True
        assert result.display_name == "Bot"
        assert result.email == "bot@acme.com"

    async def test_test_connection_failure_returns_disconnected(self) -> None:
        uow = _make_uow()
        uow.jira_integrations.get_active_by_project_id.return_value = _make_integration()

        mock_client = MagicMock()
        mock_client.test_connection = AsyncMock(side_effect=RuntimeError("401"))
        with (
            patch(
                "app.services.jira_integration_service.decrypt_token",
                return_value="plaintext_token",
            ),
            patch(
                "app.services.jira_integration_service.JiraCloudClient", return_value=mock_client
            ),
        ):
            result = await JiraIntegrationService().test_connection(
                project_id=uuid.uuid4(), uow=uow
            )

        assert result.connected is False

    async def test_get_issue_types_maps_response(self) -> None:
        uow = _make_uow()
        uow.jira_integrations.get_active_by_project_id.return_value = _make_integration()

        mock_client = MagicMock()
        mock_client.get_issue_types = AsyncMock(
            return_value=[
                {"id": "1", "name": "Story", "subtask": False},
                {"id": "2", "name": "Sub-task", "subtask": True},
            ]
        )
        with (
            patch(
                "app.services.jira_integration_service.decrypt_token",
                return_value="plaintext_token",
            ),
            patch(
                "app.services.jira_integration_service.JiraCloudClient", return_value=mock_client
            ),
        ):
            result = await JiraIntegrationService().get_issue_types(
                project_id=uuid.uuid4(), uow=uow
            )

        assert [i.name for i in result.items] == ["Story", "Sub-task"]
        assert result.items[1].subtask is True

    async def test_connection_not_found_raises(self) -> None:
        uow = _make_uow()
        uow.jira_integrations.get_active_by_project_id.return_value = None

        with pytest.raises(NotFoundError):
            await JiraIntegrationService().test_connection(project_id=uuid.uuid4(), uow=uow)
