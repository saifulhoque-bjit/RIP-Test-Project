"""Service for Jira integration config CRUD and connection testing."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.clients.jira_client import JiraCloudClient
from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    JiraClientError,
    NotFoundError,
    ValidationError,
)
from app.core.messages import (
    MSG_JIRA_INTEGRATION_CONFLICT,
    MSG_JIRA_INTEGRATION_NOT_FOUND,
)
from app.db.unit_of_work import UnitOfWork
from app.models.postgres.jira_integration_model import JiraIntegration
from app.schemas.jira_integration_schema import (
    JiraConnectionTestResponse,
    JiraIntegrationCreate,
    JiraIntegrationResponse,
    JiraIntegrationUpdate,
    JiraIssueTypeListResponse,
    JiraIssueTypeResponse,
)
from app.utils.encryption import decrypt_token, encrypt_token
from app.utils.logger import get_logger

logger = get_logger(__name__)


class JiraIntegrationService:
    @staticmethod
    def _mask_token(token: str | None) -> str:
        """Mask API token for safe display in UI.

        Shows first 6 and last 6 characters for verification.
        Example: "ATATT3...Vqdq=" instead of full token or just ****
        """
        if not token or len(token) <= 12:
            return "****"
        return f"{token[:6]}...{token[-6:]}"

    # ── CRUD ───────────────────────────────────────────────────────────────

    @staticmethod
    async def _validate_against_jira(
        *,
        jira_base_url: str,
        jira_user_email: str,
        api_token: str,
        jira_project_key: str,
    ) -> None:
        """Verify credentials are valid and the project key actually exists in
        Jira before a config is persisted — prevents silently saving bogus
        values (e.g. a project key that doesn't exist)."""
        client = JiraCloudClient(base_url=jira_base_url, email=jira_user_email, api_token=api_token)

        try:
            await client.test_connection()
        except JiraClientError as exc:
            raise ValidationError(
                f"Could not authenticate with Jira using these credentials: {exc.message}"
            ) from exc

        try:
            await client.get_project(jira_project_key)
        except JiraClientError as exc:
            raise ValidationError(
                f"Jira project '{jira_project_key}' was not found, or these "
                f"credentials do not have access to it."
            ) from exc

    async def create_integration(
        self,
        project_id: UUID,
        payload: JiraIntegrationCreate,
        uow: UnitOfWork,
        user_id: UUID,
        user_roles: list[str],
    ) -> JiraIntegrationResponse:
        # Verify caller owns the project (or is admin)
        project = uow.projects.get_by_uuid(project_id)
        if not project:
            raise NotFoundError(f"Project {project_id} not found.")
        if project.owner_id != user_id and "admin" not in user_roles:
            raise ForbiddenError("You do not have permission to configure Jira for this project.")

        # If a previous integration was deactivated, reactivate it with new config
        existing = uow.jira_integrations.get_by_project_id(project_id)
        if existing and existing.is_active:
            raise ConflictError(MSG_JIRA_INTEGRATION_CONFLICT.format(project_id=project_id))

        await self._validate_against_jira(
            jira_base_url=payload.jira_base_url.rstrip("/"),
            jira_user_email=payload.jira_user_email,
            api_token=payload.api_token,
            jira_project_key=payload.jira_project_key,
        )

        if existing and not existing.is_active:
            # Reactivate with updated config instead of creating a new row
            existing.is_active = True
            existing.jira_base_url = payload.jira_base_url.rstrip("/")
            existing.jira_project_key = payload.jira_project_key
            existing.jira_board_id = payload.jira_board_id
            existing.jira_user_email = payload.jira_user_email
            existing.jira_api_token_encrypted = encrypt_token(payload.api_token)
            existing.issue_type_name = payload.issue_type_name
            existing.epic_issue_type_name = payload.epic_issue_type_name
            existing.deprecated_transition_id = payload.deprecated_transition_id
            # Reset cached field IDs so next sync re-provisions them
            existing.traceability_field_ids = None
            uow.flush()
            uow.refresh(existing)
            logger.info("Jira integration reactivated: project_id=%s", project_id)
            response = JiraIntegrationResponse.model_validate(existing)
            response.api_token_hint = self._mask_token(existing.jira_api_token_encrypted)
            return response

        integration = JiraIntegration(
            project_id=project_id,
            created_by_id=user_id,
            jira_base_url=payload.jira_base_url.rstrip("/"),
            jira_project_key=payload.jira_project_key,
            jira_board_id=payload.jira_board_id,
            jira_user_email=payload.jira_user_email,
            jira_api_token_encrypted=encrypt_token(payload.api_token),
            issue_type_name=payload.issue_type_name,
            epic_issue_type_name=payload.epic_issue_type_name,
            deprecated_transition_id=payload.deprecated_transition_id,
        )
        uow.jira_integrations.add(integration)
        try:
            uow.flush()
        except IntegrityError as exc:
            # Backstop for two concurrent "connect Jira" requests for the
            # same project racing past the pre-check above — the await on
            # _validate_against_jira leaves a wide window for this.
            uow.rollback()
            raise ConflictError(
                MSG_JIRA_INTEGRATION_CONFLICT.format(project_id=project_id)
            ) from exc
        uow.refresh(integration)

        logger.info("Jira integration created: project_id=%s", project_id)
        response = JiraIntegrationResponse.model_validate(integration)
        response.api_token_hint = self._mask_token(integration.jira_api_token_encrypted)
        return response

    def get_integration(self, project_id: UUID, uow: UnitOfWork) -> JiraIntegrationResponse:
        integration = uow.jira_integrations.get_active_by_project_id(project_id)
        if not integration:
            raise NotFoundError(MSG_JIRA_INTEGRATION_NOT_FOUND.format(project_id=project_id))
        response = JiraIntegrationResponse.model_validate(integration)
        response.api_token_hint = self._mask_token(integration.jira_api_token_encrypted)
        return response

    async def update_integration(
        self,
        project_id: UUID,
        payload: JiraIntegrationUpdate,
        uow: UnitOfWork,
    ) -> JiraIntegrationResponse:
        integration = uow.jira_integrations.get_active_by_project_id(project_id)
        if not integration:
            raise NotFoundError(MSG_JIRA_INTEGRATION_NOT_FOUND.format(project_id=project_id))

        updates = payload.model_dump(exclude_unset=True)

        # Only re-verify against Jira if something connection-relevant changed —
        # merge with the current values so e.g. a project-key-only change is
        # still validated against the (unchanged) base URL/credentials.
        connection_fields = ("jira_base_url", "jira_project_key", "jira_user_email", "api_token")
        if any(field in updates for field in connection_fields):
            merged_base_url = updates.get("jira_base_url", integration.jira_base_url)
            merged_token = (
                updates["api_token"]
                if "api_token" in updates
                else decrypt_token(integration.jira_api_token_encrypted)
            )
            await self._validate_against_jira(
                jira_base_url=merged_base_url.rstrip("/") if merged_base_url else merged_base_url,
                jira_user_email=updates.get("jira_user_email", integration.jira_user_email),
                api_token=merged_token,
                jira_project_key=updates.get("jira_project_key", integration.jira_project_key),
            )

        for field_name, value in updates.items():
            if field_name == "jira_base_url" and value:
                value = value.rstrip("/")
            elif field_name == "api_token" and value:
                # Encrypt the token before storing; use encrypted column name
                value = encrypt_token(value)
                field_name = "jira_api_token_encrypted"
            setattr(integration, field_name, value)

        uow.flush()
        uow.refresh(integration)

        logger.info("Jira integration updated: project_id=%s", project_id)
        response = JiraIntegrationResponse.model_validate(integration)
        response.api_token_hint = self._mask_token(integration.jira_api_token_encrypted)
        return response

    def delete_integration(self, project_id: UUID, uow: UnitOfWork) -> None:
        integration = uow.jira_integrations.get_active_by_project_id(project_id)
        if not integration:
            raise NotFoundError(MSG_JIRA_INTEGRATION_NOT_FOUND.format(project_id=project_id))
        integration.is_active = False
        uow.flush()
        logger.info("Jira integration deactivated: project_id=%s", project_id)

    # ── Connection test ────────────────────────────────────────────────────

    async def test_connection(
        self, project_id: UUID, uow: UnitOfWork
    ) -> JiraConnectionTestResponse:
        integration = uow.jira_integrations.get_active_by_project_id(project_id)
        if not integration:
            raise NotFoundError(MSG_JIRA_INTEGRATION_NOT_FOUND.format(project_id=project_id))

        token = decrypt_token(integration.jira_api_token_encrypted)
        client = JiraCloudClient(
            base_url=integration.jira_base_url,
            email=integration.jira_user_email,
            api_token=token,
        )

        try:
            user_info = await client.test_connection()
            return JiraConnectionTestResponse(
                connected=True,
                display_name=user_info.get("displayName"),
                email=user_info.get("emailAddress"),
            )
        except Exception as exc:
            logger.warning("Jira connection test failed: %s", exc)
            return JiraConnectionTestResponse(connected=False)

    # ── Issue types discovery ──────────────────────────────────────────────

    async def get_issue_types(self, project_id: UUID, uow: UnitOfWork) -> JiraIssueTypeListResponse:
        integration = uow.jira_integrations.get_active_by_project_id(project_id)
        if not integration:
            raise NotFoundError(MSG_JIRA_INTEGRATION_NOT_FOUND.format(project_id=project_id))

        token = decrypt_token(integration.jira_api_token_encrypted)
        client = JiraCloudClient(
            base_url=integration.jira_base_url,
            email=integration.jira_user_email,
            api_token=token,
        )

        raw_types = await client.get_issue_types(integration.jira_project_key)
        items = [
            JiraIssueTypeResponse(
                id=it.get("id", ""),
                name=it.get("name", ""),
                subtask=it.get("subtask", False),
            )
            for it in raw_types
        ]
        return JiraIssueTypeListResponse(items=items)
