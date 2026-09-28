"""Neo4j repository for fragment nodes."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
import json
from uuid import UUID

from neo4j import Driver

from app.core.exceptions import NotFoundError
from app.core.messages import MSG_FRAGMENT_GRAPH_SOURCE_NOT_FOUND
from app.db.neo4j import get_neo4j_driver
from app.models.neo4j.fragment_model import (
    FragmentBBoxCoordinatesModel,
    FragmentBBoxModel,
    FragmentModel,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


class FragmentRepository:
    """Persistence layer for source fragments in Neo4j."""

    def __init__(self, driver: Driver | None = None) -> None:
        self._driver = driver or get_neo4j_driver()

    async def create_fragments_for_source(
        self,
        source_id: UUID,
        fragments: Sequence[FragmentModel],
    ) -> list[FragmentModel]:
        if not fragments:
            return []

        await asyncio.to_thread(self._ensure_source_node_exists, str(source_id))
        return await asyncio.to_thread(
            self._execute_write_batch,
            str(source_id),
            list(fragments),
        )

    async def list_fragments_for_source(self, source_id: UUID) -> list[FragmentModel]:
        await asyncio.to_thread(self._ensure_source_node_exists, str(source_id))
        return await asyncio.to_thread(self._execute_list_query, str(source_id))

    async def list_fragments_for_project(
        self,
        project_id: UUID,
        *,
        source_id: UUID | None = None,
        frag_type: str | None = None,
        content: str | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> tuple[int, list[FragmentModel]]:
        source_id_str = str(source_id) if source_id is not None else None
        return await asyncio.to_thread(
            self._execute_list_project_query,
            str(project_id),
            source_id_str,
            frag_type,
            content,
            skip,
            limit,
        )

    async def get_fragment_for_project(
        self,
        project_id: UUID,
        fragment_id: str,
    ) -> FragmentModel | None:
        return await asyncio.to_thread(
            self._execute_get_project_query,
            str(project_id),
            fragment_id,
        )

    async def update_fragment_bbox(
        self,
        source_id: UUID,
        fragment_id: str,
        bbox_json: str,
    ) -> FragmentModel | None:
        """Update only the ``bbox`` property of a Fragment node.

        Returns the updated fragment model, or ``None`` when the fragment
        does not exist under the given source.
        """
        await asyncio.to_thread(self._ensure_source_node_exists, str(source_id))
        return await asyncio.to_thread(
            self._execute_update_bbox_query,
            str(source_id),
            fragment_id,
            bbox_json,
        )

    def _execute_update_bbox_query(
        self,
        source_id: str,
        fragment_id: str,
        bbox_json: str,
    ) -> FragmentModel | None:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (:Source {id: $source_id})-[:HAS_FRAGMENT]->(f:Fragment {id: $fragment_id}) "
            "SET f.bbox = $bbox, "
            "    f.created_at = coalesce(f.created_at, datetime($now)), "
            "    f.updated_at = datetime($now) "
            "RETURN f.id AS id, "
            "       f.source_id AS source_id, "
            "       f.frag_type AS frag_type, "
            "       f.source_type AS source_type, "
            "       f.content AS content, "
            "       f.bbox AS bbox, "
            "       f.content_hash AS content_hash, "
            "       f.position_index AS position_index, "
            "       f.created_at AS created_at, "
            "       f.updated_at AS updated_at"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    source_id=source_id,
                    fragment_id=fragment_id,
                    bbox=bbox_json,
                    now=now,
                ).single()
            )
        if record is None:
            return None
        return self._record_to_fragment(record)

    def _ensure_source_node_exists(self, source_id: str) -> None:
        cypher = "MATCH (s:Source {id: $source_id}) RETURN s.id AS source_id"
        with self._driver.session() as session:
            record = session.execute_read(lambda tx: tx.run(cypher, source_id=source_id).single())
        if record is None:
            raise NotFoundError(MSG_FRAGMENT_GRAPH_SOURCE_NOT_FOUND.format(source_id=source_id))

    def _execute_write_batch(
        self,
        source_id: str,
        fragments: list[FragmentModel],
    ) -> list[FragmentModel]:
        now = datetime.now(UTC).isoformat()
        rows = [
            {
                "id": fragment.id,
                "source_id": source_id,
                "frag_type": fragment.frag_type,
                "source_type": fragment.source_type,
                "content": fragment.content,
                "bbox": json.dumps(
                    [
                        {
                            "page": b.page,
                            "bbox": {"x": b.bbox.x, "y": b.bbox.y, "w": b.bbox.w, "h": b.bbox.h},
                            "confidence": b.confidence,
                        }
                        for b in fragment.bbox
                    ],
                    separators=(",", ":"),
                ),
                "content_hash": fragment.content_hash,
                "position_index": fragment.position_index,
            }
            for fragment in fragments
        ]
        cypher = (
            "MATCH (s:Source {id: $source_id}) "
            "UNWIND $rows AS row "
            "MERGE (f:Fragment {id: row.id}) "
            "SET f.source_id = row.source_id, "
            "    f.frag_type = row.frag_type, "
            "    f.source_type = row.source_type, "
            "    f.content = row.content, "
            "    f.bbox = row.bbox, "
            "    f.content_hash = row.content_hash, "
            "    f.position_index = row.position_index, "
            "    f.created_at = coalesce(f.created_at, datetime($now)), "
            "    f.updated_at = datetime($now) "
            "MERGE (s)-[:HAS_FRAGMENT]->(f) "
            "RETURN f.id AS id, "
            "       f.source_id AS source_id, "
            "       f.frag_type AS frag_type, "
            "       f.source_type AS source_type, "
            "       f.content AS content, "
            "       f.bbox AS bbox, "
            "       f.content_hash AS content_hash, "
            "       f.position_index AS position_index, "
            "       f.created_at AS created_at, "
            "       f.updated_at AS updated_at"
        )
        with self._driver.session() as session:
            records = session.execute_write(
                lambda tx: list(tx.run(cypher, source_id=source_id, rows=rows, now=now))
            )

        saved = [self._record_to_fragment(r) for r in records]
        logger.info(
            "Neo4j created/updated %d Fragment node(s) for source_id=%s",
            len(saved),
            source_id,
        )
        return saved

    def _execute_list_query(self, source_id: str) -> list[FragmentModel]:
        cypher = (
            "MATCH (s:Source {id: $source_id})-[:HAS_FRAGMENT]->(f:Fragment) "
            "RETURN f.id AS id, "
            "       f.source_id AS source_id, "
            "       f.frag_type AS frag_type, "
            "       f.source_type AS source_type, "
            "       f.content AS content, "
            "       f.bbox AS bbox, "
            "       f.content_hash AS content_hash, "
            "       f.position_index AS position_index, "
            "       f.created_at AS created_at, "
            "       f.updated_at AS updated_at "
            "ORDER BY coalesce(f.position_index, 0) ASC, f.created_at ASC, f.id ASC"
        )
        with self._driver.session() as session:
            records = session.execute_read(lambda tx: list(tx.run(cypher, source_id=source_id)))

        return [self._record_to_fragment(record) for record in records]

    def _execute_list_project_query(
        self,
        project_id: str,
        source_id: str | None,
        frag_type: str | None,
        content: str | None,
        skip: int,
        limit: int,
    ) -> tuple[int, list[FragmentModel]]:
        params: dict = {
            "project_id": project_id,
            "source_id": source_id,
            "frag_type": frag_type,
            "content": content.lower() if content else None,
            "skip": skip,
            "limit": limit,
        }
        match_clause = "MATCH (:Project {id: $project_id})-[:HAS_SOURCE]->(s:Source)-[:HAS_FRAGMENT]->(f:Fragment) "
        where_clause = (
            "WHERE ($source_id IS NULL OR s.id = $source_id) "
            "  AND ($frag_type IS NULL OR f.frag_type = $frag_type) "
            "  AND ($content IS NULL OR toLower(f.content) CONTAINS $content) "
        )
        count_cypher = match_clause + where_clause + "RETURN count(f) AS total"
        data_cypher = (
            match_clause + where_clause + "RETURN f.id AS id, "
            "       f.source_id AS source_id, "
            "       f.frag_type AS frag_type, "
            "       f.source_type AS source_type, "
            "       f.content AS content, "
            "       f.bbox AS bbox, "
            "       f.content_hash AS content_hash, "
            "       f.position_index AS position_index, "
            "       f.created_at AS created_at, "
            "       f.updated_at AS updated_at "
            "ORDER BY coalesce(f.position_index, 0) ASC, f.created_at ASC, f.id ASC "
            "SKIP $skip LIMIT $limit"
        )
        with self._driver.session() as session:
            total = session.execute_read(
                lambda tx: tx.run(count_cypher, **params).single()["total"]
            )
            records = session.execute_read(lambda tx: list(tx.run(data_cypher, **params)))
        return total, [self._record_to_fragment(record) for record in records]

    def _execute_get_project_query(self, project_id: str, fragment_id: str) -> FragmentModel | None:
        cypher = (
            "MATCH (:Project {id: $project_id})-[:HAS_SOURCE]->(:Source)-[:HAS_FRAGMENT]->(f:Fragment {id: $fragment_id}) "
            "RETURN f.id AS id, "
            "       f.source_id AS source_id, "
            "       f.frag_type AS frag_type, "
            "       f.source_type AS source_type, "
            "       f.content AS content, "
            "       f.bbox AS bbox, "
            "       f.content_hash AS content_hash, "
            "       f.position_index AS position_index, "
            "       f.created_at AS created_at, "
            "       f.updated_at AS updated_at"
        )
        with self._driver.session() as session:
            record = session.execute_read(
                lambda tx: tx.run(
                    cypher,
                    project_id=project_id,
                    fragment_id=fragment_id,
                ).single()
            )

        if record is None:
            return None
        return self._record_to_fragment(record)

    @staticmethod
    def _record_to_fragment(record) -> FragmentModel:
        bbox_raw = record.get("bbox")
        if isinstance(bbox_raw, str):
            bbox_entries = json.loads(bbox_raw) if bbox_raw else []
        else:
            bbox_entries = bbox_raw or []

        bbox_models: list[FragmentBBoxModel] = []
        for entry in bbox_entries:
            try:
                bbox_models.append(
                    FragmentBBoxModel(
                        page=int(entry.get("page", 1)),
                        bbox=FragmentBBoxCoordinatesModel(
                            x=float(entry.get("bbox").get("x", 0)),
                            y=float(entry.get("bbox").get("y", 0)),
                            w=float(entry.get("bbox").get("w", 0)),
                            h=float(entry.get("bbox").get("h", 0)),
                        ),
                        confidence=float(entry.get("confidence"))
                        if entry.get("confidence")
                        else None,
                    )
                )
            except (TypeError, ValueError, KeyError):
                continue
        return FragmentModel(
            id=str(record["id"]),
            source_id=UUID(str(record["source_id"])),
            frag_type=str(record.get("frag_type") or "unknown"),
            content=str(record["content"]),
            bbox=bbox_models,
            content_hash=str(record["content_hash"]),
            position_index=(
                int(record.get("position_index"))
                if record.get("position_index") is not None
                else None
            ),
            source_type=(str(record.get("source_type")) if record.get("source_type") else None),
            created_at=FragmentRepository._neo4j_dt_to_py(record.get("created_at")),
            updated_at=FragmentRepository._neo4j_dt_to_py(record.get("updated_at")),
        )

    @staticmethod
    def _neo4j_dt_to_py(value):
        """Convert a Neo4j DateTime value to a Python datetime, or return None."""
        if value is None:
            return None
        from datetime import datetime as _dt

        if isinstance(value, _dt):
            return value
        try:
            return value.to_native()
        except AttributeError:
            return None
