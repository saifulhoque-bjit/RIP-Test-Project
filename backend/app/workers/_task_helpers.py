"""Shared helpers used by all Celery source-processing tasks.

Kept in a dedicated module so that ``image_task``, ``document_task``, and
``source_code_task`` can import them without creating circular dependencies
with ``process_source_tasks``.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any
from uuid import UUID

import botocore.exceptions
import kombu.exceptions
import neo4j.exceptions
import redis.exceptions

from app.core.constants import (
    SOURCE_STATUS_CANCELLED,
    SOURCE_STATUS_FAILED,
    SOURCE_STATUS_QUEUED,
    SOURCE_STATUS_READY_FOR_REVIEW,
    SOURCE_STATUS_RUNNING,
    TASK_RETRY_BASE_DELAY_SECONDS,
    TASK_STATUS_CANCELLED,
)
from app.core.enums.source_ingestion_stage import SourceIngestionStage
from app.core.enums.source_status import SOURCE_PROCESSING_STATUS_VALUES
from app.utils.logger import get_logger

logger = get_logger(__name__)


class TaskCancelledError(Exception):
    """Raised when a task notices its request has been cancelled.

    Deliberately not passed through ``_handle_task_exception`` — it must
    never be retried and must never mark the row ``failed``.  Raising it is
    also the only way to stop a real Celery ``chain()`` (e.g. the per-module
    chain in ``source_code_task.py``) from dispatching its next link, since
    this deployment's thread-pool workers cannot hard-terminate a task that
    has already started (see ``app/core/task_control.py``).
    """


class CircuitBreakerTaskFailure(Exception):
    """Celery-safe re-raise of a ``CircuitBreakerError`` at a task boundary.

    ``CircuitBreakerError`` (``app/services/source_code_pipeline/src/ai/llm_client.py``)
    is deliberately a ``BaseException`` so it can't be swallowed by an
    ``except Exception`` deep in the pipeline into a false "failed module,
    continue" outcome — that would defeat the breaker's job of stopping the
    whole run. But that same property means Celery's own task-tracing
    machinery (which only recognizes ``Exception``, confirmed empirically —
    see ``docs/CircuitBreakerError_Handling_Issue_Implications.docx``) does
    not record a task raising it as a normal ``FAILURE``: under this
    deployment's real ``-P prefork`` pool it crashes the worker child
    outright (the task never resolves, just hangs ``PENDING`` forever), and
    with ``acks_late=True`` + ``task_reject_on_worker_lost`` the broker then
    redelivers the same message — re-tripping the breaker in a crash loop.

    A chain-link task (e.g. ``_process_single_module_task``) that catches
    ``CircuitBreakerError`` should raise this instead of returning a result
    dict: raising (not returning) is what stops Celery's ``chain()`` from
    dispatching its next link — the same reason ``TaskCancelledError``
    exists — while this being a plain ``Exception`` subclass keeps Celery's
    normal FAILURE recording intact. The orchestrator task blocking on that
    chain's result (``_parse_code_task``) catches both this and the original
    ``CircuitBreakerError`` (raised directly there, e.g. during global-artifact
    generation) identically.
    """


class CreditBalanceExhaustedTaskFailure(Exception):
    """Celery-safe re-raise of a ``CreditBalanceExhaustedError`` at a task boundary.

    ``CreditBalanceExhaustedError`` (``app/services/source_code_pipeline/src/ai/llm_client.py``)
    is deliberately a ``BaseException`` for the same reason as
    ``CircuitBreakerError`` — so a broad ``except Exception`` deep in the
    module pipeline can't swallow it into a false "failed module, continue"
    outcome, which would let every remaining module burn time hitting the
    identical account-wide billing/credit failure one at a time. But that
    same ``BaseException``-ness means it must not be allowed to escape a
    Celery task unhandled (see ``CircuitBreakerTaskFailure``'s docstring for
    why: it crashes the worker child under the real ``-P prefork`` pool
    instead of being recorded as a normal ``FAILURE``).

    A chain-link task (``_process_single_module_task``) that catches
    ``CreditBalanceExhaustedError`` raises this instead of returning a
    result dict: raising (not returning) is what stops Celery's ``chain()``
    from dispatching the next module — since the per-module chain is a
    single flattened *sequential* chain (see ``_run_pipeline_orchestrator``),
    this alone is sufficient to stop every module still queued behind the
    one that failed; no separate Redis-queue purge or task revocation is
    needed. Never retried — the orchestrator task blocking on the chain's
    result (``_parse_code_task``) catches this (and a directly-raised
    ``CreditBalanceExhaustedError``) and finalizes the source as failed via
    ``_handle_source_code_task_exception``.
    """


class NonRetryableLLMTaskFailure(Exception):
    """Celery-safe re-raise of a ``NonRetryableLLMError`` at a task boundary.

    ``NonRetryableLLMError`` (``app.core.llm_errors``) is deliberately a
    ``BaseException`` for the same reason as ``CircuitBreakerError``/
    ``CreditBalanceExhaustedError`` above (see their docstrings) — so a
    broad ``except Exception`` deep in the pipeline can't swallow it into a
    false "failed module/bucket, continue" outcome. Every other
    non-retryable, non-credit reason (authentication, invalid model, invalid
    request, context-length-exceeded, content policy, permission denied)
    uses this same abort-the-whole-run contract. Carries the originating
    ``LLMErrorClassification`` (typed ``Any`` here — see
    ``describe_non_retryable_llm_error``'s docstring for why this module
    avoids a top-level dependency on ``app.core.llm_errors``) so a caller
    further up the chain (e.g. ``_parse_code_task``) can recover the reason
    for notify/activity-log purposes without re-parsing the formatted
    message string.
    """

    def __init__(self, message: str, *, classification: Any = None) -> None:
        super().__init__(message)
        self.classification = classification


def mark_cancelled_and_check(
    *,
    request_id: str,
    task_db_id: str | None,
    project_id: str,
    task_type: str,
    stage: str = "cancelled",
) -> bool:
    """Return True if *request_id* is cancelled, after recording it.

    Call at a dispatch site that should simply skip its own work (and
    dispatch nothing further) once the request has been cancelled — no
    exception needed, since there is no Celery chain link to unwind here.
    """
    if not request_id:
        return False
    from app.core import task_control  # noqa: PLC0415

    if not task_control.is_request_cancelled(request_id):
        return False
    emit_task_event(
        task_db_id=task_db_id,
        project_id=project_id,
        task_type=task_type,
        status=TASK_STATUS_CANCELLED,
        stage=stage,
        progress=100,
    )
    return True


def _normalize_processing_status(status: str) -> str:
    """Coerce legacy/non-lifecycle statuses into the canonical lifecycle."""
    if status in SOURCE_PROCESSING_STATUS_VALUES:
        return status
    return SOURCE_STATUS_READY_FOR_REVIEW


def _run_async(coro: Any) -> Any:
    """Run an async coroutine from a sync Celery worker process.

    Every call site is a top-level entry point invoked directly from a sync
    Celery task body, so there is never an already-running loop to nest into
    — plain ``asyncio.run()`` is sufficient. (Previously this applied
    ``nest_asyncio`` for LangGraph's internal checkpoint replay path, but this
    app configures no checkpointer on any graph, so that path is never hit;
    the dependency only existed in requirements-dev.txt and was silently a
    no-op in production, which was the actual problem — dev and prod ran
    different code paths.)
    """
    return asyncio.run(coro)


def _mark_status(
    source_id: str,
    status: str,
    error: str | None = None,
    *,
    stage: str | None = None,
    meta: dict | None = None,
    task_db_id: str | None = None,
    task_type: str = "source_process",
    project_id: str | None = None,
) -> None:
    """Update source.status in PostgreSQL.

    When *task_db_id* and *project_id* are provided, also updates the
    corresponding ProjectTask row and publishes a unified task event to
    the Redis channel so connected WebSocket clients receive the update.
    """
    from app.db.unit_of_work import UnitOfWork  # noqa: PLC0415

    normalized_status = _normalize_processing_status(status)

    with UnitOfWork() as uow:
        source = uow.sources.get_by_uuid(UUID(source_id))
        if source is None:
            logger.warning("_mark_status: source_id=%s not found", source_id)
            return
        # A cancelled source is never un-cancelled. Cancellation is applied
        # synchronously by ProjectTaskService.cancel_request while the worker
        # is still mid-LLM-call; that worker goes on reporting progress for a
        # while afterwards, and those late writes must not resurrect the run.
        # Mirrors the same guard in ProjectTaskRepository.update_status.
        if (
            source.status == SOURCE_STATUS_CANCELLED
            and normalized_status != SOURCE_STATUS_CANCELLED
        ):
            logger.debug(
                "_mark_status: ignoring %s -> %s for source_id=%s (source is cancelled)",
                source.status,
                normalized_status,
                source_id,
            )
            return
        source.status = normalized_status
        if error is not None:
            source.processing_error = error[:4000]  # guard against overlong stack traces
        uow.commit()

    if task_db_id and project_id:
        from app.websockets.manager import publish_task_event_sync  # noqa: PLC0415

        _PROGRESS_MAP = {
            SOURCE_STATUS_QUEUED: 5,
            SOURCE_STATUS_READY_FOR_REVIEW: 100,
            SOURCE_STATUS_FAILED: 100,
            SOURCE_STATUS_CANCELLED: 100,
        }
        enriched_meta = {**(meta or {}), "source_id": source_id}
        publish_task_event_sync(
            task_db_id=task_db_id,
            celery_task_id=None,
            task_type=task_type,
            project_id=project_id,
            status=normalized_status,
            progress=_PROGRESS_MAP.get(normalized_status),
            stage=stage,
            meta=enriched_meta,
            error=error,
        )


def _update_source_ingestion_fields(*, source_ids: list[str], fields: dict[str, Any]) -> None:
    """Best-effort: patch *fields* on every SourceIngestion referenced by *source_ids*.

    *source_ids* normally all share the same ``source_ingestion_id`` (they
    come from one upload batch), but every distinct ingestion referenced is
    updated defensively. One DB round-trip regardless of how many *fields*
    are passed (e.g. a timestamp and a count together). Never raises — a
    bookkeeping failure here must not abort the generation pipeline.
    """
    from app.db.unit_of_work import UnitOfWork

    if not fields:
        return

    try:
        uuid_list = [UUID(sid) for sid in source_ids]
        with UnitOfWork() as uow:
            sources = uow.sources.get_many_by_uuids(uuid_list)
            ingestion_ids = {s.source_ingestion_id for s in sources if s.source_ingestion_id}
            if not ingestion_ids:
                return
            for ingestion_id in ingestion_ids:
                uow.source_ingestions.update_fields(ingestion_id, **fields)
            uow.commit()
    except Exception:
        logger.warning(
            "_update_source_ingestion_fields: failed to set fields=%s for source_ids=%s",
            list(fields),
            source_ids,
            exc_info=True,
        )


def _increment_source_ingestion_module_counts(
    *,
    source_ids: list[str],
    modules: int = 0,
    features: int = 0,
    user_stories: int = 0,
    modules_failed: int = 0,
) -> None:
    """Best-effort: atomically bump module/feature/user-story/failed counters.

    Mirrors ``_update_source_ingestion_fields`` — resolves the distinct
    ingestion(s) linked to *source_ids* — but increments the running totals
    via ``SourceIngestionRepository.increment_module_completion_counts``
    instead of overwriting them, so the source-code pipeline can call this
    once per module (as each finishes) without one module's completion
    clobbering another's concurrent update. Never raises — a bookkeeping
    failure here must not abort the generation pipeline.
    """
    from app.db.unit_of_work import UnitOfWork

    if not (modules or features or user_stories or modules_failed):
        return

    try:
        uuid_list = [UUID(sid) for sid in source_ids]
        with UnitOfWork() as uow:
            sources = uow.sources.get_many_by_uuids(uuid_list)
            ingestion_ids = {s.source_ingestion_id for s in sources if s.source_ingestion_id}
            if not ingestion_ids:
                return
            for ingestion_id in ingestion_ids:
                uow.source_ingestions.increment_module_completion_counts(
                    ingestion_id,
                    modules=modules,
                    features=features,
                    user_stories=user_stories,
                    modules_failed=modules_failed,
                )
            uow.commit()
    except Exception:
        logger.warning(
            "_increment_source_ingestion_module_counts: failed to bump counts for source_ids=%s",
            source_ids,
            exc_info=True,
        )


def _resolve_source_ingestion_id(*, source_ids: list[str]) -> str | None:
    """Best-effort: return the ``SourceIngestion`` id shared by *source_ids*.

    *source_ids* normally all share the same ``source_ingestion_id`` (one
    upload batch) — see ``_update_source_ingestion_fields``. Returns the first
    one found, or ``None`` if no source has an ingestion. Never raises — a
    lookup failure here must not abort the generation pipeline.
    """
    from app.db.unit_of_work import UnitOfWork

    try:
        uuid_list = [UUID(sid) for sid in source_ids]
        with UnitOfWork() as uow:
            sources = uow.sources.get_many_by_uuids(uuid_list)
            for source in sources:
                if source.source_ingestion_id:
                    return str(source.source_ingestion_id)
        return None
    except Exception:
        logger.warning(
            "_resolve_source_ingestion_id: failed to resolve ingestion for source_ids=%s",
            source_ids,
            exc_info=True,
        )
        return None


def _update_source_ingestion_fields_by_id(
    *, ingestion_id: str | None, fields: dict[str, Any]
) -> None:
    """Best-effort: patch *fields* on one specific SourceIngestion row.

    Used by flows that create a dedicated SourceIngestion per request (e.g.
    feedback-driven regeneration/revise) instead of tagging whichever
    ingestion the project's sources already happen to be linked to — so
    updating "this run's" row never touches another run's. No-ops when
    *ingestion_id* is falsy. Never raises — a bookkeeping failure here must
    not abort the generation pipeline.
    """
    from app.db.unit_of_work import UnitOfWork

    if not ingestion_id or not fields:
        return

    try:
        with UnitOfWork() as uow:
            uow.source_ingestions.update_fields(UUID(ingestion_id), **fields)
            uow.commit()
    except Exception:
        logger.warning(
            "_update_source_ingestion_fields_by_id: failed to set fields=%s for ingestion_id=%s",
            list(fields),
            ingestion_id,
            exc_info=True,
        )


def _add_source_ingestion_error(*, source_ids: list[str], error: str) -> None:
    """Best-effort: append *error* to every SourceIngestion.errors referenced by *source_ids*.

    Mirrors ``_update_source_ingestion_fields`` — resolves the distinct
    ingestion(s) linked to *source_ids* — but appends to ``errors`` via
    ``SourceIngestionRepository.add_error`` instead of overwriting a field,
    so an earlier recorded error on the same ingestion is never lost. No-ops
    on a falsy *error*. Never raises — a bookkeeping failure here must not
    abort the generation pipeline.
    """
    from app.db.unit_of_work import UnitOfWork

    if not error:
        return

    try:
        uuid_list = [UUID(sid) for sid in source_ids]
        with UnitOfWork() as uow:
            sources = uow.sources.get_many_by_uuids(uuid_list)
            ingestion_ids = {s.source_ingestion_id for s in sources if s.source_ingestion_id}
            if not ingestion_ids:
                return
            for ingestion_id in ingestion_ids:
                uow.source_ingestions.add_error(ingestion_id, error)
            uow.commit()
    except Exception:
        logger.warning(
            "_add_source_ingestion_error: failed to record error for source_ids=%s",
            source_ids,
            exc_info=True,
        )


def _add_source_ingestion_error_by_id(*, ingestion_id: str | None, error: str) -> None:
    """Best-effort: append *error* to one specific SourceIngestion row's ``errors``.

    Mirrors ``_update_source_ingestion_fields_by_id`` — targets "this run's"
    ingestion row directly rather than whichever ingestion the project's
    sources happen to be linked to. No-ops when *ingestion_id* or *error* is
    falsy. Never raises — a bookkeeping failure here must not abort the
    generation pipeline.
    """
    from app.db.unit_of_work import UnitOfWork

    if not ingestion_id or not error:
        return

    try:
        with UnitOfWork() as uow:
            uow.source_ingestions.add_error(UUID(ingestion_id), error)
            uow.commit()
    except Exception:
        logger.warning(
            "_add_source_ingestion_error_by_id: failed to record error for ingestion_id=%s",
            ingestion_id,
            exc_info=True,
        )


def _set_source_processing_error(source_id: str, error: str) -> None:
    """Best-effort: record *error* on ``Source.processing_error`` without touching status.

    For a best-effort step that fails *after* the source has already reached
    a terminal success status (e.g. domain-knowledge/architecture-document
    generation in ``source_code_task.py``, which runs once the pipeline is
    already ``READY_FOR_REVIEW``) — the failure must still be visible without
    reverting an already-successful backlog back to ``FAILED``. Unlike
    ``_mark_status``, this never changes ``source.status``. No-ops on a falsy
    *error*. Never raises.
    """
    from app.db.unit_of_work import UnitOfWork  # noqa: PLC0415

    if not error:
        return

    try:
        with UnitOfWork() as uow:
            source = uow.sources.get_by_uuid(UUID(source_id))
            if source is None:
                logger.warning("_set_source_processing_error: source_id=%s not found", source_id)
                return
            source.processing_error = error[:4000]
            uow.commit()
    except Exception:
        logger.warning(
            "_set_source_processing_error: failed to record error for source_id=%s",
            source_id,
            exc_info=True,
        )


def _add_run_stage_by_id(*, ingestion_id: str | None, stage: SourceIngestionStage) -> None:
    """Best-effort: append *stage* to one specific SourceIngestion row's ``stages``.

    Mirrors ``_update_source_ingestion_fields_by_id``/``_add_source_ingestion_error_by_id``
    — targets "this run's" ingestion row directly rather than whichever
    ingestion the project's sources happen to be linked to (the dedicated
    per-request ingestion a feedback-driven regeneration creates may have no
    linked sources at all). No-ops when *ingestion_id* is falsy. Never
    raises: a bookkeeping failure here must not abort the generation pipeline.
    """
    from app.db.unit_of_work import UnitOfWork

    if not ingestion_id:
        return

    try:
        with UnitOfWork() as uow:
            uow.source_ingestions.add_stage(UUID(ingestion_id), stage.value)
            uow.commit()
    except Exception:
        logger.warning(
            "_add_run_stage_by_id: failed to tag stage=%s for ingestion_id=%s",
            stage.value,
            ingestion_id,
            exc_info=True,
        )


def mark_sources_and_ingestion_cancelled(
    *,
    source_ids: list[str],
    project_id: str,
    task_type: str,
    stage: str,
    task_db_id: str | None = None,
    ingestion_id: str | None = None,
    extra_ingestion_fields: dict[str, Any] | None = None,
) -> None:
    """Stamp Source + SourceIngestion rows ``cancelled`` once a run stops.

    Call this once a task/pipeline has confirmed cancellation (via
    ``mark_cancelled_and_check`` at entry, or a mid-run ``CANCELLED``
    sentinel from a LangGraph pipeline) — the
    ProjectTask row and WebSocket event already move to "cancelled"
    independently of this; this keeps Source/SourceIngestion from being
    left stuck on their last in-flight status (e.g. "running").

    *ingestion_id* takes precedence when the caller owns a dedicated
    SourceIngestion row for this run (e.g. feedback-driven regeneration);
    otherwise every ingestion tagged by *source_ids* is updated. No-ops on
    an empty *source_ids* with no *ingestion_id* — cancellation before any
    source/ingestion was ever touched (e.g. a feedback-only regeneration
    run with no sources).

    *extra_ingestion_fields* lets a caller merge additional columns into the
    same SourceIngestion update — e.g. the source-code pipeline uses this to
    record how many modules/features/stories a mid-run cancel actually
    persisted (``tot_modules``/``tot_features``/``tot_user_stories``, which
    are otherwise only ever written by the normal-completion path), so a
    cancelled run's row doesn't keep showing stale/zero counts for work that
    genuinely already landed.
    """
    from app.core.enums.source_ingestion_status import SourceIngestionStatus  # noqa: PLC0415

    for source_id in source_ids:
        _mark_status(
            source_id,
            SOURCE_STATUS_CANCELLED,
            stage=stage,
            task_db_id=task_db_id,
            task_type=task_type,
            project_id=project_id,
        )

    fields = {"status": SourceIngestionStatus.CANCELLED.value, **(extra_ingestion_fields or {})}
    if ingestion_id:
        _update_source_ingestion_fields_by_id(ingestion_id=ingestion_id, fields=fields)
    elif source_ids:
        _update_source_ingestion_fields(source_ids=source_ids, fields=fields)


def describe_non_retryable_llm_error(classification: Any) -> str:
    """Detailed, DB/log-facing error string for a fatal LLM classification.

    ``classification`` is an ``app.core.llm_errors.LLMErrorClassification``
    — typed as ``Any`` here to avoid this module (imported very early, by
    every task module) taking on a hard dependency on ``app.core.llm_errors``
    at import time.
    """
    return f"[{classification.reason.value}] {classification.message}"


def cancel_sibling_tasks_on_fatal_llm_error(
    *, request_id: str | None, task_db_id: str | None, project_id: str
) -> None:
    """Best-effort: stop every other active task sharing this request now
    that this task is failing fast on a non-retryable LLM error (e.g. a
    module-feature-generation task auto-chained from ``parse_document_task``).

    Never raises — a cancellation failure must not mask the FAILED result
    already recorded for this task. The ``ProjectTaskService`` import is
    local/lazy (matching the existing convention in ``document_task_stages.py``/
    ``incremental_task.py``) so this module keeps no top-level service-layer
    dependency.
    """
    key = request_id or task_db_id
    if not key:
        return
    from app.services.project_task_service import ProjectTaskService  # noqa: PLC0415

    try:
        ProjectTaskService().cancel_sibling_tasks(
            UUID(key), exclude_task_id=UUID(task_db_id) if task_db_id else None
        )
    except Exception:
        logger.warning(
            "cancel_sibling_tasks_on_fatal_llm_error: failed project_id=%s",
            project_id,
            exc_info=True,
        )


def _get_source_ingestion_source_type(ingestion_id: str | None) -> str | None:
    """Best-effort: return the ``source_type`` of one SourceIngestion row.

    Lets completion handlers branch on the run's origin (e.g. a
    ``requirement_update`` regeneration run terminates as ``completed`` rather
    than ``ready_for_review``). Returns ``None`` when *ingestion_id* is falsy
    or the row/read fails — callers treat that as "unknown", never raising.
    """
    from app.db.unit_of_work import UnitOfWork

    if not ingestion_id:
        return None

    try:
        with UnitOfWork() as uow:
            ingestion = uow.source_ingestions.get_by_id(UUID(ingestion_id))
            return ingestion.source_type if ingestion is not None else None
    except Exception:
        logger.warning(
            "_get_source_ingestion_source_type: failed to read ingestion_id=%s",
            ingestion_id,
            exc_info=True,
        )
        return None


def _mark_sources_status_by_project(
    *,
    project_id: str,
    status: str,
    stage: str,
    task_db_id: str | None = None,
    meta: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Best-effort: apply a status transition to every source in *project_id*.

    Used by call sites (e.g. user-story generation) that only have a
    ``project_id`` in scope, not the originating ``source_ids``. Never
    raises — a bookkeeping failure here must not abort the generation
    pipeline.
    """
    from app.db.unit_of_work import UnitOfWork  # noqa: PLC0415

    try:
        with UnitOfWork() as uow:
            source_ids = [str(sid) for sid in uow.sources.get_ids_by_project(UUID(project_id))]
    except Exception:
        logger.warning(
            "_mark_sources_status_by_project: failed to resolve source_ids for project_id=%s",
            project_id,
            exc_info=True,
        )
        return

    for source_id in source_ids:
        _mark_status(
            source_id=source_id,
            status=status,
            stage=stage,
            meta=meta,
            error=error,
            task_db_id=task_db_id,
            project_id=project_id,
        )


