"""Unit tests for IncrementalUpdatesService."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from app.core.exceptions import ConflictError, NotFoundError
from app.schemas.incremental_updates_schema import (
    ChangedAction,
    UpdateAcceptRequest,
    UpdateChangeType,
    UpdateEntityType,
    UpdateRejectRequest,
)
from app.services.incremental_updates_service import IncrementalUpdatesService, _numeric_code_key


def _make_service(
    us_repo: MagicMock | None = None, mf_repo: MagicMock | None = None
) -> IncrementalUpdatesService:
    """Build the service, defaulting ``get_source_ingestion_id`` to ``None``.

    ``accept_update``/``reject_update`` always resolve the entity's
    ``source_ingestion_id`` before mutating it; tests that don't care about
    the resulting review-count bump don't need to configure this themselves.
    """
    us_repo = us_repo or MagicMock()
    mf_repo = mf_repo or MagicMock()
    if not isinstance(us_repo.get_source_ingestion_id, AsyncMock):
        us_repo.get_source_ingestion_id = AsyncMock(return_value=None)
    if not isinstance(mf_repo.get_source_ingestion_id, AsyncMock):
        mf_repo.get_source_ingestion_id = AsyncMock(return_value=None)
    return IncrementalUpdatesService(
        user_story_repo=us_repo,
        module_feature_repo=mf_repo,
    )


def _set_history_rows(uow: MagicMock, rows: list) -> None:
    """Override the `uow` fixture's default empty `list_by_project` result for a test."""
    uow.incremental_histories.list_by_project.return_value = rows


def _make_history(
    *,
    updates_json: list | None = None,
    adds_json: list | None = None,
    delete_json: list | None = None,
) -> MagicMock:
    history = MagicMock()
    history.id = uuid.uuid4()
    history.created_at = datetime(2026, 8, 1, tzinfo=UTC)
    history.updates_json = updates_json or []
    history.adds_json = adds_json or []
    history.delete_json = delete_json or []
    return history


def _story(story_id: str, code: str | None, **overrides) -> dict:
    base = {
        "id": story_id,
        "user_story_code": code,
        "title": f"Title {story_id}",
        "status": "ready",
        "as_a": "user",
        "i_want_to": "do something",
        "so_that": "get value",
        "acceptance_criteria": [],
        "nfrs": [],
        "story_points": 1,
        "technical_notes": None,
        "sources": [],
    }
    base.update(overrides)
    return base


def _feature(feature_id: str, code: str | None, children: list | None = None, **overrides) -> dict:
    base = {
        "id": feature_id,
        "fea_code": code,
        "name": f"Feature {feature_id}",
        "description": "fd",
        "functions": [],
        "sources": [],
        "children": children or [],
    }
    base.update(overrides)
    return base


def _module(module_id: str, code: str | None, children: list | None = None, **overrides) -> dict:
    base = {
        "id": module_id,
        "mod_code": code,
        "name": f"Module {module_id}",
        "description": "md",
        "children": children or [],
    }
    base.update(overrides)
    return base


class TestNumericCodeKey:
    def test_none_code_sorts_last(self):
        assert _numeric_code_key(None) == (2**31, "")

    def test_empty_string_sorts_last(self):
        assert _numeric_code_key("") == (2**31, "")

    def test_extracts_trailing_integer(self):
        assert _numeric_code_key("MOD-001") == (1, "MOD-001")

    def test_uses_last_number_group(self):
        assert _numeric_code_key("ABC-007-2") == (2, "ABC-007-2")

    def test_codeless_but_non_numeric_string_sorts_last(self):
        assert _numeric_code_key("no-digits-here") == (2**31, "no-digits-here")


