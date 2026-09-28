"""Unit tests for SourceIngestionResponse."""

from __future__ import annotations

from datetime import UTC, datetime
import uuid

from app.core.enums.source_ingestion_stage import SourceIngestionStage
from app.core.enums.source_ingestion_status import SourceIngestionStatus
from app.core.enums.source_type import SourceType
from app.schemas.source_ingestion_schema import SourceIngestionResponse


def _base_kwargs(**overrides) -> dict:
    now = datetime.now(UTC)
    kwargs = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "run_code": "RUN-1001",
        "source_type": SourceType.RFP.value,
        "stages": [SourceIngestionStage.GENERATING_MODULE_FEATURE.value],
        "status": SourceIngestionStatus.RUNNING.value,
        "description": None,
        "skip_processing": False,
        "tot_modules_from_global_artifact": 0,
        "tot_modules": 0,
        "tot_features": 0,
        "tot_user_stories": 0,
        "tot_modules_failed": 0,
        "tot_modules_updated": 0,
        "tot_features_updated": 0,
        "tot_user_stories_updated": 0,
        "tot_modules_deleted": 0,
        "tot_features_deleted": 0,
        "tot_user_stories_deleted": 0,
        "tot_modules_accepted": 0,
        "tot_modules_rejected": 0,
        "tot_features_accepted": 0,
        "tot_features_rejected": 0,
        "tot_user_stories_accepted": 0,
        "tot_user_stories_rejected": 0,
        "mod_fea_gen_started_at": None,
        "mod_fea_gen_completed_at": None,
        "user_story_gen_started_at": None,
        "user_story_gen_completed_at": None,
        "started_at": None,
        "completed_at": None,
        "created_at": now,
        "updated_at": now,
        "sources": [],
    }
    kwargs.update(overrides)
    return kwargs


class TestSourceIngestionResponseStagesDisplay:
    def test_maps_run_stage_values_to_human_readable_labels(self):
        response = SourceIngestionResponse(
            **_base_kwargs(
                stages=[
                    SourceIngestionStage.GENERATING_MODULE_FEATURE.value,
                    SourceIngestionStage.USER_STORY_READY_FOR_REVIEW.value,
                ]
            )
        )

        assert response.stages_display == [
            "Generating module & feature",
            "User story ready for review",
        ]

    def test_falls_back_to_raw_value_for_unknown_stage(self):
        response = SourceIngestionResponse(**_base_kwargs(stages=["some_unmapped_stage"]))

        assert response.stages_display == ["some_unmapped_stage"]


class TestSourceIngestionResponseStatusDisplay:
    def test_maps_status_value_to_human_readable_label(self):
        response = SourceIngestionResponse(
            **_base_kwargs(status=SourceIngestionStatus.READY_FOR_REVIEW.value)
        )

        assert response.status_display == "Ready for review"

    def test_falls_back_to_raw_value_for_unknown_status(self):
        response = SourceIngestionResponse(**_base_kwargs(status="some_unmapped_status"))

        assert response.status_display == "some_unmapped_status"
