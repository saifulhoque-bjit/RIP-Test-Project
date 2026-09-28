"""Neo4j driver singleton."""

from __future__ import annotations

from urllib.parse import urlparse

from neo4j import Driver, GraphDatabase

from app.core.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

_driver: Driver | None = None


def _build_candidate_uris(configured_uri: str) -> list[str]:
    candidates = [configured_uri]
    parsed = urlparse(configured_uri)
    hostname = parsed.hostname

    if hostname == "neo4j":
        candidates.append(configured_uri.replace("neo4j", "localhost", 1))
    elif hostname in {"localhost", "127.0.0.1"}:
        candidates.append(configured_uri.replace(hostname, "neo4j", 1))

    # Preserve order and remove duplicates.
    return list(dict.fromkeys(candidates))


def get_neo4j_driver() -> Driver:
    global _driver
    if _driver is None:
        last_error: Exception | None = None
        for uri in _build_candidate_uris(settings.NEO4J_URI):
            try:
                candidate_driver = GraphDatabase.driver(
                    uri,
                    auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
                    max_connection_pool_size=50,
                    notifications_disabled_categories=["UNRECOGNIZED"],
                )
                candidate_driver.verify_connectivity()
                _driver = candidate_driver
                if uri != settings.NEO4J_URI:
                    logger.warning(
                        "Configured NEO4J_URI=%s was unreachable; using fallback=%s",
                        settings.NEO4J_URI,
                        uri,
                    )
                logger.info("Neo4j driver initialised")
                break
            except Exception as exc:  # pragma: no cover - environment dependent
                last_error = exc
                logger.warning("Neo4j connectivity check failed for %s: %s", uri, exc)

        if _driver is None and last_error is not None:
            raise last_error
    return _driver


def create_neo4j_indexes() -> None:
    """Ensure relation-safe constraints and performance indexes exist in Neo4j.

    Graph schema (traversal-only — no direct Source→UserStory or Module→UserStory edges):
    - (:Project)-[:HAS_SOURCE]->(:Source)
    - (:Source)-[:HAS_FRAGMENT]->(:Fragment)
    - (:Project)-[:HAS_MODULE]->(:Module)
    - (:Module)-[:HAS_FEATURE]->(:Feature)
    - (:Feature)-[:HAS_USER_STORY]->(:UserStory)

    Uniqueness constraints guarantee clean MERGE endpoints for every node label.
    Performance indexes cover the property filters applied in list/count queries.
    """
    # Legacy plain indexes on id fields can block equivalent uniqueness constraints.
    legacy_drop_statements = [
        "DROP INDEX req_id IF EXISTS",
        "DROP INDEX src_id IF EXISTS",
        "DROP INDEX frag_id IF EXISTS",
        "DROP INDEX module_id IF EXISTS",
        "DROP INDEX feature_id IF EXISTS",
        # These two indexed Module.source_id and Feature.module_id for the old
        # property-based lookup model.  In the traversal-only model all Module and
        # Feature nodes are reached via edges (:Project)-[:HAS_MODULE] and
        # (:Module)-[:HAS_FEATURE], so the indexes are unused and waste write budget.
        "DROP INDEX module_source IF EXISTS",
        "DROP INDEX feature_module IF EXISTS",
        # UserStory node used to store source_id/module_id as properties; those
        # properties are now removed in favour of graph traversal.
        "DROP INDEX req_source IF EXISTS",
        "DROP INDEX req_module IF EXISTS",
        "DROP INDEX req_status IF EXISTS",
        "DROP INDEX req_version IF EXISTS",
        "DROP INDEX req_feature IF EXISTS",
        "DROP INDEX req_consensus IF EXISTS",
    ]
    # Uniqueness constraints — one per node label that participates in the schema.
    constraint_statements = [
        "CREATE CONSTRAINT project_id_unique IF NOT EXISTS FOR (p:Project)     REQUIRE p.id IS UNIQUE",
        "CREATE CONSTRAINT src_id_unique     IF NOT EXISTS FOR (s:Source)      REQUIRE s.id IS UNIQUE",
        "CREATE CONSTRAINT frag_id_unique    IF NOT EXISTS FOR (f:Fragment)    REQUIRE f.id IS UNIQUE",
        "CREATE CONSTRAINT module_id_unique  IF NOT EXISTS FOR (m:Module)      REQUIRE m.id IS UNIQUE",
        "CREATE CONSTRAINT feature_id_unique IF NOT EXISTS FOR (ft:Feature)    REQUIRE ft.id IS UNIQUE",
        "CREATE CONSTRAINT user_story_id_unique     IF NOT EXISTS FOR (r:UserStory)   REQUIRE r.id IS UNIQUE",
    ]
    # Performance indexes aligned with actual query filter patterns.
    #
    # Source / Fragment side:
    #   (:Source {id})  — covered by src_id_unique constraint
    #   (:Fragment {id}) — covered by frag_id_unique constraint
    #   Fragment.source_id — still useful for direct fragment queries in FragmentRepository
    #
    # UserStory side (all filters applied in list / count queries):
    #   r.status     — AND r.status = $status
    #   r.version    — AND r.version = $version
    #   r.feature_id — AND r.feature_id = $feature_id
    #   r.consensus  — AND r.consensus >= / <= $consensus_min / $consensus_max
    #   (source_id and module_id filtering is done via graph traversal, not node properties)
    #
    # Source side:
    #   s.project_id — project-scoped source lookups in SourceRepository
    index_statements = [
        # Fragment node — direct-lookup support in FragmentRepository
        "CREATE INDEX frag_source    IF NOT EXISTS FOR (f:Fragment)    ON (f.source_id)",
        # UserStory node — every query-time filter gets its own index
        "CREATE INDEX user_story_status     IF NOT EXISTS FOR (r:UserStory) ON (r.status)",
        "CREATE INDEX user_story_version    IF NOT EXISTS FOR (r:UserStory) ON (r.version)",
        "CREATE INDEX user_story_feature    IF NOT EXISTS FOR (r:UserStory) ON (r.feature_id)",
        "CREATE INDEX user_story_consensus  IF NOT EXISTS FOR (r:UserStory) ON (r.consensus)",
        # Source node — project-scoped source lookups
        "CREATE INDEX src_project    IF NOT EXISTS FOR (s:Source)      ON (s.project_id)",
    ]
    driver = get_neo4j_driver()
    with driver.session() as session:
        for stmt in legacy_drop_statements:
            try:
                session.run(stmt)
            except Exception as exc:  # pragma: no cover
                logger.warning("Neo4j legacy index drop skipped: %s — %s", stmt, exc)
        for stmt in constraint_statements:
            try:
                session.run(stmt)
            except Exception as exc:  # pragma: no cover
                logger.warning("Neo4j constraint creation skipped: %s — %s", stmt, exc)
        for stmt in index_statements:
            try:
                session.run(stmt)
            except Exception as exc:  # pragma: no cover
                logger.warning("Neo4j index creation skipped: %s — %s", stmt, exc)


def close_neo4j_driver() -> None:
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None
        logger.info("Neo4j driver closed")


def reset_driver_after_fork() -> None:
    """Drop the reference to a driver inherited from the pre-fork parent process.

    Must NOT call ``driver.close()`` here — after ``fork()`` the underlying
    socket is shared with the parent process, and closing it would tear down
    the parent's live connection too. Simply forgetting the reference is
    enough: this child's next ``get_neo4j_driver()`` call lazily opens its own
    independent connection. Call this from a ``worker_process_init`` handler.
    """
    global _driver
    _driver = None