def _increment_retry(source_id: str) -> None:
    """Atomically increment retry_count for a source row."""
    from app.db.unit_of_work import UnitOfWork

    with UnitOfWork() as uow:
        source = uow.sources.get_by_uuid(UUID(source_id))
        if source is not None:
            source.retry_count = (source.retry_count or 0) + 1
            uow.commit()


def _handle_task_exception(
    task: Any,
    source_id: str,
    exc: Exception,
    task_name: str,
    *,
    task_db_id: str | None = None,
    task_type: str = "source_process",
    project_id: str | None = None,
) -> dict:
    """Centralised retry / fail handler for all leaf parsing tasks.

    - Increments ``retry_count`` in PostgreSQL.
    - Logs the error.
    - If retries are not exhausted: publishes a real-time retry event (when
      task_db_id + project_id are provided) and raises ``task.retry()``.
    - If max retries reached: marks source as ``failed`` and returns the
      failure result dict so the caller can ``return`` it.
    """
    _increment_retry(source_id)
    logger.error(
        "%s failed: source_id=%s error=%s",
        task_name,
        source_id,
        exc,
        exc_info=True,
    )
    if task.request.retries >= task.max_retries:
        _mark_status(
            source_id,
            SOURCE_STATUS_FAILED,
            str(exc),
            stage="task.failed",
            task_db_id=task_db_id,
            task_type=task_type,
            project_id=project_id,
        )
        return {"source_id": source_id, "status": SOURCE_STATUS_FAILED, "error": str(exc)}

    # Notify the client that this attempt failed and a retry is scheduled.
    countdown = TASK_RETRY_BASE_DELAY_SECONDS * (2**task.request.retries)
    retry_num = task.request.retries + 1
    if task_db_id and project_id:
        from app.websockets.manager import publish_task_event_sync  # noqa: PLC0415

        publish_task_event_sync(
            task_db_id=task_db_id,
            celery_task_id=None,
            task_type=task_type,
            project_id=project_id,
            status=SOURCE_STATUS_RUNNING,
            stage=f"retry.{retry_num}",
            error=f"Attempt {retry_num} failed: {exc}. Retrying in {countdown}s...",
        )
    raise task.retry(exc=exc, countdown=countdown)


