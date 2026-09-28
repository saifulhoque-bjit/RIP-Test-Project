"""Schemas for observability and dead-letter task operations."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DeadLetterReplayRequest(BaseModel):
    """Payload for replaying a failed Celery task."""

    task_name: str = Field(min_length=1, max_length=200)
    args: list[Any] = Field(default_factory=list)
    kwargs: dict[str, Any] = Field(default_factory=dict)
    queue: str | None = Field(default=None, min_length=1, max_length=100)


class DeadLetterReplayResponse(BaseModel):
    """Response details for a replayed task dispatch."""

    task_name: str
    replay_task_id: str
    queue: str