class TestGetLatestIncrementalTree:
    @pytest.mark.asyncio
    async def test_project_not_found_raises(self, uow):
        uow.projects.get_by_uuid.return_value = None
        service = _make_service()

        with pytest.raises(NotFoundError):
            await service.get_latest_incremental_tree(project_id=uuid.uuid4(), uow=uow)

    @pytest.mark.asyncio
    async def test_no_pending_history_returns_empty_pruned_tree(self, uow):
        project_id = uuid.uuid4()
        base_tree = [
            _module(
                "mod-1",
                "MOD-001",
                children=[_feature("fea-1", "FEA-001", children=[_story("us-1", "US-001")])],
            )
        ]
        us_repo = MagicMock()
        us_repo.list_full_backlog_tree_for_project = AsyncMock(return_value=base_tree)
        _set_history_rows(uow, [])

        service = _make_service(us_repo=us_repo)
        result = await service.get_latest_incremental_tree(project_id=project_id, uow=uow)

        assert result.history_id is None
        assert result.generated_at is None
        assert result.items == []
        us_repo.list_full_backlog_tree_for_project.assert_awaited_once_with(project_id=project_id)

    @pytest.mark.asyncio
    async def test_updates_overlay_marks_changed_and_prunes_unchanged_sibling(self, uow):
        project_id = uuid.uuid4()
        base_tree = [
            _module(
                "mod-1",
                "MOD-001",
                children=[
                    _feature(
                        "fea-1",
                        "FEA-001",
                        children=[
                            _story("us-1", "US-001"),
                            _story("us-2", "US-002"),
                        ],
                    )
                ],
            )
        ]
        us_repo = MagicMock()
        us_repo.list_full_backlog_tree_for_project = AsyncMock(return_value=base_tree)
        history = _make_history(
            updates_json=[
                {
                    "module_id": "mod-1",
                    "changed": False,
                    "features": [
                        {
                            "feature_id": "fea-1",
                            "changed": False,
                            "user_stories": [
                                {
                                    "user_story_id": "us-1",
                                    "justification": "clarify wording",
                                    "user_story_code": "US-001",
                                    "title": "Story1 updated",
                                    "as_a": "user",
                                    "i_want_to": "log in securely",
                                    "so_that": "access safely",
                                    "acceptance_criteria": [],
                                    "nfrs": [],
                                    "story_points": 5,
                                    "technical_notes": "use MFA",
                                    "sources": [],
                                }
                            ],
                        }
                    ],
                }
            ]
        )
        _set_history_rows(uow, [history])

        service = _make_service(us_repo=us_repo)
        result = await service.get_latest_incremental_tree(project_id=project_id, uow=uow)

        assert result.history_id == history.id
        assert result.generated_at == history.created_at
        assert len(result.items) == 1
        module = result.items[0]
        assert module.changed is False
        assert len(module.children) == 1
        feature = module.children[0]
        assert feature.changed is False
        assert len(feature.children) == 1  # us-2 pruned as unchanged
        story = feature.children[0]
        assert story.id == "us-1"
        assert story.changed is True
        assert story.changed_action == ChangedAction.UPDATE
        assert story.justification == "clarify wording"
        assert story.proposed_items.title == "Story1 updated"
        assert story.proposed_items.story_points == 5
        uow.incremental_histories.list_by_project.assert_called_once_with(project_id, limit=1)

    @pytest.mark.asyncio
    async def test_adds_overlay_creates_new_nodes_at_every_level(self, uow):
        project_id = uuid.uuid4()
        base_tree = [
            _module("mod-1", "MOD-001", children=[_feature("fea-1", "FEA-001", children=[])])
        ]
        us_repo = MagicMock()
        us_repo.list_full_backlog_tree_for_project = AsyncMock(return_value=base_tree)
        history = _make_history(
            adds_json=[
                # New story appended into an existing feature (wrappers unchanged).
                {
                    "module_id": "mod-1",
                    "changed": False,
                    "features": [
                        {
                            "feature_id": "fea-1",
                            "changed": False,
                            "user_stories": [
                                {
                                    "user_story_id": "us-new",
                                    "changed": True,
                                    "user_story_code": None,
                                    "title": "New Story",
                                    "as_a": "user",
                                    "i_want_to": "reset password",
                                    "so_that": "regain access",
                                    "acceptance_criteria": [],
                                    "nfrs": [],
                                    "story_points": 2,
                                    "technical_notes": None,
                                    "sources": [],
                                    "justification": "missing flow",
                                }
                            ],
                        }
                    ],
                },
                # New feature appended into the existing module.
                {
                    "module_id": "mod-1",
                    "changed": False,
                    "features": [
                        {
                            "feature_id": "fea-new",
                            "changed": True,
                            "feature_code": "FEA-002",
                            "feature_name": "Password Reset",
                            "feature_description": "desc",
                            "functions": [],
                            "sources": [],
                            "justification": "new capability",
                            "user_stories": [],
                        }
                    ],
                },
                # Entirely new module.
                {
                    "changed": True,
                    "module_id": "mod-2",
                    "module_code": "MOD-002",
                    "module_name": "Reporting",
                    "module_description": "desc",
                    "justification": "new module needed",
                    "features": [
                        {
                            "feature_id": "fea-3",
                            "feature_code": "FEA-003",
                            "feature_name": "Export",
                            "feature_description": "desc",
                            "functions": [],
                            "sources": [],
                            "justification": "j2",
                            "user_stories": [
                                {
                                    "user_story_id": "us-3",
                                    "user_story_code": "US-003",
                                    "title": "Export CSV",
                                    "as_a": "admin",
                                    "i_want_to": "export data",
                                    "so_that": "analyze offline",
                                    "acceptance_criteria": [],
                                    "nfrs": [],
                                    "story_points": 3,
                                    "technical_notes": None,
                                    "sources": [],
                                    "justification": "j3",
                                }
                            ],
                        }
                    ],
                },
            ]
        )
        _set_history_rows(uow, [history])

        service = _make_service(us_repo=us_repo)
        result = await service.get_latest_incremental_tree(project_id=project_id, uow=uow)

        assert len(result.items) == 2
        mod1, mod2 = result.items
        assert mod1.id == "mod-1"
        assert len(mod1.children) == 2  # fea-1 (existing) + fea-new (added)
        fea1, fea_new = mod1.children
        assert fea1.id == "fea-1"
        assert len(fea1.children) == 1
        assert fea1.children[0].id == "us-new"
        assert fea1.children[0].changed is True
        assert fea1.children[0].changed_action == ChangedAction.CREATE
        assert fea1.children[0].justification == "missing flow"
        assert fea_new.id == "fea-new"
        assert fea_new.changed is True
        assert fea_new.changed_action == ChangedAction.CREATE
        assert fea_new.name == "Password Reset"
        assert fea_new.children == []

        assert mod2.id == "mod-2"
        assert mod2.changed is True
        assert mod2.changed_action == ChangedAction.CREATE
        assert mod2.name == "Reporting"
        assert len(mod2.children) == 1
        assert mod2.children[0].id == "fea-3"
        assert len(mod2.children[0].children) == 1
        assert mod2.children[0].children[0].id == "us-3"

    @pytest.mark.asyncio
    async def test_deletes_overlay_marks_target_node_and_ignores_unknown_target(self, uow):
        project_id = uuid.uuid4()
        base_tree = [
            _module(
                "mod-1",
                "MOD-001",
                children=[_feature("fea-1", "FEA-001", children=[_story("us-1", "US-001")])],
            ),
            _module("mod-2", "MOD-002", children=[_feature("fea-2", "FEA-002", children=[])]),
            _module("mod-3", "MOD-003", children=[]),
        ]
        us_repo = MagicMock()
        us_repo.list_full_backlog_tree_for_project = AsyncMock(return_value=base_tree)
        history = _make_history(
            delete_json=[
                {"uuid": "us-1", "type": "user_story", "justification": "duplicate story"},
                {"uuid": "fea-2", "type": "feature", "justification": "feature obsolete"},
                {"uuid": "mod-3", "type": "module", "justification": "module obsolete"},
                {"uuid": "does-not-exist", "type": "user_story", "justification": "ignored"},
                {"uuid": None, "type": "user_story", "justification": "ignored, falsy id"},
                {"uuid": "us-1", "type": "unknown_type", "justification": "ignored, unknown type"},
            ]
        )
        _set_history_rows(uow, [history])

        service = _make_service(us_repo=us_repo)
        result = await service.get_latest_incremental_tree(project_id=project_id, uow=uow)

        assert len(result.items) == 3
        mod1, mod2, mod3 = result.items
        story = mod1.children[0].children[0]
        assert story.id == "us-1"
        assert story.changed_action == ChangedAction.DELETE
        assert story.justification == "duplicate story"
        assert story.proposed_items is None

        feature = mod2.children[0]
        assert feature.id == "fea-2"
        assert feature.changed_action == ChangedAction.DELETE
        assert feature.justification == "feature obsolete"

        assert mod3.id == "mod-3"
        assert mod3.changed_action == ChangedAction.DELETE
        assert mod3.justification == "module obsolete"
        assert mod3.children == []

    @pytest.mark.asyncio
    async def test_updates_and_adds_overlays_ignore_references_to_unknown_wrapper_nodes(self, uow):
        """A wrapper module/feature/user_story id that doesn't match anything in the
        live tree is logged and skipped rather than raising — proposals can reference
        stale ids if the live tree changed since the proposal was generated."""
        project_id = uuid.uuid4()
        base_tree = [
            _module(
                "mod-1",
                "MOD-001",
                children=[_feature("fea-1", "FEA-001", children=[_story("us-1", "US-001")])],
            )
        ]
        us_repo = MagicMock()
        us_repo.list_full_backlog_tree_for_project = AsyncMock(return_value=base_tree)
        history = _make_history(
            updates_json=[
                {"module_id": "missing-mod", "changed": False, "features": []},
                {
                    "module_id": "mod-1",
                    "changed": True,
                    "justification": "module rename",
                    "module_code": "MOD-001",
                    "module_name": "M1 updated",
                    "module_description": "d",
                    "features": [
                        {"feature_id": "missing-fea", "changed": False, "user_stories": []},
                        {
                            "feature_id": "fea-1",
                            "changed": False,
                            "user_stories": [
                                {
                                    "user_story_id": "missing-us",
                                    "justification": "j",
                                    "user_story_code": None,
                                    "title": "t",
                                    "as_a": "a",
                                    "i_want_to": "i",
                                    "so_that": "s",
                                    "acceptance_criteria": [],
                                    "nfrs": [],
                                    "story_points": 1,
                                    "technical_notes": None,
                                    "sources": [],
                                }
                            ],
                        },
                    ],
                },
            ],
            adds_json=[
                {"module_id": "missing-mod-2", "changed": False, "features": []},
                {
                    "module_id": "mod-1",
                    "changed": False,
                    "features": [
                        {"feature_id": "missing-fea-2", "changed": False, "user_stories": []}
                    ],
                },
            ],
        )
        _set_history_rows(uow, [history])

        service = _make_service(us_repo=us_repo)
        result = await service.get_latest_incremental_tree(project_id=project_id, uow=uow)

        assert len(result.items) == 1
        module = result.items[0]
        assert module.id == "mod-1"
        assert module.changed is True
        assert module.changed_action == ChangedAction.UPDATE
        assert module.proposed_items.name == "M1 updated"
        # fea-1/us-1 were never actually touched by a resolvable wrapper, so they
        # were pruned as unchanged despite the parent module surviving.
        assert module.children == []

    @pytest.mark.asyncio
    async def test_sorts_modules_features_and_stories_by_numeric_code(self, uow):
        project_id = uuid.uuid4()
        base_tree = [
            _module(
                "mod-2",
                "MOD-002",
                children=[_feature("fea-2b", "FEA-002", children=[_story("us-2b", "US-002")])],
            ),
            _module(
                "mod-1",
                "MOD-001",
                children=[
                    _feature(
                        "fea-1b",
                        "FEA-002",
                        children=[_story("us-1b", "US-002"), _story("us-1a", "US-001")],
                    ),
                    _feature("fea-1a", "FEA-001", children=[]),
                ],
            ),
        ]

        def _story_update(story_id: str, code: str) -> dict:
            return {
                "user_story_id": story_id,
                "user_story_code": code,
                "justification": "j",
                "title": "t",
                "as_a": "a",
                "i_want_to": "i",
                "so_that": "s",
                "acceptance_criteria": [],
                "nfrs": [],
                "story_points": 1,
                "technical_notes": None,
                "sources": [],
            }

        us_repo = MagicMock()
        us_repo.list_full_backlog_tree_for_project = AsyncMock(return_value=base_tree)
        # Mark the leaf stories (and the childless feature) as changed so nothing
        # gets pruned before sorting runs — sorting itself is what's under test here.
        history = _make_history(
            updates_json=[
                {
                    "module_id": "mod-1",
                    "changed": False,
                    "features": [
                        {
                            "feature_id": "fea-1b",
                            "changed": False,
                            "user_stories": [
                                _story_update("us-1b", "US-002"),
                                _story_update("us-1a", "US-001"),
                            ],
                        },
                        {
                            "feature_id": "fea-1a",
                            "changed": True,
                            "justification": "j",
                            "feature_code": "FEA-001",
                            "feature_name": "F1a",
                            "feature_description": "d",
                            "functions": [],
                            "sources": [],
                            "user_stories": [],
                        },
                    ],
                },
                {
                    "module_id": "mod-2",
                    "changed": False,
                    "features": [
                        {
                            "feature_id": "fea-2b",
                            "changed": False,
                            "user_stories": [_story_update("us-2b", "US-002")],
                        }
                    ],
                },
            ]
        )
        _set_history_rows(uow, [history])

        service = _make_service(us_repo=us_repo)
        result = await service.get_latest_incremental_tree(project_id=project_id, uow=uow)

        assert [m.id for m in result.items] == ["mod-1", "mod-2"]
        mod1 = result.items[0]
        assert [f.id for f in mod1.children] == ["fea-1a", "fea-1b"]
        fea_1b = mod1.children[1]
        assert [s.id for s in fea_1b.children] == ["us-1a", "us-1b"]


