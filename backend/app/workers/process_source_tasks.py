"""Celery workers for the source file processing pipeline.

Pipeline flow
─────────────
    upload_source (SourceService)
        └─▶ process_source_task          ← dispatches by MIME type
               ├─▶ parse_document_task                               ← PDF / DOCX parsing into fragments (document_task.py)
               │       └─▶ generate_modules_and_features_task         ← AI module/feature generation (document_task.py)
               ├─▶ _parse_image_task     ← PNG / JPEG / WEBP via OmniParser (image_task.py)
               └─▶ _parse_code_task      ← ZIP (source code) via Tree-sitter (source_code_task.py)

Each leaf task follows this state machine:
    queued → processing/running → ready_for_review | failed

Status transitions and errors are written back to PostgreSQL.
Parsed entities are stored in Neo4j (graph) and, for documents,
an embedding is generated and stored via pgvector.

Every pipeline converges on ``ready_for_review`` as its success terminal,
matching the module/feature generation flow's terminal state.

Retry strategy
──────────────
All workers use exponential backoff:
    retry 1:  60 s
    retry 2: 120 s
    retry 3: 240 s

After max_retries the task is NOT re-queued; it records the error and
transitions to ``failed``.

Idempotency
───────────
- Status is checked at the start of each task: sources already in a terminal
  status (``ready_for_review``) are skipped to prevent duplicate writes on
  duplicate SQS delivery.
- All Neo4j writes use MERGE (not CREATE) — safe to replay.
- pgvector writes are upserts (UPDATE WHERE source_id = ?).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.constants import (
    COMMON_MAX_RETRIES,
    SOURCE_STATUS_FAILED,
    SOURCE_STATUS_QUEUED,
    SOURCE_STATUS_READY_FOR_REVIEW,
    TASK_RETRY_BASE_DELAY_SECONDS,
    TASK_SOURCE_DISPATCH_SOFT_TIME_LIMIT,
    TASK_SOURCE_DISPATCH_TIME_LIMIT,
)

# Rows in these states are already terminal — skip re-dispatching them.
_SOURCE_TERMINAL_STATUSES = frozenset(
    {
        SOURCE_STATUS_READY_FOR_REVIEW,
    }
)
from app.core.celery_app import celery_app
from app.utils.logger import get_logger
from app.workers._task_helpers import (
    _mark_status,
    mark_cancelled_and_check,
    mark_sources_and_ingestion_cancelled,
)
from app.workers.image_task import _parse_image_task  # noqa: F401 — registers Celery task
from app.workers.source_code_task import _parse_code_task  # noqa: F401 — registers Celery task

logger = get_logger(__name__)

# MIME type → parser category
_IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/jpg", "image/webp"})
_DOCUMENT_MIMES = frozenset(
    {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/plain",
        "text/csv",
    }
)
_CODE_MIMES = frozenset({"application/zip", "application/x-zip-compressed"})


def _parse_and_process_document(
    project_id: str,
    source_ids: list[str],
    task_db_id: str | None = None,
    skip_processing: bool = False,
) -> None:
    """Enqueue phase-1 document parsing for the source batch. Kept for backward-compat imports."""
    from app.workers.document_task import parse_document_task  # noqa: PLC0415

    parse_document_task.apply_async(args=[project_id, source_ids, task_db_id, skip_processing])


# ── Root dispatcher — helpers ───────────────────────────────────────────────


def _handle_cancelled_dispatch(
    project_id: str,
    source_id_list: list[str],
    task_db_id: str | None,
    request_id: str | None,
) -> dict[str, Any] | None:
    """Return a ``cancelled`` result dict if the request was cancelled, else ``None``."""
    if not mark_cancelled_and_check(
        request_id=request_id or task_db_id,
        task_db_id=task_db_id,
        project_id=project_id,
        task_type="source_process",
    ):
        return None

    logger.info(
        "process_source_task: request_id=%s cancelled — skipping dispatch",
        request_id or task_db_id,
    )
    mark_sources_and_ingestion_cancelled(
        source_ids=source_id_list,
        project_id=project_id,
        task_db_id=task_db_id,
        task_type="source_process",
        stage="source.cancelled",
    )
    return {"project_id": project_id, "status": "cancelled"}


def _parse_uuid_list(source_id_list: list[str]) -> list[UUID]:
    return [UUID(sid) for sid in source_id_list]


def _load_sources_to_dispatch(
    source_id_list: list[str], uuid_list: list[UUID]
) -> tuple[list[Any], list[str], list[str]]:
    """Batch-load sources, transition non-terminal ones to QUEUED in one transaction.

    Returns (sources_to_dispatch, not_found_ids, completed_ids).
    """
    from app.db.unit_of_work import UnitOfWork  # noqa: PLC0415

    not_found_ids: list[str] = []
    completed_ids: list[str] = []
    sources_to_dispatch = []

    with UnitOfWork() as uow:
        found_sources = uow.sources.get_many_by_uuids(uuid_list)
        found_map = {str(s.id): s for s in found_sources}

        for sid in source_id_list:
            if sid not in found_map:
                not_found_ids.append(sid)
                logger.error("process_source_task: source_id=%s not found — skipping", sid)

        for source in found_sources:
            source_id = str(source.id)
            if source.status in _SOURCE_TERMINAL_STATUSES:
                completed_ids.append(source_id)
                logger.info(
                    "process_source_task: source_id=%s already terminal (status=%s) — skipping",
                    source_id,
                    source.status,
                )
                continue
            # Transition to QUEUED in this batch transaction; clear any previous error.
            source.status = SOURCE_STATUS_QUEUED
            source.processing_error = None
            sources_to_dispatch.append(source)
        # auto-commit on context exit

    return sources_to_dispatch, not_found_ids, completed_ids


def _mark_source_failed(
    source_id: str,
    error: str,
    stage: str,
    task_db_id: str | None,
    project_id: str,
    failed_ids: list[str],
    *,
    log_as_warning: bool = False,
) -> None:
    _mark_status(
        source_id=source_id,
        status=SOURCE_STATUS_FAILED,
        error=error,
        stage=stage,
        task_db_id=task_db_id,
        project_id=project_id,
    )
    failed_ids.append(source_id)
    log_fn = logger.warning if log_as_warning else logger.error
    log_fn("process_source_task: %s (source_id=%s)", error, source_id)


def _route_sources_by_mime(
    sources_to_dispatch: list[Any],
    task_db_id: str | None,
    project_id: str,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Bucket sources by MIME type, marking invalid/unsupported ones failed.

    Returns (document_source_ids, image_source_ids, code_source_ids, failed_ids).
    """
    document_source_ids: list[str] = []
    image_source_ids: list[str] = []
    code_source_ids: list[str] = []
    failed_ids: list[str] = []

    # ORM attributes remain accessible after UoW closes (expire_on_commit=False).
    for source in sources_to_dispatch:
        source_id = str(source.id)
        if not source.storage_key:
            _mark_source_failed(
                source_id,
                "Source has no storage_key — cannot process",
                "dispatch.invalid_source",
                task_db_id,
                project_id,
                failed_ids,
            )
            continue

        mime_type = source.mime_type
        if mime_type in _IMAGE_MIMES:
            image_source_ids.append(source_id)
        elif mime_type in _DOCUMENT_MIMES:
            document_source_ids.append(source_id)
        elif mime_type in _CODE_MIMES:
            code_source_ids.append(source_id)
        else:
            _mark_source_failed(
                source_id,
                f"Unsupported MIME type for processing: {mime_type}",
                "dispatch.unsupported_mime",
                task_db_id,
                project_id,
                failed_ids,
                log_as_warning=True,
            )

    return document_source_ids, image_source_ids, code_source_ids, failed_ids


