"""API schemas for module-feature operations."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.constants import INITIAL_ENTITY_VERSION
from app.schemas.user_story_schema import RFPFlaggedItem, TextDiffs


class ModuleFeatureStatusEnum(str, Enum):
    """Status enumeration for Module and Feature nodes in API responses."""

    READY = "ready"
    NEEDS_EDIT = "needs_edit"
    FAILED = "failed"
    APPROVED = "approved"


class FunctionResponse(BaseModel):
    """Function response item."""

    fun_code: str | None = None
    name: str
    description: str | None = None
    func_src_ref: str | None = None


class FeatureSourceBBoxCoordinatesResponse(BaseModel):
    """Coordinates for a single bounding box attached to a feature source."""

    x: float
    y: float
    w: float
    h: float


class FeatureSourceBBoxEntryResponse(BaseModel):
    """One feature-source fragment and its bbox."""

    fragment_id: str
    bbox: FeatureSourceBBoxCoordinatesResponse


class FeatureSourcePageResponse(BaseModel):
    """Grouped feature-source bboxes for one page."""

    page: int
    bboxes: list[FeatureSourceBBoxEntryResponse]


class FeatureSourceResponse(BaseModel):
    """Source evidence for a feature."""

    source_id: str
    pages: list[FeatureSourcePageResponse]


class FeatureSourceFileResponse(BaseModel):
    """Resolved file metadata for one of a feature's cited sources."""

    id: UUID
    name: str
    type: str
    storage_key: str | None = None
    created_at: datetime
    updated_at: datetime


class FeatureVersionResponse(BaseModel):
    """Point-in-time snapshot of a feature, captured just before an incremental update overwrote it."""

    id: str
    feature_id: str
    module_id: str | None = None
    project_id: UUID | None = None
    fea_code: str | None = None
    mfu_id: str | None = None
    name: str = ""
    description: str | None = None
    version: int = INITIAL_ENTITY_VERSION
    status: ModuleFeatureStatusEnum = ModuleFeatureStatusEnum.READY
    functions: list[FunctionResponse] = Field(default_factory=list)
    sources: list[FeatureSourceResponse] = Field(default_factory=list)
    l2_sources: list[str] = Field(default_factory=list)
    justification: str | None = None
    incremental_change_type: str | None = None
    feedback_change_type: str | None = None
    is_infrastructure: bool | None = None
    condensation_note: str | None = None
    is_jira_synced: bool = False
    is_tap_synced: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    snapshotted_at: datetime | None = None


class FeatureResponse(BaseModel):
    """Feature response item."""

    id: str
    name: str
    description: str | None
    fea_code: str | None = None
    mfu_id: str | None = None
    version: int = INITIAL_ENTITY_VERSION
    status: ModuleFeatureStatusEnum = ModuleFeatureStatusEnum.READY
    total_user_stories: int = 0
    generation_metadata: dict | None = None
    incremental_change_type: str | None = None
    feedback_change_type: str | None = None
    is_infrastructure: bool | None = None
    condensation_note: str | None = None
    is_jira_synced: bool = False
    is_tap_synced: bool = False
    functions: list[FunctionResponse] = []
    sources: list[FeatureSourceResponse] = []
    source_files: list[FeatureSourceFileResponse] = Field(default_factory=list)
    l2_sources: list[str] = Field(default_factory=list)
    text_diffs: TextDiffs = Field(default_factory=dict)
    rfp_flagged_item: RFPFlaggedItem | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None
    last_previous_items: FeatureVersionResponse | None = None
    updated_fields: list[str] = Field(default_factory=list)


class ModuleVersionResponse(BaseModel):
    """Point-in-time snapshot of a module, captured just before an incremental update overwrote it."""

    id: str
    module_id: str
    project_id: UUID | None = None
    mod_code: str | None = None
    name: str = ""
    description: str | None = None
    version: int = INITIAL_ENTITY_VERSION
    status: ModuleFeatureStatusEnum = ModuleFeatureStatusEnum.READY
    justification: str | None = None
    incremental_change_type: str | None = None
    feedback_change_type: str | None = None
    is_jira_synced: bool = False
    is_tap_synced: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    snapshotted_at: datetime | None = None


class ModuleFeatureResponse(BaseModel):
    """Module response item with nested features."""

    id: str
    project_id: UUID | None = None
    mod_code: str | None = None
    name: str
    description: str | None
    version: int = INITIAL_ENTITY_VERSION
    status: ModuleFeatureStatusEnum = ModuleFeatureStatusEnum.READY
    is_jira_synced: bool = False
    is_tap_synced: bool = False
    text_diffs: TextDiffs = Field(default_factory=dict)
    rfp_flagged_item: RFPFlaggedItem | None = None
    features: list[FeatureResponse]
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None


