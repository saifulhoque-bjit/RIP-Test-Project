"""Unit tests for app.workers.incremental_task."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from app.core.enums.activity_type import ActivityType
from app.core.enums.notification_type import NotificationType
from app.core.enums.source_ingestion_stage import SourceIngestionStage
from app.core.enums.source_ingestion_status import SourceIngestionStatus
from app.core.llm_errors import LLMErrorClassification, LLMErrorReason, NonRetryableLLMError
from app.workers.incremental_task import (
    _async_incremental_pipeline,
    _notify_incremental_status,
    _persist_incremental_result,
    _record_changeset_ingested_activity,
    _remap_skip_processing_incremental_module_ids,
    incremental_update_task,
)

_REPO_PATCH_TARGET = "app.repositories.neo4j.module_feature_repository.ModuleFeatureRepository"

# A syntactically valid UUID string — the function does UUID(project_id)
# before ever touching the (mocked) repository.
_PROJECT_ID = "11111111-1111-1111-1111-111111111111"


class TestRemapSkipProcessingIncrementalModuleIds:
    """Tests for _remap_skip_processing_incremental_module_ids (skip_processing
    Incremental updates/adds fixup).

    See the function's own docstring for why this exists: the sample_result/
    Incremental/incremental_change.json fixture has module_id/feature_id values
    frozen from whichever project first captured it, so updates/adds resolve
    against the wrong (or no) node unless remapped by the project-agnostic
    module_code/feature_code.
    """

    @pytest.mark.asyncio
    async def test_remaps_matching_module_and_feature_codes(self):
        fake_module = SimpleNamespace(
            mod_code="1",
            id="real-module-id",
            features=[SimpleNamespace(fea_code="1.1", id="real-feature-id")],
        )
        with patch(_REPO_PATCH_TARGET) as MockRepo:
            MockRepo.return_value.list_modules_by_project = AsyncMock(return_value=[fake_module])
            payload = {
                "updates": [
                    {
                        "module_code": "1",
                        "module_id": "stale-module-id",
                        "features": [{"feature_code": "1.1", "feature_id": "stale-feature-id"}],
                    }
                ],
                "adds": [],
            }
            await _remap_skip_processing_incremental_module_ids(_PROJECT_ID, payload)

        module = payload["updates"][0]
        assert module["module_id"] == "real-module-id"
        assert module["features"][0]["feature_id"] == "real-feature-id"

    @pytest.mark.asyncio
    async def test_new_module_and_feature_with_no_code_are_left_alone(self):
        """A None/absent code means "genuinely new" (an adds wrapper's new child) —
        not a mismatch, so it must not be flagged or raise."""
        fake_module = SimpleNamespace(mod_code="1", id="real-module-id", features=[])
        with patch(_REPO_PATCH_TARGET) as MockRepo:
            MockRepo.return_value.list_modules_by_project = AsyncMock(return_value=[fake_module])
            payload = {
                "updates": [],
                "adds": [
                    {
                        "module_code": "1",
                        "module_id": "existing-wrapper-id",
                        "features": [
                            {
                                "feature_code": None,
                                "feature_id": None,
                                "feature_name": "New Feature",
                            }
                        ],
                    }
                ],
            }
            # Should not raise even though the new feature has no code to match.
            await _remap_skip_processing_incremental_module_ids(_PROJECT_ID, payload)

        new_feature = payload["adds"][0]["features"][0]
        assert new_feature["feature_id"] is None  # untouched, not flagged as a mismatch

    @pytest.mark.asyncio
    async def test_total_mismatch_raises_instead_of_silently_no_opping(self):
        fake_module = SimpleNamespace(mod_code="1", id="real-module-id", features=[])
        with patch(_REPO_PATCH_TARGET) as MockRepo:
            MockRepo.return_value.list_modules_by_project = AsyncMock(return_value=[fake_module])
            payload = {
                "updates": [{"module_code": "99", "module_id": "stale-id", "features": []}],
                "adds": [],
            }
            with pytest.raises(RuntimeError, match="doesn't match project"):
                await _remap_skip_processing_incremental_module_ids(_PROJECT_ID, payload)

    @pytest.mark.asyncio
    async def test_partial_mismatch_does_not_raise(self):
        fake_module = SimpleNamespace(mod_code="1", id="real-module-id", features=[])
        with patch(_REPO_PATCH_TARGET) as MockRepo:
            MockRepo.return_value.list_modules_by_project = AsyncMock(return_value=[fake_module])
            payload = {
                "updates": [
                    {"module_code": "1", "module_id": "stale-id-1", "features": []},
                    {"module_code": "99", "module_id": "stale-id-2", "features": []},
                ],
                "adds": [],
            }
            # One of two coded modules matches — must not raise.
            await _remap_skip_processing_incremental_module_ids(_PROJECT_ID, payload)

        # The unresolved module is dropped entirely rather than kept with its
        # stale (wrong-project) module_id.
        assert len(payload["updates"]) == 1
        assert payload["updates"][0]["module_id"] == "real-module-id"

    @pytest.mark.asyncio
    async def test_total_feature_mismatch_raises_even_if_modules_matched(self):
        """Generic sequential module codes ("1") can coincidentally match an
        unrelated project even when the sample is genuinely wrong — a total
        mismatch on the (more specific) feature codes must still raise."""
        fake_module = SimpleNamespace(
            mod_code="1",
            id="real-module-id",
            features=[SimpleNamespace(fea_code="1.1", id="real-feature-id")],
        )
        with patch(_REPO_PATCH_TARGET) as MockRepo:
            MockRepo.return_value.list_modules_by_project = AsyncMock(return_value=[fake_module])
            payload = {
                "updates": [
                    {
                        "module_code": "1",
                        "module_id": "stale-module-id",
                        "features": [{"feature_code": "99.99", "feature_id": "stale-feature-id"}],
                    }
                ],
                "adds": [],
            }
            with pytest.raises(RuntimeError, match="doesn't match project"):
                await _remap_skip_processing_incremental_module_ids(_PROJECT_ID, payload)

    @pytest.mark.asyncio
    async def test_no_updates_or_adds_is_a_no_op(self):
        with patch(_REPO_PATCH_TARGET) as MockRepo:
            MockRepo.return_value.list_modules_by_project = AsyncMock(return_value=[])
            payload = {"updates": [], "adds": []}
            await _remap_skip_processing_incremental_module_ids(_PROJECT_ID, payload)
        assert payload == {"updates": [], "adds": []}

    @pytest.mark.asyncio
    async def test_resolves_changed_user_story_by_feature_and_code(self):
        fake_module = SimpleNamespace(
            mod_code="1",
            id="real-module-id",
            features=[SimpleNamespace(fea_code="1.1", id="real-feature-id")],
        )
        fake_story = SimpleNamespace(id="real-story-id", user_story_code="U.S 1.1.1")
        with (
            patch(_REPO_PATCH_TARGET) as MockModuleRepo,
            patch("app.repositories.neo4j.user_story_repository.UserStoryRepository") as MockStoryRepo,
        ):
            MockModuleRepo.return_value.list_modules_by_project = AsyncMock(return_value=[fake_module])
            MockStoryRepo.return_value.list_user_stories_for_project = AsyncMock(
                return_value=([fake_story], 1)
            )
            payload = {
                "updates": [
                    {
                        "module_code": "1",
                        "features": [
                            {
                                "feature_code": "1.1",
                                "user_stories": [
                                    {
                                        "user_story_id": None,
                                        "user_story_code": "U.S 1.1.1",
                                        "changed": True,
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "adds": [],
            }
            await _remap_skip_processing_incremental_module_ids(_PROJECT_ID, payload)

        story = payload["updates"][0]["features"][0]["user_stories"][0]
        assert story["user_story_id"] == "real-story-id"
        MockStoryRepo.return_value.list_user_stories_for_project.assert_awaited_once_with(
            project_id=uuid.UUID(_PROJECT_ID),
            feature_id="real-feature-id",
            user_story_code="U.S 1.1.1",
            limit=100,
        )


class TestRecordChangesetIngestedActivity:
    """Tests for _record_changeset_ingested_activity, called once at
    incremental_update_task's "incremental_update.completed" stage."""

    def test_sums_per_category_and_resolves_actor(self):
        change_summary = {
            "adds": {"modules": 1, "features": 2, "user_stories": 3},
            "updates": {"modules": 0, "features": 1, "user_stories": 1},
            "deletes": {"modules": 0, "features": 0, "user_stories": 1},
        }
        actor_id = uuid.uuid4()

        with (
            patch(
                "app.workers.incremental_task.resolve_actor_from_task",
                return_value=actor_id,
            ) as mock_resolve,
            patch("app.workers.incremental_task.record_activity") as mock_record,
        ):
            _record_changeset_ingested_activity(
                project_id=_PROJECT_ID, task_db_id="task-1", change_summary=change_summary
            )

        mock_resolve.assert_called_once_with("task-1")
        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args.kwargs
        assert call_kwargs["project_id"] == uuid.UUID(_PROJECT_ID)
        assert call_kwargs["activity_type"] == ActivityType.INCREMENTAL_CHANGESET_INGESTED
        assert call_kwargs["actor_user_id"] == actor_id
        assert call_kwargs["data"] == {"change_summary": change_summary}
        assert "6 added" in call_kwargs["message"]
        assert "2 updated" in call_kwargs["message"]
        assert "1 deleted" in call_kwargs["message"]

    def test_zero_changes_still_records_activity(self):
        change_summary = {
            "adds": {"modules": 0, "features": 0, "user_stories": 0},
            "updates": {"modules": 0, "features": 0, "user_stories": 0},
            "deletes": {"modules": 0, "features": 0, "user_stories": 0},
        }

        with (
            patch("app.workers.incremental_task.resolve_actor_from_task", return_value=None),
            patch("app.workers.incremental_task.record_activity") as mock_record,
        ):
            _record_changeset_ingested_activity(
                project_id=_PROJECT_ID, task_db_id="task-1", change_summary=change_summary
            )

        mock_record.assert_called_once()
        assert "0 added, 0 updated, 0 deleted" in mock_record.call_args.kwargs["message"]


class TestNotifyIncrementalStatus:
    """Tests for _notify_incremental_status, covering the running/
    ready_for_review (with and without no_changes_explanation)/cancelled/
    failed transitions — mirrors _notify_source_code_pipeline_status."""

    @staticmethod
    def _uow_returning(project):
        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = project
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None
        return cm

    def test_running_notifies_started(self):
        project = SimpleNamespace(
            name="Demo Project", owner_id=uuid.UUID("00000000-0000-0000-0000-0000000000aa")
        )

        with (
            patch(
                "app.db.unit_of_work.UnitOfWork", return_value=self._uow_returning(project)
            ),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _notify_incremental_status(
                project_id=_PROJECT_ID,
                status=SourceIngestionStatus.RUNNING.value,
                source_ids=[],
            )

        mock_publish.assert_called_once()
        call_kwargs = mock_publish.call_args.kwargs
        assert call_kwargs["title"] == "Incremental Update Started"
        assert call_kwargs["notification_type"] == NotificationType.INFO

    def test_ready_for_review_with_changes_notifies_success_with_total(self):
        project = SimpleNamespace(
            name="Demo Project", owner_id=uuid.UUID("00000000-0000-0000-0000-0000000000aa")
        )
        change_summary = {
            "adds": {"modules": 1, "features": 2, "user_stories": 3},
            "updates": {"modules": 0, "features": 1, "user_stories": 0},
            "deletes": {"modules": 0, "features": 0, "user_stories": 0},
        }

        with (
            patch(
                "app.db.unit_of_work.UnitOfWork", return_value=self._uow_returning(project)
            ),
            patch(
                "app.workers.incremental_task._resolve_source_ingestion_id",
                return_value="ingestion-1",
            ),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _notify_incremental_status(
                project_id=_PROJECT_ID,
                status=SourceIngestionStatus.READY_FOR_REVIEW.value,
                source_ids=["src-1"],
                change_summary=change_summary,
            )

        mock_publish.assert_called_once()
        call_kwargs = mock_publish.call_args.kwargs
        assert call_kwargs["title"] == "Incremental Update Ready for Review"
        assert "7 change(s)" in call_kwargs["message"]
        assert call_kwargs["notification_type"] == NotificationType.SUCCESS
        assert call_kwargs["data"]["source_ingestion_id"] == "ingestion-1"
        assert call_kwargs["data"]["change_summary"] == change_summary

    def test_completed_with_no_changes_explanation_notifies_info(self):
        project = SimpleNamespace(
            name="Demo Project", owner_id=uuid.UUID("00000000-0000-0000-0000-0000000000aa")
        )

        with (
            patch(
                "app.db.unit_of_work.UnitOfWork", return_value=self._uow_returning(project)
            ),
            patch(
                "app.workers.incremental_task._resolve_source_ingestion_id",
                return_value="ingestion-1",
            ),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _notify_incremental_status(
                project_id=_PROJECT_ID,
                status=SourceIngestionStatus.COMPLETED.value,
                source_ids=["src-1"],
                change_summary={
                    "adds": {"modules": 0, "features": 0, "user_stories": 0},
                    "updates": {"modules": 0, "features": 0, "user_stories": 0},
                    "deletes": {"modules": 0, "features": 0, "user_stories": 0},
                },
                no_changes_explanation="The notes only confirmed existing behavior.",
            )

        mock_publish.assert_called_once()
        call_kwargs = mock_publish.call_args.kwargs
        assert call_kwargs["title"] == "Incremental Update: No Changes"
        assert "Demo Project" in call_kwargs["message"]
        assert "confirmed existing behavior" in call_kwargs["message"]
        assert call_kwargs["notification_type"] == NotificationType.INFO
        assert call_kwargs["data"]["no_changes_explanation"] == (
            "The notes only confirmed existing behavior."
        )

    def test_completed_without_no_changes_explanation_is_a_no_op(self):
        """COMPLETED only carries a notification when paired with
        no_changes_explanation — the only way the pipeline actually reaches
        COMPLETED (see final_ingestion_status in _async_incremental_pipeline).
        A bare COMPLETED with no explanation falls through defensively."""
        project = SimpleNamespace(
            name="Demo Project", owner_id=uuid.UUID("00000000-0000-0000-0000-0000000000aa")
        )

        with (
            patch(
                "app.db.unit_of_work.UnitOfWork", return_value=self._uow_returning(project)
            ),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _notify_incremental_status(
                project_id=_PROJECT_ID,
                status=SourceIngestionStatus.COMPLETED.value,
                source_ids=[],
            )

        mock_publish.assert_not_called()

    def test_failed_notifies_error_with_truncated_message(self):
        project = SimpleNamespace(
            name="Demo Project", owner_id=uuid.UUID("00000000-0000-0000-0000-0000000000aa")
        )

        with (
            patch(
                "app.db.unit_of_work.UnitOfWork", return_value=self._uow_returning(project)
            ),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _notify_incremental_status(
                project_id=_PROJECT_ID,
                status=SourceIngestionStatus.FAILED.value,
                source_ids=[],
                error="LLM provider timed out",
            )

        mock_publish.assert_called_once()
        call_kwargs = mock_publish.call_args.kwargs
        assert call_kwargs["title"] == "Incremental Update Failed"
        assert "LLM provider timed out" in call_kwargs["message"]
        assert call_kwargs["notification_type"] == NotificationType.ERROR

    def test_cancelled_notifies_warning(self):
        project = SimpleNamespace(
            name="Demo Project", owner_id=uuid.UUID("00000000-0000-0000-0000-0000000000aa")
        )

        with (
            patch(
                "app.db.unit_of_work.UnitOfWork", return_value=self._uow_returning(project)
            ),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _notify_incremental_status(
                project_id=_PROJECT_ID,
                status=SourceIngestionStatus.CANCELLED.value,
                source_ids=[],
            )

        mock_publish.assert_called_once()
        call_kwargs = mock_publish.call_args.kwargs
        assert call_kwargs["title"] == "Incremental Update Cancelled"
        assert "Demo Project" in call_kwargs["message"]
        assert call_kwargs["notification_type"] == NotificationType.WARNING

    def test_unrecognized_status_is_a_no_op(self):
        project = SimpleNamespace(
            name="Demo Project", owner_id=uuid.UUID("00000000-0000-0000-0000-0000000000aa")
        )

        with (
            patch(
                "app.db.unit_of_work.UnitOfWork", return_value=self._uow_returning(project)
            ),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _notify_incremental_status(
                project_id=_PROJECT_ID, status="queued", source_ids=[]
            )

        mock_publish.assert_not_called()

    def test_notifies_owner_and_assigned_members_deduped(self):
        """The owner also holds a membership row — must be notified only once."""
        owner_id = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
        member_id = uuid.UUID("00000000-0000-0000-0000-0000000000bb")
        project = SimpleNamespace(name="Demo Project", owner_id=owner_id)
        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = project
        uow.project_members.list_by_project.return_value = [
            SimpleNamespace(user_id=member_id),
            SimpleNamespace(user_id=owner_id),
        ]
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _notify_incremental_status(
                project_id=_PROJECT_ID,
                status=SourceIngestionStatus.RUNNING.value,
                source_ids=[],
            )

        notified_ids = {c.kwargs["user_id"] for c in mock_publish.call_args_list}
        assert notified_ids == {owner_id, member_id}
        assert mock_publish.call_count == 2

    def test_no_project_owner_skips_notification(self):
        with (
            patch(
                "app.db.unit_of_work.UnitOfWork", return_value=self._uow_returning(None)
            ),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _notify_incremental_status(
                project_id=_PROJECT_ID,
                status=SourceIngestionStatus.RUNNING.value,
                source_ids=[],
            )

        mock_publish.assert_not_called()

    def test_publish_failure_is_swallowed(self):
        project = SimpleNamespace(
            name="Demo Project", owner_id=uuid.UUID("00000000-0000-0000-0000-0000000000aa")
        )

        with (
            patch(
                "app.db.unit_of_work.UnitOfWork", return_value=self._uow_returning(project)
            ),
            patch(
                "app.services.notification_service.publish_notification",
                side_effect=RuntimeError("redis down"),
            ),
        ):
            _notify_incremental_status(
                project_id=_PROJECT_ID,
                status=SourceIngestionStatus.RUNNING.value,
                source_ids=[],
            )


class TestAsyncIncrementalPipeline:
    """The pipeline's terminal SourceIngestion status is READY_FOR_REVIEW when
    it proposed a changeset — same as the initial RFP/source-code pipelines —
    but COMPLETED when the AI reported no_changes_explanation, since there is
    then nothing for a human to review."""

    @pytest.mark.asyncio
    async def test_success_marks_ingestion_ready_for_review(self):
        change_summary = {
            "adds": {"modules": 1, "features": 2, "user_stories": 3},
            "updates": {"modules": 0, "features": 0, "user_stories": 0},
            "deletes": {"modules": 0, "features": 0, "user_stories": 0},
        }

        with (
            patch("app.workers.incremental_task.mark_cancelled_and_check", return_value=False),
            patch("app.workers.incremental_task.emit_task_event") as mock_emit,
            patch(
                "app.workers.incremental_task._fetch_source_file_types", return_value={}
            ),
            patch(
                "app.workers.incremental_task._persist_fragments",
                new=AsyncMock(return_value=([], [])),
            ),
            patch(
                "app.workers.incremental_task._fetch_backlog",
                new=AsyncMock(return_value="{}"),
            ),
            patch(
                "app.workers.incremental_task._run_ai_pipeline",
                new=AsyncMock(return_value={"status": "completed", "output": ""}),
            ),
            patch(
                "app.workers.incremental_task._persist_incremental_result",
                new=AsyncMock(return_value=change_summary),
            ),
            patch(
                "app.workers.incremental_task._record_changeset_ingested_activity"
            ),
            patch(
                "app.workers.incremental_task._record_changeset_started_activity"
            ) as mock_record_started,
            patch(
                "app.workers.incremental_task._update_source_ingestion_fields"
            ) as mock_update_fields,
            patch(
                "app.workers.document_task_stages._add_run_stage_by_source_ids"
            ) as mock_add_stage,
            patch(
                "app.workers.incremental_task._notify_incremental_status"
            ) as mock_notify_status,
        ):
            result = await _async_incremental_pipeline(
                task=SimpleNamespace(),
                project_id=_PROJECT_ID,
                pdf_source_ids=[],
                image_source_ids=[],
                user_message="",
                skip_processing=False,
                task_db_id="task-1",
            )

        assert result["output"] == ""
        completion_call = mock_update_fields.call_args_list[-1]
        assert completion_call.kwargs["fields"]["status"] == (
            SourceIngestionStatus.READY_FOR_REVIEW.value
        )
        mock_add_stage.assert_called_once_with([], SourceIngestionStage.READY_FOR_REVIEW)

        emit_completion_call = mock_emit.call_args_list[-1]
        assert emit_completion_call.kwargs["status"] == SourceIngestionStatus.READY_FOR_REVIEW.value
        assert emit_completion_call.kwargs["stage"] == "incremental_update.completed"

        assert mock_notify_status.call_count == 2
        started_call, completed_call = mock_notify_status.call_args_list
        assert started_call.kwargs["status"] == SourceIngestionStatus.RUNNING.value
        assert completed_call.kwargs["status"] == SourceIngestionStatus.READY_FOR_REVIEW.value
        assert completed_call.kwargs["change_summary"] == change_summary
        assert completed_call.kwargs["no_changes_explanation"] is None
        mock_record_started.assert_called_once_with(
            project_id=_PROJECT_ID, task_db_id="task-1", source_ids=[]
        )

    @pytest.mark.asyncio
    async def test_no_changes_explanation_triggers_notification(self):
        change_summary = {
            "adds": {"modules": 0, "features": 0, "user_stories": 0},
            "updates": {"modules": 0, "features": 0, "user_stories": 0},
            "deletes": {"modules": 0, "features": 0, "user_stories": 0},
        }
        ai_result = {
            "status": "completed",
            "output": "",
            "generation_metadata": {
                "no_changes_explanation": "The notes only confirmed existing behavior."
            },
        }

        with (
            patch("app.workers.incremental_task.mark_cancelled_and_check", return_value=False),
            patch("app.workers.incremental_task.emit_task_event") as mock_emit,
            patch(
                "app.workers.incremental_task._fetch_source_file_types", return_value={}
            ),
            patch(
                "app.workers.incremental_task._persist_fragments",
                new=AsyncMock(return_value=([], [])),
            ),
            patch(
                "app.workers.incremental_task._fetch_backlog",
                new=AsyncMock(return_value="{}"),
            ),
            patch(
                "app.workers.incremental_task._run_ai_pipeline",
                new=AsyncMock(return_value=ai_result),
            ),
            patch(
                "app.workers.incremental_task._persist_incremental_result",
                new=AsyncMock(return_value=change_summary),
            ),
            patch(
                "app.workers.incremental_task._record_changeset_ingested_activity"
            ),
            patch(
                "app.workers.incremental_task._update_source_ingestion_fields"
            ) as mock_update_fields,
            patch("app.workers.document_task_stages._add_run_stage_by_source_ids"),
            patch(
                "app.workers.incremental_task._notify_incremental_status"
            ) as mock_notify_status,
        ):
            await _async_incremental_pipeline(
                task=SimpleNamespace(),
                project_id=_PROJECT_ID,
                pdf_source_ids=[],
                image_source_ids=[],
                user_message="",
                skip_processing=False,
                task_db_id="task-1",
            )

        completion_call = mock_update_fields.call_args_list[-1]
        assert completion_call.kwargs["fields"]["status"] == SourceIngestionStatus.COMPLETED.value

        emit_completion_call = mock_emit.call_args_list[-1]
        assert emit_completion_call.kwargs["status"] == SourceIngestionStatus.COMPLETED.value

        completed_call = mock_notify_status.call_args_list[-1]
        assert completed_call.kwargs["status"] == SourceIngestionStatus.COMPLETED.value
        assert completed_call.kwargs["no_changes_explanation"] == (
            "The notes only confirmed existing behavior."
        )


class TestIncrementalUpdateCancellation:
    """Both cancellation checkpoints — the pre-flight check in
    incremental_update_task and the mid-generation check in
    _async_incremental_pipeline — must notify with a CANCELLED status so a
    cancelled upload doesn't leave the user with no feedback at all."""

    def test_preflight_cancellation_notifies_cancelled_status(self):
        incremental_update_task.push_request(retries=0)
        try:
            with (
                patch(
                    "app.workers.incremental_task.mark_cancelled_and_check",
                    return_value=True,
                ),
                patch(
                    "app.workers.incremental_task.mark_sources_and_ingestion_cancelled"
                ) as mock_mark_cancelled,
                patch(
                    "app.workers.incremental_task._notify_incremental_status"
                ) as mock_notify_status,
                patch("app.workers.incremental_task.record_activity") as mock_record,
            ):
                result = incremental_update_task.__wrapped__(
                    project_id=_PROJECT_ID,
                    pdf_source_ids=["src-1"],
                    image_source_ids=[],
                    user_message="",
                    skip_processing=False,
                    task_db_id="task-1",
                )
        finally:
            incremental_update_task.pop_request()

        assert result == {"project_id": _PROJECT_ID, "status": "cancelled"}
        mock_mark_cancelled.assert_called_once()
        mock_notify_status.assert_called_once_with(
            project_id=_PROJECT_ID,
            status=SourceIngestionStatus.CANCELLED.value,
            source_ids=["src-1"],
        )
        mock_record.assert_called_once()
        assert mock_record.call_args.kwargs["activity_type"] == ActivityType.INCREMENTAL_CHANGESET_CANCELLED

    @pytest.mark.asyncio
    async def test_mid_pipeline_cancellation_notifies_cancelled_status(self):
        with (
            patch("app.workers.incremental_task.mark_cancelled_and_check", return_value=False),
            patch("app.workers.incremental_task.emit_task_event"),
            patch("app.workers.incremental_task._fetch_source_file_types", return_value={}),
            patch(
                "app.workers.incremental_task._persist_fragments",
                new=AsyncMock(return_value=([], [])),
            ),
            patch(
                "app.workers.incremental_task._fetch_backlog",
                new=AsyncMock(return_value="{}"),
            ),
            patch(
                "app.workers.incremental_task._run_ai_pipeline",
                new=AsyncMock(return_value={"status": "CANCELLED"}),
            ),
            patch(
                "app.workers.incremental_task.mark_sources_and_ingestion_cancelled"
            ) as mock_mark_cancelled,
            patch(
                "app.workers.incremental_task._notify_incremental_status"
            ) as mock_notify_status,
            patch("app.workers.incremental_task.record_activity") as mock_record,
        ):
            result = await _async_incremental_pipeline(
                task=SimpleNamespace(),
                project_id=_PROJECT_ID,
                pdf_source_ids=[],
                image_source_ids=[],
                user_message="",
                skip_processing=False,
                task_db_id="task-1",
            )

        assert result == {"project_id": _PROJECT_ID, "status": "cancelled"}
        mock_mark_cancelled.assert_called_once()
        # The RUNNING notify fires unconditionally at pipeline start, then the
        # CANCELLED one once generation is aborted mid-flight.
        assert mock_notify_status.call_count == 2
        started_call, cancelled_call = mock_notify_status.call_args_list
        assert started_call.kwargs["status"] == SourceIngestionStatus.RUNNING.value
        assert cancelled_call.kwargs["status"] == SourceIngestionStatus.CANCELLED.value
        assert cancelled_call.kwargs["source_ids"] == []
        # STARTED fires unconditionally at pipeline entry, then CANCELLED once
        # mid-generation abort is detected.
        assert mock_record.call_args_list[-1].kwargs["activity_type"] == (
            ActivityType.INCREMENTAL_CHANGESET_CANCELLED
        )


class TestIncrementalUpdateTaskFailurePath:
    """incremental_update_task is itself the bound Celery task — its terminal-
    failure branch (retries exhausted) must fire a FAILED notification and
    return a terminal dict directly, mirroring
    generate_modules_and_features_task's terminal-failure notify call.

    Regression guard: this used to unconditionally call ``self.retry()``
    even after already marking the run FAILED here (a pre-existing bug —
    Celery would then raise MaxRetriesExceededError on a run that was
    already correctly terminal). It must not retry once retries are
    exhausted."""

    def test_terminal_failure_notifies_failed_status(self):
        incremental_update_task.push_request(retries=incremental_update_task.max_retries)
        try:
            with (
                patch(
                    "app.workers.incremental_task.mark_cancelled_and_check",
                    return_value=False,
                ),
                patch("app.workers.incremental_task._async_incremental_pipeline"),
                patch(
                    "app.workers.incremental_task._run_async",
                    side_effect=RuntimeError("boom"),
                ),
                patch("app.workers.incremental_task.emit_task_event"),
                patch("app.workers.incremental_task._update_source_ingestion_fields"),
                patch("app.workers.incremental_task._add_source_ingestion_error"),
                patch(
                    "app.workers.incremental_task._notify_incremental_status"
                ) as mock_notify_status,
                patch(
                    "app.workers.incremental_task._record_changeset_failed_activity"
                ) as mock_record_failed,
                patch.object(incremental_update_task, "retry") as mock_retry,
            ):
                result = incremental_update_task.__wrapped__(
                    project_id=_PROJECT_ID,
                    pdf_source_ids=["src-1"],
                    image_source_ids=[],
                    user_message="",
                    skip_processing=False,
                    task_db_id="task-1",
                )
        finally:
            incremental_update_task.pop_request()

        # Terminal: returns the failed dict directly, never retries.
        mock_retry.assert_not_called()
        assert result["status"] == SourceIngestionStatus.FAILED.value
        mock_notify_status.assert_called_once_with(
            project_id=_PROJECT_ID,
            status=SourceIngestionStatus.FAILED.value,
            source_ids=["src-1"],
            error="boom",
        )
        mock_record_failed.assert_called_once_with(
            project_id=_PROJECT_ID,
            task_db_id="task-1",
            source_ids=["src-1"],
            error="boom",
        )

    def test_fails_immediately_on_non_retryable_llm_error(self):
        """Even on the very first attempt (retries=0), a non-retryable LLM
        error must finalize as FAILED without ever calling self.retry —
        unlike a generic Exception, which retries until exhausted."""
        classification = LLMErrorClassification(
            retryable=False,
            reason=LLMErrorReason.CREDIT_EXHAUSTED,
            provider="anthropic",
            message="Your credit balance is too low to access the Anthropic API.",
            user_message="The AI provider account has run out of credits or quota.",
        )
        fake_error = NonRetryableLLMError(classification)

        incremental_update_task.push_request(retries=0)
        try:
            with (
                patch(
                    "app.workers.incremental_task.mark_cancelled_and_check",
                    return_value=False,
                ),
                patch("app.workers.incremental_task._async_incremental_pipeline"),
                patch(
                    "app.workers.incremental_task._run_async",
                    side_effect=fake_error,
                ),
                patch("app.workers.incremental_task.emit_task_event"),
                patch("app.workers.incremental_task._update_source_ingestion_fields"),
                patch("app.workers.incremental_task._add_source_ingestion_error") as mock_add_error,
                patch(
                    "app.workers.incremental_task._notify_incremental_status"
                ) as mock_notify_status,
                patch(
                    "app.workers.incremental_task._record_changeset_failed_activity"
                ) as mock_record_failed,
                patch.object(incremental_update_task, "retry") as mock_retry,
                patch(
                    "app.workers.incremental_task.cancel_sibling_tasks_on_fatal_llm_error"
                ) as mock_cancel_siblings,
            ):
                result = incremental_update_task.__wrapped__(
                    project_id=_PROJECT_ID,
                    pdf_source_ids=["src-1"],
                    image_source_ids=[],
                    user_message="",
                    skip_processing=False,
                    task_db_id="task-1",
                )
        finally:
            incremental_update_task.pop_request()

        mock_retry.assert_not_called()
        assert result["status"] == SourceIngestionStatus.FAILED.value
        assert "credit_exhausted" in result["error"]
        assert "credit_exhausted" in mock_add_error.call_args.kwargs["error"]
        mock_notify_status.assert_called_once_with(
            project_id=_PROJECT_ID,
            status=SourceIngestionStatus.FAILED.value,
            source_ids=["src-1"],
            error="The AI provider account has run out of credits or quota.",
            error_reason="credit_exhausted",
        )
        assert "credit_exhausted" in mock_record_failed.call_args.kwargs["error"]
        mock_cancel_siblings.assert_called_once_with(
            request_id="task-1", task_db_id="task-1", project_id=_PROJECT_ID
        )

    def test_retriable_failure_does_not_notify(self):
        incremental_update_task.push_request(retries=0)
        try:
            with (
                patch(
                    "app.workers.incremental_task.mark_cancelled_and_check",
                    return_value=False,
                ),
                patch("app.workers.incremental_task._async_incremental_pipeline"),
                patch(
                    "app.workers.incremental_task._run_async",
                    side_effect=RuntimeError("boom"),
                ),
                patch("app.workers.incremental_task.emit_task_event"),
                patch(
                    "app.workers.incremental_task._notify_incremental_status"
                ) as mock_notify_status,
                patch(
                    "app.workers.incremental_task._record_changeset_failed_activity"
                ) as mock_record_failed,
                patch.object(
                    incremental_update_task,
                    "retry",
                    side_effect=RuntimeError("retry-triggered"),
                ),
                pytest.raises(RuntimeError, match="retry-triggered"),
            ):
                incremental_update_task.__wrapped__(
                    project_id=_PROJECT_ID,
                    pdf_source_ids=[],
                    image_source_ids=[],
                    user_message="",
                    skip_processing=False,
                    task_db_id="task-1",
                )
        finally:
            incremental_update_task.pop_request()

        mock_notify_status.assert_not_called()
        mock_record_failed.assert_not_called()


class TestPersistIncrementalResult:
    """_persist_incremental_result resolves source_ids -> source_ingestion_id and
    threads it into every processor call, so incremental adds/updates/delete-
    suggestions get stamped with the ingestion that produced them (see
    SourceIngestionService's ingestion-completion tracking)."""

    @pytest.mark.asyncio
    async def test_resolves_and_threads_source_ingestion_id_into_every_processor_call(self):
        result = {
            "output": json.dumps(
                {
                    "updates": [{"changed": True, "module_id": "mod-1"}],
                    "adds": [{"changed": True, "module_name": "M"}],
                    "deletes": [{"uuid": "mod-2", "type": "module"}],
                    "flags": [],
                }
            )
        }

        with (
            patch(
                "app.services.incremental_update_processor_service.IncrementalUpdateProcessorService"
            ) as MockProcessorClass,
            patch(
                "app.workers.incremental_task._resolve_source_ingestion_id",
                return_value="ingestion-1",
            ) as mock_resolve,
            patch("app.db.unit_of_work.UnitOfWork") as MockUow,
        ):
            MockProcessorClass.extract_rfp_flag_map.return_value = {}
            mock_processor = MockProcessorClass.return_value
            mock_processor.handle_updates = AsyncMock()
            mock_processor.handle_adds = AsyncMock()
            mock_processor.handle_deletes = AsyncMock()
            mock_processor.handle_history = AsyncMock()
            MockUow.return_value.__enter__.return_value = MagicMock()

            await _persist_incremental_result(
                result=result,
                project_id=_PROJECT_ID,
                task_db_id="task-1",
                source_ids=["src-1", "src-2"],
            )

        mock_resolve.assert_called_once_with(source_ids=["src-1", "src-2"])
        project_uuid = uuid.UUID(_PROJECT_ID)
        mock_processor.handle_updates.assert_awaited_once_with(
            project_uuid,
            [{"changed": True, "module_id": "mod-1"}],
            {},
            source_ingestion_id="ingestion-1",
        )
        mock_processor.handle_adds.assert_awaited_once_with(
            project_uuid,
            [{"changed": True, "module_name": "M"}],
            {},
            source_ingestion_id="ingestion-1",
        )
        mock_processor.handle_deletes.assert_awaited_once_with(
            project_uuid,
            [{"uuid": "mod-2", "type": "module"}],
            source_ingestion_id="ingestion-1",
        )

    @pytest.mark.asyncio
    async def test_no_source_ingestion_found_passes_none_through(self):
        result = {"output": json.dumps({"updates": [], "adds": [], "deletes": [], "flags": []})}

        with (
            patch(
                "app.services.incremental_update_processor_service.IncrementalUpdateProcessorService"
            ) as MockProcessorClass,
            patch(
                "app.workers.incremental_task._resolve_source_ingestion_id",
                return_value=None,
            ),
            patch("app.db.unit_of_work.UnitOfWork") as MockUow,
        ):
            MockProcessorClass.extract_rfp_flag_map.return_value = {}
            mock_processor = MockProcessorClass.return_value
            mock_processor.handle_updates = AsyncMock()
            mock_processor.handle_adds = AsyncMock()
            mock_processor.handle_deletes = AsyncMock()
            mock_processor.handle_history = AsyncMock()
            MockUow.return_value.__enter__.return_value = MagicMock()

            await _persist_incremental_result(
                result=result,
                project_id=_PROJECT_ID,
                task_db_id="task-1",
                source_ids=[],
            )

        _, kwargs = mock_processor.handle_updates.await_args
        assert kwargs["source_ingestion_id"] is None
