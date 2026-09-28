"""API schemas for user story operations."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.srs_evidence_schema import SRSEvidenceInfo


class UserStoryStatus(str, Enum):
    READY = "ready"
    NEEDS_EDIT = "needs_edit"
    FAILED = "failed"
    APPROVED = "approved"
    DELETED = "deleted"


class RFPFlaggedItem(BaseModel):
    """Backlog quality-gate flag pointing to a specific story that needs RFP follow-up."""

    entity_id: str
    entity_type: str
    issue: str
    suggested_fix: str


class AcceptanceCriterion(BaseModel):
    """Structured acceptance criterion in Gherkin-style (Given/When/Then)."""

    type: str = Field(
        ..., description="Scenario type, e.g. 'Happy Path', 'Negative Path', 'Edge Case'"
    )
    given: str
    when: str
    then: str
    ac_code: str | None = None  # RFP pipeline's stable identifier, e.g. "U.S 1.5.1.2"
    id: str | None = None  # Source-code pipeline's identifier for this criterion
    l2_source_ref: str | None = None


class UserStoryNFR(BaseModel):
    """Normalized non-functional requirement for a user story."""

    id: str
    category: str
    description: str = ""
    requirement: str = ""


class TextDiffSpan(BaseModel):
    """A single semantic-diff span: the smallest substring whose meaning changed.

    ``before``/``after`` — at least one is populated; both populated means a
    reworded span, ``before`` only means removed, ``after`` only means added.
    """

    before: str | None = None
    after: str | None = None


# Nested dict keyed by field name, e.g. {"so_that": [{"before": ..., "after": ...}],
# "acceptance_criteria": {"<ac_code>": {"then": [{"before": ..., "after": ...}]}}} —
# emitted directly by the LLM in this shape (see UserStoryTextDiffs /
# FeatureTextDiffs / ModuleTextDiffs in app.schemas.rfp_pipeline_v2_graph_schema).
# Loosely typed here (rather than a strict per-entity schema) since the shape
# varies by node type.
TextDiffs = dict[str, Any]


class UserStorySourceBBox(BaseModel):
    """Bounding box used by source references attached to user stories."""

    x: float
    y: float
    w: float
    h: float


class UserStorySourceRef(BaseModel):
    """Source reference — one entry per (source_id, page) pair, with all bounding boxes for that page."""

    source_id: str
    fragment_id: str | None = Field(
        None, description="Reference to the source fragment containing this user story element"
    )
    page: int
    bboxes: list[UserStorySourceBBox] = Field(default_factory=list)


class UserStoryBBoxEntry(BaseModel):
    """Single bounding-box entry with its associated fragment."""

    bbox: UserStorySourceBBox
    fragment_id: str | None = None


class UserStorySourcePage(BaseModel):
    """All bounding boxes for a single page within a source."""

    page: int
    bboxes: list[UserStoryBBoxEntry] = Field(default_factory=list)


class UserStorySourceResponse(BaseModel):
    """Hierarchical source reference grouped by source_id then page."""

    source_id: str
    pages: list[UserStorySourcePage] = Field(default_factory=list)


class SourceCodeAcceptanceCriterion(BaseModel):
    """Acceptance criterion shape emitted by source-code story derivation."""

    type: str
    given: str
    when: str
    then: str
    id: str | None = None  # Source-code pipeline's identifier for this criterion
    ac_code: str | None = None  # Derived from `id` below when not explicitly provided
    l2_source_ref: str | None = None

    @model_validator(mode="after")
    def _derive_ac_code_from_id(self) -> SourceCodeAcceptanceCriterion:
        if self.ac_code is None and self.id is not None:
            self.ac_code = self.id
        return self


class SourceCodeStorySourceRef(BaseModel):
    """Source reference shape used by source-code backlog story payloads."""

    fragment_id: str
    source_id: str
    page: int
    bbox: UserStorySourceBBox


class SourceCodeStoryNFR(BaseModel):
    """Story-level non-functional requirement from source-code processing."""

    id: str
    category: str
    requirement: str


class SourceCodeStoryScreenASCIILayoutRef(BaseModel):
    """Reference to the ASCII layout section in an SRS file."""

    storage_key: str = ""
    srs_file: str = ""
    section: str = ""
    start_line: int | None = None
    end_line: int | None = None


class SourceCodeStoryScreen(BaseModel):
    """A screen (UI artifact) referenced by a user story."""

    artifact_id: str = ""
    screen_id: str = ""
    srs_file: str = ""
    ascii_layout_ref: SourceCodeStoryScreenASCIILayoutRef | None = None
    controls_touched: list[str] = Field(default_factory=list)
    events_covered: list[str] = Field(default_factory=list)


class SourceCodeStorySchema(BaseModel):
    """Source-code story payload derived from StorySchema in rfp pipeline schema."""

    model_config = ConfigDict(extra="forbid")

    user_story_id: str
    user_story_code: str
    module_id: str | None = None
    feature_id: str | None = None
    title: str
    as_a: str
    i_want_to: str
    so_that: str
    acceptance_criteria: list[SourceCodeAcceptanceCriterion] = Field(default_factory=list)
    nfrs: list[SourceCodeStoryNFR] | None = None
    technical_notes: str
    story_points: int = Field(..., ge=1, le=8)
    sources: list[SourceCodeStorySourceRef] | None = Field(default_factory=list)
    l2_sources: list[str] = Field(default_factory=list)
    screens: list[SourceCodeStoryScreen] | None = None


class UserStoryListItemResponse(BaseModel):
    """UserStory item used in list responses."""

    id: str
    user_story_code: str
    title: str
    description: str | None
    consensus: float
    status: UserStoryStatus

    version: int
    feature_id: str | None
    project_id: UUID | None = None
    as_a: str | None = None
    i_want_to: str | None = None
    so_that: str | None = None
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    nfrs: list[UserStoryNFR] = Field(default_factory=list)
    technical_notes: str | None = None
    story_points: int | None = None
    sources: list[UserStorySourceResponse] | None = Field(default_factory=list)
    l2_sources: list[str] = Field(default_factory=list)
    rfp_flagged_item: RFPFlaggedItem | None = None
    is_current: bool = True
    is_jira_synced: bool = False
    is_tap_synced: bool = False
    del_reason: str | None = None
    deleted_at: datetime | None = None
    source_file_count: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class UserStoryListResponse(BaseModel):
    """List user stories response wrapper."""

    total: int
    are_all_approved: bool
    skip: int
    limit: int
    items: list[UserStoryListItemResponse]


class ProjectUserStorySummaryResponse(BaseModel):
    """Project-level user story summary."""

    project_id: UUID
    total_user_stories: int
    ready_count: int
    needs_edit_count: int
    failed_count: int
    approved_count: int
    total_modules: int
    total_features: int
    jira_sync_count: int = 0
    tap_sync_count: int = 0


class UserStoryRegenerationRequest(BaseModel):
    """Request payload for regenerating user stories with human feedback."""

    feedback: str | None = None
    module_ids: list[str] | None = None
    feature_ids: list[str] | None = None
    user_story_ids: list[str] | None = None


class StorySpecificFeedbackInput(BaseModel):
    """A highlighted excerpt and targeted comment for a specific part of a user story."""

    selected_text: str = Field(
        ...,
        description="The exact text selected by the user inside the user story.",
        examples=["eliminating reconciliation delays caused by the absence of real-time linkage"],
    )
    selected_feedback: str = Field(
        ...,
        description="The reviewer's comment or instruction specific to the selected text.",
        examples=[
            "Change to target reconciliation delay reduction to under 2 minutes per transaction."
        ],
    )


class StoryFeedbackInput(BaseModel):
    """Per-story feedback item sent by the frontend for targeted patch regeneration."""

    user_story_id: str = Field(
        ...,
        description="UUID of the user story to be revised.",
        examples=["3fa85f64-5717-4562-b3fc-2c963f66afa6"],
    )
    overall_feedback: str | None = Field(
        default=None,
        description=(
            "Optional whole-story comment. Provide when the feedback applies to the story "
            "as a whole rather than a specific selection."
        ),
        examples=[
            "The acceptance criteria are too basic. Add an edge case for concurrent bindings."
        ],
    )
    specific_feedback: list[StorySpecificFeedbackInput] | None = Field(
        default=None,
        description=(
            "Optional list of inline selection-level comments. Each entry pairs a highlighted "
            "excerpt with a targeted instruction for the AI."
        ),
    )


class UserStoryRegenerationQueuedResponse(BaseModel):
    """Async task response for user story regeneration."""

    task_id: str = Field(
        description=(
            "Celery task UUID. Connect to "
            "`/ws/projects/{project_id}` "
            "for real-time progress updates."
        )
    )
    project_id: UUID
    source_ids: list[UUID]
    user_story_ids: list[str] = Field(
        default_factory=list,
        description=(
            "IDs of the user stories targeted by this task. Empty for full-project "
            "generation/regeneration; populated for feedback-driven patch requests so "
            "the frontend can correlate this `task_id` with the stories it patches — "
            "even across concurrent, overlapping requests."
        ),
    )
    status: str


class UserStoryDeleteRequest(BaseModel):
    """Optional request body for deleting a single user story."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(
        default=None,
        description="Required when the user story status is approved.",
    )


