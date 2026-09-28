"""Internal domain models for module-feature workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TypedDict
from uuid import UUID

from app.core.constants import INITIAL_ENTITY_VERSION


class ModuleFeatureStatus(str, Enum):
    """Status enumeration for Module and Feature nodes.

    Represents the current approval/review status of modules and features
    within a project's user story analysis workflow.

    Attributes
    ----------
    READY : str
        Module/feature is ready for review (default, not yet reviewed).
    NEEDS_EDIT : str
        Module/feature has conflicting user stories or status and needs edits.
    FAILED : str
        Module/feature has been reviewed and rejected/failed.
    APPROVED : str
        Module/feature has been reviewed and approved.
    """

    READY = "ready"
    NEEDS_EDIT = "needs_edit"
    FAILED = "failed"
    APPROVED = "approved"


class ChangeType(str, Enum):
    """Tracks how an item was affected by an incremental or feedback pipeline run."""

    ADDED = "ADDED"
    UPDATED = "UPDATED"
    DELETE_SUGGESTED = "DELETE_SUGGESTED"


class RFPFlaggedItemDict(TypedDict):
    """Quality-gate flag pointing to a specific module/feature/story that needs RFP follow-up.

    Defined here (not in ``user_story_model``) so both Module/Feature and
    UserStory domain models can use it without a circular import — this
    module has no dependency on ``user_story_model``, while ``user_story_model``
    already imports ``ChangeType`` from here.
    """

    entity_id: str
    entity_type: str
    issue: str
    suggested_fix: str


@dataclass(slots=True)
class FunctionModel:
    """Domain model for a Function within a Feature."""

    name: str
    fun_code: str | None = None
    description: str | None = None
    func_src_ref: str | None = None


class FeatureSourceBBoxModel(TypedDict):
    """Bounding-box coordinates for one source fragment evidence block."""

    x: float
    y: float
    w: float
    h: float


class FeatureSourceBBoxEntryModel(TypedDict):
    """One fragment evidence entry on a specific source page."""

    fragment_id: str
    bbox: FeatureSourceBBoxModel


class FeatureSourcePageModel(TypedDict):
    """Collection of fragment evidence entries for a source page."""

    page: int
    bboxes: list[FeatureSourceBBoxEntryModel]


class FeatureSourceModel(TypedDict):
    """Source evidence attached to a feature."""

    source_id: str
    pages: list[FeatureSourcePageModel]


@dataclass(slots=True)
class FeatureModel:
    """Domain model for a Feature within a Module.

    Represents a single feature that belongs to a module. Features are
    the leaf nodes in the module-feature hierarchy and carry their own
    status independent from (though typically synchronized with) their
    parent module.

    Attributes
    ----------
    id : str
        Unique identifier for the feature.
    module_id : str
        ID of the parent module containing this feature.
    name : str
        Display name of the feature.
    description : str | None
        Detailed description of the feature's purpose and scope.
    project_id : UUID | None
        ID of the project this feature belongs to.
    fea_code : str | None
        Optional feature code or identifier from the source.
    status : ModuleFeatureStatus
        Current status of the feature (ready, needs_edit, failed, approved).
        Defaults to READY.
    """

    id: str
    project_id: UUID | None = None
    # SourceIngestion (Postgres) row that created this feature, or last
    # updated its content via feedback-driven regeneration.
    source_ingestion_id: str | None = None
    module_id: str = field(kw_only=True)
    name: str = field(kw_only=True)
    description: str | None = field(kw_only=True)
    fea_code: str | None = None
    # Feature-unit id (e.g. "MFU-001") the feature was derived from during
    # source-code spec extraction. None for RFP/manually-created features.
    mfu_id: str | None = None
    version: int = INITIAL_ENTITY_VERSION
    status: ModuleFeatureStatus = ModuleFeatureStatus.READY
    total_user_stories: int = 0
    justification: str | None = None
    incremental_change_type: ChangeType | None = None
    feedback_change_type: ChangeType | None = None
    generation_metadata: dict | None = None
    is_infrastructure: bool | None = None
    condensation_note: str | None = None
    is_jira_synced: bool = False
    is_tap_synced: bool = False
    functions: list[FunctionModel] = field(default_factory=list)
    sources: list[FeatureSourceModel] = field(default_factory=list)
    # Granular L2 source-reference IDs from the source-code pipeline (e.g.
    # "SRS::MFU-001::S5::EVENT-001") — distinct from ``sources`` above, which
    # is the RFP-document {source_id, pages} evidence shape.
    l2_sources: list[str] = field(default_factory=list)
    # Nested dict keyed by field name (and, for functions, fun_code) —
    # emitted directly by the LLM in this shape (see FeatureTextDiffs /
    # ModuleTextDiffs in app.schemas.rfp_pipeline_v2_graph_schema).
    text_diffs: dict = field(default_factory=dict)
    # Quality-gate flag from an incremental update run that failed its
    # correction loop (see IncrementalUpdateProcessorService.extract_rfp_flag_map).
    # None on a clean run / outside the incremental-update flow.
    rfp_flagged_item: RFPFlaggedItemDict | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None
    is_deleted: bool = False
    deletion_reason: str | None = None


@dataclass(slots=True)
class ModuleModel:
    """Domain model for a Module in the module-feature hierarchy.

    Represents a logical module containing multiple features. Modules
    are typically the intermediate level in the hierarchy between
    project user stories and individual features.

    Attributes
    ----------
    id : str
        Unique identifier for the module.
    name : str
        Display name of the module.
    description : str | None
        Detailed description of the module's purpose and scope.
    features : list[FeatureModel]
        List of features contained within this module.
    project_id : UUID | None
        ID of the project this module belongs to.
    mod_code : str | None
        Optional module code or identifier from the source.
    status : ModuleFeatureStatus
        Current status of the module (ready, needs_edit, failed, approved).
        This status is typically synchronized across all child features.
        Defaults to READY.
    """

    id: str
    name: str
    description: str | None
    features: list[FeatureModel]
    project_id: UUID | None = None
    # SourceIngestion (Postgres) row that created this module, or last
    # updated its content via feedback-driven regeneration.
    source_ingestion_id: str | None = None
    mod_code: str | None = None
    version: int = INITIAL_ENTITY_VERSION
    status: ModuleFeatureStatus = ModuleFeatureStatus.READY
    justification: str | None = None
    incremental_change_type: ChangeType | None = None
    feedback_change_type: ChangeType | None = None
    is_jira_synced: bool = False
    is_tap_synced: bool = False
    # Nested dict keyed by field name (and, for functions, fun_code) —
    # emitted directly by the LLM in this shape (see FeatureTextDiffs /
    # ModuleTextDiffs in app.schemas.rfp_pipeline_v2_graph_schema).
    text_diffs: dict = field(default_factory=dict)
    # Quality-gate flag from an incremental update run that failed its
    # correction loop (see IncrementalUpdateProcessorService.extract_rfp_flag_map).
    # None on a clean run / outside the incremental-update flow.
    rfp_flagged_item: RFPFlaggedItemDict | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None
    is_deleted: bool = False
    deletion_reason: str | None = None
