"""Unit tests for FragmentRepository."""

from __future__ import annotations

import uuid

import pytest

from app.core.exceptions import NotFoundError
from app.models.neo4j.fragment_model import (
    FragmentBBoxCoordinatesModel,
    FragmentBBoxModel,
    FragmentModel,
)
from app.repositories.neo4j.fragment_repository import FragmentRepository


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
    def __init__(self, single_results, captured_runs):
        self._single_results = single_results
        self._captured_runs = captured_runs

    def session(self):
        return _FakeSession(self._single_results, self._captured_runs)


def _make_fragment(source_id: uuid.UUID) -> FragmentModel:
    return FragmentModel(
        id="fragment-1",
        source_id=source_id,
        frag_type="text",
        content="Fragment content",
        bbox=[
            FragmentBBoxModel(
                page=1,
                bbox=FragmentBBoxCoordinatesModel(x=1.0, y=2.0, w=3.0, h=4.0),
            )
        ],
        content_hash="hash-1",
    )


class TestFragmentRepository:
    @pytest.mark.asyncio
    async def test_create_fragments_for_source_success(self):
        captured_runs = []
        source_id = uuid.uuid4()
        driver = _FakeDriver(
            single_results=[
                {"source_id": "src-1"},
                [
                    {
                        "id": "fragment-1",
                        "source_id": str(source_id),
                        "frag_type": "text",
                        "content": "Fragment content",
                        "bbox": '[{"page":1,"bbox":{"x":1.0,"y":2.0,"w":3.0,"h":4.0},"confidence":null}]',
                        "content_hash": "hash-1",
                    }
                ],
            ],
            captured_runs=captured_runs,
        )
        repository = FragmentRepository(driver)

        created_fragments = await repository.create_fragments_for_source(
            source_id,
            [_make_fragment(source_id)],
        )

        assert [f.id for f in created_fragments] == ["fragment-1"]
        assert len(captured_runs) == 2
        assert "MATCH (s:Source {id: $source_id})" in captured_runs[0][0]
        assert "MERGE (f:Fragment {id: row.id})" in captured_runs[1][0]
        assert captured_runs[1][1]["rows"][0]["source_id"] == str(source_id)
        assert captured_runs[1][1]["rows"][0]["source_type"] is None

    @pytest.mark.asyncio
    async def test_create_fragments_for_source_graph_source_missing(self):
        source_id = uuid.uuid4()
        repository = FragmentRepository(_FakeDriver(single_results=[None], captured_runs=[]))

        with pytest.raises(NotFoundError):
            await repository.create_fragments_for_source(
                source_id,
                [_make_fragment(source_id)],
            )

    @pytest.mark.asyncio
    async def test_list_fragments_for_source_success(self):
        source_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(
            single_results=[
                {"source_id": str(source_id)},
                [
                    {
                        "id": "fragment-1",
                        "source_id": str(source_id),
                        "frag_type": "text",
                        "content": "List content",
                        "bbox": '[{"page":1,"bbox":{"x":1.0,"y":2.0,"w":3.0,"h":4.0},"confidence":0.9}]',
                        "content_hash": "hash-list",
                    }
                ],
            ],
            captured_runs=captured_runs,
        )
        repository = FragmentRepository(driver)

        fragments = await repository.list_fragments_for_source(source_id)

        assert len(fragments) == 1
        assert fragments[0].id == "fragment-1"
        assert fragments[0].frag_type == "text"
        assert fragments[0].source_type is None

    @pytest.mark.asyncio
    async def test_list_fragments_for_source_returns_source_type_when_present(self):
        source_id = uuid.uuid4()
        driver = _FakeDriver(
            single_results=[
                {"source_id": str(source_id)},
                [
                    {
                        "id": "fragment-1",
                        "source_id": str(source_id),
                        "frag_type": "text",
                        "source_type": "pdf",
                        "content": "List content",
                        "bbox": '[{"page":1,"bbox":{"x":1.0,"y":2.0,"w":3.0,"h":4.0},"confidence":0.9}]',
                        "content_hash": "hash-list",
                    }
                ],
            ],
            captured_runs=[],
        )
        repository = FragmentRepository(driver)

        fragments = await repository.list_fragments_for_source(source_id)

        assert len(fragments) == 1
        assert fragments[0].source_type == "pdf"

    @pytest.mark.asyncio
    async def test_get_fragment_for_project_success(self):
        project_id = uuid.uuid4()
        source_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(
            single_results=[
                {
                    "id": "fragment-1",
                    "source_id": str(source_id),
                    "frag_type": "text",
                    "content": "View content",
                    "bbox": '[{"page":1,"bbox":{"x":1.0,"y":2.0,"w":3.0,"h":4.0},"confidence":0.9}]',
                    "content_hash": "hash-view",
                },
            ],
            captured_runs=captured_runs,
        )
        repository = FragmentRepository(driver)

        fragment = await repository.get_fragment_for_project(project_id, "fragment-1")

        assert fragment is not None
        assert fragment.id == "fragment-1"
        assert fragment.frag_type == "text"

    async def test_get_fragment_for_project_not_found(self):
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = FragmentRepository(driver)

        result = await repository.get_fragment_for_project(uuid.uuid4(), "missing")

        assert result is None

    async def test_update_fragment_bbox_success(self):
        source_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(
            single_results=[
                {"source_id": str(source_id)},  # _ensure_source_node_exists
                {
                    "id": "fragment-1",
                    "source_id": str(source_id),
                    "frag_type": "text",
                    "source_type": None,
                    "content": "hello",
                    "bbox": "[]",
                    "content_hash": "hash",
                    "created_at": None,
                    "updated_at": None,
                },
            ],
            captured_runs=captured_runs,
        )
        repository = FragmentRepository(driver)

        result = await repository.update_fragment_bbox(source_id, "fragment-1", "[]")

        assert result is not None
        assert result.id == "fragment-1"

    async def test_update_fragment_bbox_source_not_found_raises(self):
        driver = _FakeDriver(single_results=[None], captured_runs=[])
        repository = FragmentRepository(driver)

        with pytest.raises(NotFoundError):
            await repository.update_fragment_bbox(uuid.uuid4(), "fragment-1", "[]")

    async def test_update_fragment_bbox_fragment_not_found_returns_none(self):
        source_id = uuid.uuid4()
        driver = _FakeDriver(
            single_results=[{"source_id": str(source_id)}, None],
            captured_runs=[],
        )
        repository = FragmentRepository(driver)

        result = await repository.update_fragment_bbox(source_id, "missing", "[]")

        assert result is None

    async def test_list_fragments_for_project_success(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(
            single_results=[
                {"total": 1},
                [
                    {
                        "id": "fragment-1",
                        "source_id": str(uuid.uuid4()),
                        "frag_type": "text",
                        "source_type": None,
                        "content": "hello",
                        "bbox": "[]",
                        "content_hash": "hash",
                        "created_at": None,
                        "updated_at": None,
                    }
                ],
            ],
            captured_runs=[],
        )
        repository = FragmentRepository(driver)

        total, fragments = await repository.list_fragments_for_project(project_id)

        assert total == 1
        assert len(fragments) == 1
        assert fragments[0].id == "fragment-1"

    async def test_list_fragments_for_project_no_matches(self):
        project_id = uuid.uuid4()
        driver = _FakeDriver(single_results=[{"total": 0}, []], captured_runs=[])
        repository = FragmentRepository(driver)

        total, fragments = await repository.list_fragments_for_project(project_id)

        assert total == 0
        assert fragments == []

    async def test_list_fragments_for_project_applies_filters(self):
        project_id = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver(single_results=[{"total": 0}, []], captured_runs=captured_runs)
        repository = FragmentRepository(driver)

        await repository.list_fragments_for_project(
            project_id, source_id=uuid.uuid4(), frag_type="text", content="Hello", skip=5, limit=10
        )

        _, params = captured_runs[0]
        assert params["content"] == "hello"
        assert params["frag_type"] == "text"
        assert params["skip"] == 5
        assert params["limit"] == 10


class TestFragmentOrdering:
    """Fragment ids are uuid5 hashes and every fragment in a batch is written
    with one shared ``created_at``, so ``ORDER BY created_at, id`` handed the
    LLM a shuffled document. Order must come from the persisted position_index.
    """

    @pytest.mark.asyncio
    async def test_write_batch_persists_position_index(self):
        captured_runs = []
        source_id = uuid.uuid4()
        fragment = _make_fragment(source_id)
        fragment.position_index = 7
        driver = _FakeDriver(
            single_results=[{"source_id": "src-1"}, []],
            captured_runs=captured_runs,
        )

        await FragmentRepository(driver).create_fragments_for_source(source_id, [fragment])

        cypher, params = captured_runs[1]
        assert "f.position_index = row.position_index" in cypher
        assert params["rows"][0]["position_index"] == 7

    @pytest.mark.asyncio
    async def test_list_for_source_orders_by_position_index(self):
        captured_runs = []
        source_id = uuid.uuid4()
        driver = _FakeDriver(
            single_results=[{"source_id": "src-1"}, []],
            captured_runs=captured_runs,
        )

        await FragmentRepository(driver).list_fragments_for_source(source_id)

        cypher = captured_runs[1][0]
        assert "ORDER BY coalesce(f.position_index, 0) ASC" in cypher
        assert "f.position_index AS position_index" in cypher
        # id must never be the primary sort key — it is a content hash.
        assert not cypher.rstrip().endswith("ORDER BY f.created_at ASC, f.id ASC")

    @pytest.mark.asyncio
    async def test_list_for_project_orders_by_position_index(self):
        captured_runs = []
        driver = _FakeDriver(single_results=[{"total": 0}, []], captured_runs=captured_runs)

        await FragmentRepository(driver).list_fragments_for_project(uuid.uuid4())

        assert any(
            "ORDER BY coalesce(f.position_index, 0) ASC" in cypher for cypher, _ in captured_runs
        )

    def test_record_to_fragment_hydrates_position_index(self):
        record = {
            "id": "f1",
            "source_id": str(uuid.uuid4()),
            "frag_type": "image",
            "content": "",
            "bbox": "[]",
            "content_hash": "h",
            "position_index": 42,
        }

        assert FragmentRepository._record_to_fragment(record).position_index == 42

    def test_record_to_fragment_tolerates_legacy_row_without_position_index(self):
        """Fragments written before this field existed must still load."""
        record = {
            "id": "f1",
            "source_id": str(uuid.uuid4()),
            "frag_type": "text",
            "content": "x",
            "bbox": "[]",
            "content_hash": "h",
        }

        assert FragmentRepository._record_to_fragment(record).position_index is None
