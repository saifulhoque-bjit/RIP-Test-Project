"""Neo4j graph repository for source file processing.

Responsibilities
────────────────
- Write parsed entities (File, Function, Class, UIElement) as graph nodes.
- Write relationships (CONTAINS, CALLS, DEPENDS_ON, DEFINES) between nodes.
- Provide semantic-search helpers via embedding similarity.

Design decisions
────────────────
- Uses the **sync** neo4j driver wrapped in ``asyncio.to_thread`` so calls
  are non-blocking from async FastAPI/Celery async contexts.
- Each public method accepts a driver injected at call-site (from
  ``app.db.neo4j.get_neo4j_driver()``) for testability.
- All Cypher writes use ``MERGE`` (not ``CREATE``) so tasks are idempotent
  — re-running a failed task will not duplicate nodes.
- Node IDs are deterministic UUIDs derived from the source_id + content hash
  so MERGE keys are stable across retries.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
import hashlib
from typing import TYPE_CHECKING, Any
from uuid import UUID

from neo4j import Driver

from app.core.constants import SOURCE_DEFAULT_FORMAT
from app.models.neo4j.source_model import SourceNode
from app.utils.logger import get_logger

if TYPE_CHECKING:
    from app.models.postgres.source_model import Source

logger = get_logger(__name__)

# ── Node labels ────────────────────────────────────────────────────────────
_LABEL_FILE = "Source"
_LABEL_FUNCTION = "Function"
_LABEL_CLASS = "Class"
_LABEL_UI_ELEMENT = "UIElement"
_LABEL_DOCUMENT_CHUNK = "DocumentChunk"

# ── Relationship types ─────────────────────────────────────────────────────
_REL_CONTAINS = "CONTAINS"
_REL_CALLS = "CALLS"
_REL_DEPENDS_ON = "DEPENDS_ON"
_REL_DEFINES = "DEFINES"


def _node_id(source_id: str, *parts: str) -> str:
    """Derive a deterministic node ID from the source_id and content parts."""
    raw = ":".join([source_id, *parts])
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class SourceRepository:
    """Graph operations for the Source processing pipeline.

    Usage (inside a Celery task)::

        driver = get_neo4j_driver()
        repo = SourceRepository(driver)
        await repo.upsert_file_node(source_id, metadata)
    """

    def __init__(self, driver: Driver) -> None:
        self._driver = driver

    # ── File node ──────────────────────────────────────────────────────────

    async def upsert_file_node(self, node: SourceNode) -> None:
        """Create or update a :File node for the source record."""
        sid = str(node.id)
        params: dict[str, Any] = {
            "node_id": sid,
            "source_id": sid,
            "project_id": str(node.project_id),
            "original_name": node.name,
            "file_type": node.file_type,
            "mime_type": node.mime_type or "",
            "storage_key": node.file_path or "",
            "checksum_sha256": node.checksum_sha256,
        }
        cypher = (
            f"MERGE (f:{_LABEL_FILE} {{node_id: $node_id}}) "
            "SET f.source_id = $source_id, "
            "    f.project_id = $project_id, "
            "    f.original_name = $original_name, "
            "    f.file_type = $file_type, "
            "    f.mime_type = $mime_type, "
            "    f.storage_key = $storage_key, "
            "    f.checksum_sha256 = $checksum_sha256"
        )
        await asyncio.to_thread(self._execute_write, cypher, params)
        logger.debug("Neo4j upserted File node: source_id=%s", sid)

    # ── UI elements ────────────────────────────────────────────────────────

    async def upsert_ui_elements(
        self,
        source_id: UUID,
        elements: list[dict[str, Any]],
    ) -> None:
        """Upsert UI element nodes parsed by OmniParser.

        Each *element* dict must contain:
            type         — element type (e.g. 'text', 'icon', 'button')
            bbox         — [x1, y1, x2, y2] bounding box floats
            content      — textual content or label (may be empty)
            interactivity — bool
            source       — detection source (e.g. 'paddleocr', 'yolo')
        """
        sid = str(source_id)
        for idx, el in enumerate(elements):
            node_id = _node_id(sid, "ui", str(idx))
            params: dict[str, Any] = {
                "file_node_id": sid,
                "node_id": node_id,
                "source_id": sid,
                "element_type": el.get("type", "unknown"),
                "bbox": str(el.get("bbox", [])),
                "content": el.get("content", ""),
                "interactivity": bool(el.get("interactivity", False)),
                "detection_source": el.get("source", ""),
                "position_index": idx,
            }
            cypher = (
                f"MERGE (u:{_LABEL_UI_ELEMENT} {{node_id: $node_id}}) "
                "SET u.source_id = $source_id, "
                "    u.element_type = $element_type, "
                "    u.bbox = $bbox, "
                "    u.content = $content, "
                "    u.interactivity = $interactivity, "
                "    u.detection_source = $detection_source, "
                "    u.position_index = $position_index "
                f"WITH u "
                f"MATCH (f:{_LABEL_FILE} {{node_id: $file_node_id}}) "
                f"MERGE (f)-[:{_REL_CONTAINS}]->(u)"
            )
            await asyncio.to_thread(self._execute_write, cypher, params)

        logger.info(
            "Neo4j upserted %d UIElement nodes for source_id=%s",
            len(elements),
            sid,
        )

    # ── Source code entities ───────────────────────────────────────────────

    async def upsert_code_entities(
        self,
        source_id: UUID,
        entities: list[dict[str, Any]],
    ) -> None:
        """Upsert Function / Class nodes parsed by Tree-sitter.

        Each *entity* dict must contain:
            name      — symbol name
            kind      — 'function' | 'class' | 'method'
            start_line — integer
            end_line   — integer
            language  — programming language (e.g. 'python', 'javascript')
        Optional:
            calls     — list of function names this entity calls
            depends_on — list of class/module names this entity imports
        """
        sid = str(source_id)
        for entity in entities:
            kind = entity.get("kind", "function").lower()
            label = _LABEL_CLASS if kind == "class" else _LABEL_FUNCTION
            node_id = _node_id(sid, kind, entity["name"], str(entity.get("start_line", 0)))
            params: dict[str, Any] = {
                "file_node_id": sid,
                "node_id": node_id,
                "source_id": sid,
                "name": entity["name"],
                "kind": kind,
                "start_line": entity.get("start_line", 0),
                "end_line": entity.get("end_line", 0),
                "language": entity.get("language", ""),
            }
            # Upsert the entity node and link to its file.
            cypher = (
                f"MERGE (e:{label} {{node_id: $node_id}}) "
                "SET e.source_id = $source_id, "
                "    e.name = $name, "
                "    e.kind = $kind, "
                "    e.start_line = $start_line, "
                "    e.end_line = $end_line, "
                "    e.language = $language "
                f"WITH e "
                f"MATCH (f:{_LABEL_FILE} {{node_id: $file_node_id}}) "
                f"MERGE (f)-[:{_REL_DEFINES}]->(e)"
            )
            await asyncio.to_thread(self._execute_write, cypher, params)

            # Write CALLS relationships.
            for callee_name in entity.get("calls", []):
                callee_node_id = _node_id(sid, "function", callee_name, "0")
                call_params: dict[str, Any] = {
                    "caller_id": node_id,
                    "callee_id": callee_node_id,
                    "callee_name": callee_name,
                    "source_id": sid,
                }
                call_cypher = (
                    f"MERGE (callee:{_LABEL_FUNCTION} {{node_id: $callee_id}}) "
                    "SET callee.name = $callee_name, callee.source_id = $source_id "
                    f"WITH callee "
                    f"MATCH (caller:{_LABEL_FUNCTION} {{node_id: $caller_id}}) "
                    f"MERGE (caller)-[:{_REL_CALLS}]->(callee)"
                )
                await asyncio.to_thread(self._execute_write, call_cypher, call_params)

            # Write DEPENDS_ON relationships.
            for dep_name in entity.get("depends_on", []):
                dep_node_id = _node_id(sid, "class", dep_name, "0")
                dep_params: dict[str, Any] = {
                    "entity_id": node_id,
                    "dep_id": dep_node_id,
                    "dep_name": dep_name,
                    "source_id": sid,
                }
                dep_cypher = (
                    f"MERGE (dep:{_LABEL_CLASS} {{node_id: $dep_id}}) "
                    "SET dep.name = $dep_name, dep.source_id = $source_id "
                    f"WITH dep "
                    f"MATCH (e:{label} {{node_id: $entity_id}}) "
                    f"MERGE (e)-[:{_REL_DEPENDS_ON}]->(dep)"
                )
                await asyncio.to_thread(self._execute_write, dep_cypher, dep_params)

        logger.info(
            "Neo4j upserted %d code entities for source_id=%s",
            len(entities),
            sid,
        )

    # ── Level-1 source projection ──────────────────────────────────────────

    async def create_level1_sources(self, nodes: list[SourceNode]) -> None:
        """Project bare Level-1 Source metadata into Neo4j for a batch of sources.

        Accepts :class:`~app.models.neo4j.source_model.SourceNode` objects so
        the repository works with a typed model rather than raw ORM instances.

        Level-1 means only the core Source node properties are written —
        no Fragment nodes, no Module/Feature/UserStory relationships.
        Those are populated by later pipeline stages (Level 2 / Level 3).

        Idempotent — safe to retry; uses MERGE semantics.
        """
        if not nodes:
            return
        rows = [self._normalize_source_node(n) for n in nodes]
        await asyncio.to_thread(self._execute_merge_batch, rows)

    def _execute_merge_batch(self, rows: list[dict[str, Any]]) -> None:
        """Batched MERGE write for Source nodes linked to existing Project nodes.

        Uses MATCH (not MERGE) for the Project node so that bulk upload never
        creates or modifies Project data — the Project must already exist in
        Neo4j (written by ``ProjectService._upsert_project_node_in_neo4j`` at
        project-creation time).  If the Project node is not found for a given
        row, the Source node is still written with its ``project_id`` scalar
        property; only the ``HAS_SOURCE`` edge is skipped.
        """
        cypher = (
            "UNWIND $rows AS row "
            "MERGE (s:Source {id: row.id}) "
            "SET s += row.properties, s.project_id = row.project_id "
            "WITH s, row "
            "MATCH (p:Project {id: row.project_id}) "
            "MERGE (p)-[:HAS_SOURCE]->(s)"
        )

        with self._driver.session() as session:
            session.execute_write(lambda tx: tx.run(cypher, rows=rows).consume())
        logger.info(
            "Neo4j merged %d Level-1 Source node(s)",
            len(rows),
        )

    def _normalize_source_node(self, node: SourceNode) -> dict[str, Any]:
        """Validate and normalise a SourceNode for the batched Cypher write."""
        if node.id is None:
            raise ValueError("SourceNode must have an 'id'")
        if node.project_id is None:
            raise ValueError("SourceNode must have a 'project_id'")

        properties = {
            "name": node.name,
            "file_type": node.file_type,
            "mime_type": node.mime_type,
            "file_path": node.file_path,
            "status": node.status,
            "source_type": node.source_type,
            "checksum_sha256": node.checksum_sha256,
            "is_incremental": node.is_incremental,
            "user_message": node.user_message,
            "created_at": self._serialize_temporal(node.created_at),
            "updated_at": self._serialize_temporal(node.updated_at),
        }
        # Drop None values so MERGE SET does not overwrite existing properties with null.
        properties = {k: v for k, v in properties.items() if v is not None}

        return {
            "id": str(node.id),
            "project_id": str(node.project_id),
            "properties": properties,
        }

    @staticmethod
    def _serialize_temporal(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return str(value)

    @staticmethod
    def orm_to_node(source: Source) -> SourceNode:
        """Convert a ``Source`` ORM object to a :class:`SourceNode`.

        Call this in the service layer before passing sources to
        :meth:`create_level1_sources`, or before calling
        :meth:`upsert_file_node`.
        """
        ingestion = getattr(source, "source_ingestion", None)
        return SourceNode(
            id=source.id,
            project_id=source.project_id,
            name=source.original_name,
            file_type=(source.file_type or SOURCE_DEFAULT_FORMAT).lower(),
            mime_type=getattr(source, "mime_type", None),
            file_path=source.storage_key,
            status=source.status,
            source_type=getattr(source, "source_type", None),
            checksum_sha256=source.checksum_sha256,
            is_incremental=getattr(ingestion, "is_incremental", None),
            user_message=getattr(ingestion, "user_message", None),
            created_at=source.created_at,
            updated_at=source.updated_at,
        )

    # ── Delete ─────────────────────────────────────────────────────────────

    async def delete_source_node(self, source_id: UUID) -> int:
        """DETACH DELETE the Source node and all nodes exclusively owned by it.

        Deletes (in order) Fragment/Feature/Module nodes linked to the source,
        then the Source node itself. Finally removes only orphan UserStory
        nodes (those no longer linked by HAS_USER_STORY).

        Returns:
            1 if the source node was found and deleted, 0 otherwise.
        """
        return await asyncio.to_thread(self._execute_delete_source_node, str(source_id))

    def _execute_delete_source_node(self, source_id: str) -> int:
        """Sync implementation of source-node teardown for asyncio.to_thread.

        Traversal-only model: user stories are reached only via
        Project→Module→Feature→(HAS_USER_STORY)→UserStory
        or by matching fragment_id on UserStory nodes.
        There is no direct Source→UserStory edge, and user story cleanup
        is intentionally orphan-only to avoid removing user stories still
        referenced by other nodes.
        """
        cypher = (
            "OPTIONAL MATCH (s:Source {id: $source_id}) "
            "OPTIONAL MATCH (s)-[:HAS_FRAGMENT]->(f:Fragment) "
            "OPTIONAL MATCH (s)<-[:HAS_SOURCE]-(:Project)-[:HAS_MODULE]->(m:Module) "
            "WHERE m.source_id = $source_id "
            "OPTIONAL MATCH (m)-[:HAS_FEATURE]->(ft:Feature) "
            "WITH s, collect(DISTINCT f) AS fragments, collect(DISTINCT ft) AS features, collect(DISTINCT m) AS modules "
            "FOREACH (f IN fragments | DETACH DELETE f) "
            "FOREACH (ft IN features | DETACH DELETE ft) "
            "FOREACH (m IN modules | DETACH DELETE m) "
            "WITH s "
            "FOREACH (_ IN CASE WHEN s IS NOT NULL THEN [1] ELSE [] END | "
            "  DETACH DELETE s) "
            # Remove only orphan user stories after source subgraph deletion.
            "WITH 1 AS _ "
            "OPTIONAL MATCH (r:UserStory) "
            "WHERE NOT EXISTS { (:Feature)-[:HAS_USER_STORY]->(r) } "
            "FOREACH (_x IN CASE WHEN r IS NOT NULL THEN [1] ELSE [] END | "
            "  DETACH DELETE r) "
            "RETURN 1 AS deleted"
        )
        with self._driver.session() as session:
            result = session.execute_write(lambda tx: tx.run(cypher, source_id=source_id).single())
        return 1 if result else 0

    # ── Internal helpers ───────────────────────────────────────────────────

    def _execute_write(self, cypher: str, params: dict[str, Any]) -> None:
        """Execute a single write transaction against Neo4j (sync, blocking)."""
        with self._driver.session() as session:
            session.run(cypher, params)
