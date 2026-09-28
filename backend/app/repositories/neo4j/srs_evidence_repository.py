"""Neo4j repository for SRS evidence graph operations."""

from __future__ import annotations

import asyncio
from typing import Any

from neo4j import Driver

from app.db.neo4j import get_neo4j_driver


class SRSEvidenceRepository:
    """Persistence layer for SRSEvidence nodes in Neo4j."""

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
            "MERGE (e:SRSEvidence {id: row.id}) "
            "SET e.module_code = row.module_code, "
            "    e.feature_code = row.feature_code, "
            "    e.user_story_code = row.user_story_code, "
            "    e.group_spec_id = row.group_spec_id, "
            "    e.user_story_id = row.user_story_id, "
            "    e.l2_id = row.l2_id, "
            "    e.file_name = row.file_name, "
            "    e.section_anchor = row.section_anchor, "
            "    e.section_path = row.section_path, "
            "    e.highlight_type = row.highlight_type, "
            "    e.target_string = row.target_string, "
            "    e.exact_quote = row.exact_quote, "
            "    e.line_number = row.line_number, "
            "    e.precision = row.precision, "
            "    e.evidence_role = row.evidence_role, "
            "    e.srs_document_type = row.srs_document_type, "
            "    e.ac_ids = row.ac_ids, "
            "    e.trace_id = row.trace_id, "
            "    e.context_snippet = row.context_snippet, "
            "    e.tier = row.tier, "
            "    e.created_at = coalesce(e.created_at, datetime(row.created_at)), "
            "    e.updated_at = datetime(row.updated_at) "
            "MERGE (p)-[:HAS_SRS_EVIDENCE]->(e) "
            "WITH p, e, row "
            "OPTIONAL MATCH (p)-[:HAS_MODULE]->(:Module)-[:HAS_FEATURE]->(f:Feature {fea_code: row.feature_code}) "
            "FOREACH (_ IN CASE WHEN f IS NULL THEN [] ELSE [1] END | MERGE (f)-[:HAS_SRS_EVIDENCE]->(e)) "
            "WITH p, e, row "
            "OPTIONAL MATCH (gs:GroupSpec {id: row.group_spec_id}) "
            "FOREACH (_ IN CASE WHEN gs IS NULL OR row.group_spec_id = '' THEN [] ELSE [1] END | MERGE (gs)-[:HAS_SRS_EVIDENCE]->(e)) "
            "WITH p, e, row "
            "OPTIONAL MATCH (us:UserStory {id: row.user_story_id, project_id: $project_id}) "
            "FOREACH (_ IN CASE WHEN us IS NULL OR row.user_story_id = '' THEN [] ELSE [1] END | MERGE (us)-[:HAS_SRS_EVIDENCE]->(e)) "
            "RETURN count(e) AS count"
        )

        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(cypher, project_id=project_id, rows=rows).single()
            )
        return int(record["count"]) if record else 0