class TestGetIncrementalUpdatesList:
    @pytest.mark.asyncio
    async def test_project_not_found_raises(self, uow):
        uow.projects.get_by_uuid.return_value = None
        service = _make_service()

        with pytest.raises(NotFoundError):
            await service.get_incremental_updates_list(project_id=uuid.uuid4(), uow=uow)

    @pytest.mark.asyncio
    async def test_empty_tree_returns_zero_counts(self, uow):
        project_id = uuid.uuid4()
        us_repo = MagicMock()
        us_repo.list_user_stories_tree_for_project = AsyncMock(return_value=[])
        service = _make_service(us_repo=us_repo)

        result = await service.get_incremental_updates_list(project_id=project_id, uow=uow)

        assert result.items == []
        assert result.module_added == 0
        assert result.user_story_deleted_suggested == 0
        us_repo.list_user_stories_tree_for_project.assert_awaited_once_with(project_id=project_id)

    @pytest.mark.asyncio
    async def test_filters_pruned_tree_and_tallies_counts_by_own_change_type(self, uow):
        project_id = uuid.uuid4()
        tree = [
            {
                "id": "mod-a",
                "mod_code": "MOD-A",
                "name": "Module A",
                "incremental_change_type": None,
                "children": [
                    {
                        "id": "fea-a1",
                        "name": "Feature A1",
                        "incremental_change_type": "ADDED",
                        "children": [
                            {
                                "id": "us-a1s1",
                                "user_story_code": "US-1",
                                "name": "Story 1",
                                "status": "ready",
                                "incremental_change_type": None,
                            },
                            {
                                "id": "us-a1s2",
                                "user_story_code": "US-2",
                                "name": "Story 2",
                                "status": "ready",
                                "incremental_change_type": "UPDATED",
                            },
                        ],
                    },
                    {
                        "id": "fea-a2",
                        "name": "Feature A2",
                        "incremental_change_type": None,
                        "children": [
                            {
                                "id": "us-a2s1",
                                "user_story_code": "US-3",
                                "name": "Story 3",
                                "status": "ready",
                                "incremental_change_type": None,
                            }
                        ],
                    },
                ],
            },
            {
                "id": "mod-b",
                "mod_code": "MOD-B",
                "name": "Module B",
                "incremental_change_type": "DELETE_SUGGESTED",
                "children": [],
            },
            {
                "id": "mod-c",
                "mod_code": "MOD-C",
                "name": "Module C",
                "incremental_change_type": None,
                "children": [],
            },
        ]
        us_repo = MagicMock()
        us_repo.list_user_stories_tree_for_project = AsyncMock(return_value=tree)
        service = _make_service(us_repo=us_repo)

        result = await service.get_incremental_updates_list(project_id=project_id, uow=uow)

        assert result.feature_added == 1
        assert result.user_story_updated == 1
        assert result.module_deleted_suggested == 1
        assert result.module_added == 0
        assert result.module_updated == 0
        assert result.feature_updated == 0
        assert result.feature_deleted_suggested == 0
        assert result.user_story_added == 0
        assert result.user_story_deleted_suggested == 0

        # mod-c is entirely unchanged with no surviving children, so it's dropped.
        assert len(result.items) == 2
        mod_a, mod_b = result.items
        assert mod_a.id == "mod-a"
        assert len(mod_a.children) == 1  # fea-a2 pruned: unchanged, no surviving story
        assert mod_a.children[0].id == "fea-a1"
        assert len(mod_a.children[0].children) == 1
        assert mod_a.children[0].children[0].id == "us-a1s2"
        assert mod_b.id == "mod-b"
        assert mod_b.children == []


