"""Postgres repository for TapSyncMapping."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models.postgres.tap_sync_mapping_model import TapSyncMapping
from app.repositories.postgres.base_repository import BaseRepository


class TapSyncMappingRepository(BaseRepository[TapSyncMapping]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, TapSyncMapping)

    def delete_by_rip_entity_ids(self, rip_entity_ids: list[UUID]) -> int:
        """Hard-delete mappings for a set of RIP entity ids.

        ``TapSyncMapping`` has no ``project_id`` column and no FK at all —
        it's keyed by ``rip_entity_id`` (a Neo4j Module/Feature/UserStory
        id) — so project-scoped cleanup requires the caller to already have
        collected those ids (e.g. from the Neo4j graph before deleting it).

        Returns:
            The number of rows deleted.
        """
        if not rip_entity_ids:
            return 0
        stmt = delete(TapSyncMapping).where(TapSyncMapping.rip_entity_id.in_(rip_entity_ids))
        result = self._session.execute(stmt)
        return result.rowcount

    def get_by_rip_entity(
        self,
        entity_type: str,
        entity_id: UUID,
    ) -> TapSyncMapping | None:
        return (
            self._session.query(TapSyncMapping)
            .filter(
                TapSyncMapping.rip_entity_type == entity_type,
                TapSyncMapping.rip_entity_id == entity_id,
            )
            .first()
        )

    def list_by_push_sync_id(self, sync_id: UUID) -> list[TapSyncMapping]:
        return (
            self._session.query(TapSyncMapping)
            .filter(TapSyncMapping.last_push_sync_id == sync_id)
            .all()
        )
