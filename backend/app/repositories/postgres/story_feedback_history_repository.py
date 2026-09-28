"""Repository for StoryFeedbackHistory — persists AI patch input/output per run."""

from __future__ import annotations

import uuid
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.postgres.story_feedback_history_model import StoryFeedbackHistory
from app.repositories.postgres.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class StoryFeedbackHistoryRepository(BaseRepository[StoryFeedbackHistory]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, StoryFeedbackHistory)

    def create(
        self,
        *,
        project_id: UUID,
        task_db_id: UUID | None = None,
        story_feedbacks_json: list | None = None,
        revised_stories_json: list | None = None,
        ai_status: str | None = None,
        ai_feedback: str | None = None,
    ) -> StoryFeedbackHistory:
        """Insert a new StoryFeedbackHistory row and flush to populate DB defaults."""
        record = StoryFeedbackHistory(
            id=uuid.uuid4(),
            project_id=project_id,
            task_db_id=task_db_id,
            story_feedbacks_json=story_feedbacks_json,
            revised_stories_json=revised_stories_json,
            ai_status=ai_status,
            ai_feedback=ai_feedback,
        )
        self._session.add(record)
        self._session.flush()
        self._session.refresh(record)
        logger.info(
            "[STORY_FEEDBACK_HISTORY] created record id=%s project=%s task=%s status=%s",
            record.id,
            project_id,
            task_db_id,
            ai_status,
        )
        return record

    def list_by_project(
        self,
        project_id: UUID,
        *,
        limit: int = 50,
    ) -> list[StoryFeedbackHistory]:
        """Return the most recent history rows for a project, newest first."""
        return (
            self._session.query(StoryFeedbackHistory)
            .filter(StoryFeedbackHistory.project_id == project_id)
            .order_by(StoryFeedbackHistory.created_at.desc())
            .limit(limit)
            .all()
        )

    def get_by_id(self, history_id: UUID) -> StoryFeedbackHistory | None:
        """Return a single StoryFeedbackHistory row by UUID, or None."""
        return (
            self._session.query(StoryFeedbackHistory)
            .filter(StoryFeedbackHistory.id == history_id)
            .first()
        )