def _pipeline_not_running(uow) -> None:
    uow.source_ingestions.list_running_by_project.return_value = []


class TestAcceptUpdate:
    @pytest.mark.asyncio
    async def test_project_not_found_raises(self, uow):
        uow.projects.get_by_uuid.return_value = None
        service = _make_service()
        payload = UpdateAcceptRequest(
            entity_type=UpdateEntityType.MODULE,
            entity_id="mod-1",
            change_type=UpdateChangeType.UPDATED,
        )

        with pytest.raises(NotFoundError):
            await service.accept_update(
                project_id=uuid.uuid4(), payload=payload, actor_user_id=None, uow=uow
            )
        uow.source_ingestions.increment_review_count.assert_not_called()

    @pytest.mark.asyncio
    async def test_pipeline_running_raises_conflict(self, uow):
        from app.core.enums.source_type import SourceType

        uow.source_ingestions.list_running_by_project.return_value = [
            MagicMock(source_type=SourceType.SOURCE_CODE.value, stages=[], id=uuid.uuid4())
        ]
        service = _make_service()
        payload = UpdateAcceptRequest(
            entity_type=UpdateEntityType.MODULE,
            entity_id="mod-1",
            change_type=UpdateChangeType.UPDATED,
        )

        with pytest.raises(ConflictError):
            await service.accept_update(
                project_id=uuid.uuid4(), payload=payload, actor_user_id=None, uow=uow
            )

    @pytest.mark.asyncio
    async def test_accept_updated_module_sets_approved(self, uow):
        _pipeline_not_running(uow)
        project_id = uuid.uuid4()
        mf_repo = MagicMock()
        mf_repo.accept_module = AsyncMock(return_value=True)
        service = _make_service(mf_repo=mf_repo)
        payload = UpdateAcceptRequest(
            entity_type=UpdateEntityType.MODULE,
            entity_id="mod-1",
            change_type=UpdateChangeType.UPDATED,
            comment="looks good",
        )

        result = await service.accept_update(
            project_id=project_id, payload=payload, actor_user_id=None, uow=uow
        )

        assert result.action == "accepted"
        assert result.deleted is False
        assert result.comment == "looks good"
        mf_repo.accept_module.assert_awaited_once_with(project_id, "mod-1")

    @pytest.mark.asyncio
    async def test_accept_bumps_review_count_on_tagging_ingestion(self, uow):
        _pipeline_not_running(uow)
        project_id = uuid.uuid4()
        ingestion_id = uuid.uuid4()
        mf_repo = MagicMock()
        mf_repo.accept_module = AsyncMock(return_value=True)
        mf_repo.get_source_ingestion_id = AsyncMock(return_value=str(ingestion_id))
        service = _make_service(mf_repo=mf_repo)
        payload = UpdateAcceptRequest(
            entity_type=UpdateEntityType.MODULE,
            entity_id="mod-1",
            change_type=UpdateChangeType.UPDATED,
            comment="looks good",
        )

        await service.accept_update(
            project_id=project_id, payload=payload, actor_user_id=uuid.uuid4(), uow=uow
        )

        mf_repo.get_source_ingestion_id.assert_awaited_once_with(project_id, "module", "mod-1")
        uow.source_ingestions.increment_review_count.assert_called_once_with(
            ingestion_id, entity_type="module", accepted=True
        )

    @pytest.mark.asyncio
    async def test_accept_skips_review_count_when_ingestion_id_unresolved(self, uow):
        _pipeline_not_running(uow)
        mf_repo = MagicMock()
        mf_repo.accept_module = AsyncMock(return_value=True)
        mf_repo.get_source_ingestion_id = AsyncMock(return_value=None)
        service = _make_service(mf_repo=mf_repo)
        payload = UpdateAcceptRequest(
            entity_type=UpdateEntityType.MODULE,
            entity_id="mod-1",
            change_type=UpdateChangeType.UPDATED,
        )

        await service.accept_update(
            project_id=uuid.uuid4(), payload=payload, actor_user_id=None, uow=uow
        )

        uow.source_ingestions.increment_review_count.assert_not_called()

    @pytest.mark.asyncio
    async def test_accept_added_feature_sets_approved(self, uow):
        _pipeline_not_running(uow)
        project_id = uuid.uuid4()
        mf_repo = MagicMock()
        mf_repo.accept_feature = AsyncMock(return_value=True)
        service = _make_service(mf_repo=mf_repo)
        payload = UpdateAcceptRequest(
            entity_type=UpdateEntityType.FEATURE,
            entity_id="fea-1",
            change_type=UpdateChangeType.ADDED,
        )

        result = await service.accept_update(
            project_id=project_id, payload=payload, actor_user_id=None, uow=uow
        )

        assert result.action == "accepted"
        assert result.deleted is False
        mf_repo.accept_feature.assert_awaited_once_with(project_id, "fea-1")

    @pytest.mark.asyncio
    async def test_accept_delete_suggested_feature_hard_deletes(self, uow):
        _pipeline_not_running(uow)
        project_id = uuid.uuid4()
        mf_repo = MagicMock()
        mf_repo.delete_feature_by_id = AsyncMock(return_value=True)
        service = _make_service(mf_repo=mf_repo)
        payload = UpdateAcceptRequest(
            entity_type=UpdateEntityType.FEATURE,
            entity_id="fea-1",
            change_type=UpdateChangeType.DELETE_SUGGESTED,
        )

        result = await service.accept_update(
            project_id=project_id, payload=payload, actor_user_id=None, uow=uow
        )

        assert result.action == "accepted"
        assert result.deleted is True
        mf_repo.delete_feature_by_id.assert_awaited_once_with(project_id, "fea-1")

    @pytest.mark.asyncio
    async def test_accept_delete_suggested_user_story_soft_deletes(self, uow):
        _pipeline_not_running(uow)
        project_id = uuid.uuid4()
        us_repo = MagicMock()
        us_repo.soft_delete_user_story_by_id = AsyncMock(return_value=True)
        service = _make_service(us_repo=us_repo)
        payload = UpdateAcceptRequest(
            entity_type=UpdateEntityType.USER_STORY,
            entity_id="us-1",
            change_type=UpdateChangeType.DELETE_SUGGESTED,
        )

        result = await service.accept_update(
            project_id=project_id, payload=payload, actor_user_id=None, uow=uow
        )

        assert result.action == "accepted"
        assert result.deleted is True
        us_repo.soft_delete_user_story_by_id.assert_awaited_once_with(
            project_id=project_id, user_story_id="us-1", del_reason=""
        )

    @pytest.mark.asyncio
    async def test_accept_updated_user_story_sets_approved(self, uow):
        _pipeline_not_running(uow)
        project_id = uuid.uuid4()
        us_repo = MagicMock()
        us_repo.accept_user_story = AsyncMock(return_value=True)
        service = _make_service(us_repo=us_repo)
        payload = UpdateAcceptRequest(
            entity_type=UpdateEntityType.USER_STORY,
            entity_id="us-1",
            change_type=UpdateChangeType.UPDATED,
        )

        result = await service.accept_update(
            project_id=project_id, payload=payload, actor_user_id=None, uow=uow
        )

        assert result.action == "accepted"
        assert result.deleted is False
        us_repo.accept_user_story.assert_awaited_once_with("us-1", "approved")

    @pytest.mark.asyncio
    async def test_accept_entity_not_found_raises(self, uow):
        _pipeline_not_running(uow)
        mf_repo = MagicMock()
        mf_repo.accept_module = AsyncMock(return_value=False)
        service = _make_service(mf_repo=mf_repo)
        payload = UpdateAcceptRequest(
            entity_type=UpdateEntityType.MODULE,
            entity_id="missing-mod",
            change_type=UpdateChangeType.UPDATED,
        )

        with pytest.raises(NotFoundError):
            await service.accept_update(
                project_id=uuid.uuid4(), payload=payload, actor_user_id=None, uow=uow
            )
        uow.source_ingestions.increment_review_count.assert_not_called()


