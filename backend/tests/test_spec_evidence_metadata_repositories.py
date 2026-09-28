"""Unit tests for the small spec/evidence/metadata Neo4j repositories:
ConfigSpecRepository, GroupSpecRepository, SRSEvidenceRepository,
SourceCodeMetadataRepository, ProjectMetadataRepository.
"""

from __future__ import annotations

import json
import uuid

from app.repositories.neo4j.config_spec_repository import ConfigSpecRepository
from app.repositories.neo4j.group_spec_repository import GroupSpecRepository
from app.repositories.neo4j.project_metadata_repository import ProjectMetadataRepository
from app.repositories.neo4j.source_code_metadata_repository import SourceCodeMetadataRepository
from app.repositories.neo4j.srs_evidence_repository import SRSEvidenceRepository


class _FakeResult:
    def __init__(self, single_result):
        self._single_result = single_result

    def single(self):
        return self._single_result

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
    def __init__(self, single_results, captured_runs=None):
        self._single_results = single_results
        self._captured_runs = captured_runs if captured_runs is not None else []

    def session(self):
        return _FakeSession(self._single_results, self._captured_runs)


class TestConfigSpecRepository:
    async def test_empty_rows_short_circuits(self):
        repo = ConfigSpecRepository(_FakeDriver([]))

        result = await repo.upsert_many_for_project(project_id="proj-1", rows=None)

        assert result == 0

    async def test_upserts_rows_and_returns_count(self):
        repo = ConfigSpecRepository(_FakeDriver([{"count": 2}]))

        result = await repo.upsert_many_for_project(
            project_id="proj-1", rows=[{"id": "cs-1"}, {"id": "cs-2"}]
        )

        assert result == 2

    async def test_returns_zero_when_no_record(self):
        repo = ConfigSpecRepository(_FakeDriver([None]))

        result = await repo.upsert_many_for_project(project_id="proj-1", rows=[{"id": "cs-1"}])

        assert result == 0


class TestGroupSpecRepository:
    async def test_list_by_project_returns_models(self):
        node = {
            "id": "gs-1",
            "mod_code": "MOD_001",
            "fea_code": "FEA_001",
            "filename": "spec.md",
            "storage_key": "key1",
            "source_id": "src-1",
            "project_id": "proj-1",
        }
        repo = GroupSpecRepository(_FakeDriver([[{"gs": node}]]))

        result = await repo.list_by_project("proj-1")

        assert len(result) == 1
        assert result[0].id == "gs-1"
        assert result[0].filename == "spec.md"

    async def test_upsert_many_empty_rows_short_circuits(self):
        repo = GroupSpecRepository(_FakeDriver([]))

        result = await repo.upsert_many_for_project(project_id="proj-1", rows=None)

        assert result == 0

    async def test_upsert_many_returns_count(self):
        repo = GroupSpecRepository(_FakeDriver([{"count": 1}]))

        result = await repo.upsert_many_for_project(project_id="proj-1", rows=[{"id": "gs-1"}])

        assert result == 1


class TestSRSEvidenceRepository:
    async def test_empty_rows_short_circuits(self):
        repo = SRSEvidenceRepository(_FakeDriver([]))

        result = await repo.upsert_many_for_project(project_id="proj-1", rows=None)

        assert result == 0

    async def test_upserts_rows_and_returns_count(self):
        repo = SRSEvidenceRepository(_FakeDriver([{"count": 3}]))

        result = await repo.upsert_many_for_project(
            project_id="proj-1", rows=[{"id": "e-1"}, {"id": "e-2"}, {"id": "e-3"}]
        )

        assert result == 3

    async def test_returns_zero_when_no_record(self):
        repo = SRSEvidenceRepository(_FakeDriver([None]))

        result = await repo.upsert_many_for_project(project_id="proj-1", rows=[{"id": "e-1"}])

        assert result == 0


