"""Unit tests for ModuleFeatureService."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch
import uuid

import pytest

from app.core.enums.activity_type import ActivityType
from app.core.enums.notification_type import NotificationType
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.messages import (
    MSG_ACTIVITY_MODULE_FEATURE_APPROVED,
    SUMMARY_ACTIVITY_MODULE_FEATURE_APPROVED,
)
from app.models.neo4j.fragment_model import (
    FragmentBBoxCoordinatesModel,
    FragmentBBoxModel,
    FragmentModel,
)
from app.models.neo4j.module_feature_model import ChangeType, FeatureModel, ModuleModel
from app.models.neo4j.version_model import FeatureVersionModel, ModuleVersionModel
from app.schemas.module_feature_schema import (
    FeatureFeedbackItem,
    ModuleFeatureStatusChangeRequest,
    ModuleFeatureStatusEnum,
    SyncFlagsUpdateRequest,
)
from app.services.module_feature_service import ModuleFeatureService
from tests.conftest import make_source


@pytest.fixture(autouse=True)
def _mock_approval_notify(request):
    """Prevent the module/feature-approved notify path from opening a real
    UnitOfWork or hitting Redis. ``record_activity``/``publish_notification``
    are imported lazily inside ``_notify_module_feature_approved``, so they
    must be patched at their definition module, not at
    ``app.services.module_feature_service``.
    """
    with (
        patch("app.services.activity_log_service.record_activity") as mock_record,
        patch("app.services.notification_service.publish_notification") as mock_publish,
    ):
        yield SimpleNamespace(record_activity=mock_record, publish_notification=mock_publish)


class TestModuleFeatureService:
    @pytest.mark.asyncio
    async def test_get_feature_by_module_success(self, uow):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.get_feature_for_module = AsyncMock(
            return_value=FeatureModel(
                id="feature_1",
                module_id="module_1",
                name="Feature 1",
                description="Feature description",
                status="ready",
                fea_code="FEA_001",
            )
        )
        repository.get_module_for_project = AsyncMock(
            return_value=ModuleModel(
                id="module_1",
                name="Module 1",
                description="Module description",
                status="ready",
                mod_code="MOD_001",
                features=[],
            )
        )
        repository.get_latest_feature_version = AsyncMock(return_value=None)

        response = await ModuleFeatureService(repository).get_feature_by_module(
            project_id=project_id,
            module_id="module_1",
            feature_id="feature_1",
            uow=uow,
        )

        assert response.project_id == project_id
        assert response.module_id == "module_1"
        assert response.mod_code == "MOD_001"
        assert response.feature.id == "feature_1"
        assert response.feature.fea_code == "FEA_001"
        assert response.feature.updated_fields == []
        repository.get_feature_for_module.assert_awaited_once_with(
            project_id, "module_1", "feature_1"
        )
        repository.get_module_for_project.assert_awaited_once_with(project_id, "module_1")

    @pytest.mark.asyncio
    async def test_get_feature_by_module_includes_deleted_at(self, uow):
        repository = MagicMock()
        project_id = uuid.uuid4()
        deleted_at = datetime.now(UTC)
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.get_feature_for_module = AsyncMock(
            return_value=FeatureModel(
                id="feature_1",
                module_id="module_1",
                name="Feature 1",
                description="Feature description",
                status="approved",
                fea_code="FEA_001",
                deleted_at=deleted_at,
            )
        )
        repository.get_module_for_project = AsyncMock(
            return_value=ModuleModel(
                id="module_1",
                name="Module 1",
                description="Module description",
                status="ready",
                mod_code="MOD_001",
                features=[],
            )
        )
        repository.get_latest_feature_version = AsyncMock(return_value=None)

        response = await ModuleFeatureService(repository).get_feature_by_module(
            project_id=project_id,
            module_id="module_1",
            feature_id="feature_1",
            uow=uow,
        )

        assert response.feature.deleted_at == deleted_at

    @pytest.mark.asyncio
    async def test_get_feature_by_module_updated_fields_reflects_diff_from_last_version(
        self, uow
    ):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.get_feature_for_module = AsyncMock(
            return_value=FeatureModel(
                id="feature_1",
                module_id="module_1",
                name="Feature 1 renamed",
                description="Feature description",
                status="ready",
                fea_code="FEA_001",
                is_jira_synced=True,
            )
        )
        repository.get_module_for_project = AsyncMock(
            return_value=ModuleModel(
                id="module_1",
                name="Module 1",
                description="Module description",
                status="ready",
                mod_code="MOD_001",
                features=[],
            )
        )
        repository.get_latest_feature_version = AsyncMock(
            return_value=FeatureVersionModel(
                id="version_1",
                feature_id="feature_1",
                name="Feature 1",
                description="Feature description",
                status="ready",
                fea_code="FEA_001",
                is_jira_synced=False,
            )
        )

        response = await ModuleFeatureService(repository).get_feature_by_module(
            project_id=project_id,
            module_id="module_1",
            feature_id="feature_1",
            uow=uow,
        )

        assert response.feature.last_previous_items is not None
        # is_jira_synced differs too, but sync flags aren't tracked as content edits.
        assert set(response.feature.updated_fields) == {"name"}

    @pytest.mark.asyncio
    async def test_get_feature_by_module_updated_fields_excludes_change_type(self, uow):
        """incremental_change_type/feedback_change_type are metadata, not user-facing
        content edits — they must never appear in ``updated_fields`` even when they
        differ from the last snapshot."""
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.get_feature_for_module = AsyncMock(
            return_value=FeatureModel(
                id="feature_1",
                module_id="module_1",
                name="Feature 1",
                description="Feature description",
                status="ready",
                fea_code="FEA_001",
                incremental_change_type="added",
                feedback_change_type="updated",
            )
        )
        repository.get_module_for_project = AsyncMock(
            return_value=ModuleModel(
                id="module_1",
                name="Module 1",
                description="Module description",
                status="ready",
                mod_code="MOD_001",
                features=[],
            )
        )
        repository.get_latest_feature_version = AsyncMock(
            return_value=FeatureVersionModel(
                id="version_1",
                feature_id="feature_1",
                name="Feature 1",
                description="Feature description",
                status="ready",
                fea_code="FEA_001",
                incremental_change_type=None,
                feedback_change_type=None,
            )
        )

        response = await ModuleFeatureService(repository).get_feature_by_module(
            project_id=project_id,
            module_id="module_1",
            feature_id="feature_1",
            uow=uow,
        )

        assert response.feature.updated_fields == []

    @pytest.mark.asyncio
    async def test_get_feature_by_module_updated_fields_empty_when_unchanged(self, uow):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.get_feature_for_module = AsyncMock(
            return_value=FeatureModel(
                id="feature_1",
                module_id="module_1",
                name="Feature 1",
                description="Feature description",
                status="ready",
                fea_code="FEA_001",
            )
        )
        repository.get_module_for_project = AsyncMock(
            return_value=ModuleModel(
                id="module_1",
                name="Module 1",
                description="Module description",
                status="ready",
                mod_code="MOD_001",
                features=[],
            )
        )
        repository.get_latest_feature_version = AsyncMock(
            return_value=FeatureVersionModel(
                id="version_1",
                feature_id="feature_1",
                name="Feature 1",
                description="Feature description",
                status="ready",
                fea_code="FEA_001",
            )
        )

        response = await ModuleFeatureService(repository).get_feature_by_module(
            project_id=project_id,
            module_id="module_1",
            feature_id="feature_1",
            uow=uow,
        )

        assert response.feature.updated_fields == []

    @pytest.mark.asyncio
    async def test_get_feature_by_module_not_found(self, uow):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.get_feature_for_module = AsyncMock(return_value=None)

        with pytest.raises(NotFoundError):
            await ModuleFeatureService(repository).get_feature_by_module(
                project_id=project_id,
                module_id="module_1",
                feature_id="missing_feature",
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_get_feature_by_module_mod_code_defaults_to_none_when_module_lookup_misses(
        self, uow
    ):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.get_feature_for_module = AsyncMock(
            return_value=FeatureModel(
                id="feature_1",
                module_id="module_1",
                name="Feature 1",
                description="Feature description",
                status="ready",
                fea_code="FEA_001",
            )
        )
        repository.get_module_for_project = AsyncMock(return_value=None)
        repository.get_latest_feature_version = AsyncMock(return_value=None)

        response = await ModuleFeatureService(repository).get_feature_by_module(
            project_id=project_id,
            module_id="module_1",
            feature_id="feature_1",
            uow=uow,
        )

        assert response.mod_code is None
        assert response.feature.id == "feature_1"

    @pytest.mark.asyncio
    async def test_list_modules_for_project_success(self, uow):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.list_modules_by_project = AsyncMock(
            return_value=[
                ModuleModel(
                    id="module_1",
                    name="Module 1",
                    description="Module description",
                    status="ready",
                    mod_code="MOD_001",
                    features=[
                        FeatureModel(
                            id="feature_11",
                            module_id="module_1",
                            name="Feature 11",
                            description="Feature description",
                            status="ready",
                            fea_code="FEA_001",
                            sources=[
                                {
                                    "source_id": "3f8fc943-4b4b-493f-8d45-18acc34f2870",
                                    "pages": [
                                        {
                                            "page": 3,
                                            "bboxes": [
                                                {
                                                    "fragment_id": "aa4cbe33-f3c0-4e53-8dd2-080974de148c",
                                                    "bbox": {
                                                        "x": 108.02,
                                                        "y": 525.52,
                                                        "w": 243.01,
                                                        "h": 55.68,
                                                    },
                                                }
                                            ],
                                        }
                                    ],
                                }
                            ],
                        )
                    ],
                )
            ]
        )

        response = await ModuleFeatureService(repository).list_modules_for_project(
            project_id=project_id,
            uow=uow,
            skip=0,
            limit=20,
        )

        assert response.total == 1
        assert response.skip == 0
        assert response.limit == 20
        assert response.items[0].id == "module_1"
        assert response.items[0].features[0].id == "feature_11"
        assert [item.model_dump() for item in response.items[0].features[0].sources] == [
            {
                "source_id": "3f8fc943-4b4b-493f-8d45-18acc34f2870",
                "pages": [
                    {
                        "page": 3,
                        "bboxes": [
                            {
                                "fragment_id": "aa4cbe33-f3c0-4e53-8dd2-080974de148c",
                                "bbox": {
                                    "x": 108.02,
                                    "y": 525.52,
                                    "w": 243.01,
                                    "h": 55.68,
                                },
                            }
                        ],
                    }
                ],
            }
        ]

    @pytest.mark.asyncio
    async def test_list_modules_for_project_includes_deleted_at(self, uow):
        repository = MagicMock()
        project_id = uuid.uuid4()
        deleted_at = datetime.now(UTC)
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.list_modules_by_project = AsyncMock(
            return_value=[
                ModuleModel(
                    id="module_1",
                    name="Module 1",
                    description="Module description",
                    status="approved",
                    mod_code="MOD_001",
                    deleted_at=deleted_at,
                    features=[
                        FeatureModel(
                            id="feature_11",
                            module_id="module_1",
                            name="Feature 11",
                            description="Feature description",
                            status="approved",
                            fea_code="FEA_001",
                            deleted_at=deleted_at,
                        )
                    ],
                )
            ]
        )

        response = await ModuleFeatureService(repository).list_modules_for_project(
            project_id=project_id,
            uow=uow,
            skip=0,
            limit=20,
        )

        assert response.items[0].deleted_at == deleted_at
        assert response.items[0].features[0].deleted_at == deleted_at

    @pytest.mark.asyncio
    async def test_list_modules_tree_for_project_includes_mfu_id(self, uow):
        """Regression test: the tree endpoint used to omit ``mfu_id`` entirely
        (FeatureTreeNode had no such field), even though FeatureResponse (the
        non-tree endpoint) already returned it."""
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.list_modules_by_project = AsyncMock(
            return_value=[
                ModuleModel(
                    id="module_1",
                    name="Module 1",
                    description="Module description",
                    status="draft",
                    mod_code="MOD-ANCES",
                    features=[
                        FeatureModel(
                            id="feature_11",
                            module_id="module_1",
                            name="Feature 11",
                            description="Feature description",
                            status="draft",
                            fea_code="ANCES-001-F1",
                            mfu_id="MFU-001",
                        )
                    ],
                )
            ]
        )

        response = await ModuleFeatureService(repository).list_modules_tree_for_project(
            project_id=project_id,
            uow=uow,
        )

        assert response.items[0].children[0].mfu_id == "MFU-001"

    @pytest.mark.asyncio
    async def test_list_modules_tree_for_project_forwards_source_ingestion_id_filter(self, uow):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.list_modules_by_project = AsyncMock(return_value=[])

        await ModuleFeatureService(repository).list_modules_tree_for_project(
            project_id=project_id,
            uow=uow,
            source_ingestion_id="ingestion-1",
        )

        repository.list_modules_by_project.assert_awaited_once_with(
            project_id, source_ingestion_id="ingestion-1"
        )

    def test_normalize_feature_sources_accepts_json_string(self):
        raw_sources = """[
            {
                "source_id": "3f8fc943-4b4b-493f-8d45-18acc34f2870",
                "pages": [
                    {
                        "page": "3",
                        "bboxes": [
                            {
                                "fragment_id": "aa4cbe33-f3c0-4e53-8dd2-080974de148c",
                                "bbox": {"x": 108.02, "y": 525.52, "w": 243.01, "h": 55.68}
                            }
                        ]
                    }
                ]
            }
        ]"""

        normalized = ModuleFeatureService._normalize_feature_sources(raw_sources)

        assert normalized == [
            {
                "source_id": "3f8fc943-4b4b-493f-8d45-18acc34f2870",
                "pages": [
                    {
                        "page": 3,
                        "bboxes": [
                            {
                                "fragment_id": "aa4cbe33-f3c0-4e53-8dd2-080974de148c",
                                "bbox": {
                                    "x": 108.02,
                                    "y": 525.52,
                                    "w": 243.01,
                                    "h": 55.68,
                                },
                            }
                        ],
                    }
                ],
            }
        ]

    @pytest.mark.asyncio
    async def test_enqueue_module_feature_regeneration_success(self, uow):
        source = make_source(is_deleted=False)
        uow.projects.get_by_uuid.return_value = MagicMock()
        uow.sources.get_by_uuid.return_value = source
        uow.sources.get_ids_by_project.return_value = [source.id]
        repository = MagicMock()
        repository.list_modules_by_project = AsyncMock(
            return_value=[
                ModuleModel(
                    id="module_1",
                    name="Module 1",
                    description="Module description",
                    status="draft",
                    mod_code="MOD_001",
                    features=[
                        FeatureModel(
                            id="feature_1",
                            module_id="module_1",
                            fea_code="FEA_001",
                            name="Feature 1",
                            description="Feature description",
                            status="draft",
                        )
                    ],
                )
            ]
        )
        fragment = FragmentModel(
            id="fragment_1",
            source_id=source.id,
            frag_type="paragraph",
            content="Authentication requirements",
            bbox=[
                FragmentBBoxModel(
                    page=1,
                    bbox=FragmentBBoxCoordinatesModel(x=1.0, y=2.0, w=3.0, h=4.0),
                    confidence=0.9,
                )
            ],
            content_hash="hash",
        )

        with (
            patch(
                "app.services.fragment_service.FragmentService.list_fragments",
                new=AsyncMock(return_value=SimpleNamespace(fragments=[fragment])),
            ),
            patch(
                "app.workers.document_task.regenerate_modules_and_features_task.apply_async",
                return_value=SimpleNamespace(id="task-123"),
            ) as mock_apply_async,
        ):
            response = await ModuleFeatureService(repository).enqueue_module_feature_regeneration(
                project_id=source.project_id,
                feedback="Refine module grouping",
                uow=uow,
            )

        assert response.task_id  # UUID string assigned from ProjectTask row
        assert len(response.task_id) == 36  # UUID format
        assert response.source_ids == [source.id]
        assert response.project_id == source.project_id
        assert response.status == "queued"
        mock_apply_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_enqueue_module_feature_regeneration_project_not_found(self, uow):
        repository = MagicMock()
        uow.projects.get_by_uuid.return_value = None

        with pytest.raises(NotFoundError):
            await ModuleFeatureService(repository).enqueue_module_feature_regeneration(
                project_id=uuid.uuid4(),
                feedback="Refine module grouping",
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_enqueue_module_feature_regeneration_no_sources_creates_standalone_ingestion(
        self, uow
    ):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        uow.sources.get_ids_by_project.return_value = []
        repository.list_modules_by_project = AsyncMock(return_value=[])

        with (
            patch(
                "app.services.project_graph_service.ProjectGraphService.get_project_metadata",
                return_value={"business_requirements": [], "exclusions": []},
            ),
            patch(
                "app.workers.document_task.regenerate_modules_and_features_task.apply_async",
                return_value=SimpleNamespace(id="task-123"),
            ) as mock_apply_async,
        ):
            response = await ModuleFeatureService(repository).enqueue_module_feature_regeneration(
                project_id=project_id,
                feedback="Refine module grouping",
                module_ids=["MOD-1"],
                uow=uow,
            )

        assert response.source_ids == []
        mock_apply_async.assert_called_once()
        uow.source_ingestions.create_ingestion.assert_called_once_with(
            project_id=project_id,
            source_type="requirement_update",
            status="running",
            stages=["generating_requirements"],
            entity_json={"feedback": "Refine module grouping", "module_ids": ["MOD-1"]},
            started_at=ANY,
        )

    @pytest.mark.asyncio
    async def test_enqueue_feature_regeneration_tags_generating_requirements_stage(self, uow):
        """Regression test: source-code feedback regeneration (the
        /user-stories/regenerate-for-source-code endpoint) doesn't split into
        separate module/feature vs. user-story phases — like the sibling RFP
        feedback flows (`enqueue_module_feature_regeneration`,
        `enqueue_user_story_regeneration_by_feedback`), it must tag the
        collapsed GENERATING_REQUIREMENTS stage, not the two-phase RFP
        GENERATING_MODULE_FEATURE/GENERATING_USER_STORY stages.
        """
        project_id = uuid.uuid4()
        uow.source_ingestions.list_running_by_project.return_value = []
        uow.source_ingestions.get_earliest_by_project.return_value = None
        repository = MagicMock()

        feedback_items = [
            FeatureFeedbackItem(
                mod_code="MOD-ANCES",
                mfu_id="MFU-001",
                overall_feedback="Tighten the validation rule.",
            )
        ]

        with (
            patch(
                "app.services.source_code_metadata_service.SourceCodeMetadataService.get_for_module",
                new=AsyncMock(
                    return_value=SimpleNamespace(module_response={}, module_manifest={})
                ),
            ),
            patch(
                "app.workers.source_code_task.regenerate_feature_mfu_task.apply_async",
                return_value=SimpleNamespace(id="task-123"),
            ) as mock_apply_async,
        ):
            response = await ModuleFeatureService(repository).enqueue_feature_regeneration(
                project_id=project_id,
                feedback_items=feedback_items,
                uow=uow,
            )

        assert response.project_id == project_id
        mock_apply_async.assert_called_once()
        uow.source_ingestions.create_ingestion.assert_called_once_with(
            project_id=project_id,
            source_type="requirement_update",
            status="running",
            stages=["generating_requirements"],
            entity_json=ANY,
            started_at=ANY,
        )

    @pytest.mark.asyncio
    async def test_change_module_feature_status_for_project_approved_triggers_user_story_regeneration(
        self, uow
    ):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        uow.sources.get_ids_by_project.return_value = [uuid.uuid4()]
        uow.source_ingestions.list_running_by_project.return_value = []
        repository.count_pending_feedback_changes_by_project = AsyncMock(return_value=0)
        repository.change_module_feature_status_for_project = AsyncMock(
            return_value=ModuleModel(
                id="module_1",
                project_id=project_id,
                name="Module 1",
                description=None,
                status="approved",
                mod_code="MOD_001",
                features=[],
            )
        )
        repository.count_modules_and_features_for_project = AsyncMock(return_value=(1, 3))

        with patch(
            "app.services.user_story_service.UserStoryService.enqueue_user_story_generation",
            new=AsyncMock(return_value=SimpleNamespace(task_id="task-regen-123", status="queued")),
        ) as mock_enqueue:
            response = await ModuleFeatureService(
                repository
            ).change_module_feature_status_for_project(
                project_id=project_id,
                payload=ModuleFeatureStatusChangeRequest(status=ModuleFeatureStatusEnum.APPROVED),
                uow=uow,
            )

        assert response.project_id == project_id
        assert response.task_id == "task-regen-123"
        mock_enqueue.assert_awaited_once_with(
            project_id=project_id,
            uow=uow,
            user_id=None,
        )

    @pytest.mark.asyncio
    async def test_change_module_feature_status_for_project_approved_records_activity_and_notifies_owner_and_members(
        self, uow, _mock_approval_notify
    ):
        repository = MagicMock()
        project_id = uuid.uuid4()
        owner_id = uuid.uuid4()
        member_id = uuid.uuid4()
        actor_id = uuid.uuid4()
        project = SimpleNamespace(owner_id=owner_id, name="My Project")
        uow.projects.get_by_uuid.return_value = project
        uow.sources.get_ids_by_project.return_value = [uuid.uuid4()]
        uow.source_ingestions.list_running_by_project.return_value = []
        # The owner also holds a membership row — must be notified only once.
        uow.project_members.list_by_project.return_value = [
            SimpleNamespace(user_id=member_id),
            SimpleNamespace(user_id=owner_id),
        ]
        repository.count_pending_feedback_changes_by_project = AsyncMock(return_value=0)
        repository.change_module_feature_status_for_project = AsyncMock(
            return_value=ModuleModel(
                id="module_1",
                project_id=project_id,
                name="Module 1",
                description=None,
                status="approved",
                mod_code="MOD_001",
                features=[],
            )
        )
        repository.count_modules_and_features_for_project = AsyncMock(return_value=(2, 5))

        with patch(
            "app.services.user_story_service.UserStoryService.enqueue_user_story_generation",
            new=AsyncMock(return_value=SimpleNamespace(task_id="task-regen-125", status="queued")),
        ):
            await ModuleFeatureService(repository).change_module_feature_status_for_project(
                project_id=project_id,
                payload=ModuleFeatureStatusChangeRequest(status=ModuleFeatureStatusEnum.APPROVED),
                uow=uow,
                user_id=actor_id,
            )

        _mock_approval_notify.record_activity.assert_called_once_with(
            project_id=project_id,
            activity_type=ActivityType.MODULE_FEATURE_APPROVED,
            summary=SUMMARY_ACTIVITY_MODULE_FEATURE_APPROVED,
            message=MSG_ACTIVITY_MODULE_FEATURE_APPROVED.format(total_modules=2, total_features=5),
            actor_user_id=actor_id,
            data={"total_modules": 2, "total_features": 5},
        )

        notified_user_ids = {
            call.kwargs["user_id"]
            for call in _mock_approval_notify.publish_notification.call_args_list
        }
        assert notified_user_ids == {owner_id, member_id}
        assert _mock_approval_notify.publish_notification.call_count == 2
        for call in _mock_approval_notify.publish_notification.call_args_list:
            assert call.kwargs["message"] == (
                '2 module(s) and 5 feature(s) approved in "My Project".'
            )
            assert call.kwargs["notification_type"] == NotificationType.SUCCESS

    @pytest.mark.asyncio
    async def test_change_module_feature_status_for_project_approved_forwards_skip_processing_true(
        self, uow
    ):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        uow.sources.get_ids_by_project.return_value = [uuid.uuid4()]
        uow.source_ingestions.list_running_by_project.return_value = []
        repository.count_pending_feedback_changes_by_project = AsyncMock(return_value=0)
        repository.change_module_feature_status_for_project = AsyncMock(
            return_value=ModuleModel(
                id="module_1",
                project_id=project_id,
                name="Module 1",
                description=None,
                status="approved",
                mod_code="MOD_001",
                features=[],
            )
        )
        repository.count_modules_and_features_for_project = AsyncMock(return_value=(1, 3))

        with patch(
            "app.services.user_story_service.UserStoryService.enqueue_user_story_generation",
            new=AsyncMock(return_value=SimpleNamespace(task_id="task-regen-124", status="queued")),
        ) as mock_enqueue:
            response = await ModuleFeatureService(
                repository
            ).change_module_feature_status_for_project(
                project_id=project_id,
                payload=ModuleFeatureStatusChangeRequest(
                    status=ModuleFeatureStatusEnum.APPROVED,
                    skip_processing=True,
                ),
                uow=uow,
            )

        assert response.project_id == project_id
        assert response.task_id == "task-regen-124"
        mock_enqueue.assert_awaited_once_with(
            project_id=project_id,
            uow=uow,
            user_id=None,
            skip_processing=True,
        )

    @pytest.mark.asyncio
    async def test_change_module_feature_status_for_project_approved_no_sources_raises(self, uow):
        """Approving modules without any uploaded sources must fail BEFORE the Neo4j write."""
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        uow.sources.get_ids_by_project.return_value = []  # no sources
        uow.source_ingestions.list_running_by_project.return_value = []

        with pytest.raises(NotFoundError):
            await ModuleFeatureService(repository).change_module_feature_status_for_project(
                project_id=project_id,
                payload=ModuleFeatureStatusChangeRequest(status=ModuleFeatureStatusEnum.APPROVED),
                uow=uow,
            )

        # Neo4j must NOT have been touched
        repository.change_module_feature_status_for_project.assert_not_called()

    @pytest.mark.asyncio
    async def test_change_module_feature_status_for_project_approved_pending_feedback_change_raises(
        self, uow
    ):
        """Approving while a module/feature still has an unresolved feedback-driven
        change (ADDED/UPDATED/DELETE_SUGGESTED) must fail BEFORE the Neo4j write —
        that content is awaiting accept/reject via /feedback-updates."""
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        uow.sources.get_ids_by_project.return_value = [uuid.uuid4()]
        uow.source_ingestions.list_running_by_project.return_value = []
        repository.count_pending_feedback_changes_by_project = AsyncMock(return_value=2)

        with pytest.raises(ConflictError):
            await ModuleFeatureService(repository).change_module_feature_status_for_project(
                project_id=project_id,
                payload=ModuleFeatureStatusChangeRequest(status=ModuleFeatureStatusEnum.APPROVED),
                uow=uow,
            )

        repository.count_pending_feedback_changes_by_project.assert_awaited_once_with(project_id)
        # Neo4j must NOT have been touched
        repository.change_module_feature_status_for_project.assert_not_called()

    @pytest.mark.asyncio
    async def test_change_module_feature_status_for_project_non_approved_does_not_trigger_user_story_regeneration(
        self, uow, _mock_approval_notify
    ):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        uow.source_ingestions.list_running_by_project.return_value = []
        repository.change_module_feature_status_for_project = AsyncMock(
            return_value=ModuleModel(
                id="module_1",
                project_id=project_id,
                name="Module 1",
                description=None,
                status="failed",
                mod_code="MOD_001",
                features=[],
            )
        )

        with patch(
            "app.services.user_story_service.UserStoryService.enqueue_user_story_regeneration",
            new=AsyncMock(),
        ) as mock_enqueue:
            response = await ModuleFeatureService(
                repository
            ).change_module_feature_status_for_project(
                project_id=project_id,
                payload=ModuleFeatureStatusChangeRequest(status=ModuleFeatureStatusEnum.FAILED),
                uow=uow,
            )

        assert response.status == "failed"
        mock_enqueue.assert_not_awaited()
        _mock_approval_notify.record_activity.assert_not_called()
        _mock_approval_notify.publish_notification.assert_not_called()
        repository.count_modules_and_features_for_project.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_module_by_project_success(self, uow):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.get_module_for_project = AsyncMock(
            return_value=ModuleModel(
                id="module_1",
                name="Module 1",
                description="desc",
                status="ready",
                mod_code="MOD_001",
                features=[],
            )
        )
        repository.get_latest_module_version = AsyncMock(return_value=None)

        response = await ModuleFeatureService(repository).get_module_by_project(
            project_id=project_id, module_id="module_1", uow=uow
        )

        assert response.project_id == project_id
        assert response.module.id == "module_1"

    @pytest.mark.asyncio
    async def test_get_module_by_project_includes_deleted_at(self, uow):
        repository = MagicMock()
        project_id = uuid.uuid4()
        deleted_at = datetime.now(UTC)
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.get_module_for_project = AsyncMock(
            return_value=ModuleModel(
                id="module_1",
                name="Module 1",
                description="desc",
                status="approved",
                mod_code="MOD_001",
                features=[],
                deleted_at=deleted_at,
            )
        )
        repository.get_latest_module_version = AsyncMock(return_value=None)

        response = await ModuleFeatureService(repository).get_module_by_project(
            project_id=project_id, module_id="module_1", uow=uow
        )

        assert response.module.deleted_at == deleted_at

    @pytest.mark.asyncio
    async def test_get_module_by_project_updated_fields_reflects_diff_from_last_version(
        self, uow
    ):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.get_module_for_project = AsyncMock(
            return_value=ModuleModel(
                id="module_1",
                name="Module 1 renamed",
                description="desc",
                status="ready",
                mod_code="MOD_001",
                features=[],
                is_jira_synced=True,
            )
        )
        repository.get_latest_module_version = AsyncMock(
            return_value=ModuleVersionModel(
                id="version_1",
                module_id="module_1",
                name="Module 1",
                description="desc",
                status="ready",
                mod_code="MOD_001",
                is_jira_synced=False,
            )
        )

        response = await ModuleFeatureService(repository).get_module_by_project(
            project_id=project_id, module_id="module_1", uow=uow
        )

        assert response.module.last_previous_items is not None
        # is_jira_synced differs too, but sync flags aren't tracked as content edits.
        assert set(response.module.updated_fields) == {"name"}

    @pytest.mark.asyncio
    async def test_get_module_by_project_updated_fields_excludes_change_type(self, uow):
        """incremental_change_type/feedback_change_type are metadata, not user-facing
        content edits — they must never appear in ``updated_fields`` even when they
        differ from the last snapshot."""
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.get_module_for_project = AsyncMock(
            return_value=ModuleModel(
                id="module_1",
                name="Module 1",
                description="desc",
                status="ready",
                mod_code="MOD_001",
                features=[],
                incremental_change_type="added",
                feedback_change_type="updated",
            )
        )
        repository.get_latest_module_version = AsyncMock(
            return_value=ModuleVersionModel(
                id="version_1",
                module_id="module_1",
                name="Module 1",
                description="desc",
                status="ready",
                mod_code="MOD_001",
                incremental_change_type=None,
                feedback_change_type=None,
            )
        )

        response = await ModuleFeatureService(repository).get_module_by_project(
            project_id=project_id, module_id="module_1", uow=uow
        )

        assert response.module.updated_fields == []

    @pytest.mark.asyncio
    async def test_get_module_by_project_updated_fields_empty_when_unchanged(self, uow):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.get_module_for_project = AsyncMock(
            return_value=ModuleModel(
                id="module_1",
                name="Module 1",
                description="desc",
                status="ready",
                mod_code="MOD_001",
                features=[],
            )
        )
        repository.get_latest_module_version = AsyncMock(
            return_value=ModuleVersionModel(
                id="version_1",
                module_id="module_1",
                name="Module 1",
                description="desc",
                status="ready",
                mod_code="MOD_001",
            )
        )

        response = await ModuleFeatureService(repository).get_module_by_project(
            project_id=project_id, module_id="module_1", uow=uow
        )

        assert response.module.updated_fields == []

    @pytest.mark.asyncio
    async def test_get_module_by_project_not_found(self, uow):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.get_module_for_project = AsyncMock(return_value=None)

        with pytest.raises(NotFoundError):
            await ModuleFeatureService(repository).get_module_by_project(
                project_id=project_id, module_id="missing", uow=uow
            )

    @pytest.mark.asyncio
    async def test_get_module_by_project_project_not_found(self, uow):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = None

        with pytest.raises(NotFoundError):
            await ModuleFeatureService(repository).get_module_by_project(
                project_id=project_id, module_id="module_1", uow=uow
            )

    @pytest.mark.asyncio
    async def test_update_module_sync_flags_success(self, uow):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.update_module_sync_flags = AsyncMock(
            return_value=ModuleModel(
                id="module_1",
                name="Module 1",
                description="desc",
                status="ready",
                mod_code="MOD_001",
                features=[],
                is_jira_synced=True,
            )
        )

        response = await ModuleFeatureService(repository).update_module_sync_flags(
            project_id=project_id,
            module_id="module_1",
            payload=SyncFlagsUpdateRequest(is_jira_synced=True),
            uow=uow,
        )

        assert response.module.id == "module_1"
        repository.update_module_sync_flags.assert_awaited_once_with(
            project_id, "module_1", is_jira_synced=True, is_tap_synced=None
        )

    @pytest.mark.asyncio
    async def test_update_module_sync_flags_project_not_found(self, uow):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = None

        with pytest.raises(NotFoundError):
            await ModuleFeatureService(repository).update_module_sync_flags(
                project_id=project_id,
                module_id="module_1",
                payload=SyncFlagsUpdateRequest(is_jira_synced=True),
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_update_module_sync_flags_module_not_found(self, uow):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.update_module_sync_flags = AsyncMock(return_value=None)

        with pytest.raises(NotFoundError):
            await ModuleFeatureService(repository).update_module_sync_flags(
                project_id=project_id,
                module_id="missing",
                payload=SyncFlagsUpdateRequest(is_jira_synced=True),
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_update_feature_sync_flags_success(self, uow):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.update_feature_sync_flags = AsyncMock(
            return_value=FeatureModel(
                id="feature_1",
                module_id="module_1",
                name="Feature 1",
                description="desc",
                status="ready",
                fea_code="FEA_001",
                is_tap_synced=True,
            )
        )

        response = await ModuleFeatureService(repository).update_feature_sync_flags(
            project_id=project_id,
            module_id="module_1",
            feature_id="feature_1",
            payload=SyncFlagsUpdateRequest(is_tap_synced=True),
            uow=uow,
        )

        assert response.feature.id == "feature_1"

    @pytest.mark.asyncio
    async def test_update_feature_sync_flags_project_not_found(self, uow):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = None

        with pytest.raises(NotFoundError):
            await ModuleFeatureService(repository).update_feature_sync_flags(
                project_id=project_id,
                module_id="module_1",
                feature_id="feature_1",
                payload=SyncFlagsUpdateRequest(is_tap_synced=True),
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_update_feature_sync_flags_feature_not_found(self, uow):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.update_feature_sync_flags = AsyncMock(return_value=None)

        with pytest.raises(NotFoundError):
            await ModuleFeatureService(repository).update_feature_sync_flags(
                project_id=project_id,
                module_id="module_1",
                feature_id="missing",
                payload=SyncFlagsUpdateRequest(is_tap_synced=True),
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_delete_module_hard_deletes_when_not_approved(self, uow):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.get_module_for_project = AsyncMock(
            return_value=ModuleModel(
                id="module_1",
                name="Module 1",
                description="desc",
                status="ready",
                mod_code="MOD_001",
                features=[],
            )
        )
        repository.hard_delete_module_cascade = AsyncMock(return_value=True)

        result = await ModuleFeatureService(repository).delete_module(
            project_id=project_id, module_id="module_1", uow=uow
        )

        assert result == {"is_deleted": True, "deletion_reason": None, "deleted_at": None}
        repository.hard_delete_module_cascade.assert_awaited_once_with(
            project_id=project_id, module_id="module_1"
        )
        repository.soft_delete_module_cascade.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_module_soft_deletes_when_approved_with_reason(self, uow):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.get_module_for_project = AsyncMock(
            return_value=ModuleModel(
                id="module_1",
                name="Module 1",
                description="desc",
                status="approved",
                mod_code="MOD_001",
                features=[],
            )
        )
        repository.soft_delete_module_cascade = AsyncMock(return_value=True)

        result = await ModuleFeatureService(repository).delete_module(
            project_id=project_id, module_id="module_1", uow=uow, reason="No longer needed"
        )

        assert result["is_deleted"] is True
        assert result["deletion_reason"] == "No longer needed"
        assert result["deleted_at"] is not None
        repository.soft_delete_module_cascade.assert_awaited_once_with(
            project_id=project_id, module_id="module_1", reason="No longer needed"
        )
        repository.hard_delete_module_cascade.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_module_approved_without_reason_raises_validation_error(self, uow):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.get_module_for_project = AsyncMock(
            return_value=ModuleModel(
                id="module_1",
                name="Module 1",
                description="desc",
                status="approved",
                mod_code="MOD_001",
                features=[],
            )
        )

        with pytest.raises(ValidationError):
            await ModuleFeatureService(repository).delete_module(
                project_id=project_id, module_id="module_1", uow=uow
            )
        repository.soft_delete_module_cascade.assert_not_called()
        repository.hard_delete_module_cascade.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_module_project_not_found(self, uow):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = None

        with pytest.raises(NotFoundError):
            await ModuleFeatureService(repository).delete_module(
                project_id=project_id, module_id="module_1", uow=uow
            )

    @pytest.mark.asyncio
    async def test_delete_module_not_found(self, uow):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.get_module_for_project = AsyncMock(return_value=None)

        with pytest.raises(NotFoundError):
            await ModuleFeatureService(repository).delete_module(
                project_id=project_id, module_id="missing", uow=uow
            )

    @pytest.mark.asyncio
    async def test_delete_feature_hard_deletes_when_not_approved(self, uow):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.get_feature_for_module = AsyncMock(
            return_value=FeatureModel(
                id="feature_1",
                module_id="module_1",
                name="Feature 1",
                description="desc",
                status="ready",
                fea_code="FEA_001",
            )
        )
        repository.hard_delete_feature_cascade = AsyncMock(return_value=True)

        result = await ModuleFeatureService(repository).delete_feature(
            project_id=project_id, module_id="module_1", feature_id="feature_1", uow=uow
        )

        assert result == {"is_deleted": True, "deletion_reason": None, "deleted_at": None}
        repository.hard_delete_feature_cascade.assert_awaited_once_with(
            project_id=project_id, module_id="module_1", feature_id="feature_1"
        )
        repository.soft_delete_feature_by_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_feature_soft_deletes_when_approved_with_reason(self, uow):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.get_feature_for_module = AsyncMock(
            return_value=FeatureModel(
                id="feature_1",
                module_id="module_1",
                name="Feature 1",
                description="desc",
                status="approved",
                fea_code="FEA_001",
            )
        )
        repository.soft_delete_feature_by_id = AsyncMock(return_value=True)

        result = await ModuleFeatureService(repository).delete_feature(
            project_id=project_id,
            module_id="module_1",
            feature_id="feature_1",
            uow=uow,
            reason="No longer needed",
        )

        assert result["is_deleted"] is True
        assert result["deletion_reason"] == "No longer needed"
        assert result["deleted_at"] is not None
        repository.soft_delete_feature_by_id.assert_awaited_once_with(
            project_id=project_id,
            module_id="module_1",
            feature_id="feature_1",
            reason="No longer needed",
        )
        repository.hard_delete_feature_cascade.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_feature_approved_without_reason_raises_validation_error(self, uow):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.get_feature_for_module = AsyncMock(
            return_value=FeatureModel(
                id="feature_1",
                module_id="module_1",
                name="Feature 1",
                description="desc",
                status="approved",
                fea_code="FEA_001",
            )
        )

        with pytest.raises(ValidationError):
            await ModuleFeatureService(repository).delete_feature(
                project_id=project_id, module_id="module_1", feature_id="feature_1", uow=uow
            )
        repository.soft_delete_feature_by_id.assert_not_called()
        repository.hard_delete_feature_cascade.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_feature_project_not_found(self, uow):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = None

        with pytest.raises(NotFoundError):
            await ModuleFeatureService(repository).delete_feature(
                project_id=project_id, module_id="module_1", feature_id="feature_1", uow=uow
            )

    @pytest.mark.asyncio
    async def test_delete_feature_not_found(self, uow):
        repository = MagicMock()
        project_id = uuid.uuid4()
        uow.projects.get_by_uuid.return_value = MagicMock()
        repository.get_feature_for_module = AsyncMock(return_value=None)

        with pytest.raises(NotFoundError):
            await ModuleFeatureService(repository).delete_feature(
                project_id=project_id, module_id="module_1", feature_id="missing", uow=uow
            )

    @pytest.mark.asyncio
    async def test_upsert_modules_and_features_v2_none_skeleton_returns_empty(self):
        repository = MagicMock()

        result = await ModuleFeatureService(repository).upsert_modules_and_features_v2(
            project_id=uuid.uuid4(), module_feature_skeleton=None
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_upsert_modules_and_features_v2_persists_modules_and_features(self):
        repository = MagicMock()
        repository.upsert_modules_and_features_v2 = AsyncMock()
        project_id = uuid.uuid4()
        skeleton = {
            "modules": [
                {
                    "mod_code": "MOD_001",
                    "module_name": "Module 1",
                    "description": "desc",
                    "features": [
                        {
                            "fea_code": "FEA_001",
                            "feature_name": "Feature 1",
                            "description": "desc",
                            "functions": [],
                            "sources": [],
                            "l2_sources": [],
                        }
                    ],
                }
            ]
        }

        result = await ModuleFeatureService(repository).upsert_modules_and_features_v2(
            project_id=project_id, module_feature_skeleton=skeleton
        )

        assert len(result) == 1
        assert result[0].mod_code == "MOD_001"
        assert len(result[0].features) == 1
        assert result[0].features[0].fea_code == "FEA_001"
        repository.upsert_modules_and_features_v2.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_upsert_modules_and_features_v2_not_regenerating_skips_lookup(self):
        repository = MagicMock()
        repository.upsert_modules_and_features_v2 = AsyncMock()
        repository.get_module_by_mod_code = AsyncMock()
        project_id = uuid.uuid4()
        skeleton = {
            "modules": [
                {
                    "mod_code": "MOD_001",
                    "module_name": "Module 1",
                    "description": "desc",
                    "features": [],
                }
            ]
        }

        result = await ModuleFeatureService(repository).upsert_modules_and_features_v2(
            project_id=project_id, module_feature_skeleton=skeleton, is_regeneration=False
        )

        assert result[0].feedback_change_type is None
        repository.get_module_by_mod_code.assert_not_called()

    @pytest.mark.asyncio
    async def test_upsert_modules_and_features_v2_regeneration_new_module_and_feature_are_added(
        self,
    ):
        repository = MagicMock()
        repository.upsert_modules_and_features_v2 = AsyncMock()
        repository.get_module_by_mod_code = AsyncMock(return_value=None)
        repository.snapshot_module_version = AsyncMock()
        repository.snapshot_feature_version = AsyncMock()
        project_id = uuid.uuid4()
        skeleton = {
            "modules": [
                {
                    "mod_code": "MOD_NEW",
                    "module_name": "New Module",
                    "description": "desc",
                    "features": [
                        {
                            "fea_code": "FEA_NEW",
                            "feature_name": "New Feature",
                            "description": "desc",
                            "functions": [],
                            "sources": [],
                            "l2_sources": [],
                        }
                    ],
                }
            ]
        }

        result = await ModuleFeatureService(repository).upsert_modules_and_features_v2(
            project_id=project_id, module_feature_skeleton=skeleton, is_regeneration=True
        )

        assert result[0].feedback_change_type == ChangeType.ADDED
        assert result[0].features[0].feedback_change_type == ChangeType.ADDED
        repository.snapshot_module_version.assert_not_called()
        repository.snapshot_feature_version.assert_not_called()

    @pytest.mark.asyncio
    async def test_upsert_modules_and_features_v2_regeneration_changed_content_is_updated(self):
        repository = MagicMock()
        repository.upsert_modules_and_features_v2 = AsyncMock()
        existing_feature = FeatureModel(
            id="feature-1",
            module_id="module-1",
            name="Old Feature Name",
            description="old desc",
            fea_code="FEA_EXIST",
        )
        existing_module = ModuleModel(
            id="module-1",
            name="Old Module Name",
            description="old desc",
            mod_code="MOD_EXIST",
            features=[existing_feature],
        )
        repository.get_module_by_mod_code = AsyncMock(return_value=existing_module)
        repository.snapshot_module_version = AsyncMock()
        repository.snapshot_feature_version = AsyncMock()
        project_id = uuid.uuid4()
        skeleton = {
            "modules": [
                {
                    "mod_code": "MOD_EXIST",
                    "module_name": "New Module Name",
                    "description": "old desc",
                    "features": [
                        {
                            "fea_code": "FEA_EXIST",
                            "feature_name": "New Feature Name",
                            "description": "old desc",
                            "functions": [],
                            "sources": [],
                            "l2_sources": [],
                        }
                    ],
                }
            ]
        }

        result = await ModuleFeatureService(repository).upsert_modules_and_features_v2(
            project_id=project_id, module_feature_skeleton=skeleton, is_regeneration=True
        )

        assert result[0].id == "module-1"
        assert result[0].feedback_change_type == ChangeType.UPDATED
        assert result[0].features[0].id == "feature-1"
        assert result[0].features[0].feedback_change_type == ChangeType.UPDATED
        repository.snapshot_module_version.assert_awaited_once_with(project_id, "module-1")
        repository.snapshot_feature_version.assert_awaited_once_with(project_id, "feature-1")

    @pytest.mark.asyncio
    async def test_upsert_modules_and_features_v2_regeneration_unchanged_content_stays_none(self):
        repository = MagicMock()
        repository.upsert_modules_and_features_v2 = AsyncMock()
        existing_feature = FeatureModel(
            id="feature-1",
            module_id="module-1",
            name="Feature Name",
            description="desc",
            fea_code="FEA_EXIST",
        )
        existing_module = ModuleModel(
            id="module-1",
            name="Module Name",
            description="desc",
            mod_code="MOD_EXIST",
            features=[existing_feature],
        )
        repository.get_module_by_mod_code = AsyncMock(return_value=existing_module)
        repository.snapshot_module_version = AsyncMock()
        repository.snapshot_feature_version = AsyncMock()
        project_id = uuid.uuid4()
        skeleton = {
            "modules": [
                {
                    "mod_code": "MOD_EXIST",
                    "module_name": "Module Name",
                    "description": "desc",
                    "features": [
                        {
                            "fea_code": "FEA_EXIST",
                            "feature_name": "Feature Name",
                            "description": "desc",
                            "functions": [],
                            "sources": [],
                            "l2_sources": [],
                        }
                    ],
                }
            ]
        }

        result = await ModuleFeatureService(repository).upsert_modules_and_features_v2(
            project_id=project_id, module_feature_skeleton=skeleton, is_regeneration=True
        )

        assert result[0].id == "module-1"
        assert result[0].feedback_change_type is None
        assert result[0].features[0].id == "feature-1"
        assert result[0].features[0].feedback_change_type is None
        repository.snapshot_module_version.assert_not_called()
        repository.snapshot_feature_version.assert_not_called()

    @pytest.mark.asyncio
    async def test_upsert_modules_and_features_v2_stamps_source_ingestion_id(self):
        repository = MagicMock()
        repository.upsert_modules_and_features_v2 = AsyncMock()
        repository.get_module_by_mod_code = AsyncMock(return_value=None)
        project_id = uuid.uuid4()
        skeleton = {
            "modules": [
                {
                    "mod_code": "MOD_NEW",
                    "module_name": "New Module",
                    "description": "desc",
                    "features": [
                        {
                            "fea_code": "FEA_NEW",
                            "feature_name": "New Feature",
                            "description": "desc",
                            "functions": [],
                            "sources": [],
                            "l2_sources": [],
                        }
                    ],
                }
            ]
        }

        result = await ModuleFeatureService(repository).upsert_modules_and_features_v2(
            project_id=project_id,
            module_feature_skeleton=skeleton,
            is_regeneration=True,
            source_ingestion_id="ingestion-1",
        )

        assert result[0].source_ingestion_id == "ingestion-1"
        assert result[0].features[0].source_ingestion_id == "ingestion-1"
        persisted_module = repository.upsert_modules_and_features_v2.await_args.args[1]
        assert persisted_module.source_ingestion_id == "ingestion-1"
        assert persisted_module.features[0].source_ingestion_id == "ingestion-1"

    @pytest.mark.asyncio
    async def test_upsert_modules_and_features_for_source_code_none_skeleton_returns_empty(self):
        repository = MagicMock()

        result = await ModuleFeatureService(repository).upsert_modules_and_features_for_source_code(
            project_id=uuid.uuid4(), module_feature_skeleton=None
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_upsert_modules_and_features_for_source_code_persists(self):
        repository = MagicMock()
        repository.upsert_modules_and_features_for_source_code = AsyncMock()
        project_id = uuid.uuid4()
        skeleton = {
            "modules": [
                {
                    "mod_code": "MOD_001",
                    "module_name": "Module 1",
                    "description": "desc",
                    "features": [],
                }
            ]
        }

        result = await ModuleFeatureService(repository).upsert_modules_and_features_for_source_code(
            project_id=project_id, module_feature_skeleton=skeleton
        )

        assert len(result) == 1
        repository.upsert_modules_and_features_for_source_code.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_upsert_modules_and_features_for_source_code_stamps_source_ingestion_id(self):
        repository = MagicMock()
        repository.upsert_modules_and_features_for_source_code = AsyncMock()
        project_id = uuid.uuid4()
        skeleton = {
            "modules": [
                {
                    "mod_code": "MOD_001",
                    "module_name": "Module 1",
                    "description": "desc",
                    "features": [
                        {
                            "fea_code": "FEA_001",
                            "feature_name": "Feature 1",
                            "description": "desc",
                            "functions": [],
                            "sources": [],
                            "l2_sources": [],
                        }
                    ],
                }
            ]
        }

        result = await ModuleFeatureService(repository).upsert_modules_and_features_for_source_code(
            project_id=project_id,
            module_feature_skeleton=skeleton,
            source_ingestion_id="ingestion-9",
        )

        assert result[0].source_ingestion_id == "ingestion-9"
        assert result[0].features[0].source_ingestion_id == "ingestion-9"
