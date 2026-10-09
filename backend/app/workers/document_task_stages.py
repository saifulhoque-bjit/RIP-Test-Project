"""Reusable stage helpers for document workflow tasks.

These helpers isolate stage-level orchestration logic from Celery task entry
points so task functions can stay focused on retries, status transitions, and
queue handoffs.
"""

from __future__ import annotations

from collections.abc import Callable
import contextlib
from datetime import UTC, datetime
import inspect
import json
import time
from typing import Any
from uuid import UUID

from app.core.constants import (
    SOURCE_INGESTION_STATUS_FAILED,
    SOURCE_INGESTION_STATUS_READY_FOR_REVIEW,
    SOURCE_INGESTION_STATUS_RUNNING,
    SOURCE_STATUS_FAILED,
    SOURCE_STATUS_READY_FOR_REVIEW,
    SOURCE_STATUS_RUNNING,
    TASK_RETRY_BASE_DELAY_SECONDS,
    TASK_STATUS_CANCELLED,
)
from app.core.enums.activity_type import ActivityType
from app.core.enums.source_ingestion_stage import SourceIngestionStage
from app.core.enums.source_ingestion_status import SourceIngestionStatus
from app.core.llm_errors import NonRetryableLLMError
from app.core.messages import (
    MSG_ACTIVITY_RFP_FEEDBACK_REGENERATED,
    MSG_ACTIVITY_RFP_FEEDBACK_REGENERATION_CANCELLED,
    MSG_ACTIVITY_RFP_FEEDBACK_REGENERATION_FAILED,
    MSG_ACTIVITY_RFP_FEEDBACK_REGENERATION_STARTED,
    MSG_ACTIVITY_RFP_MODULES_GENERATED,
    MSG_ACTIVITY_RFP_MODULES_GENERATION_CANCELLED,
    MSG_ACTIVITY_RFP_MODULES_GENERATION_FAILED,
    MSG_ACTIVITY_RFP_MODULES_GENERATION_STARTED,
    MSG_ACTIVITY_RFP_MODULES_REGENERATED,
    MSG_ACTIVITY_RFP_MODULES_REGENERATION_CANCELLED,
    MSG_ACTIVITY_RFP_MODULES_REGENERATION_FAILED,
    MSG_ACTIVITY_RFP_MODULES_REGENERATION_STARTED,
    MSG_ACTIVITY_RFP_USER_STORIES_GENERATED,
    MSG_ACTIVITY_RFP_USER_STORIES_GENERATION_CANCELLED,
    MSG_ACTIVITY_RFP_USER_STORIES_GENERATION_FAILED,
    MSG_ACTIVITY_RFP_USER_STORIES_GENERATION_STARTED,
    MSG_ACTIVITY_RFP_USER_STORIES_REGENERATED,
    MSG_ACTIVITY_RFP_USER_STORIES_REGENERATION_CANCELLED,
    MSG_ACTIVITY_RFP_USER_STORIES_REGENERATION_FAILED,
    MSG_ACTIVITY_RFP_USER_STORIES_REGENERATION_STARTED,
    SUMMARY_ACTIVITY_RFP_FEEDBACK_REGENERATED,
    SUMMARY_ACTIVITY_RFP_FEEDBACK_REGENERATION_CANCELLED,
    SUMMARY_ACTIVITY_RFP_FEEDBACK_REGENERATION_FAILED,
    SUMMARY_ACTIVITY_RFP_FEEDBACK_REGENERATION_STARTED,
    SUMMARY_ACTIVITY_RFP_MODULES_GENERATED,
    SUMMARY_ACTIVITY_RFP_MODULES_GENERATION_CANCELLED,
    SUMMARY_ACTIVITY_RFP_MODULES_GENERATION_FAILED,
    SUMMARY_ACTIVITY_RFP_MODULES_GENERATION_STARTED,
    SUMMARY_ACTIVITY_RFP_MODULES_REGENERATED,
    SUMMARY_ACTIVITY_RFP_MODULES_REGENERATION_CANCELLED,
    SUMMARY_ACTIVITY_RFP_MODULES_REGENERATION_FAILED,
    SUMMARY_ACTIVITY_RFP_MODULES_REGENERATION_STARTED,
    SUMMARY_ACTIVITY_RFP_USER_STORIES_GENERATED,
    SUMMARY_ACTIVITY_RFP_USER_STORIES_GENERATION_CANCELLED,
    SUMMARY_ACTIVITY_RFP_USER_STORIES_GENERATION_FAILED,
    SUMMARY_ACTIVITY_RFP_USER_STORIES_GENERATION_STARTED,
    SUMMARY_ACTIVITY_RFP_USER_STORIES_REGENERATED,
    SUMMARY_ACTIVITY_RFP_USER_STORIES_REGENERATION_CANCELLED,
    SUMMARY_ACTIVITY_RFP_USER_STORIES_REGENERATION_FAILED,
    SUMMARY_ACTIVITY_RFP_USER_STORIES_REGENERATION_STARTED,
)
from app.models.neo4j.module_feature_model import ChangeType
from app.services.activity_log_service import record_activity, resolve_actor_from_task
from app.services.module_feature_service import ModuleFeatureService
from app.utils.logger import get_logger
from app.workers._task_helpers import (
    _add_run_stage_by_id,
    _add_source_ingestion_error,
    _add_source_ingestion_error_by_id,
    _mark_sources_status_by_project,
    _mark_status,
    _resolve_source_ingestion_id,
    _run_async,
    _update_source_ingestion_fields,
    _update_source_ingestion_fields_by_id,
    cancel_sibling_tasks_on_fatal_llm_error as _cancel_sibling_tasks_on_fatal_llm_error,
    describe_non_retryable_llm_error as _describe_non_retryable_llm_error,
    emit_task_event,
    mark_cancelled_and_check,
    mark_sources_and_ingestion_cancelled,
)

logger = get_logger(__name__)


def _resolve_valid_source_ids(*, all_source_ids: list[str]) -> tuple[list[str], str | None]:
    """Return validated source IDs and an optional error message.

    The function validates UUID format and source existence in Postgres.
    """
    from app.db.unit_of_work import UnitOfWork

    try:
        uuid_list = [UUID(source_id) for source_id in all_source_ids]
    except ValueError as exc:
        return [], str(exc)

    with UnitOfWork() as uow:
        found_ids = {str(source.id) for source in uow.sources.get_many_by_uuids(uuid_list)}

    valid_source_ids = [source_id for source_id in all_source_ids if source_id in found_ids]
    if not valid_source_ids:
        return [], "No valid source IDs were provided."
    return valid_source_ids, None


