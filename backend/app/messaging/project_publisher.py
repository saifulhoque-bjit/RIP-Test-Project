"""Celery-backed implementation of :class:`~app.core.protocols.IProjectEventPublisher`.

This module is the **only** place in the codebase that imports Celery task
functions for project events.  The service layer depends on the
:class:`~app.core.protocols.IProjectEventPublisher` protocol, so swapping
the broker (or mocking in tests) requires changing only this module and the
wiring in ``deps.py``.
"""

from __future__ import annotations

from app.utils.logger import get_logger

logger = get_logger(__name__)


class CeleryProjectEventPublisher:
    """Enqueues Celery tasks for project-level Neo4j graph projections.

    Task functions are imported lazily inside each method to avoid importing
    the Celery worker module at application startup, which keeps the FastAPI
    process free of Celery side-effects during tests.
    """

    def project_upserted(self, project_id: str, name: str, status: str) -> None:
        """Enqueue ``sync_project_to_neo4j`` on the ``neo4j_sync`` queue."""
        from app.workers.project_tasks import sync_project_to_neo4j  # lazy import

        sync_project_to_neo4j.apply_async(args=[project_id, name, status])
        logger.debug("Enqueued sync_project_to_neo4j for project_id=%s", project_id)

    def project_deleted(self, project_id: str) -> None:
        """Enqueue ``delete_project_graph`` on the ``neo4j_sync`` queue."""
        from app.workers.project_tasks import delete_project_graph  # lazy import

        delete_project_graph.apply_async(args=[project_id])
        logger.debug("Enqueued delete_project_graph for project_id=%s", project_id)