def _dispatch_sources(
    project_id: str,
    image_source_ids: list[str],
    document_source_ids: list[str],
    code_source_ids: list[str],
    task_db_id: str | None,
    skip_processing: bool,
    request_id: str | None,
) -> None:
    """Fan out to the leaf tasks, checking for cooperative cancellation between batches."""
    from app.core import task_control  # noqa: PLC0415

    effective_request_id = request_id or task_db_id

    for source_id in image_source_ids:
        if task_control.is_request_cancelled(effective_request_id):
            logger.info(
                "process_source_task: cancelled mid-fan-out — skipping remaining image dispatches"
            )
            break
        _parse_image_task.apply_async(args=[project_id, source_id, task_db_id, request_id])

    if document_source_ids and not task_control.is_request_cancelled(effective_request_id):
        from app.workers.document_task import parse_document_task  # noqa: PLC0415

        parse_document_task.apply_async(
            args=[project_id, document_source_ids, task_db_id, skip_processing, request_id]
        )
        logger.info(
            "Enqueued parse_document_task: project_id=%s source_count=%d",
            project_id,
            len(document_source_ids),
        )

    for source_id in code_source_ids:
        if task_control.is_request_cancelled(effective_request_id):
            logger.info(
                "process_source_task: cancelled mid-fan-out — skipping remaining code dispatches"
            )
            break
        _parse_code_task.apply_async(
            args=[project_id, source_id, task_db_id, skip_processing, request_id]
        )