# ── Transient-infrastructure classification (source-code module tasks) ──────
# See docs/CELERY_RETRY_POLICY.docx Section A/D: process_single_module and
# persist_single_module retry once on these before failing — everything else
# (deterministic business/programming/config/auth errors) still fails
# immediately via their existing generic `except Exception` branch.
RETRYABLE_MODULE_INFRA: tuple[type[BaseException], ...] = (
    redis.exceptions.ConnectionError,
    redis.exceptions.TimeoutError,
    redis.exceptions.BusyLoadingError,
    redis.exceptions.ReadOnlyError,
    kombu.exceptions.OperationalError,
    ConnectionError,
    ConnectionResetError,
    BrokenPipeError,
    TimeoutError,
    socket.timeout,
    socket.gaierror,
    botocore.exceptions.EndpointConnectionError,
    botocore.exceptions.ConnectionClosedError,
    botocore.exceptions.ConnectTimeoutError,
    botocore.exceptions.ReadTimeoutError,
    botocore.exceptions.IncompleteReadError,
)

# Only relevant to persist_single_module_task, which writes live to Neo4j
# (process_single_module never touches Neo4j).
RETRYABLE_NEO4J_INFRA: tuple[type[BaseException], ...] = (
    neo4j.exceptions.ServiceUnavailable,
    neo4j.exceptions.SessionExpired,
    neo4j.exceptions.TransientError,
)