class TestRejectUpdate:
    @pytest.mark.asyncio
    async def test_project_not_found_raises(self, uow):
        uow.projects.get_by_uuid.return_value = None
        service = _make_service()
        payload = UpdateRejectRequest(
            entity_type=UpdateEntityType.MODULE,
            entity_id="mod-1",
            change_type=UpdateChangeType.UPDATED,
        )

        with pytest.raises(NotFoundError):
            await service.reject_update(
                project_id=uuid.uuid4(), payload=payload, actor_user_id=None, uow=uow
            )
        uow.source_ingestions.increment_review_count.assert_not_called()

    @pytest.mark.asyncio
    async def test_pipeline_running_raises_conflict(self, uow):
        from app.core.enums.source_ingestion_stage import SourceIngestionStage
        from app.core.enums.source_type import SourceType

        uow.source_ingestions.list_running_by_project.return_value = [
            MagicMock(
                source_type=SourceType.RFP.value,
                stages=[SourceIngestionStage.GENERATING_MODULE_FEATURE.value],
                id=uuid.uuid4(),
            )
        ]
        service = _make_service()
        payload = UpdateRejectRequest(
            entity_type=UpdateEntityType.MODULE,
            entity_id="mod-1",
            change_type=UpdateChangeType.UPDATED,
        )

        with pytest.raises(ConflictError):
            await service.reject_update(
                project_id=uuid.uuid4(), payload=payload, actor_user_id=None, uow=uow
            )

    @pytest.mark.asyncio
    async def test_reject_added_module_hard_deletes(self, uow):
        _pipeline_not_running(uow)
        project_id = uuid.uuid4()
        mf_repo = MagicMock()
        mf_repo.delete_module_by_id = AsyncMock(return_value=True)
        service = _make_service(mf_repo=mf_repo)
        payload = UpdateRejectRequest(
            entity_type=UpdateEntityType.MODULE,
            entity_id="mod-1",
            change_type=UpdateChangeType.ADDED,
            reason="not needed",
        )

        result = await service.reject_update(
            project_id=project_id, payload=payload, actor_user_id=None, uow=uow
        )

        assert result.action == "rejected"
        assert result.deleted is True
        assert result.reason == "not needed"
        mf_repo.delete_module_by_id.assert_awaited_once_with(project_id, "mod-1")

    @pytest.mark.asyncio
    async def test_reject_bumps_review_count_on_tagging_ingestion(self, uow):
        _pipeline_not_running(uow)
        project_id = uuid.uuid4()
        ingestion_id = uuid.uuid4()
        mf_repo = MagicMock()
        mf_repo.delete_module_by_id = AsyncMock(return_value=True)
        mf_repo.get_source_ingestion_id = AsyncMock(return_value=str(ingestion_id))
        service = _make_service(mf_repo=mf_repo)
        payload = UpdateRejectRequest(
            entity_type=UpdateEntityType.MODULE,
            entity_id="mod-1",
            change_type=UpdateChangeType.ADDED,
            reason="not needed",
        )

        await service.reject_update(
            project_id=project_id, payload=payload, actor_user_id=uuid.uuid4(), uow=uow
        )

        mf_repo.get_source_ingestion_id.assert_awaited_once_with(project_id, "module", "mod-1")
        uow.source_ingestions.increment_review_count.assert_called_once_with(
            ingestion_id, entity_type="module", accepted=False
        )

    @pytest.mark.asyncio
    async def test_reject_skips_review_count_when_ingestion_id_unresolved(self, uow):
        _pipeline_not_running(uow)
        mf_repo = MagicMock()
        mf_repo.delete_module_by_id = AsyncMock(return_value=True)
        mf_repo.get_source_ingestion_id = AsyncMock(return_value=None)
        service = _make_service(mf_repo=mf_repo)
        payload = UpdateRejectRequest(
            entity_type=UpdateEntityType.MODULE,
            entity_id="mod-1",
            change_type=UpdateChangeType.ADDED,
            reason="not needed",
        )

        await service.reject_update(
            project_id=uuid.uuid4(), payload=payload, actor_user_id=None, uow=uow
        )

        uow.source_ingestions.increment_review_count.assert_not_called()

    @pytest.mark.asyncio
    async def test_reject_delete_suggested_feature_restores_from_snapshot(self, uow):
        _pipeline_not_running(uow)
        project_id = uuid.uuid4()
        mf_repo = MagicMock()
        mf_repo.restore_feature_from_latest_version = AsyncMock(return_value=True)
        service = _make_service(mf_repo=mf_repo)
        payload = UpdateRejectRequest(
            entity_type=UpdateEntityType.FEATURE,
            entity_id="fea-1",
            change_type=UpdateChangeType.DELETE_SUGGESTED,
        )

        result = await service.reject_update(
            project_id=project_id, payload=payload, actor_user_id=None, uow=uow
        )

        assert result.action == "rejected"
        assert result.deleted is False
        mf_repo.restore_feature_from_latest_version.assert_awaited_once_with(project_id, "fea-1")

    @pytest.mark.asyncio
    async def test_reject_updated_module_restores_from_snapshot(self, uow):
        _pipeline_not_running(uow)
        project_id = uuid.uuid4()
        mf_repo = MagicMock()
        mf_repo.restore_module_from_latest_version = AsyncMock(return_value=True)
        service = _make_service(mf_repo=mf_repo)
        payload = UpdateRejectRequest(
            entity_type=UpdateEntityType.MODULE,
            entity_id="mod-1",
            change_type=UpdateChangeType.UPDATED,
        )

        result = await service.reject_update(
            project_id=project_id, payload=payload, actor_user_id=None, uow=uow
        )

        assert result.action == "rejected"
        assert result.deleted is False
        mf_repo.restore_module_from_latest_version.assert_awaited_once_with(project_id, "mod-1")

    @pytest.mark.asyncio
    async def test_reject_updated_feature_restores_from_snapshot(self, uow):
        _pipeline_not_running(uow)
        project_id = uuid.uuid4()
        mf_repo = MagicMock()
        mf_repo.restore_feature_from_latest_version = AsyncMock(return_value=True)
        service = _make_service(mf_repo=mf_repo)
        payload = UpdateRejectRequest(
            entity_type=UpdateEntityType.FEATURE,
            entity_id="fea-1",
            change_type=UpdateChangeType.UPDATED,
        )

        result = await service.reject_update(
            project_id=project_id, payload=payload, actor_user_id=None, uow=uow
        )

        assert result.action == "rejected"
        assert result.deleted is False
        mf_repo.restore_feature_from_latest_version.assert_awaited_once_with(project_id, "fea-1")

    @pytest.mark.asyncio
    async def test_reject_updated_user_story_restores_from_snapshot(self, uow):
        _pipeline_not_running(uow)
        project_id = uuid.uuid4()
        us_repo = MagicMock()
        us_repo.restore_user_story_from_latest_version = AsyncMock(return_value=True)
        service = _make_service(us_repo=us_repo)
        payload = UpdateRejectRequest(
            entity_type=UpdateEntityType.USER_STORY,
            entity_id="us-1",
            change_type=UpdateChangeType.UPDATED,
        )

        result = await service.reject_update(
            project_id=project_id, payload=payload, actor_user_id=None, uow=uow
        )

        assert result.action == "rejected"
        assert result.deleted is False
        us_repo.restore_user_story_from_latest_version.assert_awaited_once_with("us-1")

    @pytest.mark.asyncio
    async def test_reject_delete_suggested_module_restores_from_snapshot(self, uow):
        _pipeline_not_running(uow)
        project_id = uuid.uuid4()
        mf_repo = MagicMock()
        mf_repo.restore_module_from_latest_version = AsyncMock(return_value=True)
        service = _make_service(mf_repo=mf_repo)
        payload = UpdateRejectRequest(
            entity_type=UpdateEntityType.MODULE,
            entity_id="mod-1",
            change_type=UpdateChangeType.DELETE_SUGGESTED,
        )

        result = await service.reject_update(
            project_id=project_id, payload=payload, actor_user_id=None, uow=uow
        )

        assert result.action == "rejected"
        assert result.deleted is False
        mf_repo.restore_module_from_latest_version.assert_awaited_once_with(project_id, "mod-1")

    @pytest.mark.asyncio
    async def test_reject_delete_suggested_user_story_restores_from_snapshot(self, uow):
        _pipeline_not_running(uow)
        project_id = uuid.uuid4()
        us_repo = MagicMock()
        us_repo.restore_user_story_from_latest_version = AsyncMock(return_value=True)
        service = _make_service(us_repo=us_repo)
        payload = UpdateRejectRequest(
            entity_type=UpdateEntityType.USER_STORY,
            entity_id="us-1",
            change_type=UpdateChangeType.DELETE_SUGGESTED,
        )

        result = await service.reject_update(
            project_id=project_id, payload=payload, actor_user_id=None, uow=uow
        )

        assert result.action == "rejected"
        assert result.deleted is False
        us_repo.restore_user_story_from_latest_version.assert_awaited_once_with("us-1")

    @pytest.mark.asyncio
    async def test_reject_entity_not_found_raises(self, uow):
        _pipeline_not_running(uow)
        us_repo = MagicMock()
        us_repo.restore_user_story_from_latest_version = AsyncMock(return_value=False)
        service = _make_service(us_repo=us_repo)
        payload = UpdateRejectRequest(
            entity_type=UpdateEntityType.USER_STORY,
            entity_id="missing-us",
            change_type=UpdateChangeType.UPDATED,
        )

        with pytest.raises(NotFoundError):
            await service.reject_update(
                project_id=uuid.uuid4(), payload=payload, actor_user_id=None, uow=uow
            )
        uow.source_ingestions.increment_review_count.assert_not_called()


