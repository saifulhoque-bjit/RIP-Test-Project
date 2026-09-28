"""Celery task for the incremental backlog update pipeline.

Pipeline stages (runs entirely inside this task)
─────────────────────────────────────────────────
1. Parse each PDF source via DocumentService → collect meeting_notes chunks.
2. Download each image source from S3 and run extract_image_fragment
   → collect image_notes fragments.
3. Persist all fragments (PDF + image) to Neo4j via FragmentService.
4. Fetch the existing project backlog from Neo4j.
5. Call run_incremental_update (LLM pipeline, 20-40 min).
6. Log the result and emit the final WebSocket event.

WebSocket progress stages
──────────────────────────
  incremental_update.started            →  5 %  (processing)
  incremental_update.pdf_parsing        → 20 %  (processing)
  incremental_update.image_extraction   → 40 %  (processing)
  incremental_update.fragments_saved    → 55 %  (processing)
  incremental_update.backlog_fetched    → 65 %  (processing)
  incremental_update.ai_processing      → 70 %  (processing)
  incremental_update.completed          →100 %  (ready_for_review, or completed
                                                  when no_changes_explanation)
  incremental_update.failed             →  0 %  (failed)
"""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import UTC, datetime
import json
from typing import Any
from uuid import UUID

from app.core.celery_app import celery_app
from app.core.constants import (
    CONTEXT_MODE_FULL,
    CONTEXT_MODE_SUBSET,
    INCREMENTAL_UPDATE_TASK_TYPE,
    SOURCE_STATUS_FAILED,
    SOURCE_STATUS_RUNNING,
    TASK_INCREMENTAL_SOFT_TIME_LIMIT,
    TASK_INCREMENTAL_TIME_LIMIT,
    TASK_MAX_RETRIES,
    TASK_STATUS_CANCELLED,
)
from app.core.enums.activity_type import ActivityType
from app.core.enums.source_ingestion_stage import SourceIngestionStage
from app.core.enums.source_ingestion_status import SourceIngestionStatus
from app.core.llm_errors import NonRetryableLLMError
from app.core.messages import (
    MSG_ACTIVITY_INCREMENTAL_CHANGESET_CANCELLED,
    MSG_ACTIVITY_INCREMENTAL_CHANGESET_FAILED,
    MSG_ACTIVITY_INCREMENTAL_CHANGESET_INGESTED,
    MSG_ACTIVITY_INCREMENTAL_CHANGESET_STARTED,
    SUMMARY_ACTIVITY_INCREMENTAL_CHANGESET_CANCELLED,
    SUMMARY_ACTIVITY_INCREMENTAL_CHANGESET_FAILED,
    SUMMARY_ACTIVITY_INCREMENTAL_CHANGESET_INGESTED,
    SUMMARY_ACTIVITY_INCREMENTAL_CHANGESET_STARTED,
)
from app.services.activity_log_service import record_activity, resolve_actor_from_task
from app.utils.logger import get_logger
from app.workers._task_helpers import (
    _add_source_ingestion_error,
    _resolve_source_ingestion_id,
    _run_async,
    _update_source_ingestion_fields,
    cancel_sibling_tasks_on_fatal_llm_error,
    describe_non_retryable_llm_error,
    emit_task_event,
    mark_cancelled_and_check,
    mark_sources_and_ingestion_cancelled,
)

logger = get_logger(__name__)

_TASK_TYPE = INCREMENTAL_UPDATE_TASK_TYPE
_DEBUG_BASE_DIR = "temp/incremental_debug"


def _record_changeset_ingested_activity(
    *, project_id: str, task_db_id: str, change_summary: dict
) -> None:
    """Log an activity-feed entry summarizing an ingested AI change-set.

    Flattens each category's per-entity-type sub-counts (modules/features/
    user_stories) into one total per add/update/delete, since this codebase
    has no baseline/version-number concept to report against instead.
    """
    adds_total = sum(change_summary["adds"].values())
    updates_total = sum(change_summary["updates"].values())
    deletes_total = sum(change_summary["deletes"].values())
    record_activity(
        project_id=UUID(project_id),
        activity_type=ActivityType.INCREMENTAL_CHANGESET_INGESTED,
        summary=SUMMARY_ACTIVITY_INCREMENTAL_CHANGESET_INGESTED,
        message=MSG_ACTIVITY_INCREMENTAL_CHANGESET_INGESTED.format(
            adds=adds_total, updates=updates_total, deletes=deletes_total
        ),
        actor_user_id=resolve_actor_from_task(task_db_id),
        data={"change_summary": change_summary},
    )


def _record_changeset_started_activity(
    *, project_id: str, task_db_id: str, source_ids: list[str]
) -> None:
    """Log an activity-feed entry for the start of an incremental change-set ingestion."""
    record_activity(
        project_id=UUID(project_id),
        activity_type=ActivityType.INCREMENTAL_CHANGESET_STARTED,
        summary=SUMMARY_ACTIVITY_INCREMENTAL_CHANGESET_STARTED,
        message=MSG_ACTIVITY_INCREMENTAL_CHANGESET_STARTED,
        actor_user_id=resolve_actor_from_task(task_db_id),
        data={"source_ids": source_ids},
    )


def _record_changeset_cancelled_activity(
    *, project_id: str, task_db_id: str, source_ids: list[str]
) -> None:
    """Log an activity-feed entry for a cancelled incremental change-set ingestion."""
    record_activity(
        project_id=UUID(project_id),
        activity_type=ActivityType.INCREMENTAL_CHANGESET_CANCELLED,
        summary=SUMMARY_ACTIVITY_INCREMENTAL_CHANGESET_CANCELLED,
        message=MSG_ACTIVITY_INCREMENTAL_CHANGESET_CANCELLED,
        actor_user_id=resolve_actor_from_task(task_db_id),
        data={"source_ids": source_ids},
    )


def _record_changeset_failed_activity(
    *, project_id: str, task_db_id: str, source_ids: list[str], error: str
) -> None:
    """Log an activity-feed entry for a terminal incremental change-set failure."""
    record_activity(
        project_id=UUID(project_id),
        activity_type=ActivityType.INCREMENTAL_CHANGESET_FAILED,
        summary=SUMMARY_ACTIVITY_INCREMENTAL_CHANGESET_FAILED,
        message=MSG_ACTIVITY_INCREMENTAL_CHANGESET_FAILED.format(error=error[:200]),
        actor_user_id=resolve_actor_from_task(task_db_id),
        data={"source_ids": source_ids, "error": error},
    )


