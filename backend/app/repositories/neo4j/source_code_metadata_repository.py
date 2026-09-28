"""Neo4j repository for SourceCodeMetadata graph operations."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from neo4j import Driver

from app.db.neo4j import get_neo4j_driver
from app.models.neo4j.source_code_metadata_model import SourceCodeMetadataModel


class SourceCodeMetadataRepository:
    """Persistence layer for SourceCodeMetadata nodes in Neo4j."""

    def __init__(self, driver: Driver | None = None) -> None:
        self._driver = driver or get_neo4j_driver()

    async def get_by_project_and_module(
        self,
        *,
        project_id: str,
        module_id: str,
    ) -> SourceCodeMetadataModel | None:
        """Return the SourceCodeMetadata row for one module of a project.

        The upsert MERGE key is ``(project_id, module_id)``, so there is at
        most one row per module — this is inherently "the latest" one.
        """
        return await asyncio.to_thread(
            self._execute_get_by_project_and_module,
            project_id,
            module_id,
        )

    def _execute_get_by_project_and_module(
        self,
        project_id: str,
        module_id: str,
    ) -> SourceCodeMetadataModel | None:
        cypher = (
            "MATCH (scm:SourceCodeMetadata {project_id: $project_id, module_id: $module_id}) "
            "RETURN scm.id AS id, scm.module_id AS module_id, "
            "       scm.module_response AS module_response, "
            "       scm.module_manifest AS module_manifest, "
            "       scm.source_id AS source_id, scm.project_id AS project_id, "
            "       scm.created_at AS created_at, scm.updated_at AS updated_at"
        )
        with self._driver.session() as session:
            record = session.execute_read(
                lambda tx: tx.run(cypher, project_id=project_id, module_id=module_id).single()
            )
        if record is None:
            return None

        raw_module_response = record["module_response"]
        raw_module_manifest = record["module_manifest"]
        return SourceCodeMetadataModel(
            id=record["id"],
            module_id=record["module_id"],
            module_response=json.loads(raw_module_response) if raw_module_response else None,
            module_manifest=json.loads(raw_module_manifest) if raw_module_manifest else None,
            source_id=record["source_id"],
            project_id=record["project_id"],
            created_at=record["created_at"],
            updated_at=record["updated_at"],
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
            "MERGE (scm:SourceCodeMetadata {project_id: row.project_id, module_id: row.module_id}) "
            "SET scm.id              = coalesce(scm.id, row.id), "
            "    scm.source_id       = row.source_id, "
            "    scm.module_response = row.module_response, "
            "    scm.module_manifest = row.module_manifest, "
            "    scm.created_at      = coalesce(scm.created_at, datetime(row.created_at)), "
            "    scm.updated_at      = datetime(row.updated_at) "
            "MERGE (p)-[:HAS_SOURCE_CODE_METADATA]->(scm) "
            "WITH p, scm, row "
            "OPTIONAL MATCH (p)-[:HAS_MODULE]->(m:Module {mod_code: row.module_id}) "
            "FOREACH (_ IN CASE WHEN m IS NULL THEN [] ELSE [1] END | MERGE (m)-[:HAS_SOURCE_CODE_METADATA]->(scm)) "
            "RETURN count(DISTINCT scm) AS count"
        )

        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(cypher, project_id=project_id, rows=rows).single()
            )
        return int(record["count"]) if record else 0