class TestIngestionCompletionSideEffects:
    """Accept/reject must trigger SourceIngestionService's completion checks.

    Resolving a pending incremental change is exactly the signal that may
    complete its `requirement_update` ingestion and, in turn, the project's
    RFP/source-code generation ingestion — see source_ingestion_service.py.
    """

    @staticmethod
    def _patch_completion_checks():
        return (
            patch(
                "app.services.source_ingestion_service.SourceIngestionService."
                "try_complete_open_feedback_or_incremental_ingestions",
                new=AsyncMock(),
            ),
            patch(
                "app.services.source_ingestion_service.SourceIngestionService."
                "try_complete_generation_ingestion",
                new=AsyncMock(),
            ),
        )

    @pytest.mark.asyncio
    async def test_accept_update_triggers_completion_checks(self, uow):
        _pipeline_not_running(uow)
        project_id = uuid.uuid4()
        us_repo = MagicMock()
        us_repo.accept_user_story = AsyncMock(return_value=True)
        service = _make_service(us_repo=us_repo)
        payload = UpdateAcceptRequest(
            entity_type=UpdateEntityType.USER_STORY,
            entity_id="us-1",
            change_type=UpdateChangeType.UPDATED,
        )
        open_patch, generation_patch = self._patch_completion_checks()

        with open_patch as mock_open, generation_patch as mock_generation:
            await service.accept_update(
                project_id=project_id, payload=payload, actor_user_id=None, uow=uow
            )

        mock_open.assert_awaited_once_with(
            uow, project_id, service._mf_repo, service._us_repo, actor_user_id=None
        )
        mock_generation.assert_awaited_once_with(
            uow, project_id, service._us_repo, actor_user_id=None
        )

    @pytest.mark.asyncio
    async def test_reject_update_triggers_completion_checks(self, uow):
        _pipeline_not_running(uow)
        project_id = uuid.uuid4()
        mf_repo = MagicMock()
        mf_repo.delete_module_by_id = AsyncMock(return_value=True)
        service = _make_service(mf_repo=mf_repo)
        payload = UpdateRejectRequest(
            entity_type=UpdateEntityType.MODULE,
            entity_id="mod-1",
            change_type=UpdateChangeType.ADDED,
            reason="not needed",
        )
        open_patch, generation_patch = self._patch_completion_checks()

        with open_patch as mock_open, generation_patch as mock_generation:
            await service.reject_update(
                project_id=project_id, payload=payload, actor_user_id=None, uow=uow
            )

        mock_open.assert_awaited_once_with(
            uow, project_id, service._mf_repo, service._us_repo, actor_user_id=None
        )
        mock_generation.assert_awaited_once_with(
            uow, project_id, service._us_repo, actor_user_id=None
        )

    @pytest.mark.asyncio
    async def test_completion_checks_not_run_when_entity_not_found(self, uow):
        _pipeline_not_running(uow)
        mf_repo = MagicMock()
        mf_repo.accept_module = AsyncMock(return_value=False)
        service = _make_service(mf_repo=mf_repo)
        payload = UpdateAcceptRequest(
            entity_type=UpdateEntityType.MODULE,
            entity_id="missing-mod",
            change_type=UpdateChangeType.UPDATED,
        )
        open_patch, generation_patch = self._patch_completion_checks()

        with open_patch as mock_open, generation_patch as mock_generation, pytest.raises(NotFoundError):
            await service.accept_update(
                project_id=uuid.uuid4(), payload=payload, actor_user_id=None, uow=uow
            )

        mock_open.assert_not_called()
        mock_generation.assert_not_called()
