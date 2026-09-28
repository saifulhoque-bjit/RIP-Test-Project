"""Neo4j repository for Project graph operations."""

from __future__ import annotations

from typing import Any, NamedTuple
from uuid import UUID

from neo4j import Driver

from app.models.neo4j.project_model import ProjectNode
from app.utils.logger import get_logger

logger = get_logger(__name__)


class UserStoryCounts(NamedTuple):
    """User story totals and external-sync counts for a single project."""

    total: int
    approved: int
    jira_synced: int = 0
    tap_synced: int = 0
    pending_jira_sync: int = 0
    pending_tap_sync: int = 0


class ProjectProgressFlags(NamedTuple):
    """Whether a project has any live Module/Feature and/or UserStory node."""

    has_module_feature: bool
    has_user_story: bool


class ProjectRepository:
    """Graph operations for the Project node in Neo4j."""

    def __init__(self, driver: Driver) -> None:
        self._driver = driver

    # ── Upsert ─────────────────────────────────────────────────────────────

    def upsert_project_node(self, node: ProjectNode) -> None:
        """Create or update a :Project node in Neo4j.

        Accepts a :class:`~app.models.neo4j.project_model.ProjectNode`
        so callers work with a typed model rather than loose keyword args.

        Uses MERGE so the operation is idempotent — safe to call on every
        project creation or update without duplicating nodes.
        ``created_at`` is set only on first creation (via ``coalesce``);
        ``updated_at`` is refreshed on every call.
        """
        cypher = (
            "MERGE (p:Project {id: $project_id}) "
            "SET p.name = $project_name, "
            "    p.status = $project_status, "
            "    p.created_at = coalesce(p.created_at, datetime()), "
            "    p.updated_at = datetime() "
            "RETURN p.id AS project_id"
        )
        with self._driver.session() as session:
            session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    project_id=str(node.id),
                    project_name=node.name,
                    project_status=node.status,
                ).single()
            )
        logger.debug("Neo4j upserted Project node: id=%s", node.id)

    # ── Delete ─────────────────────────────────────────────────────────────

    # Each statement is scoped independently to ``(:Project {id: $project_id})``
    # rather than combined into one query with several parallel OPTIONAL MATCH
    # branches — combining unrelated branches (e.g. the Source/Fragment chain
    # and the Module/Feature/UserStory chain) in a single WITH would compute
    # their cartesian product before collect(DISTINCT ...) dedups it, which
    # gets expensive fast on a large project. Run together in ONE transaction
    # so the whole cleanup is atomic; order matters — children must be
    # deleted (or unlinked-and-collected) before the ancestor they hang off
    # is removed, and the Project node itself must be deleted last.
    _DELETE_PROJECT_GRAPH_STATEMENTS: tuple[str, ...] = (
        # 1. UserStory + its version snapshots (must run before Feature is deleted)
        "MATCH (:Project {id: $project_id})-[:HAS_MODULE]->(:Module)"
        "-[:HAS_FEATURE]->(:Feature)-[:HAS_USER_STORY]->(r:UserStory) "
        "OPTIONAL MATCH (r)-[:HAS_VERSION]->(rv:UserStoryVersion) "
        "WITH collect(DISTINCT r) AS user_stories, collect(DISTINCT rv) AS user_story_versions "
        "FOREACH (n IN user_story_versions | DETACH DELETE n) "
        "FOREACH (n IN user_stories | DETACH DELETE n)",
        # 2. Feature + its version snapshots (must run before Module is deleted)
        "MATCH (:Project {id: $project_id})-[:HAS_MODULE]->(:Module)-[:HAS_FEATURE]->(ft:Feature) "
        "OPTIONAL MATCH (ft)-[:HAS_VERSION]->(ftv:FeatureVersion) "
        "WITH collect(DISTINCT ft) AS features, collect(DISTINCT ftv) AS feature_versions "
        "FOREACH (n IN feature_versions | DETACH DELETE n) "
        "FOREACH (n IN features | DETACH DELETE n)",
        # 3. Module + its version snapshots (must run before Project is deleted)
        "MATCH (:Project {id: $project_id})-[:HAS_MODULE]->(m:Module) "
        "OPTIONAL MATCH (m)-[:HAS_VERSION]->(mv:ModuleVersion) "
        "WITH collect(DISTINCT m) AS modules, collect(DISTINCT mv) AS module_versions "
        "FOREACH (n IN module_versions | DETACH DELETE n) "
        "FOREACH (n IN modules | DETACH DELETE n)",
        # 4. Source + its fragments
        "MATCH (:Project {id: $project_id})-[:HAS_SOURCE]->(s:Source) "
        "OPTIONAL MATCH (s)-[:HAS_FRAGMENT]->(f:Fragment) "
        "WITH collect(DISTINCT s) AS sources, collect(DISTINCT f) AS fragments "
        "FOREACH (n IN fragments | DETACH DELETE n) "
        "FOREACH (n IN sources | DETACH DELETE n)",
        # 5-8. Spec/metadata/evidence nodes still directly linked to Project
        "MATCH (:Project {id: $project_id})-[:HAS_CONFIG_SPEC]->(cs:ConfigSpec) DETACH DELETE cs",
        "MATCH (:Project {id: $project_id})-[:HAS_GROUP_SPEC]->(gs:GroupSpec) DETACH DELETE gs",
        "MATCH (:Project {id: $project_id})-[:HAS_SOURCE_CODE_METADATA]->(scm:SourceCodeMetadata) "
        "DETACH DELETE scm",
        "MATCH (:Project {id: $project_id})-[:HAS_SRS_EVIDENCE]->(se:SRSEvidence) DETACH DELETE se",
        # 9. ProjectMetadata — standalone node, keyed by property (no relationship)
        "MATCH (pm:ProjectMetadata {project_id: $project_id}) DETACH DELETE pm",
        # 10. The Project node itself — always last
        "MATCH (p:Project {id: $project_id}) DETACH DELETE p",
    )

    # Backlog-only counterpart, used by :meth:`delete_backlog_for_project` on
    # the cancel path. Same children-before-parent ordering and the same
    # one-statement-per-level split as above — see that method's docstring for
    # why collapsing these into a single multi-branch query is not an option
    # here.
    #
    # Every level is rooted at ``(:Module {project_id})`` and reached by
    # traversal, exactly as the single-query version was: :Feature is MERGEd
    # with ``project_id`` in its key, but :UserStory is MERGEd by ``id``
    # alone (``user_story_repository.py``), so its ``project_id`` property is
    # not guaranteed on every write path. Matching stories by that property
    # instead of by traversal would silently leave some behind. Each
    # statement counts the rows it deletes so the caller can log them without
    # a second read pass; ``(key, statement)`` pairs so each count lands under
    # the right name regardless of statement order.
    _DELETE_BACKLOG_STATEMENTS: tuple[tuple[str, str], ...] = (
        # 1. UserStory + its version snapshots and SRS evidence
        (
            "deleted_user_stories",
            "MATCH (:Module {project_id: $project_id})"
            "-[:HAS_FEATURE]->(:Feature)-[:HAS_USER_STORY]->(us:UserStory) "
            "OPTIONAL MATCH (us)-[:HAS_VERSION]->(uv:UserStoryVersion) "
            "OPTIONAL MATCH (us)-[:HAS_SRS_EVIDENCE]->(ue:SRSEvidence) "
            "WITH collect(DISTINCT us) AS user_stories, "
            "     collect(DISTINCT uv) AS versions, collect(DISTINCT ue) AS evidence "
            "FOREACH (n IN evidence | DETACH DELETE n) "
            "FOREACH (n IN versions | DETACH DELETE n) "
            "FOREACH (n IN user_stories | DETACH DELETE n) "
            "RETURN size(user_stories) AS deleted",
        ),
        # 2. Feature + its version snapshots and SRS evidence (after UserStory)
        (
            "deleted_features",
            "MATCH (:Module {project_id: $project_id})-[:HAS_FEATURE]->(ft:Feature) "
            "OPTIONAL MATCH (ft)-[:HAS_VERSION]->(fv:FeatureVersion) "
            "OPTIONAL MATCH (ft)-[:HAS_SRS_EVIDENCE]->(fe:SRSEvidence) "
            "WITH collect(DISTINCT ft) AS features, "
            "     collect(DISTINCT fv) AS versions, collect(DISTINCT fe) AS evidence "
            "FOREACH (n IN evidence | DETACH DELETE n) "
            "FOREACH (n IN versions | DETACH DELETE n) "
            "FOREACH (n IN features | DETACH DELETE n) "
            "RETURN size(features) AS deleted",
        ),
        # 3. Module + its version snapshots (after Feature)
        (
            "deleted_modules",
            "MATCH (m:Module {project_id: $project_id}) "
            "OPTIONAL MATCH (m)-[:HAS_VERSION]->(mv:ModuleVersion) "
            "WITH collect(DISTINCT m) AS modules, collect(DISTINCT mv) AS versions "
            "FOREACH (n IN versions | DETACH DELETE n) "
            "FOREACH (n IN modules | DETACH DELETE n) "
            "RETURN size(modules) AS deleted",
        ),
    )

    def delete_project_graph_sync(self, project_id: str) -> int:
        """DETACH DELETE the Project node and every node it owns in Neo4j.

        Blocking (synchronous) — intended to be called from Celery workers
        which already run in a separate thread pool.

        Deletes, in one atomic transaction: UserStory (+ UserStoryVersion),
        Feature (+ FeatureVersion), Module (+ ModuleVersion), Source (+
        Fragment), ConfigSpec, GroupSpec, SourceCodeMetadata, SRSEvidence,
        ProjectMetadata, and finally the Project node itself. Every
        statement is idempotent — safe to retry the whole task if a prior
        attempt failed partway (the transaction is atomic, so a failure
        never leaves a partially-cleaned graph).

        Returns:
            1 if the transaction executed successfully, 0 otherwise.
        """

        def _run_all(tx: Any) -> None:
            for statement in self._DELETE_PROJECT_GRAPH_STATEMENTS:
                tx.run(statement, project_id=project_id)

        with self._driver.session() as session:
            session.execute_write(_run_all)
        logger.info("Neo4j project graph deleted: project_id=%s", project_id)
        return 1

    def get_all_entity_ids_sync(self, project_id: str) -> list[str]:
        """Return every Module/Feature/UserStory id owned by this project.

        Must be called *before* :meth:`delete_project_graph_sync` — once the
        graph is gone these ids are unrecoverable. Used to clean up
        ``tap_sync_mappings`` in Postgres, which has no FK to the project
        and is keyed only by these ids (``rip_entity_id``).
        """
        cypher = (
            "MATCH (:Project {id: $project_id})-[:HAS_MODULE]->(m:Module) "
            "OPTIONAL MATCH (m)-[:HAS_FEATURE]->(ft:Feature) "
            "OPTIONAL MATCH (ft)-[:HAS_USER_STORY]->(r:UserStory) "
            "RETURN collect(DISTINCT m.id) + collect(DISTINCT ft.id) + collect(DISTINCT r.id) AS ids"
        )
        with self._driver.session() as session:
            record = session.execute_read(lambda tx: tx.run(cypher, project_id=project_id).single())
        if record is None:
            return []
        return [entity_id for entity_id in (record["ids"] or []) if entity_id is not None]

    def delete_backlog_for_project(self, project_id: str) -> dict[str, int]:
        """DETACH DELETE every Module/Feature/UserStory (+ Version/SRSEvidence) for a project.

        Blocking (synchronous) — same calling convention as
        :meth:`delete_project_graph_sync`: intended to be called directly
        from Celery worker code already running in a thread-pool worker.

        Narrower than :meth:`delete_project_graph_sync`: this leaves the
        :Project node itself, :Source/:Fragment (document-source nodes,
        unrelated to the source-code pipeline), and
        :GroupSpec/:ConfigSpec/:SourceCodeMetadata (pipeline spec/config
        artifacts, not backlog) untouched — only the derived backlog
        (Module/Feature/UserStory) and their per-entity ModuleVersion/
        FeatureVersion/UserStoryVersion snapshots and SRSEvidence nodes
        (which would otherwise dangle once their parent Feature/UserStory is
        gone) are removed. Rooted at ``(:Module {project_id})`` — the module's
        own property, not a traversal from :Project — with Feature/UserStory
        reached from there by relationship.

        Split into one statement per backlog level — same reason
        :attr:`_DELETE_PROJECT_GRAPH_STATEMENTS` is split, and the reason
        matters more here. This runs on the user-facing cancel path
        (``ProjectTaskService.cancel_request``), and a single query with all
        seven OPTIONAL MATCH branches expands to
        ``features x stories x moduleVersions x featureVersions x
        storyVersions x featureSRS x storySRS`` rows, all materialized before
        ``collect(DISTINCT ...)`` dedups them. Because Module/Feature/
        UserStoryVersion snapshots are CREATE-per-change, that product grows
        superlinearly with a project's edit history and was making cancel
        take minutes. Per level the row count is linear in the nodes deleted.

        Still one transaction, so the rollback stays atomic. Order matters:
        children before the ancestor they hang off, otherwise the DETACH
        DELETE of a parent breaks the traversal that finds its children.

        Returns:
            ``{"deleted_modules", "deleted_features", "deleted_user_stories"}``
            counts, for logging.
        """

        def _run_all(tx: Any) -> dict[str, int]:
            deleted: dict[str, int] = {}
            for key, statement in self._DELETE_BACKLOG_STATEMENTS:
                record = tx.run(statement, project_id=project_id).single()
                deleted[key] = int(record["deleted"]) if record else 0
            return deleted

        with self._driver.session() as session:
            deleted = session.execute_write(_run_all)
        counts = {
            "deleted_modules": deleted.get("deleted_modules", 0),
            "deleted_features": deleted.get("deleted_features", 0),
            "deleted_user_stories": deleted.get("deleted_user_stories", 0),
        }
        logger.info("Neo4j project backlog deleted: project_id=%s counts=%s", project_id, counts)
        return counts

    # ── Aggregation ────────────────────────────────────────────────────────

    def get_user_story_counts(self, project_ids: list[UUID]) -> dict[UUID, UserStoryCounts]:
        """Return total/approved/synced user story counts keyed by project id (single Cypher query).

        Uses UNWIND + OPTIONAL MATCH so projects with zero user stories are
        still included in the result with counts of 0.  The approved and
        external-sync counts are all derived in the same pass via CASE WHEN
        rather than separate queries. A missing ``is_jira_synced``/
        ``is_tap_synced`` property (legacy rows predating those fields)
        evaluates to NULL in the comparison and is correctly excluded from
        the count, same as an explicit ``false``. Failures bubble up to the
        caller (service layer) which applies best-effort handling.
        """
        if not project_ids:
            return {}
        cypher = (
            "UNWIND $project_ids AS pid "
            "OPTIONAL MATCH (:Project {id: pid})-[:HAS_MODULE]->(:Module)"
            "-[:HAS_FEATURE]->(:Feature)-[:HAS_USER_STORY]->(r:UserStory) "
            "RETURN pid, count(CASE WHEN r IS NULL OR r.deleted_at IS NULL THEN r END) AS req_count, "
            "count(CASE WHEN r.deleted_at IS NULL AND r.status = 'approved' THEN r END) AS approved_count, "
            "count(CASE WHEN r.deleted_at IS NULL AND r.is_jira_synced = true THEN r END) AS jira_synced_count, "
            "count(CASE WHEN r.deleted_at IS NULL AND r.is_tap_synced = true THEN r END) AS tap_synced_count, "
            "count(CASE WHEN r.status IN ['approved', 'deleted'] "
            "AND coalesce(r.is_current, true) = true "
            "AND coalesce(r.is_jira_synced, false) = false THEN r END) AS pending_jira_sync_count, "
            "count(CASE WHEN r.status IN ['approved', 'deleted'] "
            "AND coalesce(r.is_current, true) = true "
            "AND coalesce(r.is_tap_synced, false) = false THEN r END) AS pending_tap_sync_count"
        )
        counts: dict[UUID, UserStoryCounts] = dict.fromkeys(
            project_ids, UserStoryCounts(total=0, approved=0)
        )
        with self._driver.session() as session:
            records = session.execute_read(
                lambda tx: list(tx.run(cypher, project_ids=[str(p) for p in project_ids]))
            )
        for record in records:
            try:
                counts[UUID(record["pid"])] = UserStoryCounts(
                    total=int(record["req_count"] or 0),
                    approved=int(record["approved_count"] or 0),
                    jira_synced=int(record["jira_synced_count"] or 0),
                    tap_synced=int(record["tap_synced_count"] or 0),
                    pending_jira_sync=int(record.get("pending_jira_sync_count") or 0),
                    pending_tap_sync=int(record.get("pending_tap_sync_count") or 0),
                )
            except Exception:
                pass
        return counts

    def get_progress_flags(self, project_ids: list[UUID]) -> dict[UUID, ProjectProgressFlags]:
        """Return, per project id, whether it has any live Module and any live UserStory.

        Powers the ``GET /projects/list`` ``stage`` filter — cheaply
        distinguishes projects that have only reached Module/Feature
        generation from those that have progressed to User Story
        generation, without loading full counts.

        Module nodes carry ``project_id`` directly (see
        :meth:`get_module_feature_story_counts_for_projects`), so the
        Module check filters on that property. UserStory nodes do **not**
        reliably carry ``project_id`` (it's only set on some write paths —
        see :attr:`_DELETE_BACKLOG_STATEMENTS`'s comment), so the UserStory
        check instead traverses from a project-scoped Module through
        ``HAS_FEATURE``/``HAS_USER_STORY``, matching every other UserStory
        lookup in this codebase. Missing project ids resolve to
        ``(False, False)`` rather than being absent from the result.
        """
        if not project_ids:
            return {}
        cypher = (
            "UNWIND $project_ids AS pid "
            "OPTIONAL MATCH (m:Module) WHERE m.project_id = pid AND m.deleted_at IS NULL "
            "WITH pid, count(DISTINCT m) AS module_count "
            "OPTIONAL MATCH (m2:Module)-[:HAS_FEATURE]->(:Feature)-[:HAS_USER_STORY]->(s:UserStory) "
            "WHERE m2.project_id = pid AND s.deleted_at IS NULL "
            "RETURN pid, module_count, count(DISTINCT s) AS story_count"
        )
        flags: dict[UUID, ProjectProgressFlags] = dict.fromkeys(
            project_ids, ProjectProgressFlags(has_module_feature=False, has_user_story=False)
        )
        with self._driver.session() as session:
            records = session.execute_read(
                lambda tx: list(tx.run(cypher, project_ids=[str(p) for p in project_ids]))
            )
        for record in records:
            try:
                flags[UUID(record["pid"])] = ProjectProgressFlags(
                    has_module_feature=int(record["module_count"] or 0) > 0,
                    has_user_story=int(record["story_count"] or 0) > 0,
                )
            except Exception:
                pass
        return flags

    def get_global_module_feature_story_counts(self) -> dict:
        """Count all modules, features, and user stories across every project.

        Uses sequential OPTIONAL MATCH + WITH aggregations to avoid cartesian
        products; each intermediate WITH collapses results to a single row.
        Unscoped — used only for a super_admin's platform-wide dashboard
        view; see :meth:`get_module_feature_story_counts_for_projects` for
        the tenant/member-scoped equivalent.

        Also returns ``approved_stories`` and, scoped to *approved* stories
        only, ``pending_jira_sync_stories``/``pending_tap_sync_stories`` —
        approved stories not yet synced to Jira/TAP respectively (derived
        from the same ``UserStory`` match via ``CASE WHEN``, same pattern as
        :meth:`get_user_story_counts`). ``coalesce(..., false)`` treats a
        missing sync-flag property (legacy rows) as "not synced" rather than
        silently excluding it from the count.
        """
        cypher = (
            "OPTIONAL MATCH (m:Module) WHERE m.deleted_at IS NULL "
            "WITH count(DISTINCT m) AS total_modules "
            "OPTIONAL MATCH (f:Feature) WHERE f.deleted_at IS NULL "
            "WITH total_modules, count(DISTINCT f) AS total_features "
            "OPTIONAL MATCH (s:UserStory) WHERE s.deleted_at IS NULL "
            "RETURN total_modules, total_features, count(DISTINCT s) AS total_stories, "
            "count(CASE WHEN s.status = 'approved' THEN s END) AS approved_stories, "
            "count(CASE WHEN s.status = 'approved' AND coalesce(s.is_jira_synced, false) = false THEN s END) AS pending_jira_sync_stories, "
            "count(CASE WHEN s.status = 'approved' AND coalesce(s.is_tap_synced, false) = false THEN s END) AS pending_tap_sync_stories"
        )
        with self._driver.session() as session:
            record = session.execute_read(lambda tx: tx.run(cypher).single())
        if record is None:
            return {
                "total_modules": 0,
                "total_features": 0,
                "total_stories": 0,
                "approved_stories": 0,
                "pending_jira_sync_stories": 0,
                "pending_tap_sync_stories": 0,
            }
        return {
            "total_modules": int(record["total_modules"] or 0),
            "total_features": int(record["total_features"] or 0),
            "total_stories": int(record["total_stories"] or 0),
            "approved_stories": int(record["approved_stories"] or 0),
            "pending_jira_sync_stories": int(record["pending_jira_sync_stories"] or 0),
            "pending_tap_sync_stories": int(record["pending_tap_sync_stories"] or 0),
        }

    def get_module_feature_story_counts_for_projects(self, project_ids: list[UUID]) -> dict:
        """Count modules, features, and user stories restricted to *project_ids*.

        Used to scope a Client Admin's (their tenant's projects) or a
        Member's (their owned + assigned projects) dashboard view
        — Module nodes carry ``project_id`` directly, so this filters on
        that property rather than traversing from ``Project`` nodes.
        Returns all-zero counts for an empty ``project_ids`` list without
        querying (mirrors ``get_user_story_counts``'s empty-input short-circuit).

        Also returns ``approved_stories`` and, scoped to *approved* stories
        only, ``pending_jira_sync_stories``/``pending_tap_sync_stories`` —
        see :meth:`get_global_module_feature_story_counts`.
        """
        if not project_ids:
            return {
                "total_modules": 0,
                "total_features": 0,
                "total_stories": 0,
                "approved_stories": 0,
                "pending_jira_sync_stories": 0,
                "pending_tap_sync_stories": 0,
            }
        cypher = (
            "OPTIONAL MATCH (m:Module) WHERE m.project_id IN $project_ids AND m.deleted_at IS NULL "
            "WITH count(DISTINCT m) AS total_modules "
            "OPTIONAL MATCH (f:Feature) WHERE f.project_id IN $project_ids AND f.deleted_at IS NULL "
            "WITH total_modules, count(DISTINCT f) AS total_features "
            "OPTIONAL MATCH (s:UserStory) WHERE s.project_id IN $project_ids AND s.deleted_at IS NULL "
            "RETURN total_modules, total_features, count(DISTINCT s) AS total_stories, "
            "count(CASE WHEN s.status = 'approved' THEN s END) AS approved_stories, "
            "count(CASE WHEN s.status = 'approved' AND coalesce(s.is_jira_synced, false) = false THEN s END) AS pending_jira_sync_stories, "
            "count(CASE WHEN s.status = 'approved' AND coalesce(s.is_tap_synced, false) = false THEN s END) AS pending_tap_sync_stories"
        )
        with self._driver.session() as session:
            record = session.execute_read(
                lambda tx: tx.run(cypher, project_ids=[str(p) for p in project_ids]).single()
            )
        if record is None:
            return {
                "total_modules": 0,
                "total_features": 0,
                "total_stories": 0,
                "approved_stories": 0,
                "pending_jira_sync_stories": 0,
                "pending_tap_sync_stories": 0,
            }
        return {
            "total_modules": int(record["total_modules"] or 0),
            "total_features": int(record["total_features"] or 0),
            "total_stories": int(record["total_stories"] or 0),
            "approved_stories": int(record["approved_stories"] or 0),
            "pending_jira_sync_stories": int(record["pending_jira_sync_stories"] or 0),
            "pending_tap_sync_stories": int(record["pending_tap_sync_stories"] or 0),
        }