def _handle_dispatch_failure(
    task: Any,
    exc: Exception,
    all_dispatch_ids: list[str],
    task_db_id: str | None,
    project_id: str,
) -> dict[str, Any]:
    """Mark everything failed once retries are exhausted, otherwise re-raise as a retry."""
    logger.error(
        "process_source_task: dispatch failed project_id=%s error=%s",
        project_id,
        exc,
        exc_info=True,
    )
    if task.request.retries >= task.max_retries:
        for source_id in all_dispatch_ids:
            _mark_status(
                source_id=source_id,
                status=SOURCE_STATUS_FAILED,
                error=str(exc),
                stage="dispatch.failed",
                task_db_id=task_db_id,
                project_id=project_id,
            )
        return {"project_id": project_id, "status": SOURCE_STATUS_FAILED, "error": str(exc)}

    countdown = TASK_RETRY_BASE_DELAY_SECONDS * (2**task.request.retries)
    raise task.retry(exc=exc, countdown=countdown)


def _determine_final_status(
    dispatched_count: int,
    not_found_ids: list[str],
    completed_ids: list[str],
    source_id_list: list[str],
) -> str:
    if dispatched_count > 0:
        return SOURCE_STATUS_QUEUED
    if len(not_found_ids) == len(source_id_list):
        return "not_found"
    if len(completed_ids) == len(source_id_list):
        # All requested sources were already terminal (ready_for_review) —
        # use a synthetic summary label rather than a DB status value.
        return "already_terminal"
    return SOURCE_STATUS_FAILED


# ── Root dispatcher ────────────────────────────────────────────────────────


@celery_app.task(
    bind=True,
    name="tasks.process_source",
    max_retries=COMMON_MAX_RETRIES,
    acks_late=True,
    soft_time_limit=TASK_SOURCE_DISPATCH_SOFT_TIME_LIMIT,
    time_limit=TASK_SOURCE_DISPATCH_TIME_LIMIT,
)
def process_source_task(
    self,
    project_id: str,
    source_ids: list[str] | str,
    task_db_id: str | None = None,
    skip_processing: bool = False,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Route a source to the correct parser based on its MIME type.

    Batch-loads all source rows in a single query, transitions each to
    ``queued``, then dispatches to the appropriate leaf task.  Returns a
    dict with batch routing summary.
    """
    source_id_list: list[str] = [source_ids] if isinstance(source_ids, str) else list(source_ids)
    logger.info(
        "process_source_task started: project_id=%s source_count=%d skip_processing=%s",
        project_id,
        len(source_id_list),
        skip_processing,
    )

    cancelled_result = _handle_cancelled_dispatch(
        project_id, source_id_list, task_db_id, request_id
    )
    if cancelled_result is not None:
        return cancelled_result

    try:
        uuid_list = _parse_uuid_list(source_id_list)
    except ValueError as exc:
        logger.error(
            "process_source_task: invalid UUID in source_ids project_id=%s: %s", project_id, exc
        )
        return {"project_id": project_id, "status": SOURCE_STATUS_FAILED, "error": str(exc)}

    sources_to_dispatch, not_found_ids, completed_ids = _load_sources_to_dispatch(
        source_id_list, uuid_list
    )
    document_source_ids, image_source_ids, code_source_ids, failed_ids = _route_sources_by_mime(
        sources_to_dispatch, task_db_id, project_id
    )

    all_dispatch_ids = image_source_ids + document_source_ids + code_source_ids
    try:
        _dispatch_sources(
            project_id,
            image_source_ids,
            document_source_ids,
            code_source_ids,
            task_db_id,
            skip_processing,
            request_id,
        )
    except Exception as exc:
        return _handle_dispatch_failure(self, exc, all_dispatch_ids, task_db_id, project_id)

    dispatched_count = len(all_dispatch_ids)
    final_status = _determine_final_status(
        dispatched_count, not_found_ids, completed_ids, source_id_list
    )

    logger.info(
        "process_source_task dispatched: project_id=%s total=%d documents=%d images=%d code=%d failed=%d not_found=%d completed=%d",
        project_id,
        len(source_id_list),
        len(document_source_ids),
        len(image_source_ids),
        len(code_source_ids),
        len(failed_ids),
        len(not_found_ids),
        len(completed_ids),
    )
    return {
        "project_id": project_id,
        "status": final_status,
        "total_sources": len(source_id_list),
        "dispatched": dispatched_count,
        "documents": document_source_ids,
        "images": image_source_ids,
        "source_code": code_source_ids,
        "failed": failed_ids,
        "not_found": not_found_ids,
        "completed": completed_ids,
    }
