"""Periodic maintenance tasks — currently just the stale-SourceIngestion sweep.

A `celery -A app.core.celery_app.celery_app beat` process must be running for
`detect_stale_ingestions` to actually fire on the schedule registered in
`app/core/celery_app.py`'s `beat_schedule` (a production deployment needs one
such process; the worker instance table in that module's comment block does
not yet include it).
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.celery_app import celery_app
from app.core.messages import (
    MSG_SOURCE_CODE_INGESTION_ADVISORY_STALE,
    MSG_SOURCE_INGESTION_STALE_NOTIFICATION_MESSAGE,
    MSG_SOURCE_INGESTION_STALE_NOTIFICATION_TITLE,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


@celery_app.task(name="tasks.maintenance.detect_stale_ingestions")
def detect_stale_ingestions() -> dict:
    """Fail rfp-family SourceIngestion rows stuck `running` past their stage's max duration.

    A worker process killed mid-task (OOM, spot interruption, node
    replacement, deploy) never raises a Python exception, so the normal
    failure path (`_handle_task_exception` / the `except Exception` block in
    `document_task_stages.py`) never runs — the ingestion (and its sources)
    stay `running` forever, and Redis alone won't redeliver the original
    message until `CELERY_BROKER_VISIBILITY_TIMEOUT` (up to 30h) elapses.
    This sweep is the actual safety net: it force-fails the row so
    `POST /projects/{id}/modules/regenerate` (blocked while any ingestion is
    `running`) becomes callable again within one sweep interval instead of a
    day-plus, and logs an ERROR (there is otherwise no log line at all for
    this failure mode — the process died before it could write one).

    `source_type == "source_code"` ingestions are deliberately excluded from
    auto-fail (see `SourceIngestionService.is_stale`'s docstring — that
    pipeline's total duration is unbounded and legitimately multi-day for
    large projects, so no fixed threshold can safely tell "still working"
    apart from "worker died"). They instead get an advisory WARNING log only,
    with no status mutation and no owner notification, once running past
    `SOURCE_CODE_STALL_ADVISORY_THRESHOLD_SECONDS`.
    """
    from app.db.unit_of_work import UnitOfWork  # noqa: PLC0415
    from app.services.notification_service import publish_notification  # noqa: PLC0415
    from app.services.source_ingestion_service import SourceIngestionService  # noqa: PLC0415

    now = datetime.now(UTC)
    stale_info: list[dict] = []
    advisory_info: list[dict] = []
    with UnitOfWork() as uow:
        service = SourceIngestionService()
        stale = service.fail_stale_running_ingestions(uow, now=now)
        for ingestion in stale:
            project = uow.projects.get_by_uuid(ingestion.project_id)
            stale_info.append(
                {
                    "ingestion_id": str(ingestion.id),
                    "project_id": str(ingestion.project_id),
                    "run_code": ingestion.run_code,
                    "owner_id": project.owner_id if project is not None else None,
                }
            )

        for ingestion in service.list_advisory_stale_source_code_ingestions(uow, now=now):
            advisory_info.append(
                {
                    "ingestion_id": str(ingestion.id),
                    "project_id": str(ingestion.project_id),
                    "run_code": ingestion.run_code,
                }
            )

        uow.commit()

    for info in stale_info:
        logger.error(
            "detect_stale_ingestions: auto-failed ingestion_id=%s project_id=%s run_code=%s"
            " — running past its stage's max plausible duration, worker likely died silently",
            info["ingestion_id"],
            info["project_id"],
            info["run_code"],
        )
        if info["owner_id"] is None:
            continue
        try:
            from app.core.enums.notification_type import NotificationType  # noqa: PLC0415

            publish_notification(
                user_id=info["owner_id"],
                title=MSG_SOURCE_INGESTION_STALE_NOTIFICATION_TITLE,
                message=MSG_SOURCE_INGESTION_STALE_NOTIFICATION_MESSAGE.format(
                    run_code=info["run_code"]
                ),
                notification_type=NotificationType.ERROR,
                data={
                    "project_id": info["project_id"],
                    "source_ingestion_id": info["ingestion_id"],
                },
            )
        except Exception:
            logger.warning(
                "detect_stale_ingestions: failed to notify owner for ingestion_id=%s",
                info["ingestion_id"],
                exc_info=True,
            )

    for info in advisory_info:
        logger.warning(
            "detect_stale_ingestions: %s ingestion_id=%s project_id=%s run_code=%s",
            MSG_SOURCE_CODE_INGESTION_ADVISORY_STALE,
            info["ingestion_id"],
            info["project_id"],
            info["run_code"],
        )

    return {"stale_count": len(stale_info), "advisory_stale_count": len(advisory_info)}
