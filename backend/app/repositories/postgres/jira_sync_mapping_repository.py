"""Postgres repository for JiraSyncMapping."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.postgres.jira_sync_mapping_model import JiraSyncMapping
from app.repositories.postgres.base_repository import BaseRepository


class JiraSyncMappingRepository(BaseRepository[JiraSyncMapping]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, JiraSyncMapping)

    def list_by_integration(self, integration_id: UUID) -> list[JiraSyncMapping]:
        return (
            self._session.query(JiraSyncMapping)
            .filter(JiraSyncMapping.integration_id == integration_id)
            .all()
        )

    def get_by_rip_entity(
        self,
        integration_id: UUID,
        entity_type: str,
        entity_id: UUID,
    ) -> JiraSyncMapping | None:
        return (
            self._session.query(JiraSyncMapping)
            .filter(
                JiraSyncMapping.integration_id == integration_id,
                JiraSyncMapping.rip_entity_type == entity_type,
                JiraSyncMapping.rip_entity_id == entity_id,
            )
            .first()
        )

    def list_synced_entity_ids(self, integration_id: UUID) -> set[UUID]:
        rows = (
            self._session.query(JiraSyncMapping.rip_entity_id)
            .filter(
                JiraSyncMapping.integration_id == integration_id,
                JiraSyncMapping.sync_status == "synced",
            )
            .all()
        )
        return {row[0] for row in rows}

    def mark_deprecated(self, integration_id: UUID, entity_ids: list[UUID]) -> int:
        if not entity_ids:
            return 0
        count = (
            self._session.query(JiraSyncMapping)
            .filter(
                JiraSyncMapping.integration_id == integration_id,
                JiraSyncMapping.rip_entity_id.in_(entity_ids),
            )
            .update({"sync_status": "deprecated"}, synchronize_session="fetch")
        )
        return count