class ModuleFeatureSingleResponse(BaseModel):
    """Single module response wrapper."""

    project_id: UUID | None = None
    module: ModuleFeatureResponse


class ModuleDetailResponse(BaseModel):
    """Module detail response item, without nested features."""

    id: str
    project_id: UUID | None = None
    mod_code: str | None = None
    name: str
    description: str | None
    version: int = INITIAL_ENTITY_VERSION
    status: ModuleFeatureStatusEnum = ModuleFeatureStatusEnum.READY
    incremental_change_type: str | None = None
    feedback_change_type: str | None = None
    is_jira_synced: bool = False
    is_tap_synced: bool = False
    text_diffs: TextDiffs = Field(default_factory=dict)
    rfp_flagged_item: RFPFlaggedItem | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None
    last_previous_items: ModuleVersionResponse | None = None
    updated_fields: list[str] = Field(default_factory=list)


class ModuleSingleResponse(BaseModel):
    """Single module response wrapper, without nested features."""

    project_id: UUID | None = None
    module: ModuleDetailResponse


class ModuleFeatureFeatureSingleResponse(BaseModel):
    """Single feature response wrapper."""

    project_id: UUID | None = None
    module_id: str
    mod_code: str | None = None
    feature: FeatureResponse


class ModuleFeatureListResponse(BaseModel):
    """List module response wrapper."""

    total: int
    skip: int
    limit: int
    items: list[ModuleFeatureListItemResponse]


class ModuleFeatureListItemResponse(BaseModel):
    """List item for module response without source context."""

    id: str
    mod_code: str | None = None
    name: str
    description: str | None
    version: int = INITIAL_ENTITY_VERSION
    status: ModuleFeatureStatusEnum = ModuleFeatureStatusEnum.READY
    is_jira_synced: bool = False
    is_tap_synced: bool = False
    total_features: int = 0
    total_user_stories: int = 0
    rfp_flagged_item: RFPFlaggedItem | None = None
    features: list[FeatureResponse]
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None


class FunctionTreeNode(BaseModel):
    """Function node in the module tree view."""

    fun_code: str | None = None
    name: str
    description: str | None = None
    func_src_ref: str | None = None


class FeatureTreeNode(BaseModel):
    """Feature node in the module tree view."""

    id: str
    fea_code: str | None = None
    mfu_id: str | None = None
    name: str
    description: str | None = None
    children: list[FunctionTreeNode] = []


class ModuleTreeNode(BaseModel):
    """Module node in the module tree view."""

    id: str
    mod_code: str | None = None
    name: str
    description: str | None = None
    children: list[FeatureTreeNode] = []


class ModuleTreeListResponse(BaseModel):
    """Response for the module tree list endpoint."""

    total: int
    items: list[ModuleTreeNode]


class ModuleFeatureRegenerationRequest(BaseModel):
    """Request payload for regenerating module/features with human feedback."""

    feedback: str = Field(min_length=1)
    module_ids: list[str] | None = None
    feature_ids: list[str] | None = None


class ModuleFeatureRegenerationQueuedResponse(BaseModel):
    """Async task response for module/feature regeneration."""

    task_id: str = Field(
        description=(
            "Celery task UUID. Connect to "
            "`/ws/projects/{project_id}` "
            "for real-time progress updates."
        )
    )
    project_id: UUID
    source_ids: list[UUID]
    status: str


class FeatureFeedbackSpecificItem(BaseModel):
    """A highlighted excerpt and targeted comment for a specific part of the feature/story."""

    selected_text: str = Field(min_length=1)
    selected_feedback: str = Field(min_length=1)