def _notify_incremental_status(
    *,
    project_id: str,
    status: str,
    source_ids: list[str] | None = None,
    change_summary: dict[str, dict[str, int]] | None = None,
    no_changes_explanation: str | None = None,
    error: str | None = None,
    error_reason: str | None = None,
) -> None:
    """Best-effort: notify the project owner and every assigned member about
    an incremental-update status change.

    Fires for the ``running`` (started), ``ready_for_review`` (completed with
    a proposed changeset awaiting review), ``completed`` (completed with
    ``no_changes_explanation`` — nothing for a human to review), ``cancelled``,
    and ``failed`` transitions. Never raises — a notification failure must
    not abort the pipeline.
    """
    from app.core.enums.notification_type import NotificationType  # noqa: PLC0415
    from app.db.unit_of_work import UnitOfWork  # noqa: PLC0415
    from app.services.notification_service import publish_notification  # noqa: PLC0415

    try:
        with UnitOfWork() as uow:
            project = uow.projects.get_by_uuid(UUID(project_id))
            if project is None:
                return
            recipient_ids = {
                member.user_id for member in uow.project_members.list_by_project(UUID(project_id))
            }
        if project.owner_id is not None:
            recipient_ids.add(project.owner_id)
        if not recipient_ids:
            return

        if status == SourceIngestionStatus.RUNNING.value:
            title = "Incremental Update Started"
            message = f'Incremental update processing has started for "{project.name}".'
            notification_type = NotificationType.INFO
        elif status == SourceIngestionStatus.COMPLETED.value and no_changes_explanation:
            title = "Incremental Update: No Changes"
            message = (
                f'The incremental update for "{project.name}" found no backlog changes '
                f"to make: {no_changes_explanation[:300]}"
            )
            notification_type = NotificationType.INFO
        elif status == SourceIngestionStatus.READY_FOR_REVIEW.value:
            counts = change_summary or {}
            total_changes = sum(
                sum(counts.get(category, {}).values())
                for category in ("adds", "updates", "deletes")
            )
            title = "Incremental Update Ready for Review"
            message = f'{total_changes} change(s) are ready for review in "{project.name}".'
            notification_type = NotificationType.SUCCESS
        elif status == SourceIngestionStatus.CANCELLED.value:
            title = "Incremental Update Cancelled"
            message = f'Incremental update was cancelled for "{project.name}".'
            notification_type = NotificationType.WARNING
        elif status == SourceIngestionStatus.FAILED.value:
            title = "Incremental Update Failed"
            message = f'Incremental update failed for "{project.name}".'
            if error:
                message += f" Error: {error[:200]}"
            notification_type = NotificationType.ERROR
        else:
            return

        data = {
            "project_id": project_id,
            "status": status,
            "source_ingestion_id": (
                _resolve_source_ingestion_id(source_ids=source_ids) if source_ids else None
            ),
            "change_summary": change_summary,
            "no_changes_explanation": no_changes_explanation,
            "error": error,
            "error_reason": error_reason,
        }
        for user_id in recipient_ids:
            try:
                publish_notification(
                    user_id=user_id,
                    title=title,
                    message=message,
                    notification_type=notification_type,
                    data=data,
                )
            except Exception:
                logger.warning(
                    "_notify_incremental_status: failed to notify project_id=%s status=%s "
                    "user_id=%s",
                    project_id,
                    status,
                    user_id,
                    exc_info=True,
                )
    except Exception:
        logger.warning(
            "_notify_incremental_status: failed for project_id=%s status=%s",
            project_id,
            status,
            exc_info=True,
        )


def _fragment_to_dict(fragment) -> dict:
    """Serialize a FragmentModel to a dict, excluding the internal content_hash field."""
    d = dataclasses.asdict(fragment)
    d.pop("content_hash", None)
    d.pop("created_at", None)
    d.pop("updated_at", None)
    return d


@celery_app.task(
    bind=True,
    name="tasks.incremental_update",
    max_retries=TASK_MAX_RETRIES,
    acks_late=True,
    soft_time_limit=TASK_INCREMENTAL_SOFT_TIME_LIMIT,
    time_limit=TASK_INCREMENTAL_TIME_LIMIT,
)
def incremental_update_task(
    self,
    project_id: str,
    pdf_source_ids: list[str],
    image_source_ids: list[str],
    user_message: str,
    skip_processing: bool,
    task_db_id: str,
    context_mode: str = CONTEXT_MODE_FULL,
) -> dict[str, Any]:
    """Run the full incremental update pipeline.

    Args:
        project_id:       Project UUID as string.
        pdf_source_ids:   Source UUIDs (strings) for uploaded PDFs.
        image_source_ids: Source UUIDs (strings) for uploaded images.
        user_message:     Optional user-supplied context for the LLM.
        skip_processing:  When True, returns cached output without calling LLMs.
        task_db_id:       ProjectTask PK for WebSocket event publishing.
        context_mode:     "full" (complete backlog) or "subset" (selector-filtered).
    """
    logger.info(
        "incremental_update_task started: project=%s pdfs=%d images=%d task_db_id=%s",
        project_id,
        len(pdf_source_ids),
        len(image_source_ids),
        task_db_id,
    )

    if mark_cancelled_and_check(
        request_id=task_db_id,
        task_db_id=task_db_id,
        project_id=project_id,
        task_type=_TASK_TYPE,
        stage="incremental_update.cancelled",
    ):
        mark_sources_and_ingestion_cancelled(
            source_ids=pdf_source_ids + image_source_ids,
            project_id=project_id,
            task_db_id=task_db_id,
            task_type=_TASK_TYPE,
            stage="incremental_update.cancelled",
        )
        _notify_incremental_status(
            project_id=project_id,
            status=SourceIngestionStatus.CANCELLED.value,
            source_ids=pdf_source_ids + image_source_ids,
        )
        _record_changeset_cancelled_activity(
            project_id=project_id,
            task_db_id=task_db_id,
            source_ids=pdf_source_ids + image_source_ids,
        )
        return {"project_id": project_id, "status": "cancelled"}

    try:
        result = _run_async(
            _async_incremental_pipeline(
                task=self,
                project_id=project_id,
                pdf_source_ids=pdf_source_ids,
                image_source_ids=image_source_ids,
                user_message=user_message,
                skip_processing=skip_processing,
                task_db_id=task_db_id,
                context_mode=context_mode,
            )
        )
    except NonRetryableLLMError as exc:
        classification = exc.classification
        error_detail = describe_non_retryable_llm_error(classification)
        logger.error(
            "incremental_update_task: non-retryable LLM error project=%s task_db_id=%s "
            "reason=%s",
            project_id,
            task_db_id,
            classification.reason.value,
        )
        emit_task_event(
            task_db_id=task_db_id,
            project_id=project_id,
            task_type=_TASK_TYPE,
            status=SOURCE_STATUS_FAILED,
            stage="incremental_update.failed",
            progress=0,
            error=error_detail,
        )
        _update_source_ingestion_fields(
            source_ids=pdf_source_ids + image_source_ids,
            fields={"status": SourceIngestionStatus.FAILED.value},
        )
        _add_source_ingestion_error(
            source_ids=pdf_source_ids + image_source_ids, error=error_detail
        )
        _notify_incremental_status(
            project_id=project_id,
            status=SourceIngestionStatus.FAILED.value,
            source_ids=pdf_source_ids + image_source_ids,
            error=classification.user_message,
            error_reason=classification.reason.value,
        )
        _record_changeset_failed_activity(
            project_id=project_id,
            task_db_id=task_db_id,
            source_ids=pdf_source_ids + image_source_ids,
            error=error_detail,
        )
        cancel_sibling_tasks_on_fatal_llm_error(
            request_id=task_db_id, task_db_id=task_db_id, project_id=project_id
        )
        return {"project_id": project_id, "status": SourceIngestionStatus.FAILED.value, "error": error_detail}
    except Exception as exc:
        logger.error(
            "incremental_update_task failed: project=%s task_db_id=%s error=%s",
            project_id,
            task_db_id,
            exc,
            exc_info=True,
        )
        if self.request.retries >= self.max_retries:
            emit_task_event(
                task_db_id=task_db_id,
                project_id=project_id,
                task_type=_TASK_TYPE,
                status=SOURCE_STATUS_FAILED,
                stage="incremental_update.failed",
                progress=0,
                error=str(exc),
            )
            _update_source_ingestion_fields(
                source_ids=pdf_source_ids + image_source_ids,
                fields={"status": SourceIngestionStatus.FAILED.value},
            )
            _add_source_ingestion_error(
                source_ids=pdf_source_ids + image_source_ids, error=str(exc)
            )
            _notify_incremental_status(
                project_id=project_id,
                status=SourceIngestionStatus.FAILED.value,
                source_ids=pdf_source_ids + image_source_ids,
                error=str(exc),
            )
            _record_changeset_failed_activity(
                project_id=project_id,
                task_db_id=task_db_id,
                source_ids=pdf_source_ids + image_source_ids,
                error=str(exc),
            )
            return {
                "project_id": project_id,
                "status": SourceIngestionStatus.FAILED.value,
                "error": str(exc),
            }

        # Retry with exponential back-off — only reached while retries remain.
        from app.core.constants import TASK_RETRY_BASE_DELAY_SECONDS  # noqa: PLC0415

        retry_num = self.request.retries + 1
        countdown = TASK_RETRY_BASE_DELAY_SECONDS * (2**self.request.retries)
        emit_task_event(
            task_db_id=task_db_id,
            project_id=project_id,
            task_type=_TASK_TYPE,
            status=SOURCE_STATUS_RUNNING,
            stage=f"incremental_update.retry.{retry_num}",
            progress=30,
            error=f"Attempt {retry_num} failed: {exc}. Retrying in {countdown}s...",
        )
        raise self.retry(exc=exc, countdown=countdown)

    logger.info(
        "incremental_update_task completed: project=%s task_db_id=%s",
        project_id,
        task_db_id,
    )
    return result


