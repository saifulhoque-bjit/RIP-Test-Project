"""Unit tests for SourceRepository pure-function helpers.

These tests require no live Neo4j connection — they cover the metadata
normalisation and serialisation methods that were the source of a
production bug where _normalize_source_node returned the project UUID
as the Source node's `id`, causing every Level-1 source projection to be
stored under the wrong id and making the processing pipeline unable to
locate the Source node for fragment creation.
"""

from __future__ import annotations

from datetime import UTC, datetime
import uuid

import pytest

from app.models.neo4j.source_model import SourceNode
from app.repositories.neo4j.source_repository import SourceRepository

# ── Fake Neo4j driver helpers ──────────────────────────────────────────────


class _FakeResult:
    def __init__(self, single_result):
        self._single_result = single_result

    def single(self):
        return self._single_result

    def consume(self):
        return None


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

    def execute_write(self, callback):
        return callback(_FakeTx(self._single_results, self._captured_runs))

    def run(self, cypher, params):
        """Mirrors `SourceRepository._execute_write`'s direct `session.run(cypher, params)` call."""
        self._captured_runs.append((cypher, params))
        return _FakeResult(None)


class _FakeDriver:
    def __init__(self, single_results, captured_runs):
        self._single_results = single_results
        self._captured_runs = captured_runs

    def session(self):
        return _FakeSession(self._single_results, self._captured_runs)


# ── Helpers ────────────────────────────────────────────────────────────────


def _repo() -> SourceRepository:
    """Return a repository instance without a real Neo4j driver."""
    repo = SourceRepository.__new__(SourceRepository)
    return repo


def _make_node(
    source_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
) -> SourceNode:
    return SourceNode(
        id=source_id or uuid.uuid4(),
        project_id=project_id or uuid.uuid4(),
        name="spec.pdf",
        file_type="pdf",
        file_path="projects/proj/sources/src/spec.pdf",
        status="uploaded",
        checksum_sha256="abc123",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 2, tzinfo=UTC),
    )


# ── _normalize_source_node ─────────────────────────────────────────────────


class TestNormalizeSourceNode:
    """Critical regression tests for _normalize_source_node."""

    def test_id_is_source_id_not_project_id(self):
        """The `id` key in the normalised row MUST be the source UUID.

        Previously this was incorrectly set to str(project_id), which caused
        Source nodes to be stored in Neo4j under the wrong id and made the
        fragment-creation pipeline fail with 'Source node not found'.
        """
        repo = _repo()
        source_id = uuid.uuid4()
        project_id = uuid.uuid4()

        node = _make_node(source_id=source_id, project_id=project_id)
        result = repo._normalize_source_node(node)

        assert result["id"] == str(source_id), (
            f"Expected source_id={source_id!r} but got {result['id']!r}. "
            "This is the regression that caused 'Source node not found in Neo4j'."
        )
        assert result["id"] != str(project_id)

    def test_project_id_is_present_in_row(self):
        """The normalised row must include `project_id` so the Cypher can write
        `SET s.project_id = row.project_id` and merge the Project node."""
        repo = _repo()
        source_id = uuid.uuid4()
        project_id = uuid.uuid4()

        node = _make_node(source_id=source_id, project_id=project_id)
        result = repo._normalize_source_node(node)

        assert "project_id" in result, (
            "Missing 'project_id' key — the Cypher FOREACH and SET s.project_id "
            "would receive null, skipping Project node creation."
        )
        assert result["project_id"] == str(project_id)

    def test_source_properties_preserved(self):
        """Source node properties must be present."""
        repo = _repo()
        node = _make_node()
        result = repo._normalize_source_node(node)

        assert result["properties"]["name"] == "spec.pdf"
        assert result["properties"]["file_type"] == "pdf"
        assert result["properties"]["status"] == "uploaded"
        assert result["properties"]["checksum_sha256"] == "abc123"

    def test_none_property_values_are_dropped(self):
        """None values in properties must be omitted to avoid overwriting
        existing Neo4j properties with null during MERGE SET."""
        repo = _repo()
        node = _make_node()
        node = SourceNode(
            id=node.id,
            project_id=node.project_id,
            name=node.name,
            file_type=node.file_type,
            status=node.status,
            file_path=None,
            checksum_sha256=None,
            created_at=node.created_at,
            updated_at=node.updated_at,
        )

        result = repo._normalize_source_node(node)

        assert "checksum_sha256" not in result["properties"]
        assert "file_path" not in result["properties"]

    def test_missing_id_raises(self):
        repo = _repo()
        node = _make_node()
        node = SourceNode(
            id=None,  # type: ignore[arg-type]
            project_id=node.project_id,
            name=node.name,
            file_type=node.file_type,
            status=node.status,
        )
        with pytest.raises(ValueError, match="'id'"):
            repo._normalize_source_node(node)

    def test_missing_project_id_raises(self):
        repo = _repo()
        node = _make_node()
        node = SourceNode(
            id=node.id,
            project_id=None,  # type: ignore[arg-type]
            name=node.name,
            file_type=node.file_type,
            status=node.status,
        )
        with pytest.raises(ValueError, match="'project_id'"):
            repo._normalize_source_node(node)