class UserStoryDeleteResponse(BaseModel):
    """Response after deleting a single user story."""

    user_story_id: str
    project_id: UUID
    is_current: bool
    del_reason: str | None = None
    deleted_at: datetime | None = None


class UserStoryDeleteByProjectResponse(BaseModel):
    """Response after deleting project-scoped user stories."""

    project_id: UUID
    deleted_count: int


# ── Tree-view schemas ──────────────────────────────────────────────────────


class UserStoryTreeNode(BaseModel):
    """Leaf node representing a single user story in the tree."""

    id: str
    user_story_code: str
    name: str
    status: UserStoryStatus
    incremental_change_type: str | None = None
    feedback_change_type: str | None = None
    is_jira_synced: bool = False
    is_tap_synced: bool = False
    source_ingestion_id: str | None = None


class FeatureTreeNode(BaseModel):
    """Feature node with its user story children."""

    id: str
    fea_code: str | None = None
    name: str
    description: str | None = None
    children: list[UserStoryTreeNode] = Field(default_factory=list)
    incremental_change_type: str | None = None
    feedback_change_type: str | None = None
    source_ingestion_id: str | None = None


class ModuleTreeNode(BaseModel):
    """Module node with its feature children."""

    id: str
    mod_code: str | None = None
    name: str
    description: str | None = None
    children: list[FeatureTreeNode] = Field(default_factory=list)
    incremental_change_type: str | None = None
    feedback_change_type: str | None = None
    source_ingestion_id: str | None = None


