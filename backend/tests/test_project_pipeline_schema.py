"""Unit tests for ProjectPipelineItemResponse."""

from __future__ import annotations

from datetime import UTC, datetime
import uuid

from app.core.enums.source_ingestion_stage import SourceIngestionStage
from app.core.enums.source_ingestion_status import SourceIngestionStatus
from app.core.enums.source_type import SourceType
from app.schemas.project_pipeline_schema import ProjectPipelineItemResponse


def _base_kwargs(**overrides) -> dict:
    now = datetime.now(UTC)
    kwargs = {
        "id": uuid.uuid4(),
        "run_code": "RUN-1001",
        "project_id": uuid.uuid4(),
        "project_name": "Demo Project",
        "source_type": SourceType.SOURCE_CODE.value,
        "stages": [SourceIngestionStage.INGESTING_SOURCES.value],
        "status": SourceIngestionStatus.RUNNING.value,
        "created_at": now,
        "updated_at": now,
    }
    kwargs.update(overrides)
    return kwargs


class TestProjectPipelineItemResponseStages:
    def test_accepts_source_code_checkpoint_stage_values(self):
        response = ProjectPipelineItemResponse(**_base_kwargs())

        assert response.stages == [SourceIngestionStage.INGESTING_SOURCES.value]
        assert response.stages_display == ["Ingesting Sources"]

    def test_accepts_run_stage_values(self):
        response = ProjectPipelineItemResponse(
            **_base_kwargs(
                source_type=SourceType.RFP.value,
                stages=[
                    SourceIngestionStage.GENERATING_MODULE_FEATURE.value,
                    SourceIngestionStage.GENERATING_USER_STORY.value,
                ],
            )
        )

        assert response.stages == [
            SourceIngestionStage.GENERATING_MODULE_FEATURE.value,
            SourceIngestionStage.GENERATING_USER_STORY.value,
        ]
        assert response.stages_display == [
            "Generating module & feature",
            "Generating user story",
        ]

    def test_stages_display_falls_back_to_raw_value_for_unknown_stage(self):
        response = ProjectPipelineItemResponse(**_base_kwargs(stages=["some_unmapped_stage"]))

        assert response.stages_display == ["some_unmapped_stage"]


class TestProjectPipelineItemResponseStatusDisplay:
    def test_maps_status_value_to_human_readable_label(self):
        response = ProjectPipelineItemResponse(
            **_base_kwargs(status=SourceIngestionStatus.READY_FOR_REVIEW.value)
        )

        assert response.status_display == "Ready for review"
