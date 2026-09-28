"""Business logic for fragment creation workflows."""

from __future__ import annotations

import hashlib
import json
from uuid import NAMESPACE_URL, UUID, uuid5

from app.core.exceptions import NotFoundError
from app.core.messages import (
    MSG_FRAGMENT_NOT_FOUND,
    MSG_FRAGMENT_NOT_FOUND_FOR_PROJECT,
    MSG_PROJECT_NOT_FOUND,
    MSG_SOURCE_NOT_FOUND,
)
from app.db.unit_of_work import UnitOfWork
from app.models.neo4j.fragment_model import (
    FragmentBBoxCoordinatesModel,
    FragmentBBoxModel,
    FragmentModel,
)
from app.repositories.neo4j.fragment_repository import FragmentRepository
from app.schemas.fragment_schema import (
    CreateSingleFragmentResponse,
    FragmentBBoxCoordinates,
    FragmentBBoxItem,
    FragmentResponse,
    ListFragmentsByProjectResponse,
    ListFragmentsResponse,
    UpdateFragmentBBoxRequest,
    UpdateFragmentBBoxResponse,
)
from app.services.fragment_embedding_service import FragmentEmbeddingService
from app.utils.logger import get_logger

logger = get_logger(__name__)


class FragmentService:
    """Validates and persists fragments linked to an existing source."""

    def __init__(
        self,
        repository: FragmentRepository | None = None,
        embedding_service: FragmentEmbeddingService | None = None,
    ) -> None:
        self._repository = repository or FragmentRepository()
        # Import lazily to avoid circular imports at module load time.
        if embedding_service is None:
            from app.services.fragment_embedding_service import (
                FragmentEmbeddingService,  # noqa: PLC0415
            )

            embedding_service = FragmentEmbeddingService()
        self._embedding_service = embedding_service

    async def list_fragments(
        self,
        *,
        source_id: UUID,
        uow: UnitOfWork,
    ) -> ListFragmentsResponse:
        source = uow.sources.get_by_uuid(source_id)
        if source is None or source.is_deleted:
            raise NotFoundError(MSG_SOURCE_NOT_FOUND.format(source_id=source_id))

        fragments = await self._repository.list_fragments_for_source(source_id)
        return ListFragmentsResponse(
            source_id=source_id,
            total_count=len(fragments),
            fragments=[self._to_response_model(fragment) for fragment in fragments],
        )

    async def list_fragments_by_project(
        self,
        *,
        project_id: UUID,
        source_id: UUID | None = None,
        frag_type: str | None = None,
        content: str | None = None,
        skip: int = 0,
        limit: int = 20,
        uow: UnitOfWork,
    ) -> ListFragmentsByProjectResponse:
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))

        total, fragments = await self._repository.list_fragments_for_project(
            project_id,
            source_id=source_id,
            frag_type=frag_type,
            content=content,
            skip=skip,
            limit=limit,
        )
        return ListFragmentsByProjectResponse(
            project_id=project_id,
            total=total,
            skip=skip,
            limit=limit,
            items=[self._to_response_model(fragment) for fragment in fragments],
        )

    async def get_fragment_by_project(
        self,
        *,
        project_id: UUID,
        fragment_id: str,
        uow: UnitOfWork,
    ) -> CreateSingleFragmentResponse:
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))

        fragment = await self._repository.get_fragment_for_project(project_id, fragment_id)
        if fragment is None:
            raise NotFoundError(
                MSG_FRAGMENT_NOT_FOUND_FOR_PROJECT.format(
                    fragment_id=fragment_id, project_id=project_id
                )
            )

        return CreateSingleFragmentResponse(
            source_id=fragment.source_id,
            fragment=self._to_response_model(fragment),
        )

    async def update_fragment_bbox(
        self,
        *,
        source_id: UUID,
        fragment_id: str,
        request: UpdateFragmentBBoxRequest,
        uow: UnitOfWork,
    ) -> UpdateFragmentBBoxResponse:
        source = uow.sources.get_by_uuid(source_id)
        if source is None or source.is_deleted:
            raise NotFoundError(MSG_SOURCE_NOT_FOUND.format(source_id=source_id))

        bbox_json = json.dumps(
            [
                {
                    "page": entry.page,
                    "bbox": {
                        "x": entry.bbox.x,
                        "y": entry.bbox.y,
                        "w": entry.bbox.w,
                        "h": entry.bbox.h,
                    },
                    "confidence": entry.confidence,
                }
                for entry in request.bbox
            ]
        )
        updated = await self._repository.update_fragment_bbox(source_id, fragment_id, bbox_json)
        if updated is None:
            raise NotFoundError(
                MSG_FRAGMENT_NOT_FOUND.format(fragment_id=fragment_id, source_id=source_id)
            )

        return UpdateFragmentBBoxResponse(
            fragment_id=fragment_id,
            source_id=source_id,
            bbox=[
                FragmentBBoxItem(
                    page=entry.page,
                    bbox=FragmentBBoxCoordinates(
                        x=entry.bbox.x,
                        y=entry.bbox.y,
                        w=entry.bbox.w,
                        h=entry.bbox.h,
                    ),
                    confidence=entry.confidence,
                )
                for entry in updated.bbox
            ],
            created_at=updated.created_at,
            updated_at=updated.updated_at,
        )

    @staticmethod
    def _to_response_model(fragment: FragmentModel) -> FragmentResponse:
        return FragmentResponse(
            id=fragment.id,
            source_id=fragment.source_id,
            frag_type=fragment.frag_type,
            source_type=fragment.source_type,
            content=fragment.content,
            bbox=[
                FragmentBBoxItem(
                    page=entry.page,
                    bbox=FragmentBBoxCoordinates(
                        x=entry.bbox.x,
                        y=entry.bbox.y,
                        w=entry.bbox.w,
                        h=entry.bbox.h,
                    ),
                    confidence=entry.confidence,
                )
                for entry in fragment.bbox
            ],
            content_hash=fragment.content_hash,
            created_at=fragment.created_at,
            updated_at=fragment.updated_at,
        )

    async def create_fragments_from_chunks(
        self,
        *,
        source_id: UUID,
        chunks: list[dict],
    ) -> list[FragmentModel]:
        """Persist document-pipeline chunks as fragments in Neo4j and store
        their embeddings in PostgreSQL (``fragment_embeddings`` table).

        Intended for use by background Celery tasks where a request-scoped
        UnitOfWork is not available.  Source existence is assumed to have been
        validated earlier in the task pipeline.

        Args:
            source_id: The UUID of the owning source.
            chunks: Raw chunk dicts produced by ``ChunkerPipeline.run()``.
                Each dict should provide ``frag_type`` (or legacy ``type``),
                ``content``, ``bbox``, and
                optionally ``embedding``
                (a 1536-dim float list).

        Returns:
            Saved FragmentModel list from Neo4j (with created_at / updated_at).
            ``content_hash`` is an internal deduplication field; callers that
            serialize fragments for external consumers should exclude it.
        """
        local_fragments = [
            self._chunk_to_model(source_id=source_id, position_index=index, chunk=chunk)
            for index, chunk in enumerate(chunks)
        ]

        # Save to Neo4j; the returned models carry the actual created_at/updated_at
        # timestamps set by the database.
        saved_fragments = await self._repository.create_fragments_for_source(
            source_id, local_fragments
        )
        logger.info(
            "Persisted %d fragment(s) from document chunks for source_id=%s",
            len(saved_fragments),
            source_id,
        )
        logger.debug(
            "fragments for source_id=%s:\n%s",
            source_id,
            json.dumps(
                [
                    {
                        "id": f.id,
                        "frag_type": f.frag_type,
                        "content_preview": f.content[:100],
                        "created_at": str(f.created_at),
                        "updated_at": str(f.updated_at),
                    }
                    for f in saved_fragments
                ],
                indent=2,
            ),
        )

        # Collect (fragment_id, embedding) pairs using the locally-built fragments
        # whose IDs are deterministic and match the saved ones.
        embedding_pairs: list[tuple[str, list[float]]] = [
            (fragment.id, chunk["embedding"])
            for fragment, chunk in zip(local_fragments, chunks, strict=False)
            if chunk.get("embedding")
        ]
        logger.debug(
            "embedding_pairs for source_id=%s:\n%s",
            source_id,
            json.dumps(
                [
                    {"fragment_id": fid, "embedding_dim": len(vec), "embedding_preview": vec[:5]}
                    for fid, vec in embedding_pairs
                ],
                indent=2,
            ),
        )
        if embedding_pairs:
            await self._embedding_service.store_embeddings_for_source(
                source_id=source_id,
                embedding_pairs=embedding_pairs,
            )

        return saved_fragments

    @staticmethod
    def _chunk_to_model(
        *,
        source_id: UUID,
        position_index: int,
        chunk: dict,
    ) -> FragmentModel:
        """Convert a raw chunker output dict into a ``FragmentModel``.

        Chunk keys used:
            frag_type — fragment type/category label (or legacy ``type``)
            content  — full text content of the chunk
            bbox     — list of {"page": N, "bbox": {"x":..,"y":..,"w":..,"h":..}} dicts
        """
        content = "\n".join(line.rstrip() for line in chunk["content"].strip().splitlines()).strip()

        bbox_models: list[FragmentBBoxModel] = []
        for entry in chunk.get("bbox") or []:
            bbox_data = entry.get("bbox")
            if isinstance(bbox_data, (list, tuple)) and len(bbox_data) >= 4:
                # Backward compatibility for legacy [x, y, w, h] bbox payloads.
                bbox_data = {
                    "x": bbox_data[0],
                    "y": bbox_data[1],
                    "w": bbox_data[2],
                    "h": bbox_data[3],
                }
            if not bbox_data or not isinstance(bbox_data, dict):
                continue
            try:
                bbox_models.append(
                    FragmentBBoxModel(
                        page=int(entry.get("page", 1)),
                        bbox=FragmentBBoxCoordinatesModel(
                            x=float(bbox_data.get("x", 0)),
                            y=float(bbox_data.get("y", 0)),
                            w=float(bbox_data.get("w", 0)),
                            h=float(bbox_data.get("h", 0)),
                        ),
                        confidence=float(entry.get("confidence"))
                        if entry.get("confidence")
                        else None,
                    )
                )
            except (TypeError, ValueError, KeyError):
                logger.warning(
                    "Skipping malformed bbox entry for source_id=%s position_index=%s entry=%s",
                    source_id,
                    position_index,
                    entry,
                )
                continue
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        fragment_id = str(
            uuid5(
                NAMESPACE_URL,
                f"{source_id}:{position_index}:{content_hash}",
            )
        )

        return FragmentModel(
            id=fragment_id,
            source_id=source_id,
            frag_type=(
                str(
                    chunk.get("frag_type") or chunk.get("type") or chunk.get("path") or "unknown"
                ).strip()
            ),
            content=content,
            bbox=bbox_models,
            content_hash=content_hash,
            position_index=position_index,
            source_type=(
                str(chunk.get("source_type")).strip()
                if chunk.get("source_type") is not None
                else None
            ),
        )
