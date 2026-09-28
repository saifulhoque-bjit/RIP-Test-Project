"""Pydantic schemas for the /projects/me/pipelines response.

A "pipeline" is a SourceIngestion viewed across every project owned by a
user — one row per uploaded batch, with ``run_code``, ``stages``, and
``status`` tracking its generation lifecycle, plus ``source_type`` since an
ingestion is always scoped to a single upload's source type.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, computed_field

from app.core.enums.source_ingestion_stage import SOURCE_INGESTION_STAGE_DISPLAY_LABELS
from app.core.enums.source_ingestion_status import (
    SOURCE_INGESTION_STATUS_DISPLAY_LABELS,
    SourceIngestionStatus,
)
from app.core.enums.source_type import SourceType


class ProjectPipelineItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_code: str
    project_id: UUID
    project_name: str | None = None
    source_type: SourceType
    stages: list[str]
    status: SourceIngestionStatus
    tot_modules: int = 0
    tot_features: int = 0
    tot_user_stories: int = 0
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[misc]
    @property
    def stages_display(self) -> list[str]:
        """Human-readable labels for the pipeline row's ``stages`` values."""
        return [SOURCE_INGESTION_STAGE_DISPLAY_LABELS.get(stage, stage) for stage in self.stages]

    @computed_field  # type: ignore[misc]
    @property
    def status_display(self) -> str:
        """Human-readable label for the pipeline row's current status."""
        return SOURCE_INGESTION_STATUS_DISPLAY_LABELS.get(self.status.value, self.status.value)


class ProjectPipelineListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[ProjectPipelineItemResponse]
    total: int
    skip: int
    limit: int
    has_next: bool
    has_previous: bool