# ── TestOrmToNode ──────────────────────────────────────────────────────────


class TestOrmToNode:
    """Tests for SourceRepository.orm_to_node static converter."""

    def test_orm_to_node_maps_all_fields(self):
        """orm_to_node must copy every Source ORM field to the SourceNode."""
        from unittest.mock import MagicMock

        source = MagicMock()
        source.id = uuid.uuid4()
        source.project_id = uuid.uuid4()
        source.original_name = "spec.pdf"
        source.file_type = "PDF"
        source.mime_type = "application/pdf"
        source.storage_key = "projects/p/sources/s/spec.pdf"
        source.status = "uploaded"
        source.checksum_sha256 = "abc123"
        source.created_at = datetime(2026, 1, 1, tzinfo=UTC)
        source.updated_at = datetime(2026, 1, 2, tzinfo=UTC)

        node = SourceRepository.orm_to_node(source)

        assert node.id == source.id
        assert node.project_id == source.project_id
        assert node.name == "spec.pdf"
        assert node.file_type == "pdf"  # lowercased
        assert node.mime_type == "application/pdf"
        assert node.file_path == source.storage_key
        assert node.status == source.status
        assert node.checksum_sha256 == source.checksum_sha256
        assert node.created_at == source.created_at
        assert node.updated_at == source.updated_at


# ── TestDeleteSourceNode ──────────────────────────────────────────────────


class TestDeleteSourceNode:
    @pytest.mark.asyncio
    async def test_delete_source_node_removes_module_feature_and_orphan_user_stories(self):
        source_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(
            single_results=[{"deleted": 1}],
            captured_runs=captured_runs,
        )
        repository = SourceRepository(driver)

        deleted = await repository.delete_source_node(source_id)

        assert deleted == 1
        assert len(captured_runs) == 1
        cypher, params = captured_runs[0]
        assert params["source_id"] == str(source_id)
        assert "OPTIONAL MATCH (s)<-[:HAS_SOURCE]-(:Project)-[:HAS_MODULE]->(m:Module)" in cypher
        assert "WHERE m.source_id = $source_id" in cypher
        assert "OPTIONAL MATCH (m)-[:HAS_FEATURE]->(ft:Feature)" in cypher
        assert "FOREACH (ft IN features | DETACH DELETE ft)" in cypher
        assert "FOREACH (m IN modules | DETACH DELETE m)" in cypher
        assert "WHERE NOT EXISTS { (:Feature)-[:HAS_USER_STORY]->(r) }" in cypher
        assert "OPTIONAL MATCH (r:UserStory)" in cypher

    @pytest.mark.asyncio
    async def test_delete_source_node_returns_zero_when_not_found(self):
        source_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(
            single_results=[None],
            captured_runs=captured_runs,
        )
        repository = SourceRepository(driver)

        deleted = await repository.delete_source_node(source_id)

        assert deleted == 0


class TestUpsertFileNode:
    @pytest.mark.asyncio
    async def test_writes_file_node_with_expected_params(self):
        captured_runs = []
        driver = _FakeDriver(single_results=[], captured_runs=captured_runs)
        repository = SourceRepository(driver)
        node = SourceNode(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            name="doc.pdf",
            file_type="pdf",
            mime_type="application/pdf",
            file_path="key/doc.pdf",
            checksum_sha256="abc",
            status="uploaded",
        )

        await repository.upsert_file_node(node)

        cypher, params = captured_runs[0]
        assert "MERGE (f:Source" in cypher
        assert params["original_name"] == "doc.pdf"
        assert params["source_id"] == str(node.id)