class SyncCandidateUserStoryNode(UserStoryTreeNode):
    """``UserStoryTreeNode`` plus ``version`` — lets the Sync Tray display the
    story's version straight from this tree, without a per-story detail
    fetch (avoids one extra request per story on tray open)."""

    version: int
    deleted_at: datetime | None = None


class SyncCandidateFeatureNode(FeatureTreeNode):
    """``FeatureTreeNode`` narrowed to ``SyncCandidateUserStoryNode`` children."""

    children: list[SyncCandidateUserStoryNode] = Field(default_factory=list)


class SyncCandidateModuleNode(ModuleTreeNode):
    """``ModuleTreeNode`` narrowed to ``SyncCandidateFeatureNode`` children."""

    children: list[SyncCandidateFeatureNode] = Field(default_factory=list)


class SyncCandidateTreeResponse(BaseModel):
    """Module → feature → user story tree pruned to approved, not-yet-synced stories."""

    sync_target: Literal["jira", "tap"]
    total_count: int
    items: list[SyncCandidateModuleNode] = Field(default_factory=list)


class UserStoryTreeResponse(BaseModel):
    """Full module 2192 feature 2192 user story tree for a project."""

    are_all_approved_for_us: bool
    are_all_approved_for_mod: bool
    are_all_approved_for_fea: bool
    items: list[ModuleTreeNode]


class UpdateBboxesRequest(BaseModel):
    """Request body for updating bboxes on a single user story."""

    sources: list[UserStorySourceResponse] | None = Field(default_factory=list, min_length=0)


class UpdateBboxesResponse(BaseModel):
    """Response after updating bboxes on a user story."""

    id: str
    project_id: UUID | None
    sources: list[UserStorySourceResponse] | None = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ProjectUserStoriesStatusChangedResponse(BaseModel):
    """Response after bulk-changing status for all project user stories."""

    project_id: UUID
    status: str
    updated_count: int


