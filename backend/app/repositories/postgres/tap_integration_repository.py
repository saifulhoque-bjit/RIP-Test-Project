"""Postgres repository for TapIntegration."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.postgres.tap_integration_model import TapIntegration
from app.repositories.postgres.base_repository import BaseRepository


class TapIntegrationRepository(BaseRepository[TapIntegration]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, TapIntegration)

    def get_by_project_id(self, project_id: UUID) -> TapIntegration | None:
        return (
            self._session.query(TapIntegration)
            .filter(TapIntegration.project_id == project_id)
            .first()
        )

    def get_active_by_project_id(self, project_id: UUID) -> TapIntegration | None:
        return (
            self._session.query(TapIntegration)
            .filter(
                TapIntegration.project_id == project_id,
                TapIntegration.is_active.is_(True),
            )
            .first()
        )