class TestSourceCodeMetadataRepository:
    async def test_get_by_project_and_module_found(self):
        record = {
            "id": "scm-1",
            "module_id": "MOD_001",
            "module_response": json.dumps({"a": 1}),
            "module_manifest": json.dumps({"b": 2}),
            "source_id": "src-1",
            "project_id": "proj-1",
            "created_at": None,
            "updated_at": None,
        }
        repo = SourceCodeMetadataRepository(_FakeDriver([record]))

        result = await repo.get_by_project_and_module(project_id="proj-1", module_id="MOD_001")

        assert result is not None
        assert result.module_response == {"a": 1}
        assert result.module_manifest == {"b": 2}

    async def test_get_by_project_and_module_not_found(self):
        repo = SourceCodeMetadataRepository(_FakeDriver([None]))

        result = await repo.get_by_project_and_module(project_id="proj-1", module_id="missing")

        assert result is None

    async def test_get_by_project_and_module_null_json_fields(self):
        record = {
            "id": "scm-1",
            "module_id": "MOD_001",
            "module_response": None,
            "module_manifest": None,
            "source_id": "src-1",
            "project_id": "proj-1",
            "created_at": None,
            "updated_at": None,
        }
        repo = SourceCodeMetadataRepository(_FakeDriver([record]))

        result = await repo.get_by_project_and_module(project_id="proj-1", module_id="MOD_001")

        assert result.module_response is None
        assert result.module_manifest is None

    async def test_upsert_many_empty_rows_short_circuits(self):
        repo = SourceCodeMetadataRepository(_FakeDriver([]))

        result = await repo.upsert_many_for_project(project_id="proj-1", rows=None)

        assert result == 0

    async def test_upsert_many_returns_count(self):
        repo = SourceCodeMetadataRepository(_FakeDriver([{"count": 1}]))

        result = await repo.upsert_many_for_project(
            project_id="proj-1", rows=[{"id": "scm-1", "module_id": "MOD_001"}]
        )

        assert result == 1


class TestProjectMetadataRepository:
    def test_upsert(self):
        captured_runs = []
        repo = ProjectMetadataRepository(_FakeDriver([{"id": "pm-1"}], captured_runs))

        repo.upsert(project_id=uuid.uuid4())

        cypher, _ = captured_runs[0]
        assert "MERGE (pm:ProjectMetadata" in cypher

    def test_update_only_provided_fields(self):
        captured_runs = []
        repo = ProjectMetadataRepository(_FakeDriver([{"id": "pm-1"}], captured_runs))

        repo.update(project_id=uuid.uuid4(), exclusions=["legacy module"])

        _, params = captured_runs[0]
        assert params["exclusions_json"] == json.dumps(["legacy module"], ensure_ascii=False)
        assert params["business_requirements_json"] is None
        assert params["persona_glossary_json"] is None
        assert params["domain_knowledge_storage_key"] is None
        assert params["architecture_document_storage_key"] is None

    def test_update_storage_keys(self):
        captured_runs = []
        repo = ProjectMetadataRepository(_FakeDriver([{"id": "pm-1"}], captured_runs))

        repo.update(
            project_id=uuid.uuid4(),
            domain_knowledge_storage_key="projects/p-1/pipeline-artifacts/domain_knowledge.md",
        )

        _, params = captured_runs[0]
        assert (
            params["domain_knowledge_storage_key"]
            == "projects/p-1/pipeline-artifacts/domain_knowledge.md"
        )
        assert params["architecture_document_storage_key"] is None
        assert params["exclusions_json"] is None

    def test_get_returns_parsed_lists(self):
        record = {
            "br": json.dumps([{"id": "br-1"}]),
            "excl": json.dumps(["legacy"]),
            "pg": json.dumps([{"term": "MFU"}]),
            "domain_knowledge_storage_key": "projects/p-1/pipeline-artifacts/domain_knowledge.md",
            "architecture_document_storage_key": None,
        }
        repo = ProjectMetadataRepository(_FakeDriver([record]))

        result = repo.get(project_id=uuid.uuid4())

        assert result["business_requirements"] == [{"id": "br-1"}]
        assert result["exclusions"] == ["legacy"]
        assert result["persona_glossary"] == [{"term": "MFU"}]
        assert (
            result["domain_knowledge_storage_key"]
            == "projects/p-1/pipeline-artifacts/domain_knowledge.md"
        )
        assert result["architecture_document_storage_key"] is None

    def test_get_returns_empty_lists_when_node_missing(self):
        repo = ProjectMetadataRepository(_FakeDriver([None]))

        result = repo.get(project_id=uuid.uuid4())

        assert result == {
            "business_requirements": [],
            "exclusions": [],
            "persona_glossary": [],
            "domain_knowledge_storage_key": None,
            "architecture_document_storage_key": None,
        }

    def test_get_handles_invalid_json_gracefully(self):
        record = {
            "br": "not json",
            "excl": None,
            "pg": json.dumps({"not": "a list"}),
            "domain_knowledge_storage_key": None,
            "architecture_document_storage_key": None,
        }
        repo = ProjectMetadataRepository(_FakeDriver([record]))

        result = repo.get(project_id=uuid.uuid4())

        assert result["business_requirements"] == []
        assert result["exclusions"] == []
        assert result["persona_glossary"] == []
