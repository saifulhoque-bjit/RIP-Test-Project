"""Repository for storing fragment-level embeddings in PostgreSQL."""

from __future__ import annotations

from collections.abc import Sequence
import uuid
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.postgres.fragment_embedding_model import FragmentEmbedding
from app.repositories.postgres.base_repository import BaseRepository


class FragmentEmbeddingRepository(BaseRepository[FragmentEmbedding]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, FragmentEmbedding)

    def bulk_upsert(
        self,
        project_id: UUID,
        source_id: UUID,
        records: Sequence[tuple[str, list[float]]],
    ) -> int:
        """Insert or update fragment embeddings.

        Uses ``ON CONFLICT (source_id, fragment_id) DO UPDATE`` so calling
        this method multiple times for the same source is idempotent.

        Args:
            project_id: UUID of the owning project.
            source_id:  UUID of the owning source file.
            records:    Sequence of ``(fragment_id, embedding_vector)`` pairs.

        Returns:
            The number of rows upserted.
        """
        if not records:
            return 0

        rows = [
            {
                "id": uuid.uuid4(),
                "project_id": project_id,
                "source_id": source_id,
                "fragment_id": fragment_id,
                "embedding": embedding,
            }
            for fragment_id, embedding in records
        ]

        stmt = insert(FragmentEmbedding).values(rows)
        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=["source_id", "fragment_id"],
            set_={
                "embedding": stmt.excluded.embedding,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        self._session.execute(upsert_stmt)
        return len(rows)

    def delete_by_source_id(self, source_id: UUID) -> int:
        """Delete all fragment_embeddings rows for a given source.

        Returns:
            The number of rows deleted.
        """
        stmt = delete(FragmentEmbedding).where(FragmentEmbedding.source_id == source_id)
        result = self._session.execute(stmt)
        return result.rowcount

    def delete_by_project_id(self, project_id: UUID) -> int:
        """Delete all fragment_embeddings rows for a given project.

        Returns:
            The number of rows deleted.
        """
        stmt = delete(FragmentEmbedding).where(FragmentEmbedding.project_id == project_id)
        result = self._session.execute(stmt)
        return result.rowcount