# ── Async implementation ───────────────────────────────────────────────────────


async def _async_incremental_pipeline(
    *,
    task: Any,
    project_id: str,
    pdf_source_ids: list[str],
    image_source_ids: list[str],
    user_message: str,
    skip_processing: bool,
    task_db_id: str,
    context_mode: str = CONTEXT_MODE_FULL,
) -> dict[str, Any]:
    """Main async body of the incremental update pipeline."""
    emit_task_event(
        task_db_id=task_db_id,
        project_id=project_id,
        task_type=_TASK_TYPE,
        status=SOURCE_STATUS_RUNNING,
        stage="incremental_update.started",
        progress=5,
        meta={
            "pdf_count": len(pdf_source_ids),
            "image_count": len(image_source_ids),
        },
    )
    _update_source_ingestion_fields(
        source_ids=pdf_source_ids + image_source_ids,
        fields={
            "started_at": datetime.now(UTC),
            "status": SourceIngestionStatus.RUNNING.value,
        },
    )
    _notify_incremental_status(
        project_id=project_id,
        status=SourceIngestionStatus.RUNNING.value,
        source_ids=pdf_source_ids + image_source_ids,
    )
    _record_changeset_started_activity(
        project_id=project_id,
        task_db_id=task_db_id,
        source_ids=pdf_source_ids + image_source_ids,
    )

    # Batch-fetch file_type for all sources upfront (single DB query).
    source_file_types = _fetch_source_file_types(pdf_source_ids + image_source_ids)

    # ── Stage 1: Parse PDF sources ─────────────────────────────────────────────
    chunks_by_source: dict[str, list[dict]] = {}
    if pdf_source_ids:
        chunks_by_source = await _parse_pdf_sources(
            pdf_source_ids=pdf_source_ids,
            project_id=project_id,
            task_db_id=task_db_id,
        )

    # ── Stage 2: Extract image fragments ──────────────────────────────────────
    fragments_by_image_source: dict[str, dict] = {}
    if image_source_ids:
        fragments_by_image_source = await _extract_image_fragments(
            image_source_ids=image_source_ids,
            project_id=project_id,
            task_db_id=task_db_id,
        )

    # ── Stage 3: Persist all fragments ────────────────────────────────────────
    meeting_notes_fragments, saved_image_fragments = await _persist_fragments(
        chunks_by_source=chunks_by_source,
        fragments_by_image_source=fragments_by_image_source,
        project_id=project_id,
        task_db_id=task_db_id,
        source_file_types=source_file_types,
    )

    # ── Stage 4: Fetch existing backlog ───────────────────────────────────────
    backlog_json = await _fetch_backlog(
        project_id=project_id,
        task_db_id=task_db_id,
    )

    # ── Stage 5: Run AI pipeline ──────────────────────────────────────────────
    result = await _run_ai_pipeline(
        backlog_json=backlog_json,
        meeting_notes_fragments=meeting_notes_fragments,
        image_fragments=saved_image_fragments,
        user_message=user_message,
        skip_processing=skip_processing,
        project_id=project_id,
        task_db_id=task_db_id,
        context_mode=context_mode,
    )

    if isinstance(result, dict) and result.get("status") == "CANCELLED":
        logger.info(
            "[INCREMENTAL] cancelled mid-generation: project=%s task_db_id=%s",
            project_id,
            task_db_id,
        )
        emit_task_event(
            task_db_id=task_db_id,
            project_id=project_id,
            task_type=_TASK_TYPE,
            status=TASK_STATUS_CANCELLED,
            stage="incremental_update.cancelled",
            progress=100,
        )
        mark_sources_and_ingestion_cancelled(
            source_ids=pdf_source_ids + image_source_ids,
            project_id=project_id,
            task_db_id=task_db_id,
            task_type=_TASK_TYPE,
            stage="incremental_update.cancelled",
        )
        _notify_incremental_status(
            project_id=project_id,
            status=SourceIngestionStatus.CANCELLED.value,
            source_ids=pdf_source_ids + image_source_ids,
        )
        _record_changeset_cancelled_activity(
            project_id=project_id,
            task_db_id=task_db_id,
            source_ids=pdf_source_ids + image_source_ids,
        )
        return {"project_id": project_id, "status": "cancelled"}

    if isinstance(result, dict) and result.get("status") == "failed":
        raise RuntimeError(result.get("error") or "incremental update generation failed")

    # ── Stage 6: Persist incremental changes ──────────────────────────────────
    change_summary = await _persist_incremental_result(
        result=result,
        project_id=project_id,
        task_db_id=task_db_id,
        source_ids=pdf_source_ids + image_source_ids,
        skip_processing=skip_processing,
    )

    no_changes_explanation = (result.get("generation_metadata") or {}).get("no_changes_explanation")
    # No proposed changeset means there's nothing for a human to review —
    # the ingestion is done, not merely awaiting review, unlike every other
    # (non-empty) incremental run.
    final_ingestion_status = (
        SourceIngestionStatus.COMPLETED.value
        if no_changes_explanation
        else SourceIngestionStatus.READY_FOR_REVIEW.value
    )

    _update_source_ingestion_fields(
        source_ids=pdf_source_ids + image_source_ids,
        fields={
            "completed_at": datetime.now(UTC),
            "status": final_ingestion_status,
            "tot_modules": change_summary["adds"]["modules"],
            "tot_features": change_summary["adds"]["features"],
            "tot_user_stories": change_summary["adds"]["user_stories"],
            "tot_modules_updated": change_summary["updates"]["modules"],
            "tot_features_updated": change_summary["updates"]["features"],
            "tot_user_stories_updated": change_summary["updates"]["user_stories"],
            "tot_modules_deleted": change_summary["deletes"]["modules"],
            "tot_features_deleted": change_summary["deletes"]["features"],
            "tot_user_stories_deleted": change_summary["deletes"]["user_stories"],
            "no_changes_explanation": no_changes_explanation,
        },
    )
    from app.workers.document_task_stages import _add_run_stage_by_source_ids  # noqa: PLC0415

    # Incremental is a single automated pass with no module/feature vs.
    # user-story review split (see SourceIngestionStage's module docstring),
    # so it tags the generic READY_FOR_REVIEW checkpoint rather than the
    # RFP-specific MODULE_FEATURE_READY_FOR_REVIEW/USER_STORY_READY_FOR_REVIEW.
    _add_run_stage_by_source_ids(
        pdf_source_ids + image_source_ids, SourceIngestionStage.READY_FOR_REVIEW
    )

    emit_task_event(
        task_db_id=task_db_id,
        project_id=project_id,
        task_type=_TASK_TYPE,
        status=final_ingestion_status,
        stage="incremental_update.completed",
        progress=100,
        meta={
            "result_keys": list(result.keys()),
            "updates": change_summary["updates"],
            "adds": change_summary["adds"],
            "deletes": change_summary["deletes"],
            "no_changes_explanation": no_changes_explanation,
        },
    )

    _notify_incremental_status(
        project_id=project_id,
        status=final_ingestion_status,
        source_ids=pdf_source_ids + image_source_ids,
        change_summary=change_summary,
        no_changes_explanation=no_changes_explanation,
    )

    _record_changeset_ingested_activity(
        project_id=project_id, task_db_id=task_db_id, change_summary=change_summary
    )

    logger.info(
        "[INCREMENTAL] Pipeline complete: project=%s task_db_id=%s result_keys=%s",
        project_id,
        task_db_id,
        list(result.keys()),
    )
    return result