class FeatureFeedbackItem(BaseModel):
    """One feedback entry for a feature/MFU regeneration request.

    ``mod_code``/``mfu_id`` identify the target MFU directly — the request no
    longer pins a single ``feature_id``; the target Feature is resolved
    server-side from ``mod_code``/``mfu_id``. Feedback items are grouped by
    ``(mod_code, mfu_id)`` so one request can drive regeneration across
    several MFUs at once.
    """

    mod_code: str = Field(
        min_length=1, description='The MFU\'s parent module code (e.g. "MOD-ANCES").'
    )
    mfu_id: str = Field(min_length=1, description='The target MFU id (e.g. "MFU-001").')
    user_story_code: str | None = Field(
        default=None,
        description=(
            "Scopes this feedback to one user story, identified by its pipeline "
            '``user_story_code`` (e.g. "ANCES-001-F1-S2") — the same id the '
            "revise pipeline matches against; omit for feature-wide feedback."
        ),
    )
    overall_feedback: str | None = Field(default=None, min_length=1)
    specific_feedback: list[FeatureFeedbackSpecificItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_feedback_content(self) -> FeatureFeedbackItem:
        if not self.overall_feedback and not self.specific_feedback:
            raise ValueError("each feedback item requires overall_feedback or specific_feedback")
        return self


class FeatureRegenerationRequest(BaseModel):
    """Request payload for regenerating one or more feature MFUs with human feedback."""

    feedback_items: list[FeatureFeedbackItem] = Field(min_length=1)
    skip_processing: bool = Field(
        default=False,
        description=(
            "When true, bypasses the LLM regeneration call and loads a canned sample "
            "result instead — for local testing only."
        ),
    )


class FeatureRegenerationTarget(BaseModel):
    """One module/MFU pair queued for regeneration."""

    module_id: str
    mfu_id: str


class FeatureRegenerationQueuedResponse(BaseModel):
    """Async task response for a feedback-driven MFU regeneration."""

    task_id: str = Field(
        description=(
            "Celery task UUID. Connect to "
            "`/ws/projects/{project_id}` "
            "for real-time progress updates."
        )
    )
    project_id: UUID
    targets: list[FeatureRegenerationTarget]
    status: str


class ModuleFeatureStatusChangeRequest(BaseModel):
    """Request payload for changing module and feature status.

    This endpoint updates all modules and child features in a project to a new
    status. The update is atomic per matched module/feature subgraph.

    Attributes
    ----------
    status : ModuleFeatureStatusEnum
        New status to apply to all modules and all their features in a project.
        Valid values: 'ready', 'needs_edit', 'failed', 'approved'
    """

    status: ModuleFeatureStatusEnum = Field(
        description="New status for all modules and all their features in the project"
    )
    skip_processing: bool = Field(
        default=False,
        description=(
            "When true and status=approved, user story generation uses cached pipeline output "
            "instead of running full processing."
        ),
    )
    is_incremental: bool = Field(
        default=False,
        description=(
            "When true and status=approved, user story generation is skipped "
            "(incremental update flow handles it separately)."
        ),
    )


class ModuleFeatureStatusChangeResponse(BaseModel):
    """Response for module/feature status change operation.

    Indicates whether the status update operation succeeded.
    When status is approved, also includes the project_id, task_id and status.
    """

    project_id: UUID | None = Field(
        default=None, description="Project ID (only when status is approved)"
    )
    task_id: str | None = Field(
        default=None, description="Enqueued task ID (only when status is approved)"
    )
    status: str | None = Field(default=None, description="Resulting status after the operation")


class ModuleDeleteRequest(BaseModel):
    """Request payload for deleting a module and its features.

    ``reason`` is required when the module status is ``approved``.
    """

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(
        default=None, description="Required when the module status is approved."
    )


class ModuleDeleteResponse(BaseModel):
    """Response for a module delete operation."""

    module_id: str
    project_id: UUID
    is_deleted: bool
    deletion_reason: str | None = None
    deleted_at: datetime | None = None


class FeatureDeleteRequest(BaseModel):
    """Request payload for deleting a single feature.

    ``reason`` is required when the feature status is ``approved``.
    """

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(
        default=None, description="Required when the feature status is approved."
    )


class FeatureDeleteResponse(BaseModel):
    """Response for a feature delete operation."""

    feature_id: str
    module_id: str
    project_id: UUID
    is_deleted: bool
    deletion_reason: str | None = None
    deleted_at: datetime | None = None


class SyncFlagsUpdateRequest(BaseModel):
    """Partial-update payload for a module's or feature's external sync flags.

    Only the supplied field(s) are changed — omit a field to leave it untouched.
    """

    model_config = ConfigDict(extra="forbid")

    is_jira_synced: bool | None = Field(
        default=None, description="Whether this item has been synced to Jira"
    )
    is_tap_synced: bool | None = Field(
        default=None, description="Whether this item has been synced to TAP"
    )

    @model_validator(mode="after")
    def validate_at_least_one_updatable_field(self) -> SyncFlagsUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided: is_jira_synced, is_tap_synced")
        return self
