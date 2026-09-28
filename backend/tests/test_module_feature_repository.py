"""Unit tests for ModuleFeatureRepository."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import uuid

import pytest

from app.core.constants import INITIAL_ENTITY_VERSION
from app.core.exceptions import NotFoundError
from app.models.neo4j.module_feature_model import ChangeType, FeatureModel, ModuleModel
from app.repositories.neo4j.module_feature_repository import ModuleFeatureRepository


class _FakeResult:
    def __init__(self, single_result):
        self._single_result = single_result

    def single(self):
        return self._single_result

    def consume(self):
        return None

    def __iter__(self):
        if isinstance(self._single_result, list):
            return iter(self._single_result)
        return iter([])


class _FakeTx:
    def __init__(self, single_results, captured_runs):
        self._single_results = single_results
        self._captured_runs = captured_runs

    def run(self, cypher, **params):
        self._captured_runs.append((cypher, params))
        return _FakeResult(self._single_results.pop(0))


class _FakeSession:
    def __init__(self, single_results, captured_runs):
        self._single_results = single_results
        self._captured_runs = captured_runs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return None

    def execute_read(self, callback):
        return callback(_FakeTx(self._single_results, self._captured_runs))

    def execute_write(self, callback):
        return callback(_FakeTx(self._single_results, self._captured_runs))


class _FakeDriver:
    def __init__(self, single_results, captured_runs):
        self._single_results = single_results
        self._captured_runs = captured_runs

    def session(self):
        return _FakeSession(self._single_results, self._captured_runs)


class TestModuleFeatureRepository:
    @pytest.mark.asyncio
    async def test_list_modules_by_project_success(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(
            single_results=[
                [
                    {
                        "module_id": "module_1",
                        "project_id": str(project_id),
                        "name": "Module 1",
                        "description": "Module description",
                        "module_status": "draft",
                        "mod_code": "MOD_001",
                        "module_deleted_at": None,
                        "features": [
                            {
                                "id": "feature_11",
                                "project_id": str(project_id),
                                "module_id": "module_1",
                                "name": "Feature 11",
                                "description": "Feature description",
                                "status": "draft",
                                "fea_code": "FEA_001",
                                "deleted_at": None,
                                "sources": json.dumps(
                                    [
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
                                ),
                                "req_count": 3,
                            }
                        ],
                    }
                ]
            ],
            captured_runs=captured_runs,
        )
        repository = ModuleFeatureRepository(driver)

        modules = await repository.list_modules_by_project(project_id)

        assert len(modules) == 1
        assert modules[0].id == "module_1"
        assert modules[0].name == "Module 1"
        assert modules[0].status == "draft"
        assert modules[0].mod_code == "MOD_001"
        assert modules[0].deleted_at is None
        assert len(modules[0].features) == 1
        assert modules[0].features[0].status == "draft"
        assert modules[0].features[0].fea_code == "FEA_001"
        assert modules[0].features[0].deleted_at is None
        assert len(modules[0].features) == 1
        assert modules[0].features[0].id == "feature_11"
        assert modules[0].features[0].name == "Feature 11"
        assert modules[0].features[0].total_user_stories == 3
        assert modules[0].features[0].sources == [
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
    async def test_list_modules_by_project_returns_deleted_at(self):
        project_id = uuid.uuid4()
        deleted_at = datetime(2026, 8, 18, tzinfo=UTC)
        driver = _FakeDriver(
            single_results=[
                [
                    {
                        "module_id": "module_1",
                        "project_id": str(project_id),
                        "name": "Module 1",
                        "description": "Module description",
                        "module_status": "approved",
                        "mod_code": "MOD_001",
                        "module_deleted_at": deleted_at,
                        "features": [
                            {
                                "id": "feature_11",
                                "project_id": str(project_id),
                                "module_id": "module_1",
                                "name": "Feature 11",
                                "description": "Feature description",
                                "status": "approved",
                                "fea_code": "FEA_001",
                                "deleted_at": deleted_at,
                                "req_count": 0,
                            }
                        ],
                    }
                ]
            ],
            captured_runs=[],
        )
        repository = ModuleFeatureRepository(driver)

        modules = await repository.list_modules_by_project(project_id)

        assert modules[0].deleted_at == deleted_at
        assert modules[0].features[0].deleted_at == deleted_at

    @pytest.mark.asyncio
    async def test_list_modules_by_project_empty_project_returns_empty_list(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[[]], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        modules = await repository.list_modules_by_project(project_id)
        assert modules == []

    @pytest.mark.asyncio
    async def test_get_feature_for_module_success(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(
            single_results=[
                {
                    "feature_id": "feature_1",
                    "module_id": "module_1",
                    "name": "Feature 1",
                    "description": "Feature description",
                    "project_id": str(project_id),
                    "fea_code": "FEA_001",
                    "status": "draft",
                    "justification": None,
                    "incremental_change_type": None,
                    "feedback_change_type": None,
                    "generation_metadata": None,
                    "is_infrastructure": None,
                    "condensation_note": None,
                    "functions": None,
                    "sources": None,
                    "created_at": None,
                    "updated_at": None,
                    "deleted_at": None,
                }
            ],
            captured_runs=captured_runs,
        )
        repository = ModuleFeatureRepository(driver)

        feature = await repository.get_feature_for_module(project_id, "module_1", "feature_1")

        assert feature is not None
        assert feature.id == "feature_1"
        assert feature.module_id == "module_1"
        assert feature.fea_code == "FEA_001"
        assert feature.status == "draft"
        assert feature.deleted_at is None

    @pytest.mark.asyncio
    async def test_get_feature_for_module_returns_deleted_at(self):
        project_id = uuid.uuid4()
        deleted_at = datetime(2026, 8, 18, tzinfo=UTC)
        driver = _FakeDriver(
            single_results=[
                {
                    "feature_id": "feature_1",
                    "module_id": "module_1",
                    "name": "Feature 1",
                    "description": "Feature description",
                    "project_id": str(project_id),
                    "fea_code": "FEA_001",
                    "status": "approved",
                    "justification": None,
                    "incremental_change_type": None,
                    "feedback_change_type": None,
                    "generation_metadata": None,
                    "is_infrastructure": None,
                    "condensation_note": None,
                    "functions": None,
                    "sources": None,
                    "created_at": None,
                    "updated_at": None,
                    "deleted_at": deleted_at,
                }
            ],
            captured_runs=[],
        )
        repository = ModuleFeatureRepository(driver)

        feature = await repository.get_feature_for_module(project_id, "module_1", "feature_1")

        assert feature is not None
        assert feature.deleted_at == deleted_at

    @pytest.mark.asyncio
    async def test_get_feature_for_module_not_found(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        feature = await repository.get_feature_for_module(project_id, "module_1", "missing_feature")

        assert feature is None

    @pytest.mark.asyncio
    async def test_upsert_modules_and_features_v2_uses_unwind_for_features(self):
        project_id = uuid.uuid4()
        captured_runs = []
        module = ModuleModel(
            id=str(uuid.uuid4()),
            project_id=project_id,
            mod_code="MOD_EMPTY",
            name="Module Empty",
            description="No features yet",
            features=[],
        )
        driver = _FakeDriver(single_results=[{"module_id": module.id}], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        result = await repository.upsert_modules_and_features_v2(project_id, module)

        assert result.id == module.id
        cypher, params = captured_runs[0]
        assert "UNWIND features AS feature" in cypher
        assert "FOREACH (feature IN features" not in cypher
        assert params["features"] == []

    @pytest.mark.asyncio
    async def test_upsert_modules_for_source_code_uses_unwind_for_features(self):
        project_id = uuid.uuid4()
        captured_runs = []
        module = ModuleModel(
            id=str(uuid.uuid4()),
            project_id=project_id,
            mod_code="MOD_EMPTY",
            name="Module Empty",
            description="No features yet",
            features=[],
        )
        driver = _FakeDriver(single_results=[{"module_id": module.id}], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        result = await repository.upsert_modules_and_features_for_source_code(project_id, module)

        assert result.id == module.id
        cypher, params = captured_runs[0]
        assert "UNWIND features AS feature" in cypher
        assert "FOREACH (feature IN features" not in cypher
        assert params["features"] == []

    @pytest.mark.asyncio
    async def test_upsert_modules_and_features_v2_serializes_sources(self):
        project_id = uuid.uuid4()
        captured_runs = []
        module = ModuleModel(
            id=str(uuid.uuid4()),
            project_id=project_id,
            mod_code="MOD_SRC",
            name="Module Source",
            description="Has feature source references",
            features=[
                FeatureModel(
                    id=str(uuid.uuid4()),
                    project_id=project_id,
                    module_id="module-source",
                    fea_code="FEA_SRC",
                    name="Feature Source",
                    description="Feature with sources",
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
        driver = _FakeDriver(single_results=[{"module_id": module.id}], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        await repository.upsert_modules_and_features_v2(project_id, module)

        _, params = captured_runs[0]
        assert json.loads(params["features"][0]["sources"]) == module.features[0].sources

    @pytest.mark.asyncio
    async def test_upsert_modules_and_features_v2_passes_feedback_change_type(self):
        project_id = uuid.uuid4()
        captured_runs = []
        module = ModuleModel(
            id=str(uuid.uuid4()),
            project_id=project_id,
            mod_code="MOD_FB",
            name="Module Feedback",
            description="Regenerated module",
            feedback_change_type=ChangeType.UPDATED,
            features=[
                FeatureModel(
                    id=str(uuid.uuid4()),
                    project_id=project_id,
                    module_id="module-fb",
                    fea_code="FEA_FB",
                    name="Feature Feedback",
                    description="Regenerated feature",
                    feedback_change_type=ChangeType.ADDED,
                )
            ],
        )
        driver = _FakeDriver(single_results=[{"module_id": module.id}], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        await repository.upsert_modules_and_features_v2(project_id, module)

        cypher, params = captured_runs[0]
        assert "coalesce($feedback_change_type, m.feedback_change_type)" in cypher
        assert "coalesce(feature.feedback_change_type, f.feedback_change_type)" in cypher
        assert params["feedback_change_type"] == "UPDATED"
        assert params["features"][0]["feedback_change_type"] == "ADDED"
        # module is UPDATED -> version bumps; feature is ADDED -> no bump (coalesce seeds initial).
        assert params["module_version_bump"] is True
        assert params["features"][0]["version_bump"] is False

    @pytest.mark.asyncio
    async def test_upsert_modules_and_features_v2_updated_feature_bumps_version(self):
        project_id = uuid.uuid4()
        captured_runs = []
        module = ModuleModel(
            id=str(uuid.uuid4()),
            project_id=project_id,
            mod_code="MOD_FB2",
            name="Module Feedback 2",
            description="Not itself changed",
            features=[
                FeatureModel(
                    id=str(uuid.uuid4()),
                    project_id=project_id,
                    module_id="module-fb2",
                    fea_code="FEA_FB2",
                    name="Feature Feedback 2",
                    description="Regenerated, content changed",
                    feedback_change_type=ChangeType.UPDATED,
                )
            ],
        )
        driver = _FakeDriver(single_results=[{"module_id": module.id}], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        await repository.upsert_modules_and_features_v2(project_id, module)

        cypher, params = captured_runs[0]
        assert "CASE WHEN $module_version_bump" in cypher
        assert "CASE WHEN feature.version_bump" in cypher
        assert params["module_version_bump"] is False
        assert params["features"][0]["version_bump"] is True

    @pytest.mark.asyncio
    async def test_upsert_modules_and_features_v2_no_feedback_change_type_passes_none(self):
        project_id = uuid.uuid4()
        captured_runs = []
        module = ModuleModel(
            id=str(uuid.uuid4()),
            project_id=project_id,
            mod_code="MOD_PLAIN",
            name="Module Plain",
            description="Not regenerated",
            features=[],
        )
        driver = _FakeDriver(single_results=[{"module_id": module.id}], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        await repository.upsert_modules_and_features_v2(project_id, module)

        _, params = captured_runs[0]
        assert params["feedback_change_type"] is None

    @pytest.mark.asyncio
    async def test_get_module_by_mod_code_found(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(
            single_results=[
                {
                    "module_id": "module_1",
                    "project_id": str(project_id),
                    "name": "Module 1",
                    "description": "desc",
                    "status": "ready",
                    "version": 1,
                    "mod_code": "MOD_001",
                    "module_created_at": None,
                    "module_updated_at": None,
                    "module_deleted_at": None,
                    "incremental_change_type": None,
                    "feedback_change_type": None,
                    "justification": None,
                    "text_diffs": None,
                    "rfp_flagged_item": None,
                    "is_jira_synced": False,
                    "is_tap_synced": False,
                },
                [],
            ],
            captured_runs=[],
        )
        repository = ModuleFeatureRepository(driver)

        module = await repository.get_module_by_mod_code(project_id, "MOD_001")

        assert module is not None
        assert module.id == "module_1"
        assert module.mod_code == "MOD_001"
        assert module.features == []
        assert module.deleted_at is None

    @pytest.mark.asyncio
    async def test_get_module_by_mod_code_returns_deleted_at(self):
        project_id = uuid.uuid4()
        deleted_at = datetime(2026, 8, 18, tzinfo=UTC)
        driver = _FakeDriver(
            single_results=[
                {
                    "module_id": "module_1",
                    "project_id": str(project_id),
                    "name": "Module 1",
                    "description": "desc",
                    "status": "approved",
                    "version": 1,
                    "mod_code": "MOD_001",
                    "module_created_at": None,
                    "module_updated_at": None,
                    "module_deleted_at": deleted_at,
                    "incremental_change_type": None,
                    "feedback_change_type": None,
                    "justification": None,
                    "text_diffs": None,
                    "rfp_flagged_item": None,
                    "is_jira_synced": False,
                    "is_tap_synced": False,
                },
                [],
            ],
            captured_runs=[],
        )
        repository = ModuleFeatureRepository(driver)

        module = await repository.get_module_by_mod_code(project_id, "MOD_001")

        assert module is not None
        assert module.deleted_at == deleted_at

    @pytest.mark.asyncio
    async def test_get_module_for_project_returns_deleted_at(self):
        project_id = uuid.uuid4()
        deleted_at = datetime(2026, 8, 18, tzinfo=UTC)
        driver = _FakeDriver(
            single_results=[
                {
                    "module_id": "module_1",
                    "project_id": str(project_id),
                    "name": "Module 1",
                    "description": "desc",
                    "status": "approved",
                    "version": 1,
                    "mod_code": "MOD_001",
                    "module_created_at": None,
                    "module_updated_at": None,
                    "module_deleted_at": deleted_at,
                    "incremental_change_type": None,
                    "feedback_change_type": None,
                    "justification": None,
                    "text_diffs": None,
                    "rfp_flagged_item": None,
                    "is_jira_synced": False,
                    "is_tap_synced": False,
                },
                [
                    {
                        "feature_id": "feature_1",
                        "project_id": str(project_id),
                        "module_id": "module_1",
                        "name": "Feature 1",
                        "description": "desc",
                        "status": "approved",
                        "version": 1,
                        "fea_code": "FEA_001",
                        "mfu_id": None,
                        "created_at": None,
                        "updated_at": None,
                        "deleted_at": deleted_at,
                        "functions": None,
                        "sources": None,
                        "l2_sources": None,
                        "generation_metadata": None,
                        "is_infrastructure": None,
                        "condensation_note": None,
                        "is_jira_synced": False,
                        "is_tap_synced": False,
                        "incremental_change_type": None,
                        "feedback_change_type": None,
                        "justification": None,
                        "text_diffs": None,
                        "rfp_flagged_item": None,
                    }
                ],
            ],
            captured_runs=[],
        )
        repository = ModuleFeatureRepository(driver)

        module = await repository.get_module_for_project(project_id, "module_1")

        assert module is not None
        assert module.deleted_at == deleted_at
        assert module.features[0].deleted_at == deleted_at

    @pytest.mark.asyncio
    async def test_get_module_by_mod_code_not_found(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        module = await repository.get_module_by_mod_code(project_id, "MOD_MISSING")

        assert module is None

    @pytest.mark.asyncio
    async def test_get_feature_by_mod_code_and_mfu_found(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(
            single_results=[
                {
                    "feature_id": "feature_1",
                    "module_id": "module_1",
                    "name": "Feature 1",
                    "description": "Feature description",
                    "project_id": str(project_id),
                    "fea_code": "FEA_001",
                    "status": "ready",
                    "justification": None,
                    "incremental_change_type": None,
                    "feedback_change_type": None,
                    "generation_metadata": None,
                    "is_infrastructure": None,
                    "condensation_note": None,
                    "functions": None,
                    "sources": None,
                    "created_at": None,
                    "updated_at": None,
                }
            ],
            captured_runs=[],
        )
        repository = ModuleFeatureRepository(driver)

        feature = await repository.get_feature_by_mod_code_and_mfu(project_id, "MOD_001", "mfu-1")

        assert feature is not None
        assert feature.id == "feature_1"

    @pytest.mark.asyncio
    async def test_get_feature_by_mod_code_and_mfu_not_found(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        feature = await repository.get_feature_by_mod_code_and_mfu(
            project_id, "MOD_001", "missing-mfu"
        )

        assert feature is None

    @pytest.mark.asyncio
    async def test_get_module_id_for_feature_found(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"module_id": "module_1"}], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        module_id = await repository.get_module_id_for_feature(project_id, "feature_1")

        assert module_id == "module_1"

    @pytest.mark.asyncio
    async def test_get_module_id_for_feature_not_found(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        module_id = await repository.get_module_id_for_feature(project_id, "missing")

        assert module_id is None

    @pytest.mark.asyncio
    async def test_count_features_for_module(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"feature_count": 3}], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        count = await repository.count_features_for_module(project_id, "module_1")

        assert count == 3

    @pytest.mark.asyncio
    async def test_count_features_for_module_no_record_returns_zero(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        count = await repository.count_features_for_module(project_id, "module_1")

        assert count == 0

    @pytest.mark.asyncio
    async def test_are_all_modules_approved_true(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"are_all_approved": True}], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        assert await repository.are_all_modules_approved(project_id) is True

    @pytest.mark.asyncio
    async def test_are_all_modules_approved_no_record_returns_false(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        assert await repository.are_all_modules_approved(project_id) is False

    @pytest.mark.asyncio
    async def test_are_all_features_approved_true(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"are_all_approved": True}], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        assert await repository.are_all_features_approved(project_id) is True

    @pytest.mark.asyncio
    async def test_count_pending_changes_by_ingestion_sums_modules_and_features(self):
        driver = _FakeDriver(single_results=[{"pending": 3}], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        count = await repository.count_pending_changes_by_ingestion("ingestion-1")

        assert count == 3

    @pytest.mark.asyncio
    async def test_count_pending_changes_by_ingestion_no_record_returns_zero(self):
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        assert await repository.count_pending_changes_by_ingestion("ingestion-1") == 0

    @pytest.mark.asyncio
    async def test_count_pending_changes_by_ingestion_filters_by_ingestion_id(self):
        captured_runs = []
        driver = _FakeDriver(single_results=[{"pending": 0}], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        await repository.count_pending_changes_by_ingestion("ingestion-42")

        cypher, params = captured_runs[0]
        assert "Module {source_ingestion_id: $source_ingestion_id}" in cypher
        assert "Feature {source_ingestion_id: $source_ingestion_id}" in cypher
        assert "m.incremental_change_type IS NOT NULL OR m.feedback_change_type IS NOT NULL" in cypher
        assert "f.incremental_change_type IS NOT NULL OR f.feedback_change_type IS NOT NULL" in cypher
        assert params["source_ingestion_id"] == "ingestion-42"

    @pytest.mark.asyncio
    async def test_count_pending_feedback_changes_by_project_sums_modules_and_features(self):
        driver = _FakeDriver(single_results=[{"pending": 2}], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        count = await repository.count_pending_feedback_changes_by_project(uuid.uuid4())

        assert count == 2

    @pytest.mark.asyncio
    async def test_count_pending_feedback_changes_by_project_no_record_returns_zero(self):
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        assert await repository.count_pending_feedback_changes_by_project(uuid.uuid4()) == 0

    @pytest.mark.asyncio
    async def test_count_pending_feedback_changes_by_project_filters_by_project_id_and_feedback_only(
        self,
    ):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[{"pending": 0}], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        await repository.count_pending_feedback_changes_by_project(project_id)

        cypher, params = captured_runs[0]
        assert "Module {project_id: $project_id}" in cypher
        assert "Feature {project_id: $project_id}" in cypher
        assert "m.feedback_change_type IS NOT NULL" in cypher
        assert "f.feedback_change_type IS NOT NULL" in cypher
        # incremental_change_type must NOT be checked here — only feedback-driven
        # changes block approval, unlike count_pending_changes_by_ingestion above.
        assert "incremental_change_type" not in cypher
        assert params["project_id"] == str(project_id)

    @pytest.mark.asyncio
    async def test_count_modules_and_features_for_project_returns_counts(self):
        driver = _FakeDriver(
            single_results=[{"total_modules": 3, "total_features": 7}], captured_runs=[]
        )
        repository = ModuleFeatureRepository(driver)

        total_modules, total_features = await repository.count_modules_and_features_for_project(
            uuid.uuid4()
        )

        assert (total_modules, total_features) == (3, 7)

    @pytest.mark.asyncio
    async def test_count_modules_and_features_for_project_no_record_returns_zero(self):
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        assert await repository.count_modules_and_features_for_project(uuid.uuid4()) == (0, 0)

    @pytest.mark.asyncio
    async def test_count_modules_and_features_for_project_filters_soft_deleted(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(
            single_results=[{"total_modules": 0, "total_features": 0}],
            captured_runs=captured_runs,
        )
        repository = ModuleFeatureRepository(driver)

        await repository.count_modules_and_features_for_project(project_id)

        cypher, params = captured_runs[0]
        assert "WHERE m.deleted_at IS NULL" in cypher
        assert "WHERE m2.deleted_at IS NULL AND f.deleted_at IS NULL" in cypher
        assert params["project_id"] == str(project_id)

    @pytest.mark.asyncio
    async def test_delete_feature_by_id_success(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"deleted_count": 1}], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        assert await repository.delete_feature_by_id(project_id, "feature_1") is True

    @pytest.mark.asyncio
    async def test_delete_feature_by_id_not_found(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"deleted_count": 0}], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        assert await repository.delete_feature_by_id(project_id, "missing") is False

    @pytest.mark.asyncio
    async def test_delete_module_by_id_success(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"deleted_count": 1}], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        assert await repository.delete_module_by_id(project_id, "module_1") is True

    @pytest.mark.asyncio
    async def test_delete_module_by_id_not_found(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"deleted_count": 0}], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        assert await repository.delete_module_by_id(project_id, "missing") is False

    @pytest.mark.asyncio
    async def test_hard_delete_module_cascade_success(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(
            single_results=[{"deleted_count": 1}], captured_runs=captured_runs
        )
        repository = ModuleFeatureRepository(driver)

        assert await repository.hard_delete_module_cascade(project_id, "module_1") is True
        cypher, params = captured_runs[0]
        assert "DETACH DELETE mv, fv, rv, r, f, m" in cypher
        assert "HAS_VERSION]->(mv:ModuleVersion)" in cypher
        assert "HAS_VERSION]->(fv:FeatureVersion)" in cypher
        assert "HAS_VERSION]->(rv:UserStoryVersion)" in cypher
        assert params == {"project_id": str(project_id), "module_id": "module_1"}

    @pytest.mark.asyncio
    async def test_hard_delete_module_cascade_not_found(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"deleted_count": 0}], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        assert await repository.hard_delete_module_cascade(project_id, "missing") is False

    @pytest.mark.asyncio
    async def test_soft_delete_module_cascade_success(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(
            single_results=[{"updated_count": 1}], captured_runs=captured_runs
        )
        repository = ModuleFeatureRepository(driver)

        result = await repository.soft_delete_module_cascade(
            project_id, "module_1", "No longer needed"
        )

        assert result is True
        cypher, params = captured_runs[0]
        assert "m.is_deleted = true" in cypher
        assert "f.is_deleted = true" in cypher
        assert "r.is_current = false" in cypher
        assert "r.del_reason = $reason" in cypher
        assert params["reason"] == "No longer needed"
        assert params["module_id"] == "module_1"

    @pytest.mark.asyncio
    async def test_soft_delete_module_cascade_not_found(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"updated_count": 0}], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        result = await repository.soft_delete_module_cascade(project_id, "missing", "reason")
        assert result is False

    @pytest.mark.asyncio
    async def test_hard_delete_feature_cascade_success(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(
            single_results=[{"deleted_count": 1}], captured_runs=captured_runs
        )
        repository = ModuleFeatureRepository(driver)

        result = await repository.hard_delete_feature_cascade(
            project_id, "module_1", "feature_1"
        )

        assert result is True
        cypher, params = captured_runs[0]
        assert "DETACH DELETE fv, rv, r, f" in cypher
        assert "HAS_VERSION]->(fv:FeatureVersion)" in cypher
        assert "HAS_VERSION]->(rv:UserStoryVersion)" in cypher
        assert params == {
            "project_id": str(project_id),
            "module_id": "module_1",
            "feature_id": "feature_1",
        }

    @pytest.mark.asyncio
    async def test_hard_delete_feature_cascade_not_found(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"deleted_count": 0}], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        result = await repository.hard_delete_feature_cascade(
            project_id, "module_1", "missing"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_soft_delete_feature_by_id_success(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(
            single_results=[{"updated_count": 1}], captured_runs=captured_runs
        )
        repository = ModuleFeatureRepository(driver)

        result = await repository.soft_delete_feature_by_id(
            project_id, "module_1", "feature_1", "No longer needed"
        )

        assert result is True
        cypher, params = captured_runs[0]
        assert "f.is_deleted = true" in cypher
        assert "r.is_current = false" in cypher
        assert "r.del_reason = $reason" in cypher
        assert params["reason"] == "No longer needed"
        assert params["feature_id"] == "feature_1"

    @pytest.mark.asyncio
    async def test_soft_delete_feature_by_id_not_found(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"updated_count": 0}], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        result = await repository.soft_delete_feature_by_id(
            project_id, "module_1", "missing", "reason"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_update_module_success(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[{"updated_count": 1}], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        result = await repository.update_module(
            project_id, "module_1", "MOD_001", "Module 1", "desc"
        )

        assert result is True
        cypher, params = captured_runs[0]
        assert params["module_id"] == "module_1"
        assert params["mod_code"] == "MOD_001"

    @pytest.mark.asyncio
    async def test_update_module_not_found(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"updated_count": 0}], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        result = await repository.update_module(
            project_id, "missing", "MOD_001", "Module 1", "desc"
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_snapshot_module_version_success(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"created_count": 1}], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        assert await repository.snapshot_module_version(project_id, "module_1") is True

    @pytest.mark.asyncio
    async def test_snapshot_module_version_module_missing(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"created_count": 0}], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        assert await repository.snapshot_module_version(project_id, "missing") is False

    @pytest.mark.asyncio
    async def test_accept_module_success(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"updated_count": 1}], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        assert await repository.accept_module(project_id, "module_1") is True

    @pytest.mark.asyncio
    async def test_accept_module_feedback_change_success(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[{"updated_count": 1}], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        result = await repository.accept_module_feedback_change(project_id, "module_1")

        assert result is True
        cypher, _ = captured_runs[0]
        assert "m.feedback_change_type = null" in cypher
        assert "incremental_change_type" not in cypher

    @pytest.mark.asyncio
    async def test_accept_module_feedback_change_not_found(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"updated_count": 0}], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        assert await repository.accept_module_feedback_change(project_id, "missing") is False

    @pytest.mark.asyncio
    async def test_get_latest_module_version_found(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(
            single_results=[
                {
                    "id": str(uuid.uuid4()),
                    "module_id": "module_1",
                    "project_id": str(project_id),
                    "mod_code": "MOD_001",
                    "name": "Module 1",
                    "description": "desc",
                    "status": "ready",
                    "version": 1,
                    "justification": None,
                    "incremental_change_type": None,
                    "feedback_change_type": None,
                    "is_jira_synced": False,
                    "is_tap_synced": False,
                    "created_at": None,
                    "updated_at": None,
                    "snapshotted_at": None,
                }
            ],
            captured_runs=[],
        )
        repository = ModuleFeatureRepository(driver)

        version = await repository.get_latest_module_version(project_id, "module_1")

        assert version is not None
        assert version.module_id == "module_1"

    @pytest.mark.asyncio
    async def test_get_latest_module_version_none(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        version = await repository.get_latest_module_version(project_id, "module_1")

        assert version is None

    @pytest.mark.asyncio
    async def test_restore_module_from_latest_version_with_snapshot(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(
            single_results=[{"updated_count": 1, "restored": True}], captured_runs=[]
        )
        repository = ModuleFeatureRepository(driver)

        assert await repository.restore_module_from_latest_version(project_id, "module_1") is True

    @pytest.mark.asyncio
    async def test_restore_module_from_latest_version_no_snapshot_still_clears_flags(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(
            single_results=[{"updated_count": 1, "restored": False}], captured_runs=[]
        )
        repository = ModuleFeatureRepository(driver)

        assert await repository.restore_module_from_latest_version(project_id, "module_1") is True

    @pytest.mark.asyncio
    async def test_restore_module_from_latest_version_module_missing(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        assert await repository.restore_module_from_latest_version(project_id, "missing") is False

    @pytest.mark.asyncio
    async def test_restore_module_from_latest_feedback_version_with_snapshot(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(
            single_results=[{"updated_count": 1, "restored": True}], captured_runs=captured_runs
        )
        repository = ModuleFeatureRepository(driver)

        result = await repository.restore_module_from_latest_feedback_version(
            project_id, "module_1"
        )

        assert result is True
        cypher, _ = captured_runs[0]
        assert "m.feedback_change_type = null" in cypher
        assert "incremental_change_type" not in cypher

    @pytest.mark.asyncio
    async def test_restore_module_from_latest_feedback_version_no_snapshot_still_clears_flag(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(
            single_results=[{"updated_count": 1, "restored": False}], captured_runs=[]
        )
        repository = ModuleFeatureRepository(driver)

        result = await repository.restore_module_from_latest_feedback_version(
            project_id, "module_1"
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_restore_module_from_latest_feedback_version_module_missing(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        result = await repository.restore_module_from_latest_feedback_version(
            project_id, "missing"
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_get_max_mod_code_returns_max(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"max_code": 7}], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        assert await repository.get_max_mod_code(project_id) == 7

    @pytest.mark.asyncio
    async def test_get_max_mod_code_no_modules_returns_zero(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"max_code": None}], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        assert await repository.get_max_mod_code(project_id) == 0

    @pytest.mark.asyncio
    async def test_get_max_fea_code_returns_mod_code_and_suffix(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(
            single_results=[{"mod_code": "MOD_001", "max_suffix": 3}], captured_runs=[]
        )
        repository = ModuleFeatureRepository(driver)

        mod_code, suffix = await repository.get_max_fea_code(project_id, "module_1")

        assert mod_code == "MOD_001"
        assert suffix == 3

    @pytest.mark.asyncio
    async def test_get_max_fea_code_module_missing(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        mod_code, suffix = await repository.get_max_fea_code(project_id, "missing")

        assert mod_code == ""
        assert suffix == 0

    @pytest.mark.asyncio
    async def test_create_module_success(self):
        project_id = uuid.uuid4()
        module = ModuleModel(
            id=str(uuid.uuid4()),
            project_id=project_id,
            mod_code="MOD_NEW",
            name="New Module",
            description="desc",
            features=[],
        )
        driver = _FakeDriver(single_results=[{"module_id": module.id}], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        result = await repository.create_module(project_id, module)

        assert result.id == module.id

    @pytest.mark.asyncio
    async def test_create_module_project_missing_raises_not_found(self):
        project_id = uuid.uuid4()
        module = ModuleModel(
            id=str(uuid.uuid4()),
            project_id=project_id,
            mod_code="MOD_NEW",
            name="New Module",
            description="desc",
            features=[],
        )
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        with pytest.raises(NotFoundError):
            await repository.create_module(project_id, module)

    @pytest.mark.asyncio
    async def test_update_feature_success(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"updated_count": 1}], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        result = await repository.update_feature(
            project_id, "feature_1", "FEA_001", "Feature 1", "desc", "[]", "[]"
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_update_feature_not_found(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"updated_count": 0}], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        result = await repository.update_feature(
            project_id, "missing", "FEA_001", "Feature 1", "desc", "[]", "[]"
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_create_feature_success(self):
        project_id = uuid.uuid4()
        feature = FeatureModel(
            id=str(uuid.uuid4()),
            project_id=project_id,
            module_id="module_1",
            fea_code="FEA_NEW",
            name="New Feature",
            description="desc",
        )
        driver = _FakeDriver(single_results=[{"feature_id": feature.id}], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        result = await repository.create_feature(project_id, "module_1", feature)

        assert result.id == feature.id

    @pytest.mark.asyncio
    async def test_create_feature_module_missing_raises_not_found(self):
        project_id = uuid.uuid4()
        feature = FeatureModel(
            id=str(uuid.uuid4()),
            project_id=project_id,
            module_id="module_1",
            fea_code="FEA_NEW",
            name="New Feature",
            description="desc",
        )
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        with pytest.raises(NotFoundError):
            await repository.create_feature(project_id, "missing_module", feature)

    @pytest.mark.asyncio
    async def test_snapshot_feature_version_success(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"created_count": 1}], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        assert await repository.snapshot_feature_version(project_id, "feature_1") is True

    @pytest.mark.asyncio
    async def test_accept_feature_success(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"updated_count": 1}], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        assert await repository.accept_feature(project_id, "feature_1") is True

    @pytest.mark.asyncio
    async def test_accept_feature_feedback_change_success(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[{"updated_count": 1}], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        result = await repository.accept_feature_feedback_change(project_id, "feature_1")

        assert result is True
        cypher, _ = captured_runs[0]
        assert "f.feedback_change_type = null" in cypher
        assert "incremental_change_type" not in cypher

    @pytest.mark.asyncio
    async def test_accept_feature_feedback_change_not_found(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"updated_count": 0}], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        assert await repository.accept_feature_feedback_change(project_id, "missing") is False

    @pytest.mark.asyncio
    async def test_get_feature_pending_change_flags_found(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(
            single_results=[
                {"incremental_change_type": "UPDATED", "feedback_change_type": None}
            ],
            captured_runs=[],
        )
        repository = ModuleFeatureRepository(driver)

        result = await repository.get_feature_pending_change_flags(project_id, "feature_1")

        assert result == ("UPDATED", None)

    @pytest.mark.asyncio
    async def test_get_feature_pending_change_flags_not_found(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        assert await repository.get_feature_pending_change_flags(project_id, "missing") is None

    @pytest.mark.asyncio
    async def test_get_latest_feature_version_found(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(
            single_results=[
                {
                    "id": str(uuid.uuid4()),
                    "feature_id": "feature_1",
                    "module_id": "module_1",
                    "project_id": str(project_id),
                    "fea_code": "FEA_001",
                    "mfu_id": None,
                    "name": "Feature 1",
                    "description": "desc",
                    "status": "ready",
                    "version": 1,
                    "functions": None,
                    "sources": None,
                    "l2_sources": None,
                    "is_infrastructure": None,
                    "condensation_note": None,
                    "justification": None,
                    "incremental_change_type": None,
                    "feedback_change_type": None,
                    "is_jira_synced": False,
                    "is_tap_synced": False,
                    "created_at": None,
                    "updated_at": None,
                    "snapshotted_at": None,
                }
            ],
            captured_runs=[],
        )
        repository = ModuleFeatureRepository(driver)

        version = await repository.get_latest_feature_version(project_id, "feature_1")

        assert version is not None
        assert version.feature_id == "feature_1"

    @pytest.mark.asyncio
    async def test_get_latest_feature_version_none(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        version = await repository.get_latest_feature_version(project_id, "feature_1")

        assert version is None

    @pytest.mark.asyncio
    async def test_restore_feature_from_latest_version_with_snapshot(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(
            single_results=[{"updated_count": 1, "restored": True}], captured_runs=[]
        )
        repository = ModuleFeatureRepository(driver)

        assert await repository.restore_feature_from_latest_version(project_id, "feature_1") is True

    @pytest.mark.asyncio
    async def test_restore_feature_from_latest_version_feature_missing(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        assert await repository.restore_feature_from_latest_version(project_id, "missing") is False

    @pytest.mark.asyncio
    async def test_restore_feature_from_latest_feedback_version_with_snapshot(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(
            single_results=[{"updated_count": 1, "restored": True}], captured_runs=captured_runs
        )
        repository = ModuleFeatureRepository(driver)

        result = await repository.restore_feature_from_latest_feedback_version(
            project_id, "feature_1"
        )

        assert result is True
        cypher, _ = captured_runs[0]
        assert "f.feedback_change_type = null" in cypher
        assert "incremental_change_type" not in cypher

    @pytest.mark.asyncio
    async def test_restore_feature_from_latest_feedback_version_feature_missing(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        result = await repository.restore_feature_from_latest_feedback_version(
            project_id, "missing"
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_mark_module_delete_suggested_success(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[{"updated_count": 1}], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        result = await repository.mark_module_delete_suggested(project_id, "module_1", "why")

        assert result is True
        cypher, params = captured_runs[0]
        assert params["justification"] == "why"
        assert params["source_ingestion_id"] is None
        assert "m.version                 = coalesce(m.version, $version) + 1" in cypher
        assert params["version"] == INITIAL_ENTITY_VERSION

    @pytest.mark.asyncio
    async def test_mark_module_delete_suggested_stamps_source_ingestion_id(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[{"updated_count": 1}], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        await repository.mark_module_delete_suggested(
            project_id, "module_1", "why", source_ingestion_id="ingestion-1"
        )

        cypher, params = captured_runs[0]
        assert "m.source_ingestion_id     = coalesce($source_ingestion_id, m.source_ingestion_id)" in cypher
        assert params["source_ingestion_id"] == "ingestion-1"

    @pytest.mark.asyncio
    async def test_mark_feature_delete_suggested_success(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[{"updated_count": 1}], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        result = await repository.mark_feature_delete_suggested(project_id, "feature_1", "why")

        assert result is True
        cypher, params = captured_runs[0]
        assert "f.version                 = coalesce(f.version, $version) + 1" in cypher
        assert params["version"] == INITIAL_ENTITY_VERSION

    @pytest.mark.asyncio
    async def test_mark_feature_delete_suggested_stamps_source_ingestion_id(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[{"updated_count": 1}], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        await repository.mark_feature_delete_suggested(
            project_id, "feature_1", "why", source_ingestion_id="ingestion-1"
        )

        cypher, params = captured_runs[0]
        assert "f.source_ingestion_id     = coalesce($source_ingestion_id, f.source_ingestion_id)" in cypher
        assert params["source_ingestion_id"] == "ingestion-1"

class TestParseHelpers:
    def test_neo4j_dt_to_py_none(self):
        assert ModuleFeatureRepository._neo4j_dt_to_py(None) is None

    def test_neo4j_dt_to_py_python_datetime_passthrough(self):
        from datetime import datetime

        dt = datetime(2026, 1, 1)
        assert ModuleFeatureRepository._neo4j_dt_to_py(dt) is dt

    def test_neo4j_dt_to_py_no_to_native_returns_none(self):
        assert ModuleFeatureRepository._neo4j_dt_to_py(object()) is None

    def test_parse_functions_empty(self):
        assert ModuleFeatureRepository._parse_functions(None) == []
        assert ModuleFeatureRepository._parse_functions("") == []

    def test_parse_functions_valid(self):
        value = json.dumps(
            [{"fun_code": "F1", "name": "Fn", "description": "d", "func_src_ref": "r"}]
        )
        result = ModuleFeatureRepository._parse_functions(value)
        assert len(result) == 1
        assert result[0].fun_code == "F1"

    def test_parse_functions_invalid_json_returns_empty(self):
        assert ModuleFeatureRepository._parse_functions("not json") == []

    def test_parse_sources_empty(self):
        assert ModuleFeatureRepository._parse_sources(None) == []

    def test_parse_sources_normalizes_legacy_string_entries(self):
        value = json.dumps(["src-1", {"source_id": "src-2", "pages": []}])
        result = ModuleFeatureRepository._parse_sources(value)
        assert result == [{"source_id": "src-1", "pages": []}, {"source_id": "src-2", "pages": []}]

    def test_parse_sources_invalid_json_returns_empty(self):
        assert ModuleFeatureRepository._parse_sources("{not json") == []

    def test_parse_sources_non_list_json_returns_empty(self):
        assert ModuleFeatureRepository._parse_sources(json.dumps({"a": 1})) == []

    def test_parse_l2_sources_empty(self):
        assert ModuleFeatureRepository._parse_l2_sources(None) == []

    def test_parse_l2_sources_valid(self):
        value = json.dumps(["SRS::MFU-001::S5::EVENT-001", ""])
        assert ModuleFeatureRepository._parse_l2_sources(value) == ["SRS::MFU-001::S5::EVENT-001"]

    def test_parse_l2_sources_invalid_json_returns_empty(self):
        assert ModuleFeatureRepository._parse_l2_sources("not json") == []

    def test_parse_l2_sources_non_list_returns_empty(self):
        assert ModuleFeatureRepository._parse_l2_sources(json.dumps({"a": 1})) == []

    def test_parse_text_diffs_empty(self):
        assert ModuleFeatureRepository._parse_text_diffs(None) == {}

    def test_parse_text_diffs_valid_dict(self):
        value = json.dumps({"name": {"old": "a", "new": "b"}})
        assert ModuleFeatureRepository._parse_text_diffs(value) == {
            "name": {"old": "a", "new": "b"}
        }

    def test_parse_text_diffs_legacy_list_shape_returns_empty(self):
        assert ModuleFeatureRepository._parse_text_diffs(json.dumps([1, 2, 3])) == {}

    def test_parse_text_diffs_invalid_json_returns_empty(self):
        assert ModuleFeatureRepository._parse_text_diffs("not json") == {}

    def test_parse_rfp_flagged_item_empty(self):
        assert ModuleFeatureRepository._parse_rfp_flagged_item(None) is None

    def test_parse_rfp_flagged_item_valid(self):
        value = json.dumps({"reason": "conflict"})
        assert ModuleFeatureRepository._parse_rfp_flagged_item(value) == {"reason": "conflict"}

    def test_parse_rfp_flagged_item_non_dict_returns_none(self):
        assert ModuleFeatureRepository._parse_rfp_flagged_item(json.dumps([1, 2])) is None

    def test_parse_rfp_flagged_item_invalid_json_returns_none(self):
        assert ModuleFeatureRepository._parse_rfp_flagged_item("not json") is None


class TestModuleFeatureRepositorySourceIngestionId:
    @pytest.mark.asyncio
    async def test_list_modules_by_project_filters_by_source_ingestion_id(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[[]], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        await repository.list_modules_by_project(project_id, source_ingestion_id="ingestion-7")

        cypher, params = captured_runs[0]
        assert (
            "WHERE m.deleted_at IS NULL "
            "  AND ($source_ingestion_id IS NULL OR m.source_ingestion_id = $source_ingestion_id) "
            in cypher
        )
        assert params["source_ingestion_id"] == "ingestion-7"

    @pytest.mark.asyncio
    async def test_list_modules_by_project_no_filter_passes_none(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[[]], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        await repository.list_modules_by_project(project_id)

        _, params = captured_runs[0]
        assert params["source_ingestion_id"] is None

    @pytest.mark.asyncio
    async def test_list_modules_by_project_req_count_excludes_soft_deleted_user_stories(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[[]], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        await repository.list_modules_by_project(project_id)

        cypher, _ = captured_runs[0]
        assert "count(CASE WHEN r.deleted_at IS NULL THEN r END) AS req_count" in cypher

    @pytest.mark.asyncio
    async def test_list_modules_by_project_excludes_soft_deleted_features(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[[]], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        await repository.list_modules_by_project(project_id)

        cypher, _ = captured_runs[0]
        assert "WITH m, collect(CASE WHEN f IS NOT NULL AND f.deleted_at IS NULL THEN " in cypher

    @pytest.mark.asyncio
    async def test_get_module_for_project_excludes_soft_deleted_module(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[None], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        module = await repository.get_module_for_project(project_id, "module_1")

        assert module is None
        cypher, _ = captured_runs[0]
        assert "WHERE m.deleted_at IS NULL " in cypher

    @pytest.mark.asyncio
    async def test_get_module_by_mod_code_excludes_soft_deleted_module(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[None], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        module = await repository.get_module_by_mod_code(project_id, "MOD_001")

        assert module is None
        cypher, _ = captured_runs[0]
        assert "WHERE m.deleted_at IS NULL " in cypher


class TestGetSourceIngestionId:
    @pytest.mark.asyncio
    async def test_module_found_returns_tagged_ingestion_id(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(
            single_results=[{"source_ingestion_id": "ingestion-1"}], captured_runs=captured_runs
        )
        repository = ModuleFeatureRepository(driver)

        result = await repository.get_source_ingestion_id(project_id, "module", "mod-1")

        assert result == "ingestion-1"
        cypher, params = captured_runs[0]
        assert "MATCH (n:Module {id: $entity_id, project_id: $project_id})" in cypher
        assert params == {"entity_id": "mod-1", "project_id": str(project_id)}

    @pytest.mark.asyncio
    async def test_feature_found_returns_tagged_ingestion_id(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(
            single_results=[{"source_ingestion_id": "ingestion-2"}], captured_runs=captured_runs
        )
        repository = ModuleFeatureRepository(driver)

        result = await repository.get_source_ingestion_id(project_id, "feature", "fea-1")

        assert result == "ingestion-2"
        cypher, _ = captured_runs[0]
        assert "MATCH (n:Feature {id: $entity_id, project_id: $project_id})" in cypher

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = ModuleFeatureRepository(driver)

        result = await repository.get_source_ingestion_id(project_id, "module", "missing")

        assert result is None

    @pytest.mark.asyncio
    async def test_list_features_for_module_excludes_soft_deleted_features(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(
            single_results=[
                {
                    "module_id": "module_1",
                    "project_id": str(project_id),
                    "name": "Module 1",
                    "description": "desc",
                    "status": "ready",
                    "version": 1,
                    "mod_code": "MOD_001",
                    "module_created_at": None,
                    "module_updated_at": None,
                    "module_deleted_at": None,
                    "incremental_change_type": None,
                    "feedback_change_type": None,
                    "justification": None,
                    "text_diffs": None,
                    "rfp_flagged_item": None,
                    "is_jira_synced": False,
                    "is_tap_synced": False,
                },
                [],
            ],
            captured_runs=captured_runs,
        )
        repository = ModuleFeatureRepository(driver)

        await repository.get_module_for_project(project_id, "module_1")

        features_cypher, _ = captured_runs[1]
        assert "WHERE f.deleted_at IS NULL " in features_cypher

    @pytest.mark.asyncio
    async def test_get_feature_for_module_excludes_soft_deleted_feature(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[None], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        feature = await repository.get_feature_for_module(project_id, "module_1", "feature_1")

        assert feature is None
        cypher, _ = captured_runs[0]
        assert "WHERE f.deleted_at IS NULL " in cypher

    @pytest.mark.asyncio
    async def test_get_feature_by_mod_code_and_mfu_excludes_soft_deleted_feature(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[None], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        feature = await repository.get_feature_by_mod_code_and_mfu(project_id, "MOD_001", "mfu-1")

        assert feature is None
        cypher, _ = captured_runs[0]
        assert "WHERE f.deleted_at IS NULL " in cypher

    @pytest.mark.asyncio
    async def test_count_features_for_module_excludes_soft_deleted_features(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[{"feature_count": 0}], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        await repository.count_features_for_module(project_id, "module_1")

        cypher, _ = captured_runs[0]
        assert "WHERE f.deleted_at IS NULL " in cypher

    @pytest.mark.asyncio
    async def test_are_all_modules_approved_excludes_soft_deleted_modules(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(
            single_results=[{"are_all_approved": True}], captured_runs=captured_runs
        )
        repository = ModuleFeatureRepository(driver)

        await repository.are_all_modules_approved(project_id)

        cypher, _ = captured_runs[0]
        assert "WHERE m.deleted_at IS NULL " in cypher

    @pytest.mark.asyncio
    async def test_are_all_features_approved_excludes_soft_deleted_features(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(
            single_results=[{"are_all_approved": True}], captured_runs=captured_runs
        )
        repository = ModuleFeatureRepository(driver)

        await repository.are_all_features_approved(project_id)

        cypher, _ = captured_runs[0]
        assert "WHERE f.deleted_at IS NULL " in cypher

    @pytest.mark.asyncio
    async def test_upsert_v2_new_module_and_feature_stamp_source_ingestion_id(self):
        project_id = uuid.uuid4()
        captured_runs = []
        module = ModuleModel(
            id=str(uuid.uuid4()),
            project_id=project_id,
            source_ingestion_id="ingestion-1",
            mod_code="MOD_SRC_ING",
            name="Module",
            description="desc",
            features=[
                FeatureModel(
                    id=str(uuid.uuid4()),
                    project_id=project_id,
                    source_ingestion_id="ingestion-1",
                    module_id="module-src-ing",
                    fea_code="FEA_SRC_ING",
                    name="Feature",
                    description="desc",
                )
            ],
        )
        driver = _FakeDriver(single_results=[{"module_id": module.id}], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        await repository.upsert_modules_and_features_v2(project_id, module)

        cypher, params = captured_runs[0]
        assert "m.source_ingestion_id  = CASE WHEN m.source_ingestion_id IS NULL" in cypher
        assert "f.source_ingestion_id = CASE WHEN f.source_ingestion_id IS NULL" in cypher
        assert params["source_ingestion_id"] == "ingestion-1"
        assert params["features"][0]["source_ingestion_id"] == "ingestion-1"

    @pytest.mark.asyncio
    async def test_upsert_for_source_code_stamps_source_ingestion_id_unconditionally(self):
        project_id = uuid.uuid4()
        captured_runs = []
        module = ModuleModel(
            id=str(uuid.uuid4()),
            project_id=project_id,
            source_ingestion_id="ingestion-2",
            mod_code="MOD_SC_ING",
            name="Module",
            description="desc",
            features=[
                FeatureModel(
                    id=str(uuid.uuid4()),
                    project_id=project_id,
                    source_ingestion_id="ingestion-2",
                    module_id="module-sc-ing",
                    fea_code="FEA_SC_ING",
                    name="Feature",
                    description="desc",
                )
            ],
        )
        driver = _FakeDriver(single_results=[{"module_id": module.id}], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        await repository.upsert_modules_and_features_for_source_code(project_id, module)

        cypher, params = captured_runs[0]
        assert "m.source_ingestion_id  = $source_ingestion_id" in cypher
        assert "f.source_ingestion_id  = feature.source_ingestion_id" in cypher
        assert params["source_ingestion_id"] == "ingestion-2"
        assert params["features"][0]["source_ingestion_id"] == "ingestion-2"

    @pytest.mark.asyncio
    async def test_update_module_passes_source_ingestion_id(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[{"updated_count": 1}], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        result = await repository.update_module(
            project_id,
            "module_1",
            "MOD_001",
            "Module 1",
            "desc",
            source_ingestion_id="ingestion-3",
        )

        assert result is True
        cypher, params = captured_runs[0]
        assert "coalesce($source_ingestion_id, m.source_ingestion_id)" in cypher
        assert params["source_ingestion_id"] == "ingestion-3"

    @pytest.mark.asyncio
    async def test_update_feature_passes_source_ingestion_id(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[{"updated_count": 1}], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        result = await repository.update_feature(
            project_id,
            "feature_1",
            "FEA_001",
            "Feature 1",
            "desc",
            "[]",
            "[]",
            source_ingestion_id="ingestion-4",
        )

        assert result is True
        cypher, params = captured_runs[0]
        assert "coalesce($source_ingestion_id, f.source_ingestion_id)" in cypher
        assert params["source_ingestion_id"] == "ingestion-4"

    @pytest.mark.asyncio
    async def test_create_module_passes_source_ingestion_id(self):
        project_id = uuid.uuid4()
        captured_runs = []
        module = ModuleModel(
            id=str(uuid.uuid4()),
            project_id=project_id,
            source_ingestion_id="ingestion-5",
            mod_code="MOD_NEW_ING",
            name="New Module",
            description="desc",
            features=[],
        )
        driver = _FakeDriver(single_results=[{"module_id": module.id}], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        await repository.create_module(project_id, module)

        _, params = captured_runs[0]
        assert params["source_ingestion_id"] == "ingestion-5"

    @pytest.mark.asyncio
    async def test_create_feature_passes_source_ingestion_id(self):
        project_id = uuid.uuid4()
        feature = FeatureModel(
            id=str(uuid.uuid4()),
            project_id=project_id,
            source_ingestion_id="ingestion-6",
            module_id="module_1",
            fea_code="FEA_NEW_ING",
            name="New Feature",
            description="desc",
        )
        captured_runs = []
        driver = _FakeDriver(single_results=[{"feature_id": feature.id}], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        await repository.create_feature(project_id, "module_1", feature)

        _, params = captured_runs[0]
        assert params["source_ingestion_id"] == "ingestion-6"

    @pytest.mark.asyncio
    async def test_snapshot_module_version_captures_source_ingestion_id(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[{"created_count": 1}], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        assert await repository.snapshot_module_version(project_id, "module_1") is True
        cypher, _ = captured_runs[0]
        assert "source_ingestion_id: m.source_ingestion_id" in cypher

    @pytest.mark.asyncio
    async def test_snapshot_feature_version_captures_source_ingestion_id(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[{"created_count": 1}], captured_runs=captured_runs)
        repository = ModuleFeatureRepository(driver)

        assert await repository.snapshot_feature_version(project_id, "feature_1") is True
        cypher, _ = captured_runs[0]
        assert "source_ingestion_id: f.source_ingestion_id" in cypher
