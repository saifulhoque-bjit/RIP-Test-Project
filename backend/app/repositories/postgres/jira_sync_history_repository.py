"""Postgres repository for JiraSyncHistory."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.postgres.jira_sync_history_model import JiraSyncHistory
from app.repositories.postgres.base_repository import BaseRepository


class JiraSyncHistoryRepository(BaseRepository[JiraSyncHistory]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, JiraSyncHistory)

    def list_by_project(
        self, project_id: UUID, skip: int = 0, limit: int = 20
    ) -> tuple[list[JiraSyncHistory], int]:
        query = (
            self._session.query(JiraSyncHistory)
            .filter(JiraSyncHistory.project_id == project_id)
            .order_by(JiraSyncHistory.created_at.desc())
        )
        total = query.count()
        items = query.offset(skip).limit(limit).all()
        return items, total

    def get_latest_by_project(self, project_id: UUID) -> JiraSyncHistory | None:
        return (
            self._session.query(JiraSyncHistory)
            .filter(JiraSyncHistory.project_id == project_id)
            .order_by(JiraSyncHistory.created_at.desc())
            .first()
        )
