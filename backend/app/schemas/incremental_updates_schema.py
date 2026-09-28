"""API schemas for the merged incremental-updates review tree.

Merges the current live backlog (Module -> Feature -> UserStory) from Neo4j
with the latest pending ``IncrementalHistory`` row's adds/updates/deletes,
annotating each node with ``changed`` / ``changed_action`` / ``proposed_items``
so a human reviewer can see current vs. proposed state in one tree.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.module_feature_schema import FeatureSourceResponse, FunctionResponse
from app.schemas.user_story_schema import (
    AcceptanceCriterion,
    ModuleTreeNode,
    UserStoryNFR,
    UserStorySourceResponse,
    UserStoryStatus,
)


class ChangedAction(str, Enum):
    """CRUD verb describing why a node is flagged as changed."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class ModuleProposedItems(BaseModel):
    """Proposed field values for a module update — only fields the proposal changes."""

    mod_code: str | None = None
    name: str | None = None
    description: str | None = None


class FeatureProposedItems(BaseModel):
    """Proposed field values for a feature update."""

    fea_code: str | None = None
    name: str | None = None
    description: str | None = None
    functions: list[FunctionResponse] | None = None
    sources: list[FeatureSourceResponse] | None = None


class UserStoryProposedItems(BaseModel):
    """Proposed field values for a user story update."""

    user_story_code: str | None = None
    title: str | None = None
    as_a: str | None = None
    i_want_to: str | None = None
    so_that: str | None = None
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    nfrs: list[UserStoryNFR] = Field(default_factory=list)
    story_points: int | None = None
    technical_notes: str | None = None
    sources: list[UserStorySourceResponse] | None = None


class IncrementalUserStoryNode(BaseModel):
    """A user story leaf, annotated with its incremental review state."""

    id: str | None = None
    user_story_code: str | None = None
    title: str | None = None
    status: UserStoryStatus | None = None
    as_a: str | None = None
    i_want_to: str | None = None
    so_that: str | None = None
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    nfrs: list[UserStoryNFR] = Field(default_factory=list)
    story_points: int | None = None
    technical_notes: str | None = None
    sources: list[UserStorySourceResponse] = Field(default_factory=list)
    changed: bool = False
    changed_action: ChangedAction | None = None
    justification: str | None = None
    proposed_items: UserStoryProposedItems | None = None


class IncrementalFeatureNode(BaseModel):
    """A feature node, annotated with its incremental review state."""

    id: str | None = None
    fea_code: str | None = None
    name: str | None = None
    description: str | None = None
    functions: list[FunctionResponse] = Field(default_factory=list)
    sources: list[FeatureSourceResponse] = Field(default_factory=list)
    changed: bool = False
    changed_action: ChangedAction | None = None
    justification: str | None = None
    proposed_items: FeatureProposedItems | None = None
    children: list[IncrementalUserStoryNode] = Field(default_factory=list)


class IncrementalModuleNode(BaseModel):
    """A module node, annotated with its incremental review state."""

    id: str | None = None
    mod_code: str | None = None
    name: str | None = None
    description: str | None = None
    changed: bool = False
    changed_action: ChangedAction | None = None
    justification: str | None = None
    proposed_items: ModuleProposedItems | None = None
    children: list[IncrementalFeatureNode] = Field(default_factory=list)


class IncrementalUpdatesTreeResponse(BaseModel):
    """Full module -> feature -> user story tree merged with the latest pending incremental proposal."""

    history_id: UUID | None = None
    generated_at: datetime | None = None
    items: list[IncrementalModuleNode] = Field(default_factory=list)


class IncrementalUpdatesListResponse(BaseModel):
    """Module -> feature -> user story tree pruned to nodes with a non-null ``incremental_change_type``.

    A node is included if it was itself flagged by an incremental update, or
    if it has a surviving descendant that was — e.g. a feature with no change
    of its own still appears when one of its user stories changed, so parent
    linkage is never lost.

    The count fields tally every node in the tree by its own
    ``incremental_change_type`` (ADDED / UPDATED / DELETE_SUGGESTED),
    independent of where it ended up in the pruned ``items`` tree.
    """

    module_added: int = 0
    module_updated: int = 0
    module_deleted_suggested: int = 0
    feature_added: int = 0
    feature_updated: int = 0
    feature_deleted_suggested: int = 0
    user_story_added: int = 0
    user_story_updated: int = 0
    user_story_deleted_suggested: int = 0
    items: list[ModuleTreeNode] = Field(default_factory=list)


# ── Accept / reject a pending incremental change ────────────────────────────


class UpdateEntityType(str, Enum):
    """Which kind of node an accept/reject decision targets."""

    MODULE = "module"
    FEATURE = "feature"
    USER_STORY = "user_story"


class UpdateChangeType(str, Enum):
    """Mirrors ``app.models.neo4j.module_feature_model.ChangeType`` for the API layer."""

    ADDED = "ADDED"
    UPDATED = "UPDATED"
    DELETE_SUGGESTED = "DELETE_SUGGESTED"


class UpdateAcceptRequest(BaseModel):
    """Request body for accepting a pending incremental change."""

    entity_type: UpdateEntityType
    entity_id: str
    change_type: UpdateChangeType
    comment: str | None = None


class UpdateRejectRequest(BaseModel):
    """Request body for rejecting a pending incremental change."""

    entity_type: UpdateEntityType
    entity_id: str
    change_type: UpdateChangeType
    reason: str | None = None


class UpdateDecisionResponse(BaseModel):
    """Result of an accept/reject decision."""

    entity_type: UpdateEntityType
    entity_id: str
    change_type: UpdateChangeType
    action: str
    deleted: bool = False
    comment: str | None = None
    reason: str | None = None
