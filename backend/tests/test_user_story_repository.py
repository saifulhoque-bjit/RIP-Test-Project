"""Unit tests for UserStoryRepository."""

from __future__ import annotations

from datetime import UTC, datetime
import uuid

import pytest

from app.core.constants import INITIAL_ENTITY_VERSION
from app.models.neo4j.user_story_model import UserStoryModel
from app.repositories.neo4j.user_story_repository import UserStoryRepository


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


def _make_user_story(
    source_id: uuid.UUID | None = None, user_story_id: str = "req_1"
) -> UserStoryModel:
    return UserStoryModel(
        id=user_story_id,
        user_story_code="ARCH-001",
        title="Core Architecture",
        description="Some description",
        consensus=9.8,
        status="approved",
        version=1,
        feature_id=None,
    )


def _make_record(user_story_id: str = "req_1") -> dict:
    return {
        "user_story_id": user_story_id,
        "user_story_code": "ARCH-001",
        "title": "Core Architecture",
        "description": "Some description",
        "consensus": 9.8,
        "status": "approved",
        "version": "1.0",
        "feature_id": None,
        "source_file_count": 1,
    }


def _make_full_record(user_story_id: str = "req_1", **overrides) -> dict:
    """A record shape satisfying `_record_to_model`'s required (bracket-access) keys."""
    record = {
        "user_story_id": user_story_id,
        "user_story_code": "ARCH-001",
        "title": "Core Architecture",
        "description": "Some description",
        "consensus": 9.8,
        "status": "approved",
        "version": 1,
        "feature_id": "feature_1",
        "project_id": None,
        "as_a": None,
        "i_want_to": None,
        "so_that": None,
        "acceptance_criteria": None,
        "nfrs": None,
        "technical_notes": None,
        "story_points": None,
        "sources": None,
        "l2_sources": None,
        "is_current": True,
        "is_jira_synced": False,
        "is_tap_synced": False,
        "del_reason": None,
        "deleted_at": None,
        "rfp_flagged_item": None,
        "created_at": None,
        "updated_at": None,
    }
    record.update(overrides)
    return record


