"""Repository for ActivityLog — project-scoped activity feed rows."""

from __future__ import annotations

import uuid
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.postgres.activity_log_model import ActivityLog
from app.repositories.postgres.base_repository import BaseRepository


class ActivityLogRepository(BaseRepository[ActivityLog]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, ActivityLog)

    # ── Feed queries ───────────────────────────────────────────────────────

    def list_by_project(
        self,
        project_id: UUID,
        *,
        skip: int = 0,
        limit: int = 20,
        activity_type: str | None = None,
    ) -> tuple[list[ActivityLog], int]:
        """Return a paginated activity feed for *project_id*.

        ``activity_type`` narrows the feed when given; ``None`` means no
        filtering. Results are ordered newest-first. The total count is
        returned as the second element so callers can build pagination
        metadata without an extra query.
        """
        query = self._session.query(ActivityLog).filter(ActivityLog.project_id == project_id)
        if activity_type is not None:
            query = query.filter(ActivityLog.activity_type == activity_type)
        query = query.order_by(ActivityLog.created_at.desc())
        total = query.count()
        items = query.offset(skip).limit(limit).all()
        return items, total

    # ── Creation ───────────────────────────────────────────────────────────

    def create(
        self,
        *,
        project_id: UUID,
        actor_user_id: UUID | None,
        activity_type: str,
        summary: str,
        message: str,
        data: dict | None = None,
    ) -> ActivityLog:
        """Insert a new ActivityLog row and flush to populate DB defaults.

        The caller is responsible for committing the session.
        Returns the ORM instance with all DB-generated fields populated.
        """
        activity_log = ActivityLog(
            id=uuid.uuid4(),
            project_id=project_id,
            actor_user_id=actor_user_id,
            activity_type=activity_type,
            summary=summary,
            message=message,
            data=data,
        )
        self._session.add(activity_log)
        self._session.flush()
        self._session.refresh(activity_log)
        return activity_log
