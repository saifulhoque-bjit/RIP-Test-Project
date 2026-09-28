"""Neo4j repository for GroupSpec graph operations."""

from __future__ import annotations

import asyncio
from typing import Any

from neo4j import Driver

from app.db.neo4j import get_neo4j_driver
from app.models.neo4j.group_spec_model import GroupSpecModel


class GroupSpecRepository:
    """Persistence layer for GroupSpec nodes in Neo4j."""

    def __init__(self, driver: Driver | None = None) -> None:
        self._driver = driver or get_neo4j_driver()

    async def list_by_project(self, project_id: str) -> list[GroupSpecModel]:
        """Return every GroupSpec node linked to *project_id*.

        Used by the Export feature to locate each module/feature's
        already-generated SRS markdown file (via ``storage_key``) so it can
        be pulled from S3 and bundled — no content is stored in Neo4j itself.
        """
        return await asyncio.to_thread(self._execute_list_by_project, project_id)

    def _execute_list_by_project(self, project_id: str) -> list[GroupSpecModel]:
        cypher = (
            "MATCH (:Project {id: $project_id})-[:HAS_GROUP_SPEC]->(gs:GroupSpec) "
            "RETURN gs "
            "ORDER BY gs.mod_code, gs.fea_code"
        )
        with self._driver.session() as session:
            records = session.execute_read(lambda tx: list(tx.run(cypher, project_id=project_id)))
        return [self._build_model(record["gs"]) for record in records]

    @staticmethod
    def _build_model(node: Any) -> GroupSpecModel:
        return GroupSpecModel(
            id=str(node.get("id") or ""),
            mod_code=node.get("mod_code"),
            fea_code=node.get("fea_code"),
            filename=node.get("filename"),
            storage_key=node.get("storage_key"),
            source_id=node.get("source_id"),
            project_id=node.get("project_id"),
        )

    async def upsert_many_for_project(
        self,
        *,
        project_id: str,
        rows: list[dict[str, Any]] | None,
    ) -> int:
        return await asyncio.to_thread(
            self._execute_upsert_many_for_project,
            project_id,
            rows or [],
        )

    def _execute_upsert_many_for_project(
        self,
        project_id: str,
        rows: list[dict[str, Any]],
    ) -> int:
        if not rows:
            return 0

        cypher = (
            "MATCH (p:Project {id: $project_id}) "
            "UNWIND $rows AS row "
            "MERGE (gs:GroupSpec {id: row.id}) "
            "SET gs.mod_code = row.mod_code, "
            "    gs.fea_code = row.fea_code, "
            "    gs.filename = row.filename, "
            "    gs.storage_key = row.storage_key, "
            "    gs.project_id = row.project_id, "
            "    gs.source_id = row.source_id, "
            "    gs.created_at = coalesce(gs.created_at, datetime(row.created_at)), "
            "    gs.updated_at = datetime(row.updated_at) "
            "MERGE (p)-[:HAS_GROUP_SPEC]->(gs) "
            "WITH p, gs, row "
            "OPTIONAL MATCH (p)-[:HAS_MODULE]->(m:Module {mod_code: row.mod_code}) "
            "FOREACH (_ IN CASE WHEN m IS NULL THEN [] ELSE [1] END | MERGE (m)-[:HAS_GROUP_SPEC]->(gs)) "
            "RETURN count(DISTINCT gs) AS count"
        )

        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(cypher, project_id=project_id, rows=rows).single()
            )
        return int(record["count"]) if record else 0
