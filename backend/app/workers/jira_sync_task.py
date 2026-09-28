"""Celery task for RIP → Jira one-way sync."""

from __future__ import annotations

from datetime import UTC
from typing import Any

from app.core.celery_app import celery_app
from app.db.neo4j import get_neo4j_driver
from app.db.unit_of_work import UnitOfWork
from app.repositories.neo4j.module_feature_repository import ModuleFeatureRepository
from app.repositories.neo4j.user_story_repository import UserStoryRepository
from app.services.jira_sync_service import JiraSyncService
from app.utils.logger import get_logger
from app.workers._task_helpers import _run_async

logger = get_logger(__name__)

_TASK_SOFT_TIME_LIMIT = 600
_TASK_TIME_LIMIT = 780


@celery_app.task(
    bind=True,
    name="tasks.sync_to_jira",
    max_retries=2,
    acks_late=True,
    soft_time_limit=_TASK_SOFT_TIME_LIMIT,
    time_limit=_TASK_TIME_LIMIT,
)
def sync_to_jira_task(
    self,
    project_id: str,
    released_entity_ids: list[str],
    held_entity_ids: list[str],
    triggered_by_id: str,
    task_db_id: str | None = None,
) -> dict[str, Any]:
    """Execute RIP → Jira sync as a background Celery task."""
    from uuid import UUID

    logger.info(
        "Jira sync task started: project_id=%s released=%s held=%s",
        project_id,
        len(released_entity_ids),
        len(held_entity_ids),
    )

    try:
        driver = get_neo4j_driver()
        module_feature_repo = ModuleFeatureRepository(driver)
        user_story_repo = UserStoryRepository(driver)
        sync_service = JiraSyncService(module_feature_repo, user_story_repo)

        with UnitOfWork() as uow:
            history = _run_async(
                sync_service.execute_sync(
                    project_id=UUID(project_id),
                    released_entity_ids=[UUID(eid) for eid in released_entity_ids],
                    held_entity_ids=[UUID(eid) for eid in held_entity_ids],
                    triggered_by_id=UUID(triggered_by_id),
                    uow=uow,
                )
            )
            uow.commit()

        logger.info(
            "Jira sync task completed: project_id=%s status=%s summary=%s",
            project_id,
            history.status,
            history.summary,
        )
        return {"status": history.status, "summary": history.summary}

    except Exception as exc:
        logger.error(
            "Jira sync task failed: project_id=%s error=%s", project_id, exc, exc_info=True
        )

        # Record failure in sync history
        try:
            with UnitOfWork() as uow:
                integration = uow.jira_integrations.get_active_by_project_id(UUID(project_id))
                if integration:
                    from datetime import datetime

                    from app.models.postgres.jira_sync_history_model import JiraSyncHistory

                    history = JiraSyncHistory(
                        integration_id=integration.id,
                        project_id=UUID(project_id),
                        triggered_by_id=UUID(triggered_by_id),
                        status="failed",
                        summary={"error": str(exc)[:500]},
                        items_released=released_entity_ids,
                        items_held=held_entity_ids,
                        completed_at=datetime.now(UTC),
                    )
                    uow.jira_sync_history.add(history)
                    uow.commit()
        except Exception:
            logger.error("Failed to record sync failure", exc_info=True)

        raise
