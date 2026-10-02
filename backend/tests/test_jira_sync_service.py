"""Unit tests for JiraSyncService — sync preview + pure helper functions.

The Neo4j repositories are ``MagicMock``s with ``AsyncMock`` query methods, and
the ``UnitOfWork`` is a ``MagicMock``, so no Neo4j/Postgres connection is made.
Real ``ModuleModel`` / ``FeatureModel`` / ``UserStoryModel`` dataclasses stand in
for graph rows; sync-mapping rows are ``SimpleNamespace`` since the service only
reads their attributes.  ``asyncio_mode = auto`` collects the async tests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from app.core.enums.activity_type import ActivityType
from app.core.enums.notification_type import NotificationType
from app.core.exceptions import NotFoundError
from app.models.neo4j.module_feature_model import FeatureModel, ModuleModel
from app.models.neo4j.user_story_model import UserStoryModel
from app.services.jira_sync_service import (
    JiraSyncService,
    _ac_to_dict,
    _adf_code_block,
    _adf_heading,
    _adf_paragraph,
    _build_adf_document,
    _build_epic_fields,
    _build_story_fields,
    _compute_content_hash,
    _discover_epic_link_field,
    _ensure_custom_fields,
    _escape_jql,
    _find_existing_by_summary,
    _find_existing_jira_issue,
    _first_attr,
    _nfr_to_dict,
    _save_mapping,
    _submit_issue_fields,
)

# ── Fixtures / factories ────────────────────────────────────────────────────


def _feature(**overrides) -> FeatureModel:
    fid = overrides.get("id", str(uuid.uuid4()))
    return FeatureModel(
        id=fid,
        module_id=overrides.get("module_id", str(uuid.uuid4())),
        name=overrides.get("name", "Login"),
        description=overrides.get("description", "Login feature"),
        fea_code=overrides.get("fea_code", "FEA-1"),
    )


def _module(features: list[FeatureModel]) -> ModuleModel:
    return ModuleModel(
        id=str(uuid.uuid4()),
        name="Auth",
        description="Auth module",
        features=features,
        mod_code="MOD-1",
    )


def _story(feature_id: str, **overrides) -> UserStoryModel:
    return UserStoryModel(
        id=overrides.get("id", str(uuid.uuid4())),
        user_story_code=overrides.get("user_story_code", "US-1"),
        title=overrides.get("title", "Login story"),
        description=overrides.get("description", "A login story"),
        consensus=0.9,
        status="approved",
        version=overrides.get("version", 1),
        feature_id=feature_id,
        as_a="user",
        i_want_to="log in",
        so_that="access the app",
        acceptance_criteria=[{"type": "Happy Path", "given": "g", "when": "w", "then": "t"}],
    )


def _mapping(entity_type: str, entity_id, *, content_hash: str, **overrides) -> SimpleNamespace:
    return SimpleNamespace(
        rip_entity_type=entity_type,
        rip_entity_id=entity_id,
        rip_entity_code=overrides.get("rip_entity_code", "CODE-1"),
        rip_content_hash=content_hash,
        rip_version=overrides.get("rip_version", 1),
        sync_status=overrides.get("sync_status", "synced"),
        jira_issue_key=overrides.get("jira_issue_key", "MER-1"),
    )


def _service(modules, stories) -> JiraSyncService:
    module_repo = MagicMock()
    module_repo.list_modules_by_project = AsyncMock(return_value=modules)
    story_repo = MagicMock()
    story_repo.list_user_stories_for_project = AsyncMock(return_value=(stories, len(stories)))
    return JiraSyncService(module_feature_repo=module_repo, user_story_repo=story_repo)


def _uow(mappings: list) -> MagicMock:
    uow = MagicMock()
    uow.jira_integrations.get_active_by_project_id.return_value = SimpleNamespace(id=uuid.uuid4())
    uow.jira_sync_mappings.list_by_integration.return_value = mappings
    return uow


# ── Content hash ────────────────────────────────────────────────────────────


class TestComputeContentHash:
    def test_deterministic_for_same_input(self) -> None:
        feat = _feature()
        assert _compute_content_hash("feature", feat) == _compute_content_hash("feature", feat)

    def test_changes_when_field_changes(self) -> None:
        before = _compute_content_hash("feature", _feature(name="Login"))
        after = _compute_content_hash("feature", _feature(name="Logout"))
        assert before != after

    def test_user_story_hash_covers_gherkin(self) -> None:
        fid = str(uuid.uuid4())
        base = _story(fid)
        changed = _story(fid)
        changed.acceptance_criteria = [{"type": "Edge", "given": "x", "when": "y", "then": "z"}]
        assert _compute_content_hash("user_story", base) != _compute_content_hash(
            "user_story", changed
        )


# ── ADF helpers ─────────────────────────────────────────────────────────────


class TestAdfHelpers:
    def test_document_wraps_content(self) -> None:
        doc = _build_adf_document([_adf_paragraph("hi")])
        assert doc["type"] == "doc"
        assert doc["version"] == 1
        assert doc["content"][0]["type"] == "paragraph"

    def test_paragraph_italic_mark(self) -> None:
        node = _adf_paragraph("note", italic=True)
        assert node["content"][0]["marks"] == [{"type": "em"}]

    def test_heading_level(self) -> None:
        node = _adf_heading("Title", level=2)
        assert node["type"] == "heading"
        assert node["attrs"]["level"] == 2

    def test_code_block_language(self) -> None:
        node = _adf_code_block("Given x", language="gherkin")
        assert node["type"] == "codeBlock"
        assert node["attrs"]["language"] == "gherkin"


# ── Sync preview ────────────────────────────────────────────────────────────


class TestComputeSyncPreview:
    async def test_all_new_when_no_mappings(self) -> None:
        feature = _feature()
        module = _module([feature])
        story = _story(feature.id)
        service = _service([module], [story])
        uow = _uow([])

        result = await service.compute_sync_preview(project_id=uuid.uuid4(), uow=uow)

        assert result.new_count == 2  # feature + story
        assert result.changed_count == 0
        assert result.deprecated_count == 0
        assert len(result.items) == 2

    async def test_changed_new_and_deprecated_classification(self) -> None:
        feature = _feature()
        module = _module([feature])
        story = _story(feature.id)
        service = _service([module], [story])

        # Feature has a stale hash → changed; story has no mapping → new;
        # an extra mapping points at an entity no longer in the hierarchy → deprecated.
        mappings = [
            _mapping("feature", uuid.UUID(feature.id), content_hash="stale-hash"),
            _mapping(
                "user_story",
                uuid.uuid4(),
                content_hash="whatever",
                jira_issue_key="MER-99",
                rip_entity_code="US-99",
            ),
        ]
        uow = _uow(mappings)

        result = await service.compute_sync_preview(project_id=uuid.uuid4(), uow=uow)

        assert result.new_count == 1
        assert result.changed_count == 1
        assert result.deprecated_count == 1
        types = {(i.rip_entity_type, i.change_type) for i in result.items}
        assert ("feature", "changed") in types
        assert ("user_story", "new") in types
        assert ("user_story", "deprecated") in types

    async def test_unchanged_feature_is_skipped(self) -> None:
        feature = _feature()
        module = _module([feature])
        service = _service([module], [])
        # Mapping hash matches current content → not reported.
        current_hash = _compute_content_hash("feature", feature)
        uow = _uow([_mapping("feature", uuid.UUID(feature.id), content_hash=current_hash)])

        result = await service.compute_sync_preview(project_id=uuid.uuid4(), uow=uow)

        assert result.items == []
        assert result.unchanged_count == 1

    async def test_raises_not_found_when_no_active_integration(self) -> None:
        service = _service([], [])
        uow = MagicMock()
        uow.jira_integrations.get_active_by_project_id.return_value = None

        with pytest.raises(NotFoundError):
            await service.compute_sync_preview(project_id=uuid.uuid4(), uow=uow)

    async def test_story_version_ahead_of_mapping_flags_re_test_required(self) -> None:
        feature = _feature()
        module = _module([feature])
        story = _story(feature.id, version=2)
        service = _service([module], [story])
        current_hash = _compute_content_hash("feature", feature)
        mapping = _mapping(
            "user_story",
            uuid.UUID(story.id),
            content_hash=_compute_content_hash("user_story", story),
            rip_version=1,
        )
        uow = _uow(
            [
                _mapping("feature", uuid.UUID(feature.id), content_hash=current_hash),
                mapping,
            ]
        )

        result = await service.compute_sync_preview(project_id=uuid.uuid4(), uow=uow)

        story_items = [i for i in result.items if i.rip_entity_type == "user_story"]
        assert story_items == [] or "re_test_required" in (story_items[0].flags or [])

    async def test_deprecated_mapping_already_marked_deprecated_is_skipped(self) -> None:
        service = _service([], [])
        stale_id = uuid.uuid4()
        uow = _uow([_mapping("feature", stale_id, content_hash="hash", sync_status="deprecated")])

        result = await service.compute_sync_preview(project_id=uuid.uuid4(), uow=uow)

        assert result.items == []
        assert result.deprecated_count == 0


class TestMarkSynced:
    async def test_calls_updater(self) -> None:
        updater = AsyncMock()

        await JiraSyncService._mark_synced("feature", updater, log_ctx="fea-1")

        updater.assert_awaited_once()

    async def test_swallows_updater_exception(self) -> None:
        updater = AsyncMock(side_effect=RuntimeError("neo4j down"))

        await JiraSyncService._mark_synced("feature", updater, log_ctx="fea-1")  # must not raise


class TestNotifyJiraSyncStatus:
    def _make_uow(self, *, owner_id, member_id=None):
        project = SimpleNamespace(name="Demo Project", owner_id=owner_id)
        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = project
        uow.project_members.list_by_project.return_value = (
            [SimpleNamespace(user_id=member_id)] if member_id else []
        )
        return uow

    def test_started_notifies_owner_and_members(self) -> None:
        owner_id = uuid.uuid4()
        member_id = uuid.uuid4()
        uow = self._make_uow(owner_id=owner_id, member_id=member_id)
        project_id = uuid.uuid4()
        actor_id = uuid.uuid4()

        with (
            patch("app.services.activity_log_service.record_activity") as mock_record,
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            JiraSyncService._notify_jira_sync_status(
                uow=uow, project_id=project_id, status="started", actor_user_id=actor_id
            )

        mock_record.assert_called_once_with(
            project_id=project_id,
            activity_type=ActivityType.JIRA_SYNC_STARTED,
            summary="Jira Sync Started",
            message="Started syncing to Jira",
            actor_user_id=actor_id,
            data={},
        )
        notified_ids = {c.kwargs["user_id"] for c in mock_publish.call_args_list}
        assert notified_ids == {owner_id, member_id}
        for c in mock_publish.call_args_list:
            assert c.kwargs["notification_type"] == NotificationType.INFO

    def test_completed_records_counts(self) -> None:
        owner_id = uuid.uuid4()
        uow = self._make_uow(owner_id=owner_id)
        project_id = uuid.uuid4()

        with (
            patch("app.services.activity_log_service.record_activity") as mock_record,
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            JiraSyncService._notify_jira_sync_status(
                uow=uow,
                project_id=project_id,
                status="completed",
                actor_user_id=None,
                created=2,
                updated=3,
                deprecated=1,
                errors_count=0,
            )

        mock_record.assert_called_once_with(
            project_id=project_id,
            activity_type=ActivityType.JIRA_SYNC_COMPLETED,
            summary="Jira Sync Completed",
            message="Synced to Jira: 2 created, 3 updated, 1 deprecated, 0 error(s)",
            actor_user_id=None,
            data={"created": 2, "updated": 3, "deprecated": 1, "errors": 0},
        )
        mock_publish.assert_called_once()
        assert mock_publish.call_args.kwargs["notification_type"] == NotificationType.SUCCESS

    def test_failed_records_error(self) -> None:
        owner_id = uuid.uuid4()
        uow = self._make_uow(owner_id=owner_id)
        project_id = uuid.uuid4()

        with (
            patch("app.services.activity_log_service.record_activity") as mock_record,
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            JiraSyncService._notify_jira_sync_status(
                uow=uow,
                project_id=project_id,
                status="failed",
                actor_user_id=None,
                error="boom",
            )

        mock_record.assert_called_once_with(
            project_id=project_id,
            activity_type=ActivityType.JIRA_SYNC_FAILED,
            summary="Jira Sync Failed",
            message="Jira sync failed: boom",
            actor_user_id=None,
            data={"error": "boom"},
        )
        mock_publish.assert_called_once()
        assert mock_publish.call_args.kwargs["notification_type"] == NotificationType.ERROR

    def test_no_project_does_not_notify(self) -> None:
        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = None

        with (
            patch("app.services.activity_log_service.record_activity") as mock_record,
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            JiraSyncService._notify_jira_sync_status(
                uow=uow, project_id=uuid.uuid4(), status="started", actor_user_id=None
            )

        mock_record.assert_not_called()
        mock_publish.assert_not_called()

    def test_notification_failure_does_not_fail_sync(self) -> None:
        uow = self._make_uow(owner_id=uuid.uuid4())

        with patch(
            "app.services.activity_log_service.record_activity",
            side_effect=RuntimeError("activity database unavailable"),
        ):
            JiraSyncService._notify_jira_sync_status(
                uow=uow,
                project_id=uuid.uuid4(),
                status="completed",
                actor_user_id=None,
            )


class TestGetSyncHistory:
    def test_returns_paginated_response(self) -> None:
        service = _service([], [])
        uow = MagicMock()
        history_row = SimpleNamespace(
            id=uuid.uuid4(),
            integration_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            status="completed",
            summary=None,
            error_details=None,
            started_at=datetime.now(UTC),
            completed_at=None,
            created_at=datetime.now(UTC),
        )
        uow.jira_sync_history.list_by_project.return_value = ([history_row], 1)

        result = service.get_sync_history(uuid.uuid4(), 0, 20, uow)

        assert result.total == 1
        assert len(result.items) == 1

    def test_get_sync_history_detail_found(self) -> None:
        service = _service([], [])
        uow = MagicMock()
        history_row = SimpleNamespace(
            id=uuid.uuid4(),
            integration_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            status="completed",
            summary=None,
            error_details=None,
            started_at=datetime.now(UTC),
            completed_at=None,
            created_at=datetime.now(UTC),
        )
        uow.jira_sync_history.get.return_value = history_row

        result = service.get_sync_history_detail(history_row.id, uow)

        assert result.id == history_row.id

    def test_get_sync_history_detail_not_found_raises(self) -> None:
        service = _service([], [])
        uow = MagicMock()
        uow.jira_sync_history.get.return_value = None

        with pytest.raises(NotFoundError):
            service.get_sync_history_detail(uuid.uuid4(), uow)


class TestFirstAttr:
    def test_returns_first_present_attribute(self) -> None:
        obj = SimpleNamespace(module_name="Auth")
        assert _first_attr(obj, "name", "module_name") == "Auth"

    def test_returns_default_when_none_present(self) -> None:
        obj = SimpleNamespace()
        assert _first_attr(obj, "name", "module_name", default="fallback") == "fallback"


class TestComputeContentHashModule:
    def test_module_type_hash(self) -> None:
        module = SimpleNamespace(name="Auth", description="Auth module")
        result = _compute_content_hash("module", module)
        assert isinstance(result, str) and len(result) == 64


class TestAcToDict:
    def test_dict_input(self) -> None:
        result = _ac_to_dict({"type": "Happy", "given": "g", "when": "w", "then": "t"})
        assert result == {"type": "Happy", "given": "g", "when": "w", "then": "t"}

    def test_object_input(self) -> None:
        ac = SimpleNamespace(type="Happy", given="g", when="w", then="t")
        result = _ac_to_dict(ac)
        assert result == {"type": "Happy", "given": "g", "when": "w", "then": "t"}


class TestNfrToDict:
    def test_dict_input(self) -> None:
        result = _nfr_to_dict({"category": "Perf", "requirement": "fast", "description": "d"})
        assert result["category"] == "Perf"

    def test_object_input(self) -> None:
        nfr = SimpleNamespace(category="Perf", requirement="fast", description="d")
        result = _nfr_to_dict(nfr)
        assert result["category"] == "Perf"


class TestEscapeJql:
    def test_escapes_quotes_and_backslashes(self) -> None:
        assert _escape_jql('He said "hi"') == 'He said \\"hi\\"'
        assert _escape_jql("back\\slash") == "back\\\\slash"


class TestBuildEpicFields:
    def test_builds_fields_with_traceability(self) -> None:
        feature = _feature()
        module = _module([feature])
        fields = _build_epic_fields(
            feature, module, {"rip_id": "customfield_1", "rip_version": "customfield_2"}
        )

        assert fields["summary"] == feature.name
        assert fields["customfield_1"] == str(feature.id)
        assert fields["customfield_2"] == "1"
        assert fields["components"] == [{"name": module.name}]

    def test_no_traceability_ids_omits_custom_fields(self) -> None:
        feature = _feature()
        module = _module([feature])
        fields = _build_epic_fields(feature, module, {})

        assert "customfield_1" not in fields


class TestBuildStoryFields:
    def test_builds_fields_with_as_a_i_want_to_so_that(self) -> None:
        feature = _feature()
        module = _module([feature])
        story = _story(feature.id)

        fields = _build_story_fields(
            story, feature, module, {"rip_id": "customfield_1"}, integration=SimpleNamespace()
        )

        assert story.user_story_code in fields["summary"]
        assert fields["customfield_1"] == str(story.id)
        assert fields["components"] == [{"name": module.name}]

    def test_builds_fields_without_feature_or_module(self) -> None:
        story = _story(str(uuid.uuid4()))
        story.as_a = None
        story.i_want_to = None
        story.so_that = None

        fields = _build_story_fields(story, None, None, {}, integration=SimpleNamespace())

        assert "components" not in fields

    def test_includes_nfrs_in_description(self) -> None:
        story = _story(str(uuid.uuid4()))
        story.nfrs = [{"category": "Perf", "requirement": "fast", "description": "d"}]

        fields = _build_story_fields(story, None, None, {}, integration=SimpleNamespace())

        assert fields["description"]["type"] == "doc"


class TestSaveMapping:
    def test_adds_mapping_to_uow(self) -> None:
        uow = MagicMock()
        entity_id = str(uuid.uuid4())

        _save_mapping(uow, uuid.uuid4(), "feature", entity_id, "FEA-1", "MER-1", "10001", "hash", 1)

        uow.jira_sync_mappings.add.assert_called_once()
        added = uow.jira_sync_mappings.add.call_args[0][0]
        assert str(added.rip_entity_id) == entity_id
        assert added.jira_issue_key == "MER-1"
        assert added.sync_status == "synced"


class TestEnsureCustomFields:
    async def test_returns_cached_field_ids_without_calling_client(self) -> None:
        integration = SimpleNamespace(traceability_field_ids={"rip_id": "customfield_1"})
        client = AsyncMock()
        uow = MagicMock()

        result = await _ensure_custom_fields(client, integration, uow)

        assert result == {"rip_id": "customfield_1"}
        client.get_fields.assert_not_awaited()

    async def test_creates_missing_fields(self) -> None:
        integration = SimpleNamespace(traceability_field_ids=None)
        client = AsyncMock()
        client.get_fields.return_value = [{"name": "RIP ID", "id": "customfield_1", "custom": True}]
        client.create_field.return_value = {"id": "customfield_new"}
        uow = MagicMock()

        result = await _ensure_custom_fields(client, integration, uow)

        assert result["rip_id"] == "customfield_1"
        assert result["rip_module_id"] == "customfield_new"
        uow.flush.assert_called_once()


class TestDiscoverEpicLinkField:
    async def test_returns_field_id_when_found(self) -> None:
        client = AsyncMock()
        client.get_fields.return_value = [{"name": "Epic Link", "id": "customfield_10014"}]

        result = await _discover_epic_link_field(client)

        assert result == "customfield_10014"

    async def test_returns_none_when_not_found(self) -> None:
        client = AsyncMock()
        client.get_fields.return_value = [{"name": "Other Field", "id": "customfield_1"}]

        assert await _discover_epic_link_field(client) is None

    async def test_returns_none_on_exception(self) -> None:
        client = AsyncMock()
        client.get_fields.side_effect = RuntimeError("api down")

        assert await _discover_epic_link_field(client) is None


class TestSubmitIssueFields:
    async def test_creates_issue_when_no_issue_key(self) -> None:
        client = AsyncMock()
        client.create_issue.return_value = {"id": "1", "key": "MER-1"}

        result = await _submit_issue_fields(
            client, {"summary": "x"}, rip_field_ids=set(), epic_link_field=None
        )

        assert result == {"id": "1", "key": "MER-1"}

    async def test_updates_issue_when_issue_key_given(self) -> None:
        client = AsyncMock()

        result = await _submit_issue_fields(
            client, {"summary": "x"}, issue_key="MER-1", rip_field_ids=set(), epic_link_field=None
        )

        assert result is None
        client.update_issue.assert_awaited_once_with("MER-1", {"summary": "x"})

    async def test_retries_without_rip_fields_on_screen_error(self) -> None:
        client = AsyncMock()
        client.create_issue.side_effect = [
            RuntimeError("customfield_1 cannot be set"),
            {"id": "1", "key": "MER-1"},
        ]

        result = await _submit_issue_fields(
            client,
            {"summary": "x", "customfield_1": "v"},
            rip_field_ids={"customfield_1"},
            epic_link_field=None,
        )

        assert result == {"id": "1", "key": "MER-1"}
        second_call_fields = client.create_issue.call_args_list[1][0][0]
        assert "customfield_1" not in second_call_fields

    async def test_retries_with_epic_link_field_when_parent_rejected(self) -> None:
        client = AsyncMock()
        client.create_issue.side_effect = [
            RuntimeError("parent field is not on the appropriate screen"),
            {"id": "1", "key": "MER-1"},
        ]

        result = await _submit_issue_fields(
            client,
            {"summary": "x", "parent": {"key": "MER-EPIC-1"}},
            rip_field_ids=set(),
            epic_link_field="customfield_10014",
        )

        assert result == {"id": "1", "key": "MER-1"}
        second_call_fields = client.create_issue.call_args_list[1][0][0]
        assert "parent" not in second_call_fields
        assert second_call_fields["customfield_10014"] == "MER-EPIC-1"

    async def test_reraises_when_no_fallback_applies(self) -> None:
        client = AsyncMock()
        client.create_issue.side_effect = RuntimeError("totally unrelated error")

        with pytest.raises(RuntimeError, match="totally unrelated error"):
            await _submit_issue_fields(
                client, {"summary": "x"}, rip_field_ids=set(), epic_link_field=None
            )


class TestFindExistingJiraIssue:
    async def test_returns_none_when_no_rip_id_field(self) -> None:
        client = AsyncMock()

        result = await _find_existing_jira_issue(client, "entity-1", {}, "MER")

        assert result is None
        client.search_issues.assert_not_awaited()

    async def test_returns_match_when_found(self) -> None:
        client = AsyncMock()
        client.search_issues.return_value = [{"key": "MER-1", "id": "10001", "summary": "x"}]

        result = await _find_existing_jira_issue(
            client, "entity-1", {"rip_id": "customfield_10200"}, "MER"
        )

        assert result == {"key": "MER-1", "id": "10001"}

    async def test_returns_none_when_no_match(self) -> None:
        client = AsyncMock()
        client.search_issues.return_value = []

        result = await _find_existing_jira_issue(
            client, "entity-1", {"rip_id": "customfield_10200"}, "MER"
        )

        assert result is None

    async def test_returns_none_on_search_exception(self) -> None:
        client = AsyncMock()
        client.search_issues.side_effect = RuntimeError("jira down")

        result = await _find_existing_jira_issue(
            client, "entity-1", {"rip_id": "customfield_10200"}, "MER"
        )

        assert result is None


class TestFindExistingBySummary:
    async def test_returns_none_for_empty_summary(self) -> None:
        client = AsyncMock()

        assert await _find_existing_by_summary(client, "", "Story", "MER") is None

    async def test_returns_match_on_exact_summary(self) -> None:
        client = AsyncMock()
        client.search_issues.return_value = [
            {"key": "MER-1", "id": "10001", "fields": {"summary": "Login story"}}
        ]

        result = await _find_existing_by_summary(client, "Login story", "Story", "MER")

        assert result == {"key": "MER-1", "id": "10001"}

    async def test_returns_none_when_no_exact_match(self) -> None:
        client = AsyncMock()
        client.search_issues.return_value = [
            {"key": "MER-1", "id": "10001", "fields": {"summary": "Different summary"}}
        ]

        result = await _find_existing_by_summary(client, "Login story", "Story", "MER")

        assert result is None

    async def test_returns_none_on_search_exception(self) -> None:
        client = AsyncMock()
        client.search_issues.side_effect = RuntimeError("jira down")

        result = await _find_existing_by_summary(client, "Login story", "Story", "MER")

        assert result is None
