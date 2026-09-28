"""Domain models for point-in-time snapshots of Module, Feature, and UserStory nodes.

Each ``*VersionModel`` mirrors the fields of its source entity plus a
reference id (``module_id`` / ``feature_id`` / ``user_story_id``) pointing back
to the node the snapshot was taken from. A new snapshot row is written every
time an incremental update changes an existing entity, so multiple versions
can accumulate per entity over time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.core.constants import INITIAL_ENTITY_VERSION
from app.models.neo4j.module_feature_model import ChangeType, FunctionModel, ModuleFeatureStatus


@dataclass(slots=True)
class ModuleVersionModel:
    """Snapshot of a Module node's properties at a point in time."""

    id: str
    module_id: str
    project_id: UUID | None = None
    # SourceIngestion that produced this snapshotted version.
    source_ingestion_id: str | None = None
    mod_code: str | None = None
    name: str = ""
    description: str | None = None
    version: int = INITIAL_ENTITY_VERSION
    status: ModuleFeatureStatus = ModuleFeatureStatus.READY
    justification: str | None = None
    incremental_change_type: ChangeType | None = None
    feedback_change_type: ChangeType | None = None
    is_jira_synced: bool = False
    is_tap_synced: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    snapshotted_at: datetime | None = None


@dataclass(slots=True)
class FeatureVersionModel:
    """Snapshot of a Feature node's properties at a point in time."""

    id: str
    feature_id: str
    module_id: str | None = None
    project_id: UUID | None = None
    # SourceIngestion that produced this snapshotted version.
    source_ingestion_id: str | None = None
    fea_code: str | None = None
    mfu_id: str | None = None
    name: str = ""
    description: str | None = None
    version: int = INITIAL_ENTITY_VERSION
    status: ModuleFeatureStatus = ModuleFeatureStatus.READY
    functions: list[FunctionModel] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    l2_sources: list[str] = field(default_factory=list)
    justification: str | None = None
    incremental_change_type: ChangeType | None = None
    feedback_change_type: ChangeType | None = None
    is_infrastructure: bool | None = None
    condensation_note: str | None = None
    is_jira_synced: bool = False
    is_tap_synced: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    snapshotted_at: datetime | None = None


@dataclass(slots=True)
class UserStoryVersionModel:
    """Snapshot of a UserStory node's properties at a point in time."""

    id: str
    user_story_id: str
    feature_id: str | None = None
    project_id: UUID | None = None
    # SourceIngestion that produced this snapshotted version.
    source_ingestion_id: str | None = None
    user_story_code: str = ""
    title: str = ""
    description: str | None = None
    consensus: float | None = None
    status: str | None = None
    version: int = INITIAL_ENTITY_VERSION
    as_a: str | None = None
    i_want_to: str | None = None
    so_that: str | None = None
    acceptance_criteria: list[dict] = field(default_factory=list)
    nfrs: list[dict] = field(default_factory=list)
    technical_notes: str | None = None
    story_points: int | None = None
    justification: str | None = None
    incremental_change_type: ChangeType | None = None
    feedback_change_type: ChangeType | None = None
    sources: list[dict] = field(default_factory=list)
    l2_sources: list[str] = field(default_factory=list)
    is_jira_synced: bool = False
    is_tap_synced: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    snapshotted_at: datetime | None = None
