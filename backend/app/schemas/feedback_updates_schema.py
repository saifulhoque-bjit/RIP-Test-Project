"""API schemas for accepting/rejecting a feedback-driven regeneration change.

Distinct from ``app.schemas.incremental_updates_schema`` — that module covers
the incremental-source-update review flow (``incremental_change_type``); this
one covers the ``/modules/regenerate`` and ``/user-stories/regenerate-by-feedback``
feedback flows (``feedback_change_type``) on ``Module``/``Feature``/``UserStory``
nodes. The two tracking flags are independent, so a decision made here never
touches ``incremental_change_type``.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class FeedbackEntityType(str, Enum):
    """Which kind of node a feedback-change accept/reject decision targets."""

    MODULE = "module"
    FEATURE = "feature"
    USER_STORY = "user_story"


class FeedbackChangeType(str, Enum):
    """Mirrors ``app.models.neo4j.module_feature_model.ChangeType`` for the API layer."""

    ADDED = "ADDED"
    UPDATED = "UPDATED"
    DELETE_SUGGESTED = "DELETE_SUGGESTED"


class FeedbackUpdateAcceptRequest(BaseModel):
    """Request body for accepting a pending feedback-driven regeneration change."""

    entity_type: FeedbackEntityType
    entity_id: str
    change_type: FeedbackChangeType
    comment: str | None = None


class FeedbackUpdateRejectRequest(BaseModel):
    """Request body for rejecting a pending feedback-driven regeneration change."""

    entity_type: FeedbackEntityType
    entity_id: str
    change_type: FeedbackChangeType
    comment: str | None = None


class FeedbackUpdateDecisionResponse(BaseModel):
    """Result of a feedback-change accept/reject decision."""

    entity_type: FeedbackEntityType
    entity_id: str
    change_type: FeedbackChangeType
    action: str
    deleted: bool = False
    comment: str | None = None
