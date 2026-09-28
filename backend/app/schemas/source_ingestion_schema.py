"""Pydantic schemas for SourceIngestion list APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, computed_field

from app.core.enums.source_ingestion_stage import SOURCE_INGESTION_STAGE_DISPLAY_LABELS
from app.core.enums.source_ingestion_status import SOURCE_INGESTION_STATUS_DISPLAY_LABELS


class SourceIngestionSourceResponse(BaseModel):
    """Compact source payload nested under a source ingestion."""

    id: UUID
    original_name: str
    status: str
    file_type: str
    upload_type: str
    file_size_bytes: int
    storage_key: str | None
    created_at: datetime


class SourceIngestionResponse(BaseModel):
    """Source-ingestion row with nested related sources."""

    id: UUID
    project_id: UUID
    run_code: str
    source_type: str
    stages: list[str]
    status: str
    description: str | None
    skip_processing: bool
    entity_json: dict[str, Any] | None = None
    no_changes_explanation: str | None = None
    errors: list[str] = []
    tot_modules_from_global_artifact: int
    tot_modules: int
    tot_features: int
    tot_user_stories: int
    tot_modules_failed: int
    tot_modules_updated: int
    tot_features_updated: int
    tot_user_stories_updated: int
    tot_modules_deleted: int
    tot_features_deleted: int
    tot_user_stories_deleted: int
    tot_modules_accepted: int
    tot_modules_rejected: int
    tot_features_accepted: int
    tot_features_rejected: int
    tot_user_stories_accepted: int
    tot_user_stories_rejected: int
    mod_fea_gen_started_at: datetime | None
    mod_fea_gen_completed_at: datetime | None
    user_story_gen_started_at: datetime | None
    user_story_gen_completed_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    sources: list[SourceIngestionSourceResponse]

    @computed_field  # type: ignore[misc]
    @property
    def stages_display(self) -> list[str]:
        """Human-readable labels for the ingestion's ``stages`` values."""
        return [SOURCE_INGESTION_STAGE_DISPLAY_LABELS.get(stage, stage) for stage in self.stages]

    @computed_field  # type: ignore[misc]
    @property
    def status_display(self) -> str:
        """Human-readable label for the ingestion's current lifecycle status."""
        return SOURCE_INGESTION_STATUS_DISPLAY_LABELS.get(self.status, self.status)


class SourceIngestionListResponse(BaseModel):
    """Paginated list response for source ingestions."""

    items: list[SourceIngestionResponse]
    total: int
    skip: int
    limit: int