_BOTO_RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}
_BOTO_RETRYABLE_CODES = {
    "Throttling",
    "ThrottlingException",
    "SlowDown",
    "RequestTimeout",
    "ServiceUnavailable",
    "InternalError",
}


def is_retryable_boto(exc: botocore.exceptions.ClientError) -> bool:
    """True for a transient S3/AWS ClientError worth one retry (throttling, 5xx).

    False for deterministic errors (AccessDenied, InvalidAccessKeyId,
    NoSuchKey, ExpiredToken, 403/404) that a retry can't fix.
    """
    metadata = exc.response.get("ResponseMetadata", {})
    code = exc.response.get("Error", {}).get("Code", "")
    return (
        metadata.get("HTTPStatusCode") in _BOTO_RETRYABLE_HTTP_STATUS
        or code in _BOTO_RETRYABLE_CODES
    )


def emit_task_event(
    *,
    task_db_id: str | None,
    project_id: str,
    task_type: str,
    status: str,
    stage: str,
    progress: int,
    meta: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Publish a task progress event (ProjectTask DB row + Redis pub/sub).

    This is the single place all Celery tasks call to report progress to
    connected WebSocket clients.  It is a no-op when *task_db_id* is not
    provided (task tracking disabled / tests without a task row).
    """
    if not task_db_id:
        return
    from app.websockets.manager import publish_task_event_sync  # noqa: PLC0415

    publish_task_event_sync(
        task_db_id=task_db_id,
        celery_task_id=None,
        task_type=task_type,
        project_id=project_id,
        status=status,
        progress=progress,
        stage=stage,
        meta=meta,
        error=error,
    )


async def _upsert_neo4j_file_node(source_id: str, neo4j_repo: Any) -> None:
    """Load source from PostgreSQL and upsert its :File node in Neo4j.

    Shared by ``image_task`` and ``source_code_task`` to avoid duplicating
    the UnitOfWork + source lookup + ``upsert_file_node`` pattern.
    """
    from app.db.unit_of_work import UnitOfWork
    from app.repositories.neo4j.source_repository import SourceRepository

    with UnitOfWork() as uow:
        source = uow.sources.get_by_uuid(UUID(source_id))
        if source is not None:
            await neo4j_repo.upsert_file_node(SourceRepository.orm_to_node(source))


async def _download_source_zip_from_s3(
    source_id: str,
    storage_key: str,
) -> bytes:
    """Download source ZIP archive from S3.

    Args:
        source_id: UUID of the source (for logging)
        storage_key: S3 object key / path

    Returns:
        ZIP file bytes
    """
    from app.clients.aws_session import get_aws_session
    from app.core.config import settings

    logger.info("Downloading source ZIP from S3: source_id=%s key=%s", source_id, storage_key)
    async with get_aws_session().client("s3") as s3:
        response = await s3.get_object(
            Bucket=settings.AWS_S3_SOURCES_BUCKET,
            Key=storage_key,
        )
        zip_bytes: bytes = await response["Body"].read()
    logger.info("Downloaded %d bytes from S3 for source_id=%s", len(zip_bytes), source_id)
    return zip_bytes


def _assert_zip_members_are_safe(zf: Any, destination: Any) -> None:
    """Reject ZIP archives containing path-traversal or absolute-path entries.

    ``zipfile.ZipFile.extractall`` does not defend against malicious archive
    entries such as ``../../etc/passwd`` or ``/etc/passwd`` (CWE-22): those
    entries can escape ``destination`` and overwrite arbitrary files on the
    host filesystem. This validates every member name *before* any extraction
    happens, so a single unsafe entry aborts the whole archive.

    Args:
        zf: Open ``zipfile.ZipFile`` handle to inspect.
        destination: Directory the archive is about to be extracted into.

    Raises:
        ValueError: If any member resolves outside ``destination``.
    """
    destination_root = destination.resolve()
    for member in zf.namelist():
        # Reject POSIX/Windows absolute paths and Windows drive-letter paths
        # (e.g. "/etc/passwd", "\\etc\\passwd", "C:\\Windows\\system32\\...").
        if member.startswith("/") or member.startswith("\\"):
            raise ValueError(f"Unsafe ZIP entry (absolute path): {member!r}")
        if len(member) > 1 and member[1] == ":":
            raise ValueError(f"Unsafe ZIP entry (absolute path): {member!r}")

        resolved = (destination_root / member).resolve()
        if resolved != destination_root and destination_root not in resolved.parents:
            raise ValueError(f"Unsafe ZIP entry (path traversal): {member!r}")


def _extract_zip_to_codebase_folder(
    zip_bytes: bytes,
    project_id: str,
) -> str:
    """Extract ZIP archive to ./temp/source_codes/{project_id}/codebase.

    Creates the folder structure and extracts all files from the ZIP.

    Args:
        zip_bytes: ZIP file content (bytes)
        project_id: Project UUID (used in folder path)

    Returns:
        Absolute path to the extracted codebase folder

    Raises:
        ValueError: If the archive contains an unsafe (path-traversal or
            absolute-path) entry — see ``_assert_zip_members_are_safe``.
    """
    from io import BytesIO
    from pathlib import Path
    import shutil
    import zipfile

    # Get project root directory
    root_dir = Path(__file__).resolve().parent.parent.parent

    # Define codebase directory path
    codebase_dir = root_dir / "temp" / "source_codes" / project_id / "codebase"

    # Clean if exists, then create
    if codebase_dir.exists():
        shutil.rmtree(codebase_dir)

    codebase_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Created codebase directory: %s", codebase_dir)

    # Extract ZIP to the folder — validate every member first (CWE-22 guard).
    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        _assert_zip_members_are_safe(zf, codebase_dir)
        zf.extractall(codebase_dir)

    logger.info("Extracted ZIP to codebase folder: %s", codebase_dir)
    return str(codebase_dir)
