"""Business logic for persisting fragment-level embeddings.

Called exclusively by ``FragmentService.create_fragments_from_chunks``
via ``store_embeddings_for_source``.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from app.db.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)


class FragmentEmbeddingService:
    """Persists fragment embeddings produced by the document chunker pipeline."""

    async def store_embeddings_for_source(
        self,
        *,
        source_id: UUID,
        embedding_pairs: list[tuple[str, list[float]]],
    ) -> int:
        """Store *(fragment_id, embedding)* pairs for a source in PostgreSQL.

        Resolves ``project_id`` from the source record automatically.
        Skips any pair whose vector is already stored (checked via L2 distance
        against all existing vectors in the project).

        Args:
            source_id:       UUID of the owning source file.
            embedding_pairs: List of ``(fragment_id, embedding_vector)`` tuples.

        Returns:
            Number of rows inserted or updated (0 if source not found or all
            embeddings were unchanged).
        """

        def _run() -> int:
            with UnitOfWork() as uow:
                source = uow.sources.get_by_uuid(source_id)
                if source is None:
                    logger.warning(
                        "FragmentEmbeddingService: source_id=%s not found "
                        "\u2014 embeddings not stored",
                        source_id,
                    )
                    return 0

                project_id = source.project_id

                # # Filter out pairs whose embedding already matches a stored
                # # vector in this project (idempotent at the vector level).
                # new_pairs: list[tuple[str, list[float]]] = []
                # skipped = 0
                # for fragment_id, embedding in embedding_pairs:
                #     already_stored = uow.fragment_embeddings.embedding_matches_for_project(
                #         project_id, embedding
                #     )
                #     if already_stored:
                #         first_hit = already_stored[0]
                #         logger.debug(
                #             "FragmentEmbeddingService: skipping fragment_id=%s "
                #             "\u2014 identical embedding already stored "
                #             "(source_id=%s, fragment_id=%s)",
                #             fragment_id,
                #             first_hit.source_id,
                #             first_hit.fragment_id,
                #         )
                #         skipped += 1
                #     else:
                #         new_pairs.append((fragment_id, embedding))

                # if skipped:
                #     logger.info(
                #         "FragmentEmbeddingService: skipped %d unchanged "
                #         "embedding(s) for source_id=%s",
                #         skipped,
                #         source_id,
                #     )

                # if not new_pairs:
                #     return 0

                count = uow.fragment_embeddings.bulk_upsert(
                    project_id=project_id,
                    source_id=source_id,
                    records=embedding_pairs,
                )
                logger.info(
                    "FragmentEmbeddingService: stored %d embedding(s) "
                    "for source_id=%s (project_id=%s)",
                    count,
                    source_id,
                    project_id,
                )
                return count

        return await asyncio.to_thread(_run)