def _run_parse_document_task(
    *,
    project_id: str,
    source_ids: list[str],
    task_db_id: str | None,
    dispatch_generate_task: Callable[[str, list[str], str | None, bool], None],
    skip_processing: bool = False,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Run phase-1 parsing orchestration and dispatch phase-1b generation.

    Side effects:
    - Updates per-source parsing/failure status via `_mark_sources_failed` and
      `_process_document_sources`.
        - Calls `dispatch_generate_task(project_id, processed_sources, task_db_id, skip_processing)`
      only when at least one source is parsed successfully.

    This helper does not raise on normal failures; it returns a task payload
    with `status=failed` and error details.
    """
    started_at = time.monotonic()
    all_source_ids = list(source_ids)

    try:
        if mark_cancelled_and_check(
            request_id=request_id or task_db_id,
            task_db_id=task_db_id,
            project_id=project_id,
            task_type="source_process",
            stage="source.cancelled",
        ):
            mark_sources_and_ingestion_cancelled(
                source_ids=all_source_ids,
                project_id=project_id,
                task_db_id=task_db_id,
                task_type="source_process",
                stage="source.cancelled",
            )
            return {"project_id": project_id, "status": "cancelled"}

        valid_source_ids, resolve_error = _resolve_valid_source_ids(
            all_source_ids=all_source_ids,
        )
        if resolve_error:
            logger.error(
                "parse_document_task: source resolution failed project_id=%s ids=%s error=%s",
                project_id,
                all_source_ids,
                resolve_error,
            )
            _mark_sources_failed(
                source_ids=all_source_ids,
                error=resolve_error,
                stage="source.source_not_found",
            )
            _fail_ingestion_for_parse_failure(
                project_id=project_id,
                source_ids=all_source_ids,
                errors=[resolve_error],
            )
            return {
                "project_id": project_id,
                "status": SOURCE_STATUS_FAILED,
                "error": resolve_error,
            }

        processed_sources, failed_sources = _process_document_sources(
            project_id=project_id,
            source_ids=valid_source_ids,
            task_db_id=task_db_id,
        )

        if not processed_sources:
            _fail_ingestion_for_parse_failure(
                project_id=project_id,
                source_ids=valid_source_ids,
                errors=[failed["error"] for failed in failed_sources],
            )
            return {
                "project_id": project_id,
                "status": SOURCE_STATUS_FAILED,
                "processed_sources": 0,
                "failed_sources": failed_sources,
                "error": "All source processing failed.",
            }

        dispatch_generate_task(project_id, processed_sources, task_db_id, skip_processing)

        duration_ms = int((time.monotonic() - started_at) * 1000)
        return {
            "project_id": project_id,
            "status": SOURCE_STATUS_RUNNING,
            "processed_sources": len(processed_sources),
            "failed_sources": failed_sources,
            "duration_ms": duration_ms,
        }
    except Exception as exc:
        logger.exception(
            "parse_document_task: unexpected top-level failure project_id=%s: %s",
            project_id,
            exc,
        )
        _mark_sources_failed(
            source_ids=all_source_ids,
            error=f"Unexpected task failure: {exc}",
            stage="source.parsing.unexpected_failure",
            task_db_id=task_db_id,
            project_id=project_id,
        )
        _fail_ingestion_for_parse_failure(
            project_id=project_id,
            source_ids=all_source_ids,
            errors=[f"Unexpected task failure: {exc}"],
        )
        return {
            "project_id": project_id,
            "status": SOURCE_STATUS_FAILED,
            "error": str(exc),
        }


def _fail_ingestion_for_parse_failure(
    *,
    project_id: str,
    source_ids: list[str],
    errors: list[str],
) -> None:
    """Mark the run's SourceIngestion failed with the parse-phase reason(s).

    A parse-phase failure otherwise only reaches the Source rows, leaving the
    ingestion ``running`` until ``fail_stale_running_ingestions`` fails it
    with the generic stale-run message — so the Pipelines tab showed
    "failed" with no specific reason and no failure notification was sent.
    Mirrors the generation-phase failure path: records each distinct error
    on ``errors`` and notifies the project owner. Never raises.
    """
    distinct_errors = [error for error in dict.fromkeys(errors) if error]
    _update_source_ingestion_fields(
        source_ids=source_ids,
        fields={"status": SourceIngestionStatus.FAILED.value},
    )
    for error in distinct_errors:
        _add_source_ingestion_error(source_ids=source_ids, error=error)
    _notify_module_feature_status(
        project_id=project_id,
        status=SourceIngestionStatus.FAILED.value,
        is_regeneration=False,
        error="; ".join(distinct_errors) or None,
    )


def _mark_sources_failed(
    *,
    source_ids: list[str],
    error: str,
    stage: str,
    task_db_id: str | None = None,
    project_id: str | None = None,
) -> None:
    """Best-effort status update helper for source failure transitions."""
    for source_id in source_ids:
        try:
            _mark_status(
                source_id=source_id,
                status=SOURCE_STATUS_FAILED,
                error=error,
                stage=stage,
                task_db_id=task_db_id,
                project_id=project_id,
            )
        except Exception:
            pass


def _process_document_sources(
    *,
    project_id: str,
    source_ids: list[str],
    task_db_id: str | None,
) -> tuple[list[str], list[dict[str, str]]]:
    """Parse each source and collect success/failure outputs."""
    processed_sources: list[str] = []
    failed_sources: list[dict[str, str]] = []

    for source_id in source_ids:
        _mark_status(
            source_id=source_id,
            status=SOURCE_STATUS_RUNNING,
            stage="source.processing.started",
            meta={"project_id": project_id},
            task_db_id=task_db_id,
            project_id=project_id,
        )

        try:
            _run_async(_async_build_fragments(source_id=source_id))
            processed_sources.append(source_id)
        except Exception as exc:
            logger.exception(
                "parse_document_task: source processing failed source_id=%s error=%s",
                source_id,
                exc,
            )
            _mark_status(
                source_id=source_id,
                status=SOURCE_STATUS_FAILED,
                error=str(exc),
                stage="source.failed",
                task_db_id=task_db_id,
                project_id=project_id,
            )
            failed_sources.append({"source_id": source_id, "error": str(exc)})

    return processed_sources, failed_sources


def _mark_sources_status(
    *,
    source_ids: list[str],
    status: str,
    stage: str,
    project_id: str,
    task_db_id: str | None = None,
    task_type: str = "source_process",
    meta: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Apply a status transition to all provided sources."""
    for source_id in source_ids:
        _mark_status(
            source_id=source_id,
            status=status,
            stage=stage,
            meta=meta,
            error=error,
            task_db_id=task_db_id,
            task_type=task_type,
            project_id=project_id,
        )


def _add_run_stage_by_source_ids(source_ids: list[str], stage: SourceIngestionStage) -> None:
    """Best-effort: append *stage* to the SourceIngestion.stages array for *source_ids*.

    Mirrors ``_add_source_code_stage`` in ``app/workers/source_code_task.py``.
    Never raises: a bookkeeping failure here must not abort the generation
    pipeline.
    """
    from app.db.unit_of_work import UnitOfWork
    from app.services.source_ingestion_service import SourceIngestionService

    try:
        with UnitOfWork() as uow:
            SourceIngestionService.add_stage_by_source_ids(
                uow, [UUID(sid) for sid in source_ids], stage
            )
            uow.commit()
    except Exception:
        logger.warning(
            "_add_run_stage_by_source_ids: failed to tag stage=%s for source_ids=%s",
            stage.value,
            source_ids,
            exc_info=True,
        )


def _notify_module_feature_status(
    *,
    project_id: str,
    status: str,
    is_regeneration: bool,
    modules_added: int | None = None,
    modules_updated: int | None = None,
    features_added: int | None = None,
    features_updated: int | None = None,
    error: str | None = None,
    error_reason: str | None = None,
) -> None:
    """Best-effort: notify the project owner about a module/feature status change.

    Fires for the ``running`` (started), ``ready_for_review`` (completed),
    and ``failed`` transitions for both the initial generation and
    regeneration flows. Both flows resolve completion to the same
    ``ready_for_review`` status now (the module/feature-vs-user-story phase
    is distinguished via ``SourceIngestionStage``/``stages``, not the status value) —
    ``is_regeneration`` picks the wording: the initial generation flow is
    awaiting the user's module/feature approval, while regeneration is
    simply ready for review. The initial generation flow has no update
    concept (every module/feature is brand new), so it only ever passes
    ``modules_added``/``features_added``. Never raises — a notification
    failure must not abort the generation pipeline.
    """
    from app.core.enums.notification_type import NotificationType  # noqa: PLC0415
    from app.db.unit_of_work import UnitOfWork  # noqa: PLC0415
    from app.services.notification_service import publish_notification  # noqa: PLC0415

    action = "Regeneration" if is_regeneration else "Generation"
    total_modules = (modules_added or 0) + (modules_updated or 0)
    total_features = (features_added or 0) + (features_updated or 0)
    try:
        with UnitOfWork() as uow:
            project = uow.projects.get_by_uuid(UUID(project_id))
        if project is None or project.owner_id is None:
            return

        if status == SourceIngestionStatus.RUNNING.value:
            title = f"Module & Feature {action} Started"
            message = f'Module and feature {action.lower()} has started for "{project.name}".'
            notification_type = NotificationType.INFO
        elif status == SourceIngestionStatus.READY_FOR_REVIEW.value:
            if is_regeneration:
                title = "Modules & Features Ready for Review"
                message = (
                    f"{total_modules} module(s) and {total_features} feature(s) are ready "
                    f'for review in "{project.name}".'
                )
            else:
                title = "Modules & Features Ready for Approval"
                message = (
                    f"{total_modules} module(s) and {total_features} feature(s) are ready "
                    f'for approval in "{project.name}".'
                )
            notification_type = NotificationType.SUCCESS
        elif status == SourceIngestionStatus.FAILED.value:
            title = f"Module & Feature {action} Failed"
            message = f'Module and feature {action.lower()} failed for "{project.name}".'
            if error:
                message += f" Error: {error[:200]}"
            notification_type = NotificationType.ERROR
        else:
            return

        publish_notification(
            user_id=project.owner_id,
            title=title,
            message=message,
            notification_type=notification_type,
            data={
                "project_id": project_id,
                "status": status,
                "tot_modules": modules_added,
                "tot_features": features_added,
                "tot_modules_updated": modules_updated,
                "tot_features_updated": features_updated,
                "error_reason": error_reason,
            },
        )
    except Exception:
        logger.warning(
            "_notify_module_feature_status: failed to notify project_id=%s status=%s",
            project_id,
            status,
            exc_info=True,
        )


def _notify_user_story_status(
    *,
    project_id: str,
    status: str,
    total_user_stories: int | None = None,
    error: str | None = None,
    error_reason: str | None = None,
) -> None:
    """Best-effort: notify the project owner about a user-story generation status change.

    Fires for the ``running`` (started), ``completed``, and ``failed``
    transitions of the initial (module-approval auto-chained) user-story
    generation flow only (``task_type == "story_generation"`` in
    ``_run_user_story_backlog_task``) — plain regeneration uses
    :func:`_notify_user_story_regeneration_status` instead. Never raises — a
    notification failure must not abort the generation pipeline.
    """
    from app.core.enums.notification_type import NotificationType  # noqa: PLC0415
    from app.db.unit_of_work import UnitOfWork  # noqa: PLC0415
    from app.services.notification_service import publish_notification  # noqa: PLC0415

    try:
        with UnitOfWork() as uow:
            project = uow.projects.get_by_uuid(UUID(project_id))
        if project is None or project.owner_id is None:
            return

        if status == SourceIngestionStatus.RUNNING.value:
            title = "User Story Generation Started"
            message = f'User story generation has started for "{project.name}".'
            notification_type = NotificationType.INFO
        elif status == SourceIngestionStatus.READY_FOR_REVIEW.value:
            title = "User Stories Ready for Review"
            message = (
                f'{total_user_stories} user story(ies) have been generated for "{project.name}".'
            )
            notification_type = NotificationType.SUCCESS
        elif status == SourceIngestionStatus.FAILED.value:
            title = "User Story Generation Failed"
            message = f'User story generation failed for "{project.name}".'
            if error:
                message += f" Error: {error[:200]}"
            notification_type = NotificationType.ERROR
        else:
            return

        publish_notification(
            user_id=project.owner_id,
            title=title,
            message=message,
            notification_type=notification_type,
            data={
                "project_id": project_id,
                "status": status,
                "total_user_stories": total_user_stories,
                "error_reason": error_reason,
            },
        )
    except Exception:
        logger.warning(
            "_notify_user_story_status: failed to notify project_id=%s status=%s",
            project_id,
            status,
            exc_info=True,
        )


def _notify_user_story_regeneration_status(
    *,
    project_id: str,
    status: str,
    total_user_stories: int | None = None,
    error: str | None = None,
    error_reason: str | None = None,
) -> None:
    """Best-effort: notify the project owner and every assigned member about
    a plain (non-feedback) user-story regeneration's start or outcome.

    Fires for the ``running`` (started), ``ready_for_review`` (completed),
    and ``failed`` transitions of ``task_type == "story_regeneration"`` in
    ``_run_user_story_backlog_task``. Never raises — a notification failure
    must not abort the regeneration pipeline.
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
            title = "User Story Regeneration Started"
            message = f'User story regeneration has started for "{project.name}".'
            notification_type = NotificationType.INFO
        elif status == SourceIngestionStatus.READY_FOR_REVIEW.value:
            title = "User Stories Regenerated"
            message = (
                f"{total_user_stories or 0} user story(ies) have been regenerated and are "
                f'ready for review in "{project.name}".'
            )
            notification_type = NotificationType.SUCCESS
        elif status == SourceIngestionStatus.FAILED.value:
            title = "User Story Regeneration Failed"
            message = f'User story regeneration failed for "{project.name}".'
            if error:
                message += f" Error: {error[:200]}"
            notification_type = NotificationType.ERROR
        else:
            return

        for user_id in recipient_ids:
            try:
                publish_notification(
                    user_id=user_id,
                    title=title,
                    message=message,
                    notification_type=notification_type,
                    data={
                        "project_id": project_id,
                        "status": status,
                        "total_user_stories": total_user_stories,
                        "error_reason": error_reason,
                    },
                )
            except Exception:
                logger.warning(
                    "_notify_user_story_regeneration_status: failed to notify project_id=%s "
                    "user_id=%s",
                    project_id,
                    user_id,
                    exc_info=True,
                )
    except Exception:
        logger.warning(
            "_notify_user_story_regeneration_status: failed for project_id=%s status=%s",
            project_id,
            status,
            exc_info=True,
        )


def _run_source_module_feature_phase(
    *,
    project_id: str,
    source_ids: list[str],
    skip_processing: bool = False,
    request_id: str | None = None,
) -> tuple[list[dict[str, Any]], Any]:
    """Collect source fragments then run module/feature generation."""
    fragments = _run_async(_async_collect_fragments(source_ids=source_ids))
    module_feature_output, _stored_modules = _run_module_feature_phase(
        project_id=project_id,
        fragments=fragments,
        skip_processing=skip_processing,
        request_id=request_id,
        source_ingestion_id=_resolve_source_ingestion_id(source_ids=source_ids),
    )
    return fragments, module_feature_output


def _run_source_module_feature_generation_task(
    *,
    self,
    project_id: str,
    source_ids: list[str],
    task_db_id: str | None = None,
    skip_processing: bool = False,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Run source module-feature generation with status transitions and retries.

    Behavior:
    - Marks all sources as running before generation.
    - Collects fragments and runs the module-feature pipeline.
    - Marks all sources as completed on success, failed on terminal error.
    - Raises `self.retry(...)` for retriable errors.
    """
    if mark_cancelled_and_check(
        request_id=request_id or task_db_id,
        task_db_id=task_db_id,
        project_id=project_id,
        task_type="source_process",
        stage="modules_and_features.cancelled",
    ):
        mark_sources_and_ingestion_cancelled(
            source_ids=source_ids,
            project_id=project_id,
            task_db_id=task_db_id,
            task_type="source_process",
            stage="modules_and_features.cancelled",
        )
        record_activity(
            project_id=UUID(project_id),
            activity_type=ActivityType.RFP_MODULES_GENERATION_CANCELLED,
            summary=SUMMARY_ACTIVITY_RFP_MODULES_GENERATION_CANCELLED,
            message=MSG_ACTIVITY_RFP_MODULES_GENERATION_CANCELLED,
            actor_user_id=resolve_actor_from_task(task_db_id),
            data={"source_ids": source_ids},
        )
        return {"project_id": project_id, "status": "cancelled"}

    try:
        _mark_sources_status(
            source_ids=source_ids,
            status=SOURCE_STATUS_RUNNING,
            stage="modules_and_features.generation.started",
            project_id=project_id,
            task_db_id=task_db_id,
            meta={"project_id": project_id},
        )
        _update_source_ingestion_fields(
            source_ids=source_ids,
            fields={"mod_fea_gen_started_at": datetime.now(UTC)},
        )
        _notify_module_feature_status(
            project_id=project_id,
            status=SourceIngestionStatus.RUNNING.value,
            is_regeneration=False,
        )
        record_activity(
            project_id=UUID(project_id),
            activity_type=ActivityType.RFP_MODULES_GENERATION_STARTED,
            summary=SUMMARY_ACTIVITY_RFP_MODULES_GENERATION_STARTED,
            message=MSG_ACTIVITY_RFP_MODULES_GENERATION_STARTED,
            actor_user_id=resolve_actor_from_task(task_db_id),
            data={"source_ids": source_ids},
        )

        fragments, module_feature_output = _run_source_module_feature_phase(
            project_id=project_id,
            source_ids=source_ids,
            skip_processing=skip_processing,
            request_id=request_id or task_db_id,
        )

        if (
            isinstance(module_feature_output, dict)
            and module_feature_output.get("status") == "CANCELLED"
        ):
            logger.info(
                "generate_modules_and_features_task: cancelled mid-generation project_id=%s",
                project_id,
            )
            emit_task_event(
                task_db_id=task_db_id,
                project_id=project_id,
                task_type="source_process",
                status=TASK_STATUS_CANCELLED,
                stage="modules_and_features.cancelled",
                progress=100,
            )
            mark_sources_and_ingestion_cancelled(
                source_ids=source_ids,
                project_id=project_id,
                task_db_id=task_db_id,
                task_type="source_process",
                stage="modules_and_features.cancelled",
            )
            return {"project_id": project_id, "status": "cancelled"}

        total_modules = len(module_feature_output.feature_inventory)
        total_features = _count_total_features(module_feature_output)

        _mark_sources_status(
            source_ids=source_ids,
            status=SOURCE_STATUS_READY_FOR_REVIEW,
            stage="modules_and_features.generation.completed",
            project_id=project_id,
            task_db_id=task_db_id,
            meta={
                "total_fragments": len(fragments),
                "total_modules": total_modules,
            },
        )
        _update_source_ingestion_fields(
            source_ids=source_ids,
            fields={
                "mod_fea_gen_completed_at": datetime.now(UTC),
                "tot_modules": total_modules,
                "tot_features": total_features,
                "status": SourceIngestionStatus.READY_FOR_REVIEW.value,
            },
        )
        _add_run_stage_by_source_ids(
            source_ids, SourceIngestionStage.MODULE_FEATURE_READY_FOR_REVIEW
        )
        _notify_module_feature_status(
            project_id=project_id,
            status=SourceIngestionStatus.READY_FOR_REVIEW.value,
            is_regeneration=False,
            modules_added=total_modules,
            features_added=total_features,
        )
        record_activity(
            project_id=UUID(project_id),
            activity_type=ActivityType.RFP_MODULES_GENERATED,
            summary=SUMMARY_ACTIVITY_RFP_MODULES_GENERATED,
            message=MSG_ACTIVITY_RFP_MODULES_GENERATED.format(
                total_modules=total_modules, total_features=total_features
            ),
            actor_user_id=resolve_actor_from_task(task_db_id),
            data={
                "total_modules": total_modules,
                "total_features": total_features,
                "source_ids": source_ids,
            },
        )

        return {
            "project_id": project_id,
            "status": SOURCE_STATUS_READY_FOR_REVIEW,
            "processed_sources": len(source_ids),
            "total_fragments": len(fragments),
            "total_modules": total_modules,
            "total_features": total_features,
        }
    except NonRetryableLLMError as exc:
        classification = exc.classification
        error_detail = _describe_non_retryable_llm_error(classification)
        logger.error(
            "generate_modules_and_features_task: non-retryable LLM error project_id=%s "
            "reason=%s",
            project_id,
            classification.reason.value,
        )
        _mark_sources_status(
            source_ids=source_ids,
            status=SOURCE_STATUS_FAILED,
            error=error_detail,
            stage="modules_and_features.generation.failed",
            project_id=project_id,
            task_db_id=task_db_id,
        )
        _update_source_ingestion_fields(
            source_ids=source_ids,
            fields={"status": SourceIngestionStatus.FAILED.value},
        )
        _add_source_ingestion_error(source_ids=source_ids, error=error_detail)
        _notify_module_feature_status(
            project_id=project_id,
            status=SourceIngestionStatus.FAILED.value,
            is_regeneration=False,
            error=classification.user_message,
            error_reason=classification.reason.value,
        )
        record_activity(
            project_id=UUID(project_id),
            activity_type=ActivityType.RFP_MODULES_GENERATION_FAILED,
            summary=SUMMARY_ACTIVITY_RFP_MODULES_GENERATION_FAILED,
            message=MSG_ACTIVITY_RFP_MODULES_GENERATION_FAILED.format(error=error_detail[:200]),
            actor_user_id=resolve_actor_from_task(task_db_id),
            data={
                "source_ids": source_ids,
                "error": error_detail,
                "llm_error_reason": classification.reason.value,
            },
        )
        _cancel_sibling_tasks_on_fatal_llm_error(
            request_id=request_id, task_db_id=task_db_id, project_id=project_id
        )
        return {
            "project_id": project_id,
            "status": SOURCE_STATUS_FAILED,
            "error": error_detail,
        }
    except Exception as exc:
        logger.exception(
            "generate_modules_and_features_task: failed project_id=%s error=%s",
            project_id,
            exc,
        )
        if self.request.retries >= self.max_retries:
            _mark_sources_status(
                source_ids=source_ids,
                status=SOURCE_STATUS_FAILED,
                error=str(exc),
                stage="modules_and_features.generation.failed",
                project_id=project_id,
                task_db_id=task_db_id,
            )
            _update_source_ingestion_fields(
                source_ids=source_ids,
                fields={"status": SourceIngestionStatus.FAILED.value},
            )
            _add_source_ingestion_error(source_ids=source_ids, error=str(exc))
            _notify_module_feature_status(
                project_id=project_id,
                status=SourceIngestionStatus.FAILED.value,
                is_regeneration=False,
                error=str(exc),
            )
            record_activity(
                project_id=UUID(project_id),
                activity_type=ActivityType.RFP_MODULES_GENERATION_FAILED,
                summary=SUMMARY_ACTIVITY_RFP_MODULES_GENERATION_FAILED,
                message=MSG_ACTIVITY_RFP_MODULES_GENERATION_FAILED.format(error=str(exc)[:200]),
                actor_user_id=resolve_actor_from_task(task_db_id),
                data={"source_ids": source_ids, "error": str(exc)},
            )
            return {
                "project_id": project_id,
                "status": SOURCE_STATUS_FAILED,
                "error": str(exc),
            }

        retry_num = self.request.retries + 1
        countdown = TASK_RETRY_BASE_DELAY_SECONDS * (2**self.request.retries)
        logger.info(
            "generate_modules_and_features_task: scheduling retry %d in %ds project_id=%s",
            retry_num,
            countdown,
            project_id,
        )
        _mark_sources_status(
            source_ids=source_ids,
            status=SOURCE_STATUS_RUNNING,
            error=f"Attempt {retry_num} failed: {exc}. Retrying in {countdown}s...",
            stage=f"modules_and_features.generation.retry.{retry_num}",
            project_id=project_id,
            task_db_id=task_db_id,
        )
        raise self.retry(exc=exc, countdown=countdown)


def _load_module_feature_output(payload: Any):
    from app.schemas.rfp_pipeline_v2_graph_schema import ModuleFeatureOutput

    normalized_payload = payload.get("output", payload) if isinstance(payload, dict) else payload
    if isinstance(normalized_payload, str):
        return ModuleFeatureOutput.model_validate_json(normalized_payload)
    return ModuleFeatureOutput.model_validate(normalized_payload)


def _count_total_features(module_feature_output: Any) -> int:
    return sum(len(module.features) for module in module_feature_output.feature_inventory)


def _count_module_feature_regeneration_changes(stored_modules: list[Any]) -> dict[str, int]:
    """Split a regeneration run's persisted modules/features into added vs. updated counts.

    ``stored_modules`` is the list ``ModuleFeatureService.upsert_modules_and_features_v2``
    returns — each module (and nested feature) carries the ``feedback_change_type``
    resolved against its existing node (ADDED for a brand-new module/feature
    code, UPDATED when content changed, or ``None`` when nothing changed).
    Unlike first-time generation, a regeneration's output inventory mixes
    genuinely new modules/features with existing ones just being revised, so
    the total inventory size cannot stand in for "added" — this tallies each
    bucket from the actual persisted `feedback_change_type`.
    """
    modules_added = sum(
        1 for module in stored_modules if module.feedback_change_type == ChangeType.ADDED
    )
    modules_updated = sum(
        1 for module in stored_modules if module.feedback_change_type == ChangeType.UPDATED
    )
    features_added = sum(
        1
        for module in stored_modules
        for feature in module.features
        if feature.feedback_change_type == ChangeType.ADDED
    )
    features_updated = sum(
        1
        for module in stored_modules
        for feature in module.features
        if feature.feedback_change_type == ChangeType.UPDATED
    )
    return {
        "modules_added": modules_added,
        "modules_updated": modules_updated,
        "features_added": features_added,
        "features_updated": features_updated,
    }


async def _async_build_fragments(*, source_id: str) -> list:
    from app.db.unit_of_work import UnitOfWork
    from app.services.document_parser_service import DocumentService
    from app.services.fragment_service import FragmentService

    with UnitOfWork() as uow:
        source = uow.sources.get_by_uuid(UUID(source_id))
        source_name = source.original_name if source is not None else "source"
        source_mime = (source.mime_type or "") if source is not None else ""
        source_file_type = (source.file_type or "") if source is not None else ""

    logger.info(
        "_async_build_fragments: parsing source_id=%s name=%s mime=%s",
        source_id,
        source_name,
        source_mime,
    )

    parse_result = await DocumentService().parse_and_format_for_alternate_pipeline(
        source_id=UUID(source_id)
    )
    chunks = _apply_source_type_fallback(
        chunks=parse_result.chunks,
        source_file_type=source_file_type,
    )

    return await FragmentService().create_fragments_from_chunks(
        source_id=UUID(source_id),
        chunks=chunks,
    )


def _apply_source_type_fallback(
    *, chunks: list[dict[str, Any]], source_file_type: str
) -> list[dict[str, Any]]:
    """Fill missing chunk source_type values from sources.file_type."""
    if not source_file_type:
        return chunks

    return [
        (
            {
                **chunk,
                "source_type": source_file_type,
            }
            if isinstance(chunk, dict)
            and (
                chunk.get("source_type") is None
                or (
                    isinstance(chunk.get("source_type"), str)
                    and not chunk.get("source_type").strip()
                )
            )
            else chunk
        )
        for chunk in chunks
    ]


async def _async_collect_fragments(*, source_ids: list[str]) -> list[dict[str, Any]]:
    from app.db.unit_of_work import UnitOfWork
    from app.services.fragment_service import FragmentService

    rows: list[dict[str, Any]] = []
    with UnitOfWork() as uow:
        for source_id in source_ids:
            fragments_response = await FragmentService().list_fragments(
                source_id=UUID(source_id),
                uow=uow,
            )
            rows.extend(
                {
                    "id": fragment.id,
                    "source_id": str(fragment.source_id),
                    "source_type": fragment.source_type,
                    "frag_type": fragment.frag_type,
                    "content": fragment.content,
                    "bbox": [
                        {
                            "page": entry.page,
                            "bbox": {
                                "x": entry.bbox.x,
                                "y": entry.bbox.y,
                                "w": entry.bbox.w,
                                "h": entry.bbox.h,
                            },
                            "confidence": entry.confidence,
                        }
                        for entry in fragment.bbox
                    ],
                }
                for fragment in fragments_response.fragments
            )
    return rows


def _get_project_llm_options(*, project_id: str) -> dict[str, Any]:
    """Load project-level LLM selection, plus the tenant's stored API key for
    that provider, as pipeline options.

    The API key is only included when the project's tenant has an *active*
    ``tenant_llm_providers`` row for the project's ``llm_provider`` — an
    inactive (admin-disabled) provider is treated the same as no key at all,
    so pipeline calls fall back to whatever default the LLM client resolves
    to (see ``get_llm`` call sites reading ``options.get("llm_api_key")``).
    """
    from app.db.unit_of_work import UnitOfWork
    from app.services.tenant_llm_provider_service import TenantLLMProviderService

    with UnitOfWork() as uow:
        project = uow.projects.get_by_uuid(UUID(project_id))
        if project is None:
            return {}

        options: dict[str, Any] = {
            "llm_provider": project.llm_provider,
            "llm_model": project.llm_model,
        }

        if project.tenant_id and project.llm_provider:
            api_key = TenantLLMProviderService(uow).get_active_api_key(
                project.tenant_id, project.llm_provider
            )
            if api_key:
                options["llm_api_key"] = api_key

        return options


def _extract_business_requirements_from_module_feature_output(
    module_feature_output: Any,
) -> list[dict] | None:
    """Extract business_requirements list from module-feature output payload."""
    raw = getattr(module_feature_output, "business_requirements", None)
    if raw is None and isinstance(module_feature_output, dict):
        raw = module_feature_output.get("business_requirements")
    if raw is None:
        return None
    result = []
    for item in raw:
        if hasattr(item, "model_dump"):
            result.append(item.model_dump(mode="json"))
        elif isinstance(item, dict):
            result.append(item)
    return result


def _extract_exclusions_from_module_feature_output(module_feature_output: Any) -> list[str] | None:
    """Extract exclusions list from module-feature output payload."""
    raw = getattr(module_feature_output, "exclusions", None)
    if raw is None and isinstance(module_feature_output, dict):
        raw = module_feature_output.get("exclusions")
    if raw is None:
        return None
    return [str(item) for item in raw if item is not None]


def _parse_backlog_payload(backlog_items: Any) -> Any:
    payload = (
        backlog_items.get("output", backlog_items)
        if isinstance(backlog_items, dict)
        else backlog_items
    )
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except (TypeError, ValueError):
            return None
    return payload


def _normalize_persona_entry(item: Any) -> dict[str, str] | None:
    if hasattr(item, "model_dump"):
        item = item.model_dump(mode="json")
    if not isinstance(item, dict):
        return None

    persona = str(item.get("persona") or "").strip()
    description = str(item.get("description") or "").strip()
    if not persona and not description:
        return None

    return {
        "persona": persona,
        "description": description,
    }


def _extract_persona_glossary(backlog_items: Any) -> list[dict[str, str]] | None:
    """Extract persona glossary entries from backlog output payload."""
    payload = _parse_backlog_payload(backlog_items)
    if payload is None:
        return None

    raw_personas = None
    if isinstance(payload, dict):
        raw_personas = payload.get("persona_glossary")
    else:
        raw_personas = getattr(payload, "persona_glossary", None)

    if raw_personas is None:
        return None

    personas: list[dict[str, str]] = []
    for item in raw_personas:
        normalized = _normalize_persona_entry(item)
        if normalized is not None:
            personas.append(normalized)

    return personas


def _update_project_metadata_best_effort(
    *,
    project_id: str,
    persona_glossary: list[dict[str, str]] | None = None,
    business_requirements: list[dict] | None = None,
    exclusions: list[str] | None = None,
) -> None:
    """Best-effort sync of project-level metadata to Neo4j ProjectMetadata node."""
    if persona_glossary is None and business_requirements is None and exclusions is None:
        return

    try:
        from app.services.project_graph_service import ProjectGraphService

        ProjectGraphService().update_project_metadata(
            project_id=UUID(project_id),
            persona_glossary=persona_glossary,
            business_requirements=business_requirements,
            exclusions=exclusions,
        )
    except Exception as exc:
        logger.warning(
            "project metadata sync failed (best-effort): project_id=%s error=%s",
            project_id,
            exc,
        )


def _run_module_feature_phase(
    *,
    project_id: str,
    fragments: list[dict[str, Any]],
    modules_and_features: str | dict[str, Any] = "",
    feedback: str = "",
    skip_processing: bool = False,
    request_id: str | None = None,
    is_regeneration: bool = False,
    source_ingestion_id: str | None = None,
) -> tuple[Any, list[Any]]:
    """Invoke the module/feature AI pipeline and persist the result.

    Returns ``(module_feature_output, stored_modules)`` — ``stored_modules``
    is the list of ``ModuleModel`` objects actually persisted (each carrying
    its own and its nested features' ``feedback_change_type``), so a
    regeneration caller can tell how many were newly added vs. updated
    instead of treating the AI output's full inventory size as "added".
    """
    from app.services.rfp_pipeline_v2_graph_service.graph_module_feature import run_module_feature

    if not isinstance(modules_and_features, str):
        modules_and_features = json.dumps(modules_and_features, default=str)

    logger.info("_run_module_feature_phase feedback=%s", feedback)

    options = _get_project_llm_options(project_id=project_id)
    if request_id:
        options["request_id"] = request_id

    run_module_feature_kwargs: dict[str, Any] = {
        "fragments": json.dumps(fragments, default=str),
        "modules_and_features": modules_and_features,
        "feedback": feedback,
        "skip_processing": skip_processing,
    }
    if "options" in inspect.signature(run_module_feature).parameters:
        run_module_feature_kwargs["options"] = options

    _debug_kwargs: dict[str, Any] = {
        **run_module_feature_kwargs,
        "fragments": fragments,
        "modules_and_features": json.loads(modules_and_features)
        if modules_and_features
        else modules_and_features,
    }

    # START: Testing and debugging: dump the kwargs to a JSON file for inspection
    from app.utils.common import dump_json_debug  # noqa: PLC0415

    dump_json_debug(
        "module_feature_generation_kwargs.json", _debug_kwargs, base_dir="temp/modules_and_features"
    )
    # END: Testing and debugging: dump the kwargs to a JSON file for inspection

    module_feature_result = _run_async(run_module_feature(**run_module_feature_kwargs))

    # START: Testing and debugging: dump the module-feature output to a JSON file for inspection
    _debug_module_feature_result = module_feature_result
    if isinstance(module_feature_result, dict) and isinstance(
        module_feature_result.get("output"), str
    ):
        with contextlib.suppress(ValueError):
            _debug_module_feature_result = {
                **module_feature_result,
                "output": json.loads(module_feature_result["output"]),
            }

    dump_json_debug(
        "module_feature_generation_output.json",
        _debug_module_feature_result,
        base_dir="temp/modules_and_features",
    )
    # END: Testing and debugging: dump the module-feature output to a JSON file for inspection

    if (
        isinstance(module_feature_result, dict)
        and module_feature_result.get("status") == "CANCELLED"
    ):
        logger.info("_run_module_feature_phase: cancelled mid-generation project_id=%s", project_id)
        return {"status": "CANCELLED"}, []

    if isinstance(module_feature_result, dict) and module_feature_result.get("status") == "failed":
        raise RuntimeError(module_feature_result.get("error") or "module/feature generation failed")

    module_feature_output = _load_module_feature_output(module_feature_result)

    from app.core import task_control  # noqa: PLC0415

    if request_id and task_control.is_request_cancelled(request_id):
        logger.info(
            "_run_module_feature_phase: cancelled after generation, before persistence project_id=%s",
            project_id,
        )
        return {"status": "CANCELLED"}, []

    _update_project_metadata_best_effort(
        project_id=project_id,
        business_requirements=_extract_business_requirements_from_module_feature_output(
            module_feature_output
        ),
        exclusions=_extract_exclusions_from_module_feature_output(module_feature_output),
    )

    stored_modules: list[Any] = []
    if module_feature_output.feature_inventory:
        stored_modules = _run_async(
            ModuleFeatureService().upsert_modules_and_features_v2(
                project_id=UUID(project_id),
                module_feature_skeleton=module_feature_output,
                is_regeneration=is_regeneration,
                source_ingestion_id=source_ingestion_id,
            )
        )

    return module_feature_output, stored_modules


def _run_module_feature_regeneration_task(
    *,
    self,
    project_id: str,
    source_ids: list[str],
    fragments: list[dict[str, Any]],
    modules_and_features: str | dict[str, Any],
    feedback: str,
    task_db_id: str | None = None,
    skip_processing: bool = False,
    ingestion_id: str | None = None,
) -> dict[str, Any]:
    """Run module-feature regeneration with unified events and retry handling.

    Mirrors `_run_source_module_feature_generation_task`'s status/realtime
    tracking — per-source status marks plus SourceIngestion bookkeeping
    (started_at/completed_at, counts, status) — while keeping this task's
    own stage labels and task_type ("module_regeneration"). Raises
    `self.retry(...)` when retries remain.

    `ingestion_id` is the dedicated SourceIngestion row created for this
    feedback-driven regeneration request (one per request, never shared with
    another run) — its status is updated directly by id rather than by
    tagging whichever ingestion the project's sources happen to be linked to.
    """
    if mark_cancelled_and_check(
        request_id=task_db_id,
        task_db_id=task_db_id,
        project_id=project_id,
        task_type="module_regeneration",
        stage="modules_and_features.cancelled",
    ):
        mark_sources_and_ingestion_cancelled(
            source_ids=source_ids,
            project_id=project_id,
            task_db_id=task_db_id,
            task_type="module_regeneration",
            stage="modules_and_features.cancelled",
            ingestion_id=ingestion_id,
        )
        record_activity(
            project_id=UUID(project_id),
            activity_type=ActivityType.RFP_MODULES_REGENERATION_CANCELLED,
            summary=SUMMARY_ACTIVITY_RFP_MODULES_REGENERATION_CANCELLED,
            message=MSG_ACTIVITY_RFP_MODULES_REGENERATION_CANCELLED,
            actor_user_id=resolve_actor_from_task(task_db_id),
            data={"source_ids": source_ids, "ingestion_id": ingestion_id},
        )
        return {"project_id": project_id, "status": "cancelled"}

    try:
        _mark_sources_status(
            source_ids=source_ids,
            status=SOURCE_INGESTION_STATUS_RUNNING,
            stage="modules_and_features.regeneration.started",
            project_id=project_id,
            task_db_id=task_db_id,
            task_type="module_regeneration",
            meta={"project_id": project_id, "source_ids": source_ids},
        )
        _update_source_ingestion_fields_by_id(
            ingestion_id=ingestion_id,
            fields={
                "started_at": datetime.now(UTC),
                "status": SourceIngestionStatus.RUNNING.value,
            },
        )
        _notify_module_feature_status(
            project_id=project_id,
            status=SourceIngestionStatus.RUNNING.value,
            is_regeneration=True,
        )
        record_activity(
            project_id=UUID(project_id),
            activity_type=ActivityType.RFP_MODULES_REGENERATION_STARTED,
            summary=SUMMARY_ACTIVITY_RFP_MODULES_REGENERATION_STARTED,
            message=MSG_ACTIVITY_RFP_MODULES_REGENERATION_STARTED,
            actor_user_id=resolve_actor_from_task(task_db_id),
            data={"source_ids": source_ids, "ingestion_id": ingestion_id},
        )

        module_feature_output, stored_modules = _run_module_feature_phase(
            project_id=project_id,
            fragments=fragments,
            modules_and_features=modules_and_features,
            feedback=feedback,
            skip_processing=skip_processing,
            request_id=task_db_id,
            is_regeneration=True,
            source_ingestion_id=ingestion_id,
        )

        if (
            isinstance(module_feature_output, dict)
            and module_feature_output.get("status") == "CANCELLED"
        ):
            logger.info(
                "regenerate_modules_and_features_task: cancelled mid-generation project_id=%s",
                project_id,
            )
            emit_task_event(
                task_db_id=task_db_id,
                project_id=project_id,
                task_type="module_regeneration",
                status=TASK_STATUS_CANCELLED,
                stage="modules_and_features.cancelled",
                progress=100,
            )
            mark_sources_and_ingestion_cancelled(
                source_ids=source_ids,
                project_id=project_id,
                task_db_id=task_db_id,
                task_type="module_regeneration",
                stage="modules_and_features.cancelled",
                ingestion_id=ingestion_id,
            )
            return {"project_id": project_id, "status": "cancelled"}

        change_counts = _count_module_feature_regeneration_changes(stored_modules)
        modules_added = change_counts["modules_added"]
        modules_updated = change_counts["modules_updated"]
        features_added = change_counts["features_added"]
        features_updated = change_counts["features_updated"]

        # Every feedback-driven regeneration now leaves its regenerated
        # modules/features flagged with `feedback_change_type`
        # (ADDED/UPDATED) awaiting human accept/reject via
        # /feedback-updates/accept|reject — so the run always resolves to
        # READY_FOR_REVIEW, never COMPLETED, regardless of source type.
        source_completion_status = SOURCE_INGESTION_STATUS_READY_FOR_REVIEW
        ingestion_completion_status = SourceIngestionStatus.READY_FOR_REVIEW.value

        _mark_sources_status(
            source_ids=source_ids,
            status=source_completion_status,
            stage="modules_and_features.regeneration.completed",
            project_id=project_id,
            task_db_id=task_db_id,
            task_type="module_regeneration",
            meta={
                "project_id": project_id,
                "source_ids": source_ids,
                "tot_modules": modules_added,
                "tot_features": features_added,
                "tot_modules_updated": modules_updated,
                "tot_features_updated": features_updated,
            },
        )
        _update_source_ingestion_fields_by_id(
            ingestion_id=ingestion_id,
            fields={
                "completed_at": datetime.now(UTC),
                "tot_modules": modules_added,
                "tot_modules_updated": modules_updated,
                "tot_features": features_added,
                "tot_features_updated": features_updated,
                "status": ingestion_completion_status,
            },
        )
        _add_run_stage_by_id(
            ingestion_id=ingestion_id, stage=SourceIngestionStage.READY_FOR_REVIEW
        )
        _notify_module_feature_status(
            project_id=project_id,
            status=ingestion_completion_status,
            is_regeneration=True,
            modules_added=modules_added,
            modules_updated=modules_updated,
            features_added=features_added,
            features_updated=features_updated,
        )
        record_activity(
            project_id=UUID(project_id),
            activity_type=ActivityType.RFP_MODULES_REGENERATED,
            summary=SUMMARY_ACTIVITY_RFP_MODULES_REGENERATED,
            message=MSG_ACTIVITY_RFP_MODULES_REGENERATED.format(
                tot_modules=modules_added,
                tot_modules_updated=modules_updated,
                tot_features=features_added,
                tot_features_updated=features_updated,
            ),
            actor_user_id=resolve_actor_from_task(task_db_id),
            data={
                "tot_modules": modules_added,
                "tot_features": features_added,
                "tot_modules_updated": modules_updated,
                "tot_features_updated": features_updated,
                "source_ids": source_ids,
                "ingestion_id": ingestion_id,
            },
        )

        return {
            "project_id": project_id,
            "status": source_completion_status,
            "tot_modules": modules_added,
            "tot_features": features_added,
            "tot_modules_updated": modules_updated,
            "tot_features_updated": features_updated,
        }
    except NonRetryableLLMError as exc:
        classification = exc.classification
        error_detail = _describe_non_retryable_llm_error(classification)
        logger.error(
            "regenerate_modules_and_features_task: non-retryable LLM error project_id=%s "
            "reason=%s",
            project_id,
            classification.reason.value,
        )
        _mark_sources_status(
            source_ids=source_ids,
            status=SOURCE_INGESTION_STATUS_FAILED,
            stage="modules_and_features.regeneration.failed",
            project_id=project_id,
            task_db_id=task_db_id,
            task_type="module_regeneration",
            error=error_detail,
            meta={"project_id": project_id, "source_ids": source_ids},
        )
        _update_source_ingestion_fields_by_id(
            ingestion_id=ingestion_id,
            fields={"status": SourceIngestionStatus.FAILED.value},
        )
        _add_source_ingestion_error_by_id(ingestion_id=ingestion_id, error=error_detail)
        _notify_module_feature_status(
            project_id=project_id,
            status=SourceIngestionStatus.FAILED.value,
            is_regeneration=True,
            error=classification.user_message,
            error_reason=classification.reason.value,
        )
        record_activity(
            project_id=UUID(project_id),
            activity_type=ActivityType.RFP_MODULES_REGENERATION_FAILED,
            summary=SUMMARY_ACTIVITY_RFP_MODULES_REGENERATION_FAILED,
            message=MSG_ACTIVITY_RFP_MODULES_REGENERATION_FAILED.format(error=error_detail[:200]),
            actor_user_id=resolve_actor_from_task(task_db_id),
            data={
                "source_ids": source_ids,
                "ingestion_id": ingestion_id,
                "error": error_detail,
                "llm_error_reason": classification.reason.value,
            },
        )
        _cancel_sibling_tasks_on_fatal_llm_error(
            request_id=task_db_id, task_db_id=task_db_id, project_id=project_id
        )
        return {
            "project_id": project_id,
            "status": SOURCE_INGESTION_STATUS_FAILED,
            "error": error_detail,
        }
    except Exception as exc:
        logger.exception("regenerate_modules_and_features_task failed: %s", exc)
        if self.request.retries >= self.max_retries:
            _mark_sources_status(
                source_ids=source_ids,
                status=SOURCE_INGESTION_STATUS_FAILED,
                stage="modules_and_features.regeneration.failed",
                project_id=project_id,
                task_db_id=task_db_id,
                task_type="module_regeneration",
                error=str(exc),
                meta={"project_id": project_id, "source_ids": source_ids},
            )
            _update_source_ingestion_fields_by_id(
                ingestion_id=ingestion_id,
                fields={"status": SourceIngestionStatus.FAILED.value},
            )
            _add_source_ingestion_error_by_id(ingestion_id=ingestion_id, error=str(exc))
            _notify_module_feature_status(
                project_id=project_id,
                status=SourceIngestionStatus.FAILED.value,
                is_regeneration=True,
                error=str(exc),
            )
            record_activity(
                project_id=UUID(project_id),
                activity_type=ActivityType.RFP_MODULES_REGENERATION_FAILED,
                summary=SUMMARY_ACTIVITY_RFP_MODULES_REGENERATION_FAILED,
                message=MSG_ACTIVITY_RFP_MODULES_REGENERATION_FAILED.format(error=str(exc)[:200]),
                actor_user_id=resolve_actor_from_task(task_db_id),
                data={"source_ids": source_ids, "ingestion_id": ingestion_id, "error": str(exc)},
            )
            return {
                "project_id": project_id,
                "status": SOURCE_INGESTION_STATUS_FAILED,
                "error": str(exc),
            }
        retry_num = self.request.retries + 1
        countdown = TASK_RETRY_BASE_DELAY_SECONDS * (2**self.request.retries)
        _mark_sources_status(
            source_ids=source_ids,
            status=SOURCE_INGESTION_STATUS_RUNNING,
            stage=f"modules_and_features.retry.{retry_num}",
            project_id=project_id,
            task_db_id=task_db_id,
            task_type="module_regeneration",
            error=f"Attempt {retry_num} failed: {exc}. Retrying in {countdown}s...",
            meta={"project_id": project_id, "source_ids": source_ids},
        )
        raise self.retry(exc=exc, countdown=countdown)


async def _remap_skip_processing_feature_ids(project_id: str, backlog_items: dict) -> None:
    """Rewrite each epic's ``feature_id`` to match this project's real Feature nodes.

    The skip_processing sample (``sample_result/RFP/agile_backlog.json``) is a
    static snapshot harvested from whichever project first captured it — its
    embedded ``feature_id`` UUIDs belong to that project, not the one
    skip_processing is being exercised against now. Feature ids are
    project-specific (generated fresh per project), but ``epic_code`` (e.g.
    "1.1") is the project-agnostic module/feature numbering the sample and
    every real project share, so it's used here to re-resolve each epic's
    ``feature_id`` against this project's actual Feature nodes before the
    stories are upserted — otherwise every story links to a Feature that
    doesn't exist in this project's graph and never shows up in the tree.

    Raises when EVERY epic fails to resolve — that means the sample simply
    doesn't correspond to this project at all (wrong sample, or modules/
    features haven't been generated for it yet), and silently proceeding
    would just reproduce the exact orphaning bug this function exists to fix,
    minus the visibility. A partial mismatch (some epics resolve, some don't)
    is logged at ERROR rather than raised, since a batch that's mostly usable
    shouldn't be thrown away over one bad epic_code — but the unresolved
    epic(s) are dropped from the output entirely rather than upserted with
    their stale (wrong-project) feature_id, which would silently reproduce
    the same orphaning bug for just that epic.
    """
    from app.repositories.neo4j.module_feature_repository import ModuleFeatureRepository

    output = backlog_items.get("output")
    if not isinstance(output, str):
        return
    try:
        parsed = json.loads(output)
    except ValueError:
        return

    epics = parsed.get("epics") or []
    if not epics:
        return

    modules = await ModuleFeatureRepository().list_modules_by_project(UUID(project_id))
    fea_code_to_id = {
        feature.fea_code: feature.id
        for module in modules
        for feature in module.features
        if feature.fea_code
    }

    matched_epics = []
    unmatched: list[str] = []
    for epic in epics:
        code = epic.get("epic_code")
        real_feature_id = fea_code_to_id.get(code)
        if real_feature_id:
            epic["feature_id"] = real_feature_id
            matched_epics.append(epic)
        else:
            unmatched.append(code)

    if unmatched:
        logger.error(
            "_remap_skip_processing_feature_ids: %d/%d epic_code(s) had no matching "
            "Feature in project_id=%s — dropping from output: %s",
            len(unmatched),
            len(epics),
            project_id,
            unmatched,
        )
    if len(unmatched) == len(epics):
        raise RuntimeError(
            f"skip_processing sample (sample_result/RFP/agile_backlog.json) doesn't match "
            f"project {project_id} — none of {len(epics)} epic_code(s) matched a real "
            f"Feature. Generate modules/features for this project first (skip_processing="
            f"False), or use a sample harvested against a project with matching module/"
            f"feature codes."
        )

    parsed["epics"] = matched_epics
    backlog_items["output"] = json.dumps(parsed, ensure_ascii=False)


def _run_backlog_phase(
    *,
    self,
    project_id: str,
    fragments: list[dict[str, Any]],
    modules_and_features: str | dict[str, Any],
    user_stories: str | list[dict[str, Any]] | None,
    feedback: str | None,
    stage_prefix: str,
    task_db_id: str | None = None,
    task_type: str = "story_generation",
    skip_processing: bool = False,
    request_id: str | None = None,
    source_ingestion_id: str | None = None,
) -> dict[str, Any]:
    """Invoke backlog generation/regeneration and persist user stories.

    Raises `self.retry(...)` for retriable failures, otherwise returns a
    terminal failed payload with event emission.
    """
    try:
        from app.services.rfp_pipeline_v2_graph_service.graph_agile_backlog import run_agile_backlog
        from app.services.user_story_service import UserStoryService

        backlog_input_payload = {
            "user_stories": len(user_stories) if user_stories else 0,
            "feedback": feedback or "",
            "skip_processing": skip_processing,
        }
        logger.info(
            "_run_backlog_phase run_agile_backlog input_payload=%s",
            json.dumps(backlog_input_payload, ensure_ascii=False, default=str),
        )

        options = _get_project_llm_options(project_id=project_id)
        if request_id:
            options["request_id"] = request_id

        run_agile_backlog_signature = inspect.signature(run_agile_backlog)
        run_agile_backlog_kwargs: dict[str, Any] = {
            "fragments": fragments,
            "modules_and_features": modules_and_features,
            "user_stories": user_stories or "",
            "feedback": feedback or "",
            "skip_processing": skip_processing,
        }
        if "options" in run_agile_backlog_signature.parameters:
            run_agile_backlog_kwargs["options"] = options

        # START: Testing and debugging: dump the kwargs to a JSON file for inspection
        from app.utils.common import dump_json_debug  # noqa: PLC0415

        try:
            dump_json_debug(
                "User_Story_generation_input.json",
                run_agile_backlog_kwargs,
                base_dir="temp/User_Story_generation",
            )
        except Exception:
            logger.warning("dump_json_debug failed for backlog input", exc_info=True)
        # END: Testing and debugging: dump the kwargs to a JSON file for inspection

        backlog_items = _run_async(run_agile_backlog(**run_agile_backlog_kwargs))

        if isinstance(backlog_items, dict) and backlog_items.get("status") == "CANCELLED":
            logger.info("_run_backlog_phase: cancelled mid-generation project_id=%s", project_id)
            return {"project_id": project_id, "status": "CANCELLED"}

        if isinstance(backlog_items, dict) and backlog_items.get("status") == "failed":
            raise RuntimeError(backlog_items.get("error") or "agile backlog generation failed")

        if skip_processing and isinstance(backlog_items, dict):
            _run_async(_remap_skip_processing_feature_ids(project_id, backlog_items))

        # START: Testing and debugging: dump the backlog items to a JSON file for inspection
        try:
            debug_backlog_items = backlog_items
            if isinstance(backlog_items, dict) and isinstance(backlog_items.get("output"), str):
                debug_backlog_items = dict(backlog_items)
                try:
                    debug_backlog_items["output"] = json.loads(debug_backlog_items["output"])
                except ValueError:
                    pass
            dump_json_debug(
                "User_Story_generation_output.json",
                debug_backlog_items,
                base_dir="temp/User_Story_generation",
            )
        except Exception:
            logger.warning("dump_json_debug failed for backlog output", exc_info=True)
        # END: Testing and debugging: dump the backlog items to a JSON file for inspection

        from app.core import task_control  # noqa: PLC0415

        if request_id and task_control.is_request_cancelled(request_id):
            logger.info(
                "_run_backlog_phase: cancelled after generation, before persistence project_id=%s",
                project_id,
            )
            return {"project_id": project_id, "status": "CANCELLED"}

        _update_project_metadata_best_effort(
            project_id=project_id,
            persona_glossary=_extract_persona_glossary(backlog_items),
        )

        upserted_count = _run_async(
            UserStoryService().upsert_user_stories_from_backlog(
                project_id=UUID(project_id),
                backlog_result=backlog_items,
                source_ingestion_id=source_ingestion_id,
            )
        )

        return {
            "project_id": project_id,
            "status": SOURCE_INGESTION_STATUS_READY_FOR_REVIEW,
            "total_items": upserted_count,
            "items": backlog_items,
        }
    except NonRetryableLLMError as exc:
        classification = exc.classification
        error_detail = _describe_non_retryable_llm_error(classification)
        logger.error(
            "_run_backlog_phase: non-retryable LLM error project_id=%s reason=%s",
            project_id,
            classification.reason.value,
        )
        emit_task_event(
            project_id=project_id,
            status=SOURCE_INGESTION_STATUS_FAILED,
            stage=f"{stage_prefix}.failed",
            progress=100,
            error=error_detail,
            meta={"project_id": project_id},
            task_db_id=task_db_id,
            task_type=task_type,
        )
        return {
            "project_id": project_id,
            "status": SOURCE_INGESTION_STATUS_FAILED,
            "error": error_detail,
            "llm_error_reason": classification.reason.value,
            "llm_user_message": classification.user_message,
        }
    except Exception as exc:
        logger.exception("_run_backlog_phase failed: %s", exc)
        if self.request.retries >= self.max_retries:
            emit_task_event(
                project_id=project_id,
                status=SOURCE_INGESTION_STATUS_FAILED,
                stage=f"{stage_prefix}.failed",
                progress=100,
                error=str(exc),
                meta={"project_id": project_id},
                task_db_id=task_db_id,
                task_type=task_type,
            )
            return {
                "project_id": project_id,
                "status": SOURCE_INGESTION_STATUS_FAILED,
                "error": str(exc),
            }
        retry_num = self.request.retries + 1
        countdown = TASK_RETRY_BASE_DELAY_SECONDS * (2**self.request.retries)
        emit_task_event(
            project_id=project_id,
            status=SOURCE_INGESTION_STATUS_RUNNING,
            stage=f"{stage_prefix}.retry.{retry_num}",
            progress=30,
            error=f"Attempt {retry_num} failed: {exc}. Retrying in {countdown}s...",
            meta={"project_id": project_id},
            task_db_id=task_db_id,
            task_type=task_type,
        )
        raise self.retry(exc=exc, countdown=countdown)


def _save_story_feedback_history(
    *,
    project_id: str,
    task_db_id: str | None,
    story_feedbacks: list[dict],
    patch_result: dict,
) -> None:
    """Persist the AI patch input and output to story_feedback_histories.

    Runs inside the Celery worker (sync context).  Failures are logged and
    swallowed so they never block story upsert or WebSocket events.
    """
    from uuid import UUID as _UUID

    from app.db.unit_of_work import UnitOfWork

    try:
        raw_output = patch_result.get("output") or {}
        if isinstance(raw_output, str):
            try:
                raw_output = json.loads(raw_output)
            except ValueError:
                raw_output = {}

        revised_stories: list | None = (
            raw_output.get("revised_stories") if isinstance(raw_output, dict) else None
        )
        ai_status: str = str(patch_result.get("status") or "UNKNOWN")
        ai_feedback: str | None = patch_result.get("ai_feedback") or None

        task_uuid: _UUID | None = None
        if task_db_id:
            try:
                task_uuid = _UUID(task_db_id)
            except (ValueError, AttributeError):
                task_uuid = None

        with UnitOfWork() as uow:
            uow.story_feedback_histories.create(
                project_id=_UUID(project_id),
                task_db_id=task_uuid,
                story_feedbacks_json=story_feedbacks or None,
                revised_stories_json=revised_stories,
                ai_status=ai_status,
                ai_feedback=ai_feedback,
            )
        logger.info(
            "[STORY_FEEDBACK_HISTORY] saved history project=%s task=%s status=%s revised=%d",
            project_id,
            task_db_id,
            ai_status,
            len(revised_stories) if revised_stories else 0,
        )
    except Exception as exc:
        logger.warning(
            "[STORY_FEEDBACK_HISTORY] failed to save history project=%s task=%s error=%s",
            project_id,
            task_db_id,
            exc,
        )


def _record_feedback_regenerated_activity(
    *,
    project_id: str,
    task_db_id: str | None,
    ingestion_id: str | None,
    stories_added: int,
    stories_updated: int,
) -> None:
    """Log an activity-feed entry for an RFP per-story feedback regeneration."""
    record_activity(
        project_id=UUID(project_id),
        activity_type=ActivityType.RFP_FEEDBACK_REGENERATED,
        summary=SUMMARY_ACTIVITY_RFP_FEEDBACK_REGENERATED,
        message=MSG_ACTIVITY_RFP_FEEDBACK_REGENERATED.format(
            tot_user_stories=stories_added, tot_user_stories_updated=stories_updated
        ),
        actor_user_id=resolve_actor_from_task(task_db_id),
        data={
            "tot_user_stories": stories_added,
            "tot_user_stories_updated": stories_updated,
            "ingestion_id": ingestion_id,
        },
    )


def _notify_story_feedback_patch_status(
    *,
    project_id: str,
    status: str,
    stories_added: int | None = None,
    stories_updated: int | None = None,
    error: str | None = None,
    error_reason: str | None = None,
) -> None:
    """Best-effort: notify the project owner and every assigned member about
    a feedback-driven user-story regeneration's start or outcome.

    Fires for the ``running`` (started), ``ready_for_review`` (completed),
    and ``failed`` transitions. Never raises — a notification failure must
    not abort the regeneration pipeline.
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

        total_stories = (stories_added or 0) + (stories_updated or 0)
        if status == SourceIngestionStatus.RUNNING.value:
            title = "User Story Regeneration from Feedback Started"
            message = f'User story regeneration from feedback has started for "{project.name}".'
            notification_type = NotificationType.INFO
        elif status == SourceIngestionStatus.READY_FOR_REVIEW.value:
            title = "User Stories Regenerated from Feedback"
            message = (
                f"{total_stories} user story(ies) regenerated from feedback are ready "
                f'for review in "{project.name}".'
            )
            notification_type = NotificationType.SUCCESS
        elif status == SourceIngestionStatus.FAILED.value:
            title = "User Story Regeneration Failed"
            message = f'User story regeneration from feedback failed for "{project.name}".'
            if error:
                message += f" Error: {error[:200]}"
            notification_type = NotificationType.ERROR
        else:
            return

        for user_id in recipient_ids:
            try:
                publish_notification(
                    user_id=user_id,
                    title=title,
                    message=message,
                    notification_type=notification_type,
                    data={
                        "project_id": project_id,
                        "status": status,
                        "tot_user_stories": stories_added,
                        "tot_user_stories_updated": stories_updated,
                        "error_reason": error_reason,
                    },
                )
            except Exception:
                logger.warning(
                    "_notify_story_feedback_patch_status: failed to notify project_id=%s "
                    "user_id=%s",
                    project_id,
                    user_id,
                    exc_info=True,
                )
    except Exception:
        logger.warning(
            "_notify_story_feedback_patch_status: failed for project_id=%s status=%s",
            project_id,
            status,
            exc_info=True,
        )


def _run_story_feedback_patch_task(
    *,
    self,
    project_id: str,
    story_feedbacks: list[dict],
    feature_contexts: list[dict],
    persona_glossary: str,
    valid_sources: str,
    story_code_to_feature_id: dict[str, str],
    options: dict,
    task_db_id: str | None = None,
    ingestion_id: str | None = None,
    skip_processing: bool = False,
) -> dict[str, Any]:
    """Run run_agile_backlog_patch and persist the revised stories.

    `ingestion_id` is the dedicated SourceIngestion row created for this
    feedback-driven patch request — updated directly by id rather than by
    tagging whichever ingestion the project's sources happen to be linked to.
    """
    stage_prefix = "user_story.feedback_patch"
    task_type = "story_feedback_patch"

    if mark_cancelled_and_check(
        request_id=task_db_id,
        task_db_id=task_db_id,
        project_id=project_id,
        task_type=task_type,
        stage=f"{stage_prefix}.cancelled",
    ):
        record_activity(
            project_id=UUID(project_id),
            activity_type=ActivityType.RFP_FEEDBACK_REGENERATION_CANCELLED,
            summary=SUMMARY_ACTIVITY_RFP_FEEDBACK_REGENERATION_CANCELLED,
            message=MSG_ACTIVITY_RFP_FEEDBACK_REGENERATION_CANCELLED,
            actor_user_id=resolve_actor_from_task(task_db_id),
            data={"ingestion_id": ingestion_id},
        )
        return {"project_id": project_id, "status": "cancelled"}

    emit_task_event(
        project_id=project_id,
        status=SOURCE_INGESTION_STATUS_RUNNING,
        stage=f"{stage_prefix}.started",
        progress=10,
        meta={"project_id": project_id},
        task_db_id=task_db_id,
        task_type=task_type,
    )
    _update_source_ingestion_fields_by_id(
        ingestion_id=ingestion_id,
        fields={
            "started_at": datetime.now(UTC),
            "status": SourceIngestionStatus.RUNNING.value,
        },
    )
    _notify_story_feedback_patch_status(
        project_id=project_id,
        status=SourceIngestionStatus.RUNNING.value,
    )
    record_activity(
        project_id=UUID(project_id),
        activity_type=ActivityType.RFP_FEEDBACK_REGENERATION_STARTED,
        summary=SUMMARY_ACTIVITY_RFP_FEEDBACK_REGENERATION_STARTED,
        message=MSG_ACTIVITY_RFP_FEEDBACK_REGENERATION_STARTED,
        actor_user_id=resolve_actor_from_task(task_db_id),
        data={"ingestion_id": ingestion_id},
    )

    try:
        from app.schemas.rfp_pipeline_v2_graph_schema import FeatureContext, StoryFeedbackItem
        from app.services.rfp_pipeline_v2_graph_service.graph_agile_backlog_patch import (
            run_agile_backlog_patch,
        )
        from app.services.user_story_service import UserStoryService

        # Deserialize input models
        story_feedback_objs = [StoryFeedbackItem.model_validate(sf) for sf in story_feedbacks]
        feature_context_objs = [FeatureContext.model_validate(fc) for fc in feature_contexts]

        effective_options = (
            {**(options or {}), "request_id": task_db_id} if task_db_id else (options or {})
        )

        # START: Testing and debugging: dump the input params to a JSON file for inspection
        from app.utils.common import dump_json_debug  # noqa: PLC0415

        try:
            dump_json_debug(
                "User_Story_feedback_patch_input.json",
                {
                    "story_feedbacks": story_feedbacks,
                    "feature_contexts": feature_contexts,
                    "persona_glossary": persona_glossary,
                    "valid_sources": valid_sources,
                    "story_code_to_feature_id": story_code_to_feature_id,
                    "skip_processing": skip_processing,
                    "options": effective_options,
                },
                base_dir="temp/User_Story_feedback_patch",
            )
        except Exception:
            logger.warning("dump_json_debug failed for feedback patch input", exc_info=True)
        # END: Testing and debugging: dump the input params to a JSON file for inspection

        patch_result = _run_async(
            run_agile_backlog_patch(
                story_feedbacks=story_feedback_objs,
                feature_contexts=feature_context_objs,
                persona_glossary=persona_glossary,
                valid_sources=valid_sources,
                skip_processing=skip_processing,
                options=effective_options,
            )
        )

        # START: Testing and debugging: dump the patch result to a JSON file for inspection
        try:
            debug_patch_result = patch_result
            if isinstance(patch_result, dict) and isinstance(patch_result.get("output"), str):
                debug_patch_result = dict(patch_result)
                try:
                    debug_patch_result["output"] = json.loads(debug_patch_result["output"])
                except ValueError:
                    pass
            dump_json_debug(
                "User_Story_feedback_patch_output.json",
                debug_patch_result,
                base_dir="temp/User_Story_feedback_patch",
            )
        except Exception:
            logger.warning("dump_json_debug failed for feedback patch output", exc_info=True)
        # END: Testing and debugging: dump the patch result to a JSON file for inspection

        if isinstance(patch_result, dict) and patch_result.get("status") == "CANCELLED":
            logger.info(
                "_run_story_feedback_patch_task: cancelled mid-generation project_id=%s",
                project_id,
            )
            emit_task_event(
                project_id=project_id,
                status=TASK_STATUS_CANCELLED,
                stage=f"{stage_prefix}.cancelled",
                progress=100,
                meta={"project_id": project_id},
                task_db_id=task_db_id,
                task_type=task_type,
            )
            return {"project_id": project_id, "status": "cancelled"}

        if isinstance(patch_result, dict) and patch_result.get("status") == "failed":
            raise RuntimeError(patch_result.get("error") or "agile backlog patch failed")

        from app.core import task_control  # noqa: PLC0415

        if task_db_id and task_control.is_request_cancelled(task_db_id):
            logger.info(
                "_run_story_feedback_patch_task: cancelled after generation, before persistence project_id=%s",
                project_id,
            )
            emit_task_event(
                project_id=project_id,
                status=TASK_STATUS_CANCELLED,
                stage=f"{stage_prefix}.cancelled",
                progress=100,
                meta={"project_id": project_id},
                task_db_id=task_db_id,
                task_type=task_type,
            )
            return {"project_id": project_id, "status": "cancelled"}

        _save_story_feedback_history(
            project_id=project_id,
            task_db_id=task_db_id,
            story_feedbacks=story_feedbacks,
            patch_result=patch_result,
        )

        story_change_counts = _run_async(
            UserStoryService().upsert_user_stories_from_patch(
                project_id=UUID(project_id),
                patch_result=patch_result,
                story_code_to_feature_id=story_code_to_feature_id,
                source_ingestion_id=ingestion_id,
            )
        )
        stories_added = story_change_counts["added"]
        stories_updated = story_change_counts["updated"]

        emit_task_event(
            project_id=project_id,
            status=SOURCE_INGESTION_STATUS_READY_FOR_REVIEW,
            stage=f"{stage_prefix}.completed",
            progress=100,
            meta={
                "project_id": project_id,
                "tot_user_stories": stories_added,
                "tot_user_stories_updated": stories_updated,
            },
            task_db_id=task_db_id,
            task_type=task_type,
        )
        _update_source_ingestion_fields_by_id(
            ingestion_id=ingestion_id,
            fields={
                "completed_at": datetime.now(UTC),
                "tot_user_stories": stories_added,
                "tot_user_stories_updated": stories_updated,
                "status": SourceIngestionStatus.READY_FOR_REVIEW.value,
            },
        )
        _add_run_stage_by_id(
            ingestion_id=ingestion_id, stage=SourceIngestionStage.READY_FOR_REVIEW
        )
        _record_feedback_regenerated_activity(
            project_id=project_id,
            task_db_id=task_db_id,
            ingestion_id=ingestion_id,
            stories_added=stories_added,
            stories_updated=stories_updated,
        )
        _notify_story_feedback_patch_status(
            project_id=project_id,
            status=SourceIngestionStatus.READY_FOR_REVIEW.value,
            stories_added=stories_added,
            stories_updated=stories_updated,
        )
        return {
            "project_id": project_id,
            "status": SOURCE_INGESTION_STATUS_READY_FOR_REVIEW,
            "tot_user_stories": stories_added,
            "tot_user_stories_updated": stories_updated,
        }

    except NonRetryableLLMError as exc:
        classification = exc.classification
        error_detail = _describe_non_retryable_llm_error(classification)
        logger.error(
            "_run_story_feedback_patch_task: non-retryable LLM error project_id=%s reason=%s",
            project_id,
            classification.reason.value,
        )
        emit_task_event(
            project_id=project_id,
            status=SOURCE_INGESTION_STATUS_FAILED,
            stage=f"{stage_prefix}.failed",
            progress=100,
            error=error_detail,
            meta={"project_id": project_id},
            task_db_id=task_db_id,
            task_type=task_type,
        )
        _update_source_ingestion_fields_by_id(
            ingestion_id=ingestion_id,
            fields={"status": SourceIngestionStatus.FAILED.value},
        )
        _add_source_ingestion_error_by_id(ingestion_id=ingestion_id, error=error_detail)
        _notify_story_feedback_patch_status(
            project_id=project_id,
            status=SourceIngestionStatus.FAILED.value,
            error=classification.user_message,
            error_reason=classification.reason.value,
        )
        record_activity(
            project_id=UUID(project_id),
            activity_type=ActivityType.RFP_FEEDBACK_REGENERATION_FAILED,
            summary=SUMMARY_ACTIVITY_RFP_FEEDBACK_REGENERATION_FAILED,
            message=MSG_ACTIVITY_RFP_FEEDBACK_REGENERATION_FAILED.format(error=error_detail[:200]),
            actor_user_id=resolve_actor_from_task(task_db_id),
            data={
                "ingestion_id": ingestion_id,
                "error": error_detail,
                "llm_error_reason": classification.reason.value,
            },
        )
        _cancel_sibling_tasks_on_fatal_llm_error(
            request_id=task_db_id, task_db_id=task_db_id, project_id=project_id
        )
        return {
            "project_id": project_id,
            "status": SOURCE_INGESTION_STATUS_FAILED,
            "error": error_detail,
        }
    except Exception as exc:
        logger.exception("_run_story_feedback_patch_task failed: %s", exc)
        if self.request.retries >= self.max_retries:
            emit_task_event(
                project_id=project_id,
                status=SOURCE_INGESTION_STATUS_FAILED,
                stage=f"{stage_prefix}.failed",
                progress=100,
                error=str(exc),
                meta={"project_id": project_id},
                task_db_id=task_db_id,
                task_type=task_type,
            )
            _update_source_ingestion_fields_by_id(
                ingestion_id=ingestion_id,
                fields={"status": SourceIngestionStatus.FAILED.value},
            )
            _add_source_ingestion_error_by_id(ingestion_id=ingestion_id, error=str(exc))
            _notify_story_feedback_patch_status(
                project_id=project_id,
                status=SourceIngestionStatus.FAILED.value,
                error=str(exc),
            )
            record_activity(
                project_id=UUID(project_id),
                activity_type=ActivityType.RFP_FEEDBACK_REGENERATION_FAILED,
                summary=SUMMARY_ACTIVITY_RFP_FEEDBACK_REGENERATION_FAILED,
                message=MSG_ACTIVITY_RFP_FEEDBACK_REGENERATION_FAILED.format(error=str(exc)[:200]),
                actor_user_id=resolve_actor_from_task(task_db_id),
                data={"ingestion_id": ingestion_id, "error": str(exc)},
            )
            return {
                "project_id": project_id,
                "status": SOURCE_INGESTION_STATUS_FAILED,
                "error": str(exc),
            }
        countdown = TASK_RETRY_BASE_DELAY_SECONDS * (2**self.request.retries)
        retry_num = self.request.retries + 1
        emit_task_event(
            project_id=project_id,
            status=SOURCE_INGESTION_STATUS_RUNNING,
            stage=f"{stage_prefix}.retry.{retry_num}",
            progress=30,
            error=f"Attempt {retry_num} failed: {exc}. Retrying in {countdown}s...",
            meta={"project_id": project_id},
            task_db_id=task_db_id,
            task_type=task_type,
        )
        raise self.retry(exc=exc, countdown=countdown)


def _run_user_story_backlog_task(
    *,
    self,
    project_id: str,
    fragments: list[dict[str, Any]],
    modules_and_features: str | dict[str, Any],
    user_stories: str | list[dict[str, Any]],
    feedback: str,
    stage_prefix: str,
    task_type: str,
    task_db_id: str | None = None,
    skip_processing: bool = False,
    ingestion_id: str | None = None,
    source_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Wrap backlog execution with start/completed/failed event emission.

    `ingestion_id` (only used for ``task_type == "story_regeneration"``) is
    the dedicated SourceIngestion row created for that feedback-driven
    regeneration request — updated directly by id rather than by tagging
    whichever ingestion the project's sources happen to be linked to.

    `source_ids` (only used for ``task_type == "story_generation"``) are the
    originating upload's sources; the ingestion bookkeeping updates the
    *source-linked* SourceIngestion row(s) so it stays on the run that the
    ``user_story`` stage was tagged onto and never bleeds into a newer
    feedback-regeneration run.
    """
    if mark_cancelled_and_check(
        request_id=task_db_id,
        task_db_id=task_db_id,
        project_id=project_id,
        task_type=task_type,
        stage=f"{stage_prefix}.cancelled",
    ):
        if task_type == "story_generation":
            record_activity(
                project_id=UUID(project_id),
                activity_type=ActivityType.RFP_USER_STORIES_GENERATION_CANCELLED,
                summary=SUMMARY_ACTIVITY_RFP_USER_STORIES_GENERATION_CANCELLED,
                message=MSG_ACTIVITY_RFP_USER_STORIES_GENERATION_CANCELLED,
                actor_user_id=resolve_actor_from_task(task_db_id),
                data={"source_ids": source_ids},
            )
        elif task_type == "story_regeneration":
            record_activity(
                project_id=UUID(project_id),
                activity_type=ActivityType.RFP_USER_STORIES_REGENERATION_CANCELLED,
                summary=SUMMARY_ACTIVITY_RFP_USER_STORIES_REGENERATION_CANCELLED,
                message=MSG_ACTIVITY_RFP_USER_STORIES_REGENERATION_CANCELLED,
                actor_user_id=resolve_actor_from_task(task_db_id),
                data={"ingestion_id": ingestion_id},
            )
        return {"project_id": project_id, "status": "cancelled"}

    emit_task_event(
        project_id=project_id,
        status=SourceIngestionStatus.RUNNING.value,
        stage=f"{stage_prefix}.started",
        progress=10,
        meta={"project_id": project_id},
        task_db_id=task_db_id,
        task_type=task_type,
    )
    # The initial "approve modules" auto-chain (task_type="story_generation",
    # triggered by PATCH /projects/{project_id}/modules/status) updates the
    # SourceIngestion / Source statuses it's linked to. Feedback-driven
    # regeneration (task_type="story_regeneration") instead owns a dedicated
    # SourceIngestion row per request, updated by id below.
    if task_type == "story_generation":
        _update_source_ingestion_fields(
            source_ids=source_ids or [],
            fields={
                "user_story_gen_started_at": datetime.now(UTC),
                "status": SourceIngestionStatus.RUNNING.value,
            },
        )
        _mark_sources_status_by_project(
            project_id=project_id,
            status=SourceIngestionStatus.RUNNING.value,
            stage=f"{stage_prefix}.started",
            task_db_id=task_db_id,
        )
        _notify_user_story_status(
            project_id=project_id,
            status=SourceIngestionStatus.RUNNING.value,
        )
        record_activity(
            project_id=UUID(project_id),
            activity_type=ActivityType.RFP_USER_STORIES_GENERATION_STARTED,
            summary=SUMMARY_ACTIVITY_RFP_USER_STORIES_GENERATION_STARTED,
            message=MSG_ACTIVITY_RFP_USER_STORIES_GENERATION_STARTED,
            actor_user_id=resolve_actor_from_task(task_db_id),
            data={"source_ids": source_ids},
        )
    elif task_type == "story_regeneration":
        _update_source_ingestion_fields_by_id(
            ingestion_id=ingestion_id,
            fields={
                "started_at": datetime.now(UTC),
                "status": SourceIngestionStatus.RUNNING.value,
            },
        )
        _notify_user_story_regeneration_status(
            project_id=project_id,
            status=SourceIngestionStatus.RUNNING.value,
        )
        record_activity(
            project_id=UUID(project_id),
            activity_type=ActivityType.RFP_USER_STORIES_REGENERATION_STARTED,
            summary=SUMMARY_ACTIVITY_RFP_USER_STORIES_REGENERATION_STARTED,
            message=MSG_ACTIVITY_RFP_USER_STORIES_REGENERATION_STARTED,
            actor_user_id=resolve_actor_from_task(task_db_id),
            data={"ingestion_id": ingestion_id},
        )

    resolved_source_ingestion_id = (
        ingestion_id
        if task_type == "story_regeneration"
        else _resolve_source_ingestion_id(source_ids=source_ids or [])
    )
    result = _run_backlog_phase(
        self=self,
        project_id=project_id,
        fragments=fragments,
        modules_and_features=modules_and_features,
        user_stories=user_stories,
        feedback=feedback,
        stage_prefix=stage_prefix,
        task_db_id=task_db_id,
        task_type=task_type,
        skip_processing=skip_processing,
        request_id=task_db_id,
        source_ingestion_id=resolved_source_ingestion_id,
    )

    if result.get("status") == "CANCELLED":
        emit_task_event(
            project_id=project_id,
            status=TASK_STATUS_CANCELLED,
            stage=f"{stage_prefix}.cancelled",
            progress=100,
            meta={"project_id": project_id},
            task_db_id=task_db_id,
            task_type=task_type,
        )
        return {"project_id": project_id, "status": "cancelled"}

    if result.get("status") == SOURCE_INGESTION_STATUS_READY_FOR_REVIEW:
        emit_task_event(
            project_id=project_id,
            status=SOURCE_INGESTION_STATUS_READY_FOR_REVIEW,
            stage=f"{stage_prefix}.completed",
            progress=100,
            meta={
                "project_id": project_id,
                "total_items": result.get("total_items", 0),
            },
            task_db_id=task_db_id,
            task_type=task_type,
        )
        if task_type == "story_generation":
            _update_source_ingestion_fields(
                source_ids=source_ids or [],
                fields={
                    "user_story_gen_completed_at": datetime.now(UTC),
                    "tot_user_stories": result.get("total_items", 0),
                    "status": SourceIngestionStatus.READY_FOR_REVIEW.value,
                },
            )
            _add_run_stage_by_source_ids(
                source_ids or [], SourceIngestionStage.USER_STORY_READY_FOR_REVIEW
            )
            _mark_sources_status_by_project(
                project_id=project_id,
                status=SOURCE_STATUS_READY_FOR_REVIEW,
                stage=f"{stage_prefix}.completed",
                task_db_id=task_db_id,
                meta={"total_items": result.get("total_items", 0)},
            )
            _notify_user_story_status(
                project_id=project_id,
                status=SourceIngestionStatus.READY_FOR_REVIEW.value,
                total_user_stories=result.get("total_items", 0),
            )
            record_activity(
                project_id=UUID(project_id),
                activity_type=ActivityType.RFP_USER_STORIES_GENERATED,
                summary=SUMMARY_ACTIVITY_RFP_USER_STORIES_GENERATED,
                message=MSG_ACTIVITY_RFP_USER_STORIES_GENERATED.format(
                    total_items=result.get("total_items", 0)
                ),
                actor_user_id=resolve_actor_from_task(task_db_id),
                data={"total_items": result.get("total_items", 0), "source_ids": source_ids},
            )
        elif task_type == "story_regeneration":
            _update_source_ingestion_fields_by_id(
                ingestion_id=ingestion_id,
                fields={
                    "completed_at": datetime.now(UTC),
                    "tot_user_stories": result.get("total_items", 0),
                    "status": SourceIngestionStatus.READY_FOR_REVIEW.value,
                },
            )
            record_activity(
                project_id=UUID(project_id),
                activity_type=ActivityType.RFP_USER_STORIES_REGENERATED,
                summary=SUMMARY_ACTIVITY_RFP_USER_STORIES_REGENERATED,
                message=MSG_ACTIVITY_RFP_USER_STORIES_REGENERATED.format(
                    total_items=result.get("total_items", 0)
                ),
                actor_user_id=resolve_actor_from_task(task_db_id),
                data={"total_items": result.get("total_items", 0), "ingestion_id": ingestion_id},
            )
            _notify_user_story_regeneration_status(
                project_id=project_id,
                status=SourceIngestionStatus.READY_FOR_REVIEW.value,
                total_user_stories=result.get("total_items", 0),
            )
    else:
        llm_error_reason = result.get("llm_error_reason")
        notify_error = result.get("llm_user_message") or result.get("error")
        emit_task_event(
            project_id=project_id,
            status=SOURCE_INGESTION_STATUS_FAILED,
            stage=f"{stage_prefix}.failed",
            progress=100,
            error=result.get("error"),
            meta={"project_id": project_id},
            task_db_id=task_db_id,
            task_type=task_type,
        )
        if task_type == "story_generation":
            _update_source_ingestion_fields(
                source_ids=source_ids or [],
                fields={"status": SourceIngestionStatus.FAILED.value},
            )
            _add_source_ingestion_error(
                source_ids=source_ids or [], error=result.get("error") or ""
            )
            _mark_sources_status_by_project(
                project_id=project_id,
                status=SOURCE_STATUS_FAILED,
                stage=f"{stage_prefix}.failed",
                task_db_id=task_db_id,
                error=result.get("error"),
            )
            _notify_user_story_status(
                project_id=project_id,
                status=SourceIngestionStatus.FAILED.value,
                error=notify_error,
                error_reason=llm_error_reason,
            )
            record_activity(
                project_id=UUID(project_id),
                activity_type=ActivityType.RFP_USER_STORIES_GENERATION_FAILED,
                summary=SUMMARY_ACTIVITY_RFP_USER_STORIES_GENERATION_FAILED,
                message=MSG_ACTIVITY_RFP_USER_STORIES_GENERATION_FAILED.format(
                    error=(result.get("error") or "")[:200]
                ),
                actor_user_id=resolve_actor_from_task(task_db_id),
                data={
                    "source_ids": source_ids,
                    "error": result.get("error"),
                    "llm_error_reason": llm_error_reason,
                },
            )
        elif task_type == "story_regeneration":
            _update_source_ingestion_fields_by_id(
                ingestion_id=ingestion_id,
                fields={"status": SourceIngestionStatus.FAILED.value},
            )
            _add_source_ingestion_error_by_id(
                ingestion_id=ingestion_id, error=result.get("error") or ""
            )
            _notify_user_story_regeneration_status(
                project_id=project_id,
                status=SourceIngestionStatus.FAILED.value,
                error=notify_error,
                error_reason=llm_error_reason,
            )
            record_activity(
                project_id=UUID(project_id),
                activity_type=ActivityType.RFP_USER_STORIES_REGENERATION_FAILED,
                summary=SUMMARY_ACTIVITY_RFP_USER_STORIES_REGENERATION_FAILED,
                message=MSG_ACTIVITY_RFP_USER_STORIES_REGENERATION_FAILED.format(
                    error=(result.get("error") or "")[:200]
                ),
                actor_user_id=resolve_actor_from_task(task_db_id),
                data={
                    "ingestion_id": ingestion_id,
                    "error": result.get("error"),
                    "llm_error_reason": llm_error_reason,
                },
            )
        if llm_error_reason:
            _cancel_sibling_tasks_on_fatal_llm_error(
                request_id=task_db_id, task_db_id=task_db_id, project_id=project_id
            )

    return result
