"""Unit tests for app.services.incremental_update_processor_service."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock
import uuid

import pytest

from app.models.neo4j.module_feature_model import (
    ChangeType,
    FeatureModel,
    ModuleFeatureStatus,
    ModuleModel,
)
from app.models.neo4j.user_story_model import UserStoryModel
from app.repositories.neo4j.module_feature_repository import ModuleFeatureRepository
from app.repositories.neo4j.user_story_repository import UserStoryRepository
from app.schemas.user_story_schema import UserStoryStatus
from app.services.incremental_update_processor_service import (
    IncrementalUpdateProcessorService,
)

PROJECT_ID = uuid.uuid4()


def make_mf_repo() -> AsyncMock:
    """AsyncMock standing in for ModuleFeatureRepository — spec'd so every
    method (all async) auto-detects as an AsyncMock child."""
    repo = AsyncMock(spec=ModuleFeatureRepository)
    repo.get_max_mod_code.return_value = 0
    repo.get_max_fea_code.return_value = ("1", 0)
    repo.update_module.return_value = True
    repo.update_feature.return_value = True
    repo.snapshot_module_version.return_value = True
    repo.snapshot_feature_version.return_value = True
    repo.mark_module_delete_suggested.return_value = True
    repo.mark_feature_delete_suggested.return_value = True
    return repo


def make_us_repo() -> AsyncMock:
    """AsyncMock standing in for UserStoryRepository."""
    repo = AsyncMock(spec=UserStoryRepository)
    repo.snapshot_user_story_version.return_value = True
    repo.bulk_upsert_user_stories_for_project.return_value = 1
    repo.get_max_user_story_code.return_value = ("1.1", 0)
    repo.get_user_story_detail_for_project.return_value = None
    repo.get_user_story_version_by_id.return_value = None
    repo.mark_user_story_delete_suggested.return_value = MagicMock()
    repo.set_user_story_status_and_flag.return_value = MagicMock()
    repo.update_user_story_sync_flags.return_value = MagicMock()
    return repo


@pytest.fixture
def mf_repo() -> AsyncMock:
    return make_mf_repo()


@pytest.fixture
def us_repo() -> AsyncMock:
    return make_us_repo()


@pytest.fixture
def service(mf_repo: AsyncMock, us_repo: AsyncMock) -> IncrementalUpdateProcessorService:
    return IncrementalUpdateProcessorService(module_feature_repo=mf_repo, user_story_repo=us_repo)


# ── handle_updates ──────────────────────────────────────────────────────────


class TestHandleUpdates:
    @pytest.mark.asyncio
    async def test_empty_list_is_a_no_op(self, service, mf_repo, us_repo):
        await service.handle_updates(PROJECT_ID, [])
        mf_repo.update_module.assert_not_called()
        us_repo.bulk_upsert_user_stories_for_project.assert_not_called()

    @pytest.mark.asyncio
    async def test_traverses_full_hierarchy_and_updates_changed_nodes(
        self, service, mf_repo, us_repo
    ):
        updates = [
            {
                "changed": True,
                "module_id": "mod-1",
                "module_code": "1",
                "module_name": "Module One",
                "features": [
                    {
                        "changed": True,
                        "feature_id": "fea-1",
                        "feature_code": "1.1",
                        "feature_name": "Feature One",
                        "user_stories": [
                            {
                                "changed": True,
                                "user_story_id": "story-1",
                                "user_story_code": "U.S 1.1.1",
                                "title": "Story One",
                            }
                        ],
                    }
                ],
            }
        ]
        await service.handle_updates(PROJECT_ID, updates)

        mf_repo.update_module.assert_awaited_once()
        mf_repo.update_feature.assert_awaited_once()
        us_repo.bulk_upsert_user_stories_for_project.assert_awaited_once()
        us_repo.set_user_story_status_and_flag.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unchanged_parent_still_recurses_into_children(self, service, mf_repo, us_repo):
        """changed=False on the module wrapper must not block feature/story updates."""
        updates = [
            {
                "changed": False,
                "module_id": "mod-1",
                "features": [
                    {
                        "changed": True,
                        "feature_id": "fea-1",
                        "feature_name": "Feature One",
                        "user_stories": [],
                    }
                ],
            }
        ]
        await service.handle_updates(PROJECT_ID, updates)

        mf_repo.update_module.assert_not_called()
        mf_repo.update_feature.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unchanged_leaf_nodes_are_skipped(self, service, mf_repo, us_repo):
        updates = [
            {
                "changed": False,
                "module_id": "mod-1",
                "features": [
                    {
                        "changed": False,
                        "feature_id": "fea-1",
                        "user_stories": [{"changed": False, "user_story_id": "story-1"}],
                    }
                ],
            }
        ]
        await service.handle_updates(PROJECT_ID, updates)

        mf_repo.update_module.assert_not_called()
        mf_repo.update_feature.assert_not_called()
        us_repo.bulk_upsert_user_stories_for_project.assert_not_called()


# ── handle_adds ─────────────────────────────────────────────────────────────


class TestHandleAdds:
    @pytest.mark.asyncio
    async def test_empty_list_is_a_no_op(self, service, mf_repo, us_repo):
        await service.handle_adds(PROJECT_ID, [])
        mf_repo.create_module.assert_not_called()
        us_repo.bulk_upsert_user_stories_for_project.assert_not_called()

    @pytest.mark.asyncio
    async def test_traverses_full_hierarchy_and_creates_changed_nodes(
        self, service, mf_repo, us_repo
    ):
        mf_repo.create_module.return_value = ModuleModel(
            id="new-mod-id",
            project_id=PROJECT_ID,
            mod_code="1",
            name="Module",
            description=None,
            features=[],
        )
        mf_repo.create_feature.return_value = FeatureModel(
            id="new-fea-id",
            project_id=PROJECT_ID,
            module_id="new-mod-id",
            fea_code="1.1",
            name="Feature",
            description=None,
        )
        adds = [
            {
                "changed": True,
                "module_name": "Module",
                "features": [
                    {
                        "changed": True,
                        "feature_name": "Feature",
                        "user_stories": [
                            {"changed": True, "user_story_code": "U.S 1.1.1", "title": "Story"}
                        ],
                    }
                ],
            }
        ]
        await service.handle_adds(PROJECT_ID, adds)

        mf_repo.create_module.assert_awaited_once()
        mf_repo.create_feature.assert_awaited_once()
        # child feature must have been linked to the *returned* (MERGE-resolved) module id
        _, kwargs = mf_repo.create_feature.await_args
        assert kwargs["module_id"] == "new-mod-id"
        us_repo.bulk_upsert_user_stories_for_project.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unchanged_module_wrapper_passes_through_existing_id_for_linking(
        self, service, mf_repo, us_repo
    ):
        adds = [
            {
                "changed": False,
                "module_id": "existing-mod-id",
                "features": [
                    {
                        "changed": True,
                        "feature_name": "New Feature",
                        "user_stories": [],
                    }
                ],
            }
        ]
        mf_repo.get_max_fea_code.return_value = ("1", 0)
        mf_repo.create_feature.return_value = FeatureModel(
            id="new-fea-id",
            project_id=PROJECT_ID,
            module_id="existing-mod-id",
            fea_code="1.1",
            name="New Feature",
            description=None,
        )
        await service.handle_adds(PROJECT_ID, adds)

        mf_repo.create_module.assert_not_called()
        _, kwargs = mf_repo.create_feature.await_args
        assert kwargs["module_id"] == "existing-mod-id"

    @pytest.mark.asyncio
    async def test_unchanged_feature_wrapper_skips_story_creation_call_but_keeps_id(
        self, service, mf_repo, us_repo
    ):
        adds = [
            {
                "changed": False,
                "module_id": "existing-mod-id",
                "features": [
                    {
                        "changed": False,
                        "feature_id": "existing-fea-id",
                        "user_stories": [
                            {"changed": True, "user_story_code": "U.S 1.1.1", "title": "Story"}
                        ],
                    }
                ],
            }
        ]
        await service.handle_adds(PROJECT_ID, adds)

        mf_repo.create_feature.assert_not_called()
        us_repo.bulk_upsert_user_stories_for_project.assert_awaited_once()


# ── handle_deletes ──────────────────────────────────────────────────────────


class TestHandleDeletes:
    @pytest.mark.asyncio
    async def test_empty_list_is_a_no_op(self, service, mf_repo, us_repo):
        await service.handle_deletes(PROJECT_ID, [])
        mf_repo.mark_module_delete_suggested.assert_not_called()

    @pytest.mark.asyncio
    async def test_marks_module_delete_suggested(self, service, mf_repo):
        await service.handle_deletes(
            PROJECT_ID, [{"uuid": "mod-1", "type": "module", "justification": "stale"}]
        )
        mf_repo.mark_module_delete_suggested.assert_awaited_once_with(
            project_id=PROJECT_ID, module_id="mod-1", justification="stale", source_ingestion_id=None
        )

    @pytest.mark.asyncio
    async def test_marks_feature_delete_suggested(self, service, mf_repo):
        await service.handle_deletes(PROJECT_ID, [{"uuid": "fea-1", "type": "feature"}])
        mf_repo.mark_feature_delete_suggested.assert_awaited_once_with(
            project_id=PROJECT_ID, feature_id="fea-1", justification=None, source_ingestion_id=None
        )

    @pytest.mark.asyncio
    async def test_marks_user_story_delete_suggested(self, service, us_repo):
        await service.handle_deletes(PROJECT_ID, [{"uuid": "story-1", "type": "user_story"}])
        us_repo.mark_user_story_delete_suggested.assert_awaited_once_with(
            user_story_id="story-1", justification=None, source_ingestion_id=None
        )
        us_repo.update_user_story_sync_flags.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_snapshots_module_version_before_marking_delete_suggested(
        self, service, mf_repo
    ):
        await service.handle_deletes(PROJECT_ID, [{"uuid": "mod-1", "type": "module"}])
        mf_repo.snapshot_module_version.assert_awaited_once_with(PROJECT_ID, "mod-1")
        mf_repo.mark_module_delete_suggested.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_snapshots_feature_version_before_marking_delete_suggested(
        self, service, mf_repo
    ):
        await service.handle_deletes(PROJECT_ID, [{"uuid": "fea-1", "type": "feature"}])
        mf_repo.snapshot_feature_version.assert_awaited_once_with(PROJECT_ID, "fea-1")
        mf_repo.mark_feature_delete_suggested.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_snapshots_user_story_version_before_marking_delete_suggested(
        self, service, us_repo
    ):
        await service.handle_deletes(PROJECT_ID, [{"uuid": "story-1", "type": "user_story"}])
        us_repo.snapshot_user_story_version.assert_awaited_once_with("story-1")
        us_repo.mark_user_story_delete_suggested.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_snapshot_exception_is_swallowed_and_delete_suggestion_still_runs(
        self, service, mf_repo
    ):
        mf_repo.snapshot_module_version.side_effect = RuntimeError("boom")
        await service.handle_deletes(PROJECT_ID, [{"uuid": "mod-1", "type": "module"}])
        mf_repo.mark_module_delete_suggested.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_suggested_no_match_does_not_reset_sync_flags(self, service, us_repo):
        us_repo.mark_user_story_delete_suggested.return_value = None
        await service.handle_deletes(PROJECT_ID, [{"uuid": "missing-story", "type": "user_story"}])
        us_repo.update_user_story_sync_flags.assert_not_called()

    @pytest.mark.asyncio
    async def test_source_ingestion_id_is_stamped_on_every_delete_suggestion(
        self, service, mf_repo, us_repo
    ):
        await service.handle_deletes(
            PROJECT_ID,
            [
                {"uuid": "mod-1", "type": "module"},
                {"uuid": "fea-1", "type": "feature"},
                {"uuid": "story-1", "type": "user_story"},
            ],
            source_ingestion_id="ingestion-1",
        )
        mf_repo.mark_module_delete_suggested.assert_awaited_once_with(
            project_id=PROJECT_ID,
            module_id="mod-1",
            justification=None,
            source_ingestion_id="ingestion-1",
        )
        mf_repo.mark_feature_delete_suggested.assert_awaited_once_with(
            project_id=PROJECT_ID,
            feature_id="fea-1",
            justification=None,
            source_ingestion_id="ingestion-1",
        )
        us_repo.mark_user_story_delete_suggested.assert_awaited_once_with(
            user_story_id="story-1", justification=None, source_ingestion_id="ingestion-1"
        )

    @pytest.mark.asyncio
    async def test_missing_uuid_is_skipped(self, service, mf_repo, us_repo):
        await service.handle_deletes(PROJECT_ID, [{"type": "module"}])
        mf_repo.mark_module_delete_suggested.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_type_is_skipped(self, service, mf_repo, us_repo):
        await service.handle_deletes(PROJECT_ID, [{"uuid": "x-1", "type": "bogus"}])
        mf_repo.mark_module_delete_suggested.assert_not_called()
        mf_repo.mark_feature_delete_suggested.assert_not_called()
        us_repo.mark_user_story_delete_suggested.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_match_found_does_not_raise(self, service, mf_repo):
        mf_repo.mark_module_delete_suggested.return_value = False
        # Should complete without raising even though nothing matched.
        await service.handle_deletes(PROJECT_ID, [{"uuid": "mod-missing", "type": "module"}])

    @pytest.mark.asyncio
    async def test_repo_exception_is_swallowed_and_does_not_abort_remaining_entries(
        self, service, mf_repo, us_repo
    ):
        mf_repo.mark_module_delete_suggested.side_effect = RuntimeError("boom")
        await service.handle_deletes(
            PROJECT_ID,
            [
                {"uuid": "mod-1", "type": "module"},
                {"uuid": "story-1", "type": "user_story"},
            ],
        )
        us_repo.mark_user_story_delete_suggested.assert_awaited_once()


# ── handle_history ───────────────────────────────────────────────────────────


class TestHandleHistory:
    @pytest.mark.asyncio
    async def test_writes_a_row_with_all_fields(self, service, uow):
        await service.handle_history(
            PROJECT_ID,
            uow,
            updates=[{"a": 1}],
            adds=[{"b": 2}],
            deletes=[{"c": 3}],
            flags=[{"d": 4}],
            persona_glossary_additions=[{"e": 5}],
            meeting_summary={"f": 6},
        )
        uow.incremental_histories.create.assert_called_once_with(
            project_id=PROJECT_ID,
            updates_json=[{"a": 1}],
            adds_json=[{"b": 2}],
            delete_json=[{"c": 3}],
            flag_json=[{"d": 4}],
            persona_glossary_additions_json=[{"e": 5}],
            meeting_summary_json={"f": 6},
        )

    @pytest.mark.asyncio
    async def test_always_writes_a_row_even_when_everything_is_empty(self, service, uow):
        await service.handle_history(PROJECT_ID, uow)
        uow.incremental_histories.create.assert_called_once_with(
            project_id=PROJECT_ID,
            updates_json=None,
            adds_json=None,
            delete_json=None,
            flag_json=None,
            persona_glossary_additions_json=None,
            meeting_summary_json=None,
        )

    @pytest.mark.asyncio
    async def test_repo_exception_propagates(self, service, uow):
        uow.incremental_histories.create.side_effect = RuntimeError("db down")
        with pytest.raises(RuntimeError, match="db down"):
            await service.handle_history(PROJECT_ID, uow, updates=[{"a": 1}])


# ── _update_module ────────────────────────────────────────────────────────


class TestUpdateModule:
    @pytest.mark.asyncio
    async def test_unchanged_is_a_no_op(self, service, mf_repo):
        await service._update_module(PROJECT_ID, {"changed": False, "module_id": "mod-1"})
        mf_repo.update_module.assert_not_called()
        mf_repo.snapshot_module_version.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_module_id_is_skipped(self, service, mf_repo):
        await service._update_module(PROJECT_ID, {"changed": True})
        mf_repo.update_module.assert_not_called()

    @pytest.mark.asyncio
    async def test_success_path_snapshots_then_updates_with_ready_status(self, service, mf_repo):
        module_data = {
            "changed": True,
            "module_id": "mod-1",
            "module_code": "1",
            "module_name": "Module",
            "module_description": "desc",
            "text_diffs": {"name": []},
        }
        await service._update_module(PROJECT_ID, module_data)

        mf_repo.snapshot_module_version.assert_awaited_once_with(PROJECT_ID, "mod-1")
        mf_repo.update_module.assert_awaited_once()
        _, kwargs = mf_repo.update_module.await_args
        assert kwargs["status"] == ModuleFeatureStatus.READY.value
        assert kwargs["rfp_flagged_item_json"] is None
        assert kwargs["incremental_change_type"] == ChangeType.UPDATED
        assert json.loads(kwargs["text_diffs_json"]) == {"name": []}
        assert kwargs["source_ingestion_id"] is None

    @pytest.mark.asyncio
    async def test_source_ingestion_id_is_passed_through(self, service, mf_repo):
        await service._update_module(
            PROJECT_ID,
            {"changed": True, "module_id": "mod-1"},
            source_ingestion_id="ingestion-1",
        )
        _, kwargs = mf_repo.update_module.await_args
        assert kwargs["source_ingestion_id"] == "ingestion-1"

    @pytest.mark.asyncio
    async def test_matching_flag_sets_failed_status_and_flag_json(self, service, mf_repo):
        module_data = {"changed": True, "module_id": "mod-1", "item_code": "M1"}
        flag_map = {"M1": {"entity_id": "mod-1", "entity_type": "module", "issue": "bad"}}
        await service._update_module(PROJECT_ID, module_data, flag_map)

        _, kwargs = mf_repo.update_module.await_args
        assert kwargs["status"] == ModuleFeatureStatus.FAILED.value
        assert json.loads(kwargs["rfp_flagged_item_json"]) == flag_map["M1"]

    @pytest.mark.asyncio
    async def test_flag_for_different_entity_type_is_not_applied(self, service, mf_repo):
        module_data = {"changed": True, "module_id": "mod-1", "item_code": "M1"}
        flag_map = {"M1": {"entity_id": "x", "entity_type": "feature", "issue": "bad"}}
        await service._update_module(PROJECT_ID, module_data, flag_map)

        _, kwargs = mf_repo.update_module.await_args
        assert kwargs["status"] == ModuleFeatureStatus.READY.value
        assert kwargs["rfp_flagged_item_json"] is None

    @pytest.mark.asyncio
    async def test_snapshot_exception_is_swallowed_and_update_still_runs(self, service, mf_repo):
        mf_repo.snapshot_module_version.side_effect = RuntimeError("boom")
        await service._update_module(PROJECT_ID, {"changed": True, "module_id": "mod-1"})
        mf_repo.update_module.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_returns_false_does_not_raise(self, service, mf_repo):
        mf_repo.update_module.return_value = False
        await service._update_module(PROJECT_ID, {"changed": True, "module_id": "mod-1"})

    @pytest.mark.asyncio
    async def test_update_exception_is_swallowed(self, service, mf_repo):
        mf_repo.update_module.side_effect = RuntimeError("boom")
        await service._update_module(PROJECT_ID, {"changed": True, "module_id": "mod-1"})


# ── _update_feature ───────────────────────────────────────────────────────


class TestUpdateFeature:
    @pytest.mark.asyncio
    async def test_unchanged_is_a_no_op(self, service, mf_repo):
        await service._update_feature(PROJECT_ID, {"changed": False, "feature_id": "fea-1"})
        mf_repo.update_feature.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_feature_id_is_skipped(self, service, mf_repo):
        await service._update_feature(PROJECT_ID, {"changed": True})
        mf_repo.update_feature.assert_not_called()

    @pytest.mark.asyncio
    async def test_success_path_serializes_functions_and_sources(self, service, mf_repo):
        feature_data = {
            "changed": True,
            "feature_id": "fea-1",
            "feature_code": "1.1",
            "feature_name": "Feature",
            "functions": [{"fun_code": "F1", "name": "fn"}],
            "sources": [{"source_id": "s1", "pages": []}],
        }
        await service._update_feature(PROJECT_ID, feature_data)

        mf_repo.snapshot_feature_version.assert_awaited_once_with(PROJECT_ID, "fea-1")
        _, kwargs = mf_repo.update_feature.await_args
        assert json.loads(kwargs["functions_json"]) == feature_data["functions"]
        assert json.loads(kwargs["sources_json"]) == feature_data["sources"]
        assert kwargs["status"] == ModuleFeatureStatus.READY.value
        assert kwargs["source_ingestion_id"] is None

    @pytest.mark.asyncio
    async def test_source_ingestion_id_is_passed_through(self, service, mf_repo):
        await service._update_feature(
            PROJECT_ID,
            {"changed": True, "feature_id": "fea-1"},
            source_ingestion_id="ingestion-1",
        )
        _, kwargs = mf_repo.update_feature.await_args
        assert kwargs["source_ingestion_id"] == "ingestion-1"

    @pytest.mark.asyncio
    async def test_matching_flag_sets_failed_status(self, service, mf_repo):
        feature_data = {"changed": True, "feature_id": "fea-1", "item_code": "F1"}
        flag_map = {"F1": {"entity_id": "fea-1", "entity_type": "feature", "issue": "bad"}}
        await service._update_feature(PROJECT_ID, feature_data, flag_map)

        _, kwargs = mf_repo.update_feature.await_args
        assert kwargs["status"] == ModuleFeatureStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_snapshot_exception_is_swallowed(self, service, mf_repo):
        mf_repo.snapshot_feature_version.side_effect = RuntimeError("boom")
        await service._update_feature(PROJECT_ID, {"changed": True, "feature_id": "fea-1"})
        mf_repo.update_feature.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_returns_false_does_not_raise(self, service, mf_repo):
        mf_repo.update_feature.return_value = False
        await service._update_feature(PROJECT_ID, {"changed": True, "feature_id": "fea-1"})

    @pytest.mark.asyncio
    async def test_update_exception_is_swallowed(self, service, mf_repo):
        mf_repo.update_feature.side_effect = RuntimeError("boom")
        await service._update_feature(PROJECT_ID, {"changed": True, "feature_id": "fea-1"})


# ── _update_user_story ────────────────────────────────────────────────────


class TestUpdateUserStory:
    @pytest.mark.asyncio
    async def test_unchanged_is_a_no_op(self, service, us_repo):
        await service._update_user_story(
            PROJECT_ID, "fea-1", {"changed": False, "user_story_id": "s1"}
        )
        us_repo.bulk_upsert_user_stories_for_project.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_story_id_is_skipped(self, service, us_repo):
        await service._update_user_story(PROJECT_ID, "fea-1", {"changed": True})
        us_repo.bulk_upsert_user_stories_for_project.assert_not_called()

    @pytest.mark.asyncio
    async def test_success_path_upserts_then_hard_resets_status_and_flag(self, service, us_repo):
        story_data = {"changed": True, "user_story_id": "story-1", "user_story_code": "U.S 1.1.1"}
        await service._update_user_story(PROJECT_ID, "fea-1", story_data)

        us_repo.snapshot_user_story_version.assert_awaited_once_with("story-1")
        us_repo.bulk_upsert_user_stories_for_project.assert_awaited_once()
        _, kwargs = us_repo.bulk_upsert_user_stories_for_project.await_args
        model: UserStoryModel = kwargs["user_stories"][0]
        assert model.status == UserStoryStatus.READY.value
        assert model.source_ingestion_id is None

        us_repo.set_user_story_status_and_flag.assert_awaited_once_with(
            "story-1", status=model.status, rfp_flagged_item=model.rfp_flagged_item
        )
        us_repo.update_user_story_sync_flags.assert_awaited_once_with(
            "story-1", is_jira_synced=False, is_tap_synced=False
        )

    @pytest.mark.asyncio
    async def test_sync_flag_reset_exception_is_swallowed(self, service, us_repo):
        us_repo.update_user_story_sync_flags.side_effect = RuntimeError("boom")
        await service._update_user_story(
            PROJECT_ID, "fea-1", {"changed": True, "user_story_id": "story-1"}
        )
        us_repo.set_user_story_status_and_flag.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_source_ingestion_id_is_passed_through(self, service, us_repo):
        await service._update_user_story(
            PROJECT_ID,
            "fea-1",
            {"changed": True, "user_story_id": "story-1", "user_story_code": "U.S 1.1.1"},
            source_ingestion_id="ingestion-1",
        )
        _, kwargs = us_repo.bulk_upsert_user_stories_for_project.await_args
        model: UserStoryModel = kwargs["user_stories"][0]
        assert model.source_ingestion_id == "ingestion-1"

    @pytest.mark.asyncio
    async def test_snapshot_exception_is_swallowed_and_upsert_still_runs(self, service, us_repo):
        us_repo.snapshot_user_story_version.side_effect = RuntimeError("boom")
        await service._update_user_story(
            PROJECT_ID, "fea-1", {"changed": True, "user_story_id": "story-1"}
        )
        us_repo.bulk_upsert_user_stories_for_project.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_upsert_exception_is_swallowed_and_status_reset_still_runs(
        self, service, us_repo
    ):
        us_repo.bulk_upsert_user_stories_for_project.side_effect = RuntimeError("boom")
        await service._update_user_story(
            PROJECT_ID, "fea-1", {"changed": True, "user_story_id": "story-1"}
        )
        us_repo.set_user_story_status_and_flag.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_status_reset_exception_is_swallowed(self, service, us_repo):
        us_repo.set_user_story_status_and_flag.side_effect = RuntimeError("boom")
        await service._update_user_story(
            PROJECT_ID, "fea-1", {"changed": True, "user_story_id": "story-1"}
        )


# ── _create_module ───────────────────────────────────────────────────────


class TestCreateModule:
    @pytest.mark.asyncio
    async def test_unchanged_returns_existing_module_id(self, service, mf_repo):
        module_id = await service._create_module(
            PROJECT_ID, {"changed": False, "module_id": "mod-1"}
        )
        assert module_id == "mod-1"
        mf_repo.create_module.assert_not_called()

    @pytest.mark.asyncio
    async def test_unchanged_generates_id_when_absent(self, service, mf_repo):
        module_id = await service._create_module(PROJECT_ID, {"changed": False})
        uuid.UUID(module_id)  # must be a valid UUID string

    @pytest.mark.asyncio
    async def test_changed_creates_module_with_next_code_and_ready_status(self, service, mf_repo):
        mf_repo.get_max_mod_code.return_value = 3
        mf_repo.create_module.return_value = ModuleModel(
            id="server-id",
            project_id=PROJECT_ID,
            mod_code="4",
            name="Module",
            description=None,
            features=[],
        )
        module_id = await service._create_module(
            PROJECT_ID, {"changed": True, "module_name": "Module", "module_description": "d"}
        )

        assert module_id == "server-id"  # MERGE-resolved id, not the locally generated one
        _, kwargs = mf_repo.create_module.await_args
        model: ModuleModel = kwargs["module"]
        assert model.mod_code == "4"
        assert model.status == ModuleFeatureStatus.READY
        assert model.incremental_change_type == ChangeType.ADDED
        assert model.source_ingestion_id is None

    @pytest.mark.asyncio
    async def test_source_ingestion_id_is_passed_through(self, service, mf_repo):
        mf_repo.create_module.return_value = ModuleModel(
            id="server-id",
            project_id=PROJECT_ID,
            mod_code="1",
            name="Module",
            description=None,
            features=[],
        )
        await service._create_module(
            PROJECT_ID,
            {"changed": True, "module_name": "Module"},
            source_ingestion_id="ingestion-1",
        )
        _, kwargs = mf_repo.create_module.await_args
        model: ModuleModel = kwargs["module"]
        assert model.source_ingestion_id == "ingestion-1"

    @pytest.mark.asyncio
    async def test_matching_flag_creates_module_with_failed_status(self, service, mf_repo):
        mf_repo.create_module.return_value = ModuleModel(
            id="server-id",
            project_id=PROJECT_ID,
            mod_code="1",
            name="M",
            description=None,
            features=[],
        )
        flag_map = {"M1": {"entity_id": "x", "entity_type": "module", "issue": "bad"}}
        await service._create_module(PROJECT_ID, {"changed": True, "item_code": "M1"}, flag_map)

        _, kwargs = mf_repo.create_module.await_args
        model: ModuleModel = kwargs["module"]
        assert model.status == ModuleFeatureStatus.FAILED
        assert model.rfp_flagged_item == flag_map["M1"]

    @pytest.mark.asyncio
    async def test_create_exception_returns_locally_generated_id_without_raising(
        self, service, mf_repo
    ):
        mf_repo.create_module.side_effect = RuntimeError("boom")
        module_id = await service._create_module(
            PROJECT_ID, {"changed": True, "module_id": "local-id", "module_name": "M"}
        )
        assert module_id == "local-id"


# ── _create_feature ──────────────────────────────────────────────────────


class TestCreateFeature:
    @pytest.mark.asyncio
    async def test_unchanged_returns_existing_feature_id(self, service, mf_repo):
        feature_id = await service._create_feature(
            PROJECT_ID, "mod-1", {"changed": False, "feature_id": "fea-1"}
        )
        assert feature_id == "fea-1"
        mf_repo.create_feature.assert_not_called()
        mf_repo.get_max_fea_code.assert_not_called()

    @pytest.mark.asyncio
    async def test_module_not_found_skips_creation(self, service, mf_repo):
        mf_repo.get_max_fea_code.return_value = ("", 0)
        feature_id = await service._create_feature(
            PROJECT_ID, "missing-mod", {"changed": True, "feature_name": "F"}
        )
        assert feature_id  # a generated id is still returned for linking
        mf_repo.create_feature.assert_not_called()

    @pytest.mark.asyncio
    async def test_changed_creates_feature_with_next_code(self, service, mf_repo):
        mf_repo.get_max_fea_code.return_value = ("2", 3)
        mf_repo.create_feature.return_value = FeatureModel(
            id="server-fea-id",
            project_id=PROJECT_ID,
            module_id="mod-1",
            fea_code="2.4",
            name="Feature",
            description=None,
        )
        feature_data = {
            "changed": True,
            "feature_name": "Feature",
            "functions": [{"fun_code": "F1", "name": "fn", "description": "d"}],
        }
        feature_id = await service._create_feature(PROJECT_ID, "mod-1", feature_data)

        assert feature_id == "server-fea-id"
        _, kwargs = mf_repo.create_feature.await_args
        model: FeatureModel = kwargs["feature"]
        assert model.fea_code == "2.4"
        assert model.functions[0].fun_code == "F1"
        assert model.status == ModuleFeatureStatus.READY
        assert model.incremental_change_type == ChangeType.ADDED
        assert model.source_ingestion_id is None

    @pytest.mark.asyncio
    async def test_source_ingestion_id_is_passed_through(self, service, mf_repo):
        mf_repo.get_max_fea_code.return_value = ("1", 0)
        mf_repo.create_feature.return_value = FeatureModel(
            id="server-fea-id",
            project_id=PROJECT_ID,
            module_id="mod-1",
            fea_code="1.1",
            name="Feature",
            description=None,
        )
        await service._create_feature(
            PROJECT_ID,
            "mod-1",
            {"changed": True, "feature_name": "Feature"},
            source_ingestion_id="ingestion-1",
        )
        _, kwargs = mf_repo.create_feature.await_args
        model: FeatureModel = kwargs["feature"]
        assert model.source_ingestion_id == "ingestion-1"

    @pytest.mark.asyncio
    async def test_create_exception_returns_locally_generated_id_without_raising(
        self, service, mf_repo
    ):
        mf_repo.get_max_fea_code.return_value = ("1", 0)
        mf_repo.create_feature.side_effect = RuntimeError("boom")
        feature_id = await service._create_feature(
            PROJECT_ID,
            "mod-1",
            {"changed": True, "feature_id": "local-fea-id", "feature_name": "F"},
        )
        assert feature_id == "local-fea-id"


# ── _create_user_story ───────────────────────────────────────────────────


class TestCreateUserStory:
    @pytest.mark.asyncio
    async def test_unchanged_is_a_no_op(self, service, us_repo):
        await service._create_user_story(PROJECT_ID, "fea-1", {"changed": False})
        us_repo.bulk_upsert_user_stories_for_project.assert_not_called()

    @pytest.mark.asyncio
    async def test_changed_builds_model_and_upserts(self, service, us_repo):
        story_data = {"changed": True, "user_story_code": "U.S 1.1.1", "title": "Story"}
        await service._create_user_story(PROJECT_ID, "fea-1", story_data)

        us_repo.bulk_upsert_user_stories_for_project.assert_awaited_once()
        _, kwargs = us_repo.bulk_upsert_user_stories_for_project.await_args
        model: UserStoryModel = kwargs["user_stories"][0]
        assert model.incremental_change_type == ChangeType.ADDED
        assert model.version == 1
        assert model.source_ingestion_id is None

    @pytest.mark.asyncio
    async def test_source_ingestion_id_is_passed_through(self, service, us_repo):
        await service._create_user_story(
            PROJECT_ID,
            "fea-1",
            {"changed": True, "user_story_code": "U.S 1.1.1"},
            source_ingestion_id="ingestion-1",
        )
        _, kwargs = us_repo.bulk_upsert_user_stories_for_project.await_args
        model: UserStoryModel = kwargs["user_stories"][0]
        assert model.source_ingestion_id == "ingestion-1"

    @pytest.mark.asyncio
    async def test_upsert_exception_is_swallowed(self, service, us_repo):
        us_repo.bulk_upsert_user_stories_for_project.side_effect = RuntimeError("boom")
        await service._create_user_story(
            PROJECT_ID, "fea-1", {"changed": True, "user_story_code": "U.S 1.1.1"}
        )


# ── _build_user_story_model_for_create ───────────────────────────────────


class TestBuildUserStoryModelForCreate:
    @pytest.mark.asyncio
    async def test_auto_generates_next_code_when_feature_id_present(self, service, us_repo):
        us_repo.get_max_user_story_code.return_value = ("1.1", 2)
        model = await service._build_user_story_model_for_create(
            {"user_story_code": "ignored-ai-code"}, "fea-1", PROJECT_ID
        )
        assert model.user_story_code == "U.S 1.1.3"
        assert model.version == 1

    @pytest.mark.asyncio
    async def test_uses_supplied_code_when_no_feature_id(self, service, us_repo):
        model = await service._build_user_story_model_for_create(
            {"user_story_code": "U.S 1.1.1"}, None, PROJECT_ID
        )
        assert model.user_story_code == "U.S 1.1.1"
        us_repo.get_max_user_story_code.assert_not_called()

    @pytest.mark.asyncio
    async def test_normalizes_incoming_nfrs(self, service, us_repo):
        story_data = {
            "user_story_code": "U.S 1.1.1",
            "nfrs": [{"id": "n1", "category": "perf", "description": "fast"}],
        }
        model = await service._build_user_story_model_for_create(story_data, "fea-1", PROJECT_ID)
        assert model.nfrs == [
            {"id": "n1", "category": "perf", "description": "fast", "requirement": ""}
        ]

    @pytest.mark.asyncio
    async def test_no_nfrs_defaults_to_empty_list(self, service, us_repo):
        model = await service._build_user_story_model_for_create(
            {"user_story_code": "U.S 1.1.1"}, "fea-1", PROJECT_ID
        )
        assert model.nfrs == []

    @pytest.mark.asyncio
    async def test_matching_flag_sets_failed_status(self, service, us_repo):
        flag_map = {"U1": {"entity_id": "x", "entity_type": "user_story", "issue": "bad"}}
        model = await service._build_user_story_model_for_create(
            {"user_story_code": "U.S 1.1.1", "item_code": "U1"}, "fea-1", PROJECT_ID, flag_map
        )
        assert model.status == UserStoryStatus.FAILED.value
        assert model.rfp_flagged_item == flag_map["U1"]

    @pytest.mark.asyncio
    async def test_no_matching_flag_sets_ready_status(self, service, us_repo):
        model = await service._build_user_story_model_for_create(
            {"user_story_code": "U.S 1.1.1"}, "fea-1", PROJECT_ID, {}
        )
        assert model.status == UserStoryStatus.READY.value
        assert model.rfp_flagged_item is None

    @pytest.mark.asyncio
    async def test_source_ingestion_id_is_stamped_when_provided(self, service, us_repo):
        model = await service._build_user_story_model_for_create(
            {"user_story_code": "U.S 1.1.1"}, "fea-1", PROJECT_ID, None, "ingestion-1"
        )
        assert model.source_ingestion_id == "ingestion-1"

    @pytest.mark.asyncio
    async def test_id_is_deterministic_for_same_project_and_code(self, service, us_repo):
        model1 = await service._build_user_story_model_for_create(
            {"user_story_code": "U.S 1.1.1"}, None, PROJECT_ID
        )
        model2 = await service._build_user_story_model_for_create(
            {"user_story_code": "U.S 1.1.1"}, None, PROJECT_ID
        )
        assert model1.id == model2.id


# ── _build_user_story_model_for_update ───────────────────────────────────


class TestBuildUserStoryModelForUpdate:
    @pytest.mark.asyncio
    async def test_increments_version_from_existing(self, service, us_repo):
        us_repo.get_user_story_version_by_id.return_value = 4
        model = await service._build_user_story_model_for_update(
            {"user_story_id": "story-1", "user_story_code": "U.S 1.1.1"}, "fea-1", PROJECT_ID
        )
        assert model.version == 5

    @pytest.mark.asyncio
    async def test_increments_version_even_when_project_scoped_detail_fetch_misses(
        self, service, us_repo
    ):
        """Regression test: a full Project->Module->Feature->UserStory traversal
        miss on ``get_user_story_detail_for_project`` (e.g. a not-yet-relinked
        relationship) must not silently reset an existing story's version back
        to 1 — the version must still come from ``get_user_story_version_by_id``,
        which matches by id alone."""
        us_repo.get_user_story_detail_for_project.return_value = None
        us_repo.get_user_story_version_by_id.return_value = 4
        model = await service._build_user_story_model_for_update(
            {"user_story_id": "story-1", "user_story_code": "U.S 1.1.1"}, "fea-1", PROJECT_ID
        )
        assert model.version == 5

    @pytest.mark.asyncio
    async def test_falls_back_to_version_one_when_fetch_raises(self, service, us_repo):
        us_repo.get_user_story_version_by_id.side_effect = RuntimeError("boom")
        model = await service._build_user_story_model_for_update(
            {"user_story_id": "story-1", "user_story_code": "U.S 1.1.1"}, "fea-1", PROJECT_ID
        )
        assert model.version == 1

    @pytest.mark.asyncio
    async def test_falls_back_to_version_one_when_not_found(self, service, us_repo):
        us_repo.get_user_story_version_by_id.return_value = None
        model = await service._build_user_story_model_for_update(
            {"user_story_id": "story-1", "user_story_code": "U.S 1.1.1"}, "fea-1", PROJECT_ID
        )
        assert model.version == 1

    @pytest.mark.asyncio
    async def test_incoming_nfrs_override_existing(self, service, us_repo):
        us_repo.get_user_story_detail_for_project.return_value = UserStoryModel(
            id="story-1",
            user_story_code="U.S 1.1.1",
            title="t",
            description=None,
            consensus=1.0,
            status="ready",
            version=1,
            nfrs=[{"id": "old", "category": "x", "description": "y", "requirement": ""}],
        )
        story_data = {
            "user_story_id": "story-1",
            "user_story_code": "U.S 1.1.1",
            "nfrs": [{"id": "new", "category": "perf", "description": "fast"}],
        }
        model = await service._build_user_story_model_for_update(story_data, "fea-1", PROJECT_ID)
        assert model.nfrs[0]["id"] == "new"

    @pytest.mark.asyncio
    async def test_falls_back_to_existing_nfrs_when_not_supplied(self, service, us_repo):
        us_repo.get_user_story_detail_for_project.return_value = UserStoryModel(
            id="story-1",
            user_story_code="U.S 1.1.1",
            title="t",
            description=None,
            consensus=1.0,
            status="ready",
            version=1,
            nfrs=[{"id": "old", "category": "x", "description": "y", "requirement": ""}],
        )
        model = await service._build_user_story_model_for_update(
            {"user_story_id": "story-1", "user_story_code": "U.S 1.1.1"}, "fea-1", PROJECT_ID
        )
        assert model.nfrs[0]["id"] == "old"

    @pytest.mark.asyncio
    async def test_matching_flag_sets_failed_status(self, service, us_repo):
        flag_map = {"U1": {"entity_id": "x", "entity_type": "user_story", "issue": "bad"}}
        model = await service._build_user_story_model_for_update(
            {"user_story_id": "story-1", "user_story_code": "U.S 1.1.1", "item_code": "U1"},
            "fea-1",
            PROJECT_ID,
            flag_map,
        )
        assert model.status == UserStoryStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_uses_llm_supplied_text_diffs_directly(self, service, us_repo):
        story_data = {
            "user_story_id": "story-1",
            "user_story_code": "U.S 1.1.1",
            "text_diffs": {"title": []},
        }
        model = await service._build_user_story_model_for_update(story_data, "fea-1", PROJECT_ID)
        assert model.text_diffs == {"title": []}
        assert model.incremental_change_type == ChangeType.UPDATED

    @pytest.mark.asyncio
    async def test_source_ingestion_id_is_stamped_when_provided(self, service, us_repo):
        model = await service._build_user_story_model_for_update(
            {"user_story_id": "story-1", "user_story_code": "U.S 1.1.1"},
            "fea-1",
            PROJECT_ID,
            None,
            "ingestion-1",
        )
        assert model.source_ingestion_id == "ingestion-1"


# ── static helpers ────────────────────────────────────────────────────────


class TestResolveFeatureId:
    def test_returns_feature_id_when_present(self):
        assert (
            IncrementalUpdateProcessorService._resolve_feature_id({"feature_id": "fea-1"})
            == "fea-1"
        )

    def test_returns_none_when_absent(self):
        assert IncrementalUpdateProcessorService._resolve_feature_id({}) is None


class TestResolveFlag:
    def test_matching_entity_type_returns_flag(self):
        flag_map = {"M1": {"entity_id": "x", "entity_type": "module", "issue": "bad"}}
        result = IncrementalUpdateProcessorService._resolve_flag(
            {"item_code": "M1"}, flag_map, "module"
        )
        assert result == flag_map["M1"]

    def test_mismatched_entity_type_returns_none(self):
        flag_map = {"M1": {"entity_id": "x", "entity_type": "feature", "issue": "bad"}}
        result = IncrementalUpdateProcessorService._resolve_flag(
            {"item_code": "M1"}, flag_map, "module"
        )
        assert result is None

    def test_missing_item_code_returns_none(self):
        flag_map = {"M1": {"entity_id": "x", "entity_type": "module", "issue": "bad"}}
        result = IncrementalUpdateProcessorService._resolve_flag({}, flag_map, "module")
        assert result is None

    def test_none_flag_map_returns_none(self):
        result = IncrementalUpdateProcessorService._resolve_flag(
            {"item_code": "M1"}, None, "module"
        )
        assert result is None


class TestGenerateUserStoryId:
    def test_deterministic_for_same_inputs(self):
        id1 = IncrementalUpdateProcessorService._generate_user_story_id(
            project_id="p1", user_story_code="U.S 1.1.1"
        )
        id2 = IncrementalUpdateProcessorService._generate_user_story_id(
            project_id="p1", user_story_code="U.S 1.1.1"
        )
        assert id1 == id2
        uuid.UUID(id1)

    def test_different_codes_produce_different_ids(self):
        id1 = IncrementalUpdateProcessorService._generate_user_story_id(
            project_id="p1", user_story_code="U.S 1.1.1"
        )
        id2 = IncrementalUpdateProcessorService._generate_user_story_id(
            project_id="p1", user_story_code="U.S 1.1.2"
        )
        assert id1 != id2

    def test_missing_inputs_generate_random_id(self):
        id1 = IncrementalUpdateProcessorService._generate_user_story_id()
        id2 = IncrementalUpdateProcessorService._generate_user_story_id()
        assert id1 != id2
        uuid.UUID(id1)


class TestExtractRfpFlagMap:
    def test_clean_run_returns_empty_map(self):
        result = IncrementalUpdateProcessorService.extract_rfp_flag_map(
            {"status": "SUCCESS", "generation_metadata": {"flagged_items": [{"entity_id": "x"}]}}
        )
        assert result == {}

    def test_top_level_fail_status_builds_map(self):
        result = IncrementalUpdateProcessorService.extract_rfp_flag_map(
            {
                "status": "FAIL_CORRECTION_EXHAUSTED",
                "generation_metadata": {
                    "flagged_items": [
                        {
                            "entity_id": "U3",
                            "entity_type": "user_story",
                            "user_summary": "bad story",
                        }
                    ]
                },
            }
        )
        assert result == {
            "U3": {
                "entity_id": "U3",
                "entity_type": "user_story",
                "issue": "bad story",
                "suggested_fix": "",
            }
        }

    def test_final_status_fail_builds_map(self):
        result = IncrementalUpdateProcessorService.extract_rfp_flag_map(
            {
                "status": "OK",
                "generation_metadata": {
                    "final_status": "FAIL",
                    "flagged_items": [
                        {"entity_id": "M1", "entity_type": "module", "user_summary": "x"}
                    ],
                },
            }
        )
        assert "M1" in result

    def test_skips_entries_missing_entity_id(self):
        result = IncrementalUpdateProcessorService.extract_rfp_flag_map(
            {
                "status": "FAIL",
                "generation_metadata": {"flagged_items": [{"entity_type": "module"}]},
            }
        )
        assert result == {}

    def test_skips_entries_with_unknown_entity_type(self):
        result = IncrementalUpdateProcessorService.extract_rfp_flag_map(
            {
                "status": "FAIL",
                "generation_metadata": {
                    "flagged_items": [{"entity_id": "x", "entity_type": "bogus"}]
                },
            }
        )
        assert result == {}

    def test_skips_non_dict_entries(self):
        result = IncrementalUpdateProcessorService.extract_rfp_flag_map(
            {"status": "FAIL", "generation_metadata": {"flagged_items": ["not-a-dict"]}}
        )
        assert result == {}

    def test_no_flagged_items_key_returns_empty_map(self):
        result = IncrementalUpdateProcessorService.extract_rfp_flag_map({"status": "FAIL"})
        assert result == {}
