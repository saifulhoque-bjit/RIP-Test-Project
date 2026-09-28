"""Neo4j repository for ConfigSpec graph operations."""

from __future__ import annotations

import asyncio
from typing import Any

from neo4j import Driver

from app.db.neo4j import get_neo4j_driver


class ConfigSpecRepository:
    """Persistence layer for ConfigSpec nodes in Neo4j."""

    def __init__(self, driver: Driver | None = None) -> None:
        self._driver = driver or get_neo4j_driver()

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
            "MERGE (cs:ConfigSpec {id: row.id}) "
            "SET cs.mod_code = row.mod_code, "
            "    cs.fea_code = row.fea_code, "
            "    cs.filename = row.filename, "
            "    cs.storage_key = row.storage_key, "
            "    cs.project_id = row.project_id, "
            "    cs.source_id = row.source_id, "
            "    cs.created_at = coalesce(cs.created_at, datetime(row.created_at)), "
            "    cs.updated_at = datetime(row.updated_at) "
            "MERGE (p)-[:HAS_CONFIG_SPEC]->(cs) "
            "WITH p, cs, row "
            "OPTIONAL MATCH (p)-[:HAS_MODULE]->(m:Module {mod_code: row.mod_code}) "
            "FOREACH (_ IN CASE WHEN m IS NULL THEN [] ELSE [1] END | MERGE (m)-[:HAS_CONFIG_SPEC]->(cs)) "
            "RETURN count(DISTINCT cs) AS count"
        )

        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(cypher, project_id=project_id, rows=rows).single()
            )
        return int(record["count"]) if record else 0
