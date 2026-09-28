"""Neo4j repository for user story graph operations."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
import re
from typing import Any
from uuid import UUID

from neo4j import Driver

from app.core.constants import INITIAL_ENTITY_VERSION
from app.db.neo4j import get_neo4j_driver
from app.models.neo4j.group_spec_model import GroupSpecModel
from app.models.neo4j.module_feature_model import ChangeType
from app.models.neo4j.srs_evidence_model import SRSEvidenceModel
from app.models.neo4j.user_story_model import UserStoryModel
from app.models.neo4j.version_model import UserStoryVersionModel
from app.schemas.user_story_schema import UserStoryStatus
from app.utils.logger import get_logger

logger = get_logger(__name__)


class UserStoryRepository:
    """Persistence layer for source-scoped user stories in Neo4j."""

    def __init__(self, driver: Driver | None = None) -> None:
        self._driver = driver or get_neo4j_driver()

    async def list_user_stories_for_project(
        self,
        *,
        project_id: UUID,
        skip: int = 0,
        limit: int = 20,
        status: str | None = None,
        version: int | None = None,
        module_id: str | None = None,
        feature_id: str | None = None,
        source_id: UUID | None = None,
        search_text: str | None = None,
        user_story_code: str | None = None,
        consensus_min: float | None = None,
        consensus_max: float | None = None,
    ) -> tuple[list[UserStoryModel], int]:
        return await asyncio.to_thread(
            self._execute_list_user_stories_for_project,
            str(project_id),
            skip,
            limit,
            status,
            version,
            module_id,
            feature_id,
            str(source_id) if source_id else None,
            search_text,
            user_story_code,
            consensus_min,
            consensus_max,
        )

    async def get_project_summary(self, project_id: UUID) -> dict:
        """Return summary counts for a project's user_stories."""
        return await asyncio.to_thread(
            self._execute_get_project_summary,
            str(project_id),
        )

    async def are_all_user_stories_approved(self, project_id: UUID) -> bool:
        """Return True only when every user_story in the project has status 'approved'.

        Returns False if the project has no user_stories.
        """
        return await asyncio.to_thread(
            self._execute_are_all_user_stories_approved,
            str(project_id),
        )

    async def count_approved_user_stories(self, project_id: UUID) -> int:
        """Return how many user_stories in the project have status 'approved'."""
        return await asyncio.to_thread(
            self._execute_count_approved_user_stories,
            str(project_id),
        )

    async def count_pending_changes_by_ingestion(self, source_ingestion_id: str) -> int:
        """Return how many UserStory nodes tagged by *source_ingestion_id* are still unresolved.

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

    async def get_source_ingestion_id(self, user_story_id: str) -> str | None:
        """Return the ``source_ingestion_id`` tagged on a UserStory node."""
        return await asyncio.to_thread(self._execute_get_source_ingestion_id, user_story_id)

    def _execute_get_source_ingestion_id(self, user_story_id: str) -> str | None:
        cypher = (
            "MATCH (r:UserStory {id: $user_story_id}) "
            "RETURN r.source_ingestion_id AS source_ingestion_id"
        )
        with self._driver.session() as session:
            record = session.execute_read(
                lambda tx: tx.run(cypher, user_story_id=user_story_id).single()
            )
        return record["source_ingestion_id"] if record else None

    async def delete_user_stories_for_project(
        self,
        *,
        project_id: UUID,
        source_ids: list[UUID],
    ) -> int:
        """Delete all project user_stories, including source-linked and project-scoped nodes."""
        return await asyncio.to_thread(
            self._execute_delete_user_stories_for_project,
            str(project_id),
            [str(sid) for sid in source_ids],
        )

    async def delete_user_story_by_id(
        self,
        *,
        project_id: UUID,
        user_story_id: str,
    ) -> bool:
        """Hard-delete a single UserStory node scoped to a project.

        Returns True if the node was found and deleted, False otherwise.
        Used for non-approved user stories.
        """
        return await asyncio.to_thread(
            self._execute_delete_user_story_by_id,
            str(project_id),
            user_story_id,
        )

    async def soft_delete_user_story_by_id(
        self,
        *,
        project_id: UUID,
        user_story_id: str,
        del_reason: str,
    ) -> bool:
        """Soft-delete an approved UserStory by setting is_current=false, del_reason, and deleted_at.

        Returns True if the node was found and updated, False otherwise.
        """
        return await asyncio.to_thread(
            self._execute_soft_delete_user_story_by_id,
            str(project_id),
            user_story_id,
            del_reason,
        )

    async def count_active_user_stories_for_feature(
        self,
        *,
        project_id: UUID,
        feature_id: str,
    ) -> int:
        """Count non-soft-deleted UserStory nodes attached to a feature.

        A UserStory counts as active when ``is_current`` is ``true`` or unset
        (legacy rows default to current). Used to detect when a feature has
        become orphaned after its last active user story was deleted.
        """
        return await asyncio.to_thread(
            self._execute_count_active_user_stories_for_feature,
            str(project_id),
            feature_id,
        )

    def _execute_count_active_user_stories_for_feature(
        self, project_id: str, feature_id: str
    ) -> int:
        cypher = (
            "MATCH (f:Feature {project_id: $project_id, id: $feature_id})"
            "-[:HAS_USER_STORY]->(r:UserStory) "
            "WHERE r.is_current = true OR r.is_current IS NULL "
            "RETURN count(r) AS story_count"
        )
        with self._driver.session() as session:
            record = session.execute_read(
                lambda tx: tx.run(cypher, project_id=project_id, feature_id=feature_id).single()
            )
        if record is None:
            return 0
        return int(record["story_count"])

    async def bulk_upsert_user_stories_for_project(
        self,
        *,
        project_id: UUID,
        user_stories: list[UserStoryModel],
    ) -> int:
        """Upsert project-scoped user_stories derived from backlog stories.

        An existing story's ``status`` is preserved (a human's
        approved/needs_edit review state survives regeneration).
        """
        if not user_stories:
            return 0
        return await asyncio.to_thread(
            self._execute_bulk_upsert_user_stories_for_project,
            str(project_id),
            user_stories,
        )

    async def bulk_upsert_user_stories_for_source_code(
        self,
        *,
        project_id: UUID,
        user_stories: list[UserStoryModel],
    ) -> int:
        """Upsert project-scoped user_stories derived from source code."""
        if not user_stories:
            return 0
        return await asyncio.to_thread(
            self._execute_bulk_upsert_user_stories_for_source_code,
            str(project_id),
            user_stories,
        )

    def get_project_summary_sync(self, project_id: UUID | str) -> dict:
        """Synchronous project summary lookup for sync service call paths."""
        return self._execute_get_project_summary(str(project_id))

    async def get_project_id_by_user_story_id(self, user_story_id: str) -> UUID | None:
        """Return the owning project's UUID for a user story, or None if it
        doesn't exist.

        Needed by routes that identify a user story by ID alone (no
        ``project_id`` path param — e.g. ``PATCH /user-stories/{id}/status``)
        so the caller can resolve and authorize the owning project *before*
        mutating anything.
        """
        return await asyncio.to_thread(self._execute_get_project_id, user_story_id)

    def _execute_get_project_id(self, user_story_id: str) -> UUID | None:
        cypher = "MATCH (r:UserStory {id: $user_story_id}) RETURN r.project_id AS project_id"
        with self._driver.session() as session:
            record = session.execute_read(
                lambda tx: tx.run(cypher, user_story_id=user_story_id).single()
            )
        if record is None or record["project_id"] is None:
            return None
        return UUID(record["project_id"])

    async def get_user_story_version_by_id(self, user_story_id: str) -> int | None:
        """Return the live ``version`` of a UserStory node by id, or None if not found.

        Unlike ``get_user_story_detail_for_project``, this matches by id alone
        instead of requiring the full ``Project-[:HAS_MODULE]->Module-
        [:HAS_FEATURE]->Feature-[:HAS_USER_STORY]->UserStory`` traversal, so a
        not-yet-established relationship link can't cause a false "not found"
        when the caller only needs the current version number (e.g. to compute
        the next version before an update).
        """
        return await asyncio.to_thread(self._execute_get_user_story_version_by_id, user_story_id)

    def _execute_get_user_story_version_by_id(self, user_story_id: str) -> int | None:
        cypher = "MATCH (r:UserStory {id: $user_story_id}) RETURN r.version AS version"
        with self._driver.session() as session:
            record = session.execute_read(
                lambda tx: tx.run(cypher, user_story_id=user_story_id).single()
            )
        if record is None or record["version"] is None:
            return None
        return int(record["version"])

    async def change_user_story_status(
        self, user_story_id: str, new_status: str
    ) -> UserStoryModel | None:
        """Update only the status field of a user_story node."""
        return await asyncio.to_thread(self._execute_change_status, user_story_id, new_status)

    async def set_user_story_status_and_flag(
        self, user_story_id: str, *, status: str, rfp_flagged_item: dict[str, Any] | None
    ) -> UserStoryModel | None:
        """Hard-set status and rfp_flagged_item, bypassing the upsert's coalesce.

        ``bulk_upsert_user_stories_for_project`` preserves an existing node's
        status/rfp_flagged_item via ``coalesce`` so a human's approved/
        needs_edit review state survives regeneration — but the incremental
        update pipeline's own pass/fail verdict for *this* run must win
        outright instead, including clearing a stale flag on a clean pass.
        """
        return await asyncio.to_thread(
            self._execute_set_status_and_flag, user_story_id, status, rfp_flagged_item
        )

    async def update_user_story_sync_flags(
        self,
        user_story_id: str,
        *,
        is_jira_synced: bool | None = None,
        is_tap_synced: bool | None = None,
    ) -> UserStoryModel | None:
        """Partially update the is_jira_synced/is_tap_synced flags of a user_story node.

        A ``None`` argument leaves that flag unchanged.
        """
        return await asyncio.to_thread(
            self._execute_update_sync_flags, user_story_id, is_jira_synced, is_tap_synced
        )

    async def snapshot_user_story_version(
        self,
        user_story_id: str,
    ) -> bool:
        """Copy the current UserStory node's properties into a new UserStoryVersion node.

        No-ops (returns False) when the user story does not exist. Call this
        before applying an update so the pre-update state is preserved.
        """
        return await asyncio.to_thread(self._execute_snapshot_user_story_version, user_story_id)

    async def accept_user_story(self, user_story_id: str, status: str) -> bool:
        """Approve a UserStory: set ``status`` and clear the incremental flags.

        Returns True when the node was found and updated, False otherwise.
        """
        return await asyncio.to_thread(self._execute_accept_user_story, user_story_id, status)

    async def accept_user_story_feedback_change(self, user_story_id: str) -> bool:
        """Approve a UserStory's feedback-driven regeneration change.

        Sets status to APPROVED and clears ``feedback_change_type`` only —
        ``incremental_change_type`` (a separate, independent flag) is left
        untouched. Returns True when the node was found and updated.
        """
        return await asyncio.to_thread(
            self._execute_accept_user_story_feedback_change, user_story_id
        )

    async def restore_user_story_from_latest_version(self, user_story_id: str) -> bool:
        """Roll a UserStory back to its most recent UserStoryVersion snapshot.

        Restores user_story_code/title/description/consensus/status/as_a/
        i_want_to/so_that/acceptance_criteria/nfrs/technical_notes/
        story_points/justification/sources/version from the latest
        ``(r)-[:HAS_VERSION]->(v:UserStoryVersion)`` node (ordered by
        ``snapshotted_at`` descending), then deletes that snapshot node since
        it has now been consumed by the rollback. Always clears
        ``text_diffs`` and ``incremental_change_type`` regardless of whether
        a snapshot exists. Returns True when the UserStory node was found,
        False otherwise.
        """
        return await asyncio.to_thread(
            self._execute_restore_user_story_from_latest_version, user_story_id
        )

    async def restore_user_story_from_latest_feedback_version(self, user_story_id: str) -> bool:
        """Roll a UserStory back to its most recent UserStoryVersion snapshot, rejecting a
        feedback-driven regeneration change.

        Same content rollback as ``restore_user_story_from_latest_version``, but
        clears ``feedback_change_type`` only — ``incremental_change_type`` is
        left untouched, since the two review flows are independent.
        """
        return await asyncio.to_thread(
            self._execute_restore_user_story_from_latest_feedback_version, user_story_id
        )

    async def mark_user_story_delete_suggested(
        self,
        user_story_id: str,
        justification: str | None = None,
        source_ingestion_id: str | None = None,
    ) -> UserStoryModel | None:
        """Flag a UserStory node as DELETE_SUGGESTED and bump its ``version`` counter.

        Call ``snapshot_user_story_version`` first (see
        ``IncrementalUpdateProcessorService.handle_deletes``) so the
        pre-suggestion state can be restored via
        ``restore_user_story_from_latest_version`` if the suggestion is
        rejected. ``source_ingestion_id``, when provided, records which
        SourceIngestion (e.g. the incremental run suggesting this delete) is
        now responsible for the node; omitted (None), the node's existing
        value is preserved.
        """
        return await asyncio.to_thread(
            self._execute_mark_delete_suggested, user_story_id, justification, source_ingestion_id
        )

    async def update_user_story_sources(
        self,
        user_story_id: str,
        sources: list[dict],
    ) -> UserStoryModel | None:
        """Update the sources property of a single UserStory node.

        Returns the updated UserStoryModel, or None if the node was not found.
        """
        serialized = self._serialize_json_list_for_neo4j(sources)
        return await asyncio.to_thread(self._execute_update_sources, user_story_id, serialized)

    async def update_user_story_bboxes(
        self,
        user_story_id: str,
        bboxes: list[dict],
    ) -> UserStoryModel | None:
        """Backward-compatible alias for older callers still using bboxes."""
        return await self.update_user_story_sources(user_story_id=user_story_id, sources=bboxes)

    async def change_all_user_story_status_by_project(
        self, project_id: str, new_status: str
    ) -> int:
        """Update status for every user_story node belonging to a project.

        Returns the number of user_story nodes updated.
        """
        return await asyncio.to_thread(
            self._execute_change_status_by_project, project_id, new_status
        )

    async def bulk_change_user_story_status_by_ids(
        self,
        project_id: str,
        user_story_ids: list[str],
        new_status: str,
    ) -> int:
        """Update status for specific user_story nodes that belong to a project.

        Only user_stories reachable via the
        ``Project->Module->Feature->HAS_USER_STORY`` traversal are updated,
        ensuring cross-project mutations are impossible.

        Returns the number of user_story nodes updated.
        """
        return await asyncio.to_thread(
            self._execute_bulk_change_status_by_ids,
            project_id,
            user_story_ids,
            new_status,
        )

    async def get_user_story_detail_for_project(
        self,
        project_id: UUID,
        user_story_id: str,
        include_deleted: bool = False,
    ) -> UserStoryModel | None:
        """Return the user story scoped to the given project, or None if not found."""
        return await asyncio.to_thread(
            self._execute_get_user_story_detail,
            str(project_id),
            user_story_id,
            include_deleted,
        )

    async def get_latest_user_story_version(
        self,
        user_story_id: str,
    ) -> UserStoryVersionModel | None:
        """Return the most recent HAS_VERSION snapshot for a user story, or None if none exists."""
        return await asyncio.to_thread(
            self._execute_get_latest_user_story_version,
            user_story_id,
        )

    async def get_max_user_story_code(
        self,
        project_id: UUID,
        feature_id: str,
    ) -> tuple[str, int]:
        """Return the feature's fea_code and the highest numeric user_story_code suffix.

        Parses the trailing integer from each ``user_story_code`` stored under
        the given feature (e.g. ``"U.S 1.1.3"`` → suffix 3, ``"U.S 2.4.10"``
        → suffix 10) and returns the maximum.  Returns ``(fea_code, 0)`` when
        the feature has no user stories yet so that the first code becomes
        ``"U.S {fea_code}.1"``.
        """
        return await asyncio.to_thread(
            self._execute_get_max_user_story_code,
            str(project_id),
            feature_id,
        )

    def _execute_get_max_user_story_code(self, project_id: str, feature_id: str) -> tuple[str, int]:
        cypher = (
            "MATCH (f:Feature {id: $feature_id}) "
            "WHERE f.project_id = $project_id "
            "OPTIONAL MATCH (f)-[:HAS_USER_STORY]->(r:UserStory) "
            "WITH f.fea_code AS fea_code, r.user_story_code AS code "
            "RETURN fea_code, "
            "       max(toInteger(split(coalesce(code, '0.0.0'), '.')[-1])) AS max_suffix"
        )
        with self._driver.session() as session:
            record = session.execute_read(
                lambda tx: tx.run(cypher, project_id=project_id, feature_id=feature_id).single()
            )
        if record is None:
            return ("", 0)
        return (record["fea_code"] or "", int(record["max_suffix"] or 0))

    def _execute_delete_user_stories_for_project(
        self, project_id: str, source_ids: list[str]
    ) -> int:
        cypher = (
            "MATCH (r:UserStory) "
            "WHERE r.project_id = $project_id "
            "DETACH DELETE r "
            "RETURN count(r) AS deleted_count"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(cypher, project_id=project_id, source_ids=source_ids).single()
            )
        if record is None:
            return 0
        return int(record["deleted_count"])

    def _execute_delete_user_story_by_id(self, project_id: str, user_story_id: str) -> bool:
        cypher = (
            "MATCH (r:UserStory {id: $user_story_id}) "
            "WHERE r.project_id = $project_id "
            "DETACH DELETE r "
            "RETURN count(r) AS deleted_count"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    project_id=project_id,
                    user_story_id=user_story_id,
                ).single()
            )
        return bool(record and int(record["deleted_count"]) > 0)

    def _execute_soft_delete_user_story_by_id(
        self, project_id: str, user_story_id: str, del_reason: str
    ) -> bool:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (r:UserStory {id: $user_story_id}) "
            "WHERE r.project_id = $project_id "
            "SET r.del_reason = $del_reason, "
            "    r.status = 'deleted', "
            "    r.deleted_at = datetime($now), "
            "    r.incremental_change_type = null, "
            "    r.is_tap_synced = false, "
            "    r.updated_at = datetime($now) "
            "RETURN count(r) AS updated_count"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    project_id=project_id,
                    user_story_id=user_story_id,
                    del_reason=del_reason,
                    now=now,
                ).single()
            )
        return bool(record and int(record["updated_count"]) > 0)

    def _execute_snapshot_user_story_version(self, user_story_id: str) -> bool:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (r:UserStory {id: $user_story_id}) "
            "CREATE (v:UserStoryVersion { "
            "    id: randomUUID(), "
            "    user_story_id: r.id, "
            "    feature_id: r.feature_id, "
            "    project_id: r.project_id, "
            "    user_story_code: r.user_story_code, "
            "    title: r.title, "
            "    description: r.description, "
            "    consensus: r.consensus, "
            "    status: r.status, "
            "    version: r.version, "
            "    as_a: r.as_a, "
            "    i_want_to: r.i_want_to, "
            "    so_that: r.so_that, "
            "    acceptance_criteria: r.acceptance_criteria, "
            "    nfrs: r.nfrs, "
            "    technical_notes: r.technical_notes, "
            "    story_points: r.story_points, "
            "    justification: r.justification, "
            "    incremental_change_type: r.incremental_change_type, "
            "    feedback_change_type: r.feedback_change_type, "
            "    sources: coalesce(r.sources, r.bboxes, []), "
            "    l2_sources: coalesce(r.l2_sources, []), "
            "    is_jira_synced: coalesce(r.is_jira_synced, false), "
            "    is_tap_synced: coalesce(r.is_tap_synced, false), "
            "    source_ingestion_id: r.source_ingestion_id, "
            "    created_at: r.created_at, "
            "    updated_at: r.updated_at, "
            "    snapshotted_at: datetime($now) "
            "}) "
            "MERGE (r)-[:HAS_VERSION]->(v) "
            "RETURN count(v) AS created_count"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(cypher, user_story_id=user_story_id, now=now).single()
            )
        return bool(record and int(record["created_count"]) > 0)

    def _execute_get_latest_user_story_version(
        self, user_story_id: str
    ) -> UserStoryVersionModel | None:
        cypher = (
            "MATCH (r:UserStory {id: $user_story_id})-[:HAS_VERSION]->(v:UserStoryVersion) "
            "WITH v ORDER BY v.snapshotted_at DESC "
            "RETURN v.id AS id, "
            "       v.user_story_id AS user_story_id, "
            "       v.feature_id AS feature_id, "
            "       v.project_id AS project_id, "
            "       v.user_story_code AS user_story_code, "
            "       v.title AS title, "
            "       v.description AS description, "
            "       v.consensus AS consensus, "
            "       v.status AS status, "
            "       v.version AS version, "
            "       v.as_a AS as_a, "
            "       v.i_want_to AS i_want_to, "
            "       v.so_that AS so_that, "
            "       v.acceptance_criteria AS acceptance_criteria, "
            "       v.nfrs AS nfrs, "
            "       v.technical_notes AS technical_notes, "
            "       v.story_points AS story_points, "
            "       v.justification AS justification, "
            "       v.incremental_change_type AS incremental_change_type, "
            "       v.feedback_change_type AS feedback_change_type, "
            "       v.sources AS sources, "
            "       v.l2_sources AS l2_sources, "
            "       coalesce(v.is_jira_synced, false) AS is_jira_synced, "
            "       coalesce(v.is_tap_synced, false) AS is_tap_synced, "
            "       v.created_at AS created_at, "
            "       v.updated_at AS updated_at, "
            "       v.snapshotted_at AS snapshotted_at "
            "LIMIT 1"
        )
        with self._driver.session() as session:
            record = session.execute_read(
                lambda tx: tx.run(cypher, user_story_id=user_story_id).single()
            )
        if record is None:
            return None
        return self._record_to_version_model(record)

    @staticmethod
    def _record_to_version_model(record: dict) -> UserStoryVersionModel:
        project_id_raw = record.get("project_id")
        return UserStoryVersionModel(
            id=str(record["id"]),
            user_story_id=str(record["user_story_id"]),
            feature_id=record.get("feature_id"),
            project_id=UUID(str(project_id_raw)) if project_id_raw else None,
            user_story_code=record.get("user_story_code") or "",
            title=record.get("title") or "",
            description=record.get("description"),
            consensus=float(record["consensus"]) if record.get("consensus") is not None else None,
            status=record.get("status"),
            version=int(float(record["version"])) if record.get("version") is not None else 1,
            as_a=record.get("as_a"),
            i_want_to=record.get("i_want_to"),
            so_that=record.get("so_that"),
            acceptance_criteria=UserStoryRepository._deserialize_json_list_from_neo4j(
                record.get("acceptance_criteria")
            ),
            nfrs=UserStoryRepository._deserialize_json_list_from_neo4j(record.get("nfrs")),
            technical_notes=record.get("technical_notes"),
            story_points=int(record["story_points"])
            if record.get("story_points") is not None
            else None,
            justification=record.get("justification"),
            incremental_change_type=record.get("incremental_change_type"),
            feedback_change_type=record.get("feedback_change_type"),
            sources=UserStoryRepository._deserialize_json_list_from_neo4j(record.get("sources")),
            l2_sources=UserStoryRepository._deserialize_l2_sources_from_neo4j(
                record.get("l2_sources")
            ),
            is_jira_synced=bool(record["is_jira_synced"])
            if record.get("is_jira_synced") is not None
            else False,
            is_tap_synced=bool(record["is_tap_synced"])
            if record.get("is_tap_synced") is not None
            else False,
            created_at=UserStoryRepository._neo4j_dt_to_py(record.get("created_at")),
            updated_at=UserStoryRepository._neo4j_dt_to_py(record.get("updated_at")),
            snapshotted_at=UserStoryRepository._neo4j_dt_to_py(record.get("snapshotted_at")),
        )

    def _execute_accept_user_story(self, user_story_id: str, status: str) -> bool:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (r:UserStory {id: $user_story_id}) "
            "SET r.status                  = $status, "
            "    r.text_diffs              = [], "
            "    r.incremental_change_type = null, "
            "    r.is_jira_synced           = false, "
            "    r.is_tap_synced            = false, "
            "    r.updated_at              = datetime($now) "
            "RETURN count(r) AS updated_count"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    user_story_id=user_story_id,
                    status=status,
                    now=now,
                ).single()
            )
        return bool(record and int(record["updated_count"]) > 0)

    def _execute_accept_user_story_feedback_change(self, user_story_id: str) -> bool:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (r:UserStory {id: $user_story_id}) "
            "SET r.status                = $status, "
            "    r.feedback_change_type = null, "
            "    r.updated_at           = datetime($now) "
            "RETURN count(r) AS updated_count"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    user_story_id=user_story_id,
                    status=UserStoryStatus.APPROVED.value,
                    now=now,
                ).single()
            )
        return bool(record and int(record["updated_count"]) > 0)

    def _execute_restore_user_story_from_latest_feedback_version(self, user_story_id: str) -> bool:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (r:UserStory {id: $user_story_id}) "
            "OPTIONAL MATCH (r)-[:HAS_VERSION]->(v:UserStoryVersion) "
            "WITH r, v ORDER BY v.snapshotted_at DESC "
            "WITH r, collect(v) AS versions "
            "WITH r, CASE WHEN size(versions) > 0 THEN versions[0] ELSE null END AS latest "
            "WITH r, latest, latest IS NOT NULL AS restored "
            "SET r.feedback_change_type = null, "
            "    r.updated_at           = datetime($now) "
            "FOREACH (_ IN CASE WHEN latest IS NOT NULL THEN [1] ELSE [] END | "
            "    SET r.user_story_code     = latest.user_story_code, "
            "        r.title               = latest.title, "
            "        r.description         = latest.description, "
            "        r.consensus           = latest.consensus, "
            "        r.status              = latest.status, "
            "        r.as_a                = latest.as_a, "
            "        r.i_want_to           = latest.i_want_to, "
            "        r.so_that             = latest.so_that, "
            "        r.acceptance_criteria = latest.acceptance_criteria, "
            "        r.nfrs                = latest.nfrs, "
            "        r.technical_notes     = latest.technical_notes, "
            "        r.story_points        = latest.story_points, "
            "        r.justification       = latest.justification, "
            "        r.sources             = latest.sources, "
            "        r.l2_sources          = latest.l2_sources, "
            "        r.version             = latest.version "
            ") "
            "FOREACH (_ IN CASE WHEN latest IS NOT NULL THEN [1] ELSE [] END | "
            "    DETACH DELETE latest "
            ") "
            "RETURN count(r) AS updated_count, restored"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(cypher, user_story_id=user_story_id, now=now).single()
            )
        if record is None or int(record["updated_count"]) == 0:
            return False
        if not record["restored"]:
            logger.warning(
                "[FEEDBACK_UPDATES] restore_user_story_from_latest_feedback_version: no "
                "UserStoryVersion snapshot found — cleared flag only, no content rollback. "
                "user_story_id=%s",
                user_story_id,
            )
        return True

    def _execute_restore_user_story_from_latest_version(self, user_story_id: str) -> bool:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (r:UserStory {id: $user_story_id}) "
            "OPTIONAL MATCH (r)-[:HAS_VERSION]->(v:UserStoryVersion) "
            "WITH r, v ORDER BY v.snapshotted_at DESC "
            "WITH r, collect(v) AS versions "
            "WITH r, CASE WHEN size(versions) > 0 THEN versions[0] ELSE null END AS latest "
            "WITH r, latest, latest IS NOT NULL AS restored "
            "SET r.text_diffs              = [], "
            "    r.incremental_change_type = null, "
            "    r.updated_at              = datetime($now) "
            "FOREACH (_ IN CASE WHEN latest IS NOT NULL THEN [1] ELSE [] END | "
            "    SET r.user_story_code    = latest.user_story_code, "
            "        r.title              = latest.title, "
            "        r.description        = latest.description, "
            "        r.consensus          = latest.consensus, "
            "        r.status             = latest.status, "
            "        r.as_a               = latest.as_a, "
            "        r.i_want_to          = latest.i_want_to, "
            "        r.so_that            = latest.so_that, "
            "        r.acceptance_criteria = latest.acceptance_criteria, "
            "        r.nfrs               = latest.nfrs, "
            "        r.technical_notes    = latest.technical_notes, "
            "        r.story_points       = latest.story_points, "
            "        r.justification      = latest.justification, "
            "        r.sources            = latest.sources, "
            "        r.l2_sources         = latest.l2_sources, "
            "        r.version            = latest.version "
            ") "
            "FOREACH (_ IN CASE WHEN latest IS NOT NULL THEN [1] ELSE [] END | "
            "    DETACH DELETE latest "
            ") "
            "RETURN count(r) AS updated_count, restored"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(cypher, user_story_id=user_story_id, now=now).single()
            )
        if record is None or int(record["updated_count"]) == 0:
            return False
        if not record["restored"]:
            logger.warning(
                "[UPDATES] restore_user_story_from_latest_version: no UserStoryVersion "
                "snapshot found — cleared flags only, no content rollback. user_story_id=%s",
                user_story_id,
            )
        return True

    def _execute_bulk_upsert_user_stories_for_project(
        self,
        project_id: str,
        user_stories: list[UserStoryModel],
    ) -> int:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (p:Project {id: $project_id}) "
            "UNWIND $user_stories AS req "
            "MERGE (r:UserStory {id: req.user_story_id}) "
            "SET r.project_id = $project_id, "
            "    r.user_story_code = req.user_story_code, "
            "    r.title = req.title, "
            "    r.description = req.description, "
            "    r.consensus = req.consensus, "
            "    r.status = coalesce(r.status, req.status), "
            "    r.version = CASE WHEN r.version IS NULL OR req.version >= r.version "
            "                     THEN req.version ELSE r.version END, "
            "    r.feature_id = req.feature_id, "
            "    r.as_a = req.as_a, "
            "    r.i_want_to = req.i_want_to, "
            "    r.so_that = req.so_that, "
            "    r.acceptance_criteria = coalesce(req.acceptance_criteria, []), "
            "    r.nfrs = coalesce(req.nfrs, []), "
            "    r.technical_notes = req.technical_notes, "
            "    r.story_points = req.story_points, "
            "    r.justification = req.justification, "
            "    r.incremental_change_type = req.incremental_change_type, "
            "    r.feedback_change_type = req.feedback_change_type, "
            "    r.sources = coalesce(req.sources, []), "
            "    r.l2_sources = coalesce(req.l2_sources, []), "
            "    r.text_diffs = req.text_diffs, "
            "    r.is_current = coalesce(req.is_current, true), "
            "    r.is_jira_synced = coalesce(r.is_jira_synced, false), "
            "    r.is_tap_synced = coalesce(r.is_tap_synced, false), "
            "    r.source_ingestion_id = CASE WHEN r.source_ingestion_id IS NULL "
            "                                 OR req.feedback_change_type IS NOT NULL "
            "                                 OR req.incremental_change_type IS NOT NULL "
            "                                 THEN req.source_ingestion_id "
            "                                 ELSE r.source_ingestion_id END, "
            "    r.source_file_count = size(coalesce(req.sources, [])), "
            "    r.del_reason = coalesce(r.del_reason, req.del_reason), "
            "    r.rfp_flagged_item = coalesce(r.rfp_flagged_item, req.rfp_flagged_item), "
            "    r.created_at = coalesce(r.created_at, datetime($now)), "
            "    r.updated_at = datetime($now) "
            "WITH r, req, p "
            "OPTIONAL MATCH (p)-[:HAS_MODULE]->(:Module)-[:HAS_FEATURE]->(ft:Feature {id: req.feature_id}) "
            "FOREACH (_ IN CASE WHEN ft IS NOT NULL THEN [1] ELSE [] END | "
            "  MERGE (ft)-[:HAS_USER_STORY]->(r) "
            ") "
            "RETURN count(DISTINCT r) AS upserted_count"
        )
        req_rows = [
            {
                "user_story_id": req.id,
                "user_story_code": req.user_story_code,
                "title": req.title,
                "description": req.description,
                "consensus": req.consensus,
                "status": req.status,
                "version": req.version,
                "feature_id": req.feature_id,
                "as_a": req.as_a,
                "i_want_to": req.i_want_to,
                "so_that": req.so_that,
                "acceptance_criteria": self._serialize_json_list_for_neo4j(req.acceptance_criteria),
                "nfrs": self._serialize_json_list_for_neo4j(req.nfrs),
                "technical_notes": req.technical_notes,
                "story_points": req.story_points,
                "justification": req.justification,
                "incremental_change_type": req.incremental_change_type,
                "feedback_change_type": req.feedback_change_type,
                "sources": self._serialize_json_list_for_neo4j(req.sources),
                "l2_sources": self._serialize_json_list_for_neo4j(req.l2_sources),
                "text_diffs": self._serialize_json_field(req.text_diffs),
                "is_current": req.is_current,
                "del_reason": req.del_reason,
                "rfp_flagged_item": self._serialize_json_field(req.rfp_flagged_item),
                "source_ingestion_id": req.source_ingestion_id,
            }
            for req in user_stories
        ]
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    project_id=project_id,
                    user_stories=req_rows,
                    now=now,
                ).single()
            )
        if record is None:
            return 0
        return int(record["upserted_count"])

    def _execute_bulk_upsert_user_stories_for_source_code(
        self,
        project_id: str,
        user_stories: list[UserStoryModel],
    ) -> int:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (p:Project {id: $project_id}) "
            "UNWIND $user_stories AS req "
            "MERGE (r:UserStory {id: req.user_story_id}) "
            "SET r.project_id = $project_id, "
            "    r.user_story_code = req.user_story_code, "
            "    r.title = req.title, "
            "    r.description = req.description, "
            "    r.consensus = req.consensus, "
            "    r.status = coalesce(r.status, req.status), "
            "    r.version = req.version, "
            "    r.feature_id = req.feature_id, "
            "    r.as_a = req.as_a, "
            "    r.i_want_to = req.i_want_to, "
            "    r.so_that = req.so_that, "
            "    r.acceptance_criteria = coalesce(req.acceptance_criteria, []), "
            "    r.nfrs = coalesce(req.nfrs, []), "
            "    r.technical_notes = req.technical_notes, "
            "    r.story_points = req.story_points, "
            "    r.sources = coalesce(req.sources, []), "
            "    r.l2_sources = coalesce(req.l2_sources, []), "
            "    r.screens = req.screens, "
            "    r.is_current = coalesce(req.is_current, true), "
            "    r.is_jira_synced = coalesce(r.is_jira_synced, false), "
            "    r.is_tap_synced = coalesce(r.is_tap_synced, false), "
            "    r.source_ingestion_id = req.source_ingestion_id, "
            "    r.source_file_count = size(coalesce(req.sources, [])), "
            "    r.del_reason = coalesce(r.del_reason, req.del_reason), "
            "    r.created_at = coalesce(r.created_at, datetime($now)), "
            "    r.updated_at = datetime($now) "
            "WITH r, req, p "
            "OPTIONAL MATCH (p)-[:HAS_MODULE]->(:Module)-[:HAS_FEATURE]->(ft:Feature {id: req.feature_id}) "
            "FOREACH (_ IN CASE WHEN ft IS NOT NULL THEN [1] ELSE [] END | "
            "  MERGE (ft)-[:HAS_USER_STORY]->(r) "
            ") "
            "RETURN count(DISTINCT r) AS upserted_count"
        )
        req_rows = [
            {
                "user_story_id": req.id,
                "user_story_code": req.user_story_code,
                "title": req.title,
                "description": req.description,
                "consensus": req.consensus,
                "status": req.status,
                "version": req.version,
                "feature_id": req.feature_id,
                "as_a": req.as_a,
                "i_want_to": req.i_want_to,
                "so_that": req.so_that,
                "acceptance_criteria": self._serialize_json_list_for_neo4j(req.acceptance_criteria),
                "nfrs": self._serialize_json_list_for_neo4j(req.nfrs),
                "technical_notes": req.technical_notes,
                "story_points": req.story_points,
                "sources": self._serialize_json_list_for_neo4j(req.sources),
                "l2_sources": self._serialize_json_list_for_neo4j(req.l2_sources),
                "screens": self._serialize_json_list_for_neo4j(req.screens)
                if req.screens
                else None,
                "is_current": req.is_current,
                "del_reason": req.del_reason,
                "source_ingestion_id": req.source_ingestion_id,
            }
            for req in user_stories
        ]
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    project_id=project_id,
                    user_stories=req_rows,
                    now=now,
                ).single()
            )
        if record is None:
            return 0
        return int(record["upserted_count"])

    def _execute_change_status(self, user_story_id: str, new_status: str) -> UserStoryModel | None:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (r:UserStory {id: $user_story_id}) "
            "SET r.status = $status, "
            "    r.created_at = coalesce(r.created_at, datetime($now)), "
            "    r.updated_at = datetime($now) "
            "RETURN r.id AS user_story_id, "
            "       r.user_story_code AS user_story_code, "
            "       r.title AS title, "
            "       r.description AS description, "
            "       r.consensus AS consensus, "
            "       r.status AS status, "
            "       r.version AS version, "
            "       r.feature_id AS feature_id, "
            "       r.project_id AS project_id, "
            "       r.as_a AS as_a, "
            "       r.i_want_to AS i_want_to, "
            "       r.so_that AS so_that, "
            "       r.acceptance_criteria AS acceptance_criteria, "
            "       r.nfrs AS nfrs, "
            "       r.technical_notes AS technical_notes, "
            "       r.story_points AS story_points, "
            "       coalesce(r.sources, r.bboxes, []) AS sources, "
            "       coalesce(r.l2_sources, []) AS l2_sources, "
            "       r.is_current AS is_current, "
            "       coalesce(r.is_jira_synced, false) AS is_jira_synced, "
            "       coalesce(r.is_tap_synced, false) AS is_tap_synced, "
            "       r.del_reason AS del_reason, "
            "       r.deleted_at AS deleted_at, "
            "       r.rfp_flagged_item AS rfp_flagged_item, "
            "       r.created_at AS created_at, "
            "       r.updated_at AS updated_at"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    user_story_id=user_story_id,
                    status=new_status,
                    now=now,
                ).single()
            )
        if record is None:
            return None
        return self._record_to_model(record)

    def _execute_set_status_and_flag(
        self, user_story_id: str, status: str, rfp_flagged_item: dict[str, Any] | None
    ) -> UserStoryModel | None:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (r:UserStory {id: $user_story_id}) "
            "SET r.status = $status, "
            "    r.rfp_flagged_item = $rfp_flagged_item, "
            "    r.created_at = coalesce(r.created_at, datetime($now)), "
            "    r.updated_at = datetime($now) "
            "RETURN r.id AS user_story_id, "
            "       r.user_story_code AS user_story_code, "
            "       r.title AS title, "
            "       r.description AS description, "
            "       r.consensus AS consensus, "
            "       r.status AS status, "
            "       r.version AS version, "
            "       r.feature_id AS feature_id, "
            "       r.project_id AS project_id, "
            "       r.as_a AS as_a, "
            "       r.i_want_to AS i_want_to, "
            "       r.so_that AS so_that, "
            "       r.acceptance_criteria AS acceptance_criteria, "
            "       r.nfrs AS nfrs, "
            "       r.technical_notes AS technical_notes, "
            "       r.story_points AS story_points, "
            "       coalesce(r.sources, r.bboxes, []) AS sources, "
            "       coalesce(r.l2_sources, []) AS l2_sources, "
            "       r.is_current AS is_current, "
            "       coalesce(r.is_jira_synced, false) AS is_jira_synced, "
            "       coalesce(r.is_tap_synced, false) AS is_tap_synced, "
            "       r.del_reason AS del_reason, "
            "       r.deleted_at AS deleted_at, "
            "       r.rfp_flagged_item AS rfp_flagged_item, "
            "       r.created_at AS created_at, "
            "       r.updated_at AS updated_at"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    user_story_id=user_story_id,
                    status=status,
                    rfp_flagged_item=self._serialize_json_field(rfp_flagged_item),
                    now=now,
                ).single()
            )
        if record is None:
            return None
        return self._record_to_model(record)

    def _execute_update_sync_flags(
        self,
        user_story_id: str,
        is_jira_synced: bool | None,
        is_tap_synced: bool | None,
    ) -> UserStoryModel | None:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (r:UserStory {id: $user_story_id}) "
            "SET r.is_jira_synced = coalesce($is_jira_synced, r.is_jira_synced, false), "
            "    r.is_tap_synced = coalesce($is_tap_synced, r.is_tap_synced, false), "
            "    r.created_at = coalesce(r.created_at, datetime($now)), "
            "    r.updated_at = datetime($now) "
            "RETURN r.id AS user_story_id, "
            "       r.user_story_code AS user_story_code, "
            "       r.title AS title, "
            "       r.description AS description, "
            "       r.consensus AS consensus, "
            "       r.status AS status, "
            "       r.version AS version, "
            "       r.feature_id AS feature_id, "
            "       r.project_id AS project_id, "
            "       r.as_a AS as_a, "
            "       r.i_want_to AS i_want_to, "
            "       r.so_that AS so_that, "
            "       r.acceptance_criteria AS acceptance_criteria, "
            "       r.nfrs AS nfrs, "
            "       r.technical_notes AS technical_notes, "
            "       r.story_points AS story_points, "
            "       coalesce(r.sources, r.bboxes, []) AS sources, "
            "       coalesce(r.l2_sources, []) AS l2_sources, "
            "       r.is_current AS is_current, "
            "       coalesce(r.is_jira_synced, false) AS is_jira_synced, "
            "       coalesce(r.is_tap_synced, false) AS is_tap_synced, "
            "       r.del_reason AS del_reason, "
            "       r.deleted_at AS deleted_at, "
            "       r.rfp_flagged_item AS rfp_flagged_item, "
            "       r.created_at AS created_at, "
            "       r.updated_at AS updated_at"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    user_story_id=user_story_id,
                    is_jira_synced=is_jira_synced,
                    is_tap_synced=is_tap_synced,
                    now=now,
                ).single()
            )
        if record is None:
            return None
        return self._record_to_model(record)

    def _execute_mark_delete_suggested(
        self,
        user_story_id: str,
        justification: str | None,
        source_ingestion_id: str | None = None,
    ) -> UserStoryModel | None:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (r:UserStory {id: $user_story_id}) "
            "SET r.incremental_change_type = $incremental_change_type, "
            "    r.justification = coalesce($justification, r.justification), "
            "    r.source_ingestion_id = coalesce($source_ingestion_id, r.source_ingestion_id), "
            "    r.version = coalesce(r.version, $version) + 1, "
            "    r.created_at = coalesce(r.created_at, datetime($now)), "
            "    r.updated_at = datetime($now) "
            "RETURN r.id AS user_story_id, "
            "       r.user_story_code AS user_story_code, "
            "       r.title AS title, "
            "       r.description AS description, "
            "       r.consensus AS consensus, "
            "       r.status AS status, "
            "       r.version AS version, "
            "       r.feature_id AS feature_id, "
            "       r.project_id AS project_id, "
            "       r.as_a AS as_a, "
            "       r.i_want_to AS i_want_to, "
            "       r.so_that AS so_that, "
            "       r.acceptance_criteria AS acceptance_criteria, "
            "       r.nfrs AS nfrs, "
            "       r.technical_notes AS technical_notes, "
            "       r.story_points AS story_points, "
            "       coalesce(r.sources, r.bboxes, []) AS sources, "
            "       coalesce(r.l2_sources, []) AS l2_sources, "
            "       r.is_current AS is_current, "
            "       coalesce(r.is_jira_synced, false) AS is_jira_synced, "
            "       coalesce(r.is_tap_synced, false) AS is_tap_synced, "
            "       r.justification AS justification, "
            "       r.incremental_change_type AS incremental_change_type, "
            "       r.feedback_change_type AS feedback_change_type, "
            "       r.del_reason AS del_reason, "
            "       r.deleted_at AS deleted_at, "
            "       r.rfp_flagged_item AS rfp_flagged_item, "
            "       r.created_at AS created_at, "
            "       r.updated_at AS updated_at"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    user_story_id=user_story_id,
                    justification=justification,
                    incremental_change_type=ChangeType.DELETE_SUGGESTED,
                    source_ingestion_id=source_ingestion_id,
                    version=INITIAL_ENTITY_VERSION,
                    now=now,
                ).single()
            )
        if record is None:
            return None
        return self._record_to_model(record)

    def _execute_update_sources(
        self, user_story_id: str, sources_serialized: list[str]
    ) -> UserStoryModel | None:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (r:UserStory {id: $user_story_id}) "
            "SET r.sources = $sources, "
            "    r.source_file_count = $source_file_count, "
            "    r.created_at = coalesce(r.created_at, datetime($now)), "
            "    r.updated_at = datetime($now) "
            "RETURN r.id AS user_story_id, "
            "       r.user_story_code AS user_story_code, "
            "       r.title AS title, "
            "       r.description AS description, "
            "       r.consensus AS consensus, "
            "       r.status AS status, "
            "       r.version AS version, "
            "       r.feature_id AS feature_id, "
            "       r.project_id AS project_id, "
            "       r.as_a AS as_a, "
            "       r.i_want_to AS i_want_to, "
            "       r.so_that AS so_that, "
            "       r.acceptance_criteria AS acceptance_criteria, "
            "       r.technical_notes AS technical_notes, "
            "       r.story_points AS story_points, "
            "       coalesce(r.sources, r.bboxes, []) AS sources, "
            "       coalesce(r.l2_sources, []) AS l2_sources, "
            "       r.is_current AS is_current, "
            "       coalesce(r.is_jira_synced, false) AS is_jira_synced, "
            "       coalesce(r.is_tap_synced, false) AS is_tap_synced, "
            "       r.del_reason AS del_reason, "
            "       r.deleted_at AS deleted_at, "
            "       r.rfp_flagged_item AS rfp_flagged_item, "
            "       r.created_at AS created_at, "
            "       r.updated_at AS updated_at"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    user_story_id=user_story_id,
                    sources=sources_serialized,
                    source_file_count=len(sources_serialized),
                    now=now,
                ).single()
            )
        if record is None:
            return None
        return self._record_to_model(record)

    def _execute_change_status_by_project(self, project_id: str, new_status: str) -> int:
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (p:Project {id: $project_id})-[:HAS_MODULE]->(:Module)-[:HAS_FEATURE]->(:Feature)-[:HAS_USER_STORY]->(r:UserStory) "
            "SET r.status = $status, "
            "    r.created_at = coalesce(r.created_at, datetime($now)), "
            "    r.updated_at = datetime($now) "
            "RETURN count(r) AS updated_count"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    project_id=project_id,
                    status=new_status,
                    now=now,
                ).single()
            )
        return int(record["updated_count"]) if record else 0

    def _execute_bulk_change_status_by_ids(
        self,
        project_id: str,
        user_story_ids: list[str],
        new_status: str,
    ) -> int:
        """Set *new_status* on the given user_story IDs, scoped to *project_id*.

        The ``Project->Module->Feature->HAS_USER_STORY`` traversal ensures that
        only user_stories belonging to this project are touched, even if the
        caller supplies IDs from another project.
        """
        now = datetime.now(UTC).isoformat()
        cypher = (
            "MATCH (p:Project {id: $project_id})-[:HAS_MODULE]->(:Module)-[:HAS_FEATURE]->(:Feature)-[:HAS_USER_STORY]->(r:UserStory) "
            "WHERE r.id IN $user_story_ids "
            "WITH DISTINCT r "
            "SET r.status = $status, "
            "    r.created_at = coalesce(r.created_at, datetime($now)), "
            "    r.updated_at = datetime($now) "
            "RETURN count(r) AS updated_count"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    project_id=project_id,
                    user_story_ids=user_story_ids,
                    status=new_status,
                    now=now,
                ).single()
            )
        return int(record["updated_count"]) if record else 0

    @staticmethod
    def _neo4j_dt_to_py(value) -> datetime | None:
        """Convert a Neo4j DateTime value to a Python datetime, or return None."""
        if value is None:
            return None
        from datetime import datetime as _dt

        if isinstance(value, _dt):
            return value
        # neo4j.time.DateTime — convert via .to_native()
        try:
            return value.to_native()
        except AttributeError:
            return None

    @staticmethod
    def _record_to_model(record: dict) -> UserStoryModel:
        project_id_raw = record.get("project_id")
        return UserStoryModel(
            id=str(record["user_story_id"]),
            user_story_code=str(record["user_story_code"]),
            title=str(record["title"]),
            description=record["description"],
            consensus=float(record["consensus"]),
            status=str(record["status"]),
            # int(float(...)) tolerates legacy "1.0"-style string versions
            # written before version became a plain integer.
            version=int(float(record["version"])) if record.get("version") is not None else 1,
            feature_id=record["feature_id"],
            # Present only in the project-scoped detail query's RETURN; other
            # executors omit these keys, so .get() yields None there.
            mfu_id=record.get("mfu_id"),
            mod_code=record.get("mod_code"),
            project_id=UUID(str(project_id_raw)) if project_id_raw else None,
            as_a=record.get("as_a"),
            i_want_to=record.get("i_want_to"),
            so_that=record.get("so_that"),
            acceptance_criteria=UserStoryRepository._deserialize_json_list_from_neo4j(
                record.get("acceptance_criteria")
            ),
            nfrs=UserStoryRepository._deserialize_json_list_from_neo4j(record.get("nfrs")),
            technical_notes=record.get("technical_notes"),
            story_points=int(record["story_points"])
            if record.get("story_points") is not None
            else None,
            justification=record.get("justification"),
            incremental_change_type=record.get("incremental_change_type"),
            feedback_change_type=record.get("feedback_change_type"),
            sources=UserStoryRepository._deserialize_json_list_from_neo4j(
                record.get("sources") if record.get("sources") is not None else record.get("bboxes")
            ),
            l2_sources=UserStoryRepository._deserialize_l2_sources_from_neo4j(
                record.get("l2_sources")
            ),
            screens=UserStoryRepository._deserialize_json_list_from_neo4j(record.get("screens"))
            or None,
            rfp_flagged_item=UserStoryRepository._deserialize_json_field(
                record.get("rfp_flagged_item")
            ),
            text_diffs=UserStoryRepository._deserialize_json_field(record.get("text_diffs")) or {},
            is_current=bool(record["is_current"]) if record.get("is_current") is not None else True,
            is_jira_synced=bool(record["is_jira_synced"])
            if record.get("is_jira_synced") is not None
            else False,
            is_tap_synced=bool(record["is_tap_synced"])
            if record.get("is_tap_synced") is not None
            else False,
            del_reason=record.get("del_reason"),
            deleted_at=UserStoryRepository._neo4j_dt_to_py(record.get("deleted_at")),
            source_file_count=int(record.get("source_file_count") or 0),
            srs_evidence=UserStoryRepository._deserialize_srs_evidence_from_neo4j(
                record.get("srs_evidence")
            ),
            created_at=UserStoryRepository._neo4j_dt_to_py(record.get("created_at")),
            updated_at=UserStoryRepository._neo4j_dt_to_py(record.get("updated_at")),
        )

    @staticmethod
    def _to_optional_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_optional_str_list(value: Any) -> list[str] | None:
        if not isinstance(value, list):
            return None
        return [str(item) for item in value if item is not None]

    @staticmethod
    def _build_group_spec_model(group_spec: Any) -> GroupSpecModel | None:
        if not isinstance(group_spec, dict):
            return None

        return GroupSpecModel(
            id=str(group_spec.get("id") or ""),
            mod_code=group_spec.get("mod_code"),
            fea_code=group_spec.get("fea_code"),
            filename=group_spec.get("filename"),
            storage_key=group_spec.get("storage_key"),
            source_id=group_spec.get("source_id"),
            project_id=group_spec.get("project_id"),
            created_at=UserStoryRepository._neo4j_dt_to_py(group_spec.get("created_at")),
            updated_at=UserStoryRepository._neo4j_dt_to_py(group_spec.get("updated_at")),
        )

    @staticmethod
    def _deserialize_srs_evidence_from_neo4j(
        entries: list[dict[str, Any]] | None,
    ) -> list[SRSEvidenceModel]:
        """Normalize SRSEvidence rows into typed domain models."""
        if not entries:
            return []

        normalized_entries: list[SRSEvidenceModel] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue

            precision_value = entry.get("precision")
            precision = str(precision_value) if precision_value is not None else None

            normalized_entries.append(
                SRSEvidenceModel(
                    id=str(entry.get("id") or ""),
                    module_code=entry.get("module_code"),
                    feature_code=entry.get("feature_code"),
                    user_story_code=entry.get("user_story_code"),
                    user_story_id=entry.get("user_story_id"),
                    group_spec_id=entry.get("group_spec_id"),
                    l2_id=entry.get("l2_id"),
                    file_name=entry.get("file_name"),
                    section_anchor=entry.get("section_anchor"),
                    section_path=UserStoryRepository._to_optional_str_list(
                        entry.get("section_path")
                    ),
                    highlight_type=entry.get("highlight_type"),
                    target_string=entry.get("target_string"),
                    exact_quote=entry.get("exact_quote"),
                    line_number=UserStoryRepository._to_optional_int(entry.get("line_number")),
                    precision=precision,
                    evidence_role=entry.get("evidence_role"),
                    srs_document_type=entry.get("srs_document_type"),
                    ac_ids=UserStoryRepository._to_optional_str_list(entry.get("ac_ids")),
                    trace_id=entry.get("trace_id"),
                    context_snippet=entry.get("context_snippet"),
                    tier=entry.get("tier"),
                    group_spec=UserStoryRepository._build_group_spec_model(entry.get("group_spec")),
                    created_at=UserStoryRepository._neo4j_dt_to_py(entry.get("created_at")),
                    updated_at=UserStoryRepository._neo4j_dt_to_py(entry.get("updated_at")),
                )
            )

        return normalized_entries

    @staticmethod
    def _serialize_json_list_for_neo4j(
        items: list[dict[str, Any]] | list[str] | None,
    ) -> list[str]:
        """Convert a list of nested maps (nfrs, acceptance_criteria, sources, screens, ...) to JSON strings for Neo4j storage."""
        if not items:
            return []

        serialized: list[str] = []
        for item in items:
            if isinstance(item, str):
                serialized.append(item)
            else:
                serialized.append(json.dumps(item, ensure_ascii=False, default=str))
        return serialized

    @staticmethod
    def _serialize_json_field(value: dict[str, Any] | None) -> str | None:
        """Convert a single nested map to a JSON string for Neo4j storage."""
        if not value:
            return None
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def _deserialize_json_field(value: str | dict[str, Any] | None) -> dict[str, Any] | None:
        """Convert a stored JSON string back into a single map."""
        if not value:
            return None
        if isinstance(value, dict):
            return value
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            # A legacy flat-list value (pre-dates the nested-dict shape) lands
            # here too — treated the same as unparseable: no reliable field
            # attribution, so it's dropped rather than raised.
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _deserialize_json_list_from_neo4j(
        items: list[str] | list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        """Convert stored JSON strings back into a list of maps (nfrs, acceptance_criteria, sources, screens, ...)."""
        if not items:
            return []

        deserialized: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, dict):
                deserialized.append(item)
                continue
            if isinstance(item, str):
                try:
                    parsed = json.loads(item)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    deserialized.append(parsed)
        return deserialized

    @staticmethod
    def _deserialize_l2_sources_from_neo4j(items: list[str] | None) -> list[str]:
        """Read back the ``l2_sources`` property: a plain list of granular L2
        source-reference IDs (e.g. "SRS::MFU-001::S5::EVENT-001"), distinct
        from the dict-shaped ``sources`` list handled by
        ``_deserialize_json_list_from_neo4j`` above.
        """
        if not items:
            return []
        return [str(item) for item in items if isinstance(item, str) and item]

    def _execute_list_user_stories_for_project(
        self,
        project_id: str,
        skip: int,
        limit: int,
        status: str | None,
        version: int | None,
        module_id: str | None = None,
        feature_id: str | None = None,
        source_id: str | None = None,
        search_text: str | None = None,
        user_story_code: str | None = None,
        consensus_min: float | None = None,
        consensus_max: float | None = None,
    ) -> tuple[list[UserStoryModel], int]:
        base_where = self._build_project_user_story_base_where(
            project_id=project_id,
            source_id=source_id,
            status=status,
            version=version,
            user_story_code=user_story_code,
            consensus_min=consensus_min,
            consensus_max=consensus_max,
            module_id=module_id,
            feature_id=feature_id,
            search_text=search_text,
        )

        count_cypher = base_where + "RETURN count(DISTINCT r) AS total"
        list_cypher = (
            base_where + "WITH DISTINCT r "
            "RETURN r.id AS user_story_id, "
            "       r.user_story_code AS user_story_code, "
            "       r.title AS title, "
            "       r.description AS description, "
            "       r.consensus AS consensus, "
            "       r.status AS status, "
            "       r.version AS version, "
            "       r.feature_id AS feature_id, "
            "       r.project_id AS project_id, "
            "       r.as_a AS as_a, "
            "       r.i_want_to AS i_want_to, "
            "       r.so_that AS so_that, "
            "       r.acceptance_criteria AS acceptance_criteria, "
            "       r.nfrs AS nfrs, "
            "       r.technical_notes AS technical_notes, "
            "       r.story_points AS story_points, "
            "       r.justification AS justification, "
            "       r.incremental_change_type AS incremental_change_type, "
            "       r.feedback_change_type AS feedback_change_type, "
            "       coalesce(r.sources, r.bboxes, []) AS sources, "
            "       coalesce(r.l2_sources, []) AS l2_sources, "
            "       r.is_current AS is_current, "
            "       coalesce(r.is_jira_synced, false) AS is_jira_synced, "
            "       coalesce(r.is_tap_synced, false) AS is_tap_synced, "
            "       r.del_reason AS del_reason, "
            "       r.deleted_at AS deleted_at, "
            "       r.rfp_flagged_item AS rfp_flagged_item, "
            "       r.created_at AS created_at, "
            "       r.updated_at AS updated_at "
            "ORDER BY r.user_story_code ASC "
            "SKIP $skip LIMIT $limit"
        )
        params = self._build_project_user_story_params(
            project_id=project_id,
            skip=skip,
            limit=limit,
            source_id=source_id,
            status=status,
            version=version,
            user_story_code=user_story_code,
            consensus_min=consensus_min,
            consensus_max=consensus_max,
            module_id=module_id,
            feature_id=feature_id,
            search_text=search_text,
        )

        with self._driver.session() as session:
            total_record = session.execute_read(
                lambda tx: tx.run(
                    count_cypher,
                    **{k: v for k, v in params.items() if k not in ("skip", "limit")},
                ).single()
            )
            records = session.execute_read(lambda tx: list(tx.run(list_cypher, **params)))

        total = int(total_record["total"]) if total_record else 0
        return [self._record_to_model(r) for r in records], total

    @staticmethod
    def _build_project_user_story_base_where(
        *,
        project_id: str,
        source_id: str | None,
        status: str | None,
        version: int | None,
        user_story_code: str | None,
        consensus_min: float | None,
        consensus_max: float | None,
        module_id: str | None,
        feature_id: str | None,
        search_text: str | None,
    ) -> str:
        clauses = [
            "MATCH (p:Project {id: $project_id})-[:HAS_MODULE]->(m:Module)-[:HAS_FEATURE]->(ft:Feature)-[:HAS_USER_STORY]->(r:UserStory) ",
            "WHERE r.deleted_at IS NULL ",
        ]
        if source_id:
            clauses.append("AND m.source_id = $source_id ")
        if status:
            clauses.append("AND r.status = $status ")
        if version:
            clauses.append("AND r.version = $version ")
        if user_story_code:
            clauses.append("AND toLower(r.user_story_code) CONTAINS toLower($user_story_code) ")
        if consensus_min is not None:
            clauses.append("AND r.consensus >= $consensus_min ")
        if consensus_max is not None:
            clauses.append("AND r.consensus <= $consensus_max ")
        if module_id:
            clauses.append("AND m.id = $module_id ")
        if feature_id:
            clauses.append("AND (ft.id = $feature_id OR r.feature_id = $feature_id) ")
        if search_text:
            clauses.append(
                "AND ("
                "toLower(r.title) CONTAINS toLower($search_text) "
                "OR toLower(r.user_story_code) CONTAINS toLower($search_text) "
                "OR toLower(coalesce(m.name, '')) CONTAINS toLower($search_text) "
                "OR toLower(coalesce(ft.name, '')) CONTAINS toLower($search_text)"
                ") "
            )
        return "".join(clauses)

    @staticmethod
    def _build_project_user_story_params(
        *,
        project_id: str,
        skip: int,
        limit: int,
        source_id: str | None,
        status: str | None,
        version: int | None,
        user_story_code: str | None,
        consensus_min: float | None,
        consensus_max: float | None,
        module_id: str | None,
        feature_id: str | None,
        search_text: str | None,
    ) -> dict:
        params: dict = {
            "project_id": project_id,
            "skip": skip,
            "limit": limit,
        }
        optional_values = {
            "source_id": source_id,
            "status": status,
            "version": version,
            "user_story_code": user_story_code,
            "consensus_min": consensus_min,
            "consensus_max": consensus_max,
            "module_id": module_id,
            "feature_id": feature_id,
            "search_text": search_text,
        }
        for key, value in optional_values.items():
            if value is not None:
                params[key] = value
        return params

    def _execute_get_user_story_detail(
        self,
        project_id: str,
        user_story_id: str,
        include_deleted: bool = False,
    ) -> UserStoryModel | None:
        """Fetch a single user story scoped to its project via graph traversal.
        The soft-delete filter is the only optional part of the query — it is
        omitted entirely when ``include_deleted`` is True, rather than being
        parameterized, since Cypher cannot bind a predicate.
        """
        deleted_filter = "" if include_deleted else "WHERE r.deleted_at IS NULL "
        user_story_cypher = (
            "MATCH (p:Project {id: $project_id})-[:HAS_MODULE]->(m:Module)-[:HAS_FEATURE]->(ft:Feature)-[:HAS_USER_STORY]->(r:UserStory {id: $user_story_id}) "
            f"{deleted_filter}"
            "WITH r, m.mod_code AS mod_code, ft.mfu_id AS mfu_id "
            "OPTIONAL MATCH (r)-[:HAS_SRS_EVIDENCE]->(e:SRSEvidence) "
            "OPTIONAL MATCH (gs_rel:GroupSpec)-[:HAS_SRS_EVIDENCE]->(e) "
            "OPTIONAL MATCH (gs_id:GroupSpec {id: e.group_spec_id}) "
            "WITH r, mod_code, mfu_id, e, coalesce(gs_rel, gs_id) AS gs "
            "WITH r, mod_code, mfu_id, "
            "     [item IN collect(DISTINCT CASE WHEN e IS NULL THEN NULL ELSE {"
            "         id: e.id, "
            "         module_code: e.module_code, "
            "         feature_code: e.feature_code, "
            "         user_story_code: e.user_story_code, "
            "         user_story_id: e.user_story_id, "
            "         group_spec_id: e.group_spec_id, "
            "         l2_id: e.l2_id, "
            "         file_name: e.file_name, "
            "         section_anchor: e.section_anchor, "
            "         section_path: e.section_path, "
            "         highlight_type: e.highlight_type, "
            "         target_string: e.target_string, "
            "         exact_quote: e.exact_quote, "
            "         line_number: e.line_number, "
            "         precision: e.precision, "
            "         evidence_role: e.evidence_role, "
            "         srs_document_type: e.srs_document_type, "
            "         ac_ids: e.ac_ids, "
            "         trace_id: e.trace_id, "
            "         context_snippet: e.context_snippet, "
            "         tier: e.tier, "
            "         created_at: e.created_at, "
            "         updated_at: e.updated_at, "
            "         group_spec: CASE WHEN gs IS NULL THEN NULL ELSE {"
            "             id: gs.id, "
            "             mod_code: gs.mod_code, "
            "             fea_code: gs.fea_code, "
            "             filename: gs.filename, "
            "             storage_key: gs.storage_key, "
            "             source_id: gs.source_id, "
            "             project_id: gs.project_id, "
            "             created_at: gs.created_at, "
            "             updated_at: gs.updated_at"
            "         } END"
            "     } END) WHERE item IS NOT NULL] AS srs_evidence "
            "RETURN r.id AS user_story_id, "
            "       r.user_story_code AS user_story_code, "
            "       r.title AS title, "
            "       r.description AS description, "
            "       r.consensus AS consensus, "
            "       r.status AS status, "
            "       r.version AS version, "
            "       r.feature_id AS feature_id, "
            "       r.project_id AS project_id, "
            "       r.as_a AS as_a, "
            "       r.i_want_to AS i_want_to, "
            "       r.so_that AS so_that, "
            "       r.acceptance_criteria AS acceptance_criteria, "
            "       r.nfrs AS nfrs, "
            "       r.technical_notes AS technical_notes, "
            "       r.story_points AS story_points, "
            "       r.justification AS justification, "
            "       r.incremental_change_type AS incremental_change_type, "
            "       r.feedback_change_type AS feedback_change_type, "
            "       coalesce(r.sources, r.bboxes, []) AS sources, "
            "       coalesce(r.l2_sources, []) AS l2_sources, "
            "       r.screens AS screens, "
            "       r.is_current AS is_current, "
            "       coalesce(r.is_jira_synced, false) AS is_jira_synced, "
            "       coalesce(r.is_tap_synced, false) AS is_tap_synced, "
            "       r.del_reason AS del_reason, "
            "       r.deleted_at AS deleted_at, "
            "       r.rfp_flagged_item AS rfp_flagged_item, "
            "       coalesce(r.text_diffs, []) AS text_diffs, "
            "       srs_evidence AS srs_evidence, "
            "       mfu_id AS mfu_id, "
            "       mod_code AS mod_code, "
            "       r.created_at AS created_at, "
            "       r.updated_at AS updated_at"
        )

        with self._driver.session() as session:
            user_story_record = session.execute_read(
                lambda tx: tx.run(
                    user_story_cypher,
                    project_id=project_id,
                    user_story_id=user_story_id,
                ).single()
            )
        if user_story_record is None:
            return None

        return self._record_to_model(user_story_record)

    def _execute_get_project_summary(self, project_id: str) -> dict:
        user_story_cypher = (
            "MATCH (p:Project {id: $project_id})-[:HAS_MODULE]->(:Module)-[:HAS_FEATURE]->(:Feature)-[:HAS_USER_STORY]->(r:UserStory) "
            "WHERE r.deleted_at IS NULL "
            "RETURN count(DISTINCT r) AS total_user_stories, "
            "       count(DISTINCT CASE WHEN r.status = 'ready' THEN r END) AS ready_count, "
            "       count(DISTINCT CASE WHEN r.status = 'needs_edit' THEN r END) AS needs_edit_count, "
            "       count(DISTINCT CASE WHEN r.status = 'failed' THEN r END) AS failed_count, "
            "       count(DISTINCT CASE WHEN r.status = 'approved' THEN r END) AS approved_count"
        )
        module_cypher = (
            "MATCH (p:Project {id: $project_id})-[:HAS_MODULE]->(m:Module) "
            "WHERE m.deleted_at IS NULL "
            "RETURN count(DISTINCT m) AS total_modules"
        )
        feature_cypher = (
            "MATCH (p:Project {id: $project_id})-[:HAS_MODULE]->(:Module)-[:HAS_FEATURE]->(ft:Feature) "
            "WHERE ft.deleted_at IS NULL "
            "RETURN count(DISTINCT ft) AS total_features"
        )
        with self._driver.session() as session:
            user_story_record = session.execute_read(
                lambda tx: tx.run(user_story_cypher, project_id=project_id).single()
            )
            module_record = session.execute_read(
                lambda tx: tx.run(module_cypher, project_id=project_id).single()
            )
            feature_record = session.execute_read(
                lambda tx: tx.run(feature_cypher, project_id=project_id).single()
            )
        return {
            "total_user_stories": int(user_story_record["total_user_stories"])
            if user_story_record
            else 0,
            "ready_count": int(user_story_record["ready_count"]) if user_story_record else 0,
            "needs_edit_count": int(user_story_record["needs_edit_count"])
            if user_story_record
            else 0,
            "failed_count": int(user_story_record["failed_count"]) if user_story_record else 0,
            "approved_count": int(user_story_record["approved_count"]) if user_story_record else 0,
            "total_modules": int(module_record["total_modules"]) if module_record else 0,
            "total_features": int(feature_record["total_features"]) if feature_record else 0,
        }

    def _execute_are_all_user_stories_approved(self, project_id: str) -> bool:
        """Return True only when every user_story in the project has status 'approved'.

        Uses DISTINCT to guard against multi-path traversal duplicates.
        Returns False when the project has no user_stories.
        """
        cypher = (
            "MATCH (p:Project {id: $project_id})-[:HAS_MODULE]->(:Module)-[:HAS_FEATURE]->(:Feature)-[:HAS_USER_STORY]->(r:UserStory) "
            "WHERE r.deleted_at IS NULL "
            "WITH count(DISTINCT r) AS total, "
            "     count(DISTINCT CASE WHEN r.status = 'approved' THEN r END) AS approved "
            "RETURN total > 0 AND total = approved AS are_all_approved"
        )
        with self._driver.session() as session:
            record = session.execute_read(lambda tx: tx.run(cypher, project_id=project_id).single())
        return bool(record["are_all_approved"]) if record else False

    def _execute_count_approved_user_stories(self, project_id: str) -> int:
        """Return how many user_stories in the project have status 'approved'."""
        cypher = (
            "MATCH (p:Project {id: $project_id})-[:HAS_MODULE]->(:Module)-[:HAS_FEATURE]->(:Feature)-[:HAS_USER_STORY]->(r:UserStory) "
            "WHERE r.deleted_at IS NULL AND r.status = 'approved' "
            "RETURN count(DISTINCT r) AS approved"
        )
        with self._driver.session() as session:
            record = session.execute_read(lambda tx: tx.run(cypher, project_id=project_id).single())
        return int(record["approved"]) if record else 0

    def _execute_count_pending_changes_by_ingestion(self, source_ingestion_id: str) -> int:
        cypher = (
            "MATCH (r:UserStory {source_ingestion_id: $source_ingestion_id}) "
            "WHERE r.incremental_change_type IS NOT NULL OR r.feedback_change_type IS NOT NULL "
            "RETURN count(r) AS pending"
        )
        with self._driver.session() as session:
            record = session.execute_read(
                lambda tx: tx.run(cypher, source_ingestion_id=source_ingestion_id).single()
            )
        return int(record["pending"]) if record else 0

    async def list_user_stories_tree_for_project(
        self,
        *,
        project_id: UUID,
        source_ingestion_id: str | None = None,
    ) -> list[dict]:
        """Return the full module → feature → user_story tree for a project.

        Returns a list of module dicts, each containing a ``children`` list of
        feature dicts, each of which contains a ``children`` list of user_story
        leaf dicts.  Empty collections are returned as empty lists — never None.

        When ``source_ingestion_id`` is provided, a Module/Feature/UserStory is
        kept if its own ``source_ingestion_id`` matches (provenance can land at
        any of the three levels — e.g. a feedback-driven regeneration may only
        touch a module's or feature's own fields without bumping a story), OR
        it has at least one surviving descendant. A Module or Feature that
        matches directly but whose children don't is still returned, just with
        an empty/partial ``children`` list — matching doesn't cascade down to
        force-include unrelated descendants.
        """
        return await asyncio.to_thread(
            self._execute_list_user_stories_tree_for_project,
            str(project_id),
            source_ingestion_id,
        )

    def _execute_list_user_stories_tree_for_project(
        self, project_id: str, source_ingestion_id: str | None = None
    ) -> list[dict]:
        """Execute a single batched Cypher query that fetches the full tree.

        Ordering: modules by ``mod_code``, features by ``fea_code``,
        user_stories by ``user_story_code`` — all ascending.
        """
        cypher = (
            "MATCH (:Project {id: $project_id})-[:HAS_MODULE]->(m:Module) "
            "WHERE m.deleted_at IS NULL "
            "OPTIONAL MATCH (m)-[:HAS_FEATURE]->(f:Feature) "
            "OPTIONAL MATCH (f)-[:HAS_USER_STORY]->(r:UserStory) "
            "WHERE $source_ingestion_id IS NULL "
            "   OR m.source_ingestion_id = $source_ingestion_id "
            "   OR f.source_ingestion_id = $source_ingestion_id "
            "   OR r.source_ingestion_id = $source_ingestion_id "
            "WITH m, f, r "
            "ORDER BY toInteger(last(split(m.mod_code, '-'))) ASC, "
            "         toInteger(last(split(f.fea_code, '-'))) ASC, "
            "         toInteger(last(split(r.user_story_code, '-'))) ASC "
            "WITH m, f, "
            "     [x IN collect(CASE WHEN r IS NOT NULL AND r.deleted_at IS NULL AND ( "
            "         $source_ingestion_id IS NULL OR r.source_ingestion_id = $source_ingestion_id "
            "     ) THEN "
            "         {id: r.id, user_story_code: r.user_story_code, name: r.title, status: r.status, "
            "          incremental_change_type: r.incremental_change_type, "
            "          feedback_change_type: r.feedback_change_type, "
            "          is_jira_synced: r.is_jira_synced, is_tap_synced: r.is_tap_synced, "
            "          source_ingestion_id: r.source_ingestion_id} "
            "     END) WHERE x IS NOT NULL] AS matched_user_stories "
            "WHERE f IS NULL "
            "   OR $source_ingestion_id IS NULL "
            "   OR f.source_ingestion_id = $source_ingestion_id "
            "   OR size(matched_user_stories) > 0 "
            "WITH m, "
            "     [x IN collect(CASE WHEN f IS NOT NULL AND f.deleted_at IS NULL AND ( "
            "         $source_ingestion_id IS NULL "
            "         OR f.source_ingestion_id = $source_ingestion_id "
            "         OR size(matched_user_stories) > 0 "
            "     ) THEN "
            "         {id: f.id, fea_code: f.fea_code, name: f.name, description: f.description, "
            "          incremental_change_type: f.incremental_change_type, "
            "          feedback_change_type: f.feedback_change_type, "
            "          source_ingestion_id: f.source_ingestion_id, "
            "          children: matched_user_stories} "
            "     END) WHERE x IS NOT NULL] AS matched_feats "
            "WHERE $source_ingestion_id IS NULL "
            "   OR m.source_ingestion_id = $source_ingestion_id "
            "   OR size(matched_feats) > 0 "
            "RETURN m.id AS module_id, "
            "       m.mod_code AS mod_code, "
            "       m.name AS module_name, "
            "       m.description AS module_description, "
            "       m.incremental_change_type AS incremental_change_type, "
            "       m.feedback_change_type AS feedback_change_type, "
            "       m.source_ingestion_id AS module_source_ingestion_id, "
            "       matched_feats AS children "
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
            {
                "id": str(record["module_id"]),
                "mod_code": record["mod_code"],
                "name": str(record["module_name"]),
                "description": record.get("module_description"),
                "incremental_change_type": record.get("incremental_change_type"),
                "feedback_change_type": record.get("feedback_change_type"),
                "source_ingestion_id": record.get("module_source_ingestion_id"),
                "children": sorted(
                    [
                        {
                            "id": str(f["id"]),
                            "fea_code": f.get("fea_code"),
                            "name": str(f["name"]),
                            "description": f.get("description"),
                            "incremental_change_type": f.get("incremental_change_type"),
                            "feedback_change_type": f.get("feedback_change_type"),
                            "source_ingestion_id": f.get("source_ingestion_id"),
                            "children": sorted(
                                [
                                    {
                                        "id": str(r["id"]),
                                        "user_story_code": str(r["user_story_code"]),
                                        "name": str(r["name"]),
                                        "status": r.get("status"),
                                        "incremental_change_type": r.get("incremental_change_type"),
                                        "feedback_change_type": r.get("feedback_change_type"),
                                        "is_jira_synced": bool(r.get("is_jira_synced") or False),
                                        "is_tap_synced": bool(r.get("is_tap_synced") or False),
                                        "source_ingestion_id": r.get("source_ingestion_id"),
                                    }
                                    for r in (f.get("children") or [])
                                    if r is not None
                                ],
                                key=lambda r: UserStoryRepository._numeric_code_key(
                                    r["user_story_code"]
                                ),
                            ),
                        }
                        for f in (record["children"] or [])
                        if f is not None
                    ],
                    key=lambda f: UserStoryRepository._numeric_code_key(f["fea_code"]),
                ),
            }
            for record in records
        ]
        modules.sort(key=lambda m: UserStoryRepository._numeric_code_key(m["mod_code"]))
        return modules

    async def list_sync_candidate_tree_for_project(
        self,
        *,
        project_id: UUID,
        sync_target: str,
    ) -> list[dict]:
        """Return the module → feature → user_story tree pruned to approved,
        not-yet-synced-to-``sync_target`` stories (``sync_target`` is ``"jira"``
        or ``"tap"``).

        Unlike ``list_user_stories_tree_for_project``, an empty module/feature
        (no surviving story beneath it) is dropped entirely rather than kept
        with empty ``children`` — there is nothing under it to sync.
        """
        return await asyncio.to_thread(
            self._execute_list_sync_candidate_tree_for_project,
            str(project_id),
            sync_target,
        )

    def _execute_list_sync_candidate_tree_for_project(
        self, project_id: str, sync_target: str
    ) -> list[dict]:
        cypher = (
            "MATCH (:Project {id: $project_id})-[:HAS_MODULE]->(m:Module) "
            "OPTIONAL MATCH (m)-[:HAS_FEATURE]->(f:Feature) "
            "OPTIONAL MATCH (f)-[:HAS_USER_STORY]->(r:UserStory) "
            "WHERE r IS NULL OR ( "
            "    r.status IN ['approved', 'deleted'] "
            "    AND coalesce(r.is_current, true) = true "
            "    AND ( "
            "        ($sync_target = 'jira' AND coalesce(r.is_jira_synced, false) = false) OR "
            "        ($sync_target = 'tap'  AND coalesce(r.is_tap_synced,  false) = false) "
            "    ) "
            ") "
            "WITH m, f, r "
            "ORDER BY toInteger(last(split(m.mod_code, '-'))) ASC, "
            "         toInteger(last(split(f.fea_code, '-'))) ASC, "
            "         toInteger(last(split(r.user_story_code, '-'))) ASC "
            "WITH m, f, "
            "     [x IN collect(CASE WHEN r IS NOT NULL AND ( "
            "         r.status IN ['approved', 'deleted'] "
            "         AND coalesce(r.is_current, true) = true "
            "         AND ( "
            "             ($sync_target = 'jira' AND coalesce(r.is_jira_synced, false) = false) OR "
            "             ($sync_target = 'tap'  AND coalesce(r.is_tap_synced,  false) = false) "
            "         ) "
            "     ) THEN "
            "         {id: r.id, user_story_code: r.user_story_code, name: r.title, status: r.status, "
            "          version: r.version, "
            "          deleted_at: CASE WHEN r.deleted_at IS NULL THEN null ELSE toString(r.deleted_at) END, "
            "          incremental_change_type: r.incremental_change_type, "
            "          feedback_change_type: r.feedback_change_type, "
            "          is_jira_synced: r.is_jira_synced, is_tap_synced: r.is_tap_synced, "
            "          source_ingestion_id: r.source_ingestion_id} "
            "     END) WHERE x IS NOT NULL] AS matched_user_stories "
            "WHERE f IS NULL OR size(matched_user_stories) > 0 "
            "WITH m, "
            "     [x IN collect(CASE WHEN f IS NOT NULL AND size(matched_user_stories) > 0 THEN "
            "         {id: f.id, fea_code: f.fea_code, name: f.name, description: f.description, "
            "          incremental_change_type: f.incremental_change_type, "
            "          feedback_change_type: f.feedback_change_type, "
            "          source_ingestion_id: f.source_ingestion_id, "
            "          children: matched_user_stories} "
            "     END) WHERE x IS NOT NULL] AS matched_feats "
            "WHERE size(matched_feats) > 0 "
            "RETURN m.id AS module_id, "
            "       m.mod_code AS mod_code, "
            "       m.name AS module_name, "
            "       m.description AS module_description, "
            "       m.incremental_change_type AS incremental_change_type, "
            "       m.feedback_change_type AS feedback_change_type, "
            "       m.source_ingestion_id AS module_source_ingestion_id, "
            "       matched_feats AS children "
            "ORDER BY toInteger(last(split(m.mod_code, '-'))) ASC"
        )
        with self._driver.session() as session:
            records = session.execute_read(
                lambda tx: list(
                    tx.run(
                        cypher,
                        project_id=project_id,
                        sync_target=sync_target,
                    )
                )
            )

        modules = [
            {
                "id": str(record["module_id"]),
                "mod_code": record["mod_code"],
                "name": str(record["module_name"]),
                "description": record.get("module_description"),
                "incremental_change_type": record.get("incremental_change_type"),
                "feedback_change_type": record.get("feedback_change_type"),
                "source_ingestion_id": record.get("module_source_ingestion_id"),
                "children": sorted(
                    [
                        {
                            "id": str(f["id"]),
                            "fea_code": f.get("fea_code"),
                            "name": str(f["name"]),
                            "description": f.get("description"),
                            "incremental_change_type": f.get("incremental_change_type"),
                            "feedback_change_type": f.get("feedback_change_type"),
                            "source_ingestion_id": f.get("source_ingestion_id"),
                            "children": sorted(
                                [
                                    {
                                        "id": str(r["id"]),
                                        "user_story_code": str(r["user_story_code"]),
                                        "name": str(r["name"]),
                                        "status": r.get("status"),
                                        "version": r.get("version"),
                                        "deleted_at": r.get("deleted_at"),
                                        "incremental_change_type": r.get("incremental_change_type"),
                                        "feedback_change_type": r.get("feedback_change_type"),
                                        "is_jira_synced": bool(r.get("is_jira_synced") or False),
                                        "is_tap_synced": bool(r.get("is_tap_synced") or False),
                                        "source_ingestion_id": r.get("source_ingestion_id"),
                                    }
                                    for r in (f.get("children") or [])
                                    if r is not None
                                ],
                                key=lambda r: UserStoryRepository._numeric_code_key(
                                    r["user_story_code"]
                                ),
                            ),
                        }
                        for f in (record["children"] or [])
                        if f is not None
                    ],
                    key=lambda f: UserStoryRepository._numeric_code_key(f["fea_code"]),
                ),
            }
            for record in records
        ]
        modules.sort(key=lambda m: UserStoryRepository._numeric_code_key(m["mod_code"]))
        return modules

    async def list_full_backlog_tree_for_project(
        self,
        *,
        project_id: UUID,
    ) -> list[dict]:
        """Return the full module → feature → user_story tree with complete field detail.

        Unlike ``list_user_stories_tree_for_project`` (id/code/name/status only),
        every node here carries its full content fields — used as the "current
        state" baseline that incremental proposals (adds/updates/deletes) are
        merged against.
        """
        return await asyncio.to_thread(
            self._execute_list_full_backlog_tree_for_project,
            str(project_id),
        )

    def _execute_list_full_backlog_tree_for_project(self, project_id: str) -> list[dict]:
        cypher = (
            "MATCH (:Project {id: $project_id})-[:HAS_MODULE]->(m:Module) "
            "WHERE m.deleted_at IS NULL "
            "OPTIONAL MATCH (m)-[:HAS_FEATURE]->(f:Feature) "
            "OPTIONAL MATCH (f)-[:HAS_USER_STORY]->(r:UserStory) "
            "WITH m, f, r "
            "ORDER BY toInteger(last(split(m.mod_code, '-'))) ASC, "
            "         toInteger(last(split(f.fea_code, '-'))) ASC, "
            "         toInteger(last(split(r.user_story_code, '-'))) ASC "
            "WITH m, f, "
            "     collect(CASE WHEN r IS NOT NULL AND r.deleted_at IS NULL THEN "
            "         {id: r.id, user_story_code: r.user_story_code, title: r.title, "
            "          status: r.status, as_a: r.as_a, i_want_to: r.i_want_to, so_that: r.so_that, "
            "          acceptance_criteria: r.acceptance_criteria, nfrs: r.nfrs, "
            "          story_points: r.story_points, technical_notes: r.technical_notes, "
            "          sources: r.sources, l2_sources: r.l2_sources} "
            "     END) AS user_stories "
            "WITH m, "
            "     collect(CASE WHEN f IS NOT NULL AND f.deleted_at IS NULL THEN "
            "         {id: f.id, fea_code: f.fea_code, name: f.name, description: f.description, "
            "          functions: f.functions, sources: f.sources, l2_sources: f.l2_sources, "
            "          children: [x IN user_stories WHERE x IS NOT NULL]} "
            "     END) AS feats "
            "RETURN m.id AS module_id, m.mod_code AS mod_code, m.name AS module_name, "
            "       m.description AS module_description, "
            "       [x IN feats WHERE x IS NOT NULL] AS children "
            "ORDER BY toInteger(last(split(m.mod_code, '-'))) ASC"
        )
        with self._driver.session() as session:
            records = session.execute_read(lambda tx: list(tx.run(cypher, project_id=project_id)))

        modules = [
            {
                "id": str(record["module_id"]),
                "mod_code": record["mod_code"],
                "name": str(record["module_name"]),
                "description": record.get("module_description"),
                "children": sorted(
                    [
                        {
                            "id": str(f["id"]),
                            "fea_code": f.get("fea_code"),
                            "name": str(f["name"]),
                            "description": f.get("description"),
                            "functions": UserStoryRepository._parse_json_blob(f.get("functions")),
                            "sources": UserStoryRepository._parse_json_blob(f.get("sources")),
                            "l2_sources": UserStoryRepository._parse_json_blob(f.get("l2_sources")),
                            "children": sorted(
                                [
                                    {
                                        "id": str(r["id"]),
                                        "user_story_code": str(r["user_story_code"]),
                                        "title": str(r["title"]),
                                        "status": r.get("status"),
                                        "as_a": r.get("as_a"),
                                        "i_want_to": r.get("i_want_to"),
                                        "so_that": r.get("so_that"),
                                        "acceptance_criteria": UserStoryRepository._deserialize_json_list_from_neo4j(
                                            r.get("acceptance_criteria")
                                        ),
                                        "nfrs": UserStoryRepository._deserialize_json_list_from_neo4j(
                                            r.get("nfrs")
                                        ),
                                        "story_points": r.get("story_points"),
                                        "technical_notes": r.get("technical_notes"),
                                        "sources": UserStoryRepository._deserialize_json_list_from_neo4j(
                                            r.get("sources")
                                        ),
                                        "l2_sources": UserStoryRepository._deserialize_l2_sources_from_neo4j(
                                            r.get("l2_sources")
                                        ),
                                    }
                                    for r in (f.get("children") or [])
                                    if r is not None
                                ],
                                key=lambda r: UserStoryRepository._numeric_code_key(
                                    r["user_story_code"]
                                ),
                            ),
                        }
                        for f in (record["children"] or [])
                        if f is not None
                    ],
                    key=lambda f: UserStoryRepository._numeric_code_key(f["fea_code"]),
                ),
            }
            for record in records
        ]
        modules.sort(key=lambda m: UserStoryRepository._numeric_code_key(m["mod_code"]))
        return modules

    @staticmethod
    def _parse_json_blob(value: str | None) -> list[dict]:
        """Deserialise a single JSON-string blob (Feature.functions/.sources convention)."""
        if not value:
            return []
        try:
            items = json.loads(value)
            return items if isinstance(items, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    @staticmethod
    def _numeric_code_key(code: str | None) -> tuple[int, str]:
        """Return a sort key that orders codes numerically by their trailing integer."""
        if not code:
            return (2**31, "")
        nums = re.findall(r"\d+", code)
        return (int(nums[-1]), code) if nums else (2**31, code)