class TestUpsertUiElements:
    @pytest.mark.asyncio
    async def test_writes_one_node_per_element(self):
        captured_runs = []
        driver = _FakeDriver(single_results=[], captured_runs=captured_runs)
        repository = SourceRepository(driver)
        elements = [
            {
                "type": "button",
                "bbox": [0, 0, 1, 1],
                "content": "OK",
                "interactivity": True,
                "source": "yolo",
            },
            {"type": "text", "bbox": [1, 1, 2, 2], "content": "Label"},
        ]

        await repository.upsert_ui_elements(uuid.uuid4(), elements)

        assert len(captured_runs) == 2
        _, params0 = captured_runs[0]
        assert params0["element_type"] == "button"
        assert params0["interactivity"] is True
        _, params1 = captured_runs[1]
        assert params1["element_type"] == "text"
        assert params1["interactivity"] is False

    @pytest.mark.asyncio
    async def test_empty_elements_writes_nothing(self):
        captured_runs = []
        driver = _FakeDriver(single_results=[], captured_runs=captured_runs)
        repository = SourceRepository(driver)

        await repository.upsert_ui_elements(uuid.uuid4(), [])

        assert captured_runs == []


class TestUpsertCodeEntities:
    @pytest.mark.asyncio
    async def test_writes_entity_and_calls_and_depends_on(self):
        captured_runs = []
        driver = _FakeDriver(single_results=[], captured_runs=captured_runs)
        repository = SourceRepository(driver)
        entities = [
            {
                "name": "do_thing",
                "kind": "function",
                "start_line": 1,
                "end_line": 10,
                "language": "python",
                "calls": ["helper"],
                "depends_on": ["SomeClass"],
            }
        ]

        await repository.upsert_code_entities(uuid.uuid4(), entities)

        # 1 entity write + 1 CALLS write + 1 DEPENDS_ON write
        assert len(captured_runs) == 3
        entity_cypher, entity_params = captured_runs[0]
        assert entity_params["name"] == "do_thing"
        calls_cypher, calls_params = captured_runs[1]
        assert "CALLS" in calls_cypher
        assert calls_params["callee_name"] == "helper"
        dep_cypher, dep_params = captured_runs[2]
        assert "DEPENDS_ON" in dep_cypher
        assert dep_params["dep_name"] == "SomeClass"

    @pytest.mark.asyncio
    async def test_class_kind_uses_class_label(self):
        captured_runs = []
        driver = _FakeDriver(single_results=[], captured_runs=captured_runs)
        repository = SourceRepository(driver)
        entities = [{"name": "MyClass", "kind": "class", "start_line": 1}]

        await repository.upsert_code_entities(uuid.uuid4(), entities)

        cypher, _ = captured_runs[0]
        assert "MERGE (e:Class" in cypher

    @pytest.mark.asyncio
    async def test_empty_entities_writes_nothing(self):
        captured_runs = []
        driver = _FakeDriver(single_results=[], captured_runs=captured_runs)
        repository = SourceRepository(driver)

        await repository.upsert_code_entities(uuid.uuid4(), [])

        assert captured_runs == []


class TestCreateLevel1Sources:
    @pytest.mark.asyncio
    async def test_empty_nodes_is_a_noop(self):
        driver = _FakeDriver(single_results=[], captured_runs=[])
        repository = SourceRepository(driver)

        await repository.create_level1_sources([])

    @pytest.mark.asyncio
    async def test_merges_batch_of_nodes(self):
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = SourceRepository(driver)
        node = SourceNode(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            name="doc.pdf",
            file_type="pdf",
            status="uploaded",
        )

        await repository.create_level1_sources([node])


class TestSerializeTemporal:
    def test_none_returns_none(self):
        assert SourceRepository._serialize_temporal(None) is None

    def test_datetime_returns_isoformat(self):
        dt = datetime(2026, 1, 1, tzinfo=UTC)
        assert SourceRepository._serialize_temporal(dt) == dt.isoformat()

    def test_other_type_returns_str(self):
        assert SourceRepository._serialize_temporal(123) == "123"