async def _parse_pdf_sources(
    *,
    pdf_source_ids: list[str],
    project_id: str,
    task_db_id: str,
) -> dict[str, list[dict]]:
    """Parse each PDF source and return a mapping of source_id → chunks."""
    from app.services.document_parser_service import DocumentService  # noqa: PLC0415

    if not pdf_source_ids:
        return {}

    emit_task_event(
        task_db_id=task_db_id,
        project_id=project_id,
        task_type=_TASK_TYPE,
        status=SOURCE_STATUS_RUNNING,
        stage="incremental_update.pdf_parsing",
        progress=10,
        meta={"pdf_count": len(pdf_source_ids)},
    )

    chunks_by_source: dict[str, list[dict]] = {}
    for i, source_id_str in enumerate(pdf_source_ids):
        source_id = UUID(source_id_str)
        try:
            response = await DocumentService().parse_and_format_for_alternate_pipeline(source_id)
            chunks_by_source[source_id_str] = response.chunks
            logger.info(
                "[INCREMENTAL] PDF parsed: source_id=%s chunks=%d",
                source_id_str,
                len(response.chunks),
            )
        except Exception as exc:
            logger.error(
                "[INCREMENTAL] PDF parsing failed: source_id=%s error=%s",
                source_id_str,
                exc,
                exc_info=True,
            )
            chunks_by_source[source_id_str] = []

        # Emit incremental progress: 10 % → 40 % spread across pdfs.
        progress = 10 + int(30 * (i + 1) / len(pdf_source_ids))
        emit_task_event(
            task_db_id=task_db_id,
            project_id=project_id,
            task_type=_TASK_TYPE,
            status=SOURCE_STATUS_RUNNING,
            stage="incremental_update.pdf_parsing",
            progress=progress,
        )

    total_chunks = sum(len(c) for c in chunks_by_source.values())
    logger.info(
        "[INCREMENTAL] PDF parsing done: sources=%d total_chunks=%d project=%s",
        len(chunks_by_source),
        total_chunks,
        project_id,
    )
    return chunks_by_source


async def _extract_image_fragments(
    *,
    image_source_ids: list[str],
    project_id: str,
    task_db_id: str,
) -> dict[str, dict]:
    """Download each image from S3 and extract structured fragments.

    Returns a mapping of source_id → fragment dict so that failed sources
    are simply absent from the result rather than silently misaligning a list.
    """
    from app.clients.aws_session import get_aws_session  # noqa: PLC0415
    from app.core.config import settings  # noqa: PLC0415
    from app.db.unit_of_work import UnitOfWork  # noqa: PLC0415
    from app.services.rfp_pipeline_v2_graph_service.image_extraction_service import (  # noqa: PLC0415
        extract_image_fragment,
    )
    from app.workers.document_task_stages import _get_project_llm_options  # noqa: PLC0415

    if not image_source_ids:
        return {}

    options = _get_project_llm_options(project_id=project_id)

    emit_task_event(
        task_db_id=task_db_id,
        project_id=project_id,
        task_type=_TASK_TYPE,
        status=SOURCE_STATUS_RUNNING,
        stage="incremental_update.image_extraction",
        progress=41,
        meta={"image_count": len(image_source_ids)},
    )

    fragments_by_source: dict[str, dict] = {}
    for i, source_id_str in enumerate(image_source_ids):
        source_id = UUID(source_id_str)
        try:
            # Resolve S3 key and MIME type from the source row.
            with UnitOfWork() as uow:
                source = uow.sources.get_by_uuid(source_id)
                if source is None or not source.storage_key:
                    logger.warning(
                        "[INCREMENTAL] Image source not found or has no key: source_id=%s",
                        source_id_str,
                    )
                else:
                    storage_key = source.storage_key
                    mime_type = source.mime_type

                    # Download image bytes from S3.
                    async with get_aws_session().client("s3") as s3:
                        s3_response = await s3.get_object(
                            Bucket=settings.AWS_S3_SOURCES_BUCKET,
                            Key=storage_key,
                        )
                        image_bytes: bytes = await s3_response["Body"].read()

                    fragment = await extract_image_fragment(
                        image_bytes, source_id_str, mime_type, options=options
                    )
                    fragments_by_source[source_id_str] = fragment
                    logger.info(
                        "[INCREMENTAL] Image fragment extracted: source_id=%s frag_type=%s",
                        source_id_str,
                        fragment.get("frag_type"),
                    )
        except Exception as exc:
            logger.error(
                "[INCREMENTAL] Image extraction failed: source_id=%s error=%s",
                source_id_str,
                exc,
                exc_info=True,
            )

        # Emit incremental progress: 41 % → 54 % spread across images.
        progress = 41 + int(13 * (i + 1) / len(image_source_ids))
        emit_task_event(
            task_db_id=task_db_id,
            project_id=project_id,
            task_type=_TASK_TYPE,
            status=SOURCE_STATUS_RUNNING,
            stage="incremental_update.image_extraction",
            progress=progress,
        )

    logger.info(
        "[INCREMENTAL] Image extraction done: extracted=%d/%d project=%s",
        len(fragments_by_source),
        len(image_source_ids),
        project_id,
    )
    return fragments_by_source