class ChangeStatusRequest(BaseModel):
    """Request body for changing a user story's status."""

    model_config = ConfigDict(extra="forbid")

    status: UserStoryStatus


class BulkStatusChangeRequest(BaseModel):
    """Request body for bulk-changing the status of specific user stories within a project."""

    model_config = ConfigDict(extra="forbid")

    user_story_ids: list[str] = Field(
        ...,
        min_length=1,
        description="Non-empty list of user story IDs to update.",
    )
    status: UserStoryStatus


class BulkStatusChangeResponse(BaseModel):
    """Response after bulk-changing the status of specific user stories by IDs."""

    project_id: UUID
    status: str
    updated_count: int
    user_story_ids: list[str]


class UserStoryStatusChangedResponse(BaseModel):
    """Response after a status change operation."""

    id: str
    status: UserStoryStatus
    project_id: UUID | None = None


class UserStorySyncFlagsUpdateRequest(BaseModel):
    """Partial-update payload for a user story's external sync flags.

    Only the supplied field(s) are changed — omit a field to leave it untouched.
    """

    model_config = ConfigDict(extra="forbid")

    is_jira_synced: bool | None = Field(
        default=None, description="Whether this user story has been synced to Jira"
    )
    is_tap_synced: bool | None = Field(
        default=None, description="Whether this user story has been synced to TAP"
    )

    @model_validator(mode="after")
    def validate_at_least_one_updatable_field(self) -> UserStorySyncFlagsUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided: is_jira_synced, is_tap_synced")
        return self


class UserStorySyncFlagsUpdatedResponse(BaseModel):
    """Response after updating a user story's external sync flags."""

    id: str
    is_jira_synced: bool
    is_tap_synced: bool
    project_id: UUID | None = None


class SourceFileInfo(BaseModel):
    """Source file summary embedded in user story detail."""

    id: str
    name: str
    type: str
    storage_key: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class UserStoryVersionResponse(BaseModel):
    """Point-in-time snapshot of a user story, captured just before an incremental update overwrote it."""

    id: str
    user_story_id: str
    feature_id: str | None = None
    project_id: UUID | None = None
    user_story_code: str = ""
    title: str = ""
    description: str | None = None
    consensus: float | None = None
    status: str | None = None
    version: int
    as_a: str | None = None
    i_want_to: str | None = None
    so_that: str | None = None
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    nfrs: list[UserStoryNFR] = Field(default_factory=list)
    technical_notes: str | None = None
    story_points: int | None = None
    justification: str | None = None
    incremental_change_type: str | None = None
    feedback_change_type: str | None = None
    sources: list[dict] = Field(default_factory=list)
    l2_sources: list[str] = Field(default_factory=list)
    is_jira_synced: bool = False
    is_tap_synced: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    snapshotted_at: datetime | None = None


class UserStoryDetailResponse(BaseModel):
    """Full user story detail including linked source files and fragments."""

    id: str
    user_story_code: str
    title: str
    description: str | None
    consensus: float
    status: UserStoryStatus
    version: int
    feature_id: str | None
    mfu_id: str | None = None
    mod_code: str | None = None
    project_id: UUID | None = None
    as_a: str | None = None
    i_want_to: str | None = None
    so_that: str | None = None
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    nfrs: list[UserStoryNFR] = Field(default_factory=list)
    technical_notes: str | None = None
    story_points: int | None = None
    justification: str | None = None
    incremental_change_type: str | None = None
    feedback_change_type: str | None = None
    sources: list[UserStorySourceResponse] | None = Field(default_factory=list)
    l2_sources: list[str] = Field(default_factory=list)
    screens: list[SourceCodeStoryScreen] | None = None
    rfp_flagged_item: RFPFlaggedItem | None = None
    text_diffs: TextDiffs = Field(default_factory=dict)
    is_current: bool = True
    is_jira_synced: bool = False
    is_tap_synced: bool = False
    del_reason: str | None = None
    deleted_at: datetime | None = None
    source_file_count: int
    source_files: list[SourceFileInfo]
    srs_evidence: list[SRSEvidenceInfo] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_previous_items: UserStoryVersionResponse | None = None
    updated_fields: list[str] = Field(default_factory=list)
