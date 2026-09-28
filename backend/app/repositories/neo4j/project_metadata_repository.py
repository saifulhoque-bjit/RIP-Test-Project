"""Neo4j repository for ProjectMetadata graph operations."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from neo4j import Driver

from app.utils.logger import get_logger

logger = get_logger(__name__)


class ProjectMetadataRepository:
    """CRUD for the :ProjectMetadata node in Neo4j.

    One :ProjectMetadata node exists per project, identified by ``project_id``.
    All list/nested fields are stored as JSON strings.
    """

    def __init__(self, driver: Driver) -> None:
        self._driver = driver

    # ── Upsert ─────────────────────────────────────────────────────────────

    def upsert(self, *, project_id: UUID) -> None:
        """Idempotently create a :ProjectMetadata node for the given project.

        Safe to call on every project creation — ON CREATE only sets the
        initial values; subsequent calls only refresh ``updated_at``.
        """
        cypher = (
            "MERGE (pm:ProjectMetadata {project_id: $project_id}) "
            "ON CREATE SET "
            "    pm.id = $id, "
            "    pm.business_requirements = '[]', "
            "    pm.exclusions = '[]', "
            "    pm.persona_glossary = '[]', "
            "    pm.domain_knowledge_storage_key = null, "
            "    pm.architecture_document_storage_key = null, "
            "    pm.created_at = datetime(), "
            "    pm.updated_at = datetime(), "
            "    pm.deleted_at = null "
            "ON MATCH SET pm.updated_at = datetime() "
            "RETURN pm.id AS id"
        )
        with self._driver.session() as session:
            session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    project_id=str(project_id),
                    id=str(uuid4()),
                ).single()
            )
        logger.debug("Neo4j upserted ProjectMetadata node: project_id=%s", project_id)

    # ── Update ─────────────────────────────────────────────────────────────

    def update(
        self,
        *,
        project_id: UUID,
        persona_glossary: list[dict[str, str]] | None = None,
        business_requirements: list[dict] | None = None,
        exclusions: list[str] | None = None,
        domain_knowledge_storage_key: str | None = None,
        architecture_document_storage_key: str | None = None,
    ) -> None:
        """Null-safe update of metadata fields on the :ProjectMetadata node.

        Only fields with a non-``None`` value are written; ``None`` means
        "leave the existing value unchanged".  MERGE ensures the node is
        created automatically if it does not yet exist.
        """
        cypher = (
            "MERGE (pm:ProjectMetadata {project_id: $project_id}) "
            "ON CREATE SET pm.id = $id, pm.created_at = datetime() "
            "SET "
            "    pm.business_requirements = CASE "
            "        WHEN $business_requirements_json IS NULL THEN pm.business_requirements "
            "        ELSE $business_requirements_json END, "
            "    pm.exclusions = CASE "
            "        WHEN $exclusions_json IS NULL THEN pm.exclusions "
            "        ELSE $exclusions_json END, "
            "    pm.persona_glossary = CASE "
            "        WHEN $persona_glossary_json IS NULL THEN pm.persona_glossary "
            "        ELSE $persona_glossary_json END, "
            "    pm.domain_knowledge_storage_key = CASE "
            "        WHEN $domain_knowledge_storage_key IS NULL THEN pm.domain_knowledge_storage_key "
            "        ELSE $domain_knowledge_storage_key END, "
            "    pm.architecture_document_storage_key = CASE "
            "        WHEN $architecture_document_storage_key IS NULL "
            "            THEN pm.architecture_document_storage_key "
            "        ELSE $architecture_document_storage_key END, "
            "    pm.updated_at = datetime() "
            "RETURN pm.id AS id"
        )
        with self._driver.session() as session:
            session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    project_id=str(project_id),
                    id=str(uuid4()),
                    business_requirements_json=(
                        json.dumps(business_requirements, ensure_ascii=False)
                        if business_requirements is not None
                        else None
                    ),
                    exclusions_json=(
                        json.dumps(exclusions, ensure_ascii=False)
                        if exclusions is not None
                        else None
                    ),
                    persona_glossary_json=(
                        json.dumps(persona_glossary, ensure_ascii=False)
                        if persona_glossary is not None
                        else None
                    ),
                    domain_knowledge_storage_key=domain_knowledge_storage_key,
                    architecture_document_storage_key=architecture_document_storage_key,
                ).single()
            )
        logger.debug("Neo4j ProjectMetadata updated: project_id=%s", project_id)

    # ── Read ───────────────────────────────────────────────────────────────

    def get(self, *, project_id: UUID) -> dict[str, Any]:
        """Return all metadata fields for the given project.

        Returns a dict with keys ``business_requirements``, ``exclusions``,
        ``persona_glossary``, ``domain_knowledge_storage_key``, and
        ``architecture_document_storage_key``.  Every list field defaults to
        ``[]`` and every storage-key field defaults to ``None`` when the node
        does not exist or the property is unset.
        """
        cypher = (
            "MATCH (pm:ProjectMetadata {project_id: $project_id}) "
            "RETURN "
            "    pm.business_requirements AS br, "
            "    pm.exclusions            AS excl, "
            "    pm.persona_glossary      AS pg, "
            "    pm.domain_knowledge_storage_key       AS domain_knowledge_storage_key, "
            "    pm.architecture_document_storage_key  AS architecture_document_storage_key"
        )
        with self._driver.session() as session:
            record = session.execute_read(
                lambda tx: tx.run(cypher, project_id=str(project_id)).single()
            )

        def _parse_json_list(raw: Any) -> list:
            if not raw:
                return []
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, list) else []
            except (json.JSONDecodeError, TypeError):
                return []

        result: dict[str, Any] = {
            "business_requirements": [],
            "exclusions": [],
            "persona_glossary": [],
            "domain_knowledge_storage_key": None,
            "architecture_document_storage_key": None,
        }

        if record:
            result["business_requirements"] = _parse_json_list(record["br"])
            result["exclusions"] = _parse_json_list(record["excl"])
            result["persona_glossary"] = _parse_json_list(record["pg"])
            result["domain_knowledge_storage_key"] = record["domain_knowledge_storage_key"]
            result["architecture_document_storage_key"] = record[
                "architecture_document_storage_key"
            ]

        return result