def _fetch_source_file_types(source_id_strs: list[str]) -> dict[str, str]:
    """Batch-fetch ``file_type`` for each source from PostgreSQL.

    Issues a single query regardless of how many IDs are supplied.
    Returns ``{source_id_str: file_type}``; sources not found are absent.
    """
    from app.db.unit_of_work import UnitOfWork  # noqa: PLC0415

    if not source_id_strs:
        return {}
    uuids = [UUID(sid) for sid in source_id_strs]
    with UnitOfWork() as uow:
        sources = uow.sources.get_many_by_uuids(uuids)
    return {str(source.id): (source.file_type or "") for source in sources}


async def _persist_fragments(
    *,
    chunks_by_source: dict[str, list[dict]],
    fragments_by_image_source: dict[str, dict],
    project_id: str,
    task_db_id: str,
    source_file_types: dict[str, str] | None = None,
) -> tuple[list, list]:
    """Persist PDF chunks and image fragments to Neo4j via FragmentService.

    Each source's data is saved under its own source_id.

    Returns:
        A tuple of (meeting_notes_fragments, saved_image_fragments) where each
        entry is a flat list of saved FragmentModel objects (with created_at /
        updated_at populated from Neo4j).
    """
    from app.services.fragment_service import FragmentService  # noqa: PLC0415
    from app.workers.document_task_stages import _apply_source_type_fallback  # noqa: PLC0415

    _file_types = source_file_types or {}

    emit_task_event(
        task_db_id=task_db_id,
        project_id=project_id,
        task_type=_TASK_TYPE,
        status=SOURCE_STATUS_RUNNING,
        stage="incremental_update.fragments_saved",
        progress=55,
    )

    svc = FragmentService()
    saved_meeting_notes_fragments: list = []
    saved_image_fragments: list = []

    for source_id_str, chunks in chunks_by_source.items():
        if not chunks:
            continue
        try:
            file_type = _file_types.get(source_id_str, "")
            typed_chunks = _apply_source_type_fallback(chunks=chunks, source_file_type=file_type)
            saved = await svc.create_fragments_from_chunks(
                source_id=UUID(source_id_str),
                chunks=typed_chunks,
            )
            saved_meeting_notes_fragments.extend(saved)
            logger.info(
                "[INCREMENTAL] PDF fragments saved: source_id=%s count=%d source_type=%s",
                source_id_str,
                len(saved),
                file_type or "unset",
            )
        except Exception as exc:
            logger.error(
                "[INCREMENTAL] PDF fragment save failed: source_id=%s error=%s",
                source_id_str,
                exc,
                exc_info=True,
            )

    for source_id_str, fragment in fragments_by_image_source.items():
        try:
            file_type = _file_types.get(source_id_str, "")
            typed_fragment = {**fragment, "source_type": file_type} if file_type else fragment
            saved = await svc.create_fragments_from_chunks(
                source_id=UUID(source_id_str),
                chunks=[typed_fragment],
            )
            saved_image_fragments.extend(saved)
            logger.info(
                "[INCREMENTAL] Image fragment saved: source_id=%s source_type=%s",
                source_id_str,
                file_type or "unset",
            )
        except Exception as exc:
            logger.error(
                "[INCREMENTAL] Image fragment save failed: source_id=%s error=%s",
                source_id_str,
                exc,
                exc_info=True,
            )

    return saved_meeting_notes_fragments, saved_image_fragments


async def _fetch_backlog(
    *,
    project_id: str,
    task_db_id: str,
) -> str:
    """Retrieve the existing backlog from Neo4j as a hierarchical JSON string.

    Returns a dict with two keys:
      - ``persona_glossary`` — list of persona/role dicts from the Project node.
      - ``modules`` — Module → Feature → UserStory tree so the AI pipeline has
        full structural context for the incremental update.
    """
    from app.db.neo4j import get_neo4j_driver  # noqa: PLC0415
    from app.repositories.neo4j.module_feature_repository import (
        ModuleFeatureRepository,  # noqa: PLC0415
    )
    from app.repositories.neo4j.user_story_repository import UserStoryRepository  # noqa: PLC0415
    from app.services.project_graph_service import ProjectGraphService  # noqa: PLC0415

    emit_task_event(
        task_db_id=task_db_id,
        project_id=project_id,
        task_type=_TASK_TYPE,
        status=SOURCE_STATUS_RUNNING,
        stage="incremental_update.backlog_fetched",
        progress=65,
    )

    project_uuid = UUID(project_id)
    _empty = json.dumps({"persona_glossary": [], "modules": []})

    try:
        driver = get_neo4j_driver()

        # Persona glossary is stored on the ProjectMetadata node.
        persona_glossary: list[dict] = await asyncio.to_thread(
            ProjectGraphService().get_persona_glossary, project_id=project_uuid
        )

        # Modules with nested features (functions + feature-level sources included).
        mf_repo = ModuleFeatureRepository(driver)
        modules = await mf_repo.list_modules_by_project(project_uuid)

        # User stories (flat), grouped by feature_id for O(1) tree assembly.
        us_repo = UserStoryRepository(driver)
        user_stories, _ = await us_repo.list_user_stories_for_project(
            project_id=project_uuid,
            skip=0,
            limit=10_000,
        )
        stories_by_feature: dict[str, list] = {}
        for us in user_stories:
            stories_by_feature.setdefault(us.feature_id or "", []).append(us)

        # Build the hierarchical structure.
        modules_data = []
        for module in modules:
            features_data = []
            for feature in module.features:
                stories = stories_by_feature.get(feature.id, [])
                features_data.append(
                    {
                        "feature_code": feature.fea_code,
                        "feature_id": feature.id,
                        "feature_name": feature.name,
                        "feature_description": feature.description,
                        "sources": feature.sources,
                        "functions": [
                            {
                                "fun_code": fn.fun_code,
                                "name": fn.name,
                                "description": fn.description,
                            }
                            for fn in feature.functions
                        ],
                        "user_stories": [
                            {
                                "user_story_id": us.id,
                                "user_story_code": us.user_story_code,
                                "title": us.title,
                                "as_a": us.as_a,
                                "i_want_to": us.i_want_to,
                                "so_that": us.so_that,
                                "acceptance_criteria": us.acceptance_criteria,
                                "story_points": us.story_points,
                                "sources": us.sources,
                                "technical_notes": us.technical_notes,
                            }
                            for us in stories
                        ],
                    }
                )
            modules_data.append(
                {
                    "module_id": module.id,
                    "module_code": module.mod_code,
                    "module_name": module.name,
                    "module_description": module.description,
                    "features": features_data,
                }
            )

        backlog = {"persona_glossary": persona_glossary, "modules": modules_data}
        backlog_json = json.dumps(backlog, default=str)
        logger.info(
            "[INCREMENTAL] Backlog fetched: project=%s modules=%d stories=%d",
            project_id,
            len(modules_data),
            len(user_stories),
        )
        return backlog_json
    except Exception as exc:
        logger.error(
            "[INCREMENTAL] Backlog fetch failed: project=%s error=%s",
            project_id,
            exc,
            exc_info=True,
        )
        return _empty


