"""Postgres repository for JiraIntegration."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.postgres.jira_integration_model import JiraIntegration
from app.repositories.postgres.base_repository import BaseRepository


class JiraIntegrationRepository(BaseRepository[JiraIntegration]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, JiraIntegration)

    def get_by_project_id(self, project_id: UUID) -> JiraIntegration | None:
        return (
            self._session.query(JiraIntegration)
            .filter(JiraIntegration.project_id == project_id)
            .first()
        )

    def get_active_by_project_id(self, project_id: UUID) -> JiraIntegration | None:
        return (
            self._session.query(JiraIntegration)
            .filter(
                JiraIntegration.project_id == project_id,
                JiraIntegration.is_active.is_(True),
            )
            .first()
        )
