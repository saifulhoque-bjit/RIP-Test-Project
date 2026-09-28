"""Unit tests for FragmentService."""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock
import uuid

import pytest

from app.core.exceptions import NotFoundError
from app.models.neo4j.fragment_model import FragmentModel
from app.services.fragment_service import FragmentService
from tests.conftest import make_source


def _make_fragment_model(
    source_id: uuid.UUID,
    *,
    fragment_id: str,
    fragment_type: str,
    content: str,
    source_type: str | None = None,
) -> FragmentModel:
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return FragmentModel(
        id=fragment_id,
        source_id=source_id,
        frag_type=fragment_type,
        content=content,
        bbox=[],
        content_hash=content_hash,
        source_type=source_type,
    )


class TestFragmentService:
    def test_chunk_to_model_generates_deterministic_id(self):
        source_id = uuid.uuid4()
        chunk = {
            "type": "text",
            "content": "Line A\nLine B",
            "bbox": [{"page": 1, "bbox": {"x": 0, "y": 0, "w": 10, "h": 10}, "confidence": 0.95}],
        }

        model_a = FragmentService._chunk_to_model(
            source_id=source_id,
            position_index=0,
            chunk=chunk,
        )
        model_b = FragmentService._chunk_to_model(
            source_id=source_id,
            position_index=0,
            chunk=chunk,
        )

        assert model_a.id == model_b.id

    @pytest.mark.asyncio
    async def test_list_fragments_success(self, uow):
        source = make_source(is_deleted=False)
        uow.sources.get_by_uuid.return_value = source
        repository = MagicMock()
        repository.list_fragments_for_source = AsyncMock(
            return_value=[
                _make_fragment_model(
                    source.id,
                    fragment_id="fragment-1",
                    fragment_type="text",
                    content="List fragment",
                )
            ]
        )

        response = await FragmentService(repository).list_fragments(source_id=source.id, uow=uow)

        assert response.source_id == source.id
        assert response.total_count == 1
        assert response.fragments[0].frag_type == "text"
        assert response.fragments[0].source_type is None

    @pytest.mark.asyncio
    async def test_list_fragments_success_with_source_type(self, uow):
        source = make_source(is_deleted=False)
        uow.sources.get_by_uuid.return_value = source
        repository = MagicMock()
        repository.list_fragments_for_source = AsyncMock(
            return_value=[
                _make_fragment_model(
                    source.id,
                    fragment_id="fragment-1",
                    fragment_type="text",
                    content="List fragment",
                    source_type="pdf",
                )
            ]
        )

        response = await FragmentService(repository).list_fragments(source_id=source.id, uow=uow)

        assert response.fragments[0].source_type == "pdf"

    def test_chunk_to_model_source_type_optional(self):
        source_id = uuid.uuid4()
        chunk = {
            "type": "text",
            "content": "Line A",
            "bbox": [],
        }

        model = FragmentService._chunk_to_model(
            source_id=source_id,
            position_index=0,
            chunk=chunk,
        )

        assert model.source_type is None

    def test_chunk_to_model_source_type_set(self):
        source_id = uuid.uuid4()
        chunk = {
            "type": "text",
            "source_type": "pdf",
            "content": "Line A",
            "bbox": [],
        }

        model = FragmentService._chunk_to_model(
            source_id=source_id,
            position_index=0,
            chunk=chunk,
        )

        assert model.source_type == "pdf"

    @pytest.mark.asyncio
    async def test_get_fragment_by_project_not_found(self, uow):
        from tests.conftest import make_project

        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        repository = MagicMock()
        repository.get_fragment_for_project = AsyncMock(return_value=None)

        with pytest.raises(NotFoundError):
            await FragmentService(repository).get_fragment_by_project(
                project_id=project.id,
                fragment_id="missing-fragment",
                uow=uow,
            )


class TestChunkToModelPositionIndex:
    def test_position_index_is_persisted_on_the_model(self):
        """Regression: position_index was accepted then thrown away, leaving the
        Neo4j read with only a uuid5 id to sort by — so the RFP pipeline received
        the document's fragments shuffled out of page order.
        """
        model = FragmentService._chunk_to_model(
            source_id=uuid.uuid4(),
            position_index=12,
            chunk={"frag_type": "text", "content": "body", "bbox": []},
        )

        assert model.position_index == 12

    def test_create_from_chunks_numbers_every_fragment_in_document_order(self):
        source_id = uuid.uuid4()
        chunks = [{"frag_type": "text", "content": f"page-{i}", "bbox": []} for i in range(5)]

        models = [
            FragmentService._chunk_to_model(source_id=source_id, position_index=i, chunk=chunk)
            for i, chunk in enumerate(chunks)
        ]

        assert [m.position_index for m in models] == [0, 1, 2, 3, 4]
        # Sorting by position_index reproduces document order; sorting by id does not.
        assert [m.content for m in sorted(models, key=lambda m: m.position_index)] == [
            f"page-{i}" for i in range(5)
        ]