def _debug_expand_output(data: dict) -> dict:
    """Parse a JSON-string ``output`` field so debug dumps render it as nested
    JSON instead of an escaped string blob. Returns a shallow copy — the
    original dict (consumed downstream as a raw string) is left untouched.
    """
    output = data.get("output") if isinstance(data, dict) else None
    if not isinstance(output, str):
        return data
    try:
        return {**data, "output": json.loads(output)}
    except json.JSONDecodeError:
        return data


def _backlog_features_to_selector_items(backlog_json: str) -> list[dict]:
    """Flatten the module→feature backlog tree into selector 'items' (feature-level)."""
    backlog = json.loads(backlog_json)
    items = []
    for module in backlog.get("modules", []):
        for feature in module.get("features", []):
            items.append(
                {
                    "id": feature["feature_id"],
                    "title": feature["feature_name"],
                    "description": feature["feature_description"],
                    "functions": [fn["name"] for fn in feature.get("functions", [])],
                    "stories": [us["title"] for us in feature.get("user_stories", [])],
                }
            )
    return items


def _filter_backlog_to_feature_ids(backlog_json: str, feature_ids: set[str]) -> str:
    """Prune the module→feature backlog tree down to the given feature IDs (SUBSET context_mode)."""
    backlog = json.loads(backlog_json)
    filtered_modules = []
    for module in backlog.get("modules", []):
        features = [f for f in module.get("features", []) if f["feature_id"] in feature_ids]
        if features:
            filtered_modules.append({**module, "features": features})
    return json.dumps(
        {"persona_glossary": backlog.get("persona_glossary", []), "modules": filtered_modules},
        default=str,
    )


async def _run_ai_pipeline(
    *,
    backlog_json: str,
    meeting_notes_fragments: list,
    image_fragments: list,
    user_message: str,
    skip_processing: bool,
    project_id: str,
    task_db_id: str,
    context_mode: str = CONTEXT_MODE_FULL,
) -> dict[str, Any]:
    """Call run_incremental_update and return the LLM result."""
    from app.services.rfp_pipeline_v2_graph_service.graph_incremental import (  # noqa: PLC0415
        run_incremental_update,
    )
    from app.workers.document_task_stages import _get_project_llm_options  # noqa: PLC0415

    options = _get_project_llm_options(project_id=project_id)
    if task_db_id:
        options["request_id"] = task_db_id

    emit_task_event(
        task_db_id=task_db_id,
        project_id=project_id,
        task_type=_TASK_TYPE,
        status=SOURCE_STATUS_RUNNING,
        stage="incremental_update.ai_processing",
        progress=70,
        meta={
            "meeting_notes_count": len(meeting_notes_fragments),
            "image_notes_count": len(image_fragments),
            "llm_provider": options.get("llm_provider"),
            "llm_model": options.get("llm_model"),
        },
    )

    meeting_notes_json = json.dumps(
        [_fragment_to_dict(f) for f in meeting_notes_fragments], default=str
    )
    image_notes_json = (
        json.dumps([_fragment_to_dict(f) for f in image_fragments], default=str)
        if image_fragments
        else "[]"
    )

    from app.utils.common import dump_json_debug  # noqa: PLC0415

    logger.info(
        "[INCREMENTAL] Calling run_incremental_update: project=%s "
        "meeting_notes=%d_fragments image_notes=%d_fragments skip=%s context_mode=%s options=%s",
        project_id,
        len(meeting_notes_fragments),
        len(image_fragments),
        skip_processing,
        context_mode,
        options,
    )

    target_backlog_json = backlog_json
    if context_mode.lower() == CONTEXT_MODE_SUBSET:
        from app.services.rfp_pipeline_v2_graph_service.graph_incremental_selector import (  # noqa: PLC0415
            note_from_json,
            run_incremental_selector,
        )

        combined_fragments = json.loads(meeting_notes_json) + json.loads(image_notes_json or "[]")
        note = note_from_json(combined_fragments)
        items = _backlog_features_to_selector_items(backlog_json)

        dump_json_debug(
            "incremental_selector_kwargs_input.json",
            {
                "note": combined_fragments,
                "note_flattened": note,
                "items": items,
                "skip_processing": skip_processing,
                "options": options,
            },
            base_dir=_DEBUG_BASE_DIR,
        )

        selector_result = await run_incremental_selector(
            note=note,
            items=items,
            skip_processing=skip_processing,
            options=options,
        )
        dump_json_debug(
            "incremental_selector_output.json",
            _debug_expand_output(selector_result),
            base_dir=_DEBUG_BASE_DIR,
        )
        if isinstance(selector_result, dict) and selector_result.get("status") == "failed":
            raise RuntimeError(selector_result.get("error") or "incremental selector failed")
        selected_feature_ids = set(selector_result.get("selected_ids") or [])
        logger.info(
            "[INCREMENTAL] Selector picked %d/%d feature(s): project=%s",
            len(selected_feature_ids),
            len(items),
            project_id,
        )
        target_backlog_json = _filter_backlog_to_feature_ids(backlog_json, selected_feature_ids)

    incremental_backlog_kwargs = {
        "backlog_json": json.loads(target_backlog_json),
        "meeting_notes_json": json.loads(meeting_notes_json),
        "image_notes_json": json.loads(image_notes_json or "[]"),
        "user_message": user_message or "",
        "skip_processing": skip_processing,
        "options": options,
    }
    dump_json_debug(
        "incremental_backlog_kwargs_input.json",
        incremental_backlog_kwargs,
        base_dir=_DEBUG_BASE_DIR,
    )

    result = await run_incremental_update(
        backlog=target_backlog_json,
        meeting_notes=meeting_notes_json,
        image_notes=image_notes_json,
        user_message=user_message,
        context_mode=context_mode,
        skip_processing=skip_processing,
        options=options,
    )
    dump_json_debug(
        "incremental_update_output.json", _debug_expand_output(result), base_dir=_DEBUG_BASE_DIR
    )

    logger.info(
        "[INCREMENTAL] run_incremental_update result: project=%s keys=%s",
        project_id,
        list(result.keys()) if isinstance(result, dict) else type(result).__name__,
    )
    return result if isinstance(result, dict) else {"output": result}


