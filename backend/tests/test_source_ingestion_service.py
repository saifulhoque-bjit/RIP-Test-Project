"""Unit tests for SourceIngestionService."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch
import uuid

import pytest

from app.core.constants import SOURCE_STATUS_FAILED
from app.core.enums.activity_type import ActivityType
from app.core.enums.notification_type import NotificationType
from app.core.enums.source_ingestion_stage import SourceIngestionStage
from app.core.enums.source_ingestion_status import SourceIngestionStatus
from app.core.enums.source_type import SourceType
from app.core.exceptions import NotFoundError
from app.core.messages import (
    MSG_ACTIVITY_USER_STORIES_APPROVED,
    MSG_SOURCE_INGESTION_STALE_AUTO_FAILED,
    SUMMARY_ACTIVITY_USER_STORIES_APPROVED,
)
from app.models.postgres.source_ingestion_model import SourceIngestion
from app.services.source_ingestion_service import SourceIngestionService
from tests.conftest import make_project, make_source


@pytest.fixture(autouse=True)
def _mock_approval_notify():
    """Prevent the user-stories-approved notify path from opening a real
    UnitOfWork or hitting Redis. ``record_activity``/``publish_notification``
    are imported lazily inside ``_notify_user_stories_approved``, so they
    must be patched at their definition module, not at
    ``app.services.source_ingestion_service``.
    """
    with (
        patch("app.services.activity_log_service.record_activity") as mock_record,
        patch("app.services.notification_service.publish_notification") as mock_publish,
    ):
        yield SimpleNamespace(record_activity=mock_record, publish_notification=mock_publish)


def _make_ingestion(**overrides) -> MagicMock:
    ingestion = MagicMock()
    ingestion.id = overrides.get("id", uuid.uuid4())
    ingestion.run_code = overrides.get("run_code", "RUN-1001")
    ingestion.project_id = overrides.get("project_id", uuid.uuid4())
    project = MagicMock()
    project.name = overrides.get("project_name", "Demo Project")
    ingestion.project = overrides.get("project", project)
    ingestion.source_type = overrides.get("source_type", SourceType.RFP.value)
    ingestion.stages = overrides.get("stages", [SourceIngestionStage.GENERATING_MODULE_FEATURE.value])
    ingestion.status = overrides.get("status", SourceIngestionStatus.RUNNING.value)
    ingestion.tot_modules = overrides.get("tot_modules", 0)
    ingestion.tot_features = overrides.get("tot_features", 0)
    ingestion.tot_user_stories = overrides.get("tot_user_stories", 0)
    ingestion.created_at = overrides.get("created_at", datetime.now(UTC))
    ingestion.updated_at = overrides.get("updated_at", datetime.now(UTC))
    return ingestion


class TestListSourceIngestions:
    def test_returns_project_scoped_paginated_ingestions_with_nested_sources(self, uow):
        project = make_project()
        now = datetime.now(UTC)
        ingestion = SourceIngestion(
            id=uuid.uuid4(),
            project_id=project.id,
            run_code="RUN-1001",
            source_type=SourceType.RFP.value,
            stages=[],
            errors=[],
            status="running",
            description="batch",
            skip_processing=False,
            tot_modules_from_global_artifact=0,
            tot_modules=0,
            tot_features=0,
            tot_user_stories=0,
            tot_modules_failed=0,
            tot_modules_updated=0,
            tot_features_updated=0,
            tot_user_stories_updated=0,
            tot_modules_deleted=0,
            tot_features_deleted=0,
            tot_user_stories_deleted=0,
            tot_modules_accepted=0,
            tot_modules_rejected=0,
            tot_features_accepted=0,
            tot_features_rejected=0,
            tot_user_stories_accepted=0,
            tot_user_stories_rejected=0,
            mod_fea_gen_started_at=None,
            mod_fea_gen_completed_at=None,
            user_story_gen_started_at=None,
            user_story_gen_completed_at=None,
            started_at=None,
            completed_at=None,
            created_at=now,
            updated_at=now,
        )

        source = make_source(project_id=project.id)
        source.source_ingestion_id = ingestion.id

        uow.projects.get_by_uuid.return_value = project
        uow.source_ingestions.get_paginated.return_value = ([ingestion], 1)
        uow.sources.get_by_ingestion_ids.return_value = [source]

        result = SourceIngestionService().list_source_ingestions(
            project_id=project.id,
            skip=0,
            limit=20,
            status="running",
            source_type=SourceType.RFP.value,
            uow=uow,
        )

        assert result.total == 1
        assert result.skip == 0
        assert result.limit == 20
        assert len(result.items) == 1
        assert result.items[0].id == ingestion.id
        assert result.items[0].mod_fea_gen_started_at is None
        assert result.items[0].user_story_gen_completed_at is None
        assert result.items[0].errors == []
        assert len(result.items[0].sources) == 1
        assert result.items[0].sources[0].id == source.id
        assert result.items[0].sources[0].file_size_bytes == source.file_size_bytes

    def test_raises_when_project_not_found(self, uow):
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = None

        with pytest.raises(NotFoundError, match="Project"):
            SourceIngestionService().list_source_ingestions(
                project_id=project_id,
                skip=0,
                limit=20,
                status=None,
                source_type=None,
                uow=uow,
            )


class TestListIngestionsForUser:
    def test_maps_owner_ingestions_to_paginated_items_with_source_type(self, uow):
        owner_id = uuid.uuid4()
        ingestion = _make_ingestion(
            source_type=SourceType.SOURCE_CODE.value,
            stages=[SourceIngestionStage.GENERATING_MODULE_FEATURE.value, SourceIngestionStage.GENERATING_USER_STORY.value],
            status=SourceIngestionStatus.READY_FOR_REVIEW.value,
            project_name="Alpha",
        )
        uow.source_ingestions.list_by_owner_paginated.return_value = ([ingestion], 3)

        result = SourceIngestionService().list_ingestions_for_user(
            uow=uow,
            owner_id=owner_id,
            skip=0,
            limit=2,
        )

        uow.source_ingestions.list_by_owner_paginated.assert_called_once_with(
            owner_id=owner_id,
            skip=0,
            limit=2,
            status=None,
            source_type=None,
            search=None,
        )
        assert result == {
            "items": [
                {
                    "id": str(ingestion.id),
                    "run_code": ingestion.run_code,
                    "project_id": str(ingestion.project_id),
                    "project_name": "Alpha",
                    "source_type": SourceType.SOURCE_CODE.value,
                    "stages": [
                        SourceIngestionStage.GENERATING_MODULE_FEATURE.value,
                        SourceIngestionStage.GENERATING_USER_STORY.value,
                    ],
                    "status": SourceIngestionStatus.READY_FOR_REVIEW.value,
                    "tot_modules": 0,
                    "tot_features": 0,
                    "tot_user_stories": 0,
                    "created_at": ingestion.created_at.isoformat(),
                    "updated_at": ingestion.updated_at.isoformat(),
                }
            ],
            "total": 3,
            "skip": 0,
            "limit": 2,
            "has_next": True,
            "has_previous": False,
        }

    def test_returns_empty_list_when_no_ingestions(self, uow):
        owner_id = uuid.uuid4()
        uow.source_ingestions.list_by_owner_paginated.return_value = ([], 0)

        result = SourceIngestionService().list_ingestions_for_user(
            uow=uow,
            owner_id=owner_id,
            skip=0,
            limit=20,
        )

        assert result == {
            "items": [],
            "total": 0,
            "skip": 0,
            "limit": 20,
            "has_next": False,
            "has_previous": False,
        }


class TestAddStageBySourceIds:
    def test_adds_stage_to_every_distinct_ingestion(self, uow):
        project = make_project()
        ingestion_id_a = uuid.uuid4()
        ingestion_id_b = uuid.uuid4()
        source_a = make_source(project_id=project.id)
        source_a.source_ingestion_id = ingestion_id_a
        source_b = make_source(project_id=project.id)
        source_b.source_ingestion_id = ingestion_id_b

        uow.sources.get_many_by_uuids.return_value = [source_a, source_b]

        SourceIngestionService.add_stage_by_source_ids(
            uow, [source_a.id, source_b.id], SourceIngestionStage.GENERATING_MODULE_FEATURE
        )

        actual_ingestion_ids = {
            call.args[0] for call in uow.source_ingestions.add_stage.call_args_list
        }
        assert actual_ingestion_ids == {ingestion_id_a, ingestion_id_b}
        for call in uow.source_ingestions.add_stage.call_args_list:
            assert call.args[1] == SourceIngestionStage.GENERATING_MODULE_FEATURE.value

    def test_skips_sources_without_an_ingestion(self, uow):
        project = make_project()
        source = make_source(project_id=project.id)
        source.source_ingestion_id = None
        uow.sources.get_many_by_uuids.return_value = [source]

        SourceIngestionService.add_stage_by_source_ids(uow, [source.id], SourceIngestionStage.GENERATING_USER_STORY)

        uow.source_ingestions.add_stage.assert_not_called()

    def test_returns_the_ingestion_ids_it_tagged(self, uow):
        project = make_project()
        ingestion_id = uuid.uuid4()
        source = make_source(project_id=project.id)
        source.source_ingestion_id = ingestion_id
        uow.sources.get_many_by_uuids.return_value = [source]

        result = SourceIngestionService.add_stage_by_source_ids(
            uow, [source.id], SourceIngestionStage.GENERATING_MODULE_FEATURE
        )

        assert result == {ingestion_id}

    def test_returns_empty_set_for_empty_source_ids(self, uow):
        result = SourceIngestionService.add_stage_by_source_ids(uow, [], SourceIngestionStage.GENERATING_MODULE_FEATURE)

        assert result == set()
        uow.sources.get_many_by_uuids.assert_not_called()

    def test_accepts_source_code_stage_checkpoint_values(self, uow):
        """The source-code pipeline's own checkpoint values tag the same
        stages array via the same code path as the RFP-family values —
        both are SourceIngestionStage members."""
        project = make_project()
        ingestion_id = uuid.uuid4()
        source = make_source(project_id=project.id)
        source.source_ingestion_id = ingestion_id
        uow.sources.get_many_by_uuids.return_value = [source]

        SourceIngestionService.add_stage_by_source_ids(
            uow, [source.id], SourceIngestionStage.INGESTING_SOURCES
        )

        uow.source_ingestions.add_stage.assert_called_once_with(
            ingestion_id, SourceIngestionStage.INGESTING_SOURCES.value
        )


class TestRecordRegenerationFeedback:
    def test_tags_and_stores_entity_json_on_existing_ingestion(self, uow):
        project = make_project()
        ingestion_id = uuid.uuid4()
        source = make_source(project_id=project.id)
        source.source_ingestion_id = ingestion_id
        uow.sources.get_many_by_uuids.return_value = [source]

        SourceIngestionService.record_regeneration_feedback(
            uow,
            project_id=project.id,
            source_ids=[source.id],
            stages=[SourceIngestionStage.GENERATING_MODULE_FEATURE],
            entity_json={
                "feedback": "Refine module grouping",
                "module_ids": ["MOD-1"],
                "feature_ids": ["FEA-1"],
            },
        )

        uow.source_ingestions.add_stage.assert_called_once_with(
            ingestion_id, SourceIngestionStage.GENERATING_MODULE_FEATURE.value
        )
        uow.source_ingestions.update_fields.assert_called_once_with(
            ingestion_id,
            entity_json={
                "feedback": "Refine module grouping",
                "module_ids": ["MOD-1"],
                "feature_ids": ["FEA-1"],
            },
        )
        uow.source_ingestions.create_ingestion.assert_not_called()

    def test_creates_standalone_ingestion_when_no_source_linked_ingestion_exists(self, uow):
        project_id = uuid.uuid4()

        SourceIngestionService.record_regeneration_feedback(
            uow,
            project_id=project_id,
            source_ids=[],
            stages=[SourceIngestionStage.GENERATING_MODULE_FEATURE],
            entity_json={"feedback": "Refine module grouping"},
        )

        uow.source_ingestions.update_fields.assert_not_called()
        uow.source_ingestions.create_ingestion.assert_called_once_with(
            project_id=project_id,
            source_type=SourceType.REQUIREMENT_UPDATE.value,
            status=SourceIngestionStatus.COMPLETED.value,
            stages=[SourceIngestionStage.GENERATING_MODULE_FEATURE.value],
            entity_json={"feedback": "Refine module grouping"},
        )

    def test_creates_standalone_ingestion_with_all_stages_when_multiple_stages_given(self, uow):
        project_id = uuid.uuid4()

        SourceIngestionService.record_regeneration_feedback(
            uow,
            project_id=project_id,
            source_ids=[],
            stages=[SourceIngestionStage.GENERATING_MODULE_FEATURE, SourceIngestionStage.GENERATING_USER_STORY],
            entity_json={"feedback": "Refine acceptance criteria"},
        )

        uow.source_ingestions.create_ingestion.assert_called_once_with(
            project_id=project_id,
            source_type=SourceType.REQUIREMENT_UPDATE.value,
            status=SourceIngestionStatus.COMPLETED.value,
            stages=[SourceIngestionStage.GENERATING_MODULE_FEATURE.value, SourceIngestionStage.GENERATING_USER_STORY.value],
            entity_json={"feedback": "Refine acceptance criteria"},
        )


class TestTryCompleteOpenFeedbackOrIncrementalIngestions:
    @pytest.mark.asyncio
    async def test_completes_ingestion_with_no_pending_changes(self, uow, _mock_approval_notify):
        project_id = uuid.uuid4()
        owner_id = uuid.uuid4()
        ingestion = _make_ingestion(
            project_id=project_id,
            source_type=SourceType.REQUIREMENT_UPDATE.value,
            status=SourceIngestionStatus.READY_FOR_REVIEW.value,
        )
        ingestion.tot_modules_accepted = 2
        ingestion.tot_features_accepted = 3
        ingestion.tot_user_stories_accepted = 1
        ingestion.tot_modules_rejected = 0
        ingestion.tot_features_rejected = 1
        ingestion.tot_user_stories_rejected = 0
        uow.source_ingestions.list_open_feedback_or_incremental_by_project.return_value = [
            ingestion
        ]
        uow.projects.get_by_uuid.return_value = MagicMock(name="Demo Project", owner_id=owner_id)
        mf_repo = MagicMock()
        mf_repo.count_pending_changes_by_ingestion = AsyncMock(return_value=0)
        us_repo = MagicMock()
        us_repo.count_pending_changes_by_ingestion = AsyncMock(return_value=0)

        await SourceIngestionService.try_complete_open_feedback_or_incremental_ingestions(
            uow, project_id, mf_repo, us_repo, actor_user_id=None
        )

        uow.source_ingestions.update_fields.assert_called_once_with(
            ingestion.id,
            status=SourceIngestionStatus.COMPLETED.value,
            completed_at=ANY,
        )
        _mock_approval_notify.record_activity.assert_called_once_with(
            project_id=project_id,
            activity_type=ActivityType.REQUIREMENT_UPDATE_REVIEW_COMPLETED,
            summary=ANY,
            message=ANY,
            actor_user_id=None,
            data={"total_accepted": 6, "total_rejected": 1},
        )
        _mock_approval_notify.publish_notification.assert_called_once_with(
            user_id=owner_id,
            title=ANY,
            message=ANY,
            notification_type=NotificationType.SUCCESS,
            data={
                "project_id": str(project_id),
                "total_accepted": 6,
                "total_rejected": 1,
            },
        )

    @pytest.mark.asyncio
    async def test_leaves_ingestion_open_while_changes_are_still_pending(self, uow):
        project_id = uuid.uuid4()
        ingestion = _make_ingestion(
            project_id=project_id,
            source_type=SourceType.REQUIREMENT_UPDATE.value,
            status=SourceIngestionStatus.READY_FOR_REVIEW.value,
        )
        uow.source_ingestions.list_open_feedback_or_incremental_by_project.return_value = [ingestion]
        mf_repo = MagicMock()
        mf_repo.count_pending_changes_by_ingestion = AsyncMock(return_value=1)
        us_repo = MagicMock()
        us_repo.count_pending_changes_by_ingestion = AsyncMock(return_value=0)

        await SourceIngestionService.try_complete_open_feedback_or_incremental_ingestions(
            uow, project_id, mf_repo, us_repo
        )

        uow.source_ingestions.update_fields.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_open_ingestions_is_a_no_op(self, uow):
        project_id = uuid.uuid4()
        uow.source_ingestions.list_open_feedback_or_incremental_by_project.return_value = []
        mf_repo = MagicMock()
        us_repo = MagicMock()

        await SourceIngestionService.try_complete_open_feedback_or_incremental_ingestions(
            uow, project_id, mf_repo, us_repo
        )

        uow.source_ingestions.update_fields.assert_not_called()
        mf_repo.count_pending_changes_by_ingestion.assert_not_called()
        us_repo.count_pending_changes_by_ingestion.assert_not_called()


class TestTryCompleteGenerationIngestion:
    @pytest.mark.asyncio
    async def test_no_ready_for_review_generation_ingestion_is_a_no_op(self, uow):
        project_id = uuid.uuid4()
        uow.source_ingestions.get_ready_for_review_generation_ingestion.return_value = None
        us_repo = MagicMock()

        await SourceIngestionService.try_complete_generation_ingestion(uow, project_id, us_repo)

        uow.source_ingestions.update_fields.assert_not_called()
        us_repo.are_all_user_stories_approved.assert_not_called()

    @pytest.mark.asyncio
    async def test_unresolved_feedback_or_incremental_blocks_completion(self, uow):
        project_id = uuid.uuid4()
        ingestion = _make_ingestion(
            project_id=project_id, status=SourceIngestionStatus.READY_FOR_REVIEW.value
        )
        uow.source_ingestions.get_ready_for_review_generation_ingestion.return_value = ingestion
        uow.source_ingestions.has_unresolved_feedback_or_incremental.return_value = True
        us_repo = MagicMock()

        await SourceIngestionService.try_complete_generation_ingestion(uow, project_id, us_repo)

        uow.source_ingestions.update_fields.assert_not_called()
        us_repo.are_all_user_stories_approved.assert_not_called()

    @pytest.mark.asyncio
    async def test_not_all_user_stories_approved_blocks_completion(self, uow):
        project_id = uuid.uuid4()
        ingestion = _make_ingestion(
            project_id=project_id, status=SourceIngestionStatus.READY_FOR_REVIEW.value
        )
        uow.source_ingestions.get_ready_for_review_generation_ingestion.return_value = ingestion
        uow.source_ingestions.has_unresolved_feedback_or_incremental.return_value = False
        us_repo = MagicMock()
        us_repo.are_all_user_stories_approved = AsyncMock(return_value=False)

        await SourceIngestionService.try_complete_generation_ingestion(uow, project_id, us_repo)

        uow.source_ingestions.update_fields.assert_not_called()

    @pytest.mark.asyncio
    async def test_completes_when_fully_approved_and_no_unresolved_feedback(self, uow):
        project_id = uuid.uuid4()
        ingestion = _make_ingestion(
            project_id=project_id, status=SourceIngestionStatus.READY_FOR_REVIEW.value
        )
        uow.projects.get_by_uuid.return_value = make_project()
        uow.source_ingestions.get_ready_for_review_generation_ingestion.return_value = ingestion
        uow.source_ingestions.has_unresolved_feedback_or_incremental.return_value = False
        us_repo = MagicMock()
        us_repo.are_all_user_stories_approved = AsyncMock(return_value=True)
        us_repo.count_approved_user_stories = AsyncMock(return_value=7)

        await SourceIngestionService.try_complete_generation_ingestion(uow, project_id, us_repo)

        uow.source_ingestions.update_fields.assert_called_once_with(
            ingestion.id,
            status=SourceIngestionStatus.COMPLETED.value,
            completed_at=ANY,
        )

    @pytest.mark.asyncio
    async def test_not_all_approved_does_not_notify(self, uow, _mock_approval_notify):
        project_id = uuid.uuid4()
        ingestion = _make_ingestion(
            project_id=project_id, status=SourceIngestionStatus.READY_FOR_REVIEW.value
        )
        uow.source_ingestions.get_ready_for_review_generation_ingestion.return_value = ingestion
        uow.source_ingestions.has_unresolved_feedback_or_incremental.return_value = False
        us_repo = MagicMock()
        us_repo.are_all_user_stories_approved = AsyncMock(return_value=False)

        await SourceIngestionService.try_complete_generation_ingestion(uow, project_id, us_repo)

        us_repo.count_approved_user_stories.assert_not_called()
        _mock_approval_notify.record_activity.assert_not_called()
        _mock_approval_notify.publish_notification.assert_not_called()

    @pytest.mark.asyncio
    async def test_completion_records_activity_and_notifies_owner_and_members(
        self, uow, _mock_approval_notify
    ):
        project_id = uuid.uuid4()
        owner_id = uuid.uuid4()
        member_id = uuid.uuid4()
        actor_id = uuid.uuid4()
        ingestion = _make_ingestion(
            project_id=project_id, status=SourceIngestionStatus.READY_FOR_REVIEW.value
        )
        project = SimpleNamespace(owner_id=owner_id, name="My Project")
        uow.projects.get_by_uuid.return_value = project
        # The owner also holds a membership row — must be notified only once.
        uow.project_members.list_by_project.return_value = [
            SimpleNamespace(user_id=member_id),
            SimpleNamespace(user_id=owner_id),
        ]
        uow.source_ingestions.get_ready_for_review_generation_ingestion.return_value = ingestion
        uow.source_ingestions.has_unresolved_feedback_or_incremental.return_value = False
        us_repo = MagicMock()
        us_repo.are_all_user_stories_approved = AsyncMock(return_value=True)
        us_repo.count_approved_user_stories = AsyncMock(return_value=7)

        await SourceIngestionService.try_complete_generation_ingestion(
            uow, project_id, us_repo, actor_user_id=actor_id
        )

        _mock_approval_notify.record_activity.assert_called_once_with(
            project_id=project_id,
            activity_type=ActivityType.USER_STORIES_APPROVED,
            summary=SUMMARY_ACTIVITY_USER_STORIES_APPROVED,
            message=MSG_ACTIVITY_USER_STORIES_APPROVED.format(total_user_stories=7),
            actor_user_id=actor_id,
            data={"total_user_stories": 7},
        )

        notified_user_ids = {
            call.kwargs["user_id"]
            for call in _mock_approval_notify.publish_notification.call_args_list
        }
        assert notified_user_ids == {owner_id, member_id}
        assert _mock_approval_notify.publish_notification.call_count == 2
        for call in _mock_approval_notify.publish_notification.call_args_list:
            assert call.kwargs["message"] == '7 user story(ies) approved in "My Project".'
            assert call.kwargs["notification_type"] == NotificationType.SUCCESS


class TestIsStale:
    def test_source_code_is_never_auto_fail_stale_even_after_100_hours(self):
        # source_code's total duration is unbounded (uncapped module count,
        # sequential per-module chain) — is_stale must never flag it,
        # regardless of how long it's been running. See
        # is_advisory_stale_source_code for the non-mutating check instead.
        now = datetime.now(UTC)
        ingestion = SourceIngestion(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            source_type=SourceType.SOURCE_CODE.value,
            started_at=now - timedelta(hours=100),
        )

        assert SourceIngestionService.is_stale(ingestion, now=now) is False

    def test_rfp_module_feature_stage_not_yet_stale(self):
        now = datetime.now(UTC)
        ingestion = SourceIngestion(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            source_type=SourceType.RFP.value,
            mod_fea_gen_started_at=now - timedelta(minutes=30),
        )

        assert SourceIngestionService.is_stale(ingestion, now=now) is False

    def test_rfp_module_feature_stage_stale(self):
        now = datetime.now(UTC)
        ingestion = SourceIngestion(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            source_type=SourceType.RFP.value,
            mod_fea_gen_started_at=now - timedelta(hours=6),
            mod_fea_gen_completed_at=None,
        )

        assert SourceIngestionService.is_stale(ingestion, now=now) is True

    def test_rfp_module_feature_completed_falls_back_to_started_at(self):
        now = datetime.now(UTC)
        ingestion = SourceIngestion(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            source_type=SourceType.RFP.value,
            started_at=now - timedelta(minutes=10),
            mod_fea_gen_started_at=now - timedelta(hours=6),
            mod_fea_gen_completed_at=now - timedelta(hours=5),
        )

        # The module/feature stage already finished, so staleness now
        # measures from the ingestion's own started_at, not the completed
        # stage's start time — otherwise a long-finished stage would look
        # permanently stale.
        assert SourceIngestionService.is_stale(ingestion, now=now) is False

    def test_rfp_user_story_stage_stale(self):
        now = datetime.now(UTC)
        ingestion = SourceIngestion(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            source_type=SourceType.RFP.value,
            mod_fea_gen_started_at=now - timedelta(hours=10),
            mod_fea_gen_completed_at=now - timedelta(hours=9),
            user_story_gen_started_at=now - timedelta(hours=6),
            user_story_gen_completed_at=None,
        )

        assert SourceIngestionService.is_stale(ingestion, now=now) is True

    def test_still_in_initial_upload_stage_uses_started_at(self):
        now = datetime.now(UTC)
        ingestion = SourceIngestion(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            source_type=SourceType.RFP.value,
            started_at=now - timedelta(hours=4),
        )

        assert SourceIngestionService.is_stale(ingestion, now=now) is True

    def test_no_reference_time_is_never_stale(self):
        now = datetime.now(UTC)
        ingestion = SourceIngestion(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            source_type=SourceType.RFP.value,
        )

        assert SourceIngestionService.is_stale(ingestion, now=now) is False


class TestIsAdvisoryStaleSourceCode:
    def test_false_for_non_source_code(self):
        now = datetime.now(UTC)
        ingestion = SourceIngestion(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            source_type=SourceType.RFP.value,
            started_at=now - timedelta(hours=100),
        )

        assert SourceIngestionService.is_advisory_stale_source_code(ingestion, now=now) is False

    def test_false_within_the_advisory_ceiling(self):
        now = datetime.now(UTC)
        ingestion = SourceIngestion(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            source_type=SourceType.SOURCE_CODE.value,
            started_at=now - timedelta(hours=30),
        )

        assert SourceIngestionService.is_advisory_stale_source_code(ingestion, now=now) is False

    def test_true_past_the_advisory_ceiling(self):
        now = datetime.now(UTC)
        ingestion = SourceIngestion(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            source_type=SourceType.SOURCE_CODE.value,
            started_at=now - timedelta(hours=55),
        )

        assert SourceIngestionService.is_advisory_stale_source_code(ingestion, now=now) is True

    def test_false_with_no_reference_time(self):
        now = datetime.now(UTC)
        ingestion = SourceIngestion(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            source_type=SourceType.SOURCE_CODE.value,
        )

        assert SourceIngestionService.is_advisory_stale_source_code(ingestion, now=now) is False


class TestListAdvisoryStaleSourceCodeIngestions:
    def test_returns_only_advisory_stale_source_code_ingestions(self, uow):
        now = datetime.now(UTC)
        stale_source_code = SourceIngestion(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            source_type=SourceType.SOURCE_CODE.value,
            started_at=now - timedelta(hours=55),
        )
        fresh_source_code = SourceIngestion(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            source_type=SourceType.SOURCE_CODE.value,
            started_at=now - timedelta(hours=10),
        )
        stale_rfp = SourceIngestion(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            source_type=SourceType.RFP.value,
            mod_fea_gen_started_at=now - timedelta(hours=6),
        )
        uow.source_ingestions.list_running.return_value = [
            stale_source_code,
            fresh_source_code,
            stale_rfp,
        ]

        result = SourceIngestionService().list_advisory_stale_source_code_ingestions(uow, now=now)

        assert result == [stale_source_code]
        uow.source_ingestions.update_fields.assert_not_called()
        uow.sources.get_by_ingestion_ids.assert_not_called()


class TestFailStaleRunningIngestions:
    def test_returns_empty_list_and_no_mutation_when_none_stale(self, uow):
        now = datetime.now(UTC)
        ingestion = SourceIngestion(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            source_type=SourceType.RFP.value,
            started_at=now - timedelta(minutes=5),
        )
        uow.source_ingestions.list_running.return_value = [ingestion]

        result = SourceIngestionService().fail_stale_running_ingestions(uow, now=now)

        assert result == []
        uow.source_ingestions.update_fields.assert_not_called()
        uow.sources.get_by_ingestion_ids.assert_not_called()

    def test_fails_stale_ingestion_and_its_sources(self, uow):
        now = datetime.now(UTC)
        ingestion = SourceIngestion(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            source_type=SourceType.RFP.value,
            started_at=now - timedelta(hours=4),
        )
        source = make_source(project_id=ingestion.project_id)
        uow.source_ingestions.list_running.return_value = [ingestion]
        uow.sources.get_by_ingestion_ids.return_value = [source]

        result = SourceIngestionService().fail_stale_running_ingestions(uow, now=now)

        assert result == [ingestion]
        assert source.status == SOURCE_STATUS_FAILED
        assert source.processing_error == MSG_SOURCE_INGESTION_STALE_AUTO_FAILED
        uow.sources.get_by_ingestion_ids.assert_called_once_with([ingestion.id])
        uow.source_ingestions.update_fields.assert_called_once_with(
            ingestion.id, status=SourceIngestionStatus.FAILED.value
        )
        uow.source_ingestions.add_error.assert_called_once_with(
            ingestion.id, MSG_SOURCE_INGESTION_STALE_AUTO_FAILED
        )

    def test_never_fails_a_source_code_ingestion_even_after_100_hours(self, uow):
        now = datetime.now(UTC)
        ingestion = SourceIngestion(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            source_type=SourceType.SOURCE_CODE.value,
            started_at=now - timedelta(hours=100),
        )
        uow.source_ingestions.list_running.return_value = [ingestion]

        result = SourceIngestionService().fail_stale_running_ingestions(uow, now=now)

        assert result == []
        uow.source_ingestions.update_fields.assert_not_called()
        uow.sources.get_by_ingestion_ids.assert_not_called()

    def test_leaves_non_stale_ingestions_among_a_mixed_batch_untouched(self, uow):
        now = datetime.now(UTC)
        stale = SourceIngestion(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            source_type=SourceType.RFP.value,
            mod_fea_gen_started_at=now - timedelta(hours=6),
        )
        fresh = SourceIngestion(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            source_type=SourceType.RFP.value,
            mod_fea_gen_started_at=now - timedelta(minutes=5),
        )
        uow.source_ingestions.list_running.return_value = [stale, fresh]
        uow.sources.get_by_ingestion_ids.return_value = []

        result = SourceIngestionService().fail_stale_running_ingestions(uow, now=now)

        assert result == [stale]
        uow.sources.get_by_ingestion_ids.assert_called_once_with([stale.id])
        uow.source_ingestions.update_fields.assert_called_once_with(
            stale.id, status=SourceIngestionStatus.FAILED.value
        )
        uow.source_ingestions.add_error.assert_called_once_with(
            stale.id, MSG_SOURCE_INGESTION_STALE_AUTO_FAILED
        )
