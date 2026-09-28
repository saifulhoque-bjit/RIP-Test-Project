"""Repository for IncrementalHistory — persists all incremental output fields per run."""

from __future__ import annotations

import uuid
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.postgres.incremental_history_model import IncrementalHistory
from app.repositories.postgres.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class IncrementalHistoryRepository(BaseRepository[IncrementalHistory]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, IncrementalHistory)

    def create(
        self,
        *,
        project_id: UUID,
        updates_json: list | None = None,
        adds_json: list | None = None,
        delete_json: list | None = None,
        flag_json: list | None = None,
        persona_glossary_additions_json: list | None = None,
        meeting_summary_json: dict | None = None,
    ) -> IncrementalHistory:
        """Insert a new IncrementalHistory row and flush to populate DB defaults."""
        record = IncrementalHistory(
            id=uuid.uuid4(),
            project_id=project_id,
            updates_json=updates_json,
            adds_json=adds_json,
            delete_json=delete_json,
            flag_json=flag_json,
            persona_glossary_additions_json=persona_glossary_additions_json,
            meeting_summary_json=meeting_summary_json,
        )
        self._session.add(record)
        self._session.flush()
        self._session.refresh(record)
        return record

    def list_by_project(
        self,
        project_id: UUID,
        *,
        limit: int = 50,
    ) -> list[IncrementalHistory]:
        """Return the most recent history rows for a project, newest first."""
        return (
            self._session.query(IncrementalHistory)
            .filter(
                IncrementalHistory.project_id == project_id,
                IncrementalHistory.deleted_at.is_(None),
            )
            .order_by(IncrementalHistory.created_at.desc())
            .limit(limit)
            .all()
        )

    def get_by_id(self, history_id: UUID) -> IncrementalHistory | None:
        """Return a single IncrementalHistory row by UUID, or None."""
        return (
            self._session.query(IncrementalHistory)
            .filter(IncrementalHistory.id == history_id)
            .first()
        )
