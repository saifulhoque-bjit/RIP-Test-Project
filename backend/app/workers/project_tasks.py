"""Celery tasks for Project → Neo4j graph synchronisation.

Why a dedicated task file?
──────────────────────────
Direct (in-process) Neo4j calls inside the request lifecycle are
best-effort: if Neo4j is unavailable the failure is silently logged
and the Postgres commit stands.  This creates silent divergence between
the two stores that is invisible until a user queries the graph.

Moving Neo4j writes here gives:
    1. Automatic retry with exponential back-off via Celery.
    2. Observability — failed tasks appear in the Celery result backend /
       Flower and can be replayed manually.
    3. Decoupling — the HTTP request returns as soon as Postgres commits;
       Neo4j sync happens asynchronously without blocking the response.

Task naming convention
──────────────────────
All tasks are prefixed ``tasks.project.`` so they are unambiguous in
monitoring dashboards and can be routed independently.

Retry strategy (mirrors process_source_tasks.py)
────────────────────────────────────────────────
    retry 1:  60 s
    retry 2: 120 s
    retry 3: 240 s
After max_retries the task is moved to the dead-letter queue (Celery
default: kept in the result backend) and an ERROR is logged with full
context so an operator can replay it.

Queue
─────
Both tasks run in the ``neo4j_sync`` queue.  Start a dedicated worker:
    celery -A app.workers.worker worker -Q neo4j_sync -c 2 --hostname neo4j_sync@%h
"""

from __future__ import annotations

from app.core.celery_app import celery_app
from app.core.constants import TASK_MAX_RETRIES, TASK_RETRY_BASE_DELAY_SECONDS
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Upsert ─────────────────────────────────────────────────────────────────


@celery_app.task(
    bind=True,
    name="tasks.project.sync_project_to_neo4j",
    max_retries=TASK_MAX_RETRIES,
    default_retry_delay=TASK_RETRY_BASE_DELAY_SECONDS,
    queue="neo4j_sync",
)
def sync_project_to_neo4j(
    self,
    project_id: str,
    name: str,
    status: str,
) -> dict[str, str]:
    """Create or update the :Project node in Neo4j.

    Called after every successful ``CREATE`` or ``UPDATE`` Postgres commit.
    Uses MERGE so the operation is idempotent — safe to replay on retry.

    Args:
        project_id: UUID string of the project.
        name:       Current project name.
        status:     Current project status value (e.g. "active").

    Returns:
        ``{"status": "ok", "project_id": project_id}`` on success.
    """
    from uuid import UUID

    from app.db.neo4j import get_neo4j_driver
    from app.models.neo4j.project_model import ProjectNode
    from app.repositories.neo4j.project_metadata_repository import ProjectMetadataRepository
    from app.repositories.neo4j.project_repository import (
        ProjectRepository as Neo4jProjectRepository,
    )

    try:
        driver = get_neo4j_driver()
        repo = Neo4jProjectRepository(driver)
        repo.upsert_project_node(ProjectNode(id=UUID(project_id), name=name, status=status))
        ProjectMetadataRepository(driver).upsert(project_id=UUID(project_id))
        logger.info("Neo4j project node synced: project_id=%s", project_id)
        return {"status": "ok", "project_id": project_id}
    except Exception as exc:
        logger.error(
            "Neo4j sync failed (attempt %d/%d): project_id=%s error=%s",
            self.request.retries + 1,
            TASK_MAX_RETRIES + 1,
            project_id,
            exc,
            exc_info=True,
        )
        raise self.retry(
            exc=exc,
            countdown=TASK_RETRY_BASE_DELAY_SECONDS * (2**self.request.retries),
        )


# ── Delete ─────────────────────────────────────────────────────────────────


def _cleanup_tap_sync_mappings(entity_ids: list[str], project_id: str) -> None:
    """Best-effort: purge tap_sync_mappings rows for the deleted project's entities.

    ``tap_sync_mappings`` has no FK to ``projects`` and is keyed only by
    ``rip_entity_id`` (a Neo4j Module/Feature/UserStory id), so it can't be
    cleaned up by a plain ``delete_by_project_id`` like the other tables.
    Never raises — a leftover mapping row is stale data, not a correctness
    issue, and must not fail the graph-delete task that already succeeded.
    """
    if not entity_ids:
        return
    try:
        from uuid import UUID

        from app.db.unit_of_work import UnitOfWork

        with UnitOfWork() as uow:
            deleted = uow.tap_sync_mappings.delete_by_rip_entity_ids(
                [UUID(entity_id) for entity_id in entity_ids]
            )
        logger.info(
            "tap_sync_mappings cleaned up for deleted project: project_id=%s deleted=%d",
            project_id,
            deleted,
        )
    except Exception:
        logger.warning(
            "tap_sync_mappings cleanup failed for project_id=%s; rows may persist.",
            project_id,
            exc_info=True,
        )


@celery_app.task(
    bind=True,
    name="tasks.project.delete_project_graph",
    max_retries=TASK_MAX_RETRIES,
    default_retry_delay=TASK_RETRY_BASE_DELAY_SECONDS,
    queue="neo4j_sync",
)
def delete_project_graph(
    self,
    project_id: str,
) -> dict[str, str]:
    """DETACH DELETE the :Project node and all owned nodes in Neo4j.

    Enqueued after every successful ``DELETE`` Postgres commit.  Because
    Postgres is already committed when this task runs, failure here does
    NOT roll back Postgres.  Retries ensure the graph eventually converges
    with the authoritative Postgres state.

    Args:
        project_id: UUID string of the deleted project.

    Returns:
        ``{"status": "ok", "project_id": project_id}`` on success.
    """
    from app.db.neo4j import get_neo4j_driver
    from app.repositories.neo4j.project_repository import (
        ProjectRepository as Neo4jProjectRepository,
    )

    try:
        repo = Neo4jProjectRepository(get_neo4j_driver())
        # Collect Module/Feature/UserStory ids BEFORE deleting the graph —
        # tap_sync_mappings has no FK to the project and is keyed only by
        # these ids, so this is the last chance to know which rows are ours.
        entity_ids = repo.get_all_entity_ids_sync(project_id)
        # delete_project_graph_sync is the public synchronous method; calling
        # it directly is correct here — we are already in a sync Celery thread.
        repo.delete_project_graph_sync(project_id)
        logger.info("Neo4j project graph deleted: project_id=%s", project_id)

        _cleanup_tap_sync_mappings(entity_ids, project_id)

        return {"status": "ok", "project_id": project_id}
    except Exception as exc:
        logger.error(
            "Neo4j graph delete failed (attempt %d/%d): project_id=%s error=%s",
            self.request.retries + 1,
            TASK_MAX_RETRIES + 1,
            project_id,
            exc,
            exc_info=True,
        )
        raise self.retry(
            exc=exc,
            countdown=TASK_RETRY_BASE_DELAY_SECONDS * (2**self.request.retries),
        )