class TestUserStoryRepository:
    @pytest.mark.asyncio
    async def test_list_user_stories_for_project_success(self):
        project_id = uuid.uuid4()
        source_id = uuid.uuid4()
        record_with_source = {
            **_make_record("req_1"),
            "source_id": str(source_id),
            "nfrs": [
                '{"id":"ARCH-001-NFR1","category":"Security","requirement":"Use parameterized queries."}'
            ],
        }
        driver = _FakeDriver(
            single_results=[
                {"total": 1},
                [record_with_source],
            ],
            captured_runs=[],
        )
        repository = UserStoryRepository(driver)

        results, total = await repository.list_user_stories_for_project(
            project_id=project_id, source_id=source_id
        )

        assert total == 1
        assert len(results) == 1
        assert results[0].id == "req_1"
        assert results[0].nfrs == [
            {
                "id": "ARCH-001-NFR1",
                "category": "Security",
                "requirement": "Use parameterized queries.",
            }
        ]

    @pytest.mark.asyncio
    async def test_list_user_stories_for_project_empty_source_ids(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(
            single_results=[
                {"total": 0},
                [],
            ],
            captured_runs=[],
        )
        repository = UserStoryRepository(driver)

        results, total = await repository.list_user_stories_for_project(
            project_id=project_id, source_id=None
        )

        assert total == 0
        assert results == []

    @pytest.mark.asyncio
    async def test_list_user_stories_for_project_excludes_soft_deleted(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(
            single_results=[
                {"total": 0},
                [],
            ],
            captured_runs=captured_runs,
        )
        repository = UserStoryRepository(driver)

        await repository.list_user_stories_for_project(project_id=project_id, source_id=None)

        count_cypher, _ = captured_runs[0]
        list_cypher, _ = captured_runs[1]
        assert "WHERE r.deleted_at IS NULL " in count_cypher
        assert "WHERE r.deleted_at IS NULL " in list_cypher

    @pytest.mark.asyncio
    async def test_get_project_summary_success(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(
            single_results=[
                {
                    "total_user_stories": 5,
                    "ready_count": 2,
                    "needs_edit_count": 1,
                    "failed_count": 1,
                    "approved_count": 1,
                },
                {"total_modules": 3},
                {"total_features": 9},
            ],
            captured_runs=[],
        )
        repository = UserStoryRepository(driver)

        summary = await repository.get_project_summary(project_id)

        assert summary["total_user_stories"] == 5
        assert summary["ready_count"] == 2
        assert summary["needs_edit_count"] == 1
        assert summary["failed_count"] == 1
        assert summary["approved_count"] == 1
        assert summary["total_modules"] == 3
        assert summary["total_features"] == 9

    @pytest.mark.asyncio
    async def test_get_project_summary_no_records(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[None, None, None], captured_runs=[])
        repository = UserStoryRepository(driver)

        summary = await repository.get_project_summary(project_id)

        assert summary["total_user_stories"] == 0
        assert summary["failed_count"] == 0
        assert summary["approved_count"] == 0
        assert summary["total_modules"] == 0
        assert summary["total_features"] == 0

    @pytest.mark.asyncio
    async def test_bulk_upsert_user_stories_for_project_persists_nfrs(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(
            single_results=[{"upserted_count": 1}],
            captured_runs=captured_runs,
        )
        repository = UserStoryRepository(driver)
        model = UserStoryModel(
            id="req_1",
            user_story_code="ARCH-001",
            title="Core Architecture",
            description=None,
            consensus=0.0,
            status="ready",
            version=1,
            feature_id=None,
            nfrs=[
                {
                    "id": "NFR-SEC-01",
                    "category": "Security",
                    "description": "Cross-resident apartment access prevention",
                    "requirement": "API must return HTTP 403 for any cross-resident apartment access attempt.",
                }
            ],
        )

        upserted = await repository.bulk_upsert_user_stories_for_project(
            project_id=project_id, user_stories=[model]
        )

        assert upserted == 1
        cypher, params = captured_runs[0]
        assert "r.nfrs = coalesce(req.nfrs, [])" in cypher
        assert len(params["user_stories"][0]["nfrs"]) == 1
        import json as _json

        stored_nfr = _json.loads(params["user_stories"][0]["nfrs"][0])
        assert stored_nfr == {
            "id": "NFR-SEC-01",
            "category": "Security",
            "description": "Cross-resident apartment access prevention",
            "requirement": "API must return HTTP 403 for any cross-resident apartment access attempt.",
        }

    @pytest.mark.asyncio
    async def test_bulk_upsert_user_stories_for_project_version_write_guards_against_regression(
        self,
    ):
        """Regression test: the SET clause must never let a caller's incorrectly
        low ``req.version`` (e.g. computed from a fallback of 1 after a failed
        version lookup) overwrite a higher live ``r.version`` already stored on
        the node."""
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(
            single_results=[{"upserted_count": 1}],
            captured_runs=captured_runs,
        )
        repository = UserStoryRepository(driver)
        model = UserStoryModel(
            id="req_1",
            user_story_code="ARCH-001",
            title="Core Architecture",
            description=None,
            consensus=0.0,
            status="ready",
            version=1,
            feature_id=None,
        )

        await repository.bulk_upsert_user_stories_for_project(
            project_id=project_id, user_stories=[model]
        )

        cypher, _ = captured_runs[0]
        assert "r.version = CASE WHEN r.version IS NULL OR req.version >= r.version" in cypher
        assert "THEN req.version ELSE r.version END" in cypher

    @pytest.mark.asyncio
    async def test_change_user_story_status_success(self):
        captured_runs = []
        updated_record = {**_make_record("req_1"), "status": "approved"}
        driver = _FakeDriver(
            single_results=[
                updated_record,  # _execute_change_status returns full record
            ],
            captured_runs=captured_runs,
        )
        repository = UserStoryRepository(driver)

        result = await repository.change_user_story_status("req_1", "approved")

        assert result is not None
        assert result.status == "approved"
        assert "r.status = $status" in captured_runs[0][0]

    @pytest.mark.asyncio
    async def test_change_user_story_status_not_found(self):
        driver = _FakeDriver(
            single_results=[None],  # _execute_change_status returns nothing
            captured_runs=[],
        )
        repository = UserStoryRepository(driver)

        result = await repository.change_user_story_status("missing", "approved")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_user_story_detail_for_project_success(self):
        project_id = uuid.uuid4()
        source_id = uuid.uuid4()
        req_record = {
            **_make_record("req_1"),
            "linked_source_ids": [str(source_id)],
            "srs_evidence": [
                {
                    "id": "ev-1",
                    "module_code": "M1",
                    "feature_code": "F1",
                    "user_story_code": "ARCH-001",
                    "user_story_id": "req_1",
                    "group_spec_id": "gs-1",
                    "file_name": "spec.md",
                    "exact_quote": "must",
                    "created_at": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
                    "updated_at": datetime(2026, 1, 2, 3, 4, 6, tzinfo=UTC),
                    "group_spec": {
                        "id": "gs-1",
                        "filename": "spec.md",
                        "created_at": datetime(2026, 1, 2, 3, 4, 7, tzinfo=UTC),
                        "updated_at": datetime(2026, 1, 2, 3, 4, 8, tzinfo=UTC),
                    },
                }
            ],
        }
        captured_runs = []
        driver = _FakeDriver(
            single_results=[
                req_record,  # user_story query (single)
            ],
            captured_runs=captured_runs,
        )
        repository = UserStoryRepository(driver)

        result = await repository.get_user_story_detail_for_project(project_id, "req_1")

        assert result is not None
        assert result.id == "req_1"
        assert result.user_story_code == "ARCH-001"
        assert len(result.srs_evidence) == 1
        assert result.srs_evidence[0].id == "ev-1"
        assert result.srs_evidence[0].file_name == "spec.md"
        assert result.srs_evidence[0].exact_quote == "must"
        assert result.srs_evidence[0].group_spec is not None
        assert result.srs_evidence[0].group_spec.id == "gs-1"
        assert result.srs_evidence[0].group_spec.filename == "spec.md"
        assert isinstance(result.srs_evidence[0].created_at, datetime)
        assert isinstance(result.srs_evidence[0].updated_at, datetime)
        assert isinstance(result.srs_evidence[0].group_spec.created_at, datetime)
        assert isinstance(result.srs_evidence[0].group_spec.updated_at, datetime)
        assert "MATCH (p:Project {id: $project_id})" in captured_runs[0][0]

    @pytest.mark.asyncio
    async def test_get_user_story_detail_for_project_not_found(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = UserStoryRepository(driver)

        result = await repository.get_user_story_detail_for_project(project_id, "missing")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_user_story_detail_for_project_empty_sources(self):
        project_id = uuid.uuid4()
        req_record = {
            **_make_record("req_1"),
        }
        driver = _FakeDriver(single_results=[req_record], captured_runs=[])
        repository = UserStoryRepository(driver)

        result = await repository.get_user_story_detail_for_project(project_id, "req_1")

        assert result is not None
        assert result.sources == []

    @pytest.mark.asyncio
    async def test_get_user_story_detail_for_project_excludes_soft_deleted(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[None], captured_runs=captured_runs)
        repository = UserStoryRepository(driver)

        result = await repository.get_user_story_detail_for_project(project_id, "req_1")

        assert result is None
        cypher, _ = captured_runs[0]
        assert "WHERE r.deleted_at IS NULL " in cypher

    @pytest.mark.asyncio
    async def test_get_project_summary_excludes_soft_deleted(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(
            single_results=[
                {
                    "total_user_stories": 0,
                    "ready_count": 0,
                    "needs_edit_count": 0,
                    "failed_count": 0,
                    "approved_count": 0,
                },
                {"total_modules": 0},
                {"total_features": 0},
            ],
            captured_runs=captured_runs,
        )
        repository = UserStoryRepository(driver)

        await repository.get_project_summary(project_id)

        user_story_cypher, _ = captured_runs[0]
        assert "WHERE r.deleted_at IS NULL " in user_story_cypher

    @pytest.mark.asyncio
    async def test_are_all_user_stories_approved_true(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"are_all_approved": True}], captured_runs=[])
        repository = UserStoryRepository(driver)

        assert await repository.are_all_user_stories_approved(project_id) is True

    @pytest.mark.asyncio
    async def test_are_all_user_stories_approved_no_record_returns_false(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = UserStoryRepository(driver)

        assert await repository.are_all_user_stories_approved(project_id) is False

    @pytest.mark.asyncio
    async def test_are_all_user_stories_approved_excludes_soft_deleted(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(
            single_results=[{"are_all_approved": True}], captured_runs=captured_runs
        )
        repository = UserStoryRepository(driver)

        await repository.are_all_user_stories_approved(project_id)

        cypher, _ = captured_runs[0]
        assert "WHERE r.deleted_at IS NULL " in cypher

    @pytest.mark.asyncio
    async def test_count_approved_user_stories_returns_count(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"approved": 5}], captured_runs=[])
        repository = UserStoryRepository(driver)

        assert await repository.count_approved_user_stories(project_id) == 5

    @pytest.mark.asyncio
    async def test_count_approved_user_stories_no_record_returns_zero(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = UserStoryRepository(driver)

        assert await repository.count_approved_user_stories(project_id) == 0

    @pytest.mark.asyncio
    async def test_count_approved_user_stories_filters_soft_deleted_and_status(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[{"approved": 0}], captured_runs=captured_runs)
        repository = UserStoryRepository(driver)

        await repository.count_approved_user_stories(project_id)

        cypher, _ = captured_runs[0]
        assert "WHERE r.deleted_at IS NULL AND r.status = 'approved'" in cypher

    @pytest.mark.asyncio
    async def test_count_pending_changes_by_ingestion(self):
        driver = _FakeDriver(single_results=[{"pending": 2}], captured_runs=[])
        repository = UserStoryRepository(driver)

        assert await repository.count_pending_changes_by_ingestion("ingestion-1") == 2

    @pytest.mark.asyncio
    async def test_count_pending_changes_by_ingestion_no_record_returns_zero(self):
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = UserStoryRepository(driver)

        assert await repository.count_pending_changes_by_ingestion("ingestion-1") == 0

    @pytest.mark.asyncio
    async def test_count_pending_changes_by_ingestion_filters_by_ingestion_id(self):
        captured_runs = []
        driver = _FakeDriver(single_results=[{"pending": 0}], captured_runs=captured_runs)
        repository = UserStoryRepository(driver)

        await repository.count_pending_changes_by_ingestion("ingestion-42")

        cypher, params = captured_runs[0]
        assert "UserStory {source_ingestion_id: $source_ingestion_id}" in cypher
        assert "r.incremental_change_type IS NOT NULL OR r.feedback_change_type IS NOT NULL" in cypher
        assert params["source_ingestion_id"] == "ingestion-42"

    @pytest.mark.asyncio
    async def test_get_source_ingestion_id_found(self):
        captured_runs = []
        driver = _FakeDriver(
            single_results=[{"source_ingestion_id": "ingestion-9"}], captured_runs=captured_runs
        )
        repository = UserStoryRepository(driver)

        result = await repository.get_source_ingestion_id("story-1")

        assert result == "ingestion-9"
        cypher, params = captured_runs[0]
        assert "MATCH (r:UserStory {id: $user_story_id})" in cypher
        assert params == {"user_story_id": "story-1"}

    @pytest.mark.asyncio
    async def test_get_source_ingestion_id_not_found_returns_none(self):
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = UserStoryRepository(driver)

        result = await repository.get_source_ingestion_id("missing")

        assert result is None

    @pytest.mark.asyncio
    async def test_delete_user_stories_for_project(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"deleted_count": 3}], captured_runs=[])
        repository = UserStoryRepository(driver)

        count = await repository.delete_user_stories_for_project(
            project_id=project_id, source_ids=[uuid.uuid4()]
        )

        assert count == 3

    @pytest.mark.asyncio
    async def test_delete_user_stories_for_project_no_record_returns_zero(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = UserStoryRepository(driver)

        count = await repository.delete_user_stories_for_project(
            project_id=project_id, source_ids=[]
        )

        assert count == 0

    @pytest.mark.asyncio
    async def test_delete_user_story_by_id_success(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"deleted_count": 1}], captured_runs=[])
        repository = UserStoryRepository(driver)

        result = await repository.delete_user_story_by_id(
            project_id=project_id, user_story_id="req_1"
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_delete_user_story_by_id_not_found(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"deleted_count": 0}], captured_runs=[])
        repository = UserStoryRepository(driver)

        result = await repository.delete_user_story_by_id(
            project_id=project_id, user_story_id="missing"
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_soft_delete_user_story_by_id_success(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[{"updated_count": 1}], captured_runs=captured_runs)
        repository = UserStoryRepository(driver)

        result = await repository.soft_delete_user_story_by_id(
            project_id=project_id, user_story_id="req_1", del_reason="obsolete"
        )

        assert result is True
        _, params = captured_runs[0]
        assert params["del_reason"] == "obsolete"

    @pytest.mark.asyncio
    async def test_soft_delete_user_story_by_id_not_found(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"updated_count": 0}], captured_runs=[])
        repository = UserStoryRepository(driver)

        result = await repository.soft_delete_user_story_by_id(
            project_id=project_id, user_story_id="missing", del_reason="obsolete"
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_count_active_user_stories_for_feature(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"story_count": 4}], captured_runs=[])
        repository = UserStoryRepository(driver)

        count = await repository.count_active_user_stories_for_feature(
            project_id=project_id, feature_id="feature_1"
        )

        assert count == 4

    @pytest.mark.asyncio
    async def test_count_active_user_stories_for_feature_no_record_returns_zero(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = UserStoryRepository(driver)

        count = await repository.count_active_user_stories_for_feature(
            project_id=project_id, feature_id="feature_1"
        )

        assert count == 0

    @pytest.mark.asyncio
    async def test_bulk_upsert_user_stories_for_project_empty_list_short_circuits(self):
        driver = _FakeDriver(single_results=[], captured_runs=[])
        repository = UserStoryRepository(driver)

        count = await repository.bulk_upsert_user_stories_for_project(
            project_id=uuid.uuid4(), user_stories=[]
        )

        assert count == 0

    @pytest.mark.asyncio
    async def test_bulk_upsert_user_stories_for_source_code_empty_list_short_circuits(self):
        driver = _FakeDriver(single_results=[], captured_runs=[])
        repository = UserStoryRepository(driver)

        count = await repository.bulk_upsert_user_stories_for_source_code(
            project_id=uuid.uuid4(), user_stories=[]
        )

        assert count == 0

    @pytest.mark.asyncio
    async def test_bulk_upsert_user_stories_for_source_code_success(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"upserted_count": 2}], captured_runs=[])
        repository = UserStoryRepository(driver)
        model = UserStoryModel(
            id="req_1",
            user_story_code="ARCH-001",
            title="Core Architecture",
            description=None,
            consensus=0.0,
            status="ready",
            version=1,
            feature_id="feature_1",
        )

        count = await repository.bulk_upsert_user_stories_for_source_code(
            project_id=project_id, user_stories=[model]
        )

        assert count == 2

    def test_get_project_summary_sync(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(
            single_results=[
                {
                    "total_user_stories": 5,
                    "ready_count": 2,
                    "needs_edit_count": 1,
                    "failed_count": 1,
                    "approved_count": 1,
                },
                {"total_modules": 3},
                {"total_features": 9},
            ],
            captured_runs=[],
        )
        repository = UserStoryRepository(driver)

        summary = repository.get_project_summary_sync(project_id)

        assert summary["total_user_stories"] == 5

    @pytest.mark.asyncio
    async def test_get_project_id_by_user_story_id_found(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"project_id": str(project_id)}], captured_runs=[])
        repository = UserStoryRepository(driver)

        result = await repository.get_project_id_by_user_story_id("req_1")

        assert result == project_id

    @pytest.mark.asyncio
    async def test_get_project_id_by_user_story_id_not_found(self):
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = UserStoryRepository(driver)

        result = await repository.get_project_id_by_user_story_id("missing")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_project_id_by_user_story_id_null_project_id(self):
        driver = _FakeDriver(single_results=[{"project_id": None}], captured_runs=[])
        repository = UserStoryRepository(driver)

        result = await repository.get_project_id_by_user_story_id("req_1")

        assert result is None

    @pytest.mark.asyncio
    async def test_set_user_story_status_and_flag_success(self):
        driver = _FakeDriver(single_results=[_make_full_record()], captured_runs=[])
        repository = UserStoryRepository(driver)

        result = await repository.set_user_story_status_and_flag(
            "req_1", status="approved", rfp_flagged_item=None
        )

        assert result is not None
        assert result.id == "req_1"

    @pytest.mark.asyncio
    async def test_set_user_story_status_and_flag_not_found(self):
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = UserStoryRepository(driver)

        result = await repository.set_user_story_status_and_flag(
            "missing", status="approved", rfp_flagged_item=None
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_update_user_story_sync_flags_success(self):
        driver = _FakeDriver(single_results=[_make_full_record()], captured_runs=[])
        repository = UserStoryRepository(driver)

        result = await repository.update_user_story_sync_flags(
            "req_1", is_jira_synced=True, is_tap_synced=None
        )

        assert result is not None

    @pytest.mark.asyncio
    async def test_update_user_story_sync_flags_not_found(self):
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = UserStoryRepository(driver)

        result = await repository.update_user_story_sync_flags("missing", is_jira_synced=True)

        assert result is None

    @pytest.mark.asyncio
    async def test_snapshot_user_story_version_success(self):
        driver = _FakeDriver(single_results=[{"created_count": 1}], captured_runs=[])
        repository = UserStoryRepository(driver)

        assert await repository.snapshot_user_story_version("req_1") is True

    @pytest.mark.asyncio
    async def test_snapshot_user_story_version_missing(self):
        driver = _FakeDriver(single_results=[{"created_count": 0}], captured_runs=[])
        repository = UserStoryRepository(driver)

        assert await repository.snapshot_user_story_version("missing") is False

    @pytest.mark.asyncio
    async def test_get_user_story_version_by_id_found(self):
        driver = _FakeDriver(single_results=[{"version": 4}], captured_runs=[])
        repository = UserStoryRepository(driver)

        result = await repository.get_user_story_version_by_id("req_1")

        assert result == 4

    @pytest.mark.asyncio
    async def test_get_user_story_version_by_id_not_found(self):
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = UserStoryRepository(driver)

        result = await repository.get_user_story_version_by_id("missing")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_user_story_version_by_id_null_version(self):
        driver = _FakeDriver(single_results=[{"version": None}], captured_runs=[])
        repository = UserStoryRepository(driver)

        result = await repository.get_user_story_version_by_id("req_1")

        assert result is None


class TestUserStoryRepositorySourceIngestionId:
    @pytest.mark.asyncio
    async def test_list_user_stories_tree_filters_and_prunes_by_source_ingestion_id(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[[]], captured_runs=captured_runs)
        repository = UserStoryRepository(driver)

        await repository.list_user_stories_tree_for_project(
            project_id=project_id, source_ingestion_id="ingestion-9"
        )

        cypher, params = captured_runs[0]
        # The initial row filter matches at module, feature, OR user-story level —
        # not user-story only — so a module/feature-level match survives even
        # when no descendant user story shares that same source_ingestion_id.
        assert (
            "WHERE $source_ingestion_id IS NULL "
            "   OR m.source_ingestion_id = $source_ingestion_id "
            "   OR f.source_ingestion_id = $source_ingestion_id "
            "   OR r.source_ingestion_id = $source_ingestion_id "
            in cypher
        )
        # Feature-level pruning keeps a feature that itself matches, even with
        # no matching child stories.
        assert (
            "WHERE f IS NULL "
            "   OR $source_ingestion_id IS NULL "
            "   OR f.source_ingestion_id = $source_ingestion_id "
            "   OR size(matched_user_stories) > 0 "
            in cypher
        )
        # Module-level pruning keeps a module that itself matches, even with no
        # matching child features.
        assert (
            "WHERE $source_ingestion_id IS NULL "
            "   OR m.source_ingestion_id = $source_ingestion_id "
            "   OR size(matched_feats) > 0 "
            in cypher
        )
        assert "source_ingestion_id: f.source_ingestion_id" in cypher
        assert "m.source_ingestion_id AS module_source_ingestion_id" in cypher
        assert params["source_ingestion_id"] == "ingestion-9"
        # Soft-deleted user stories are excluded before they ever reach
        # matched_user_stories, regardless of source_ingestion_id filtering.
        assert "CASE WHEN r IS NOT NULL AND r.deleted_at IS NULL AND ( " in cypher

    @pytest.mark.asyncio
    async def test_list_user_stories_tree_no_filter_passes_none(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[[]], captured_runs=captured_runs)
        repository = UserStoryRepository(driver)

        result = await repository.list_user_stories_tree_for_project(project_id=project_id)

        assert result == []
        _, params = captured_runs[0]
        assert params["source_ingestion_id"] is None

    @pytest.mark.asyncio
    async def test_list_user_stories_tree_includes_source_ingestion_id_at_every_level(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(
            single_results=[
                [
                    {
                        "module_id": "module_1",
                        "mod_code": "MOD-001",
                        "module_name": "Module 1",
                        "module_description": None,
                        "incremental_change_type": None,
                        "feedback_change_type": None,
                        "module_source_ingestion_id": "ingestion-module",
                        "children": [
                            {
                                "id": "feature_1",
                                "fea_code": "FEA-001",
                                "name": "Feature 1",
                                "description": None,
                                "incremental_change_type": None,
                                "feedback_change_type": None,
                                "source_ingestion_id": "ingestion-feature",
                                "children": [
                                    {
                                        "id": "req_1",
                                        "user_story_code": "REQ-001",
                                        "name": "Story 1",
                                        "status": "ready",
                                        "incremental_change_type": None,
                                        "feedback_change_type": None,
                                        "is_jira_synced": False,
                                        "is_tap_synced": False,
                                        "source_ingestion_id": "ingestion-story",
                                    }
                                ],
                            }
                        ],
                    }
                ]
            ],
            captured_runs=[],
        )
        repository = UserStoryRepository(driver)

        result = await repository.list_user_stories_tree_for_project(project_id=project_id)

        module = result[0]
        feature = module["children"][0]
        story = feature["children"][0]
        assert module["source_ingestion_id"] == "ingestion-module"
        assert feature["source_ingestion_id"] == "ingestion-feature"
        assert story["source_ingestion_id"] == "ingestion-story"

    @pytest.mark.asyncio
    async def test_list_full_backlog_tree_excludes_soft_deleted_user_stories(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[[]], captured_runs=captured_runs)
        repository = UserStoryRepository(driver)

        result = await repository.list_full_backlog_tree_for_project(project_id=project_id)

        assert result == []
        cypher, _ = captured_runs[0]
        assert "CASE WHEN r IS NOT NULL AND r.deleted_at IS NULL THEN " in cypher

    @pytest.mark.asyncio
    async def test_bulk_upsert_for_project_new_story_stamps_source_ingestion_id(self):
        captured_runs = []
        driver = _FakeDriver(single_results=[{"upserted_count": 1}], captured_runs=captured_runs)
        repository = UserStoryRepository(driver)
        model = UserStoryModel(
            id="req_1",
            user_story_code="ARCH-001",
            title="Core Architecture",
            description=None,
            consensus=0.0,
            status="ready",
            version=1,
            feature_id="feature_1",
            source_ingestion_id="ingestion-1",
        )

        await repository.bulk_upsert_user_stories_for_project(
            project_id=uuid.uuid4(), user_stories=[model]
        )

        cypher, params = captured_runs[0]
        assert "r.source_ingestion_id = CASE WHEN r.source_ingestion_id IS NULL" in cypher
        assert params["user_stories"][0]["source_ingestion_id"] == "ingestion-1"

    @pytest.mark.asyncio
    async def test_bulk_upsert_for_source_code_stamps_source_ingestion_id_unconditionally(self):
        captured_runs = []
        driver = _FakeDriver(single_results=[{"upserted_count": 1}], captured_runs=captured_runs)
        repository = UserStoryRepository(driver)
        model = UserStoryModel(
            id="req_1",
            user_story_code="ARCH-001",
            title="Core Architecture",
            description=None,
            consensus=0.0,
            status="ready",
            version=1,
            feature_id="feature_1",
            source_ingestion_id="ingestion-2",
        )

        await repository.bulk_upsert_user_stories_for_source_code(
            project_id=uuid.uuid4(), user_stories=[model]
        )

        cypher, params = captured_runs[0]
        assert "r.source_ingestion_id = req.source_ingestion_id" in cypher
        assert params["user_stories"][0]["source_ingestion_id"] == "ingestion-2"

    @pytest.mark.asyncio
    async def test_snapshot_user_story_version_captures_source_ingestion_id(self):
        captured_runs = []
        driver = _FakeDriver(single_results=[{"created_count": 1}], captured_runs=captured_runs)
        repository = UserStoryRepository(driver)

        assert await repository.snapshot_user_story_version("req_1") is True
        cypher, _ = captured_runs[0]
        assert "source_ingestion_id: r.source_ingestion_id" in cypher

    @pytest.mark.asyncio
    async def test_accept_user_story_success(self):
        driver = _FakeDriver(single_results=[{"updated_count": 1}], captured_runs=[])
        repository = UserStoryRepository(driver)

        assert await repository.accept_user_story("req_1", "approved") is True

    @pytest.mark.asyncio
    async def test_accept_user_story_feedback_change_success(self):
        driver = _FakeDriver(single_results=[{"updated_count": 1}], captured_runs=[])
        repository = UserStoryRepository(driver)

        assert await repository.accept_user_story_feedback_change("req_1") is True

    @pytest.mark.asyncio
    async def test_accept_user_story_feedback_change_not_found(self):
        driver = _FakeDriver(single_results=[{"updated_count": 0}], captured_runs=[])
        repository = UserStoryRepository(driver)

        assert await repository.accept_user_story_feedback_change("missing") is False

    @pytest.mark.asyncio
    async def test_restore_user_story_from_latest_feedback_version_with_snapshot(self):
        driver = _FakeDriver(
            single_results=[{"updated_count": 1, "restored": True}], captured_runs=[]
        )
        repository = UserStoryRepository(driver)

        assert await repository.restore_user_story_from_latest_feedback_version("req_1") is True

    @pytest.mark.asyncio
    async def test_restore_user_story_from_latest_feedback_version_no_snapshot(self):
        driver = _FakeDriver(
            single_results=[{"updated_count": 1, "restored": False}], captured_runs=[]
        )
        repository = UserStoryRepository(driver)

        assert await repository.restore_user_story_from_latest_feedback_version("req_1") is True

    @pytest.mark.asyncio
    async def test_restore_user_story_from_latest_feedback_version_missing(self):
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = UserStoryRepository(driver)

        assert (
            await repository.restore_user_story_from_latest_feedback_version("missing") is False
        )

    @pytest.mark.asyncio
    async def test_get_latest_user_story_version_found(self):
        driver = _FakeDriver(
            single_results=[
                {
                    "id": str(uuid.uuid4()),
                    "user_story_id": "req_1",
                    "feature_id": "feature_1",
                    "project_id": None,
                    "user_story_code": "ARCH-001",
                    "title": "Core Architecture",
                    "description": "desc",
                    "consensus": 9.8,
                    "status": "approved",
                    "version": 1,
                    "as_a": None,
                    "i_want_to": None,
                    "so_that": None,
                    "acceptance_criteria": None,
                    "nfrs": None,
                    "technical_notes": None,
                    "story_points": None,
                    "justification": None,
                    "incremental_change_type": None,
                    "feedback_change_type": None,
                    "sources": None,
                    "l2_sources": None,
                    "is_jira_synced": False,
                    "is_tap_synced": False,
                    "created_at": None,
                    "updated_at": None,
                    "snapshotted_at": None,
                }
            ],
            captured_runs=[],
        )
        repository = UserStoryRepository(driver)

        version = await repository.get_latest_user_story_version("req_1")

        assert version is not None
        assert version.user_story_id == "req_1"

    @pytest.mark.asyncio
    async def test_get_latest_user_story_version_none(self):
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = UserStoryRepository(driver)

        version = await repository.get_latest_user_story_version("req_1")

        assert version is None

    @pytest.mark.asyncio
    async def test_restore_user_story_from_latest_version_with_snapshot(self):
        driver = _FakeDriver(
            single_results=[{"updated_count": 1, "restored": True}], captured_runs=[]
        )
        repository = UserStoryRepository(driver)

        assert await repository.restore_user_story_from_latest_version("req_1") is True

    @pytest.mark.asyncio
    async def test_restore_user_story_from_latest_version_no_snapshot_still_clears_flags(self):
        driver = _FakeDriver(
            single_results=[{"updated_count": 1, "restored": False}], captured_runs=[]
        )
        repository = UserStoryRepository(driver)

        assert await repository.restore_user_story_from_latest_version("req_1") is True

    @pytest.mark.asyncio
    async def test_restore_user_story_from_latest_version_missing(self):
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = UserStoryRepository(driver)

        assert await repository.restore_user_story_from_latest_version("missing") is False

    @pytest.mark.asyncio
    async def test_mark_user_story_delete_suggested_success(self):
        captured_runs = []
        driver = _FakeDriver(single_results=[_make_full_record()], captured_runs=captured_runs)
        repository = UserStoryRepository(driver)

        result = await repository.mark_user_story_delete_suggested("req_1", "why")

        assert result is not None
        cypher, params = captured_runs[0]
        assert params["justification"] == "why"
        assert params["source_ingestion_id"] is None
        assert "r.version = coalesce(r.version, $version) + 1" in cypher
        assert params["version"] == INITIAL_ENTITY_VERSION

    @pytest.mark.asyncio
    async def test_mark_user_story_delete_suggested_stamps_source_ingestion_id(self):
        captured_runs = []
        driver = _FakeDriver(single_results=[_make_full_record()], captured_runs=captured_runs)
        repository = UserStoryRepository(driver)

        await repository.mark_user_story_delete_suggested(
            "req_1", "why", source_ingestion_id="ingestion-1"
        )

        cypher, params = captured_runs[0]
        assert "r.source_ingestion_id = coalesce($source_ingestion_id, r.source_ingestion_id)" in cypher
        assert params["source_ingestion_id"] == "ingestion-1"

    @pytest.mark.asyncio
    async def test_mark_user_story_delete_suggested_not_found(self):
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = UserStoryRepository(driver)

        result = await repository.mark_user_story_delete_suggested("missing", "why")

        assert result is None

    @pytest.mark.asyncio
    async def test_update_user_story_sources_success(self):
        driver = _FakeDriver(single_results=[_make_full_record()], captured_runs=[])
        repository = UserStoryRepository(driver)

        result = await repository.update_user_story_sources(
            "req_1", [{"source_id": "s1", "pages": []}]
        )

        assert result is not None

    @pytest.mark.asyncio
    async def test_update_user_story_sources_not_found(self):
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = UserStoryRepository(driver)

        result = await repository.update_user_story_sources("missing", [])

        assert result is None

    @pytest.mark.asyncio
    async def test_update_user_story_bboxes_delegates_to_sources(self):
        driver = _FakeDriver(single_results=[_make_full_record()], captured_runs=[])
        repository = UserStoryRepository(driver)

        result = await repository.update_user_story_bboxes(
            "req_1", [{"source_id": "s1", "pages": []}]
        )

        assert result is not None

    @pytest.mark.asyncio
    async def test_change_all_user_story_status_by_project(self):
        driver = _FakeDriver(single_results=[{"updated_count": 5}], captured_runs=[])
        repository = UserStoryRepository(driver)

        count = await repository.change_all_user_story_status_by_project("proj-1", "approved")

        assert count == 5

    @pytest.mark.asyncio
    async def test_change_all_user_story_status_by_project_no_record_returns_zero(self):
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = UserStoryRepository(driver)

        count = await repository.change_all_user_story_status_by_project("proj-1", "approved")

        assert count == 0

    @pytest.mark.asyncio
    async def test_bulk_change_user_story_status_by_ids(self):
        driver = _FakeDriver(single_results=[{"updated_count": 2}], captured_runs=[])
        repository = UserStoryRepository(driver)

        count = await repository.bulk_change_user_story_status_by_ids(
            "proj-1", ["req_1", "req_2"], "approved"
        )

        assert count == 2

    @pytest.mark.asyncio
    async def test_get_max_user_story_code_returns_suffix(self):
        driver = _FakeDriver(
            single_results=[{"fea_code": "FEA_001", "max_suffix": 3}], captured_runs=[]
        )
        repository = UserStoryRepository(driver)

        fea_code, suffix = await repository.get_max_user_story_code(uuid.uuid4(), "feature_1")

        assert fea_code == "FEA_001"
        assert suffix == 3

    @pytest.mark.asyncio
    async def test_get_max_user_story_code_feature_missing(self):
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = UserStoryRepository(driver)

        fea_code, suffix = await repository.get_max_user_story_code(uuid.uuid4(), "missing")

        assert fea_code == ""
        assert suffix == 0


class TestUserStoryRepositorySyncCandidates:
    @pytest.mark.asyncio
    async def test_list_sync_candidate_tree_cypher_filters_status_and_sync_flag(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[[]], captured_runs=captured_runs)
        repository = UserStoryRepository(driver)

        await repository.list_sync_candidate_tree_for_project(
            project_id=project_id, sync_target="jira"
        )

        cypher, params = captured_runs[0]
        assert (
            "WHERE r IS NULL OR ( "
            "    r.status IN ['approved', 'deleted'] "
            "    AND coalesce(r.is_current, true) = true "
            "    AND ( "
            "        ($sync_target = 'jira' AND coalesce(r.is_jira_synced, false) = false) OR "
            "        ($sync_target = 'tap'  AND coalesce(r.is_tap_synced,  false) = false) "
            "    ) "
            ") "
            in cypher
        )
        # Unlike the source_ingestion_id tree, an empty feature/module is
        # dropped unconditionally — no "keep empty parent" fallback.
        assert "WHERE f IS NULL OR size(matched_user_stories) > 0 " in cypher
        assert "WHERE size(matched_feats) > 0 " in cypher
        # version is selected so the Sync Tray can display it without a
        # per-story detail fetch (the regression this method exists to fix).
        assert "version: r.version" in cypher
        assert params["sync_target"] == "jira"
        assert params["project_id"] == str(project_id)

    @pytest.mark.asyncio
    async def test_list_sync_candidate_tree_passes_tap_target(self):
        captured_runs = []
        driver = _FakeDriver(single_results=[[]], captured_runs=captured_runs)
        repository = UserStoryRepository(driver)

        result = await repository.list_sync_candidate_tree_for_project(
            project_id=uuid.uuid4(), sync_target="tap"
        )

        assert result == []
        _, params = captured_runs[0]
        assert params["sync_target"] == "tap"

    @pytest.mark.asyncio
    async def test_list_sync_candidate_tree_shapes_nested_rows(self):
        driver = _FakeDriver(
            single_results=[
                [
                    {
                        "module_id": "module_1",
                        "mod_code": "MOD-001",
                        "module_name": "Module 1",
                        "module_description": None,
                        "incremental_change_type": None,
                        "feedback_change_type": None,
                        "module_source_ingestion_id": None,
                        "children": [
                            {
                                "id": "feature_1",
                                "fea_code": "FEA-001",
                                "name": "Feature 1",
                                "description": None,
                                "incremental_change_type": None,
                                "feedback_change_type": None,
                                "source_ingestion_id": None,
                                "children": [
                                    {
                                        "id": "req_1",
                                        "user_story_code": "REQ-001",
                                        "name": "Story 1",
                                        "status": "approved",
                                        "version": 2,
                                        "incremental_change_type": None,
                                        "feedback_change_type": None,
                                        "is_jira_synced": False,
                                        "is_tap_synced": True,
                                        "source_ingestion_id": None,
                                    }
                                ],
                            }
                        ],
                    }
                ]
            ],
            captured_runs=[],
        )
        repository = UserStoryRepository(driver)

        result = await repository.list_sync_candidate_tree_for_project(
            project_id=uuid.uuid4(), sync_target="jira"
        )

        module = result[0]
        feature = module["children"][0]
        story = feature["children"][0]
        assert module["mod_code"] == "MOD-001"
        assert feature["fea_code"] == "FEA-001"
        assert story["user_story_code"] == "REQ-001"
        assert story["version"] == 2
        assert story["is_jira_synced"] is False
        assert story["is_tap_synced"] is True
