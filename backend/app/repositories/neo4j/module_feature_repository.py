"""Neo4j repository for module-feature graph operations."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
import json as _json
import re
from typing import Any
from uuid import UUID

from neo4j import Driver

from app.core.constants import INITIAL_ENTITY_VERSION
from app.core.exceptions import NotFoundError
from app.db.neo4j import get_neo4j_driver
from app.models.neo4j.module_feature_model import (
    ChangeType,
    FeatureModel,
    FunctionModel,
    ModuleFeatureStatus,
    ModuleModel,
)
from app.models.neo4j.version_model import FeatureVersionModel, ModuleVersionModel
from app.schemas.module_feature_schema import ModuleFeatureStatusChangeRequest
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ModuleFeatureRepository:
    """Persistence layer for source-scoped modules and features in Neo4j."""

    def __init__(self, driver: Driver | None = None) -> None:
        self._driver = driver or get_neo4j_driver()

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

    @staticmethod
    def _parse_functions(value: str | None) -> list[FunctionModel]:
        """Deserialise the JSON-string ``functions`` property stored on a Feature node."""
        if not value:
            return []
        try:
            items = json.loads(value)
            return [
                FunctionModel(
                    fun_code=item.get("fun_code"),
                    name=item.get("name", ""),
                    description=item.get("description"),
                    func_src_ref=item.get("func_src_ref"),
                )
                for item in items
            ]
        except (json.JSONDecodeError, TypeError):
            return []

    @staticmethod
    def _parse_sources(value: str | None) -> list[dict]:
        """Deserialise the JSON-string ``sources`` property stored on a Feature node.

        Legacy rows written before source entries were normalized may contain
        bare source-id strings instead of ``{source_id, pages}`` dicts; coerce
        those into the expected shape instead of passing them through raw.
        """
        if not value:
            return []
        try:
            items = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(items, list):
            return []
        normalized: list[dict] = []
        for item in items:
            if isinstance(item, dict):
                normalized.append(item)
            elif isinstance(item, str) and item:
                normalized.append({"source_id": item, "pages": []})
        return normalized

    @staticmethod
    def _parse_l2_sources(value: str | None) -> list[str]:
        """Deserialise the JSON-string ``l2_sources`` property stored on a Feature node.

        Distinct from ``sources``: these are granular L2 source-reference IDs
        from the source-code pipeline (e.g. "SRS::MFU-001::S5::EVENT-001"),
        not {source_id, pages} evidence objects.
        """
        if not value:
            return []
        try:
            items = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(items, list):
            return []
        return [str(item) for item in items if isinstance(item, str) and item]

    @staticmethod
    def _parse_text_diffs(value: str | None) -> dict:
        """Deserialise the JSON-string ``text_diffs`` property stored on a Module/Feature node.

        Nested-dict shape keyed by field name, emitted directly by the LLM
        (see ``FeatureTextDiffs``/``ModuleTextDiffs`` in
        ``app.schemas.rfp_pipeline_v2_graph_schema``). A stored value in the
        old flat-list shape is treated as empty — it predates this format and
        carries no reliable field attribution.
        """
        if not value:
            return {}
        try:
            items = json.loads(value)
            if isinstance(items, dict):
                return items
            return {}
        except (json.JSONDecodeError, TypeError):
            return {}

    @staticmethod
    def _parse_rfp_flagged_item(value: str | None) -> dict | None:
        """Deserialise the JSON-string ``rfp_flagged_item`` property, or None if unset."""
        if not value:
            return None
        try:
            item = json.loads(value)
            return item if isinstance(item, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None

    async def upsert_modules_and_features_v2(
        self, project_id: UUID, module: ModuleModel
    ) -> ModuleModel:
        return await asyncio.to_thread(
            self._execute_upsert_modules_and_features_v2, str(project_id), module
        )

    async def upsert_modules_and_features_for_source_code(
        self, project_id: UUID, module: ModuleModel
    ) -> ModuleModel:
        return await asyncio.to_thread(
            self._execute_upsert_modules_and_features_for_source_code, str(project_id), module
        )

    async def list_modules_by_project(
        self,
        project_id: UUID,
        *,
        source_ingestion_id: str | None = None,
    ) -> list[ModuleModel]:
        """Return all Module nodes for the provided project ID.

        Uses a single batched Cypher query to avoid N+1 round trips.
        Results are ordered by module name ascending. When
        ``source_ingestion_id`` is provided, only modules whose own
        ``source_ingestion_id`` matches are returned.
        """
        return await asyncio.to_thread(
            self._execute_list_modules_by_project, str(project_id), source_ingestion_id
        )

    async def get_module_for_project(
        self,
        project_id: UUID,
        module_id: str,
    ) -> ModuleModel | None:
        """Return a single Module node by project and module ID."""
        return await asyncio.to_thread(self._execute_get_module_v2, str(project_id), module_id)

    async def get_module_by_mod_code(
        self,
        project_id: UUID,
        mod_code: str,
    ) -> ModuleModel | None:
        """Return a single Module node (with its features) by project and ``mod_code``.

        Used by feedback-driven regeneration to resolve a pre-existing module
        via its pipeline-facing business code, before the caller's internal
        UUID for that module is known.
        """
        return await asyncio.to_thread(
            self._execute_get_module_by_mod_code, str(project_id), mod_code
        )

    async def get_feature_for_module(
        self,
        project_id: UUID,
        module_id: str,
        feature_id: str,
    ) -> FeatureModel | None:
        """Return a single Feature node scoped to its project and parent module."""
        return await asyncio.to_thread(
            self._execute_get_feature_for_module, str(project_id), module_id, feature_id
        )

    _FEATURE_RETURN_CLAUSE = (
        "RETURN f.id AS feature_id, "
        "       f.module_id AS module_id, "
        "       f.name AS name, "
        "       f.description AS description, "
        "       f.project_id AS project_id, "
        "       f.fea_code AS fea_code, "
        "       f.mfu_id AS mfu_id, "
        "       f.version AS version, "
        "       f.status AS status, "
        "       f.justification AS justification, "
        "       f.incremental_change_type AS incremental_change_type, "
        "       f.feedback_change_type AS feedback_change_type, "
        "       f.generation_metadata AS generation_metadata, "
        "       f.is_infrastructure AS is_infrastructure, "
        "       f.condensation_note AS condensation_note, "
        "       coalesce(f.is_jira_synced, false) AS is_jira_synced, "
        "       coalesce(f.is_tap_synced, false) AS is_tap_synced, "
        "       f.functions AS functions, "
        "       f.sources AS sources, "
        "       f.l2_sources AS l2_sources, "
        "       f.text_diffs AS text_diffs, "
        "       f.created_at AS created_at, "
        "       f.updated_at AS updated_at, "
        "       f.deleted_at AS deleted_at"
    )

    @staticmethod
    def _feature_record_to_model(record: Any, fallback_project_id: str) -> FeatureModel:
        return FeatureModel(
            id=str(record["feature_id"]),
            module_id=str(record["module_id"]),
            name=str(record["name"]),
            description=record["description"],
            project_id=UUID(record["project_id"])
            if record.get("project_id")
            else UUID(fallback_project_id),
            fea_code=record["fea_code"],
            mfu_id=record.get("mfu_id"),
            version=int(record["version"])
            if record.get("version") is not None
            else INITIAL_ENTITY_VERSION,
            status=record["status"] or ModuleFeatureStatus.READY,
            justification=record.get("justification"),
            incremental_change_type=record.get("incremental_change_type"),
            feedback_change_type=record.get("feedback_change_type"),
            generation_metadata=(
                _json.loads(record["generation_metadata"])
                if isinstance(record.get("generation_metadata"), str)
                else record.get("generation_metadata")
            ),
            is_infrastructure=record.get("is_infrastructure"),
            condensation_note=record.get("condensation_note"),
            is_jira_synced=bool(record.get("is_jira_synced")),
            is_tap_synced=bool(record.get("is_tap_synced")),
            functions=ModuleFeatureRepository._parse_functions(record.get("functions")),
            sources=ModuleFeatureRepository._parse_sources(record.get("sources")),
            l2_sources=ModuleFeatureRepository._parse_l2_sources(record.get("l2_sources")),
            text_diffs=ModuleFeatureRepository._parse_text_diffs(record.get("text_diffs")),
            created_at=ModuleFeatureRepository._neo4j_dt_to_py(record.get("created_at")),
            updated_at=ModuleFeatureRepository._neo4j_dt_to_py(record.get("updated_at")),
            deleted_at=ModuleFeatureRepository._neo4j_dt_to_py(record.get("deleted_at")),
        )

    def _execute_get_feature_for_module(
        self, project_id: str, module_id: str, feature_id: str
    ) -> FeatureModel | None:
        cypher = (
            "MATCH (:Project {id: $project_id})-[:HAS_MODULE]->(:Module {id: $module_id})"
            "-[:HAS_FEATURE]->(f:Feature {id: $feature_id}) "
            "WHERE f.deleted_at IS NULL "
        ) + self._FEATURE_RETURN_CLAUSE
        with self._driver.session() as session:
            record = session.execute_read(
                lambda tx: tx.run(
                    cypher,
                    project_id=project_id,
                    module_id=module_id,
                    feature_id=feature_id,
                ).single()
            )
        if record is None:
            return None
        return self._feature_record_to_model(record, project_id)

    async def get_feature_by_mod_code_and_mfu(
        self,
        project_id: UUID,
        mod_code: str,
        mfu_id: str,
    ) -> FeatureModel | None:
        """Return a Feature by its parent module's ``mod_code`` and its ``mfu_id``.

        Used by feedback-driven MFU regeneration, where the caller supplies the
        pipeline-facing ``module_id``/``mfu_id`` pair (not the internal Neo4j
        ids) and no longer pins a specific ``feature_id``. If more than one
        Feature shares the same ``mfu_id`` under that module, the earliest-
        created one is returned — mirroring the single-feature assumption this
        regeneration flow has always made.
        """
        return await asyncio.to_thread(
            self._execute_get_feature_by_mod_code_and_mfu, str(project_id), mod_code, mfu_id
        )

    def _execute_get_feature_by_mod_code_and_mfu(
        self, project_id: str, mod_code: str, mfu_id: str
    ) -> FeatureModel | None:
        cypher = (
            (
                "MATCH (:Project {id: $project_id})-[:HAS_MODULE]->(:Module {mod_code: $mod_code})"
                "-[:HAS_FEATURE]->(f:Feature {mfu_id: $mfu_id}) "
                "WHERE f.deleted_at IS NULL "
            )
            + self._FEATURE_RETURN_CLAUSE
            + " ORDER BY f.created_at ASC LIMIT 1"
        )
        with self._driver.session() as session:
            record = session.execute_read(
                lambda tx: tx.run(
                    cypher,
                    project_id=project_id,
                    mod_code=mod_code,
                    mfu_id=mfu_id,
                ).single()
            )
        if record is None:
            return None
        return self._feature_record_to_model(record, project_id)

    async def get_module_id_for_feature(
        self,
        project_id: UUID,
        feature_id: str,
    ) -> str | None:
        """Return the parent module ID for a feature, or None if the feature does not exist."""
        return await asyncio.to_thread(
            self._execute_get_module_id_for_feature, str(project_id), feature_id
        )

    def _execute_get_module_id_for_feature(self, project_id: str, feature_id: str) -> str | None:
        cypher = (
            "MATCH (f:Feature {project_id: $project_id, id: $feature_id}) "
            "RETURN f.module_id AS module_id"
        )
        with self._driver.session() as session:
            record = session.execute_read(
                lambda tx: tx.run(cypher, project_id=project_id, feature_id=feature_id).single()
            )
        if record is None:
            return None
        return record["module_id"]

    async def count_features_for_module(
        self,
        project_id: UUID,
        module_id: str,
    ) -> int:
        """Count Feature nodes remaining under a module."""
        return await asyncio.to_thread(
            self._execute_count_features_for_module, str(project_id), module_id
        )

    def _execute_count_features_for_module(self, project_id: str, module_id: str) -> int:
        cypher = (
            "MATCH (:Project {id: $project_id})-[:HAS_MODULE]->"
            "(m:Module {id: $module_id})-[:HAS_FEATURE]->(f:Feature) "
            "WHERE f.deleted_at IS NULL "
            "RETURN count(f) AS feature_count"
        )
        with self._driver.session() as session:
            record = session.execute_read(
                lambda tx: tx.run(cypher, project_id=project_id, module_id=module_id).single()
            )
        if record is None:
            return 0
        return int(record["feature_count"])

    async def are_all_modules_approved(self, project_id: UUID) -> bool:
        """Return True only when every module in the project has status 'approved'.

        Returns False when the project has no modules.
        """
        return await asyncio.to_thread(
            self._execute_are_all_modules_approved,
            str(project_id),
        )

    def _execute_are_all_modules_approved(self, project_id: str) -> bool:
        cypher = (
            "MATCH (p:Project {id: $project_id})-[:HAS_MODULE]->(m:Module) "
            "WHERE m.deleted_at IS NULL "
            "WITH count(DISTINCT m) AS total, "
            "     count(DISTINCT CASE WHEN m.status = $approved_status THEN m END) AS approved "
            "RETURN total > 0 AND total = approved AS are_all_approved"
        )
        with self._driver.session() as session:
            record = session.execute_read(
                lambda tx: tx.run(
                    cypher,
                    project_id=project_id,
                    approved_status=ModuleFeatureStatus.APPROVED.value,
                ).single()
            )
        return bool(record["are_all_approved"]) if record else False

    async def are_all_features_approved(self, project_id: UUID) -> bool:
        """Return True only when every feature in the project has status 'approved'.

        Returns False when the project has no features.
        """
        return await asyncio.to_thread(
            self._execute_are_all_features_approved,
            str(project_id),
        )

    def _execute_are_all_features_approved(self, project_id: str) -> bool:
        cypher = (
            "MATCH (p:Project {id: $project_id})-[:HAS_MODULE]->(:Module)-[:HAS_FEATURE]->(f:Feature) "
            "WHERE f.deleted_at IS NULL "
            "WITH count(DISTINCT f) AS total, "
            "     count(DISTINCT CASE WHEN f.status = $approved_status THEN f END) AS approved "
            "RETURN total > 0 AND total = approved AS are_all_approved"
        )
        with self._driver.session() as session:
            record = session.execute_read(
                lambda tx: tx.run(
                    cypher,
                    project_id=project_id,
                    approved_status=ModuleFeatureStatus.APPROVED.value,
                ).single()
            )
        return bool(record["are_all_approved"]) if record else False

    async def count_pending_changes_by_ingestion(self, source_ingestion_id: str) -> int:
        """Return how many Module/Feature nodes tagged by *source_ingestion_id* are still unresolved.

        A node counts as pending while its ``incremental_change_type`` or its
        ``feedback_change_type`` is non-null — the two flags are set by two
        independent flows (``IncrementalUpdatesService`` and
        ``FeedbackUpdateService`` respectively, see their module docstrings)
        that never both apply to the same node, so checking either covers a
        node flagged by whichever flow tagged this ingestion.
        """
        return await asyncio.to_thread(
            self._execute_count_pending_changes_by_ingestion,
            source_ingestion_id,
        )

    def _execute_count_pending_changes_by_ingestion(self, source_ingestion_id: str) -> int:
        cypher = (
            "MATCH (m:Module {source_ingestion_id: $source_ingestion_id}) "
            "WHERE m.incremental_change_type IS NOT NULL OR m.feedback_change_type IS NOT NULL "
            "WITH count(m) AS module_pending "
            "MATCH (f:Feature {source_ingestion_id: $source_ingestion_id}) "
            "WHERE f.incremental_change_type IS NOT NULL OR f.feedback_change_type IS NOT NULL "
            "WITH module_pending, count(f) AS feature_pending "
            "RETURN module_pending + feature_pending AS pending"
        )
        with self._driver.session() as session:
            record = session.execute_read(
                lambda tx: tx.run(cypher, source_ingestion_id=source_ingestion_id).single()
            )
        return int(record["pending"]) if record else 0

    async def get_source_ingestion_id(
        self, project_id: UUID, entity_type: str, entity_id: str
    ) -> str | None:
        """Return the ``source_ingestion_id`` tagged on a Module or Feature node.

        ``entity_type`` is ``"module"`` or ``"feature"`` — used only to pick
        which fixed Cypher label to match, never interpolated into the query.
        """
        return await asyncio.to_thread(
            self._execute_get_source_ingestion_id, str(project_id), entity_type, entity_id
        )

    def _execute_get_source_ingestion_id(
        self, project_id: str, entity_type: str, entity_id: str
    ) -> str | None:
        label = "Module" if entity_type == "module" else "Feature"
        cypher = (
            f"MATCH (n:{label} {{id: $entity_id, project_id: $project_id}}) "
            "RETURN n.source_ingestion_id AS source_ingestion_id"
        )
        with self._driver.session() as session:
            record = session.execute_read(
                lambda tx: tx.run(cypher, entity_id=entity_id, project_id=project_id).single()
            )
        return record["source_ingestion_id"] if record else None

    async def count_pending_feedback_changes_by_project(self, project_id: UUID) -> int:
        """Return how many Module/Feature nodes in this project have an unresolved
        ``feedback_change_type`` (ADDED, UPDATED, or DELETE_SUGGESTED), awaiting
        accept/reject via ``/feedback-updates``.
        """
        return await asyncio.to_thread(
            self._execute_count_pending_feedback_changes_by_project, str(project_id)
        )

    def _execute_count_pending_feedback_changes_by_project(self, project_id: str) -> int:
        cypher = (
            "MATCH (m:Module {project_id: $project_id}) "
            "WHERE m.feedback_change_type IS NOT NULL "
            "WITH count(m) AS module_pending "
            "MATCH (f:Feature {project_id: $project_id}) "
            "WHERE f.feedback_change_type IS NOT NULL "
            "WITH module_pending, count(f) AS feature_pending "
            "RETURN module_pending + feature_pending AS pending"
        )
        with self._driver.session() as session:
            record = session.execute_read(
                lambda tx: tx.run(cypher, project_id=project_id).single()
            )
        return int(record["pending"]) if record else 0

    async def delete_feature_by_id(
        self,
        project_id: UUID,
        feature_id: str,
    ) -> bool:
        """Hard-delete a Feature node (and its relationships) scoped to a project."""
        return await asyncio.to_thread(
            self._execute_delete_feature_by_id, str(project_id), feature_id
        )

    def _execute_delete_feature_by_id(self, project_id: str, feature_id: str) -> bool:
        cypher = (
            "MATCH (f:Feature {project_id: $project_id, id: $feature_id}) "
            "DETACH DELETE f "
            "RETURN count(f) AS deleted_count"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(cypher, project_id=project_id, feature_id=feature_id).single()
            )
        return bool(record and int(record["deleted_count"]) > 0)

    async def delete_module_by_id(
        self,
        project_id: UUID,
        module_id: str,
    ) -> bool:
        """Hard-delete a Module node (and its relationships) scoped to a project."""
        return await asyncio.to_thread(
            self._execute_delete_module_by_id, str(project_id), module_id
        )

    def _execute_delete_module_by_id(self, project_id: str, module_id: str) -> bool:
        cypher = (
            "MATCH (m:Module {project_id: $project_id, id: $module_id}) "
            "DETACH DELETE m "
            "RETURN count(m) AS deleted_count"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(cypher, project_id=project_id, module_id=module_id).single()
            )
        return bool(record and int(record["deleted_count"]) > 0)

    async def hard_delete_module_cascade(
        self,
        project_id: UUID,
        module_id: str,
    ) -> bool:
        """Hard-delete a Module together with all its Features, their UserStories, and
        every ModuleVersion/FeatureVersion/UserStoryVersion snapshot attached to them.
        """
        return await asyncio.to_thread(
            self._execute_hard_delete_module_cascade, str(project_id), module_id
        )

    def _execute_hard_delete_module_cascade(self, project_id: str, module_id: str) -> bool:
        cypher = (
            "MATCH (m:Module {project_id: $project_id, id: $module_id}) "
            "OPTIONAL MATCH (m)-[:HAS_VERSION]->(mv:ModuleVersion) "
            "OPTIONAL MATCH (m)-[:HAS_FEATURE]->(f:Feature) "
            "OPTIONAL MATCH (f)-[:HAS_VERSION]->(fv:FeatureVersion) "
            "OPTIONAL MATCH (f)-[:HAS_USER_STORY]->(r:UserStory) "
            "OPTIONAL MATCH (r)-[:HAS_VERSION]->(rv:UserStoryVersion) "
            "DETACH DELETE mv, fv, rv, r, f, m "
            "RETURN count(DISTINCT m) AS deleted_count"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(cypher, project_id=project_id, module_id=module_id).single()
            )
        return bool(record and int(record["deleted_count"]) > 0)

    async def soft_delete_module_cascade(
        self,
        project_id: UUID,
        module_id: str,
        reason: str,
    ) -> bool:
        """Soft-delete a Module and cascade the same deletion fields onto its Features
        and their UserStories.
        """
        return await asyncio.to_thread(
            self._execute_soft_delete_module_cascade, str(project_id), module_id, reason
        )

    def _execute_soft_delete_module_cascade(
        self, project_id: str, module_id: str, reason: str
    ) -> bool:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (m:Module {project_id: $project_id, id: $module_id}) "
            "SET m.is_deleted = true, "
            "    m.deletion_reason = $reason, "
            "    m.deleted_at = datetime($now), "
            "    m.updated_at = datetime($now) "
            "WITH m "
            "OPTIONAL MATCH (m)-[:HAS_FEATURE]->(f:Feature) "
            "SET f.is_deleted = true, "
            "    f.deletion_reason = $reason, "
            "    f.deleted_at = datetime($now), "
            "    f.updated_at = datetime($now) "
            "WITH m, f "
            "OPTIONAL MATCH (f)-[:HAS_USER_STORY]->(r:UserStory) "
            "SET r.is_current = false, "
            "    r.del_reason = $reason, "
            "    r.deleted_at = datetime($now), "
            "    r.updated_at = datetime($now) "
            "RETURN count(DISTINCT m) AS updated_count"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher, project_id=project_id, module_id=module_id, reason=reason, now=now
                ).single()
            )
        return bool(record and int(record["updated_count"]) > 0)

    async def hard_delete_feature_cascade(
        self,
        project_id: UUID,
        module_id: str,
        feature_id: str,
    ) -> bool:
        """Hard-delete a Feature together with its UserStories and every
        FeatureVersion/UserStoryVersion snapshot attached to them, scoped to its parent module.
        """
        return await asyncio.to_thread(
            self._execute_hard_delete_feature_cascade, str(project_id), module_id, feature_id
        )

    def _execute_hard_delete_feature_cascade(
        self, project_id: str, module_id: str, feature_id: str
    ) -> bool:
        cypher = (
            "MATCH (f:Feature {project_id: $project_id, module_id: $module_id, id: $feature_id}) "
            "OPTIONAL MATCH (f)-[:HAS_VERSION]->(fv:FeatureVersion) "
            "OPTIONAL MATCH (f)-[:HAS_USER_STORY]->(r:UserStory) "
            "OPTIONAL MATCH (r)-[:HAS_VERSION]->(rv:UserStoryVersion) "
            "DETACH DELETE fv, rv, r, f "
            "RETURN count(DISTINCT f) AS deleted_count"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher, project_id=project_id, module_id=module_id, feature_id=feature_id
                ).single()
            )
        return bool(record and int(record["deleted_count"]) > 0)

    async def soft_delete_feature_by_id(
        self,
        project_id: UUID,
        module_id: str,
        feature_id: str,
        reason: str,
    ) -> bool:
        """Soft-delete a single Feature and cascade the same deletion fields onto its
        UserStories.
        """
        return await asyncio.to_thread(
            self._execute_soft_delete_feature_by_id, str(project_id), module_id, feature_id, reason
        )

    def _execute_soft_delete_feature_by_id(
        self, project_id: str, module_id: str, feature_id: str, reason: str
    ) -> bool:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (f:Feature {project_id: $project_id, module_id: $module_id, id: $feature_id}) "
            "SET f.is_deleted = true, "
            "    f.deletion_reason = $reason, "
            "    f.deleted_at = datetime($now), "
            "    f.updated_at = datetime($now) "
            "WITH f "
            "OPTIONAL MATCH (f)-[:HAS_USER_STORY]->(r:UserStory) "
            "SET r.is_current = false, "
            "    r.del_reason = $reason, "
            "    r.deleted_at = datetime($now), "
            "    r.updated_at = datetime($now) "
            "RETURN count(DISTINCT f) AS updated_count"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    project_id=project_id,
                    module_id=module_id,
                    feature_id=feature_id,
                    reason=reason,
                    now=now,
                ).single()
            )
        return bool(record and int(record["updated_count"]) > 0)

    async def change_module_feature_status_for_project(
        self,
        project_id: UUID,
        payload: ModuleFeatureStatusChangeRequest,
    ) -> ModuleModel | None:
        """Change status for all modules and their child features in a project.

        Performs an update on all modules and all child feature nodes
        within a Neo4j session. Updates the status property on both the Module
        node and all child Feature nodes, along with their updated_at timestamps.

        Parameters
        ----------
        project_id : UUID
            UUID of the project containing the modules.
        payload : ModuleFeatureStatusChangeRequest
            Request payload containing new status.

        Returns
        -------
        ModuleModel | None
            One updated ModuleModel snapshot with child features and new status,
            or None if no module was found in the project.

        Notes
        -----
        - The update is performed atomically within a Neo4j transaction
        - Both module.updated_at and all feature.updated_at timestamps are updated
        - All child features receive the same status as the parent module
        """
        return await asyncio.to_thread(
            self._execute_change_module_feature_status_for_project,
            str(project_id),
            payload.status.value,
        )

    def _execute_count_modules_and_features_for_project(
        self, project_id: str
    ) -> tuple[int, int]:
        """Count non-deleted modules and features under a project."""
        cypher = (
            "MATCH (:Project {id: $project_id})-[:HAS_MODULE]->(m:Module) "
            "WHERE m.deleted_at IS NULL "
            "WITH count(DISTINCT m) AS total_modules "
            "OPTIONAL MATCH (:Project {id: $project_id})-[:HAS_MODULE]->(m2:Module)"
            "-[:HAS_FEATURE]->(f:Feature) "
            "WHERE m2.deleted_at IS NULL AND f.deleted_at IS NULL "
            "RETURN total_modules, count(f) AS total_features"
        )
        with self._driver.session() as session:
            record = session.execute_read(
                lambda tx: tx.run(cypher, project_id=project_id).single()
            )
        if record is None:
            return 0, 0
        return int(record["total_modules"] or 0), int(record["total_features"] or 0)

    async def count_modules_and_features_for_project(self, project_id: UUID) -> tuple[int, int]:
        """Return ``(total_modules, total_features)`` currently in a project's graph."""
        return await asyncio.to_thread(
            self._execute_count_modules_and_features_for_project, str(project_id)
        )

    def _execute_upsert_modules_and_features_v2(
        self, project_id: str, module: ModuleModel
    ) -> ModuleModel:
        now = datetime.now(UTC).isoformat()
        # MERGE on the natural business key (project_id + mod_code) so that
        # re-running the task finds the existing node instead of creating a
        # duplicate.  The synthetic UUID `m.id` is preserved on update via
        # coalesce so external references remain stable.
        # Features are likewise merged on (project_id + fea_code) — existing
        # nodes get their mutable fields updated; new nodes are created.
        # Orphaned features are intentionally kept: regeneration refines the
        # AI output but should never silently remove user-reviewed features.
        # `version` bumps by 1 only when feedback_change_type resolves to
        # UPDATED — i.e. regeneration actually changed content and the prior
        # state was already snapshotted to a Module/FeatureVersion node by
        # the caller. Every other path (unchanged, ADDED, first-time
        # generation) coalesces, preserving whatever version is already
        # stored (or seeding it for a brand-new node).
        cypher = (
            "MATCH (p:Project {id: $project_id}) "
            "MERGE (m:Module {project_id: $project_id, mod_code: $mod_code}) "
            "SET m.id          = coalesce(m.id, $module_id), "
            "    m.name                 = $name, "
            "    m.description          = $description, "
            "    m.status               = coalesce(m.status, $status), "
            "    m.version              = CASE WHEN $module_version_bump "
            "                                  THEN coalesce(m.version, 0) + 1 "
            "                                  ELSE coalesce(m.version, $version) END, "
            "    m.feedback_change_type = coalesce($feedback_change_type, m.feedback_change_type), "
            "    m.is_jira_synced       = coalesce(m.is_jira_synced, false), "
            "    m.is_tap_synced        = coalesce(m.is_tap_synced, false), "
            "    m.source_ingestion_id  = CASE WHEN m.source_ingestion_id IS NULL "
            "                                  OR $module_version_bump "
            "                                  THEN $source_ingestion_id "
            "                                  ELSE m.source_ingestion_id END, "
            "    m.created_at           = coalesce(m.created_at, datetime($now)), "
            "    m.updated_at           = datetime($now) "
            "MERGE (p)-[:HAS_MODULE]->(m) "
            "WITH m, $project_id AS project_id, $features AS features "
            "UNWIND features AS feature "
            "MERGE (f:Feature {project_id: project_id, fea_code: feature.fea_code}) "
            "SET f.id          = coalesce(f.id, feature.id), "
            "    f.module_id   = m.id, "
            "    f.name        = feature.name, "
            "    f.description = feature.description, "
            "    f.status      = coalesce(f.status, $status), "
            "    f.version     = CASE WHEN feature.version_bump "
            "                         THEN coalesce(f.version, 0) + 1 "
            "                         ELSE coalesce(f.version, $version) END, "
            "    f.functions   = feature.functions, "
            "    f.sources     = feature.sources, "
            "    f.l2_sources  = feature.l2_sources, "
            "    f.generation_metadata  = feature.generation_metadata, "
            "    f.feedback_change_type = coalesce(feature.feedback_change_type, f.feedback_change_type), "
            "    f.is_jira_synced = coalesce(f.is_jira_synced, false), "
            "    f.is_tap_synced = coalesce(f.is_tap_synced, false), "
            "    f.source_ingestion_id = CASE WHEN f.source_ingestion_id IS NULL "
            "                                 OR feature.version_bump "
            "                                 THEN feature.source_ingestion_id "
            "                                 ELSE f.source_ingestion_id END, "
            "    f.created_at  = coalesce(f.created_at, datetime($now)), "
            "    f.updated_at  = datetime($now) "
            "MERGE (m)-[:HAS_FEATURE]->(f) "
            "RETURN DISTINCT m.id AS module_id"
        )
        feature_rows = [
            {
                "id": feature.id,
                "module_id": feature.module_id,
                "name": feature.name,
                "description": feature.description,
                "fea_code": feature.fea_code,
                "functions": json.dumps(
                    [
                        {
                            "fun_code": fn.fun_code,
                            "name": fn.name,
                            "description": fn.description,
                            "func_src_ref": fn.func_src_ref,
                        }
                        for fn in feature.functions
                    ]
                ),
                "sources": json.dumps(feature.sources),
                "l2_sources": json.dumps(feature.l2_sources),
                "generation_metadata": json.dumps(feature.generation_metadata)
                if feature.generation_metadata is not None
                else None,
                "feedback_change_type": feature.feedback_change_type.value
                if feature.feedback_change_type is not None
                else None,
                "version_bump": feature.feedback_change_type == ChangeType.UPDATED,
                "source_ingestion_id": feature.source_ingestion_id,
            }
            for feature in module.features
        ]
        with self._driver.session() as session:
            result = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    project_id=project_id,
                    module_id=module.id,
                    name=module.name,
                    description=module.description,
                    status=ModuleFeatureStatus.READY,
                    version=INITIAL_ENTITY_VERSION,
                    mod_code=module.mod_code,
                    feedback_change_type=module.feedback_change_type.value
                    if module.feedback_change_type is not None
                    else None,
                    module_version_bump=module.feedback_change_type == ChangeType.UPDATED,
                    source_ingestion_id=module.source_ingestion_id,
                    features=feature_rows,
                    now=now,
                ).single()
            )

        if result is None:
            raise NotFoundError(
                f"Project {project_id} not found in Neo4j; module {module.id} was not persisted."
            )
        return module

    def _execute_upsert_modules_and_features_for_source_code(
        self, project_id: str, module: ModuleModel
    ) -> ModuleModel:
        now = datetime.now(UTC).isoformat()
        # MERGE on the natural business key (project_id + mod_code) so that
        # re-running the task finds the existing node instead of creating a
        # duplicate.  The synthetic UUID `m.id` is preserved on update via
        # coalesce so external references remain stable.
        # Features are likewise merged on (project_id + fea_code) — existing
        # nodes get their mutable fields updated; new nodes are created.
        # Orphaned features are intentionally kept: regeneration refines the
        # AI output but should never silently remove user-reviewed features.
        cypher = (
            "MATCH (p:Project {id: $project_id}) "
            "MERGE (m:Module {project_id: $project_id, mod_code: $mod_code}) "
            "SET m.id          = coalesce(m.id, $module_id), "
            "    m.name                 = $name, "
            "    m.description          = $description, "
            "    m.status               = coalesce(m.status, $status), "
            "    m.version              = coalesce(m.version, $version), "
            "    m.is_jira_synced       = coalesce(m.is_jira_synced, false), "
            "    m.is_tap_synced        = coalesce(m.is_tap_synced, false), "
            "    m.source_ingestion_id  = $source_ingestion_id, "
            "    m.created_at           = coalesce(m.created_at, datetime($now)), "
            "    m.updated_at           = datetime($now) "
            "MERGE (p)-[:HAS_MODULE]->(m) "
            "WITH m, $project_id AS project_id, $features AS features "
            "UNWIND features AS feature "
            "MERGE (f:Feature {project_id: project_id, fea_code: feature.fea_code}) "
            "SET f.id          = coalesce(f.id, feature.id), "
            "    f.module_id   = m.id, "
            "    f.name        = feature.name, "
            "    f.description = feature.description, "
            "    f.mfu_id               = feature.mfu_id, "
            "    f.status               = coalesce(f.status, $status), "
            "    f.version              = coalesce(f.version, $version), "
            "    f.functions            = feature.functions, "
            "    f.sources              = feature.sources, "
            "    f.l2_sources           = feature.l2_sources, "
            "    f.generation_metadata  = feature.generation_metadata, "
            "    f.is_infrastructure    = feature.is_infrastructure, "
            "    f.condensation_note    = feature.condensation_note, "
            "    f.is_jira_synced       = coalesce(f.is_jira_synced, false), "
            "    f.is_tap_synced        = coalesce(f.is_tap_synced, false), "
            "    f.source_ingestion_id  = feature.source_ingestion_id, "
            "    f.created_at           = coalesce(f.created_at, datetime($now)), "
            "    f.updated_at           = datetime($now) "
            "MERGE (m)-[:HAS_FEATURE]->(f) "
            "RETURN DISTINCT m.id AS module_id"
        )
        feature_rows = [
            {
                "id": feature.id,
                "module_id": feature.module_id,
                "name": feature.name,
                "description": feature.description,
                "fea_code": feature.fea_code,
                "mfu_id": feature.mfu_id,
                "is_infrastructure": feature.is_infrastructure,
                "condensation_note": feature.condensation_note,
                "generation_metadata": json.dumps(feature.generation_metadata)
                if feature.generation_metadata is not None
                else None,
                "functions": json.dumps(
                    [
                        {
                            "fun_code": fn.fun_code,
                            "name": fn.name,
                            "description": fn.description,
                            "func_src_ref": fn.func_src_ref,
                        }
                        for fn in feature.functions
                    ]
                ),
                "sources": json.dumps(feature.sources),
                "l2_sources": json.dumps(feature.l2_sources),
                "source_ingestion_id": feature.source_ingestion_id,
            }
            for feature in module.features
        ]
        with self._driver.session() as session:
            result = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    project_id=project_id,
                    module_id=module.id,
                    name=module.name,
                    description=module.description,
                    status=ModuleFeatureStatus.READY,
                    version=INITIAL_ENTITY_VERSION,
                    mod_code=module.mod_code,
                    source_ingestion_id=module.source_ingestion_id,
                    features=feature_rows,
                    now=now,
                ).single()
            )

        if result is None:
            # Log module details for easier debugging of failed upserts.
            logger.error(
                "Module upsert failed for source-code flow: project_id=%s module_id=%s mod_code=%s module_name=%s features=%d",
                project_id,
                module.id,
                module.mod_code,
                module.name,
                len(module.features or []),
            )

            raise NotFoundError(
                f"Project {project_id} not found in Neo4j; module {module.id} was not persisted."
            )
        return module

    _MODULE_RETURN_CLAUSE = (
        "RETURN m.id AS module_id, "
        "       m.project_id AS project_id, "
        "       m.name AS name, "
        "       m.description AS description, "
        "       m.status AS status, "
        "       m.version AS version, "
        "       m.mod_code AS mod_code, "
        "       m.created_at AS module_created_at, "
        "       m.updated_at AS module_updated_at, "
        "       m.incremental_change_type AS incremental_change_type, "
        "       m.feedback_change_type AS feedback_change_type, "
        "       m.justification AS justification, "
        "       m.text_diffs AS text_diffs, "
        "       m.rfp_flagged_item AS rfp_flagged_item, "
        "       coalesce(m.is_jira_synced, false) AS is_jira_synced, "
        "       coalesce(m.is_tap_synced, false) AS is_tap_synced, "
        "       m.deleted_at AS module_deleted_at "
    )

    @staticmethod
    def _module_record_to_model(
        record: Any, fallback_project_id: str, features: list[FeatureModel]
    ) -> ModuleModel:
        return ModuleModel(
            id=str(record["module_id"]),
            name=str(record["name"]),
            description=record["description"],
            features=features,
            project_id=UUID(record["project_id"])
            if record.get("project_id")
            else UUID(fallback_project_id),
            version=int(record["version"])
            if record.get("version") is not None
            else INITIAL_ENTITY_VERSION,
            status=record["status"] or ModuleFeatureStatus.READY,
            mod_code=record["mod_code"],
            created_at=ModuleFeatureRepository._neo4j_dt_to_py(record.get("module_created_at")),
            updated_at=ModuleFeatureRepository._neo4j_dt_to_py(record.get("module_updated_at")),
            justification=record.get("justification"),
            incremental_change_type=record.get("incremental_change_type"),
            feedback_change_type=record.get("feedback_change_type"),
            text_diffs=ModuleFeatureRepository._parse_text_diffs(record.get("text_diffs")),
            rfp_flagged_item=ModuleFeatureRepository._parse_rfp_flagged_item(
                record.get("rfp_flagged_item")
            ),
            is_jira_synced=bool(record.get("is_jira_synced")),
            is_tap_synced=bool(record.get("is_tap_synced")),
            deleted_at=ModuleFeatureRepository._neo4j_dt_to_py(record.get("module_deleted_at")),
        )

    def _execute_get_module_v2(self, project_id: str, module_id: str) -> ModuleModel | None:
        cypher = (
            "MATCH (:Project {id: $project_id})-[:HAS_MODULE]->(m:Module {id: $module_id}) "
            "WHERE m.deleted_at IS NULL "
        ) + self._MODULE_RETURN_CLAUSE
        with self._driver.session() as session:
            record = session.execute_read(
                lambda tx: tx.run(
                    cypher,
                    project_id=project_id,
                    module_id=module_id,
                ).single()
            )

        if record is None:
            return None
        return self._module_record_to_model(
            record, project_id, self._execute_list_features_v2(project_id, module_id)
        )

    def _execute_get_module_by_mod_code(
        self, project_id: str, mod_code: str
    ) -> ModuleModel | None:
        cypher = (
            "MATCH (:Project {id: $project_id})-[:HAS_MODULE]->(m:Module {mod_code: $mod_code}) "
            "WHERE m.deleted_at IS NULL "
        ) + self._MODULE_RETURN_CLAUSE
        with self._driver.session() as session:
            record = session.execute_read(
                lambda tx: tx.run(
                    cypher,
                    project_id=project_id,
                    mod_code=mod_code,
                ).single()
            )

        if record is None:
            return None
        module_id = str(record["module_id"])
        return self._module_record_to_model(
            record, project_id, self._execute_list_features_v2(project_id, module_id)
        )

    def _execute_list_features_v2(self, project_id: str, module_id: str) -> list[FeatureModel]:
        cypher = (
            "MATCH (:Project {id: $project_id})-[:HAS_MODULE]->(:Module {id: $module_id})-[:HAS_FEATURE]->(f:Feature) "
            "WHERE f.deleted_at IS NULL "
            "RETURN f.id AS feature_id, "
            "       f.project_id AS project_id, "
            "       f.module_id AS module_id, "
            "       f.name AS name, "
            "       f.description AS description, "
            "       f.status AS status, "
            "       f.version AS version, "
            "       f.fea_code AS fea_code, "
            "       f.mfu_id AS mfu_id, "
            "       f.created_at AS created_at, "
            "       f.updated_at AS updated_at, "
            "       f.deleted_at AS deleted_at, "
            "       f.functions AS functions, "
            "       f.sources AS sources, "
            "       f.l2_sources AS l2_sources, "
            "       f.generation_metadata AS generation_metadata, "
            "       f.is_infrastructure AS is_infrastructure, "
            "       f.condensation_note AS condensation_note, "
            "       coalesce(f.is_jira_synced, false) AS is_jira_synced, "
            "       coalesce(f.is_tap_synced, false) AS is_tap_synced, "
            "       f.incremental_change_type AS incremental_change_type, "
            "       f.feedback_change_type AS feedback_change_type, "
            "       f.justification AS justification, "
            "       f.text_diffs AS text_diffs, "
            "       f.rfp_flagged_item AS rfp_flagged_item "
            "ORDER BY f.name ASC"
        )
        with self._driver.session() as session:
            records = session.execute_read(
                lambda tx: list(
                    tx.run(
                        cypher,
                        project_id=project_id,
                        module_id=module_id,
                    )
                )
            )

        return [
            FeatureModel(
                id=str(record["feature_id"]),
                module_id=str(record["module_id"]),
                name=str(record["name"]),
                description=record["description"],
                project_id=UUID(record["project_id"])
                if record.get("project_id")
                else UUID(project_id),
                status=record["status"] or ModuleFeatureStatus.READY,
                version=int(record["version"])
                if record.get("version") is not None
                else INITIAL_ENTITY_VERSION,
                fea_code=record["fea_code"],
                mfu_id=record.get("mfu_id"),
                functions=ModuleFeatureRepository._parse_functions(record.get("functions")),
                sources=ModuleFeatureRepository._parse_sources(record.get("sources")),
                l2_sources=ModuleFeatureRepository._parse_l2_sources(record.get("l2_sources")),
                generation_metadata=(
                    _json.loads(record["generation_metadata"])
                    if isinstance(record.get("generation_metadata"), str)
                    else record.get("generation_metadata")
                ),
                is_infrastructure=record.get("is_infrastructure"),
                condensation_note=record.get("condensation_note"),
                is_jira_synced=bool(record.get("is_jira_synced")),
                is_tap_synced=bool(record.get("is_tap_synced")),
                created_at=ModuleFeatureRepository._neo4j_dt_to_py(record.get("created_at")),
                updated_at=ModuleFeatureRepository._neo4j_dt_to_py(record.get("updated_at")),
                deleted_at=ModuleFeatureRepository._neo4j_dt_to_py(record.get("deleted_at")),
                justification=record.get("justification"),
                incremental_change_type=record.get("incremental_change_type"),
                feedback_change_type=record.get("feedback_change_type"),
                text_diffs=ModuleFeatureRepository._parse_text_diffs(record.get("text_diffs")),
                rfp_flagged_item=ModuleFeatureRepository._parse_rfp_flagged_item(
                    record.get("rfp_flagged_item")
                ),
            )
            for record in records
        ]

    def _execute_list_modules_by_project(
        self, project_id: str, source_ingestion_id: str | None = None
    ) -> list[ModuleModel]:
        cypher = (
            "MATCH (:Project {id: $project_id})-[:HAS_MODULE]->(m:Module) "
            "WHERE m.deleted_at IS NULL "
            "  AND ($source_ingestion_id IS NULL OR m.source_ingestion_id = $source_ingestion_id) "
            "OPTIONAL MATCH (m)-[:HAS_FEATURE]->(f:Feature) "
            "OPTIONAL MATCH (f)-[:HAS_USER_STORY]->(r:UserStory) "
            "WITH m, f, count(CASE WHEN r.deleted_at IS NULL THEN r END) AS req_count "
            "ORDER BY toInteger(last(split(m.mod_code, '-'))) ASC, "
            "         toInteger(last(split(f.fea_code, '-'))) ASC "
            "WITH m, collect(CASE WHEN f IS NOT NULL AND f.deleted_at IS NULL THEN "
            "    {id: f.id, project_id: f.project_id, "
            "     module_id: f.module_id, name: f.name, description: f.description, "
            "     status: f.status, version: f.version, fea_code: f.fea_code, mfu_id: f.mfu_id, functions: f.functions, sources: f.sources, "
            "     l2_sources: f.l2_sources, "
            "     generation_metadata: f.generation_metadata, "
            "     created_at: f.created_at, updated_at: f.updated_at, deleted_at: f.deleted_at, req_count: req_count, "
            "     is_infrastructure: f.is_infrastructure, "
            "     condensation_note: f.condensation_note, "
            "     is_jira_synced: coalesce(f.is_jira_synced, false), "
            "     is_tap_synced: coalesce(f.is_tap_synced, false), "
            "     incremental_change_type: f.incremental_change_type, "
            "     feedback_change_type: f.feedback_change_type, "
            "     justification: f.justification, "
            "     rfp_flagged_item: f.rfp_flagged_item} "
            "END) AS all_features "
            "RETURN m.id AS module_id, "
            "       m.project_id AS project_id, "
            "       m.name AS name, "
            "       m.description AS description, "
            "       m.status AS module_status, "
            "       m.version AS module_version, "
            "       m.mod_code AS mod_code, "
            "       m.created_at AS module_created_at, "
            "       m.updated_at AS module_updated_at, "
            "       m.incremental_change_type AS incremental_change_type, "
            "       m.feedback_change_type AS feedback_change_type, "
            "       m.justification AS justification, "
            "       m.rfp_flagged_item AS module_rfp_flagged_item, "
            "       coalesce(m.is_jira_synced, false) AS is_jira_synced, "
            "       coalesce(m.is_tap_synced, false) AS is_tap_synced, "
            "       m.deleted_at AS module_deleted_at, "
            "       [x IN all_features WHERE x IS NOT NULL] AS features "
            "ORDER BY toInteger(last(split(m.mod_code, '-'))) ASC"
        )
        with self._driver.session() as session:
            records = session.execute_read(
                lambda tx: list(
                    tx.run(
                        cypher,
                        project_id=project_id,
                        source_ingestion_id=source_ingestion_id,
                    )
                )
            )

        modules = [
            ModuleModel(
                id=str(record["module_id"]),
                name=str(record["name"]),
                description=record["description"],
                features=sorted(
                    [
                        FeatureModel(
                            id=str(f["id"]),
                            project_id=UUID(f["project_id"]) if f.get("project_id") else None,
                            module_id=str(f["module_id"]) if f.get("module_id") else "",
                            name=str(f["name"]),
                            description=f.get("description"),
                            status=f.get("status") or ModuleFeatureStatus.READY,
                            version=int(f["version"])
                            if f.get("version") is not None
                            else INITIAL_ENTITY_VERSION,
                            fea_code=f.get("fea_code"),
                            mfu_id=f.get("mfu_id"),
                            functions=ModuleFeatureRepository._parse_functions(f.get("functions")),
                            sources=ModuleFeatureRepository._parse_sources(f.get("sources")),
                            l2_sources=ModuleFeatureRepository._parse_l2_sources(
                                f.get("l2_sources")
                            ),
                            generation_metadata=(
                                _json.loads(f["generation_metadata"])
                                if isinstance(f.get("generation_metadata"), str)
                                else f.get("generation_metadata")
                            ),
                            created_at=ModuleFeatureRepository._neo4j_dt_to_py(f.get("created_at")),
                            updated_at=ModuleFeatureRepository._neo4j_dt_to_py(f.get("updated_at")),
                            deleted_at=ModuleFeatureRepository._neo4j_dt_to_py(f.get("deleted_at")),
                            total_user_stories=int(f.get("req_count") or 0),
                            is_infrastructure=f.get("is_infrastructure"),
                            condensation_note=f.get("condensation_note"),
                            is_jira_synced=bool(f.get("is_jira_synced")),
                            is_tap_synced=bool(f.get("is_tap_synced")),
                            justification=f.get("justification"),
                            incremental_change_type=f.get("incremental_change_type"),
                            feedback_change_type=f.get("feedback_change_type"),
                            rfp_flagged_item=ModuleFeatureRepository._parse_rfp_flagged_item(
                                f.get("rfp_flagged_item")
                            ),
                        )
                        for f in (record["features"] or [])
                    ],
                    key=lambda feat: ModuleFeatureRepository._numeric_code_key(feat.fea_code),
                ),
                project_id=UUID(record["project_id"]) if record.get("project_id") else None,
                status=record["module_status"] or ModuleFeatureStatus.READY,
                version=int(record["module_version"])
                if record.get("module_version") is not None
                else INITIAL_ENTITY_VERSION,
                mod_code=record["mod_code"],
                created_at=ModuleFeatureRepository._neo4j_dt_to_py(record.get("module_created_at")),
                updated_at=ModuleFeatureRepository._neo4j_dt_to_py(record.get("module_updated_at")),
                deleted_at=ModuleFeatureRepository._neo4j_dt_to_py(record.get("module_deleted_at")),
                justification=record.get("justification"),
                incremental_change_type=record.get("incremental_change_type"),
                feedback_change_type=record.get("feedback_change_type"),
                rfp_flagged_item=ModuleFeatureRepository._parse_rfp_flagged_item(
                    record.get("module_rfp_flagged_item")
                ),
                is_jira_synced=bool(record.get("is_jira_synced")),
                is_tap_synced=bool(record.get("is_tap_synced")),
            )
            for record in records
        ]
        modules.sort(key=lambda mod: ModuleFeatureRepository._numeric_code_key(mod.mod_code))
        return modules

    @staticmethod
    def _numeric_code_key(code: str | None) -> tuple[int, str]:
        """Return a sort key that orders codes numerically by their trailing integer."""
        if not code:
            return (2**31, "")
        nums = re.findall(r"\d+", code)
        return (int(nums[-1]), code) if nums else (2**31, code)

    def _execute_change_module_feature_status_for_project(
        self,
        project_id: str,
        new_status: str,
    ) -> ModuleModel | None:
        """Update module and all its child features status in Neo4j."""
        cypher = (
            "MATCH (p:Project {id: $project_id})-[:HAS_MODULE]->(m:Module) "
            "SET m.status = $new_status, m.created_at = coalesce(m.created_at, datetime($now)), m.updated_at = datetime($now) "
            "WITH m "
            "OPTIONAL MATCH (m)-[:HAS_FEATURE]->(f:Feature) "
            "SET f.status = $new_status, f.created_at = coalesce(f.created_at, datetime($now)), f.updated_at = datetime($now) "
            "WITH m, "
            "     m.id AS module_id, "
            "     m.project_id AS project_id, "
            "     m.name AS name, "
            "     m.description AS description, "
            "     m.status AS module_status, "
            "     m.mod_code AS mod_code, "
            "     [x IN [(m)-[:HAS_FEATURE]->(f) | f] | "
            "         {id: x.id, project_id: x.project_id, module_id: x.module_id, name: x.name, description: x.description, status: x.status, fea_code: x.fea_code, functions: x.functions, sources: x.sources, l2_sources: x.l2_sources}"
            "     ] AS features "
            "RETURN module_id, project_id, name, description, module_status, mod_code, features "
            "ORDER BY name ASC "
            "LIMIT 1"
        )
        now = datetime.now(UTC).isoformat()
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher, project_id=project_id, new_status=new_status, now=now
                ).single()
            )

        if record is None:
            return None

        return ModuleModel(
            id=str(record["module_id"]),
            project_id=UUID(record["project_id"]) if record.get("project_id") else None,
            name=str(record["name"]),
            description=record["description"],
            status=record["module_status"] or ModuleFeatureStatus.READY,
            mod_code=record["mod_code"],
            features=[
                FeatureModel(
                    id=str(f["id"]),
                    project_id=UUID(f["project_id"]) if f.get("project_id") else None,
                    module_id=str(f["module_id"]) if f.get("module_id") else "",
                    name=str(f["name"]),
                    description=f.get("description"),
                    status=f.get("status") or ModuleFeatureStatus.READY,
                    fea_code=f.get("fea_code"),
                    functions=ModuleFeatureRepository._parse_functions(f.get("functions")),
                    sources=ModuleFeatureRepository._parse_sources(f.get("sources")),
                    l2_sources=ModuleFeatureRepository._parse_l2_sources(f.get("l2_sources")),
                )
                for f in (record["features"] or [])
            ],
        )

    # ── Incremental update helpers ─────────────────────────────────────────

    async def update_module(
        self,
        project_id: UUID,
        module_id: str,
        mod_code: str,
        name: str,
        description: str | None,
        incremental_change_type: str | None = None,
        feedback_change_type: str | None = None,
        justification: str | None = None,
        text_diffs_json: str | None = None,
        status: str = ModuleFeatureStatus.READY.value,
        rfp_flagged_item_json: str | None = None,
        source_ingestion_id: str | None = None,
    ) -> bool:
        """Update an existing Module node's mutable properties.

        Targets the node by ``(project_id, module_id)`` — the stable UUID pair.
        ``status``/``rfp_flagged_item_json`` are hard-set (no coalesce) — default
        to READY/None for callers outside the incremental-update quality-gate
        flow. ``source_ingestion_id``, when provided, records which
        SourceIngestion triggered this update; omitted (None), the node's
        existing value is preserved. Returns True when the node was found and
        updated, False otherwise.
        """
        return await asyncio.to_thread(
            self._execute_update_module,
            str(project_id),
            module_id,
            mod_code,
            name,
            description,
            incremental_change_type,
            feedback_change_type,
            justification,
            text_diffs_json,
            status,
            rfp_flagged_item_json,
            source_ingestion_id,
        )

    async def snapshot_module_version(
        self,
        project_id: UUID,
        module_id: str,
    ) -> bool:
        """Copy the current Module node's properties into a new ModuleVersion node.

        No-ops (returns False) when the module does not exist. Call this
        before applying an update so the pre-update state is preserved.
        """
        return await asyncio.to_thread(
            self._execute_snapshot_module_version,
            str(project_id),
            module_id,
        )

    async def accept_module(self, project_id: UUID, module_id: str) -> bool:
        """Approve a Module: set status to APPROVED and clear the incremental flags.

        Returns True when the node was found and updated, False otherwise.
        """
        return await asyncio.to_thread(
            self._execute_accept_module,
            str(project_id),
            module_id,
        )

    async def accept_module_feedback_change(self, project_id: UUID, module_id: str) -> bool:
        """Approve a Module's feedback-driven regeneration change.

        Sets status to APPROVED and clears ``feedback_change_type`` only —
        ``incremental_change_type`` (a separate, independent flag) is left
        untouched. Returns True when the node was found and updated.
        """
        return await asyncio.to_thread(
            self._execute_accept_module_feedback_change,
            str(project_id),
            module_id,
        )

    async def get_latest_module_version(
        self,
        project_id: UUID,
        module_id: str,
    ) -> ModuleVersionModel | None:
        """Return the most recent HAS_VERSION snapshot for a module, or None if none exists."""
        return await asyncio.to_thread(
            self._execute_get_latest_module_version,
            str(project_id),
            module_id,
        )

    async def restore_module_from_latest_version(self, project_id: UUID, module_id: str) -> bool:
        """Roll a Module back to its most recent ModuleVersion snapshot.

        Restores mod_code/name/description/status/justification/version from
        the latest ``(m)-[:HAS_VERSION]->(v:ModuleVersion)`` node (ordered by
        ``snapshotted_at`` descending), then deletes that snapshot node since
        it has now been consumed by the rollback. Always clears
        ``text_diffs`` and ``incremental_change_type`` regardless of whether
        a snapshot exists. Returns True when the Module node was found,
        False otherwise.
        """
        return await asyncio.to_thread(
            self._execute_restore_module_from_latest_version,
            str(project_id),
            module_id,
        )

    async def restore_module_from_latest_feedback_version(
        self, project_id: UUID, module_id: str
    ) -> bool:
        """Roll a Module back to its most recent ModuleVersion snapshot, rejecting a
        feedback-driven regeneration change.

        Same content rollback as ``restore_module_from_latest_version``, but
        clears ``feedback_change_type`` only — ``incremental_change_type`` is
        left untouched, since the two review flows are independent.
        """
        return await asyncio.to_thread(
            self._execute_restore_module_from_latest_feedback_version,
            str(project_id),
            module_id,
        )

    async def get_max_mod_code(self, project_id: UUID) -> int:
        """Return the highest numeric mod_code currently stored for the project.

        Extracts the integer value from each ``mod_code`` (e.g. ``"3"`` → 3,
        ``"MOD-3"`` → 3) and returns the maximum.  Returns 0 when the project
        has no modules yet.
        """
        return await asyncio.to_thread(self._execute_get_max_mod_code, str(project_id))

    def _execute_get_max_mod_code(self, project_id: str) -> int:
        cypher = (
            "MATCH (:Project {id: $project_id})-[:HAS_MODULE]->(m:Module) "
            "WHERE m.mod_code IS NOT NULL "
            "WITH m.mod_code AS code "
            "WITH [x IN split(code, '-') | toInteger(x)] AS parts "
            "WITH [x IN parts WHERE x IS NOT NULL] AS nums "
            "WHERE size(nums) > 0 "
            "RETURN max(nums[-1]) AS max_code"
        )
        with self._driver.session() as session:
            record = session.execute_read(lambda tx: tx.run(cypher, project_id=project_id).single())
        if record is None or record["max_code"] is None:
            return 0
        return int(record["max_code"])

    async def get_max_fea_code(self, project_id: UUID, module_id: str) -> tuple[str, int]:
        """Return the module's mod_code and the highest numeric fea_code suffix.

        Parses the trailing integer from each ``fea_code`` stored under the
        given module (e.g. ``"1.3"`` → suffix 3, ``"2.10"`` → suffix 10) and
        returns the maximum.  Returns ``(mod_code, 0)`` when the module has no
        features yet so that the first fea_code becomes ``"{mod_code}.1"``.
        """
        return await asyncio.to_thread(self._execute_get_max_fea_code, str(project_id), module_id)

    def _execute_get_max_fea_code(self, project_id: str, module_id: str) -> tuple[str, int]:
        cypher = (
            "MATCH (:Project {id: $project_id})-[:HAS_MODULE]->(m:Module {id: $module_id}) "
            "OPTIONAL MATCH (m)-[:HAS_FEATURE]->(f:Feature) "
            "WITH m.mod_code AS mod_code, f.fea_code AS fea_code "
            "RETURN mod_code, "
            "       max(toInteger(split(coalesce(fea_code, '0.0'), '.')[-1])) AS max_suffix"
        )
        with self._driver.session() as session:
            record = session.execute_read(
                lambda tx: tx.run(cypher, project_id=project_id, module_id=module_id).single()
            )
        if record is None:
            return ("", 0)
        return (record["mod_code"] or "", int(record["max_suffix"] or 0))

    async def create_module(
        self,
        project_id: UUID,
        module: ModuleModel,
    ) -> ModuleModel:
        """Create (or idempotently upsert) a Module node under a project.

        Uses MERGE on ``(project_id, mod_code)`` so re-running is safe.
        """
        return await asyncio.to_thread(
            self._execute_create_module,
            str(project_id),
            module,
        )

    async def update_feature(
        self,
        project_id: UUID,
        feature_id: str,
        fea_code: str,
        name: str,
        description: str | None,
        functions_json: str,
        sources_json: str,
        incremental_change_type: str | None = None,
        feedback_change_type: str | None = None,
        justification: str | None = None,
        text_diffs_json: str | None = None,
        l2_sources_json: str | None = None,
        status: str = ModuleFeatureStatus.READY.value,
        rfp_flagged_item_json: str | None = None,
        source_ingestion_id: str | None = None,
    ) -> bool:
        """Update an existing Feature node's mutable properties.

        Targets the node by ``(project_id, feature_id)`` — the stable UUID pair.
        ``l2_sources_json`` (granular source-code pipeline references, distinct
        from ``sources_json``) leaves the stored value unchanged when omitted.
        ``status``/``rfp_flagged_item_json`` are hard-set (no coalesce) — default
        to READY/None for callers outside the incremental-update quality-gate
        flow. ``source_ingestion_id``, when provided, records which
        SourceIngestion triggered this update; omitted (None), the node's
        existing value is preserved. Returns True when the node was found and
        updated, False otherwise.
        """
        return await asyncio.to_thread(
            self._execute_update_feature,
            str(project_id),
            feature_id,
            fea_code,
            name,
            description,
            functions_json,
            sources_json,
            incremental_change_type,
            feedback_change_type,
            justification,
            text_diffs_json,
            l2_sources_json,
            status,
            rfp_flagged_item_json,
            source_ingestion_id,
        )

    async def create_feature(
        self,
        project_id: UUID,
        module_id: str,
        feature: FeatureModel,
    ) -> FeatureModel:
        """Create (or idempotently upsert) a Feature node under an existing Module.

        Uses MERGE on ``(project_id, fea_code)`` so re-running is safe.
        """
        return await asyncio.to_thread(
            self._execute_create_feature,
            str(project_id),
            module_id,
            feature,
        )

    async def snapshot_feature_version(
        self,
        project_id: UUID,
        feature_id: str,
    ) -> bool:
        """Copy the current Feature node's properties into a new FeatureVersion node.

        No-ops (returns False) when the feature does not exist. Call this
        before applying an update so the pre-update state is preserved.
        """
        return await asyncio.to_thread(
            self._execute_snapshot_feature_version,
            str(project_id),
            feature_id,
        )

    async def accept_feature(self, project_id: UUID, feature_id: str) -> bool:
        """Approve a Feature: set status to APPROVED and clear the incremental flags.

        Returns True when the node was found and updated, False otherwise.
        """
        return await asyncio.to_thread(
            self._execute_accept_feature,
            str(project_id),
            feature_id,
        )

    async def accept_feature_feedback_change(self, project_id: UUID, feature_id: str) -> bool:
        """Approve a Feature's feedback-driven regeneration change.

        Sets status to APPROVED and clears ``feedback_change_type`` only —
        ``incremental_change_type`` (a separate, independent flag) is left
        untouched. Returns True when the node was found and updated.
        """
        return await asyncio.to_thread(
            self._execute_accept_feature_feedback_change,
            str(project_id),
            feature_id,
        )

    async def get_feature_pending_change_flags(
        self, project_id: UUID, feature_id: str
    ) -> tuple[str | None, str | None] | None:
        """Return a Feature's ``(incremental_change_type, feedback_change_type)`` flags.

        Returns None if the feature does not exist. Used by callers that need
        to know which of the two independent review flows has a pending
        change on the node, without fetching the full ``FeatureModel``.
        """
        return await asyncio.to_thread(
            self._execute_get_feature_pending_change_flags,
            str(project_id),
            feature_id,
        )

    async def get_latest_feature_version(
        self,
        project_id: UUID,
        feature_id: str,
    ) -> FeatureVersionModel | None:
        """Return the most recent HAS_VERSION snapshot for a feature, or None if none exists."""
        return await asyncio.to_thread(
            self._execute_get_latest_feature_version,
            str(project_id),
            feature_id,
        )

    async def restore_feature_from_latest_version(self, project_id: UUID, feature_id: str) -> bool:
        """Roll a Feature back to its most recent FeatureVersion snapshot.

        Restores fea_code/name/description/functions/sources/status/
        justification/version from the latest
        ``(f)-[:HAS_VERSION]->(v:FeatureVersion)`` node (ordered by
        ``snapshotted_at`` descending), then deletes that snapshot node since
        it has now been consumed by the rollback. Always clears
        ``text_diffs`` and ``incremental_change_type`` regardless of whether
        a snapshot exists. Returns True when the Feature node was found,
        False otherwise.
        """
        return await asyncio.to_thread(
            self._execute_restore_feature_from_latest_version,
            str(project_id),
            feature_id,
        )

    async def restore_feature_from_latest_feedback_version(
        self, project_id: UUID, feature_id: str
    ) -> bool:
        """Roll a Feature back to its most recent FeatureVersion snapshot, rejecting a
        feedback-driven regeneration change.

        Same content rollback as ``restore_feature_from_latest_version``, but
        clears ``feedback_change_type`` only — ``incremental_change_type`` is
        left untouched, since the two review flows are independent.
        """
        return await asyncio.to_thread(
            self._execute_restore_feature_from_latest_feedback_version,
            str(project_id),
            feature_id,
        )

    async def update_module_sync_flags(
        self,
        project_id: UUID,
        module_id: str,
        *,
        is_jira_synced: bool | None = None,
        is_tap_synced: bool | None = None,
    ) -> ModuleModel | None:
        """Partially update the is_jira_synced/is_tap_synced flags of a Module node.

        A ``None`` argument leaves that flag unchanged. Returns the updated
        ModuleModel (with its features), or None if the module was not found.
        """
        return await asyncio.to_thread(
            self._execute_update_module_sync_flags,
            str(project_id),
            module_id,
            is_jira_synced,
            is_tap_synced,
        )

    async def update_feature_sync_flags(
        self,
        project_id: UUID,
        module_id: str,
        feature_id: str,
        *,
        is_jira_synced: bool | None = None,
        is_tap_synced: bool | None = None,
    ) -> FeatureModel | None:
        """Partially update the is_jira_synced/is_tap_synced flags of a Feature node.

        A ``None`` argument leaves that flag unchanged. Returns the updated
        FeatureModel, or None if the feature was not found.
        """
        return await asyncio.to_thread(
            self._execute_update_feature_sync_flags,
            str(project_id),
            module_id,
            feature_id,
            is_jira_synced,
            is_tap_synced,
        )

    async def mark_module_delete_suggested(
        self,
        project_id: UUID,
        module_id: str,
        justification: str | None = None,
        source_ingestion_id: str | None = None,
    ) -> bool:
        """Flag a Module node as DELETE_SUGGESTED and bump its ``version`` counter.

        Targets the node by ``(project_id, module_id)``. Call
        ``snapshot_module_version`` first (see ``IncrementalUpdateProcessorService
        .handle_deletes``) so the pre-suggestion state can be restored via
        ``restore_module_from_latest_version`` if the suggestion is rejected.
        ``source_ingestion_id``, when provided, records which SourceIngestion
        (e.g. the incremental run suggesting this delete) is now responsible
        for the node; omitted (None), the node's existing value is preserved.
        Returns True when the node was found and updated, False otherwise.
        """
        return await asyncio.to_thread(
            self._execute_mark_module_delete_suggested,
            str(project_id),
            module_id,
            justification,
            source_ingestion_id,
        )

    async def mark_feature_delete_suggested(
        self,
        project_id: UUID,
        feature_id: str,
        justification: str | None = None,
        source_ingestion_id: str | None = None,
    ) -> bool:
        """Flag a Feature node as DELETE_SUGGESTED and bump its ``version`` counter.

        See ``mark_module_delete_suggested`` — same rationale. Targets the
        node by ``(project_id, feature_id)``.
        """
        return await asyncio.to_thread(
            self._execute_mark_feature_delete_suggested,
            str(project_id),
            feature_id,
            justification,
            source_ingestion_id,
        )

    # ── Private execute helpers ────────────────────────────────────────────

    def _execute_update_module(
        self,
        project_id: str,
        module_id: str,
        mod_code: str,
        name: str,
        description: str | None,
        incremental_change_type: str | None = None,
        feedback_change_type: str | None = None,
        justification: str | None = None,
        text_diffs_json: str | None = None,
        status: str = ModuleFeatureStatus.READY.value,
        rfp_flagged_item_json: str | None = None,
        source_ingestion_id: str | None = None,
    ) -> bool:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (m:Module {project_id: $project_id, id: $module_id}) "
            "SET m.mod_code                = $mod_code, "
            "    m.name                    = $name, "
            "    m.description             = $description, "
            "    m.status                  = $status, "
            "    m.rfp_flagged_item        = $rfp_flagged_item, "
            "    m.justification           = $justification, "
            "    m.incremental_change_type = $incremental_change_type, "
            "    m.feedback_change_type    = $feedback_change_type, "
            "    m.text_diffs              = $text_diffs, "
            "    m.source_ingestion_id     = coalesce($source_ingestion_id, m.source_ingestion_id), "
            "    m.version                 = coalesce(m.version, $version) + 1, "
            "    m.updated_at              = datetime($now) "
            "RETURN count(m) AS updated_count"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    project_id=project_id,
                    module_id=module_id,
                    mod_code=mod_code,
                    name=name,
                    description=description,
                    status=status,
                    rfp_flagged_item=rfp_flagged_item_json,
                    justification=justification,
                    incremental_change_type=incremental_change_type,
                    feedback_change_type=feedback_change_type,
                    text_diffs=text_diffs_json,
                    source_ingestion_id=source_ingestion_id,
                    version=INITIAL_ENTITY_VERSION,
                    now=now,
                ).single()
            )
        return bool(record and int(record["updated_count"]) > 0)

    def _execute_snapshot_module_version(self, project_id: str, module_id: str) -> bool:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (m:Module {project_id: $project_id, id: $module_id}) "
            "CREATE (v:ModuleVersion { "
            "    id: randomUUID(), "
            "    module_id: m.id, "
            "    project_id: m.project_id, "
            "    mod_code: m.mod_code, "
            "    name: m.name, "
            "    description: m.description, "
            "    status: m.status, "
            "    version: m.version, "
            "    justification: m.justification, "
            "    incremental_change_type: m.incremental_change_type, "
            "    feedback_change_type: m.feedback_change_type, "
            "    is_jira_synced: coalesce(m.is_jira_synced, false), "
            "    is_tap_synced: coalesce(m.is_tap_synced, false), "
            "    source_ingestion_id: m.source_ingestion_id, "
            "    created_at: m.created_at, "
            "    updated_at: m.updated_at, "
            "    snapshotted_at: datetime($now) "
            "}) "
            "MERGE (m)-[:HAS_VERSION]->(v) "
            "RETURN count(v) AS created_count"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher, project_id=project_id, module_id=module_id, now=now
                ).single()
            )
        return bool(record and int(record["created_count"]) > 0)

    def _execute_get_latest_module_version(
        self, project_id: str, module_id: str
    ) -> ModuleVersionModel | None:
        cypher = (
            "MATCH (m:Module {project_id: $project_id, id: $module_id})"
            "-[:HAS_VERSION]->(v:ModuleVersion) "
            "WITH v ORDER BY v.snapshotted_at DESC "
            "RETURN v.id AS id, "
            "       v.module_id AS module_id, "
            "       v.project_id AS project_id, "
            "       v.mod_code AS mod_code, "
            "       v.name AS name, "
            "       v.description AS description, "
            "       v.status AS status, "
            "       v.version AS version, "
            "       v.justification AS justification, "
            "       v.incremental_change_type AS incremental_change_type, "
            "       v.feedback_change_type AS feedback_change_type, "
            "       coalesce(v.is_jira_synced, false) AS is_jira_synced, "
            "       coalesce(v.is_tap_synced, false) AS is_tap_synced, "
            "       v.created_at AS created_at, "
            "       v.updated_at AS updated_at, "
            "       v.snapshotted_at AS snapshotted_at "
            "LIMIT 1"
        )
        with self._driver.session() as session:
            record = session.execute_read(
                lambda tx: tx.run(cypher, project_id=project_id, module_id=module_id).single()
            )
        if record is None:
            return None
        return self._record_to_module_version_model(record, project_id)

    @staticmethod
    def _record_to_module_version_model(record: dict, project_id: str) -> ModuleVersionModel:
        project_id_raw = record.get("project_id")
        return ModuleVersionModel(
            id=str(record["id"]),
            module_id=str(record["module_id"]),
            project_id=UUID(str(project_id_raw)) if project_id_raw else UUID(project_id),
            mod_code=record.get("mod_code"),
            name=record.get("name") or "",
            description=record.get("description"),
            version=int(record["version"])
            if record.get("version") is not None
            else INITIAL_ENTITY_VERSION,
            status=record.get("status") or ModuleFeatureStatus.READY,
            justification=record.get("justification"),
            incremental_change_type=record.get("incremental_change_type"),
            feedback_change_type=record.get("feedback_change_type"),
            is_jira_synced=bool(record.get("is_jira_synced")),
            is_tap_synced=bool(record.get("is_tap_synced")),
            created_at=ModuleFeatureRepository._neo4j_dt_to_py(record.get("created_at")),
            updated_at=ModuleFeatureRepository._neo4j_dt_to_py(record.get("updated_at")),
            snapshotted_at=ModuleFeatureRepository._neo4j_dt_to_py(record.get("snapshotted_at")),
        )

    def _execute_accept_module(self, project_id: str, module_id: str) -> bool:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (m:Module {project_id: $project_id, id: $module_id}) "
            "SET m.status                  = $status, "
            "    m.text_diffs              = [], "
            "    m.incremental_change_type = null, "
            "    m.updated_at              = datetime($now) "
            "RETURN count(m) AS updated_count"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    project_id=project_id,
                    module_id=module_id,
                    status=ModuleFeatureStatus.APPROVED.value,
                    now=now,
                ).single()
            )
        return bool(record and int(record["updated_count"]) > 0)

    def _execute_accept_module_feedback_change(self, project_id: str, module_id: str) -> bool:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (m:Module {project_id: $project_id, id: $module_id}) "
            "SET m.status                = $status, "
            "    m.feedback_change_type = null, "
            "    m.updated_at           = datetime($now) "
            "RETURN count(m) AS updated_count"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    project_id=project_id,
                    module_id=module_id,
                    status=ModuleFeatureStatus.APPROVED.value,
                    now=now,
                ).single()
            )
        return bool(record and int(record["updated_count"]) > 0)

    def _execute_restore_module_from_latest_feedback_version(
        self, project_id: str, module_id: str
    ) -> bool:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (m:Module {project_id: $project_id, id: $module_id}) "
            "OPTIONAL MATCH (m)-[:HAS_VERSION]->(v:ModuleVersion) "
            "WITH m, v ORDER BY v.snapshotted_at DESC "
            "WITH m, collect(v) AS versions "
            "WITH m, CASE WHEN size(versions) > 0 THEN versions[0] ELSE null END AS latest "
            "WITH m, latest, latest IS NOT NULL AS restored "
            "SET m.feedback_change_type = null, "
            "    m.updated_at           = datetime($now) "
            "FOREACH (_ IN CASE WHEN latest IS NOT NULL THEN [1] ELSE [] END | "
            "    SET m.mod_code      = latest.mod_code, "
            "        m.name          = latest.name, "
            "        m.description   = latest.description, "
            "        m.status        = latest.status, "
            "        m.justification = latest.justification, "
            "        m.version       = latest.version "
            ") "
            "FOREACH (_ IN CASE WHEN latest IS NOT NULL THEN [1] ELSE [] END | "
            "    DETACH DELETE latest "
            ") "
            "RETURN count(m) AS updated_count, restored"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher, project_id=project_id, module_id=module_id, now=now
                ).single()
            )
        if record is None or int(record["updated_count"]) == 0:
            return False
        if not record["restored"]:
            logger.warning(
                "[FEEDBACK_UPDATES] restore_module_from_latest_feedback_version: no "
                "ModuleVersion snapshot found — cleared flag only, no content rollback. "
                "module_id=%s project=%s",
                module_id,
                project_id,
            )
        return True

    def _execute_restore_module_from_latest_version(self, project_id: str, module_id: str) -> bool:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (m:Module {project_id: $project_id, id: $module_id}) "
            "OPTIONAL MATCH (m)-[:HAS_VERSION]->(v:ModuleVersion) "
            "WITH m, v ORDER BY v.snapshotted_at DESC "
            "WITH m, collect(v) AS versions "
            "WITH m, CASE WHEN size(versions) > 0 THEN versions[0] ELSE null END AS latest "
            "WITH m, latest, latest IS NOT NULL AS restored "
            "SET m.text_diffs              = [], "
            "    m.incremental_change_type = null, "
            "    m.updated_at              = datetime($now) "
            "FOREACH (_ IN CASE WHEN latest IS NOT NULL THEN [1] ELSE [] END | "
            "    SET m.mod_code      = latest.mod_code, "
            "        m.name          = latest.name, "
            "        m.description   = latest.description, "
            "        m.status        = latest.status, "
            "        m.justification = latest.justification, "
            "        m.version       = latest.version "
            ") "
            "FOREACH (_ IN CASE WHEN latest IS NOT NULL THEN [1] ELSE [] END | "
            "    DETACH DELETE latest "
            ") "
            "RETURN count(m) AS updated_count, restored"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher, project_id=project_id, module_id=module_id, now=now
                ).single()
            )
        if record is None or int(record["updated_count"]) == 0:
            return False
        if not record["restored"]:
            logger.warning(
                "[UPDATES] restore_module_from_latest_version: no ModuleVersion snapshot "
                "found — cleared flags only, no content rollback. module_id=%s project=%s",
                module_id,
                project_id,
            )
        return True

    def _execute_create_module(self, project_id: str, module: ModuleModel) -> ModuleModel:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (p:Project {id: $project_id}) "
            "MERGE (m:Module {project_id: $project_id, mod_code: $mod_code}) "
            "SET m.id                      = coalesce(m.id, $module_id), "
            "    m.name                    = $name, "
            "    m.description             = $description, "
            "    m.status                  = coalesce(m.status, $status), "
            "    m.rfp_flagged_item        = coalesce(m.rfp_flagged_item, $rfp_flagged_item), "
            "    m.version                 = coalesce(m.version, $version), "
            "    m.justification           = $justification, "
            "    m.incremental_change_type = $incremental_change_type, "
            "    m.feedback_change_type    = $feedback_change_type, "
            "    m.is_jira_synced          = coalesce(m.is_jira_synced, false), "
            "    m.is_tap_synced           = coalesce(m.is_tap_synced, false), "
            "    m.source_ingestion_id     = coalesce(m.source_ingestion_id, $source_ingestion_id), "
            "    m.created_at              = coalesce(m.created_at, datetime($now)), "
            "    m.updated_at              = datetime($now) "
            "MERGE (p)-[:HAS_MODULE]->(m) "
            "RETURN m.id AS module_id"
        )
        with self._driver.session() as session:
            result = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    project_id=project_id,
                    module_id=module.id,
                    mod_code=module.mod_code,
                    name=module.name,
                    description=module.description,
                    status=module.status.value
                    if isinstance(module.status, ModuleFeatureStatus)
                    else module.status,
                    rfp_flagged_item=json.dumps(module.rfp_flagged_item)
                    if module.rfp_flagged_item
                    else None,
                    version=INITIAL_ENTITY_VERSION,
                    justification=module.justification,
                    incremental_change_type=module.incremental_change_type,
                    feedback_change_type=module.feedback_change_type,
                    source_ingestion_id=module.source_ingestion_id,
                    now=now,
                ).single()
            )
        if result is None:
            raise NotFoundError(
                f"Project {project_id} not found in Neo4j; "
                f"module {module.mod_code} could not be created."
            )
        module.id = result["module_id"]
        return module

    def _execute_update_feature(
        self,
        project_id: str,
        feature_id: str,
        fea_code: str,
        name: str,
        description: str | None,
        functions_json: str,
        sources_json: str,
        incremental_change_type: str | None = None,
        feedback_change_type: str | None = None,
        justification: str | None = None,
        text_diffs_json: str | None = None,
        l2_sources_json: str | None = None,
        status: str = ModuleFeatureStatus.READY.value,
        rfp_flagged_item_json: str | None = None,
        source_ingestion_id: str | None = None,
    ) -> bool:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (f:Feature {project_id: $project_id, id: $feature_id}) "
            "SET f.fea_code                = $fea_code, "
            "    f.name                    = $name, "
            "    f.description             = $description, "
            "    f.functions               = $functions, "
            "    f.sources                 = $sources, "
            "    f.l2_sources              = coalesce($l2_sources, f.l2_sources), "
            "    f.status                  = $status, "
            "    f.rfp_flagged_item        = $rfp_flagged_item, "
            "    f.justification           = $justification, "
            "    f.incremental_change_type = $incremental_change_type, "
            "    f.feedback_change_type    = $feedback_change_type, "
            "    f.text_diffs              = $text_diffs, "
            "    f.source_ingestion_id     = coalesce($source_ingestion_id, f.source_ingestion_id), "
            "    f.version                 = coalesce(f.version, $version) + 1, "
            "    f.updated_at              = datetime($now) "
            "RETURN count(f) AS updated_count"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    project_id=project_id,
                    feature_id=feature_id,
                    fea_code=fea_code,
                    name=name,
                    description=description,
                    functions=functions_json,
                    sources=sources_json,
                    l2_sources=l2_sources_json,
                    status=status,
                    rfp_flagged_item=rfp_flagged_item_json,
                    justification=justification,
                    incremental_change_type=incremental_change_type,
                    feedback_change_type=feedback_change_type,
                    text_diffs=text_diffs_json,
                    source_ingestion_id=source_ingestion_id,
                    version=INITIAL_ENTITY_VERSION,
                    now=now,
                ).single()
            )
        return bool(record and int(record["updated_count"]) > 0)

    def _execute_snapshot_feature_version(self, project_id: str, feature_id: str) -> bool:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (f:Feature {project_id: $project_id, id: $feature_id}) "
            "CREATE (v:FeatureVersion { "
            "    id: randomUUID(), "
            "    feature_id: f.id, "
            "    module_id: f.module_id, "
            "    project_id: f.project_id, "
            "    fea_code: f.fea_code, "
            "    mfu_id: f.mfu_id, "
            "    name: f.name, "
            "    description: f.description, "
            "    status: f.status, "
            "    version: f.version, "
            "    functions: f.functions, "
            "    sources: f.sources, "
            "    l2_sources: f.l2_sources, "
            "    is_infrastructure: f.is_infrastructure, "
            "    condensation_note: f.condensation_note, "
            "    justification: f.justification, "
            "    incremental_change_type: f.incremental_change_type, "
            "    feedback_change_type: f.feedback_change_type, "
            "    is_jira_synced: coalesce(f.is_jira_synced, false), "
            "    is_tap_synced: coalesce(f.is_tap_synced, false), "
            "    source_ingestion_id: f.source_ingestion_id, "
            "    created_at: f.created_at, "
            "    updated_at: f.updated_at, "
            "    snapshotted_at: datetime($now) "
            "}) "
            "MERGE (f)-[:HAS_VERSION]->(v) "
            "RETURN count(v) AS created_count"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher, project_id=project_id, feature_id=feature_id, now=now
                ).single()
            )
        return bool(record and int(record["created_count"]) > 0)

    def _execute_get_latest_feature_version(
        self, project_id: str, feature_id: str
    ) -> FeatureVersionModel | None:
        cypher = (
            "MATCH (f:Feature {project_id: $project_id, id: $feature_id})"
            "-[:HAS_VERSION]->(v:FeatureVersion) "
            "WITH v ORDER BY v.snapshotted_at DESC "
            "RETURN v.id AS id, "
            "       v.feature_id AS feature_id, "
            "       v.module_id AS module_id, "
            "       v.project_id AS project_id, "
            "       v.fea_code AS fea_code, "
            "       v.mfu_id AS mfu_id, "
            "       v.name AS name, "
            "       v.description AS description, "
            "       v.status AS status, "
            "       v.version AS version, "
            "       v.functions AS functions, "
            "       v.sources AS sources, "
            "       v.l2_sources AS l2_sources, "
            "       v.is_infrastructure AS is_infrastructure, "
            "       v.condensation_note AS condensation_note, "
            "       v.justification AS justification, "
            "       v.incremental_change_type AS incremental_change_type, "
            "       v.feedback_change_type AS feedback_change_type, "
            "       coalesce(v.is_jira_synced, false) AS is_jira_synced, "
            "       coalesce(v.is_tap_synced, false) AS is_tap_synced, "
            "       v.created_at AS created_at, "
            "       v.updated_at AS updated_at, "
            "       v.snapshotted_at AS snapshotted_at "
            "LIMIT 1"
        )
        with self._driver.session() as session:
            record = session.execute_read(
                lambda tx: tx.run(cypher, project_id=project_id, feature_id=feature_id).single()
            )
        if record is None:
            return None
        return self._record_to_feature_version_model(record, project_id)

    @staticmethod
    def _record_to_feature_version_model(record: dict, project_id: str) -> FeatureVersionModel:
        project_id_raw = record.get("project_id")
        return FeatureVersionModel(
            id=str(record["id"]),
            feature_id=str(record["feature_id"]),
            module_id=record.get("module_id"),
            project_id=UUID(str(project_id_raw)) if project_id_raw else UUID(project_id),
            fea_code=record.get("fea_code"),
            mfu_id=record.get("mfu_id"),
            name=record.get("name") or "",
            description=record.get("description"),
            version=int(record["version"])
            if record.get("version") is not None
            else INITIAL_ENTITY_VERSION,
            status=record.get("status") or ModuleFeatureStatus.READY,
            functions=ModuleFeatureRepository._parse_functions(record.get("functions")),
            sources=ModuleFeatureRepository._parse_sources(record.get("sources")),
            l2_sources=ModuleFeatureRepository._parse_l2_sources(record.get("l2_sources")),
            justification=record.get("justification"),
            incremental_change_type=record.get("incremental_change_type"),
            feedback_change_type=record.get("feedback_change_type"),
            is_infrastructure=record.get("is_infrastructure"),
            condensation_note=record.get("condensation_note"),
            is_jira_synced=bool(record.get("is_jira_synced")),
            is_tap_synced=bool(record.get("is_tap_synced")),
            created_at=ModuleFeatureRepository._neo4j_dt_to_py(record.get("created_at")),
            updated_at=ModuleFeatureRepository._neo4j_dt_to_py(record.get("updated_at")),
            snapshotted_at=ModuleFeatureRepository._neo4j_dt_to_py(record.get("snapshotted_at")),
        )

    def _execute_accept_feature(self, project_id: str, feature_id: str) -> bool:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (f:Feature {project_id: $project_id, id: $feature_id}) "
            "SET f.status                  = $status, "
            "    f.text_diffs              = [], "
            "    f.incremental_change_type = null, "
            "    f.updated_at              = datetime($now) "
            "RETURN count(f) AS updated_count"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    project_id=project_id,
                    feature_id=feature_id,
                    status=ModuleFeatureStatus.APPROVED.value,
                    now=now,
                ).single()
            )
        return bool(record and int(record["updated_count"]) > 0)

    def _execute_accept_feature_feedback_change(self, project_id: str, feature_id: str) -> bool:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (f:Feature {project_id: $project_id, id: $feature_id}) "
            "SET f.status                = $status, "
            "    f.feedback_change_type = null, "
            "    f.updated_at           = datetime($now) "
            "RETURN count(f) AS updated_count"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    project_id=project_id,
                    feature_id=feature_id,
                    status=ModuleFeatureStatus.APPROVED.value,
                    now=now,
                ).single()
            )
        return bool(record and int(record["updated_count"]) > 0)

    def _execute_get_feature_pending_change_flags(
        self, project_id: str, feature_id: str
    ) -> tuple[str | None, str | None] | None:
        cypher = (
            "MATCH (f:Feature {project_id: $project_id, id: $feature_id}) "
            "RETURN f.incremental_change_type AS incremental_change_type, "
            "       f.feedback_change_type AS feedback_change_type"
        )
        with self._driver.session() as session:
            record = session.execute_read(
                lambda tx: tx.run(cypher, project_id=project_id, feature_id=feature_id).single()
            )
        if record is None:
            return None
        return record.get("incremental_change_type"), record.get("feedback_change_type")

    def _execute_restore_feature_from_latest_feedback_version(
        self, project_id: str, feature_id: str
    ) -> bool:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (f:Feature {project_id: $project_id, id: $feature_id}) "
            "OPTIONAL MATCH (f)-[:HAS_VERSION]->(v:FeatureVersion) "
            "WITH f, v ORDER BY v.snapshotted_at DESC "
            "WITH f, collect(v) AS versions "
            "WITH f, CASE WHEN size(versions) > 0 THEN versions[0] ELSE null END AS latest "
            "WITH f, latest, latest IS NOT NULL AS restored "
            "SET f.feedback_change_type = null, "
            "    f.updated_at           = datetime($now) "
            "FOREACH (_ IN CASE WHEN latest IS NOT NULL THEN [1] ELSE [] END | "
            "    SET f.fea_code      = latest.fea_code, "
            "        f.name          = latest.name, "
            "        f.description   = latest.description, "
            "        f.functions     = latest.functions, "
            "        f.sources       = latest.sources, "
            "        f.l2_sources    = latest.l2_sources, "
            "        f.status        = latest.status, "
            "        f.justification = latest.justification, "
            "        f.version       = latest.version "
            ") "
            "FOREACH (_ IN CASE WHEN latest IS NOT NULL THEN [1] ELSE [] END | "
            "    DETACH DELETE latest "
            ") "
            "RETURN count(f) AS updated_count, restored"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher, project_id=project_id, feature_id=feature_id, now=now
                ).single()
            )
        if record is None or int(record["updated_count"]) == 0:
            return False
        if not record["restored"]:
            logger.warning(
                "[FEEDBACK_UPDATES] restore_feature_from_latest_feedback_version: no "
                "FeatureVersion snapshot found — cleared flag only, no content rollback. "
                "feature_id=%s project=%s",
                feature_id,
                project_id,
            )
        return True

    def _execute_restore_feature_from_latest_version(
        self, project_id: str, feature_id: str
    ) -> bool:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (f:Feature {project_id: $project_id, id: $feature_id}) "
            "OPTIONAL MATCH (f)-[:HAS_VERSION]->(v:FeatureVersion) "
            "WITH f, v ORDER BY v.snapshotted_at DESC "
            "WITH f, collect(v) AS versions "
            "WITH f, CASE WHEN size(versions) > 0 THEN versions[0] ELSE null END AS latest "
            "WITH f, latest, latest IS NOT NULL AS restored "
            "SET f.text_diffs              = [], "
            "    f.incremental_change_type = null, "
            "    f.updated_at              = datetime($now) "
            "FOREACH (_ IN CASE WHEN latest IS NOT NULL THEN [1] ELSE [] END | "
            "    SET f.fea_code      = latest.fea_code, "
            "        f.name          = latest.name, "
            "        f.description   = latest.description, "
            "        f.functions     = latest.functions, "
            "        f.sources       = latest.sources, "
            "        f.l2_sources    = latest.l2_sources, "
            "        f.status        = latest.status, "
            "        f.justification = latest.justification, "
            "        f.version       = latest.version "
            ") "
            "FOREACH (_ IN CASE WHEN latest IS NOT NULL THEN [1] ELSE [] END | "
            "    DETACH DELETE latest "
            ") "
            "RETURN count(f) AS updated_count, restored"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher, project_id=project_id, feature_id=feature_id, now=now
                ).single()
            )
        if record is None or int(record["updated_count"]) == 0:
            return False
        if not record["restored"]:
            logger.warning(
                "[UPDATES] restore_feature_from_latest_version: no FeatureVersion snapshot "
                "found — cleared flags only, no content rollback. feature_id=%s project=%s",
                feature_id,
                project_id,
            )
        return True

    def _execute_create_feature(
        self,
        project_id: str,
        module_id: str,
        feature: FeatureModel,
    ) -> FeatureModel:
        now = datetime.now(UTC).isoformat()
        functions_json = json.dumps(
            [
                {
                    "fun_code": fn.fun_code,
                    "name": fn.name,
                    "description": fn.description,
                    "func_src_ref": fn.func_src_ref,
                }
                for fn in feature.functions
            ]
        )
        sources_json = json.dumps(feature.sources)
        l2_sources_json = json.dumps(feature.l2_sources)
        cypher = (
            "MATCH (p:Project {id: $project_id})-[:HAS_MODULE]->(m:Module {id: $module_id}) "
            "MERGE (f:Feature {project_id: $project_id, fea_code: $fea_code}) "
            "SET f.id                      = coalesce(f.id, $feature_id), "
            "    f.module_id               = $module_id, "
            "    f.name                    = $name, "
            "    f.description             = $description, "
            "    f.mfu_id                  = $mfu_id, "
            "    f.functions               = $functions, "
            "    f.sources                 = $sources, "
            "    f.l2_sources              = $l2_sources, "
            "    f.status                  = coalesce(f.status, $status), "
            "    f.rfp_flagged_item        = coalesce(f.rfp_flagged_item, $rfp_flagged_item), "
            "    f.version                 = coalesce(f.version, $version), "
            "    f.justification           = $justification, "
            "    f.incremental_change_type = $incremental_change_type, "
            "    f.feedback_change_type    = $feedback_change_type, "
            "    f.is_infrastructure       = $is_infrastructure, "
            "    f.condensation_note       = $condensation_note, "
            "    f.is_jira_synced          = coalesce(f.is_jira_synced, false), "
            "    f.is_tap_synced           = coalesce(f.is_tap_synced, false), "
            "    f.source_ingestion_id     = coalesce(f.source_ingestion_id, $source_ingestion_id), "
            "    f.created_at              = coalesce(f.created_at, datetime($now)), "
            "    f.updated_at              = datetime($now) "
            "MERGE (m)-[:HAS_FEATURE]->(f) "
            "RETURN f.id AS feature_id"
        )
        with self._driver.session() as session:
            result = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    project_id=project_id,
                    module_id=module_id,
                    feature_id=feature.id,
                    fea_code=feature.fea_code,
                    mfu_id=feature.mfu_id,
                    name=feature.name,
                    description=feature.description,
                    functions=functions_json,
                    sources=sources_json,
                    l2_sources=l2_sources_json,
                    status=feature.status.value
                    if isinstance(feature.status, ModuleFeatureStatus)
                    else feature.status,
                    rfp_flagged_item=json.dumps(feature.rfp_flagged_item)
                    if feature.rfp_flagged_item
                    else None,
                    version=INITIAL_ENTITY_VERSION,
                    justification=feature.justification,
                    incremental_change_type=feature.incremental_change_type,
                    feedback_change_type=feature.feedback_change_type,
                    is_infrastructure=feature.is_infrastructure,
                    condensation_note=feature.condensation_note,
                    source_ingestion_id=feature.source_ingestion_id,
                    now=now,
                ).single()
            )
        if result is None:
            raise NotFoundError(
                f"Module {module_id} not found in project {project_id}; "
                f"feature {feature.fea_code} could not be created."
            )
        feature.id = result["feature_id"]
        return feature

    def _execute_update_module_sync_flags(
        self,
        project_id: str,
        module_id: str,
        is_jira_synced: bool | None,
        is_tap_synced: bool | None,
    ) -> ModuleModel | None:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (:Project {id: $project_id})-[:HAS_MODULE]->(m:Module {id: $module_id}) "
            "SET m.is_jira_synced = coalesce($is_jira_synced, m.is_jira_synced, false), "
            "    m.is_tap_synced = coalesce($is_tap_synced, m.is_tap_synced, false), "
            "    m.updated_at = datetime($now) "
            "RETURN m.id AS module_id, "
            "       m.project_id AS project_id, "
            "       m.name AS name, "
            "       m.description AS description, "
            "       m.status AS status, "
            "       m.version AS version, "
            "       m.mod_code AS mod_code, "
            "       m.created_at AS module_created_at, "
            "       m.updated_at AS module_updated_at, "
            "       m.incremental_change_type AS incremental_change_type, "
            "       m.feedback_change_type AS feedback_change_type, "
            "       m.justification AS justification, "
            "       coalesce(m.is_jira_synced, false) AS is_jira_synced, "
            "       coalesce(m.is_tap_synced, false) AS is_tap_synced"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    project_id=project_id,
                    module_id=module_id,
                    is_jira_synced=is_jira_synced,
                    is_tap_synced=is_tap_synced,
                    now=now,
                ).single()
            )
        if record is None:
            return None
        return ModuleModel(
            id=str(record["module_id"]),
            name=str(record["name"]),
            description=record["description"],
            features=self._execute_list_features_v2(project_id, module_id),
            project_id=UUID(record["project_id"]) if record.get("project_id") else UUID(project_id),
            version=int(record["version"])
            if record.get("version") is not None
            else INITIAL_ENTITY_VERSION,
            status=record["status"] or ModuleFeatureStatus.READY,
            mod_code=record["mod_code"],
            created_at=ModuleFeatureRepository._neo4j_dt_to_py(record.get("module_created_at")),
            updated_at=ModuleFeatureRepository._neo4j_dt_to_py(record.get("module_updated_at")),
            justification=record.get("justification"),
            incremental_change_type=record.get("incremental_change_type"),
            feedback_change_type=record.get("feedback_change_type"),
            is_jira_synced=bool(record.get("is_jira_synced")),
            is_tap_synced=bool(record.get("is_tap_synced")),
        )

    def _execute_update_feature_sync_flags(
        self,
        project_id: str,
        module_id: str,
        feature_id: str,
        is_jira_synced: bool | None,
        is_tap_synced: bool | None,
    ) -> FeatureModel | None:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (:Project {id: $project_id})-[:HAS_MODULE]->(:Module {id: $module_id})"
            "-[:HAS_FEATURE]->(f:Feature {id: $feature_id}) "
            "SET f.is_jira_synced = coalesce($is_jira_synced, f.is_jira_synced, false), "
            "    f.is_tap_synced = coalesce($is_tap_synced, f.is_tap_synced, false), "
            "    f.updated_at = datetime($now) "
            "RETURN f.id AS feature_id, "
            "       f.module_id AS module_id, "
            "       f.name AS name, "
            "       f.description AS description, "
            "       f.project_id AS project_id, "
            "       f.fea_code AS fea_code, "
            "       f.mfu_id AS mfu_id, "
            "       f.version AS version, "
            "       f.status AS status, "
            "       f.justification AS justification, "
            "       f.incremental_change_type AS incremental_change_type, "
            "       f.feedback_change_type AS feedback_change_type, "
            "       f.generation_metadata AS generation_metadata, "
            "       f.is_infrastructure AS is_infrastructure, "
            "       f.condensation_note AS condensation_note, "
            "       coalesce(f.is_jira_synced, false) AS is_jira_synced, "
            "       coalesce(f.is_tap_synced, false) AS is_tap_synced, "
            "       f.functions AS functions, "
            "       f.sources AS sources, "
            "       f.l2_sources AS l2_sources, "
            "       f.created_at AS created_at, "
            "       f.updated_at AS updated_at"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    project_id=project_id,
                    module_id=module_id,
                    feature_id=feature_id,
                    is_jira_synced=is_jira_synced,
                    is_tap_synced=is_tap_synced,
                    now=now,
                ).single()
            )
        if record is None:
            return None
        return FeatureModel(
            id=str(record["feature_id"]),
            module_id=str(record["module_id"]),
            name=str(record["name"]),
            description=record["description"],
            project_id=UUID(record["project_id"]) if record.get("project_id") else UUID(project_id),
            fea_code=record["fea_code"],
            mfu_id=record.get("mfu_id"),
            version=int(record["version"])
            if record.get("version") is not None
            else INITIAL_ENTITY_VERSION,
            status=record["status"] or ModuleFeatureStatus.READY,
            justification=record.get("justification"),
            incremental_change_type=record.get("incremental_change_type"),
            feedback_change_type=record.get("feedback_change_type"),
            generation_metadata=(
                _json.loads(record["generation_metadata"])
                if isinstance(record.get("generation_metadata"), str)
                else record.get("generation_metadata")
            ),
            is_infrastructure=record.get("is_infrastructure"),
            condensation_note=record.get("condensation_note"),
            is_jira_synced=bool(record.get("is_jira_synced")),
            is_tap_synced=bool(record.get("is_tap_synced")),
            functions=ModuleFeatureRepository._parse_functions(record.get("functions")),
            sources=ModuleFeatureRepository._parse_sources(record.get("sources")),
            l2_sources=ModuleFeatureRepository._parse_l2_sources(record.get("l2_sources")),
            created_at=ModuleFeatureRepository._neo4j_dt_to_py(record.get("created_at")),
            updated_at=ModuleFeatureRepository._neo4j_dt_to_py(record.get("updated_at")),
        )

    def _execute_mark_module_delete_suggested(
        self,
        project_id: str,
        module_id: str,
        justification: str | None,
        source_ingestion_id: str | None = None,
    ) -> bool:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (m:Module {project_id: $project_id, id: $module_id}) "
            "SET m.incremental_change_type = $incremental_change_type, "
            "    m.justification           = coalesce($justification, m.justification), "
            "    m.source_ingestion_id     = coalesce($source_ingestion_id, m.source_ingestion_id), "
            "    m.version                 = coalesce(m.version, $version) + 1, "
            "    m.updated_at              = datetime($now) "
            "RETURN count(m) AS updated_count"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    project_id=project_id,
                    module_id=module_id,
                    justification=justification,
                    incremental_change_type=ChangeType.DELETE_SUGGESTED,
                    source_ingestion_id=source_ingestion_id,
                    version=INITIAL_ENTITY_VERSION,
                    now=now,
                ).single()
            )
        return bool(record and int(record["updated_count"]) > 0)

    def _execute_mark_feature_delete_suggested(
        self,
        project_id: str,
        feature_id: str,
        justification: str | None,
        source_ingestion_id: str | None = None,
    ) -> bool:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (f:Feature {project_id: $project_id, id: $feature_id}) "
            "SET f.incremental_change_type = $incremental_change_type, "
            "    f.justification           = coalesce($justification, f.justification), "
            "    f.source_ingestion_id     = coalesce($source_ingestion_id, f.source_ingestion_id), "
            "    f.version                 = coalesce(f.version, $version) + 1, "
            "    f.updated_at              = datetime($now) "
            "RETURN count(f) AS updated_count"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    project_id=project_id,
                    feature_id=feature_id,
                    justification=justification,
                    incremental_change_type=ChangeType.DELETE_SUGGESTED,
                    source_ingestion_id=source_ingestion_id,
                    version=INITIAL_ENTITY_VERSION,
                    now=now,
                ).single()
            )
        return bool(record and int(record["updated_count"]) > 0)