def _count_changed_in_hierarchy(items: list[dict]) -> dict[str, int]:
    """Count changed=True nodes across the module→feature→user_story hierarchy."""
    modules = features = user_stories = 0
    for module in items:
        if module.get("changed"):
            modules += 1
        for feature in module.get("features", []):
            if feature.get("changed"):
                features += 1
            for story in feature.get("user_stories", []):
                if story.get("changed"):
                    user_stories += 1
    return {"modules": modules, "features": features, "user_stories": user_stories}


def _count_deletes_by_type(items: list[dict]) -> dict[str, int]:
    """Count delete items by their ``type`` field (module / feature / user_story)."""
    modules = features = user_stories = 0
    for item in items:
        t = item.get("type", "")
        if t == "module":
            modules += 1
        elif t == "feature":
            features += 1
        elif t == "user_story":
            user_stories += 1
    return {"modules": modules, "features": features, "user_stories": user_stories}


async def _remap_skip_processing_incremental_module_ids(project_id: str, payload: dict) -> None:
    """Rewrite each updates/adds module (and nested feature) id to match this project's real graph.

    The skip_processing sample (``sample_result/Incremental/incremental_change.json``)
    is a static snapshot with ``module_id``/``feature_id`` values frozen from
    whichever project first captured it. ``handle_updates``/``handle_adds``
    (``IncrementalUpdateProcessorService``) resolve existing nodes by these raw
    ids, so a stale id means an update silently no-ops, and an add's
    ``changed: false`` wrapper module/feature (representing an existing
    parent new children attach under) never resolves to a real node —
    orphaning anything created underneath it, the same class of bug fixed for
    the RFP backlog sample (see ``_remap_skip_processing_feature_ids`` in
    ``document_task_stages.py``). ``module_code``/``feature_code`` are the
    project-agnostic identifiers the sample and every project share, so they
    are used here to re-resolve each id against this project's real Module/
    Feature nodes before updates/adds run.

    Existing user stories are resolved by their code within the resolved
    project feature. Their cached ids are deliberately ignored because the
    skip-processing fixture may have been captured against another project.
    New stories under ``adds`` are left untouched so the processor can assign
    fresh ids.

    User-story deletes with ``user_story_code`` are resolved against the
    current project as well. Deletes without a code retain their UUID because
    there is no safe way to infer the intended node from a stale identifier.

    A ``None``/empty ``module_code``/``feature_code`` is not a mismatch — it
    means "genuinely new, no existing entity to match" (a legitimate ``adds``
    entry), and is skipped silently. Only a code that IS present but doesn't
    resolve counts as unmatched. A module/feature whose code fails to resolve
    is dropped from the payload entirely (rather than kept with its stale,
    wrong-project id) so ``handle_updates``/``handle_adds`` never silently
    no-ops or orphans against it. Raises when every *coded* module fails to
    resolve, OR when every *coded* feature fails to resolve (even if the
    modules themselves happened to match — module codes are often generic
    sequential numbers ("1", "2", "3"...) that coincidentally exist across
    unrelated projects, so a total feature mismatch is just as strong a
    signal that the sample doesn't correspond to this project) — proceeding
    in either case would silently reproduce the exact orphaning/no-op bug
    this function exists to fix. A partial mismatch is logged at ERROR
    rather than raised.
    """
    from app.repositories.neo4j.module_feature_repository import ModuleFeatureRepository
    from app.repositories.neo4j.user_story_repository import UserStoryRepository

    modules = await ModuleFeatureRepository().list_modules_by_project(UUID(project_id))
    module_code_to_id = {m.mod_code: m.id for m in modules if m.mod_code}
    feature_code_to_id = {
        feature.fea_code: feature.id
        for module in modules
        for feature in module.features
        if feature.fea_code
    }

    unmatched_module_codes: list[str] = []
    unmatched_feature_codes: list[str] = []

    def _remap_feature(feature: dict) -> bool:
        """Returns True if this feature should be kept in the payload."""
        code = feature.get("feature_code")
        if not code:
            return True  # genuinely new feature — nothing to remap, keep as-is
        real_feature_id = feature_code_to_id.get(code)
        if real_feature_id:
            feature["feature_id"] = real_feature_id
            return True
        unmatched_feature_codes.append(code)
        return False

    def _remap_module(module: dict) -> bool:
        """Returns True if this module should be kept in the payload."""
        code = module.get("module_code")
        if code:
            real_module_id = module_code_to_id.get(code)
            if real_module_id:
                module["module_id"] = real_module_id
            else:
                unmatched_module_codes.append(code)
                return False  # drop the whole module — its own id never resolved
        module["features"] = [f for f in (module.get("features") or []) if _remap_feature(f)]
        return True

    updates = payload.get("updates") or []
    adds = payload.get("adds") or []
    modules_to_process = updates + adds
    coded_features = [
        feature
        for module in modules_to_process
        for feature in (module.get("features") or [])
        if feature.get("feature_code")
    ]

    payload["updates"] = [m for m in updates if _remap_module(m)]
    payload["adds"] = [m for m in adds if _remap_module(m)]

    user_story_repo: UserStoryRepository | None = None
    unmatched_user_story_codes: list[str] = []

    async def _resolve_user_stories(module: dict) -> None:
        for feature in module.get("features") or []:
            feature_id = feature.get("feature_id")
            if not feature_id:
                continue
            resolved_stories: list[dict] = []
            for story in feature.get("user_stories") or []:
                code = story.get("user_story_code")
                if not story.get("changed") or not code:
                    resolved_stories.append(story)
                    continue
                nonlocal user_story_repo
                if user_story_repo is None:
                    user_story_repo = UserStoryRepository()
                matches, _ = await user_story_repo.list_user_stories_for_project(
                    project_id=UUID(project_id),
                    feature_id=feature_id,
                    user_story_code=code,
                    limit=100,
                )
                exact_matches = [
                    match
                    for match in matches
                    if match.user_story_code.casefold() == str(code).casefold()
                ]
                if len(exact_matches) == 1:
                    story["user_story_id"] = exact_matches[0].id
                    resolved_stories.append(story)
                else:
                    unmatched_user_story_codes.append(str(code))
            feature["user_stories"] = resolved_stories

    for module in payload["updates"]:
        await _resolve_user_stories(module)

    for entry in payload.get("deletes") or []:
        if entry.get("type") != "user_story" or not entry.get("user_story_code"):
            continue
        if user_story_repo is None:
            user_story_repo = UserStoryRepository()
        matches, _ = await user_story_repo.list_user_stories_for_project(
            project_id=UUID(project_id),
            user_story_code=entry["user_story_code"],
            limit=100,
        )
        exact_matches = [
            match
            for match in matches
            if match.user_story_code.casefold()
            == str(entry["user_story_code"]).casefold()
        ]
        if len(exact_matches) == 1:
            entry["uuid"] = exact_matches[0].id
        else:
            unmatched_user_story_codes.append(str(entry["user_story_code"]))

    if unmatched_module_codes or unmatched_feature_codes or unmatched_user_story_codes:
        logger.error(
            "_remap_skip_processing_incremental_module_ids: unmatched module_codes=%s "
            "feature_codes=%s user_story_codes=%s project_id=%s — dropping unresolved entries "
            "from payload",
            unmatched_module_codes,
            unmatched_feature_codes,
            unmatched_user_story_codes,
            project_id,
        )

    coded_modules = [m for m in modules_to_process if m.get("module_code")]
    total_module_mismatch = bool(coded_modules) and len(unmatched_module_codes) == len(
        coded_modules
    )
    total_feature_mismatch = bool(coded_features) and len(unmatched_feature_codes) == len(
        coded_features
    )
    if total_module_mismatch or total_feature_mismatch:
        raise RuntimeError(
            f"skip_processing sample (sample_result/Incremental/incremental_change.json) "
            f"doesn't match project {project_id} — none of the sample's coded module(s)/"
            f"feature(s) matched a real Module/Feature. Use a sample harvested against a "
            f"project with matching module/feature codes."
        )


