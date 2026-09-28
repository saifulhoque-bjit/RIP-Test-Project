"""Unit tests for UserStoryService."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch
import uuid

import pytest

from app.core.exceptions import NotFoundError, ValidationError
from app.models.neo4j.module_feature_model import ChangeType
from app.models.neo4j.user_story_model import UserStoryModel
from app.models.neo4j.version_model import UserStoryVersionModel
from app.schemas.user_story_schema import (
    BulkStatusChangeRequest,
    ChangeStatusRequest,
    StoryFeedbackInput,
    UserStoryStatus,
    UserStorySyncFlagsUpdateRequest,
)
from app.services.user_story_service import UserStoryService
from tests.conftest import make_project, make_source

_FIXED_UUID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def _make_model(source_id: uuid.UUID | None = None, user_story_id: str = "req_1") -> UserStoryModel:
    return UserStoryModel(
        id=user_story_id,
        user_story_code="ARCH-001",
        title="Core Architecture",
        description="Some description",
        consensus=9.8,
        status="approved",
        version=1,
        feature_id=None,
        source_file_count=1,
    )


class TestUserStoryService:
    def test_generate_user_story_id_is_deterministic_with_project_and_user_story_code(self):
        first = UserStoryService._generate_user_story_id(
            project_id="proj-1",
            user_story_code="REQ-001",
        )
        second = UserStoryService._generate_user_story_id(
            project_id="proj-1",
            user_story_code="REQ-001",
        )
        assert first == second

    @pytest.mark.asyncio
    async def test_change_status_success(self, uow):
        uow.source_ingestions.list_running_by_project.return_value = []
        repository = MagicMock()
        repository.change_user_story_status = AsyncMock(
            return_value=_make_model(uuid.uuid4(), "req_1")
        )

        response = await UserStoryService(repository).change_status(
            user_story_id="req_1",
            request=ChangeStatusRequest(status="approved"),
            project_id=uuid.uuid4(),
            uow=uow,
        )

        assert response.id == "req_1"
        assert response.status == "approved"
        repository.change_user_story_status.assert_awaited_once_with("req_1", "approved")

    @pytest.mark.asyncio
    async def test_change_status_not_found(self, uow):
        uow.source_ingestions.list_running_by_project.return_value = []
        repository = MagicMock()
        repository.change_user_story_status = AsyncMock(return_value=None)

        with pytest.raises(NotFoundError):
            await UserStoryService(repository).change_status(
                user_story_id="missing",
                request=ChangeStatusRequest(status="approved"),
                project_id=uuid.uuid4(),
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_list_user_stories_by_project_success(self, uow):
        project = make_project()
        source = make_source(is_deleted=False)
        uow.projects.get_by_uuid.return_value = project
        uow.sources.get_ids_by_project.return_value = [source.id]
        repository = MagicMock()
        repository.list_user_stories_for_project = AsyncMock(
            return_value=([_make_model(source.id)], 1)
        )
        repository.are_all_user_stories_approved = AsyncMock(return_value=False)

        response = await UserStoryService(repository).list_user_stories_by_project(
            project_id=project.id,
            skip=0,
            limit=20,
            uow=uow,
        )

        assert response.total == 1
        assert response.skip == 0
        assert response.limit == 20
        assert response.items[0].user_story_code == "ARCH-001"

    @pytest.mark.asyncio
    async def test_get_user_stories_tree_forwards_source_ingestion_id_filter(self, uow):
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        module_feature_repository = MagicMock()
        module_feature_repository.are_all_modules_approved = AsyncMock(return_value=False)
        module_feature_repository.are_all_features_approved = AsyncMock(return_value=False)
        repository = MagicMock()
        repository.list_user_stories_tree_for_project = AsyncMock(return_value=[])
        repository.are_all_user_stories_approved = AsyncMock(return_value=False)

        await UserStoryService(repository, module_feature_repository).get_user_stories_tree(
            project_id=project.id,
            uow=uow,
            source_ingestion_id="ingestion-1",
        )

        repository.list_user_stories_tree_for_project.assert_awaited_once_with(
            project_id=project.id, source_ingestion_id="ingestion-1"
        )

    @pytest.mark.asyncio
    async def test_get_sync_candidate_tree_not_found(self, uow):
        uow.projects.get_by_uuid.return_value = None
        repository = MagicMock()

        with pytest.raises(NotFoundError):
            await UserStoryService(repository).get_sync_candidate_tree(
                project_id=uuid.uuid4(),
                sync_target="jira",
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_get_sync_candidate_tree_forwards_target_and_counts_stories(self, uow):
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        repository = MagicMock()
        repository.list_sync_candidate_tree_for_project = AsyncMock(
            return_value=[
                {
                    "id": "module_1",
                    "mod_code": "MOD-001",
                    "name": "Module 1",
                    "children": [
                        {
                            "id": "feature_1",
                            "fea_code": "FEA-001",
                            "name": "Feature 1",
                            "children": [
                                {
                                    "id": "req_1",
                                    "user_story_code": "REQ-001",
                                    "name": "Story 1",
                                    "status": "approved",
                                    "version": 1,
                                    "is_jira_synced": False,
                                    "is_tap_synced": False,
                                },
                                {
                                    "id": "req_2",
                                    "user_story_code": "REQ-002",
                                    "name": "Story 2",
                                    "status": "approved",
                                    "version": 3,
                                    "is_jira_synced": False,
                                    "is_tap_synced": False,
                                },
                            ],
                        }
                    ],
                }
            ]
        )

        response = await UserStoryService(repository).get_sync_candidate_tree(
            project_id=project.id,
            sync_target="jira",
            uow=uow,
        )

        repository.list_sync_candidate_tree_for_project.assert_awaited_once_with(
            project_id=project.id, sync_target="jira"
        )
        assert response.sync_target == "jira"
        assert response.total_count == 2
        assert len(response.items) == 1
        stories = response.items[0].children[0].children
        assert stories[0].version == 1
        assert stories[1].version == 3

    @pytest.mark.asyncio
    async def test_list_user_stories_by_project_includes_nfrs(self, uow):
        project = make_project()
        source = make_source(is_deleted=False)
        uow.projects.get_by_uuid.return_value = project
        uow.sources.get_ids_by_project.return_value = [source.id]
        model = _make_model(source.id)
        model.nfrs = [
            {
                "id": "NFR-SEC-01",
                "category": "Security",
                "description": "Cross-resident apartment access prevention",
                "requirement": "API must return HTTP 403 for any cross-resident apartment access attempt.",
            }
        ]
        repository = MagicMock()
        repository.list_user_stories_for_project = AsyncMock(return_value=([model], 1))
        repository.are_all_user_stories_approved = AsyncMock(return_value=False)

        response = await UserStoryService(repository).list_user_stories_by_project(
            project_id=project.id,
            skip=0,
            limit=20,
            uow=uow,
        )

        assert [nfr.model_dump() for nfr in response.items[0].nfrs] == model.nfrs

    @pytest.mark.asyncio
    async def test_list_user_stories_by_project_not_found(self, uow):
        uow.projects.get_by_uuid.return_value = None
        repository = MagicMock()

        with pytest.raises(NotFoundError):
            await UserStoryService(repository).list_user_stories_by_project(
                project_id=uuid.uuid4(),
                skip=0,
                limit=20,
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_list_user_stories_by_project_no_sources(self, uow):
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        uow.sources.get_ids_by_project.return_value = []
        repository = MagicMock()
        repository.list_user_stories_for_project = AsyncMock(return_value=([], 0))
        repository.are_all_user_stories_approved = AsyncMock(return_value=False)

        response = await UserStoryService(repository).list_user_stories_by_project(
            project_id=project.id,
            skip=0,
            limit=20,
            uow=uow,
        )

        assert response.total == 0
        assert response.items == []
        repository.list_user_stories_for_project.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_project_user_story_summary_success(self, uow):
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        repository = MagicMock()
        repository.get_project_summary = AsyncMock(
            return_value={
                "total_user_stories": 10,
                "ready_count": 3,
                "needs_edit_count": 1,
                "failed_count": 2,
                "approved_count": 4,
                "total_modules": 4,
                "total_features": 12,
            }
        )

        response = await UserStoryService(repository).get_project_user_story_summary(
            project_id=project.id,
            uow=uow,
        )

        assert response.project_id == project.id
        assert response.total_user_stories == 10
        assert response.ready_count == 3
        assert response.needs_edit_count == 1
        assert response.failed_count == 2
        assert response.approved_count == 4
        assert response.total_modules == 4
        assert response.total_features == 12
        assert response.jira_sync_count == 0
        assert response.tap_sync_count == 0
        repository.get_project_summary.assert_awaited_once_with(project.id)

    @pytest.mark.asyncio
    async def test_get_project_user_story_summary_project_not_found(self, uow):
        uow.projects.get_by_uuid.return_value = None
        repository = MagicMock()

        with pytest.raises(NotFoundError):
            await UserStoryService(repository).get_project_user_story_summary(
                project_id=uuid.uuid4(),
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_upsert_user_stories_from_backlog_serializes_source_refs(self):
        repository = MagicMock()
        repository.bulk_upsert_user_stories_for_project = AsyncMock(return_value=1)

        service = UserStoryService(repository)
        project_id = uuid.uuid4()
        backlog_result = {
            "output": {
                "persona_glossary": [],
                "epics": [
                    {
                        "epic_code": "E-001",
                        "title": "Order Management",
                        "feature_id": "feature-1",
                        "parent_br": [],
                        "description": "Epic description",
                        "stories": [
                            {
                                "user_story_id": "story-1",
                                "user_story_code": "REQ-001",
                                "title": "Create order",
                                "as_a": "sales officer",
                                "i_want_to": "create new order",
                                "so_that": "I can process customer requests",
                                "acceptance_criteria": [
                                    {
                                        "type": "Happy Path",
                                        "given": "valid customer",
                                        "when": "submit order",
                                        "then": "order is created",
                                    }
                                ],
                                "nfrs": [
                                    {
                                        "id": "ARCH-001-NFR1",
                                        "category": "Security",
                                        "requirement": "Use parameterized queries.",
                                        "derived_from": "implied_gap",
                                    }
                                ],
                                "technical_notes": "none",
                                "story_points": 3,
                                "sources": [
                                    {
                                        "source_id": "src-1",
                                        "pages": [
                                            {
                                                "page": 1,
                                                "bboxes": [
                                                    {
                                                        "fragment_id": "frag-1",
                                                        "bbox": {
                                                            "x": 1,
                                                            "y": 2,
                                                            "w": 3,
                                                            "h": 4,
                                                        },
                                                    }
                                                ],
                                            },
                                            {
                                                "page": 2,
                                                "bboxes": [
                                                    {
                                                        "fragment_id": "frag-2",
                                                        "bbox": {
                                                            "x": 5,
                                                            "y": 6,
                                                            "w": 7,
                                                            "h": 8,
                                                        },
                                                    }
                                                ],
                                            },
                                        ],
                                    },
                                ],
                            }
                        ],
                    }
                ],
            }
        }

        upserted = await service.upsert_user_stories_from_backlog(
            project_id=project_id,
            backlog_result=backlog_result,
        )

        assert upserted == 1
        repository.bulk_upsert_user_stories_for_project.assert_awaited_once()

        req_models = repository.bulk_upsert_user_stories_for_project.await_args.kwargs[
            "user_stories"
        ]
        assert len(req_models) == 1
        assert req_models[0].sources == [
            {
                "source_id": "src-1",
                "pages": [
                    {
                        "page": 1,
                        "bboxes": [
                            {
                                "fragment_id": "frag-1",
                                "bbox": {"x": 1.0, "y": 2.0, "w": 3.0, "h": 4.0},
                            }
                        ],
                    },
                    {
                        "page": 2,
                        "bboxes": [
                            {
                                "fragment_id": "frag-2",
                                "bbox": {"x": 5.0, "y": 6.0, "w": 7.0, "h": 8.0},
                            }
                        ],
                    },
                ],
            },
        ]
        assert all(isinstance(item, dict) for item in req_models[0].sources)
        assert req_models[0].nfrs == [
            {
                "id": "ARCH-001-NFR1",
                "category": "Security",
                # StoryNFRSchema's canonical field is 'requirement' (no
                # 'description' field at all), so a raw payload that supplies
                # 'requirement' keeps it verbatim; 'description' normalizes to
                # empty since the validated schema never carries that key.
                "description": "",
                "requirement": "Use parameterized queries.",
            }
        ]

    @pytest.mark.asyncio
    async def test_upsert_user_stories_from_backlog_stamps_source_ingestion_id(self):
        repository = MagicMock()
        repository.bulk_upsert_user_stories_for_project = AsyncMock(return_value=1)
        service = UserStoryService(repository)
        project_id = uuid.uuid4()
        backlog_result = {
            "output": {
                "persona_glossary": [],
                "epics": [
                    {
                        "epic_code": "E-001",
                        "title": "Order Management",
                        "feature_id": "feature-1",
                        "parent_br": [],
                        "description": "Epic description",
                        "stories": [
                            {
                                "user_story_id": "story-1",
                                "user_story_code": "REQ-001",
                                "title": "Create order",
                                "as_a": "sales officer",
                                "i_want_to": "create new order",
                                "so_that": "I can process customer requests",
                                "acceptance_criteria": [],
                                "nfrs": [],
                                "technical_notes": "none",
                                "story_points": 3,
                                "sources": [],
                            }
                        ],
                    }
                ],
            }
        }

        await service.upsert_user_stories_from_backlog(
            project_id=project_id,
            backlog_result=backlog_result,
            source_ingestion_id="ingestion-1",
        )

        persisted_models = repository.bulk_upsert_user_stories_for_project.await_args.kwargs[
            "user_stories"
        ]
        assert persisted_models[0].source_ingestion_id == "ingestion-1"

    @staticmethod
    def _patch_story_payload(**overrides) -> dict:
        payload = {
            "user_story_id": "story-1",
            "user_story_code": "U.S 1.1.1",
            "title": "Create order",
            "as_a": "sales officer",
            "i_want_to": "create new order",
            "so_that": "I can process customer requests",
            "acceptance_criteria": [],
            "technical_notes": "none",
            "story_points": 3,
            "nfrs": [],
            "sources": [],
        }
        payload.update(overrides)
        return payload

    @pytest.mark.asyncio
    async def test_upsert_user_stories_from_patch_stamps_source_ingestion_id(self):
        repository = MagicMock()
        repository.bulk_upsert_user_stories_for_project = AsyncMock(return_value=1)
        repository.get_user_story_detail_for_project = AsyncMock(return_value=None)
        service = UserStoryService(repository)
        project_id = uuid.uuid4()
        patch_result = {"output": {"revised_stories": [self._patch_story_payload()]}}

        await service.upsert_user_stories_from_patch(
            project_id=project_id,
            patch_result=patch_result,
            story_code_to_feature_id={"U.S 1.1.1": "feature-1"},
            source_ingestion_id="ingestion-3",
        )

        persisted_models = repository.bulk_upsert_user_stories_for_project.await_args.kwargs[
            "user_stories"
        ]
        assert persisted_models[0].source_ingestion_id == "ingestion-3"

    @pytest.mark.asyncio
    async def test_upsert_user_stories_from_patch_marks_added_when_no_existing_story(self):
        repository = MagicMock()
        repository.bulk_upsert_user_stories_for_project = AsyncMock(return_value=1)
        repository.get_user_story_detail_for_project = AsyncMock(return_value=None)
        repository.snapshot_user_story_version = AsyncMock(return_value=True)
        service = UserStoryService(repository)
        patch_result = {"output": {"revised_stories": [self._patch_story_payload()]}}

        result = await service.upsert_user_stories_from_patch(
            project_id=uuid.uuid4(),
            patch_result=patch_result,
            story_code_to_feature_id={"U.S 1.1.1": "feature-1"},
        )

        persisted = repository.bulk_upsert_user_stories_for_project.await_args.kwargs[
            "user_stories"
        ][0]
        assert persisted.feedback_change_type == ChangeType.ADDED
        assert persisted.id == "story-1"
        assert persisted.version == 1
        repository.snapshot_user_story_version.assert_not_awaited()
        assert result == {"added": 1, "updated": 0}

    @pytest.mark.asyncio
    async def test_upsert_user_stories_from_patch_marks_updated_and_snapshots_on_change(self):
        repository = MagicMock()
        repository.bulk_upsert_user_stories_for_project = AsyncMock(return_value=1)
        existing = _make_model(user_story_id="story-1")
        existing.as_a = "sales officer"
        existing.i_want_to = "create new order"
        existing.so_that = "I can process customer requests"
        existing.technical_notes = "old notes"
        existing.story_points = 3
        existing.acceptance_criteria = []
        existing.nfrs = []
        existing.version = 2
        repository.get_user_story_detail_for_project = AsyncMock(return_value=existing)
        repository.snapshot_user_story_version = AsyncMock(return_value=True)
        service = UserStoryService(repository)
        patch_result = {
            "output": {"revised_stories": [self._patch_story_payload(technical_notes="new notes")]}
        }

        result = await service.upsert_user_stories_from_patch(
            project_id=uuid.uuid4(),
            patch_result=patch_result,
            story_code_to_feature_id={"U.S 1.1.1": "feature-1"},
        )

        repository.snapshot_user_story_version.assert_awaited_once_with("story-1")
        persisted = repository.bulk_upsert_user_stories_for_project.await_args.kwargs[
            "user_stories"
        ][0]
        assert persisted.feedback_change_type == ChangeType.UPDATED
        assert persisted.version == 3
        assert result == {"added": 0, "updated": 1}

    @pytest.mark.asyncio
    async def test_upsert_user_stories_from_patch_leaves_change_type_none_when_unchanged(self):
        repository = MagicMock()
        repository.bulk_upsert_user_stories_for_project = AsyncMock(return_value=1)
        existing = _make_model(user_story_id="story-1")
        existing.title = "Create order"
        existing.as_a = "sales officer"
        existing.i_want_to = "create new order"
        existing.so_that = "I can process customer requests"
        existing.technical_notes = "none"
        existing.story_points = 3
        existing.acceptance_criteria = []
        existing.nfrs = []
        existing.version = 4
        repository.get_user_story_detail_for_project = AsyncMock(return_value=existing)
        repository.snapshot_user_story_version = AsyncMock(return_value=True)
        service = UserStoryService(repository)
        patch_result = {"output": {"revised_stories": [self._patch_story_payload()]}}

        result = await service.upsert_user_stories_from_patch(
            project_id=uuid.uuid4(),
            patch_result=patch_result,
            story_code_to_feature_id={"U.S 1.1.1": "feature-1"},
        )

        repository.snapshot_user_story_version.assert_not_awaited()
        persisted = repository.bulk_upsert_user_stories_for_project.await_args.kwargs[
            "user_stories"
        ][0]
        assert persisted.feedback_change_type is None
        assert persisted.version == 4
        assert result == {"added": 0, "updated": 0}

    @pytest.mark.asyncio
    async def test_upsert_user_stories_from_patch_empty_output_returns_zero_counts(self):
        repository = MagicMock()
        service = UserStoryService(repository)

        result = await service.upsert_user_stories_from_patch(
            project_id=uuid.uuid4(),
            patch_result={"output": ""},
            story_code_to_feature_id={},
        )

        assert result == {"added": 0, "updated": 0}
        repository.bulk_upsert_user_stories_for_project.assert_not_called()

    def test_to_backlog_user_story_returns_nested_sources(self):
        model = _make_model(user_story_id="req_nested")
        model.project_id = uuid.uuid4()
        model.sources = [
            {
                "source_id": "src-1",
                "page": 3,
                "fragment_id": "frag-3",
                "bboxes": [{"x": 108.02, "y": 525.52, "w": 243.01, "h": 55.68}],
            },
            {
                "source_id": "src-1",
                "page": 2,
                "fragment_id": "frag-2",
                "bboxes": [{"x": 89.08, "y": 569.24, "w": 415.71, "h": 138.71}],
            },
        ]

        story_payload = UserStoryService._to_backlog_user_story(model)

        assert "sources" in story_payload
        assert "bboxes" not in story_payload
        assert story_payload["sources"] == [
            {
                "source_id": "src-1",
                "pages": [
                    {
                        "page": 3,
                        "bboxes": [
                            {
                                "fragment_id": "frag-3",
                                "bbox": {"x": 108.02, "y": 525.52, "w": 243.01, "h": 55.68},
                            }
                        ],
                    },
                    {
                        "page": 2,
                        "bboxes": [
                            {
                                "fragment_id": "frag-2",
                                "bbox": {"x": 89.08, "y": 569.24, "w": 415.71, "h": 138.71},
                            }
                        ],
                    },
                ],
            }
        ]

    @pytest.mark.asyncio
    async def test_upsert_user_stories_for_source_code_handles_missing_sources(self):
        repository = MagicMock()
        repository.bulk_upsert_user_stories_for_source_code = AsyncMock(return_value=1)

        service = UserStoryService(repository)
        project_id = uuid.uuid4()
        backlog_result = {
            "output": {
                "epics": [
                    {
                        "feature_id": "feature-1",
                        "title": "Order Management",
                        "stories": [
                            {
                                "user_story_id": "story-1",
                                "user_story_code": "REQ-001",
                                "module_id": "module-1",
                                "feature_id": "feature-1",
                                "title": "Create order",
                                "as_a": "sales officer",
                                "i_want_to": "create new order",
                                "so_that": "I can process customer requests",
                                "acceptance_criteria": [],
                                "nfrs": [],
                                "technical_notes": "none",
                                "story_points": 3,
                            }
                        ],
                    }
                ]
            }
        }

        upserted = await service.upsert_user_stories_for_source_code(
            project_id=project_id,
            backlog_result=backlog_result,
        )

        assert upserted == 1
        repository.bulk_upsert_user_stories_for_source_code.assert_awaited_once()

        req_models = repository.bulk_upsert_user_stories_for_source_code.await_args.kwargs[
            "user_stories"
        ]
        assert len(req_models) == 1
        assert req_models[0].sources == []
        assert req_models[0].source_file_count == 0

    @pytest.mark.asyncio
    async def test_upsert_user_stories_for_source_code_stamps_source_ingestion_id(self):
        repository = MagicMock()
        repository.bulk_upsert_user_stories_for_source_code = AsyncMock(return_value=1)

        service = UserStoryService(repository)
        project_id = uuid.uuid4()
        backlog_result = {
            "output": {
                "epics": [
                    {
                        "feature_id": "feature-1",
                        "title": "Order Management",
                        "stories": [
                            {
                                "user_story_id": "story-1",
                                "user_story_code": "REQ-001",
                                "module_id": "module-1",
                                "feature_id": "feature-1",
                                "title": "Create order",
                                "as_a": "sales officer",
                                "i_want_to": "create new order",
                                "so_that": "I can process customer requests",
                                "acceptance_criteria": [],
                                "nfrs": [],
                                "technical_notes": "none",
                                "story_points": 3,
                            }
                        ],
                    }
                ]
            }
        }

        await service.upsert_user_stories_for_source_code(
            project_id=project_id,
            backlog_result=backlog_result,
            source_ingestion_id="ingestion-2",
        )

        req_models = repository.bulk_upsert_user_stories_for_source_code.await_args.kwargs[
            "user_stories"
        ]
        assert req_models[0].source_ingestion_id == "ingestion-2"

    @pytest.mark.asyncio
    async def test_upsert_user_stories_for_source_code_handles_null_sources(self):
        repository = MagicMock()
        repository.bulk_upsert_user_stories_for_source_code = AsyncMock(return_value=1)

        service = UserStoryService(repository)
        project_id = uuid.uuid4()
        backlog_result = {
            "output": {
                "epics": [
                    {
                        "feature_id": "feature-1",
                        "title": "Order Management",
                        "stories": [
                            {
                                "user_story_id": "story-1",
                                "user_story_code": "REQ-001",
                                "module_id": "module-1",
                                "feature_id": "feature-1",
                                "title": "Create order",
                                "as_a": "sales officer",
                                "i_want_to": "create new order",
                                "so_that": "I can process customer requests",
                                "acceptance_criteria": [],
                                "nfrs": [],
                                "technical_notes": "none",
                                "story_points": 3,
                                "sources": None,
                            }
                        ],
                    }
                ]
            }
        }

        upserted = await service.upsert_user_stories_for_source_code(
            project_id=project_id,
            backlog_result=backlog_result,
        )

        assert upserted == 1
        repository.bulk_upsert_user_stories_for_source_code.assert_awaited_once()

        req_models = repository.bulk_upsert_user_stories_for_source_code.await_args.kwargs[
            "user_stories"
        ]
        assert len(req_models) == 1
        assert req_models[0].sources == []
        assert req_models[0].source_file_count == 0

    @pytest.mark.asyncio
    async def test_get_user_story_detail_by_project_success(self, uow):
        from app.schemas.user_story_schema import UserStoryDetailResponse

        project = make_project()
        source = make_source(is_deleted=False)
        uow.projects.get_by_uuid.return_value = project
        uow.sources.get_ids_by_project.return_value = [source.id]
        uow.sources.get_many_by_uuids.return_value = [source]

        req_model = _make_model(source.id, "req_1")
        req_model.sources = [
            {"source_id": str(source.id), "page": 1, "fragment_id": None, "bboxes": []}
        ]
        req_model.srs_evidence = [
            {
                "id": "e-1",
                "payload": {"file_name": "spec.md", "line": 18},
                "group_spec": {
                    "id": "gs-1",
                    "filename": "spec.md",
                    "content": {"kind": "markdown"},
                },
            }
        ]
        repository = MagicMock()
        repository.get_user_story_detail_for_project = AsyncMock(return_value=req_model)
        repository.get_latest_user_story_version = AsyncMock(return_value=None)

        response = await UserStoryService(repository).get_user_story_detail_by_project(
            project_id=project.id,
            user_story_id="req_1",
            uow=uow,
        )

        assert isinstance(response, UserStoryDetailResponse)
        assert response.id == "req_1"
        assert response.user_story_code == "ARCH-001"
        assert len(response.source_files) == 1
        assert response.source_files[0].name == source.original_name
        assert response.source_files[0].type == source.file_type.lower()
        assert len(response.srs_evidence) == 1
        assert response.srs_evidence[0].id == "e-1"
        assert response.srs_evidence[0].group_spec is not None
        assert response.srs_evidence[0].group_spec.id == "gs-1"
        repository.get_user_story_detail_for_project.assert_awaited_once_with(
            project.id,
            "req_1",
            False,
        )
        uow.sources.get_many_by_uuids.assert_called_once_with([source.id])

    @pytest.mark.asyncio
    async def test_get_user_story_detail_by_project_updated_fields_reflects_diff_from_last_version(
        self, uow
    ):
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        uow.sources.get_ids_by_project.return_value = []

        req_model = _make_model(None, "req_1")
        req_model.title = "Core Architecture Renamed"
        req_model.is_jira_synced = True
        repository = MagicMock()
        repository.get_user_story_detail_for_project = AsyncMock(return_value=req_model)
        repository.get_latest_user_story_version = AsyncMock(
            return_value=UserStoryVersionModel(
                id="version_1",
                user_story_id="req_1",
                user_story_code="ARCH-001",
                title="Core Architecture",
                description="Some description",
                status="approved",
                version=1,
                is_jira_synced=False,
            )
        )

        response = await UserStoryService(repository).get_user_story_detail_by_project(
            project_id=project.id,
            user_story_id="req_1",
            uow=uow,
        )

        assert response.last_previous_items is not None
        # is_jira_synced differs too, but sync flags aren't tracked as content edits.
        assert set(response.updated_fields) == {"title"}

    @pytest.mark.asyncio
    async def test_get_user_story_detail_by_project_updated_fields_excludes_status_and_change_type(
        self, uow
    ):
        """status/is_jira_synced/is_tap_synced/incremental_change_type/feedback_change_type
        are metadata, not user-facing content edits — they must never appear in
        ``updated_fields`` even when they differ from the last snapshot."""
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        uow.sources.get_ids_by_project.return_value = []

        req_model = _make_model(None, "req_1")
        req_model.status = "needs_edit"
        req_model.is_jira_synced = True
        req_model.is_tap_synced = True
        req_model.incremental_change_type = ChangeType.ADDED
        req_model.feedback_change_type = ChangeType.UPDATED
        repository = MagicMock()
        repository.get_user_story_detail_for_project = AsyncMock(return_value=req_model)
        repository.get_latest_user_story_version = AsyncMock(
            return_value=UserStoryVersionModel(
                id="version_1",
                user_story_id="req_1",
                user_story_code="ARCH-001",
                title="Core Architecture",
                description="Some description",
                status="approved",
                version=1,
                is_jira_synced=False,
                is_tap_synced=False,
                incremental_change_type=None,
                feedback_change_type=None,
            )
        )

        response = await UserStoryService(repository).get_user_story_detail_by_project(
            project_id=project.id,
            user_story_id="req_1",
            uow=uow,
        )

        assert response.updated_fields == []

    @pytest.mark.asyncio
    async def test_get_user_story_detail_by_project_updated_fields_empty_when_no_prior_version(
        self, uow
    ):
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        uow.sources.get_ids_by_project.return_value = []

        req_model = _make_model(None, "req_1")
        repository = MagicMock()
        repository.get_user_story_detail_for_project = AsyncMock(return_value=req_model)
        repository.get_latest_user_story_version = AsyncMock(return_value=None)

        response = await UserStoryService(repository).get_user_story_detail_by_project(
            project_id=project.id,
            user_story_id="req_1",
            uow=uow,
        )

        assert response.updated_fields == []

    @pytest.mark.asyncio
    async def test_get_user_story_detail_by_project_not_found(self, uow):
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        uow.sources.get_ids_by_project.return_value = [uuid.uuid4()]
        repository = MagicMock()
        repository.get_user_story_detail_for_project = AsyncMock(return_value=None)

        with pytest.raises(NotFoundError):
            await UserStoryService(repository).get_user_story_detail_by_project(
                project_id=project.id,
                user_story_id="missing",
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_get_user_story_detail_by_project_project_not_found(self, uow):
        uow.projects.get_by_uuid.return_value = None
        repository = MagicMock()

        with pytest.raises(NotFoundError):
            await UserStoryService(repository).get_user_story_detail_by_project(
                project_id=uuid.uuid4(),
                user_story_id="req_1",
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_get_user_story_detail_by_project_ignores_invalid_source_ids(self, uow):
        project = make_project()
        source = make_source(is_deleted=False)
        uow.projects.get_by_uuid.return_value = project
        uow.sources.get_ids_by_project.return_value = [source.id]
        uow.sources.get_many_by_uuids.return_value = [source]

        req_model = _make_model(source.id, "req_1")
        req_model.sources = [
            {"source_id": "not-a-uuid", "page": 1, "fragment_id": None, "bboxes": []},
            {"source_id": str(source.id), "page": 2, "fragment_id": None, "bboxes": []},
        ]
        repository = MagicMock()
        repository.get_user_story_detail_for_project = AsyncMock(return_value=req_model)
        repository.get_latest_user_story_version = AsyncMock(return_value=None)

        response = await UserStoryService(repository).get_user_story_detail_by_project(
            project_id=project.id,
            user_story_id="req_1",
            uow=uow,
        )

        assert response.id == "req_1"
        uow.sources.get_many_by_uuids.assert_called_once_with([source.id])

    @pytest.mark.asyncio
    async def test_enqueue_user_story_generation_dispatches_with_serialized_payloads(self, uow):
        project = make_project()
        source = make_source(project_id=project.id, is_deleted=False)
        uow.projects.get_by_uuid.return_value = project
        uow.sources.get_ids_by_project.return_value = [source.id]
        uow.sources.get_by_uuid.return_value = source

        fragment = SimpleNamespace(
            id="frag_1",
            source_id=source.id,
            source_type="rfp",
            frag_type="paragraph",
            content="Fragment text",
            bbox=[],
        )
        module = SimpleNamespace(
            id="module_1",
            mod_code="MOD_001",
            name="Module 1",
            description="Module description",
            features=[
                SimpleNamespace(
                    id="feature_1",
                    fea_code="FEA_001",
                    name="Feature 1",
                    description="Feature description",
                    functions=[],
                    sources=[],
                    l2_sources=[],
                )
            ],
        )

        service = UserStoryService(MagicMock())
        with (
            patch(
                "app.repositories.neo4j.module_feature_repository.ModuleFeatureRepository.list_modules_by_project",
                new=AsyncMock(return_value=[module]),
            ),
            patch(
                "app.services.fragment_service.FragmentService.list_fragments",
                new=AsyncMock(return_value=SimpleNamespace(fragments=[fragment])),
            ),
            patch(
                "app.workers.document_task.generate_user_story_task.apply_async",
                return_value=SimpleNamespace(id="task-gen-123"),
            ) as mock_apply_async,
        ):
            response = await service.enqueue_user_story_generation(
                project_id=project.id,
                uow=uow,
            )

        assert response.task_id  # UUID string from ProjectTask row
        assert len(response.task_id) == 36
        assert response.source_ids == [source.id]
        apply_args = mock_apply_async.call_args.kwargs["args"]
        assert apply_args[0] == str(project.id)
        assert isinstance(apply_args[1], list)
        assert apply_args[1][0]["id"] == "frag_1"
        assert isinstance(apply_args[2], dict)
        assert apply_args[2]["feature_inventory"][0]["id"] == "module_1"
        assert apply_args[4] is False

    @pytest.mark.asyncio
    async def test_enqueue_user_story_generation_forwards_skip_processing_true(self, uow):
        project = make_project()
        source = make_source(project_id=project.id, is_deleted=False)
        uow.projects.get_by_uuid.return_value = project
        uow.sources.get_ids_by_project.return_value = [source.id]
        uow.sources.get_by_uuid.return_value = source

        service = UserStoryService(MagicMock())
        with (
            patch(
                "app.repositories.neo4j.module_feature_repository.ModuleFeatureRepository.list_modules_by_project",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "app.services.fragment_service.FragmentService.list_fragments",
                new=AsyncMock(return_value=SimpleNamespace(fragments=[])),
            ),
            patch(
                "app.workers.document_task.generate_user_story_task.apply_async",
                return_value=SimpleNamespace(id="task-gen-skip-123"),
            ) as mock_apply_async,
        ):
            await service.enqueue_user_story_generation(
                project_id=project.id,
                uow=uow,
                skip_processing=True,
            )

        apply_args = mock_apply_async.call_args.kwargs["args"]
        assert apply_args[4] is True

    @pytest.mark.asyncio
    async def test_enqueue_user_story_generation_flips_ingestion_status_to_running_synchronously(
        self, uow
    ):
        """Regression test: the SourceIngestion's status must flip to RUNNING
        in this same call — not only later, inside the Celery task body once a
        worker picks it up — otherwise a client polling right after this call
        still sees the ingestion's prior READY_FOR_REVIEW (from module_feature
        generation) instead of RUNNING for however long the task sits queued."""
        project = make_project()
        source = make_source(project_id=project.id, is_deleted=False)
        ingestion_id = uuid.uuid4()
        source.source_ingestion_id = ingestion_id
        uow.projects.get_by_uuid.return_value = project
        uow.sources.get_ids_by_project.return_value = [source.id]
        uow.sources.get_by_uuid.return_value = source
        uow.sources.get_many_by_uuids.return_value = [source]

        service = UserStoryService(MagicMock())
        with (
            patch(
                "app.repositories.neo4j.module_feature_repository.ModuleFeatureRepository.list_modules_by_project",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "app.services.fragment_service.FragmentService.list_fragments",
                new=AsyncMock(return_value=SimpleNamespace(fragments=[])),
            ),
            patch(
                "app.workers.document_task.generate_user_story_task.apply_async",
                return_value=SimpleNamespace(id="task-gen-running-123"),
            ),
        ):
            await service.enqueue_user_story_generation(
                project_id=project.id,
                uow=uow,
            )

        uow.source_ingestions.update_fields.assert_called_once_with(
            ingestion_id, status="running"
        )

    @pytest.mark.asyncio
    async def test_enqueue_user_story_regeneration_serializes_existing_user_stories_and_strips_feedback(
        self, uow
    ):
        project = make_project()
        source = make_source(project_id=project.id, is_deleted=False)
        uow.projects.get_by_uuid.return_value = project
        uow.sources.get_ids_by_project.return_value = [source.id]
        uow.sources.get_by_uuid.return_value = source

        fragment = SimpleNamespace(
            id="frag_2",
            source_id=source.id,
            source_type="rfp",
            frag_type="paragraph",
            content="Another fragment",
            bbox=[],
        )
        module = SimpleNamespace(
            id="module_2",
            mod_code="MOD_002",
            name="Module 2",
            description="",
            features=[],
        )
        repository = MagicMock()
        repository.list_user_stories_for_project = AsyncMock(return_value=([_make_model()], 1))

        service = UserStoryService(repository)
        with (
            patch(
                "app.repositories.neo4j.module_feature_repository.ModuleFeatureRepository.list_modules_by_project",
                new=AsyncMock(return_value=[module]),
            ),
            patch(
                "app.services.fragment_service.FragmentService.list_fragments",
                new=AsyncMock(return_value=SimpleNamespace(fragments=[fragment])),
            ),
            patch(
                "app.workers.document_task.regenerate_user_story_task.apply_async",
                return_value=SimpleNamespace(id="task-regen-456"),
            ) as mock_apply_async,
        ):
            response = await service.enqueue_user_story_regeneration(
                project_id=project.id,
                feedback="  tighten acceptance criteria  ",
                uow=uow,
            )

        assert response.task_id  # UUID string from ProjectTask row
        assert len(response.task_id) == 36
        apply_args = mock_apply_async.call_args.kwargs["args"]
        assert apply_args[0] == str(project.id)
        assert isinstance(apply_args[3], dict)
        stories = apply_args[3]["epics"][0]["stories"]
        assert stories[0]["user_story_code"] == "ARCH-001"
        assert stories[0]["sources"] == []
        assert apply_args[4] == "tighten acceptance criteria"

    @pytest.mark.asyncio
    async def test_enqueue_user_story_regeneration_passes_empty_user_stories_string_when_none_exist(
        self, uow
    ):
        project = make_project()
        source = make_source(project_id=project.id, is_deleted=False)
        uow.projects.get_by_uuid.return_value = project
        uow.sources.get_ids_by_project.return_value = [source.id]
        uow.sources.get_by_uuid.return_value = source

        repository = MagicMock()
        repository.list_user_stories_for_project = AsyncMock(return_value=([], 0))
        service = UserStoryService(repository)

        with (
            patch(
                "app.repositories.neo4j.module_feature_repository.ModuleFeatureRepository.list_modules_by_project",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "app.services.fragment_service.FragmentService.list_fragments",
                new=AsyncMock(return_value=SimpleNamespace(fragments=[])),
            ),
            patch(
                "app.workers.document_task.regenerate_user_story_task.apply_async",
                return_value=SimpleNamespace(id="task-regen-empty"),
            ) as mock_apply_async,
        ):
            await service.enqueue_user_story_regeneration(
                project_id=project.id,
                feedback="   ",
                uow=uow,
            )

        apply_args = mock_apply_async.call_args.kwargs["args"]
        assert apply_args[3] == ""
        assert apply_args[4] == ""

    @pytest.mark.asyncio
    async def test_enqueue_user_story_regeneration_no_sources_creates_standalone_ingestion(
        self, uow
    ):
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        uow.sources.get_ids_by_project.return_value = []

        repository = MagicMock()
        repository.list_user_stories_for_project = AsyncMock(return_value=([], 0))
        service = UserStoryService(repository)

        with (
            patch(
                "app.repositories.neo4j.module_feature_repository.ModuleFeatureRepository.list_modules_by_project",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "app.workers.document_task.regenerate_user_story_task.apply_async",
                return_value=SimpleNamespace(id="task-regen-no-sources"),
            ) as mock_apply_async,
        ):
            response = await service.enqueue_user_story_regeneration(
                project_id=project.id,
                feedback="Tighten acceptance criteria",
                user_story_ids=["story_1"],
                uow=uow,
            )

        assert response.source_ids == []
        mock_apply_async.assert_called_once()
        uow.source_ingestions.update_fields.assert_not_called()
        uow.source_ingestions.create_ingestion.assert_called_once_with(
            project_id=project.id,
            source_type="requirement_update",
            status="running",
            stages=["generating_module_feature", "generating_user_story"],
            entity_json={
                "feedback": "Tighten acceptance criteria",
                "user_story_ids": ["story_1"],
            },
            user_story_gen_started_at=ANY,
        )

    @pytest.mark.asyncio
    async def test_enqueue_user_story_regeneration_by_feedback_no_sources_creates_standalone_ingestion(
        self, uow
    ):
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        uow.project_tasks.list_active_by_project_and_type.return_value = []
        uow.sources.get_ids_by_project.return_value = []

        model = _make_model(user_story_id="story_1")
        repository = MagicMock()
        repository.get_user_story_detail_for_project = AsyncMock(return_value=model)
        repository.list_user_stories_for_project = AsyncMock(return_value=([], 0))

        service = UserStoryService(repository)
        feedback_items = [
            StoryFeedbackInput(user_story_id="story_1", overall_feedback="Tighten the AC"),
        ]

        with (
            patch(
                "app.repositories.neo4j.module_feature_repository.ModuleFeatureRepository.list_modules_by_project",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "app.services.project_graph_service.ProjectGraphService.get_persona_glossary",
                return_value=[],
            ),
            patch(
                "app.workers.document_task.regenerate_user_stories_by_feedback_task.apply_async",
                return_value=SimpleNamespace(id="task-feedback-123"),
            ) as mock_apply_async,
        ):
            response = await service.enqueue_user_story_regeneration_by_feedback(
                project_id=project.id,
                feedback_items=feedback_items,
                uow=uow,
            )

        assert response.user_story_ids == ["story_1"]
        mock_apply_async.assert_called_once()
        uow.source_ingestions.update_fields.assert_not_called()
        uow.source_ingestions.create_ingestion.assert_called_once_with(
            project_id=project.id,
            source_type="requirement_update",
            status="running",
            stages=["generating_requirements"],
            entity_json={
                "user_story_ids": ["story_1"],
                "feedback_items": [
                    {
                        "user_story_id": "story_1",
                        "overall_feedback": "Tighten the AC",
                        "specific_feedback": None,
                    }
                ],
            },
            started_at=ANY,
        )

    @pytest.mark.asyncio
    async def test_delete_user_story_cascades_to_empty_feature_and_module(self, uow):
        project = make_project()
        uow.projects.get_by_uuid.return_value = project

        req_model = _make_model(user_story_id="req_1")
        req_model.status = "ready"
        req_model.feature_id = "feature_1"

        repository = MagicMock()
        repository.get_user_story_detail_for_project = AsyncMock(return_value=req_model)
        repository.delete_user_story_by_id = AsyncMock(return_value=True)
        repository.count_active_user_stories_for_feature = AsyncMock(return_value=0)

        module_feature_repository = MagicMock()
        module_feature_repository.get_module_id_for_feature = AsyncMock(return_value="module_1")
        module_feature_repository.delete_feature_by_id = AsyncMock(return_value=True)
        module_feature_repository.count_features_for_module = AsyncMock(return_value=0)
        module_feature_repository.delete_module_by_id = AsyncMock(return_value=True)

        service = UserStoryService(repository, module_feature_repository)
        await service.delete_user_story(
            project_id=project.id,
            user_story_id="req_1",
            uow=uow,
        )

        repository.count_active_user_stories_for_feature.assert_awaited_once_with(
            project_id=project.id, feature_id="feature_1"
        )
        module_feature_repository.get_module_id_for_feature.assert_awaited_once_with(
            project.id, "feature_1"
        )
        module_feature_repository.delete_feature_by_id.assert_awaited_once_with(
            project.id, "feature_1"
        )
        module_feature_repository.count_features_for_module.assert_awaited_once_with(
            project.id, "module_1"
        )
        module_feature_repository.delete_module_by_id.assert_awaited_once_with(
            project.id, "module_1"
        )

    @pytest.mark.asyncio
    async def test_delete_user_story_keeps_feature_when_active_stories_remain(self, uow):
        project = make_project()
        uow.projects.get_by_uuid.return_value = project

        req_model = _make_model(user_story_id="req_1")
        req_model.status = "ready"
        req_model.feature_id = "feature_1"

        repository = MagicMock()
        repository.get_user_story_detail_for_project = AsyncMock(return_value=req_model)
        repository.delete_user_story_by_id = AsyncMock(return_value=True)
        repository.count_active_user_stories_for_feature = AsyncMock(return_value=2)

        module_feature_repository = MagicMock()
        module_feature_repository.get_module_id_for_feature = AsyncMock()
        module_feature_repository.delete_feature_by_id = AsyncMock()

        service = UserStoryService(repository, module_feature_repository)
        await service.delete_user_story(
            project_id=project.id,
            user_story_id="req_1",
            uow=uow,
        )

        module_feature_repository.get_module_id_for_feature.assert_not_awaited()
        module_feature_repository.delete_feature_by_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_user_story_keeps_module_when_other_features_remain(self, uow):
        project = make_project()
        uow.projects.get_by_uuid.return_value = project

        req_model = _make_model(user_story_id="req_1")
        req_model.status = "ready"
        req_model.feature_id = "feature_1"

        repository = MagicMock()
        repository.get_user_story_detail_for_project = AsyncMock(return_value=req_model)
        repository.delete_user_story_by_id = AsyncMock(return_value=True)
        repository.count_active_user_stories_for_feature = AsyncMock(return_value=0)

        module_feature_repository = MagicMock()
        module_feature_repository.get_module_id_for_feature = AsyncMock(return_value="module_1")
        module_feature_repository.delete_feature_by_id = AsyncMock(return_value=True)
        module_feature_repository.count_features_for_module = AsyncMock(return_value=1)
        module_feature_repository.delete_module_by_id = AsyncMock()

        service = UserStoryService(repository, module_feature_repository)
        await service.delete_user_story(
            project_id=project.id,
            user_story_id="req_1",
            uow=uow,
        )

        module_feature_repository.delete_feature_by_id.assert_awaited_once_with(
            project.id, "feature_1"
        )
        module_feature_repository.delete_module_by_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_user_story_soft_delete_cascades_to_feature_and_module(self, uow):
        project = make_project()
        uow.projects.get_by_uuid.return_value = project

        req_model = _make_model(user_story_id="req_1")
        req_model.status = UserStoryStatus.APPROVED
        req_model.feature_id = "feature_1"

        repository = MagicMock()
        repository.get_user_story_detail_for_project = AsyncMock(return_value=req_model)
        repository.soft_delete_user_story_by_id = AsyncMock(return_value=True)
        repository.count_active_user_stories_for_feature = AsyncMock(return_value=0)

        module_feature_repository = MagicMock()
        module_feature_repository.get_module_id_for_feature = AsyncMock(return_value="module_1")
        module_feature_repository.delete_feature_by_id = AsyncMock(return_value=True)
        module_feature_repository.count_features_for_module = AsyncMock(return_value=0)
        module_feature_repository.delete_module_by_id = AsyncMock(return_value=True)

        service = UserStoryService(repository, module_feature_repository)
        outcome = await service.delete_user_story(
            project_id=project.id,
            user_story_id="req_1",
            uow=uow,
            reason="No longer needed",
        )

        assert outcome["is_current"] is False
        module_feature_repository.delete_feature_by_id.assert_awaited_once_with(
            project.id, "feature_1"
        )
        module_feature_repository.delete_module_by_id.assert_awaited_once_with(
            project.id, "module_1"
        )

    @pytest.mark.asyncio
    async def test_delete_user_story_approved_without_reason_raises_validation_error(self, uow):
        """A regression guard for the approved-delete reason requirement.

        Reason is required only when the story being deleted is 'approved'
        (soft delete) — this must reject before any repository write happens.
        """
        project = make_project()
        uow.projects.get_by_uuid.return_value = project

        req_model = _make_model(user_story_id="req_1")
        req_model.status = UserStoryStatus.APPROVED

        repository = MagicMock()
        repository.get_user_story_detail_for_project = AsyncMock(return_value=req_model)
        repository.soft_delete_user_story_by_id = AsyncMock()

        module_feature_repository = MagicMock()
        service = UserStoryService(repository, module_feature_repository)

        with pytest.raises(ValidationError):
            await service.delete_user_story(
                project_id=project.id,
                user_story_id="req_1",
                uow=uow,
                reason=None,
            )

        repository.soft_delete_user_story_by_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_user_story_skips_cleanup_when_feature_id_missing(self, uow):
        project = make_project()
        uow.projects.get_by_uuid.return_value = project

        req_model = _make_model(user_story_id="req_1")
        req_model.status = "ready"
        req_model.feature_id = None

        repository = MagicMock()
        repository.get_user_story_detail_for_project = AsyncMock(return_value=req_model)
        repository.delete_user_story_by_id = AsyncMock(return_value=True)
        repository.count_active_user_stories_for_feature = AsyncMock()

        module_feature_repository = MagicMock()

        service = UserStoryService(repository, module_feature_repository)
        await service.delete_user_story(
            project_id=project.id,
            user_story_id="req_1",
            uow=uow,
        )

        repository.count_active_user_stories_for_feature.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_change_status_by_project_success(self, uow):
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        uow.source_ingestions.list_running_by_project.return_value = []

        repository = MagicMock()
        repository.change_all_user_story_status_by_project = AsyncMock(return_value=3)

        service = UserStoryService(repository, MagicMock())
        result_project_id, status, count = await service.change_status_by_project(
            project_id=project.id,
            request=ChangeStatusRequest(status=UserStoryStatus.APPROVED),
            uow=uow,
        )

        assert result_project_id == project.id
        assert status == "approved"
        assert count == 3

    @pytest.mark.asyncio
    async def test_change_status_by_project_not_found(self, uow):
        uow.projects.get_by_uuid.return_value = None
        service = UserStoryService(MagicMock(), MagicMock())

        with pytest.raises(NotFoundError):
            await service.change_status_by_project(
                project_id=uuid.uuid4(),
                request=ChangeStatusRequest(status=UserStoryStatus.APPROVED),
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_change_status_by_project_pipeline_running_raises_conflict(self, uow):
        from app.core.exceptions import ConflictError

        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        uow.source_ingestions.list_running_by_project.return_value = [MagicMock()]
        service = UserStoryService(MagicMock(), MagicMock())

        with pytest.raises(ConflictError):
            await service.change_status_by_project(
                project_id=project.id,
                request=ChangeStatusRequest(status=UserStoryStatus.APPROVED),
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_bulk_change_status_by_project_success(self, uow):
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        uow.source_ingestions.list_running_by_project.return_value = []

        repository = MagicMock()
        repository.bulk_change_user_story_status_by_ids = AsyncMock(return_value=2)

        service = UserStoryService(repository, MagicMock())
        response = await service.bulk_change_status_by_project(
            project_id=project.id,
            request=BulkStatusChangeRequest(
                user_story_ids=["req_1", "req_2"], status=UserStoryStatus.APPROVED
            ),
            uow=uow,
        )

        assert response.updated_count == 2
        assert response.status == "approved"
        assert response.user_story_ids == ["req_1", "req_2"]

    @pytest.mark.asyncio
    async def test_bulk_change_status_by_project_not_found(self, uow):
        uow.projects.get_by_uuid.return_value = None
        service = UserStoryService(MagicMock(), MagicMock())

        with pytest.raises(NotFoundError):
            await service.bulk_change_status_by_project(
                project_id=uuid.uuid4(),
                request=BulkStatusChangeRequest(
                    user_story_ids=["req_1"], status=UserStoryStatus.APPROVED
                ),
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_change_status_approved_triggers_generation_ingestion_completion(self, uow):
        uow.source_ingestions.list_running_by_project.return_value = []
        repository = MagicMock()
        repository.change_user_story_status = AsyncMock(
            return_value=_make_model(uuid.uuid4(), "req_1")
        )
        project_id = uuid.uuid4()

        with patch(
            "app.services.source_ingestion_service.SourceIngestionService."
            "try_complete_generation_ingestion",
            new=AsyncMock(),
        ) as mock_complete:
            await UserStoryService(repository).change_status(
                user_story_id="req_1",
                request=ChangeStatusRequest(status="approved"),
                project_id=project_id,
                uow=uow,
            )

        mock_complete.assert_awaited_once_with(uow, project_id, repository, actor_user_id=None)

    @pytest.mark.asyncio
    async def test_change_status_approved_forwards_user_id_as_actor(self, uow):
        uow.source_ingestions.list_running_by_project.return_value = []
        repository = MagicMock()
        repository.change_user_story_status = AsyncMock(
            return_value=_make_model(uuid.uuid4(), "req_1")
        )
        project_id = uuid.uuid4()
        actor_id = uuid.uuid4()

        with patch(
            "app.services.source_ingestion_service.SourceIngestionService."
            "try_complete_generation_ingestion",
            new=AsyncMock(),
        ) as mock_complete:
            await UserStoryService(repository).change_status(
                user_story_id="req_1",
                request=ChangeStatusRequest(status="approved"),
                project_id=project_id,
                uow=uow,
                user_id=actor_id,
            )

        mock_complete.assert_awaited_once_with(uow, project_id, repository, actor_user_id=actor_id)

    @pytest.mark.asyncio
    async def test_change_status_non_approved_does_not_trigger_completion(self, uow):
        uow.source_ingestions.list_running_by_project.return_value = []
        repository = MagicMock()
        repository.change_user_story_status = AsyncMock(
            return_value=_make_model(uuid.uuid4(), "req_1")
        )

        with patch(
            "app.services.source_ingestion_service.SourceIngestionService."
            "try_complete_generation_ingestion",
            new=AsyncMock(),
        ) as mock_complete:
            await UserStoryService(repository).change_status(
                user_story_id="req_1",
                request=ChangeStatusRequest(status="needs_edit"),
                project_id=uuid.uuid4(),
                uow=uow,
            )

        mock_complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_change_status_by_project_approved_triggers_generation_ingestion_completion(
        self, uow
    ):
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        uow.source_ingestions.list_running_by_project.return_value = []
        repository = MagicMock()
        repository.change_all_user_story_status_by_project = AsyncMock(return_value=3)
        service = UserStoryService(repository, MagicMock())

        with patch(
            "app.services.source_ingestion_service.SourceIngestionService."
            "try_complete_generation_ingestion",
            new=AsyncMock(),
        ) as mock_complete:
            await service.change_status_by_project(
                project_id=project.id,
                request=ChangeStatusRequest(status=UserStoryStatus.APPROVED),
                uow=uow,
            )

        mock_complete.assert_awaited_once_with(uow, project.id, repository, actor_user_id=None)

    @pytest.mark.asyncio
    async def test_bulk_change_status_by_project_approved_triggers_generation_ingestion_completion(
        self, uow
    ):
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        uow.source_ingestions.list_running_by_project.return_value = []
        repository = MagicMock()
        repository.bulk_change_user_story_status_by_ids = AsyncMock(return_value=2)
        service = UserStoryService(repository, MagicMock())

        with patch(
            "app.services.source_ingestion_service.SourceIngestionService."
            "try_complete_generation_ingestion",
            new=AsyncMock(),
        ) as mock_complete:
            await service.bulk_change_status_by_project(
                project_id=project.id,
                request=BulkStatusChangeRequest(
                    user_story_ids=["req_1", "req_2"], status=UserStoryStatus.APPROVED
                ),
                uow=uow,
            )

        mock_complete.assert_awaited_once_with(uow, project.id, repository, actor_user_id=None)

    @pytest.mark.asyncio
    async def test_update_sync_flags_success(self):
        repository = MagicMock()
        repository.update_user_story_sync_flags = AsyncMock(
            return_value=_make_model(user_story_id="req_1")
        )
        service = UserStoryService(repository, MagicMock())

        response = await service.update_sync_flags(
            user_story_id="req_1",
            request=UserStorySyncFlagsUpdateRequest(is_jira_synced=True),
        )

        assert response.id == "req_1"
        repository.update_user_story_sync_flags.assert_awaited_once_with(
            "req_1", is_jira_synced=True, is_tap_synced=None
        )

    @pytest.mark.asyncio
    async def test_update_sync_flags_not_found(self):
        repository = MagicMock()
        repository.update_user_story_sync_flags = AsyncMock(return_value=None)
        service = UserStoryService(repository, MagicMock())

        with pytest.raises(NotFoundError):
            await service.update_sync_flags(
                user_story_id="missing",
                request=UserStorySyncFlagsUpdateRequest(is_jira_synced=True),
            )

    @pytest.mark.asyncio
    async def test_update_user_story_bboxes_success(self, uow):
        project = make_project()
        uow.projects.get_by_uuid.return_value = project

        repository = MagicMock()
        repository.update_user_story_sources = AsyncMock(
            return_value=_make_model(user_story_id="req_1")
        )
        service = UserStoryService(repository, MagicMock())

        response = await service.update_user_story_bboxes(
            project_id=project.id,
            user_story_id="req_1",
            sources=[{"source_id": str(uuid.uuid4()), "pages": []}],
            uow=uow,
        )

        assert response.id == "req_1"

    @pytest.mark.asyncio
    async def test_update_user_story_bboxes_project_not_found(self, uow):
        uow.projects.get_by_uuid.return_value = None
        service = UserStoryService(MagicMock(), MagicMock())

        with pytest.raises(NotFoundError):
            await service.update_user_story_bboxes(
                project_id=uuid.uuid4(),
                user_story_id="req_1",
                sources=None,
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_update_user_story_bboxes_not_found(self, uow):
        project = make_project()
        uow.projects.get_by_uuid.return_value = project

        repository = MagicMock()
        repository.update_user_story_sources = AsyncMock(return_value=None)
        service = UserStoryService(repository, MagicMock())

        with pytest.raises(NotFoundError):
            await service.update_user_story_bboxes(
                project_id=project.id,
                user_story_id="missing",
                sources=None,
                uow=uow,
            )


class TestExtractRfpFlagMap:
    def test_clean_run_returns_empty_map(self):
        result = UserStoryService._extract_rfp_flag_map({"status": "PASS"})
        assert result == {}

    def test_top_level_fail_status_extracts_flags(self):
        backlog_result = {
            "status": "FAIL",
            "generation_metadata": {
                "flagged_items": [
                    {
                        "entity_type": "story",
                        "entity_id": "US-1",
                        "issue": "vague",
                        "suggested_fix": "clarify",
                    },
                    {"entity_type": "feature", "entity_id": "FEA-1"},
                    {"entity_type": "story", "entity_id": None},
                ]
            },
        }

        result = UserStoryService._extract_rfp_flag_map(backlog_result)

        assert result == {
            "US-1": {
                "entity_id": "US-1",
                "entity_type": "story",
                "issue": "vague",
                "suggested_fix": "clarify",
            }
        }

    def test_final_status_containing_fail_extracts_flags(self):
        backlog_result = {
            "status": "OK",
            "generation_metadata": {
                "final_status": "FAIL_CORRECTION_EXHAUSTED",
                "flagged_items": [
                    {"entity_type": "story", "entity_id": "US-2"},
                ],
            },
        }

        result = UserStoryService._extract_rfp_flag_map(backlog_result)

        assert "US-2" in result


class TestResolveSourceCodeStoryStatus:
    def test_missing_review_guidance_returns_failed(self):
        assert (
            UserStoryService._resolve_source_code_story_status(None, "U.S 1.1.1")
            == UserStoryStatus.FAILED.value
        )

    def test_malformed_review_guidance_returns_failed(self):
        result = UserStoryService._resolve_source_code_story_status(
            {"review_guidance": "not a dict"}, "U.S 1.1.1"
        )
        assert result == UserStoryStatus.FAILED.value

    def test_story_not_flagged_returns_ready(self):
        metadata = {"review_guidance": {"flagged_story_ids": ["U.S 9.9.9"]}}
        result = UserStoryService._resolve_source_code_story_status(metadata, "U.S 1.1.1")
        assert result == UserStoryStatus.READY.value

    def test_flagged_with_approve_as_is_returns_ready(self):
        metadata = {
            "review_guidance": {
                "flagged_story_ids": ["U.S 1.1.1"],
                "recommended_action": "APPROVE_AS_IS",
                "approve_as_is_allowed": True,
            }
        }
        result = UserStoryService._resolve_source_code_story_status(metadata, "U.S 1.1.1")
        assert result == UserStoryStatus.READY.value

    def test_flagged_with_edit_returns_needs_edit(self):
        metadata = {
            "review_guidance": {
                "flagged_story_ids": ["U.S 1.1.1"],
                "recommended_action": "EDIT",
                "approve_as_is_allowed": False,
            }
        }
        result = UserStoryService._resolve_source_code_story_status(metadata, "U.S 1.1.1")
        assert result == UserStoryStatus.NEEDS_EDIT.value

    def test_flagged_with_regenerate_returns_failed(self):
        metadata = {
            "review_guidance": {
                "flagged_story_ids": ["U.S 1.1.1"],
                "recommended_action": "REGENERATE",
                "approve_as_is_allowed": False,
            }
        }
        result = UserStoryService._resolve_source_code_story_status(metadata, "U.S 1.1.1")
        assert result == UserStoryStatus.FAILED.value


class TestNormalizeBacklogUserStoryCodes:
    def test_non_dict_payload_passthrough(self):
        assert UserStoryService._normalize_backlog_user_story_codes("not a dict") == "not a dict"

    def test_no_epics_passthrough(self):
        payload = {"foo": "bar"}
        assert UserStoryService._normalize_backlog_user_story_codes(payload) == payload

    def test_valid_code_left_unchanged(self):
        payload = {"epics": [{"stories": [{"user_story_code": "U.S 1.1.1"}]}]}
        result = UserStoryService._normalize_backlog_user_story_codes(payload)
        assert result["epics"][0]["stories"][0]["user_story_code"] == "U.S 1.1.1"
        assert result["epics"][0]["parent_br"] == []

    def test_legacy_code_converted(self):
        payload = {"epics": [{"stories": [{"user_story_code": "MOD-2-F3-S4"}]}]}
        result = UserStoryService._normalize_backlog_user_story_codes(payload)
        assert result["epics"][0]["stories"][0]["user_story_code"] == "U.S 2.3.4"

    def test_unrecognized_code_falls_back_to_positional(self):
        payload = {"epics": [{"stories": [{"user_story_code": "garbage"}]}]}
        result = UserStoryService._normalize_backlog_user_story_codes(payload)
        assert result["epics"][0]["stories"][0]["user_story_code"] == "U.S 1.1.1"

    def test_non_list_stories_skipped(self):
        payload = {"epics": [{"stories": "not a list"}]}
        result = UserStoryService._normalize_backlog_user_story_codes(payload)
        assert result["epics"][0]["stories"] == "not a list"
