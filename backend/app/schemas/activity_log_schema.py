"""Pydantic schemas for the ActivityLog domain.

Schema hierarchy
────────────────
ActivityLogActorOut       — nested actor (id/name/role) on a feed entry
ActivityLogResponse       — single activity-log entry read representation
ActivityLogListResponse   — paginated collection wrapper
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.core.enums.activity_type import ActivityType


class ActivityLogActorOut(BaseModel):
    """The user who triggered an activity, with their role on this project."""

    id: UUID
    name: str | None
    role: str | None


class ActivityLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    actor: ActivityLogActorOut | None
    activity_type: ActivityType
    summary: str
    message: str
    data: dict | None
    created_at: datetime


class ActivityLogListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[ActivityLogResponse]
    total: int
    skip: int
    limit: int