async def _persist_incremental_result(
    *,
    result: dict[str, Any],
    project_id: str,
    task_db_id: str,
    source_ids: list[str],
    skip_processing: bool = False,
) -> dict[str, dict[str, int]]:
    """Parse the AI output and call the four processor methods.

    The ``result`` dict from ``_run_ai_pipeline`` carries an ``output`` key
    whose value is a JSON string containing ``updates``, ``adds``, ``deletes``,
    and ``flags``.  Failures in any section are logged but never re-raised so
    a persistence error does not roll back the completed AI work.

    ``source_ids`` (the uploaded PDF/image sources driving this run) resolves
    to the ``SourceIngestion`` row this batch belongs to — passed to every
    processor call so it's stamped onto every inserted/updated/delete-suggested
    node, letting ingestion-completion tracking (see ``SourceIngestionService``)
    find them later.
    """
    from app.db.unit_of_work import UnitOfWork  # noqa: PLC0415
    from app.services.incremental_update_processor_service import (  # noqa: PLC0415
        IncrementalUpdateProcessorService,
    )

    _empty_counts: dict[str, dict[str, int]] = {
        "updates": {"modules": 0, "features": 0, "user_stories": 0},
        "adds": {"modules": 0, "features": 0, "user_stories": 0},
        "deletes": {"modules": 0, "features": 0, "user_stories": 0},
    }

    raw_output = result.get("output", "")
    if not raw_output:
        logger.warning(
            "[INCREMENTAL] _persist_incremental_result: empty output — skipping persistence "
            "project=%s task_db_id=%s",
            project_id,
            task_db_id,
        )
        return _empty_counts

    try:
        if isinstance(raw_output, str):
            # Strip markdown code fences the LLM may emit (e.g. ```json ... ```)
            import re as _re  # noqa: PLC0415

            _fence_match = _re.match(
                r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", raw_output.strip(), _re.DOTALL
            )
            if _fence_match:
                raw_output = _fence_match.group(1).strip()
            payload = json.loads(raw_output)
        else:
            payload = raw_output
    except (json.JSONDecodeError, TypeError) as exc:
        logger.error(
            "[INCREMENTAL] _persist_incremental_result: failed to parse output JSON "
            "project=%s task_db_id=%s error=%s raw_output_preview=%.200s",
            project_id,
            task_db_id,
            exc,
            str(raw_output)[:200],
        )
        return _empty_counts

    if skip_processing:
        await _remap_skip_processing_incremental_module_ids(project_id, payload)

    project_uuid = UUID(project_id)
    processor = IncrementalUpdateProcessorService()
    source_ingestion_id = _resolve_source_ingestion_id(source_ids=source_ids)

    updates = payload.get("updates", [])
    adds = payload.get("adds", [])
    deletes = payload.get("deletes", [])
    flags = payload.get("flags", [])
    persona_glossary_additions = payload.get("persona_glossary_additions", [])
    meeting_summary = payload.get("meeting_summary") or {}
    rfp_flag_map = IncrementalUpdateProcessorService.extract_rfp_flag_map(result)

    logger.info(
        "[INCREMENTAL] Persisting result: project=%s updates=%d adds=%d deletes=%d "
        "flags=%d persona_glossary=%d meeting_summary=%s rfp_flagged=%d",
        project_id,
        len(updates),
        len(adds),
        len(deletes),
        len(flags),
        len(persona_glossary_additions),
        bool(meeting_summary),
        len(rfp_flag_map),
    )

    try:
        await processor.handle_updates(
            project_uuid, updates, rfp_flag_map, source_ingestion_id=source_ingestion_id
        )
    except Exception as exc:
        logger.error(
            "[INCREMENTAL] handle_updates failed: project=%s error=%s",
            project_id,
            exc,
            exc_info=True,
        )

    try:
        await processor.handle_adds(
            project_uuid, adds, rfp_flag_map, source_ingestion_id=source_ingestion_id
        )
    except Exception as exc:
        logger.error(
            "[INCREMENTAL] handle_adds failed: project=%s error=%s",
            project_id,
            exc,
            exc_info=True,
        )

    try:
        await processor.handle_deletes(
            project_uuid, deletes, source_ingestion_id=source_ingestion_id
        )
    except Exception as exc:
        logger.error(
            "[INCREMENTAL] handle_deletes failed: project=%s error=%s",
            project_id,
            exc,
            exc_info=True,
        )

    try:
        with UnitOfWork() as uow:
            await processor.handle_history(
                project_uuid,
                uow,
                updates=updates,
                adds=adds,
                deletes=deletes,
                flags=flags,
                persona_glossary_additions=persona_glossary_additions,
                meeting_summary=meeting_summary,
            )
    except Exception as exc:
        logger.error(
            "[INCREMENTAL] handle_history failed: project=%s error=%s",
            project_id,
            exc,
            exc_info=True,
        )

    return {
        "updates": _count_changed_in_hierarchy(updates),
        "adds": _count_changed_in_hierarchy(adds),
        "deletes": _count_deletes_by_type(deletes),
    }
