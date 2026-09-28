"""Celery task for parsing source-code ZIP archives via Tree-sitter.

Each file inside the ZIP is parsed with Tree-sitter to extract functions,
classes, and methods.  Extracted entities are written to Neo4j as
:Function / :Class nodes with DEFINED_IN and CALLS relationships.

Supported languages are detected by file extension.  Files with
unrecognised extensions are silently skipped.

Retry strategy: exponential backoff — 60 s, 120 s, 240 s.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import botocore.exceptions
from celery import chain
from celery.exceptions import SoftTimeLimitExceeded

from app.core.celery_app import celery_app
from app.core.constants import (
    COUNTER_TTL_SECONDS,
    INITIAL_ENTITY_VERSION,
    LOCK_TTL_SECONDS,
    SOURCE_CODE_CONCURRENT_PIPELINE_RETRY_COUNTDOWN_SECONDS,
    SOURCE_CODE_TASK_MAX_RETRIES,
    SOURCE_CODE_TASK_RETRY_COUNTDOWN_SECONDS,
    SOURCE_INGESTION_STATUS_FAILED,
    SOURCE_INGESTION_STATUS_READY_FOR_REVIEW,
    SOURCE_INGESTION_STATUS_RUNNING,
    SOURCE_STATUS_FAILED,
    SOURCE_STATUS_READY_FOR_REVIEW,
    SOURCE_STATUS_RUNNING,
    TASK_AI_SOFT_TIME_LIMIT,
    TASK_AI_TIME_LIMIT,
    TASK_PERSIST_SINGLE_MODULE_SOFT_TIME_LIMIT,
    TASK_PERSIST_SINGLE_MODULE_TIME_LIMIT,
    TASK_SOURCE_CODE_MODULE_SOFT_TIME_LIMIT,
    TASK_SOURCE_CODE_MODULE_TIME_LIMIT,
    TASK_SOURCE_CODE_PIPELINE_SOFT_TIME_LIMIT,
    TASK_SOURCE_CODE_PIPELINE_TIME_LIMIT,
    TASK_STATUS_CANCELLED,
)
from app.core.enums.activity_type import ActivityType
from app.core.enums.source_ingestion_stage import SourceIngestionStage
from app.core.enums.source_ingestion_status import SourceIngestionStatus
from app.core.messages import (
    MSG_ACTIVITY_SOURCE_CODE_ARCHITECTURE_DOCUMENT_COMPLETED,
    MSG_ACTIVITY_SOURCE_CODE_ARCHITECTURE_DOCUMENT_FAILED,
    MSG_ACTIVITY_SOURCE_CODE_DOMAIN_KNOWLEDGE_COMPLETED,
    MSG_ACTIVITY_SOURCE_CODE_DOMAIN_KNOWLEDGE_FAILED,
    MSG_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATED,
    MSG_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATION_CANCELLED,
    MSG_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATION_FAILED,
    MSG_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATION_STARTED,
    MSG_ACTIVITY_SOURCE_CODE_GLOBAL_ARTIFACTS_COMPLETED,
    MSG_ACTIVITY_SOURCE_CODE_MODULE_CANCELLED,
    MSG_ACTIVITY_SOURCE_CODE_MODULE_COMPLETED,
    MSG_ACTIVITY_SOURCE_CODE_MODULE_FAILED,
    MSG_ACTIVITY_SOURCE_CODE_MODULE_STARTED,
    MSG_ACTIVITY_SOURCE_CODE_PIPELINE_COMPLETED,
    MSG_ACTIVITY_SOURCE_CODE_PIPELINE_FAILED,
    MSG_ACTIVITY_SOURCE_CODE_PIPELINE_STARTED,
    MSG_FEATURE_REGENERATION_TIME_LIMIT_EXCEEDED,
    MSG_SOURCE_CODE_MODULE_PERSIST_TIME_LIMIT_EXCEEDED,
    MSG_SOURCE_CODE_MODULE_TIME_LIMIT_EXCEEDED,
    SUMMARY_ACTIVITY_SOURCE_CODE_ARCHITECTURE_DOCUMENT_COMPLETED,
    SUMMARY_ACTIVITY_SOURCE_CODE_ARCHITECTURE_DOCUMENT_FAILED,
    SUMMARY_ACTIVITY_SOURCE_CODE_DOMAIN_KNOWLEDGE_COMPLETED,
    SUMMARY_ACTIVITY_SOURCE_CODE_DOMAIN_KNOWLEDGE_FAILED,
    SUMMARY_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATED,
    SUMMARY_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATION_CANCELLED,
    SUMMARY_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATION_FAILED,
    SUMMARY_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATION_STARTED,
    SUMMARY_ACTIVITY_SOURCE_CODE_GLOBAL_ARTIFACTS_COMPLETED,
    SUMMARY_ACTIVITY_SOURCE_CODE_MODULE_CANCELLED,
    SUMMARY_ACTIVITY_SOURCE_CODE_MODULE_COMPLETED,
    SUMMARY_ACTIVITY_SOURCE_CODE_MODULE_FAILED,
    SUMMARY_ACTIVITY_SOURCE_CODE_MODULE_STARTED,
    SUMMARY_ACTIVITY_SOURCE_CODE_PIPELINE_COMPLETED,
    SUMMARY_ACTIVITY_SOURCE_CODE_PIPELINE_FAILED,
    SUMMARY_ACTIVITY_SOURCE_CODE_PIPELINE_STARTED,
)
from app.schemas.config_spec_schema import SourceCodeConfigSpecSchema
from app.schemas.group_spec_schema import SourceCodeGroupSpecSchema
from app.schemas.source_code_metadata_schema import SourceCodeMetadataSchema
from app.schemas.srs_evidence_schema import SRSEvidenceInfo
from app.schemas.user_story_schema import SourceCodeStorySchema, UserStoryStatus
from app.services.activity_log_service import record_activity, resolve_actor_from_task
from app.utils.logger import get_logger
from app.workers._task_helpers import (
    RETRYABLE_MODULE_INFRA,
    RETRYABLE_NEO4J_INFRA,
    _add_run_stage_by_id,
    _add_source_ingestion_error,
    _add_source_ingestion_error_by_id,
    _increment_source_ingestion_module_counts,
    _mark_status,
    _resolve_source_ingestion_id,
    _run_async,
    _set_source_processing_error,
    _update_source_ingestion_fields,
    _update_source_ingestion_fields_by_id,
    emit_task_event,
    is_retryable_boto,
    mark_cancelled_and_check,
    mark_sources_and_ingestion_cancelled,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# NOTE: Ensure your Celery configuration contains the following to prevent
# module_result being dropped or arriving as None in _persist_single_module_task:
#
#   task_serializer   = "json"
#   result_serializer = "json"
#   accept_content    = ["json"]
#
# Without this, the chained result may be silently corrupted.
# ---------------------------------------------------------------------------


# ── Cleanup helpers ─────────────────────────────────────────────────────────


def _cleanup_project_source_code_folder(project_id: str) -> None:
    """Remove extracted source-code temp folder for a completed project."""
    project_root = Path(__file__).resolve().parents[2]
    source_code_dir = project_root / "temp" / "source_codes" / project_id

    if not source_code_dir.exists():
        logger.info("Source-code temp folder already absent: path=%s", source_code_dir)
        return

    try:
        shutil.rmtree(source_code_dir)
        logger.info("Deleted source-code temp folder: path=%s", source_code_dir)
    except Exception as exc:
        logger.warning(
            "Failed to delete source-code temp folder for project_id=%s path=%s error=%s",
            project_id,
            source_code_dir,
            exc,
            exc_info=True,
        )


@celery_app.task(
    bind=True,
    name="tasks.parse_code.cleanup_project_folder",
    acks_late=True,
    ignore_result=True,
)
def _cleanup_project_folder_task(self, project_id: str, cancelled_at: str | None = None) -> None:
    """
    Deferred cleanup task.

    Scheduled with a countdown (default 30 s) so that any still-running
    _process_single_module_task workers finish their I/O before the temp
    directory is removed.  This task is fire-and-forget; failures are
    logged but do not affect the overall pipeline status.

    **Guarded against wiping a live run** when dispatched from a
    cancellation: unlike ``_delete_project_backlog_task``'s Neo4j delete,
    this temp folder (``temp/source_codes/{project_id}``) is project-scoped
    rather than run-scoped, so a run started between the cancel and this
    task's 30s-delayed execution would have its freshly downloaded/extracted
    files deleted out from under it. *cancelled_at* (ISO-8601, from
    :func:`_utc_now_iso`), when provided, is compared against every active
    task for the project the same way :func:`_delete_project_backlog_task`
    does — skip if any task started at/after that instant. Normal-completion
    callers of this task never pass *cancelled_at*, so their cleanup is
    unaffected.
    """
    try:
        if cancelled_at:
            from app.db.unit_of_work import UnitOfWork  # noqa: PLC0415

            with UnitOfWork() as uow:
                active = uow.project_tasks.list_active_by_project(UUID(project_id))
            newer = [task for task in active if _started_at_or_after(task, cancelled_at)]
            if newer:
                logger.info(
                    "Deferred temp-folder cleanup skipped for project_id=%s — %d task(s) "
                    "started at/after the cancellation (%s); a live run's files must not "
                    "be wiped.",
                    project_id,
                    len(newer),
                    cancelled_at,
                )
                return
        _cleanup_project_source_code_folder(project_id)
    except Exception as exc:
        logger.warning(
            "Deferred cleanup failed for project_id=%s: %s",
            project_id,
            exc,
            exc_info=True,
        )


def _started_at_or_after(task: Any, cancelled_at: str | None) -> bool:
    """True if *task* began at/after *cancelled_at* — i.e. it is a newer run.

    Conservative by design: an absent timestamp, a missing ``created_at``, or
    any value the two cannot be compared against each other returns True, so
    :func:`_delete_project_backlog_task` skips rather than risk deleting a
    live run's backlog. Naive datetimes are read as UTC, matching how the
    ``created_at`` column is written.
    """
    if not cancelled_at:
        return True
    try:
        threshold = datetime.fromisoformat(cancelled_at)
        created_at = getattr(task, "created_at", None)
        if created_at is None:
            return True
        if threshold.tzinfo is None:
            threshold = threshold.replace(tzinfo=UTC)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        return created_at >= threshold
    except Exception:
        return True


@celery_app.task(
    bind=True,
    name="tasks.parse_code.delete_project_backlog",
    acks_late=True,
    ignore_result=True,
)
def _delete_project_backlog_task(self, project_id: str, cancelled_at: str | None = None) -> None:
    """Deferred whole-project backlog delete, run after a cancellation.

    Split out of :func:`_finalize_source_code_cancellation` so the delete
    never runs inside the cancel request. It is the single slowest step of a
    rollback (a multi-statement Neo4j delete over every Module/Feature/
    UserStory and their version snapshots), and making the user's ``DELETE``
    call wait on it was the bulk of the "cancel takes forever" latency. The
    caller now sees ``cancelled`` immediately and this lands a moment later.

    **Guarded against wiping a live run**: because the delete is
    whole-project scoped rather than run-scoped, a run started between the
    cancel and this task executing would have its freshly generated backlog
    destroyed. *cancelled_at* (ISO-8601, from :func:`_utc_now_iso`) is the
    moment the cancellation was finalized, and only an active task created at
    or after it counts as such a run.

    Comparing against that instant — rather than skipping whenever the
    project has any active task at all — matters because nothing else in the
    system ever clears a project's backlog. Six task types create
    ``ProjectTask`` rows (``source_process``, ``module_regeneration``,
    ``story_regeneration``, ``story_generation``, ``story_feedback_patch``,
    ``feature_regeneration``); with a bare "is anything active?" check, an
    unrelated regeneration that happened to be running when the user hit
    cancel would suppress the rollback permanently, and the cancelled run's
    partial modules/features would stay in the graph forever — silently
    merging into the next run's output, since writes are MERGE-by-id and only
    overwrite what gets regenerated.

    Skips conservatively: if *cancelled_at* is absent or any active task's
    ``created_at`` cannot be compared to it, the delete is skipped rather
    than risking a live run's data.

    Fire-and-forget: never raises. A Neo4j or Postgres failure here leaves
    stale backlog rows behind, which is recoverable; letting it bubble would
    put a cleanup task into Celery's retry loop for no benefit.
    """
    try:
        from app.db.unit_of_work import UnitOfWork  # noqa: PLC0415

        with UnitOfWork() as uow:
            active = uow.project_tasks.list_active_by_project(UUID(project_id))
        newer = [task for task in active if _started_at_or_after(task, cancelled_at)]
        if newer:
            logger.info(
                "Deferred backlog delete skipped for project_id=%s — %d task(s) started "
                "at/after the cancellation (%s); their output must not be wiped.",
                project_id,
                len(newer),
                cancelled_at,
            )
            return

        _delete_source_code_backlog(project_id)
    except Exception as exc:
        logger.warning(
            "Deferred backlog delete failed for project_id=%s: %s",
            project_id,
            exc,
            exc_info=True,
        )


# ── Utility helpers ─────────────────────────────────────────────────────────


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _add_source_code_stage(source_id: str, stage: SourceIngestionStage) -> None:
    """Best-effort: append *stage* to the SourceIngestion.stages array for *source_id*.

    Source-code-pipeline-only checkpoint markers (Ingesting Sources / Code
    Dependency Analysis / Discovering Modules / Extracting Requirements),
    appended alongside — not instead of — the module_feature/user_story
    stage values already tagged onto the ingestion at upload time (both
    families are SourceIngestionStage members). Never
    raises: a bookkeeping failure here must not abort the generation
    pipeline.
    """
    from app.db.unit_of_work import UnitOfWork
    from app.services.source_ingestion_service import SourceIngestionService

    try:
        with UnitOfWork() as uow:
            SourceIngestionService.add_stage_by_source_ids(uow, [UUID(source_id)], stage)
            uow.commit()
    except Exception:
        logger.warning(
            "_add_source_code_stage: failed to tag stage=%s for source_id=%s",
            stage.value,
            source_id,
            exc_info=True,
        )


def _get_module_id(module_result: Any) -> str:
    """Best-effort module id extraction for logging."""
    try:
        return str(module_result.get("module_id") or "")
    except Exception:
        return ""


def _normalize_story_nfrs(raw_nfrs: Any) -> list[dict[str, str]]:
    """Keep only the supported NFR fields from source-code story outputs."""
    if not isinstance(raw_nfrs, list):
        return []

    normalized: list[dict[str, str]] = []
    for item in raw_nfrs:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "id": str(item.get("id") or ""),
                "category": str(item.get("category") or ""),
                "description": str(item.get("description") or ""),
                "requirement": str(item.get("requirement") or ""),
            }
        )
    return normalized


def _build_display_module_name(module_code: str, module_name: str) -> str:
    """Return a display module name prefixed with code when the code is missing."""
    if not module_code:
        return module_name

    normalized_module_name = module_name.casefold()
    normalized_module_code = module_code.casefold()
    if normalized_module_code in normalized_module_name:
        return module_name

    return f"{module_code} - {module_name}" if module_name else module_code


def _build_module_feature_id_maps(
    stored_modules: list[Any],
) -> tuple[dict[str, str], dict[str, str]]:
    """Build lookup maps from module/feature codes to persisted Neo4j ids."""
    module_id_by_code: dict[str, str] = {}
    feature_id_by_code: dict[str, str] = {}

    for module in stored_modules:
        mod_code = str(getattr(module, "mod_code", "") or "")
        mod_name = str(getattr(module, "mod_name", "") or "")
        mod_code_name_key = _build_display_module_name(mod_code, mod_name)
        module_id = str(getattr(module, "id", "") or "")
        if mod_code_name_key and module_id:
            module_id_by_code[mod_code_name_key] = module_id

        for feature in getattr(module, "features", []) or []:
            fea_code = str(getattr(feature, "fea_code", "") or "")
            feature_id = str(getattr(feature, "id", "") or "")
            if fea_code and feature_id:
                feature_id_by_code[fea_code] = feature_id

    return module_id_by_code, feature_id_by_code


# ── Pipeline result builders ─────────────────────────────────────────────────


def _normalize_functions_payload(raw_functions: list[Any]) -> list[dict[str, Any]]:
    """Map raw pipeline function items (``id``/``label``/``l2_source_ref``) to the
    canonical ``Feature.functions`` shape (``fun_code``/``name``/``func_src_ref``)
    expected by ``ModuleFeatureRepository._parse_functions``/``FunctionModel``.
    """
    functions_payload: list[dict[str, Any]] = []
    for fn in raw_functions or []:
        if not isinstance(fn, dict):
            continue
        functions_payload.append(
            {
                "fun_code": str(fn.get("id") or ""),
                "name": str(fn.get("label") or fn.get("function_label") or fn.get("name") or ""),
                "description": fn.get("description") or "",
                "func_src_ref": fn.get("l2_source_ref"),
            }
        )
    return functions_payload


def _build_module_feature_skeleton_from_results(
    module_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a ModuleFeatureService-compatible skeleton from pipeline module results."""
    feature_inventory: list[dict[str, Any]] = []

    for module_result in module_results:
        feature_derivation = module_result.get("feature_derivation") or {}
        for derivation_result in feature_derivation.get("results", []):
            module_code = str(derivation_result.get("module_id") or "")
            module_name = str(derivation_result.get("module_name") or "")
            module_description = derivation_result.get("module_business_description") or ""

            features_payload: list[dict[str, Any]] = []
            for feature_idx, feature in enumerate(derivation_result.get("features", []), start=0):
                feature_id = str(feature.get("id") or "")
                functions_payload = _normalize_functions_payload(feature.get("functions", []))

                features_payload.append(
                    {
                        "fea_code": feature_id,
                        "mfu_id": derivation_result.get("mfu_id"),
                        "name": str(feature.get("title") or ""),
                        "description": feature.get("description") or "",
                        "generation_metadata": derivation_result.get("generation_metadata"),
                        "is_infrastructure": feature.get("is_infrastructure"),
                        "condensation_note": feature.get("condensation_note"),
                        "functions": functions_payload,
                        "l2_sources": [
                            str(ref)
                            for ref in (feature.get("l2_sources") or [])
                            if isinstance(ref, str) and ref
                        ],
                    }
                )

            if not features_payload:
                logger.info(
                    "No features derived for module_id=%s module_name=%s — skipping module in skeleton.",
                    module_code,
                    module_name,
                )
                continue

            display_module_name = _build_display_module_name(module_code, module_name)

            feature_inventory.append(
                {
                    "mod_code": module_code,
                    "name": display_module_name,
                    "description": module_description,
                    "features": features_payload,
                }
            )

    return {"feature_inventory": feature_inventory}


def _resolve_screens_storage_keys(
    raw_screens: list[Any],
    storage_key_by_filename: dict[str, str],
) -> list[dict[str, Any]]:
    """Return screens with ascii_layout_ref.storage_key populated from group spec mapping."""
    resolved: list[dict[str, Any]] = []
    for screen in raw_screens:
        if not isinstance(screen, dict):
            continue
        screen_copy = dict(screen)
        raw_ref = screen_copy.get("ascii_layout_ref")
        if isinstance(raw_ref, dict):
            ref_copy = dict(raw_ref)
            srs_file = str(ref_copy.get("srs_file") or "")
            if srs_file and srs_file in storage_key_by_filename:
                ref_copy["storage_key"] = storage_key_by_filename[srs_file]
            screen_copy["ascii_layout_ref"] = ref_copy
        resolved.append(screen_copy)
    return resolved


def _build_backlog_result_from_results(
    project_id: str,
    source_id: str,
    module_results: list[dict[str, Any]],
    group_spec_id_by_filename: dict[str, str] | None = None,
    storage_key_by_filename: dict[str, str] | None = None,
    module_id_by_code: dict[str, str] | None = None,
    feature_id_by_code: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build an AgileBacklogOutput-compatible payload from feature_derivation user stories."""
    epics: list[dict[str, Any]] = []
    group_spec_id_by_filename = group_spec_id_by_filename or {}
    storage_key_by_filename = storage_key_by_filename or {}
    module_id_by_code = module_id_by_code or {}
    feature_id_by_code = feature_id_by_code or {}
    now = _utc_now_iso()

    for module_idx, module_result in enumerate(module_results, start=0):
        feature_derivation = module_result.get("feature_derivation") or {}
        for derivation_result in feature_derivation.get("results", []):
            module_code = str(derivation_result.get("module_id") or "")
            module_name = str(derivation_result.get("module_name") or "")
            mod_code_name_key = _build_display_module_name(module_code, module_name)
            resolved_module_id = module_id_by_code.get(mod_code_name_key, "")

            for feature_idx, feature in enumerate(derivation_result.get("features", []), start=0):
                feature_code = str(feature.get("id") or "")
                resolved_feature_id = feature_id_by_code.get(feature_code, "")
                stories_payload: list[dict[str, Any]] = []
                srs_evidence_payload: list[dict[str, Any]] = []

                for story_idx, story in enumerate(feature.get("user_stories", []), start=0):
                    user_story_code = str(story.get("id") or "")

                    if project_id and user_story_code:
                        user_story_id = str(
                            uuid5(NAMESPACE_URL, f"{project_id}:{user_story_code.strip()}")
                        )
                    else:
                        user_story_id = str(uuid4())

                    acceptance_criteria = []
                    for ac in story.get("acceptance_criteria", []):
                        ac_payload = {
                            "type": ac.get("path_type") or "",
                            "given": ac.get("given") or "",
                            "when": ac.get("when") or "",
                            "then": ac.get("then") or "",
                        }

                        # Optional source-code acceptance-criteria metadata.
                        if ac.get("id") is not None:
                            ac_payload["id"] = str(ac.get("id"))
                        if ac.get("l2_source_ref") is not None:
                            ac_payload["l2_source_ref"] = str(ac.get("l2_source_ref"))

                        acceptance_criteria.append(ac_payload)

                    story_payload = {
                        "user_story_id": user_story_id,
                        "user_story_code": user_story_code,
                        "module_id": resolved_module_id,
                        "feature_id": resolved_feature_id,
                        "title": str(story.get("title") or ""),
                        "as_a": str(story.get("as_a") or ""),
                        "i_want_to": str(story.get("i_want_to") or ""),
                        "so_that": str(story.get("so_that") or ""),
                        "acceptance_criteria": acceptance_criteria,
                        "technical_notes": str(story.get("technical_notes") or ""),
                        "story_points": int(story.get("story_points") or 1),
                        "l2_sources": [
                            str(ref)
                            for ref in (story.get("l2_sources") or [])
                            if isinstance(ref, str) and ref
                        ],
                        # "sources": [
                        #     {
                        #         "fragment_id": "",
                        #         "source_id": source_id,
                        #         "page": 1,
                        #         "bbox": {"x": 0.0, "y": 0.0, "w": 0.0, "h": 0.0},
                        #     }
                        # ],
                    }

                    raw_story_nfrs = (
                        story.get("nfrs")
                        if "nfrs" in story
                        else story.get("non_functional_requirements")
                    )
                    if isinstance(raw_story_nfrs, list):
                        story_payload["nfrs"] = _normalize_story_nfrs(raw_story_nfrs)

                    raw_screens = story.get("screens")
                    if isinstance(raw_screens, list):
                        story_payload["screens"] = _resolve_screens_storage_keys(
                            raw_screens, storage_key_by_filename
                        )

                    stories_payload.append(
                        SourceCodeStorySchema.model_validate(story_payload).model_dump(mode="json")
                    )

                    for evidence in story.get("srs_evidence", []):
                        raw_section_path = evidence.get("section_path")
                        section_path: list[str] = []
                        if isinstance(raw_section_path, list):
                            section_path = [
                                str(item) for item in raw_section_path if item is not None
                            ]

                        raw_line_number = evidence.get("line_number")
                        line_number: int | None = None
                        if raw_line_number not in (None, ""):
                            try:
                                line_number = int(raw_line_number)
                            except (TypeError, ValueError):
                                line_number = None

                        raw_ac_ids = evidence.get("ac_ids")
                        ac_ids: list[str] = []
                        if isinstance(raw_ac_ids, list):
                            ac_ids = [str(item) for item in raw_ac_ids if item is not None]

                        srs_evidence_row = {
                            "id": str(uuid4()),
                            "module_code": module_code,
                            "feature_code": feature_code,
                            "user_story_code": user_story_code,
                            "group_spec_id": group_spec_id_by_filename.get(
                                evidence.get("file_name") or "", ""
                            ),
                            "user_story_id": user_story_id,
                            "l2_id": str(evidence.get("l2_id") or ""),
                            "file_name": str(evidence.get("file_name") or ""),
                            "section_anchor": str(evidence.get("section_anchor") or ""),
                            "section_path": section_path,
                            "highlight_type": str(evidence.get("highlight_type") or ""),
                            "target_string": str(evidence.get("target_string") or ""),
                            "exact_quote": str(evidence.get("exact_quote") or ""),
                            "line_number": line_number,
                            "precision": str(evidence.get("precision") or ""),
                            "evidence_role": str(evidence.get("evidence_role") or ""),
                            "srs_document_type": str(evidence.get("srs_document_type") or ""),
                            "ac_ids": ac_ids,
                            "trace_id": str(evidence.get("trace_id") or ""),
                            "context_snippet": str(evidence.get("context_snippet") or ""),
                            "tier": str(evidence.get("tier") or ""),
                            "created_at": now,
                            "updated_at": now,
                        }
                        srs_evidence_payload.append(
                            SRSEvidenceInfo.model_validate(srs_evidence_row).model_dump(mode="json")
                        )

                epics.append(
                    {
                        "epic_code": f"EPIC-{resolved_module_id or module_idx}-{resolved_feature_id or feature_idx}",
                        "title": module_name,
                        "parent_br": [],
                        "feature_id": resolved_feature_id,
                        "feature_name": str(feature.get("title") or ""),
                        "description": str(feature.get("description") or ""),
                        "stories": stories_payload,
                        "srs_evidence": srs_evidence_payload,
                        "generation_metadata": derivation_result.get("generation_metadata"),
                    }
                )

    return {
        "output": {
            "persona_glossary": [],
            "epics": epics,
        }
    }


def _collect_group_spec_rows(
    *,
    project_id: str,
    source_id: str,
    module_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    now = _utc_now_iso()

    for module_result in module_results:
        grouped_specs = (module_result.get("spec_generation") or {}).get("grouped_specs") or {}
        items = grouped_specs.get("markdown_specs") or []
        if not isinstance(items, list):
            continue

        for item in items:
            mod_code = str(item.get("module_id") or module_result.get("module_id") or "")
            fea_code = str(item.get("feature_unit_id") or "")
            filename = str(item.get("filename") or "")
            deterministic_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"{project_id}:{source_id}:{mod_code}:{fea_code}:{filename}",
                )
            )

            group_spec_row = {
                "id": deterministic_id,
                "project_id": project_id,
                "source_id": source_id,
                "mod_code": mod_code,
                "fea_code": fea_code,
                "filename": filename,
                "storage_key": None,
                "created_at": now,
                "updated_at": now,
                "content": item.get("content"),
            }
            rows.append(
                SourceCodeGroupSpecSchema.model_validate(group_spec_row).model_dump(mode="json")
            )

    return rows


def _collect_config_spec_rows(
    *,
    project_id: str,
    source_id: str,
    module_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    now = _utc_now_iso()

    for module_result in module_results:
        grouped_specs = (module_result.get("spec_generation") or {}).get("grouped_specs") or {}
        items = grouped_specs.get("config_specs") or []
        if not isinstance(items, list):
            continue

        for item in items:
            mod_code = str(item.get("module_id") or module_result.get("module_id") or "")
            fea_code = str(item.get("feature_unit_id") or "")
            filename = str(item.get("filename") or "")
            deterministic_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"{project_id}:{source_id}:{mod_code}:{fea_code}:{filename}",
                )
            )

            config_spec_row = {
                "id": deterministic_id,
                "project_id": project_id,
                "source_id": source_id,
                "mod_code": mod_code,
                "fea_code": fea_code,
                "filename": filename,
                "storage_key": None,
                "created_at": now,
                "updated_at": now,
                "content": item.get("content"),
            }
            rows.append(
                SourceCodeConfigSpecSchema.model_validate(config_spec_row).model_dump(mode="json")
            )

    return rows


# ── Neo4j persistence helpers ────────────────────────────────────────────────


async def _persist_domain_knowledge_artifact(
    *,
    project_id: str,
    content: str,
) -> str | None:
    """Upload the generated domain-knowledge markdown to S3 and record its key on :ProjectMetadata.

    Best-effort: a storage failure here must not abort a pipeline run that
    already produced module/feature/user-story results. Returns the S3
    storage key on success, or ``None`` if there was no content to persist or
    the upload/update failed.
    """
    if not content:
        return None

    from app.clients.s3_client import upload_to_s3

    storage_key = f"projects/{project_id}/pipeline-artifacts/domain_knowledge.md"
    try:
        await upload_to_s3(
            file_bytes=content.encode("utf-8"),
            object_key=storage_key,
            content_type="text/markdown; charset=utf-8",
        )

        def _update_metadata() -> None:
            from app.db.neo4j import get_neo4j_driver
            from app.repositories.neo4j.project_metadata_repository import (
                ProjectMetadataRepository,
            )

            ProjectMetadataRepository(get_neo4j_driver()).update(
                project_id=UUID(project_id),
                domain_knowledge_storage_key=storage_key,
            )

        await asyncio.to_thread(_update_metadata)
    except Exception:
        logger.exception(
            "Failed to persist domain-knowledge artifact for project_id=%s",
            project_id,
        )
        return None

    logger.info(
        "Domain-knowledge artifact stored: project_id=%s storage_key=%s",
        project_id,
        storage_key,
    )
    return storage_key


async def _persist_architecture_document_artifact(
    *,
    project_id: str,
    content: str,
) -> str | None:
    """Upload the generated architecture-document markdown to S3 and record its key on :ProjectMetadata.

    Best-effort: a storage failure here must not abort a pipeline run that
    already produced module/feature/user-story results. Returns the S3
    storage key on success, or ``None`` if there was no content to persist or
    the upload/update failed.
    """
    if not content:
        return None

    from app.clients.s3_client import upload_to_s3

    storage_key = f"projects/{project_id}/pipeline-artifacts/architecture_document.md"
    try:
        await upload_to_s3(
            file_bytes=content.encode("utf-8"),
            object_key=storage_key,
            content_type="text/markdown; charset=utf-8",
        )

        def _update_metadata() -> None:
            from app.db.neo4j import get_neo4j_driver
            from app.repositories.neo4j.project_metadata_repository import (
                ProjectMetadataRepository,
            )

            ProjectMetadataRepository(get_neo4j_driver()).update(
                project_id=UUID(project_id),
                architecture_document_storage_key=storage_key,
            )

        await asyncio.to_thread(_update_metadata)
    except Exception:
        logger.exception(
            "Failed to persist architecture-document artifact for project_id=%s",
            project_id,
        )
        return None

    logger.info(
        "Architecture-document artifact stored: project_id=%s storage_key=%s",
        project_id,
        storage_key,
    )
    return storage_key


def _record_pipeline_documents_failure(
    *,
    project_id: str,
    source_id: str,
    error: str,
) -> None:
    """Surface a domain-knowledge/architecture-document generation failure.

    Called once per ``_generate_and_persist_pipeline_documents`` run when at
    least one of the two artifacts failed. By this point the main module
    pipeline has already finished and finalized Source/SourceIngestion as
    READY_FOR_REVIEW — the backlog itself is still valid and usable, so this
    deliberately does NOT revert either status to FAILED. It only records
    the error message (so it's visible on the source/ingestion rows) and
    notifies the project owner ONCE per run, even when both artifacts
    failed — the per-artifact activity-log entry (which artifact, which
    error) is recorded separately by the caller via
    ``_record_stage_activity`` so the notification itself doesn't double up.
    Never raises.
    """
    _set_source_processing_error(source_id, error)
    _add_source_ingestion_error(source_ids=[source_id], error=error)

    from app.core.enums.notification_type import NotificationType  # noqa: PLC0415
    from app.db.unit_of_work import UnitOfWork  # noqa: PLC0415
    from app.services.notification_service import publish_notification  # noqa: PLC0415

    try:
        with UnitOfWork() as uow:
            project = uow.projects.get_by_uuid(UUID(project_id))
        if project is None or project.owner_id is None:
            return
        publish_notification(
            user_id=project.owner_id,
            title="Source Code Document Generation Failed",
            message=(
                "Domain-knowledge/architecture-document generation failed for "
                f'"{project.name}": {error[:300]}'
            ),
            notification_type=NotificationType.ERROR,
            data={"project_id": project_id, "source_id": source_id, "error": error},
        )
    except Exception:
        logger.warning(
            "_record_pipeline_documents_failure: failed to notify project_id=%s",
            project_id,
            exc_info=True,
        )


_PIPELINE_DOCUMENT_STAGE_ACTIVITY: dict[str, dict[str, Any]] = {
    "domain_knowledge": {
        "completed": (
            ActivityType.SOURCE_CODE_DOMAIN_KNOWLEDGE_COMPLETED,
            SUMMARY_ACTIVITY_SOURCE_CODE_DOMAIN_KNOWLEDGE_COMPLETED,
            MSG_ACTIVITY_SOURCE_CODE_DOMAIN_KNOWLEDGE_COMPLETED,
        ),
        "failed": (
            ActivityType.SOURCE_CODE_DOMAIN_KNOWLEDGE_FAILED,
            SUMMARY_ACTIVITY_SOURCE_CODE_DOMAIN_KNOWLEDGE_FAILED,
            MSG_ACTIVITY_SOURCE_CODE_DOMAIN_KNOWLEDGE_FAILED,
        ),
    },
    "architecture_document": {
        "completed": (
            ActivityType.SOURCE_CODE_ARCHITECTURE_DOCUMENT_COMPLETED,
            SUMMARY_ACTIVITY_SOURCE_CODE_ARCHITECTURE_DOCUMENT_COMPLETED,
            MSG_ACTIVITY_SOURCE_CODE_ARCHITECTURE_DOCUMENT_COMPLETED,
        ),
        "failed": (
            ActivityType.SOURCE_CODE_ARCHITECTURE_DOCUMENT_FAILED,
            SUMMARY_ACTIVITY_SOURCE_CODE_ARCHITECTURE_DOCUMENT_FAILED,
            MSG_ACTIVITY_SOURCE_CODE_ARCHITECTURE_DOCUMENT_FAILED,
        ),
    },
}


def _record_stage_activity(
    *,
    project_id: str,
    source_id: str,
    task_db_id: str | None,
    stage: str | None,
    outcome: str,
    error: str | None = None,
) -> None:
    """Log one activity-feed entry for a single document-generation stage.

    ``stage`` is one of ``"domain_knowledge"``/``"architecture_document"`` —
    each is its own independent LLM call/method, so each gets its own
    completed/failed entry rather than one combined row for both. No-ops
    when ``stage`` is unknown (e.g. a failure before either stage started,
    during pipeline construction) — there is no specific artifact to
    attribute it to; the run-level notification/error-recording in
    ``_record_pipeline_documents_failure`` still covers that case. Never
    raises — the "completed" outcome is called inline in the caller's own
    try block, so a bug here must not be mistaken for that stage failing.
    """
    stage_activity = _PIPELINE_DOCUMENT_STAGE_ACTIVITY.get(stage or "")
    if stage_activity is None:
        return
    try:
        activity_type, summary, message_template = stage_activity[outcome]
        message = (
            message_template.format(error=error[:200])
            if outcome == "failed"
            else message_template
        )
        record_activity(
            project_id=UUID(project_id),
            activity_type=activity_type,
            summary=summary,
            message=message,
            actor_user_id=resolve_actor_from_task(task_db_id),
            data={"source_id": source_id, **({"error": error} if error else {})},
        )
    except Exception:
        logger.warning(
            "_record_stage_activity: failed to log activity project_id=%s stage=%s outcome=%s",
            project_id,
            stage,
            outcome,
            exc_info=True,
        )


async def _generate_and_persist_pipeline_documents(
    *,
    project_id: str,
    source_id: str,
    task_db_id: str | None,
    project_dir: str | None,
    config_path: str | None,
    codebase_dir: str | None,
    skip_processing: bool,
) -> None:
    """Generate the domain-knowledge and architecture-document artifacts and persist them.

    Must only be called once every module for this source has finished
    processing: both artifacts read ``_global/module_manifest.json``, which
    only gets its DDD-quality names/descriptions (via ``refine_module_names``)
    as each module's own Stage 3 completes — calling this any earlier would
    persist artifacts derived from the pre-refinement manifest.

    Best-effort end-to-end: a failure never blocks the pipeline-completion
    callback (status marking, cleanup dispatch) that triggers this, and never
    reverts the already-finalized Source/SourceIngestion status. It IS,
    however, recorded via ``_record_pipeline_documents_failure`` — logged,
    attached to the source/ingestion error fields, notified to the project
    owner, and added to the activity log — instead of only being logged.
    """
    if not project_dir or not config_path or not codebase_dir:
        logger.warning(
            "Skipping domain-knowledge/architecture-document generation for "
            "project_id=%s: missing project_dir/config_path/codebase_dir.",
            project_id,
        )
        return

    from app.core.llm_errors import NonRetryableLLMError
    from app.services.source_code_pipeline.pipeline_orchestrator import (
        PipelineContext,
        PipelineOrchestrator,
    )
    from app.services.source_code_pipeline.src.ai.llm_client import (
        CircuitBreakerError,
        CreditBalanceExhaustedError,
        LLMClient,
    )
    from app.workers._task_helpers import describe_non_retryable_llm_error

    # generate_domain_knowledge/generate_architecture_document are the only
    # two LLM-calling pipeline stages not wrapped in LLMClient.pipeline_run_guard
    # (unlike every module/global-artifact stage), so nothing else resets the
    # process-wide circuit breaker before their calls. Without this, this
    # project's document-generation run can inherit a breaker already tripped
    # by an unrelated project's LLM timeouts sharing this same recycled worker
    # child (celery_app.py's --max-tasks-per-child) and fail immediately even
    # though this project's own LLM calls are healthy.
    LLMClient.reset_circuit_breaker()
    from app.workers.document_task_stages import _get_project_llm_options

    runtime_llm_options = _get_project_llm_options(project_id=project_id)

    def _build_pipeline() -> PipelineOrchestrator:
        context = PipelineContext(
            project_dir=project_dir,
            config_path=config_path,
            source_dir=codebase_dir,
            skip_processing=skip_processing,
            llm_api_key=runtime_llm_options.get("llm_api_key"),
        )
        return PipelineOrchestrator(context)

    failures: list[str] = []
    current_stage: str | None = None
    try:
        pipeline = await asyncio.to_thread(_build_pipeline)

        current_stage = "domain_knowledge"
        logger.info("Generating domain knowledge for project_id=%s", project_id)
        domain_result = await asyncio.to_thread(pipeline.generate_domain_knowledge)
        if domain_result.get("status") == "failed":
            error = domain_result.get("error")
            logger.error(
                "Domain knowledge generation failed for project_id=%s: %s",
                project_id,
                error,
            )
            failures.append(f"Domain knowledge generation failed: {error}")
            _record_stage_activity(
                project_id=project_id,
                source_id=source_id,
                task_db_id=task_db_id,
                stage=current_stage,
                outcome="failed",
                error=str(error),
            )
        else:
            await _persist_domain_knowledge_artifact(
                project_id=project_id,
                content=domain_result.get("content", ""),
            )
            _record_stage_activity(
                project_id=project_id,
                source_id=source_id,
                task_db_id=task_db_id,
                stage=current_stage,
                outcome="completed",
            )

        current_stage = "architecture_document"
        logger.info("Generating architecture document for project_id=%s", project_id)
        architecture_result = await asyncio.to_thread(pipeline.generate_architecture_document)
        if architecture_result.get("status") == "failed":
            error = architecture_result.get("error")
            logger.error(
                "Architecture document generation failed for project_id=%s: %s",
                project_id,
                error,
            )
            failures.append(f"Architecture document generation failed: {error}")
            _record_stage_activity(
                project_id=project_id,
                source_id=source_id,
                task_db_id=task_db_id,
                stage=current_stage,
                outcome="failed",
                error=str(error),
            )
        else:
            await _persist_architecture_document_artifact(
                project_id=project_id,
                content=architecture_result.get("content", ""),
            )
            _record_stage_activity(
                project_id=project_id,
                source_id=source_id,
                task_db_id=task_db_id,
                stage=current_stage,
                outcome="completed",
            )
    except CircuitBreakerError as exc:
        # BaseException by design (see llm_client.py's class docstring), so
        # the `except Exception` below would not have caught it — left
        # unhandled it escapes this best-effort task entirely uncaught,
        # which under a real -P prefork worker crashes the child process
        # outright rather than recording a normal failure (see
        # docs/CircuitBreakerError_Handling_Issue_Implications.docx).
        # Swallowed here too, consistent with this function's own
        # best-effort contract.
        logger.error(
            "Circuit breaker tripped generating domain-knowledge/architecture-document "
            "artifacts for project_id=%s: %s",
            project_id,
            exc,
        )
        failures.append(f"Circuit breaker tripped: {exc}")
        _record_stage_activity(
            project_id=project_id,
            source_id=source_id,
            task_db_id=task_db_id,
            stage=current_stage,
            outcome="failed",
            error=str(exc),
        )
    except CreditBalanceExhaustedError as exc:
        # Same BaseException-passthrough reasoning as CircuitBreakerError
        # above. Unlike the module-processing path in
        # _process_single_module_task/_parse_code_task, this stage runs
        # AFTER the module pipeline has already succeeded — swallowed here
        # too, consistent with this function's own best-effort contract
        # (see _record_pipeline_documents_failure's docstring for why this
        # does not revert Source/SourceIngestion status).
        logger.error(
            "Credit balance/quota exhausted generating domain-knowledge/"
            "architecture-document artifacts for project_id=%s: %s",
            project_id,
            exc,
        )
        failures.append(f"Credit balance/quota exhausted: {exc}")
        _record_stage_activity(
            project_id=project_id,
            source_id=source_id,
            task_db_id=task_db_id,
            stage=current_stage,
            outcome="failed",
            error=str(exc),
        )
    except NonRetryableLLMError as exc:
        # Same BaseException-passthrough reasoning as CircuitBreakerError/
        # CreditBalanceExhaustedError above, for every OTHER non-retryable
        # reason (authentication, invalid model, invalid request,
        # context-length-exceeded, content policy, permission denied). Left
        # unhandled, this would silently break this function's own
        # long-established best-effort contract — module generation stops
        # the WHOLE run on these reasons (see _process_single_module_task),
        # but this stage already runs AFTER that module pipeline has
        # succeeded, so it stays best-effort: swallowed here too, never
        # reverting Source/SourceIngestion status.
        error_detail = describe_non_retryable_llm_error(exc.classification)
        logger.error(
            "Non-retryable LLM error generating domain-knowledge/"
            "architecture-document artifacts for project_id=%s: %s",
            project_id,
            error_detail,
        )
        failures.append(f"Non-retryable LLM error: {error_detail}")
        _record_stage_activity(
            project_id=project_id,
            source_id=source_id,
            task_db_id=task_db_id,
            stage=current_stage,
            outcome="failed",
            error=error_detail,
        )
    except Exception as exc:
        logger.exception(
            "Unexpected error generating domain-knowledge/architecture-document "
            "artifacts for project_id=%s",
            project_id,
        )
        failures.append(f"Unexpected error: {exc}")
        _record_stage_activity(
            project_id=project_id,
            source_id=source_id,
            task_db_id=task_db_id,
            stage=current_stage,
            outcome="failed",
            error=str(exc),
        )

    if failures:
        _record_pipeline_documents_failure(
            project_id=project_id,
            source_id=source_id,
            error="; ".join(failures),
        )


@celery_app.task(
    bind=True,
    name="tasks.parse_code.generate_pipeline_documents",
    acks_late=True,
    soft_time_limit=TASK_AI_SOFT_TIME_LIMIT,
    time_limit=TASK_AI_TIME_LIMIT,
    ignore_result=True,
)
def _generate_pipeline_documents_task(
    self,
    project_id: str,
    source_id: str,
    project_dir: str | None,
    config_path: str | None,
    codebase_dir: str | None,
    skip_processing: bool,
    task_db_id: str | None = None,
) -> None:
    """Generate domain-knowledge/architecture-document artifacts for a finished pipeline.

    Dispatched from _persist_single_module_task's completion callback rather
    than called inline: these are LLM calls, so they need the much larger
    AI-generation time budget (TASK_AI_SOFT_TIME_LIMIT/TASK_AI_TIME_LIMIT)
    instead of sharing _persist_single_module_task's tight persistence-only
    budget (TASK_PERSIST_SINGLE_MODULE_TIME_LIMIT). Running them inline used
    to let a slow LLM call push the last module's persist task past its hard
    time limit, which killed the worker process outright and surfaced as an
    uncaught TimeLimitExceeded in the blocking orchestrator .get() — aborting
    the whole pipeline even though the backlog had already been finalized.
    """
    _run_async(
        _generate_and_persist_pipeline_documents(
            project_id=project_id,
            source_id=source_id,
            task_db_id=task_db_id,
            project_dir=project_dir,
            config_path=config_path,
            codebase_dir=codebase_dir,
            skip_processing=skip_processing,
        )
    )


async def _persist_group_specs_to_neo4j(
    *,
    project_id: str,
    rows: list[dict[str, Any]] | None = None,
) -> int:
    from app.services.group_spec_service import GroupSpecService

    if not rows:
        return 0

    return await GroupSpecService().upsert_many_for_project(
        project_id=project_id,
        rows=rows,
    )


async def _persist_config_specs_to_neo4j(
    *,
    project_id: str,
    rows: list[dict[str, Any]] | None = None,
) -> int:
    from app.services.config_spec_service import ConfigSpecService

    if not rows:
        return 0

    return await ConfigSpecService().upsert_many_for_project(
        project_id=project_id,
        rows=rows,
    )


async def _persist_srs_evidence_to_neo4j(
    *,
    project_id: str,
    srs_evidence_rows: list[dict[str, Any]] | None = None,
) -> int:
    from app.services.srs_evidence_service import SRSEvidenceService

    if not srs_evidence_rows:
        return 0

    return await SRSEvidenceService().upsert_many_for_project(
        project_id=project_id,
        rows=srs_evidence_rows,
    )


async def _fetch_module_manifest_for_metadata(
    *,
    project_dir: str | None,
    config_path: str | None,
    codebase_dir: str | None,
    skip_processing: bool,
) -> dict[str, Any]:
    """Load module_manifest.json via PipelineOrchestrator.get_module_manifest().

    Best-effort: returns {} on any failure so a manifest-load problem never
    discards the pipeline results (group specs / features / stories) already
    persisted earlier in _persist_pipeline_results.
    """
    if not project_dir or not config_path or not codebase_dir:
        return {}

    def _load() -> dict[str, Any]:
        from app.services.source_code_pipeline.pipeline_orchestrator import (
            PipelineContext,
            PipelineOrchestrator,
        )

        context = PipelineContext(
            project_dir=project_dir,
            config_path=config_path,
            source_dir=codebase_dir,
            skip_processing=skip_processing,
        )
        pipeline = PipelineOrchestrator(context)
        return pipeline.get_module_manifest().get("module_manifest", {})

    try:
        return await asyncio.to_thread(_load)
    except Exception:
        logger.warning(
            "Failed to load module_manifest via get_module_manifest() for project_dir=%s",
            project_dir,
            exc_info=True,
        )
        return {}


def _collect_source_code_metadata_rows(
    *,
    project_id: str,
    source_id: str,
    module_results: list[dict[str, Any]],
    module_manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    now = _utc_now_iso()

    for module_result in module_results:
        module_id = str(module_result.get("module_id") or "")
        if not module_id:
            continue

        deterministic_id = str(
            uuid5(NAMESPACE_URL, f"{project_id}:{source_id}:{module_id}:source_code_metadata")
        )
        metadata_row = {
            "id": deterministic_id,
            "project_id": project_id,
            "source_id": source_id,
            "module_id": module_id,
            "module_response": module_result,
            "module_manifest": module_manifest,
            "created_at": now,
            "updated_at": now,
        }
        rows.append(SourceCodeMetadataSchema.model_validate(metadata_row).model_dump(mode="json"))

    return rows


async def _persist_source_code_metadata_to_neo4j(
    *,
    project_id: str,
    rows: list[dict[str, Any]] | None = None,
) -> int:
    from app.services.source_code_metadata_service import SourceCodeMetadataService

    if not rows:
        return 0

    return await SourceCodeMetadataService().upsert_many_for_project(
        project_id=project_id,
        rows=rows,
    )


async def _persist_pipeline_results(
    *,
    project_id: str,
    source_id: str,
    module_results: list[dict[str, Any]],
    task_db_id: str | None = None,
    project_dir: str | None = None,
    config_path: str | None = None,
    codebase_dir: str | None = None,
    skip_processing: bool = False,
) -> dict[str, Any]:
    from app.services.group_spec_service import GroupSpecService
    from app.services.module_feature_service import ModuleFeatureService
    from app.services.user_story_service import UserStoryService

    group_spec_rows = _collect_group_spec_rows(
        project_id=project_id,
        source_id=source_id,
        module_results=module_results,
    )

    group_spec_id_by_filename: dict[str, str] = {}
    storage_key_by_filename: dict[str, str] = {}
    for row in group_spec_rows:
        filename = str(row.get("filename") or "")
        row_id = str(row.get("id") or "")
        if filename and row_id:
            group_spec_id_by_filename[filename] = row_id

        if filename:
            storage_key_by_filename[filename] = GroupSpecService._build_storage_key(
                project_id=project_id,
                module_id=row.get("mod_code"),
                feature_unit_id=row.get("fea_code"),
                file_name=filename,
                row_id=row_id,
            )

    logger.info(
        "Collected GroupSpec rows for project_id=%s count=%d",
        project_id,
        len(group_spec_rows),
    )

    group_spec_count = await _persist_group_specs_to_neo4j(
        project_id=project_id,
        rows=group_spec_rows,
    )
    emit_task_event(
        task_db_id=task_db_id,
        project_id=project_id,
        task_type="source_process",
        status=SOURCE_STATUS_RUNNING,
        stage="source_code.group_specs.persisted",
        progress=88,
        meta={"source_id": source_id, "group_specs_stored": group_spec_count},
    )
    logger.info(
        "Stored GroupSpec rows in Neo4j for project_id=%s count=%d",
        project_id,
        group_spec_count,
    )

    config_spec_rows = _collect_config_spec_rows(
        project_id=project_id,
        source_id=source_id,
        module_results=module_results,
    )

    config_spec_count = await _persist_config_specs_to_neo4j(
        project_id=project_id,
        rows=config_spec_rows,
    )
    emit_task_event(
        task_db_id=task_db_id,
        project_id=project_id,
        task_type="source_process",
        status=SOURCE_STATUS_RUNNING,
        stage="source_code.config_specs.persisted",
        progress=89,
        meta={"source_id": source_id, "config_specs_stored": config_spec_count},
    )
    logger.info(
        "Stored ConfigSpec rows in Neo4j for project_id=%s count=%d",
        project_id,
        config_spec_count,
    )

    source_ingestion_id = _resolve_source_ingestion_id(source_ids=[source_id])

    module_feature_skeleton = _build_module_feature_skeleton_from_results(module_results)
    stored_modules = []
    module_id_by_code: dict[str, str] = {}
    feature_id_by_code: dict[str, str] = {}

    if module_feature_skeleton.get("feature_inventory"):
        stored_modules = await ModuleFeatureService().upsert_modules_and_features_for_source_code(
            project_id=UUID(project_id),
            module_feature_skeleton=module_feature_skeleton,
            source_ingestion_id=source_ingestion_id,
        )

        module_id_by_code, feature_id_by_code = _build_module_feature_id_maps(stored_modules)

        logger.info(
            "Stored module-feature skeleton in Neo4j for project_id=%s modules=%d",
            project_id,
            len(stored_modules),
        )
        emit_task_event(
            task_db_id=task_db_id,
            project_id=project_id,
            task_type="source_process",
            status=SOURCE_STATUS_RUNNING,
            stage="source_code.module_features.persisted",
            progress=92,
            meta={"source_id": source_id, "modules_stored": len(stored_modules)},
        )

    backlog_result = _build_backlog_result_from_results(
        project_id=project_id,
        source_id=source_id,
        module_results=module_results,
        group_spec_id_by_filename=group_spec_id_by_filename,
        storage_key_by_filename=storage_key_by_filename,
        module_id_by_code=module_id_by_code,
        feature_id_by_code=feature_id_by_code,
    )
    backlog_output = backlog_result.get("output", {})
    if backlog_output.get("epics"):
        upserted_stories = await UserStoryService().upsert_user_stories_for_source_code(
            project_id=UUID(project_id),
            backlog_result=backlog_result,
            source_ingestion_id=source_ingestion_id,
        )
        logger.info(
            "Stored backlog user stories in Neo4j for project_id=%s stories=%d",
            project_id,
            upserted_stories,
        )
        emit_task_event(
            task_db_id=task_db_id,
            project_id=project_id,
            task_type="source_process",
            status=SOURCE_STATUS_RUNNING,
            stage="source_code.user_stories.persisted",
            progress=95,
            meta={"source_id": source_id, "stories_stored": upserted_stories},
        )

    epics = backlog_output.get("epics", [])
    srs_evidence_rows: list[dict[str, Any]] = []
    if isinstance(epics, list):
        for epic in epics:
            if isinstance(epic, dict):
                epic_rows = epic.get("srs_evidence", [])
                if isinstance(epic_rows, list):
                    srs_evidence_rows.extend(epic_rows)

    srs_evidence_count = await _persist_srs_evidence_to_neo4j(
        project_id=project_id,
        srs_evidence_rows=srs_evidence_rows,
    )
    emit_task_event(
        task_db_id=task_db_id,
        project_id=project_id,
        task_type="source_process",
        status=SOURCE_STATUS_RUNNING,
        stage="source_code.srs_evidence.persisted",
        progress=97,
        meta={"source_id": source_id, "srs_evidence_stored": srs_evidence_count},
    )
    logger.info(
        "Stored SRSEvidence rows in Neo4j for project_id=%s count=%d",
        project_id,
        srs_evidence_count,
    )

    # ── Last step: persist per-module SourceCodeMetadata (module response + manifest) ──
    module_manifest = await _fetch_module_manifest_for_metadata(
        project_dir=project_dir,
        config_path=config_path,
        codebase_dir=codebase_dir,
        skip_processing=skip_processing,
    )
    source_code_metadata_rows = _collect_source_code_metadata_rows(
        project_id=project_id,
        source_id=source_id,
        module_results=module_results,
        module_manifest=module_manifest,
    )
    source_code_metadata_count = await _persist_source_code_metadata_to_neo4j(
        project_id=project_id,
        rows=source_code_metadata_rows,
    )
    emit_task_event(
        task_db_id=task_db_id,
        project_id=project_id,
        task_type="source_process",
        status=SOURCE_STATUS_RUNNING,
        stage="source_code.metadata.persisted",
        progress=98,
        meta={"source_id": source_id, "source_code_metadata_stored": source_code_metadata_count},
    )
    logger.info(
        "Stored SourceCodeMetadata rows in Neo4j for project_id=%s count=%d",
        project_id,
        source_code_metadata_count,
    )

    return {
        "modules_processed": len(module_results),
        "group_specs_stored": group_spec_count,
        "config_specs_stored": config_spec_count,
        "backlog_stories_stored": len(backlog_output.get("epics", [])),
        "srs_evidence_stored": srs_evidence_count,
        "source_code_metadata_stored": source_code_metadata_count,
    }


_ZERO_BACKLOG_COUNTS = {"tot_modules": 0, "tot_features": 0, "tot_user_stories": 0}


def _delete_source_code_backlog(project_id: str | None) -> None:
    """Wipe every Module/Feature/UserStory for *project_id* on cancellation.

    Cancelling a source-code run rolls back the *entire* project's backlog,
    not just what this run produced — reuses the same dependency-order-safe
    delete pattern as whole-project deletion
    (``ProjectRepository.delete_project_graph_sync`` /
    ``app/workers/project_tasks.py::delete_project_graph``), scoped to just
    the backlog (leaves the :Project node, :Source/:Fragment, and
    :GroupSpec/:ConfigSpec/:SourceCodeMetadata untouched).

    Plain sync call — ``ProjectRepository`` uses the sync Neo4j driver, so
    this is safe to call directly from a sync Celery task body. Logged at
    ERROR (not swallowed quietly like other best-effort bookkeeping in this
    file) since this is core to the requested cancel behavior, but never
    raises — a Neo4j failure must not prevent the run from being marked
    cancelled.

    Not called from the cancel request itself: it is the slowest step of a
    rollback, so ``_finalize_source_code_cancellation`` dispatches
    ``_delete_project_backlog_task`` instead of calling this inline. Go
    through that task rather than calling this directly from any new
    request-path code — it also carries the guard against wiping a newer
    run's backlog.
    """
    if not project_id:
        return
    try:
        from app.db.neo4j import get_neo4j_driver
        from app.repositories.neo4j.project_repository import (
            ProjectRepository as Neo4jProjectRepository,
        )

        counts = Neo4jProjectRepository(get_neo4j_driver()).delete_backlog_for_project(project_id)
        logger.info("Deleted backlog for cancelled project_id=%s: %s", project_id, counts)
    except Exception:
        logger.error(
            "Failed to delete backlog for cancelled project_id=%s",
            project_id,
            exc_info=True,
        )


def _finalize_source_code_cancellation(
    *,
    request_id: str | None,
    task_db_id: str | None,
    project_id: str | None,
    source_ids: list[str] | None = None,
    stage: str = "source_code.cancelled",
) -> None:
    """Unconditionally finalize a *confirmed* cancellation, rolling the run back.

    This is the single canonical "stop and roll back a source-code run"
    routine, used from two places:

    - ``ProjectTaskService.cancel_request`` — the normal path. Cancellation is
      applied synchronously the moment the user asks for it, so the run never
      sits in an intermediate state waiting for a worker to notice.
    - the worker's own checkpoints — a backstop for a task that was already
      past the point of being stopped from outside.

    Never re-checks the flag and never raises: a transient Redis/DB/Neo4j
    error here must not silently skip finalization (the bug this replaced — a
    redundant re-check that fails open on a Redis blip and quietly no-ops)
    nor escape uncaught from a checkpoint that runs before its task's own
    try/except block. Safe to call more than once for the same run.

    Order matters, and it is the reverse of what it used to be: the
    "cancelled" status is emitted *first*, and the backlog delete is then
    **dispatched** to :func:`_delete_project_backlog_task` rather than run
    inline. Deleting first meant the caller's ``DELETE`` request waited on a
    whole-project Neo4j delete before anything read ``cancelled`` — the bulk
    of the cancel latency users were seeing.

    The trade this makes deliberately: there is now a brief window where a
    client can observe ``cancelled`` while the backlog is still present. That
    is acceptable because ``cancelled`` is sticky and the rows are about to
    disappear; a cancel that appears to hang is not.
    """
    source_ids = [sid for sid in (source_ids or []) if sid]
    try:
        emit_task_event(
            task_db_id=task_db_id,
            project_id=project_id,
            task_type="source_process",
            status=TASK_STATUS_CANCELLED,
            stage=stage,
            progress=100,
        )

        if source_ids:
            mark_sources_and_ingestion_cancelled(
                source_ids=source_ids,
                project_id=project_id,
                task_type="source_process",
                stage=stage,
                task_db_id=task_db_id,
                extra_ingestion_fields=_ZERO_BACKLOG_COUNTS,
            )
            for source_id in source_ids:
                try:
                    from app.core.celery_app import celery_app as _celery_app  # noqa: PLC0415

                    _celery_app.backend.client.delete(f"module_done_count:{source_id}")
                except Exception:
                    logger.warning(
                        "Failed to clear module_done_count for source_id=%s after cancellation",
                        source_id,
                        exc_info=True,
                    )
        if project_id:
            # Backlog delete goes out with no countdown (the sooner the stale
            # backlog is gone the better, and the task re-checks for a newer
            # run before deleting); the temp-folder cleanup keeps its 30 s
            # delay so any still-running module task finishes its I/O first.
            # The timestamp is what lets each task tell a genuinely newer run
            # apart from an unrelated task that was already in flight — same
            # instant passed to both so they agree on the cutoff.
            cancelled_at = _utc_now_iso()
            _delete_project_backlog_task.apply_async(args=[project_id, cancelled_at])
            _cleanup_project_folder_task.apply_async(args=[project_id, cancelled_at], countdown=30)
    except Exception:
        logger.error(
            "Failed to finalize cancellation for request_id=%s project_id=%s source_ids=%s "
            "— the run is still being stopped, but some cleanup/bookkeeping may be incomplete",
            request_id,
            project_id,
            source_ids,
            exc_info=True,
        )


def _handle_source_code_cancellation(
    *,
    request_id: str | None,
    task_db_id: str | None,
    project_id: str | None,
    source_id: str | None = None,
    stage: str = "source_code.cancelled",
) -> bool:
    """Return True if *request_id* (or *task_db_id* as a fallback) is cancelled.

    On a hit, calls :func:`_finalize_source_code_cancellation`. Use this at a
    checkpoint that doesn't yet know whether the run was cancelled; if a
    lower layer already reported cancellation authoritatively (e.g.
    ``process_single_module`` returning ``status="cancelled"``), call
    :func:`_finalize_source_code_cancellation` directly instead — re-checking
    the flag here is redundant and, since ``is_request_cancelled`` fails
    open on a transient Redis error, could wrongly skip finalization for a
    cancellation that's already confirmed.

    Never raises itself: callers that are a Celery chain link (e.g.
    ``_process_single_module_task``) must raise ``TaskCancelledError`` on a
    True return to actually stop ``chain()`` from dispatching its next link;
    callers before any chain is dispatched (e.g. ``_run_pipeline_orchestrator``)
    can just return early instead.

    *request_id* falls back to *task_db_id* — a plain single-source upload
    never sets an explicit request_id, and ``ProjectTask.request_id``
    defaults to the task's own id in that case (see
    ``project_task_repository.py``), so that's the key the cancel flag is
    actually stored under.
    """
    from app.core import task_control  # noqa: PLC0415

    request_id = request_id or task_db_id
    if not request_id or not task_control.is_request_cancelled(request_id):
        return False

    _finalize_source_code_cancellation(
        request_id=request_id,
        task_db_id=task_db_id,
        project_id=project_id,
        source_ids=[source_id] if source_id else None,
        stage=stage,
    )
    return True


_MODULE_ACTIVITY: dict[str, tuple[ActivityType, str, str]] = {
    "started": (
        ActivityType.SOURCE_CODE_MODULE_STARTED,
        SUMMARY_ACTIVITY_SOURCE_CODE_MODULE_STARTED,
        MSG_ACTIVITY_SOURCE_CODE_MODULE_STARTED,
    ),
    "completed": (
        ActivityType.SOURCE_CODE_MODULE_COMPLETED,
        SUMMARY_ACTIVITY_SOURCE_CODE_MODULE_COMPLETED,
        MSG_ACTIVITY_SOURCE_CODE_MODULE_COMPLETED,
    ),
    "failed": (
        ActivityType.SOURCE_CODE_MODULE_FAILED,
        SUMMARY_ACTIVITY_SOURCE_CODE_MODULE_FAILED,
        MSG_ACTIVITY_SOURCE_CODE_MODULE_FAILED,
    ),
    "cancelled": (
        ActivityType.SOURCE_CODE_MODULE_CANCELLED,
        SUMMARY_ACTIVITY_SOURCE_CODE_MODULE_CANCELLED,
        MSG_ACTIVITY_SOURCE_CODE_MODULE_CANCELLED,
    ),
}


def _notify_module_failed(
    *,
    project_id: str | None,
    module_id: str | None,
    module_name: str | None,
    error: str | None = None,
) -> None:
    """Best-effort: notify the project owner that one source-code module failed.

    Fires once per failed module, independent of the once-per-run
    ``_notify_source_code_pipeline_status`` notification fired when every
    module has finished — so the owner learns about a failure as soon as it
    happens rather than only at the end of a (possibly long-running)
    pipeline. No-ops when *project_id* is missing. Never raises — a
    notification failure must not abort module/pipeline processing.
    """
    if not project_id:
        return

    from app.core.enums.notification_type import NotificationType  # noqa: PLC0415
    from app.db.unit_of_work import UnitOfWork  # noqa: PLC0415
    from app.services.notification_service import publish_notification  # noqa: PLC0415

    try:
        with UnitOfWork() as uow:
            project = uow.projects.get_by_uuid(UUID(project_id))
        if project is None or project.owner_id is None:
            return

        display_name = module_name or module_id or "unknown"
        publish_notification(
            user_id=project.owner_id,
            title=SUMMARY_ACTIVITY_SOURCE_CODE_MODULE_FAILED,
            message=MSG_ACTIVITY_SOURCE_CODE_MODULE_FAILED.format(
                module_name=display_name, error=(error or "")[:200]
            ),
            notification_type=NotificationType.ERROR,
            data={
                "project_id": project_id,
                "module_id": module_id,
                "module_name": module_name,
                "error": error,
            },
        )
    except Exception:
        logger.warning(
            "_notify_module_failed: failed to notify project_id=%s module_id=%s",
            project_id,
            module_id,
            exc_info=True,
        )


def _record_module_activity(
    *,
    project_id: str | None,
    source_id: str | None,
    task_db_id: str | None,
    module_id: str | None,
    module_name: str | None,
    outcome: str,
    total_features: int | None = None,
    total_user_stories: int | None = None,
    error: str | None = None,
) -> None:
    """Best-effort: log one per-module activity-feed entry.

    Fires once at module start (``"started"``) and again once the module
    reaches a terminal outcome (``"completed"``/``"failed"``/``"cancelled"``)
    — activity-log rows are append-only (never updated in place), so a
    module's status over time is reconstructed from these separate rows
    rather than one row being mutated. A ``"failed"`` outcome also notifies
    the project owner (``_notify_module_failed``) — centralized here so
    every failure call site gets the notification without having to
    remember to fire it separately. No-ops when *project_id* is missing
    (defensive only — every real dispatch site always provides it; the
    parameter is optional on the caller tasks purely for direct/test calls).
    Never raises.
    """
    if not project_id:
        return
    if outcome == "failed":
        _notify_module_failed(
            project_id=project_id,
            module_id=module_id,
            module_name=module_name,
            error=error,
        )
    try:
        activity_type, summary, message_template = _MODULE_ACTIVITY[outcome]
        display_name = module_name or module_id or "unknown"
        if outcome == "completed":
            message = message_template.format(
                module_name=display_name,
                total_features=total_features or 0,
                total_user_stories=total_user_stories or 0,
            )
        elif outcome == "failed":
            message = message_template.format(module_name=display_name, error=(error or "")[:200])
        else:
            message = message_template.format(module_name=display_name)
        record_activity(
            project_id=UUID(project_id),
            activity_type=activity_type,
            summary=summary,
            message=message,
            actor_user_id=resolve_actor_from_task(task_db_id),
            data={
                "source_id": source_id,
                "module_id": module_id,
                "module_name": module_name,
                "status": outcome,
                **(
                    {"total_features": total_features, "total_user_stories": total_user_stories}
                    if outcome == "completed"
                    else {}
                ),
                **({"error": error} if error else {}),
            },
        )
    except Exception:
        logger.warning(
            "_record_module_activity: failed to log activity project_id=%s module_id=%s "
            "outcome=%s",
            project_id,
            module_id,
            outcome,
            exc_info=True,
        )


# ── Celery tasks ─────────────────────────────────────────────────────────────


@celery_app.task(
    bind=True,
    name="tasks.parse_code.process_single_module",
    acks_late=True,
    soft_time_limit=TASK_SOURCE_CODE_MODULE_SOFT_TIME_LIMIT,
    time_limit=TASK_SOURCE_CODE_MODULE_TIME_LIMIT,
    max_retries=SOURCE_CODE_TASK_MAX_RETRIES,
    default_retry_delay=SOURCE_CODE_TASK_RETRY_COUNTDOWN_SECONDS,
    ignore_result=False,
)
def _process_single_module_task(
    self,
    project_dir: str,
    config_path: str,
    codebase_dir: str,
    module_data: dict[str, Any],
    artifacts_enriched: dict[str, Any],
    module_manifest: dict[str, Any],
    skip_processing: bool = False,
    project_id: str | None = None,
    request_id: str | None = None,
    source_id: str | None = None,
    task_db_id: str | None = None,
) -> dict[str, Any]:
    """
    Process one module through the LLM pipeline.

    May run for 10 minutes to 6 hours depending on module size.

    Retries once (see CELERY_RETRY_POLICY.docx Section A) on transient
    infrastructure: SoftTimeLimitExceeded/MemoryError, ConcurrentPipelineError
    (another project's run held the process-wide single-flight lock), and
    Redis/broker/network/S3 connectivity blips. Everything else — business,
    programming, config, auth, or an already-exhausted LLM failure — fails
    immediately.
    """
    from app.core import task_control
    from app.core.llm_errors import NonRetryableLLMError
    from app.workers._task_helpers import (
        CircuitBreakerTaskFailure,
        CreditBalanceExhaustedTaskFailure,
        NonRetryableLLMTaskFailure,
        TaskCancelledError,
        describe_non_retryable_llm_error,
    )

    # Normalized once so every use below (this entry check, PipelineContext,
    # and thus every check further down the pipeline: process_single_module's
    # step boundaries, run_for_module/run_for_mfu's per-MFU checks) sees the
    # same id the cancel flag is actually stored under (see
    # _handle_source_code_cancellation's docstring re: the task_db_id fallback).
    request_id = request_id or task_db_id
    module_id = module_data.get("module_id") if isinstance(module_data, dict) else None
    module_name = module_data.get("module_name") if isinstance(module_data, dict) else None

    # Checked before bind_log_context/try — this task's own except Exception
    # is broad and would otherwise swallow TaskCancelledError, which must
    # propagate to stop the outer chain() from dispatching the next module.
    # A mid-run cancel is only ever observed here (the root task has already
    # returned once module chains are dispatched), so this checkpoint owns
    # finalizing Source/SourceIngestion/ProjectTask to "cancelled" and
    # cleaning up — nothing downstream will do it once the chain is aborted.
    if _handle_source_code_cancellation(
        request_id=request_id,
        task_db_id=task_db_id,
        project_id=project_id,
        source_id=source_id,
    ):
        _record_module_activity(
            project_id=project_id,
            source_id=source_id,
            task_db_id=task_db_id,
            module_id=module_id,
            module_name=module_name,
            outcome="cancelled",
        )
        raise TaskCancelledError(f"request_id={request_id} cancelled")

    # Poison-loop guard: bounds total deliveries of this exact task message to
    # max_retries+1, independent of self.request.retries — a worker killed by
    # OOM/spot reclaim never reaches the except blocks below to call
    # self.retry(), so task_reject_on_worker_lost's broker-level redelivery
    # would otherwise let an always-OOM module loop forever.
    delivery_key = self.request.id or f"{project_id}:{module_id}"
    if not task_control.register_delivery_within_limit(
        delivery_key, max_deliveries=SOURCE_CODE_TASK_MAX_RETRIES + 1
    ):
        logger.error(
            "process_single_module_task: exceeded max deliveries for "
            "project_id=%s module_id=%s module_name=%s — dead-lettering "
            "without further processing (poison-loop guard).",
            project_id,
            module_id,
            module_name,
        )
        return {
            "module_id": module_id,
            "error": "Exceeded maximum redelivery attempts (poison-loop guard)",
            "status": "failed",
        }

    from app.services.source_code_pipeline.pipeline_orchestrator import (
        PipelineContext,
        PipelineOrchestrator,
    )
    from app.services.source_code_pipeline.src.ai.llm_client import (
        CircuitBreakerError,
        ConcurrentPipelineError,
        CreditBalanceExhaustedError,
    )
    from app.utils.log_context import bind_log_context

    def _retry_or_fail(
        exc: BaseException,
        error_message: str | None = None,
        countdown: int = SOURCE_CODE_TASK_RETRY_COUNTDOWN_SECONDS,
    ) -> dict[str, Any]:
        """Retry once (bounded by self.request.retries/self.max_retries), else fail."""
        if self.request.retries >= self.max_retries:
            return {
                "module_id": module_id,
                "error": error_message or str(exc),
                "status": "failed",
            }
        raise self.retry(exc=exc, countdown=countdown)

    with bind_log_context(project_id=project_id, module_id=module_id):
        _record_module_activity(
            project_id=project_id,
            source_id=source_id,
            task_db_id=task_db_id,
            module_id=module_id,
            module_name=module_name,
            outcome="started",
        )
        try:
            from app.workers.document_task_stages import _get_project_llm_options

            runtime_llm_options = _get_project_llm_options(project_id=project_id)
            context = PipelineContext(
                project_dir=project_dir,
                config_path=config_path,
                source_dir=codebase_dir,
                skip_processing=skip_processing,  # individual modules will be processed in parallel chains
                request_id=request_id,
                llm_api_key=runtime_llm_options.get("llm_api_key"),
            )
            pipeline = PipelineOrchestrator(context)
            result = pipeline.process_single_module(
                module_data=module_data,
                artifacts_enriched=artifacts_enriched,
                module_manifest=module_manifest,
            )
            if isinstance(result, dict) and result.get("status") == "cancelled":
                # process_single_module's own step-boundary checks caught this
                # mid-module — it can only report state, not stop chain()
                # itself, so this is the one place that turns "cancelled"
                # into an actual chain-stopping raise. Cancellation is
                # already confirmed by the pipeline layer above, so finalize
                # directly — re-checking the flag here would be redundant
                # and, on a transient Redis error, could wrongly skip it.
                _finalize_source_code_cancellation(
                    request_id=request_id,
                    task_db_id=task_db_id,
                    project_id=project_id,
                    source_ids=[source_id] if source_id else None,
                )
                _record_module_activity(
                    project_id=project_id,
                    source_id=source_id,
                    task_db_id=task_db_id,
                    module_id=module_id,
                    module_name=module_name,
                    outcome="cancelled",
                )
                raise TaskCancelledError(f"request_id={request_id} cancelled")
            logger.info(
                "Single module processed: project_id=%s module_id=%s module_name=%s result_keys=%s",
                project_id,
                module_id,
                module_name,
                sorted(result.keys()) if isinstance(result, dict) else [],
            )

            # START: Testing and debugging: dump the single-module pipeline result to a JSON file for inspection
            from app.utils.common import dump_json_debug  # noqa: PLC0415

            dump_json_debug(
                f"module_result_{project_id}_{module_id}.json",
                result,
                base_dir="temp/source_codes/module_results",
            )
            # END: Testing and debugging: dump the single-module pipeline result to a JSON file for inspection

            return result if isinstance(result, dict) else {}

        except ConcurrentPipelineError as exc:
            # A second project tried to run in this process — retry once
            # (Celery re-queues, hopefully onto an idle worker) rather than
            # recording a false "failed module" on the first collision. The
            # lock this collided on can stay held for 10 min-6h (another
            # project's pipeline run), so this uses a much longer countdown
            # than the generic infra-blip retry — see
            # SOURCE_CODE_CONCURRENT_PIPELINE_RETRY_COUNTDOWN_SECONDS.
            logger.error(
                "ConcurrentPipelineError for project_id=%s module_id=%s module_name=%s: %s",
                project_id,
                module_id,
                module_name,
                exc,
            )
            return _retry_or_fail(
                exc, countdown=SOURCE_CODE_CONCURRENT_PIPELINE_RETRY_COUNTDOWN_SECONDS
            )

        except TaskCancelledError:
            # Must propagate unmangled — this task's own except Exception
            # below is broad and would otherwise swallow it, defeating the
            # only mechanism that stops chain() from dispatching the next
            # module.
            raise

        except CircuitBreakerError as exc:
            # CircuitBreakerError is deliberately a BaseException (see
            # llm_client.py's class docstring) so it can't be swallowed by
            # `except Exception` below into a false "failed module,
            # continue" outcome — the whole point of the breaker is to stop
            # the rest of THIS chain too, not just this one module. But that
            # same BaseException-ness means it must not be allowed to escape
            # this task unhandled either: verified empirically, Celery's
            # trace_task only recognizes Exception, so under the real
            # -P prefork pool an unhandled BaseException kills the worker
            # child outright (confirmed: exitcode 0, "Worker exited
            # prematurely"), leaving the task stuck at PENDING forever
            # instead of FAILURE — and with acks_late=True +
            # task_reject_on_worker_lost, the broker then redelivers the
            # same message, re-tripping the breaker in a crash loop. See
            # docs/CircuitBreakerError_Handling_Issue_Implications.docx.
            #
            # Re-raising CircuitBreakerTaskFailure (a plain Exception) gets
            # both properties at once: raising, not returning, still stops
            # chain() from dispatching the next module — same mechanism as
            # TaskCancelledError above — while being a type Celery's own
            # tracing correctly records as FAILURE. _parse_code_task's
            # blocking chain .get() then re-raises it there, where it's
            # caught (alongside a direct CircuitBreakerError) and finalized
            # as a failed source, never retried — the breaker only trips
            # after the LLM client's own bounded per-call retry ladder
            # already exhausted itself, so a retry would just redo the same
            # work to hit the same wall again.
            logger.error(
                "Circuit breaker tripped while processing project_id=%s module_id=%s "
                "module_name=%s — failing this module immediately, no retry: %s",
                project_id,
                module_id,
                module_name,
                exc,
            )
            _record_module_activity(
                project_id=project_id,
                source_id=source_id,
                task_db_id=task_db_id,
                module_id=module_id,
                module_name=module_name,
                outcome="failed",
                error=str(exc),
            )
            raise CircuitBreakerTaskFailure(str(exc)) from exc

        except CreditBalanceExhaustedError as exc:
            # Same BaseException-passthrough/re-raise pattern as
            # CircuitBreakerError just above, for the same two reasons: (1)
            # a broad `except Exception` below must not swallow this into a
            # false "failed module, continue" outcome — every other module
            # still queued behind this one would hit the identical
            # account-wide billing/credit wall, so the whole run must stop
            # here, immediately, with no retry; (2) a BaseException left to
            # escape this Celery task unhandled crashes the worker child
            # under the real -P prefork pool instead of being recorded as a
            # normal FAILURE. Re-raising CreditBalanceExhaustedTaskFailure (a
            # plain Exception) gets both: raising — not returning — stops
            # chain() from dispatching the next module (the per-module chain
            # is a single sequential chain, so nothing further is ever even
            # queued), while Celery's tracing records it correctly.
            # _parse_code_task's blocking chain .get() re-raises it there,
            # where it's caught and finalized as a failed source with the
            # original provider error message intact.
            logger.error(
                "Credit balance/quota exhausted while processing project_id=%s "
                "module_id=%s module_name=%s — failing the entire pipeline "
                "immediately, no retry: %s",
                project_id,
                module_id,
                module_name,
                exc,
            )
            _record_module_activity(
                project_id=project_id,
                source_id=source_id,
                task_db_id=task_db_id,
                module_id=module_id,
                module_name=module_name,
                outcome="failed",
                error=str(exc),
            )
            raise CreditBalanceExhaustedTaskFailure(str(exc)) from exc

        except NonRetryableLLMError as exc:
            # Same BaseException-passthrough/re-raise pattern as
            # CircuitBreakerError/CreditBalanceExhaustedError just above, for
            # every OTHER non-retryable reason (authentication, invalid
            # model, invalid request, context-length-exceeded, content
            # policy, permission denied). Per this pipeline's design, one
            # module hitting a deterministic, non-retryable LLM error stops
            # the WHOLE run immediately — not just this module — since
            # retrying (here or on the next module) will not help. Re-raising
            # NonRetryableLLMTaskFailure (a plain Exception) gets both: raising
            # — not returning — stops chain() from dispatching the next
            # module, while Celery's tracing records it correctly instead of
            # crashing the -P prefork worker child.
            classification = exc.classification
            error_detail = describe_non_retryable_llm_error(classification)
            logger.error(
                "Non-retryable LLM error (reason=%s) while processing project_id=%s "
                "module_id=%s module_name=%s — failing the entire pipeline "
                "immediately, no retry: %s",
                classification.reason.value,
                project_id,
                module_id,
                module_name,
                error_detail,
            )
            _record_module_activity(
                project_id=project_id,
                source_id=source_id,
                task_db_id=task_db_id,
                module_id=module_id,
                module_name=module_name,
                outcome="failed",
                error=error_detail,
            )
            raise NonRetryableLLMTaskFailure(error_detail, classification=classification) from exc

        except (SoftTimeLimitExceeded, MemoryError) as exc:
            # Usually a real hang / an always-OOM module rather than a
            # transient blip, so retry at most once — the poison-loop guard
            # above bounds a worker that keeps dying on the same module.
            logger.error(
                "%s for project_id=%s module_id=%s module_name=%s after %d s: %s",
                type(exc).__name__,
                project_id,
                module_id,
                module_name,
                TASK_SOURCE_CODE_MODULE_SOFT_TIME_LIMIT,
                MSG_SOURCE_CODE_MODULE_TIME_LIMIT_EXCEEDED,
            )
            return _retry_or_fail(exc, error_message=MSG_SOURCE_CODE_MODULE_TIME_LIMIT_EXCEEDED)

        except RETRYABLE_MODULE_INFRA as exc:
            logger.error(
                "Transient infrastructure error in _process_single_module_task "
                "project_id=%s module_id=%s module_name=%s: %s",
                project_id,
                module_id,
                module_name,
                exc,
            )
            return _retry_or_fail(exc)

        except botocore.exceptions.ClientError as exc:
            if is_retryable_boto(exc):
                logger.error(
                    "Retryable S3/AWS error in _process_single_module_task "
                    "project_id=%s module_id=%s module_name=%s: %s",
                    project_id,
                    module_id,
                    module_name,
                    exc,
                )
                return _retry_or_fail(exc)
            logger.exception(
                "Non-retryable S3/AWS error in _process_single_module_task "
                "project_id=%s module_id=%s module_name=%s: %s",
                project_id,
                module_id,
                module_name,
                exc,
            )
            return {
                "module_id": module_id,
                "error": str(exc),
                "status": "failed",
            }

        except Exception as exc:
            logger.exception(
                "Unexpected error in _process_single_module_task "
                "project_id=%s module_id=%s module_name=%s: %s",
                project_id,
                module_id,
                module_name,
                exc,
            )
            return {
                "module_id": module_id,
                "error": str(exc),
                "status": "failed",
            }


def _notify_source_code_pipeline_status(
    *,
    project_id: str,
    status: str,
    total_modules: int | None = None,
    total_features: int | None = None,
    total_user_stories: int | None = None,
    error: str | None = None,
    error_reason: str | None = None,
) -> None:
    """Best-effort: notify the project owner about a source-code pipeline status change.

    Fires for the ``running`` (started), ``ready_for_review`` (generation
    finished, awaiting human review), and ``failed`` transitions. Never
    raises — a notification failure must not abort the pipeline.
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
            title = "Source Code Pipeline Started"
            message = f'Source code processing has started for "{project.name}".'
            notification_type = NotificationType.INFO
        elif status == SourceIngestionStatus.READY_FOR_REVIEW.value:
            title = "Source Code Pipeline Ready for Review"
            message = (
                f"{total_modules} module(s), {total_features} feature(s), and "
                f'{total_user_stories} user story(ies) were generated for "{project.name}".'
            )
            notification_type = NotificationType.SUCCESS
        elif status == SourceIngestionStatus.FAILED.value:
            title = "Source Code Pipeline Failed"
            message = f'Source code processing failed for all modules in "{project.name}".'
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
                "total_modules": total_modules,
                "total_features": total_features,
                "total_user_stories": total_user_stories,
                "error_reason": error_reason,
            },
        )
    except Exception:
        logger.warning(
            "_notify_source_code_pipeline_status: failed to notify project_id=%s status=%s",
            project_id,
            status,
            exc_info=True,
        )


def _finalize_source_code_pipeline(
    *,
    project_id: str,
    source_id: str,
    status: str = SourceIngestionStatus.READY_FOR_REVIEW.value,
    task_db_id: str | None = None,
    error: str | None = None,
    error_reason: str | None = None,
    failed_modules: int = 0,
) -> None:
    """Best-effort: finalize SourceIngestion counts/timestamps once every
    module has been persisted.

    *status* reflects the outcome across all modules — READY_FOR_REVIEW when
    at least one module succeeded (awaiting human review of the generated
    backlog), FAILED when every module failed. The source-code pipeline
    never uses COMPLETED for this transition. *error* (only meaningful when
    *status* is FAILED) is recorded onto the SourceIngestion's ``errors``.
    *failed_modules* is the running count of modules that failed processing,
    persisted onto ``tot_modules_failed`` alongside the succeeded-module counts.

    Called exactly once per source (guarded by the caller's completion lock).
    Never raises — a bookkeeping failure here must not discard the
    already-persisted pipeline results or block the scheduled cleanup.
    """
    try:
        from app.repositories.neo4j.user_story_repository import UserStoryRepository

        summary = _run_async(UserStoryRepository().get_project_summary(UUID(project_id)))
        now = datetime.now(UTC)
        _update_source_ingestion_fields(
            source_ids=[source_id],
            fields={
                "mod_fea_gen_completed_at": now,
                "user_story_gen_completed_at": now,
                "tot_modules": summary["total_modules"],
                "tot_features": summary["total_features"],
                "tot_user_stories": summary["total_user_stories"],
                "tot_modules_failed": failed_modules,
                "status": status,
            },
        )
        if status == SourceIngestionStatus.FAILED.value:
            _add_source_ingestion_error(source_ids=[source_id], error=error or "")
            record_activity(
                project_id=UUID(project_id),
                activity_type=ActivityType.SOURCE_CODE_PIPELINE_FAILED,
                summary=SUMMARY_ACTIVITY_SOURCE_CODE_PIPELINE_FAILED,
                message=MSG_ACTIVITY_SOURCE_CODE_PIPELINE_FAILED.format(error=(error or "")[:200]),
                actor_user_id=resolve_actor_from_task(task_db_id),
                data={"source_id": source_id, "error": error, "llm_error_reason": error_reason},
            )
        _notify_source_code_pipeline_status(
            project_id=project_id,
            status=status,
            total_modules=summary["total_modules"],
            total_features=summary["total_features"],
            total_user_stories=summary["total_user_stories"],
            error=error,
            error_reason=error_reason,
        )
        if status == SourceIngestionStatus.READY_FOR_REVIEW.value:
            _add_source_code_stage(source_id, SourceIngestionStage.READY_FOR_REVIEW)

            record_activity(
                project_id=UUID(project_id),
                activity_type=ActivityType.SOURCE_CODE_PIPELINE_COMPLETED,
                summary=SUMMARY_ACTIVITY_SOURCE_CODE_PIPELINE_COMPLETED,
                message=MSG_ACTIVITY_SOURCE_CODE_PIPELINE_COMPLETED.format(
                    total_modules=summary["total_modules"],
                    total_features=summary["total_features"],
                    total_user_stories=summary["total_user_stories"],
                ),
                actor_user_id=resolve_actor_from_task(task_db_id),
                data={"source_id": source_id, **summary},
            )
    except Exception:
        logger.warning(
            "Failed to finalize source-code pipeline bookkeeping for source_id=%s",
            source_id,
            exc_info=True,
        )


def _handle_source_code_task_exception(
    source_id: str,
    exc: BaseException,
    task_name: str,
    *,
    task_db_id: str | None = None,
    project_id: str | None = None,
    error_reason: str | None = None,
) -> dict[str, Any]:
    """Terminal, source-code-only failure handler — never schedules a Celery retry.

    Unlike the shared ``_handle_task_exception`` (used by document/image
    tasks, which retries with exponential backoff before failing), this
    always fails immediately. ``app/services/source_code_pipeline``'s
    ``LLMClient`` already runs its own bounded per-call retry ladder before
    any exception reaches this task, so a Celery-level retry on top of that
    would only redo already-completed work (S3 download, extraction, every
    earlier module) to hit the same failure again — the same reasoning
    already applied to the ``CircuitBreakerError``/``ConcurrentPipelineError``
    branches in ``_parse_code_task``, which this consolidates.
    """
    logger.error(
        "%s failed: source_id=%s error=%s",
        task_name,
        source_id,
        exc,
        exc_info=True,
    )
    _mark_status(
        source_id,
        SOURCE_STATUS_FAILED,
        str(exc),
        stage="task.failed",
        task_db_id=task_db_id,
        project_id=project_id,
    )
    _finalize_source_code_pipeline(
        project_id=project_id,
        source_id=source_id,
        status=SourceIngestionStatus.FAILED.value,
        task_db_id=task_db_id,
        error=str(exc),
        error_reason=error_reason,
    )
    return {"source_id": source_id, "status": SOURCE_STATUS_FAILED, "error": str(exc)}


@celery_app.task(
    bind=True,
    name="tasks.parse_code.persist_single_module",
    acks_late=True,
    soft_time_limit=TASK_PERSIST_SINGLE_MODULE_SOFT_TIME_LIMIT,
    time_limit=TASK_PERSIST_SINGLE_MODULE_TIME_LIMIT,
    max_retries=SOURCE_CODE_TASK_MAX_RETRIES,
    default_retry_delay=SOURCE_CODE_TASK_RETRY_COUNTDOWN_SECONDS,
    ignore_result=False,
)
def _persist_single_module_task(
    self,
    module_result: dict[str, Any],  # injected as first positional arg by Celery chain
    project_id: str,
    source_id: str,
    project_dir: str | None = None,
    config_path: str | None = None,
    codebase_dir: str | None = None,
    skip_processing: bool = False,
    task_db_id: str | None = None,
    total_modules: int = 0,
    request_id: str | None = None,
    module_name: str | None = None,
) -> dict[str, Any]:
    """
    Persist the result of one processed module to Neo4j.

    Redis counter is incremented and TTL refreshed atomically via
              a pipeline so concurrent completions cannot race.

    Completion callback is protected by an nx=True Redis SET lock
              so retries / duplicate completions cannot fire _mark_status or
              cleanup more than once.

    Temp-folder cleanup is deferred to _cleanup_project_folder_task
              with a 30-second countdown rather than running inline, giving
              any still-running process tasks time to finish their I/O.
    """
    from app.core.celery_app import celery_app as _app  # redis backend

    module_id = _get_module_id(module_result)
    logger.info("Processing module result for persistence: module_id=%s", module_id)

    from app.core import task_control

    # Tracks whether this module should count against the "all modules
    # failed" check at pipeline completion. A module that already came back
    # marked failed from _process_single_module_task counts as failed here
    # too, even though it still runs through persistence below (it simply
    # persists zero results).
    module_failed = isinstance(module_result, dict) and module_result.get("status") == "failed"

    # Normalized like every other checkpoint in this file: an upload that
    # never set an explicit request_id has the flag stored under the task's
    # own id instead (see _handle_source_code_cancellation's docstring).
    # Without the fallback this check silently no-ops on that path.
    effective_request_id = request_id or task_db_id

    if effective_request_id and task_control.is_request_cancelled(effective_request_id):
        logger.info(
            "_persist_single_module_task: request cancelled, skipping persistence "
            "module_id=%s source_id=%s",
            module_id,
            source_id,
        )
        # Finalize (and return) immediately instead of falling through to the
        # completion-counter block below: that block increments
        # module_done_count and, once it reaches total_modules, marks the run
        # COMPLETED — which would wrongly overwrite the CANCELLED status this
        # finalizes to if the cancelled module happens to be the last one in
        # the chain (no subsequent module's entry checkpoint left to catch it
        # instead). _finalize_source_code_cancellation is idempotent-safe if
        # a later checkpoint also calls it for the same run.
        _finalize_source_code_cancellation(
            request_id=effective_request_id,
            task_db_id=task_db_id,
            project_id=project_id,
            source_ids=[source_id] if source_id else None,
        )
        _record_module_activity(
            project_id=project_id,
            source_id=source_id,
            task_db_id=task_db_id,
            module_id=module_id,
            module_name=module_name,
            outcome="cancelled",
        )
        return {
            "modules_processed": 0,
            "group_specs_stored": 0,
            "backlog_stories_stored": 0,
            "srs_evidence_stored": 0,
            "status": "cancelled",
        }

    # Poison-loop guard: see the identical check/comment in
    # _process_single_module_task — bounds total deliveries of this exact
    # task message independent of self.request.retries, since a worker
    # killed by OOM/spot reclaim never reaches the except blocks below.
    delivery_key = self.request.id or f"{project_id}:{module_id}"
    if not task_control.register_delivery_within_limit(
        delivery_key, max_deliveries=SOURCE_CODE_TASK_MAX_RETRIES + 1
    ):
        logger.error(
            "_persist_single_module_task: exceeded max deliveries for "
            "module_id=%s source_id=%s — module recorded as failed, "
            "continuing with remaining modules (poison-loop guard).",
            module_id,
            source_id,
        )
        module_failed = True
        result = {
            "modules_processed": 0,
            "group_specs_stored": 0,
            "backlog_stories_stored": 0,
            "srs_evidence_stored": 0,
            "error": "Exceeded maximum redelivery attempts (poison-loop guard)",
        }
    elif not isinstance(module_result, dict) or not module_result:
        result = {
            "modules_processed": 0,
            "group_specs_stored": 0,
            "backlog_stories_stored": 0,
            "srs_evidence_stored": 0,
        }
    else:

        def _retry_or_mark_failed(
            exc: BaseException, error_message: str | None = None
        ) -> dict[str, Any]:
            """Retry once (bounded by self.request.retries/self.max_retries), else fail."""
            if self.request.retries >= self.max_retries:
                return {
                    "modules_processed": 0,
                    "group_specs_stored": 0,
                    "backlog_stories_stored": 0,
                    "srs_evidence_stored": 0,
                    "error": error_message or str(exc),
                }
            raise self.retry(exc=exc, countdown=SOURCE_CODE_TASK_RETRY_COUNTDOWN_SECONDS)

        try:
            result = _run_async(
                _persist_pipeline_results(
                    project_id=project_id,
                    source_id=source_id,
                    module_results=[module_result],
                    task_db_id=task_db_id,
                    project_dir=project_dir,
                    config_path=config_path,
                    codebase_dir=codebase_dir,
                    skip_processing=skip_processing,
                )
            )
        except (SoftTimeLimitExceeded, MemoryError) as exc:
            # Retry at most once — the poison-loop guard above bounds a
            # worker that keeps dying on the same module. If retries are
            # exhausted, treat this module as failed rather than letting the
            # exception propagate — an unhandled exception here would abort
            # the outer chain(*module_chains) and stop every module after
            # this one from ever running.
            logger.error(
                "%s while persisting module_id=%s for source_id=%s after %d s.",
                type(exc).__name__,
                module_id,
                source_id,
                TASK_PERSIST_SINGLE_MODULE_SOFT_TIME_LIMIT,
            )
            result = _retry_or_mark_failed(
                exc, error_message=MSG_SOURCE_CODE_MODULE_PERSIST_TIME_LIMIT_EXCEEDED
            )
            module_failed = True
        except (*RETRYABLE_MODULE_INFRA, *RETRYABLE_NEO4J_INFRA) as exc:
            logger.error(
                "Transient infrastructure error while persisting module_id=%s for source_id=%s: %s",
                module_id,
                source_id,
                exc,
            )
            result = _retry_or_mark_failed(exc)
            module_failed = True
        except botocore.exceptions.ClientError as exc:
            if is_retryable_boto(exc):
                logger.error(
                    "Retryable S3/AWS error while persisting module_id=%s for source_id=%s: %s",
                    module_id,
                    source_id,
                    exc,
                )
                result = _retry_or_mark_failed(exc)
            else:
                logger.exception(
                    "Non-retryable S3/AWS error while persisting module_id=%s for source_id=%s: %s",
                    module_id,
                    source_id,
                    exc,
                )
                result = {
                    "modules_processed": 0,
                    "group_specs_stored": 0,
                    "backlog_stories_stored": 0,
                    "srs_evidence_stored": 0,
                    "error": str(exc),
                }
            module_failed = True
        except Exception as exc:
            logger.exception(
                "Failed to persist module_id=%s for source_id=%s: %s — "
                "module recorded as failed, continuing with remaining modules.",
                module_id,
                source_id,
                exc,
            )
            module_failed = True
            result = {
                "modules_processed": 0,
                "group_specs_stored": 0,
                "backlog_stories_stored": 0,
                "srs_evidence_stored": 0,
                "error": str(exc),
            }

    if module_failed:
        _record_module_activity(
            project_id=project_id,
            source_id=source_id,
            task_db_id=task_db_id,
            module_id=module_id,
            module_name=module_name,
            outcome="failed",
            error=result.get("error"),
        )
        _increment_source_ingestion_module_counts(
            source_ids=[source_id],
            modules_failed=1,
        )
    else:
        _record_module_activity(
            project_id=project_id,
            source_id=source_id,
            task_db_id=task_db_id,
            module_id=module_id,
            module_name=module_name,
            outcome="completed",
            total_features=result.get("group_specs_stored", 0),
            total_user_stories=result.get("backlog_stories_stored", 0),
        )
        _increment_source_ingestion_module_counts(
            source_ids=[source_id],
            modules=1,
            features=result.get("group_specs_stored", 0),
            user_stories=result.get("backlog_stories_stored", 0),
        )

    # ── Track completion and signal when all modules are done ────────────────
    if total_modules > 0:
        try:
            redis = _app.backend.client
            counter_key = f"module_done_count:{source_id}"
            failed_counter_key = f"module_failed_count:{source_id}"
            lock_key = f"module_completion_lock:{source_id}"

            # Atomic incr + expire in a single Redis pipeline
            pipe = redis.pipeline()
            pipe.incr(counter_key)
            pipe.expire(counter_key, COUNTER_TTL_SECONDS)  # 1-hour safety-net TTL
            if module_failed:
                pipe.incr(failed_counter_key)
                pipe.expire(failed_counter_key, COUNTER_TTL_SECONDS)
            pipe_results = pipe.execute()
            done_count = pipe_results[0]

            logger.info(
                "Module persisted for source_id=%s (%d/%d)%s",
                source_id,
                done_count,
                total_modules,
                " [FAILED]" if module_failed else "",
            )

            if done_count >= total_modules:
                # nx=True ensures only one worker fires the completion
                # logic, even if a task is retried or two workers race.
                acquired = redis.set(lock_key, "1", nx=True, ex=LOCK_TTL_SECONDS)
                if acquired:
                    failed_count = int(redis.get(failed_counter_key) or 0)
                    all_modules_failed = failed_count >= total_modules

                    if all_modules_failed:
                        logger.error(
                            "All %d/%d modules failed for source_id=%s — marking source "
                            "ingestion as FAILED.",
                            failed_count,
                            total_modules,
                            source_id,
                        )

                    pipeline_source_status = (
                        SOURCE_STATUS_FAILED
                        if all_modules_failed
                        else SOURCE_STATUS_READY_FOR_REVIEW
                    )
                    pipeline_ingestion_status = (
                        SourceIngestionStatus.FAILED.value
                        if all_modules_failed
                        else SourceIngestionStatus.READY_FOR_REVIEW.value
                    )

                    # Surfaced so the orchestrator task blocking on this chain's
                    # final result (see _run_pipeline_orchestrator) can return the
                    # pipeline's actual terminal status instead of always "running".
                    result["status"] = pipeline_ingestion_status

                    _mark_status(
                        source_id,
                        pipeline_source_status,
                        task_db_id=task_db_id,
                        project_id=project_id,
                        stage=f"source_code.pipeline.{pipeline_ingestion_status}",
                    )
                    emit_task_event(
                        task_db_id=task_db_id,
                        project_id=project_id,
                        task_type="source_process",
                        status=pipeline_ingestion_status,
                        stage=f"source_code.pipeline.{pipeline_ingestion_status}",
                        progress=100,
                        meta={
                            "source_id": source_id,
                            "failed_modules": failed_count,
                            "total_modules": total_modules,
                        },
                    )

                    _finalize_source_code_pipeline(
                        project_id=project_id,
                        source_id=source_id,
                        status=pipeline_ingestion_status,
                        task_db_id=task_db_id,
                        error=(
                            f"All {failed_count}/{total_modules} modules failed."
                            if all_modules_failed
                            else None
                        ),
                        failed_modules=failed_count,
                    )

                    # Generate domain-knowledge/architecture-document artifacts now
                    # that every module has finished refining module_manifest.json,
                    # via a dedicated task (its own AI-generation time budget —
                    # see _generate_pipeline_documents_task) rather than inline:
                    # cleanup is chained as its link so it still only runs (and
                    # deletes project_dir) once document generation is done.
                    # Skipped when every module failed: there is nothing
                    # meaningful to derive and it would waste an LLM call.
                    if not all_modules_failed:
                        _generate_pipeline_documents_task.apply_async(
                            kwargs={
                                "project_id": project_id,
                                "source_id": source_id,
                                "project_dir": project_dir,
                                "config_path": config_path,
                                "codebase_dir": codebase_dir,
                                "skip_processing": skip_processing,
                                "task_db_id": task_db_id,
                            },
                            link=_cleanup_project_folder_task.si(project_id).set(countdown=30),
                        )
                    else:
                        # Defer cleanup so in-flight process tasks finish I/O
                        _cleanup_project_folder_task.apply_async(
                            args=[project_id],
                            countdown=30,  # seconds of grace period
                        )

                    redis.delete(counter_key)
                    redis.delete(failed_counter_key)
                    logger.info(
                        "All %d modules done for source_id=%s (%d failed) — completion fired, "
                        "cleanup scheduled in 30 s.",
                        total_modules,
                        source_id,
                        failed_count,
                    )
                else:
                    logger.info(
                        "Completion lock already held for source_id=%s — skipping duplicate callback.",
                        source_id,
                    )
                    # Completion already fired on an earlier (redelivered) attempt
                    # for this same final module — read back the status it set
                    # instead of leaving the caller's result defaulted to "running".
                    from app.db.unit_of_work import UnitOfWork as _UnitOfWork

                    with _UnitOfWork() as uow:
                        existing_source = uow.sources.get_by_uuid(UUID(source_id))
                        if existing_source is not None:
                            result["status"] = existing_source.status
        except Exception as exc:
            # Redis failure must never discard an already-persisted module result.
            # Log the error and return the persist result normally.
            logger.exception(
                "Failed to update module completion counter for source_id=%s: %s",
                source_id,
                exc,
            )

    return result


@celery_app.task(
    bind=True,
    name="tasks.parse_code",
    acks_late=True,
    soft_time_limit=TASK_SOURCE_CODE_PIPELINE_SOFT_TIME_LIMIT,
    time_limit=TASK_SOURCE_CODE_PIPELINE_TIME_LIMIT,
)
def _parse_code_task(
    self,
    project_id: str,
    source_id: str,
    task_db_id: str | None = None,
    skip_processing: bool = False,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Parse source code from a ZIP archive via Tree-sitter.

    Steps
    ─────
    1. Transition source status → processing.
    2. Download ZIP bytes from S3.
    3. Extract and parse each source file with Tree-sitter.
    4. Upsert :File, :Function, :Class nodes and relationships in Neo4j.
    5. Generate and store a summary embedding from extracted symbols.
    6. Transition source status → ready_for_review (signalled per-module).
    """
    from app.core import task_control
    from app.core.llm_errors import NonRetryableLLMError
    from app.services.source_code_pipeline.src.ai.llm_client import (
        CircuitBreakerError,
        ConcurrentPipelineError,
        CreditBalanceExhaustedError,
    )
    from app.workers._task_helpers import (
        CircuitBreakerTaskFailure,
        CreditBalanceExhaustedTaskFailure,
        NonRetryableLLMTaskFailure,
        TaskCancelledError,
    )

    logger.info("_parse_code_task started: source_id=%s", source_id)

    # Redelivery guard — this task never schedules a Celery retry (see
    # _handle_source_code_task_exception), but Celery's at-least-once broker
    # redelivery (worker crash/ack timeout) can still redeliver the SAME
    # message. Without this, a redelivered invocation would unconditionally
    # re-mark the row "running" and redo S3 download/extract/every module
    # from scratch as a second, independent run. task_db_id is the stable
    # identifier carried by that redelivered message (a genuinely new manual
    # re-run always gets a fresh ProjectTask id, so this never blocks one).
    if task_db_id and not task_control.claim_task_execution(task_db_id):
        logger.warning(
            "_parse_code_task: duplicate/redelivered invocation detected for "
            "task_db_id=%s source_id=%s — skipping to avoid a second "
            "independent run.",
            task_db_id,
            source_id,
        )
        return {"source_id": source_id, "status": "duplicate_skipped"}

    # Entry checkpoint — every other checkpoint in this file
    # (_process_single_module_task, _persist_single_module_task) checks
    # cancellation at entry too.
    if _handle_source_code_cancellation(
        request_id=request_id,
        task_db_id=task_db_id,
        project_id=project_id,
        source_id=source_id,
    ):
        return {"source_id": source_id, "status": TASK_STATUS_CANCELLED}

    _mark_status(source_id, SOURCE_STATUS_RUNNING, task_db_id=task_db_id, project_id=project_id)
    _update_source_ingestion_fields(
        source_ids=[source_id],
        fields={
            "mod_fea_gen_started_at": datetime.now(UTC),
            "user_story_gen_started_at": datetime.now(UTC),
            "status": SourceIngestionStatus.RUNNING.value,
        },
    )
    _notify_source_code_pipeline_status(
        project_id=project_id,
        status=SourceIngestionStatus.RUNNING.value,
    )
    record_activity(
        project_id=UUID(project_id),
        activity_type=ActivityType.SOURCE_CODE_PIPELINE_STARTED,
        summary=SUMMARY_ACTIVITY_SOURCE_CODE_PIPELINE_STARTED,
        message=MSG_ACTIVITY_SOURCE_CODE_PIPELINE_STARTED,
        actor_user_id=resolve_actor_from_task(task_db_id),
        data={"source_id": source_id},
    )
    emit_task_event(
        task_db_id=task_db_id,
        project_id=project_id,
        task_type="source_process",
        status=SOURCE_STATUS_RUNNING,
        stage="source_code.parsing.started",
        progress=55,
        meta={"source_id": source_id},
    )

    try:
        _mark_status(
            source_id,
            SOURCE_STATUS_RUNNING,
            task_db_id=task_db_id,
            project_id=project_id,
            stage="source.processing",
        )
        emit_task_event(
            task_db_id=task_db_id,
            project_id=project_id,
            task_type="source_process",
            status=SOURCE_STATUS_RUNNING,
            stage="source_code.pipeline.started",
            progress=60,
            meta={"source_id": source_id},
        )
        result = _run_async(
            _async_parse_code(
                project_id,
                source_id,
                task_db_id=task_db_id,
                skip_processing=skip_processing,
                request_id=request_id,
            )
        )
    except TaskCancelledError as exc:
        # Raised by a chain-link checkpoint deep in the pipeline (e.g.
        # _process_single_module_task's entry/cancelled-result checks) and
        # propagated here by _run_pipeline_orchestrator's blocking chain
        # .get(). That checkpoint already finalized Source/SourceIngestion/
        # ProjectTask to "cancelled" before raising — this must NOT fall
        # through to `except Exception` below, which would mark the source
        # FAILED for what is actually a confirmed, already-finalized
        # cancellation.
        logger.info(
            "_parse_code_task: request_id=%s cancelled mid-run for source_id=%s: %s",
            request_id or task_db_id,
            source_id,
            exc,
        )
        return {"source_id": source_id, "status": TASK_STATUS_CANCELLED, "error": str(exc)}
    except (CircuitBreakerError, CircuitBreakerTaskFailure) as exc:
        # CircuitBreakerError is deliberately a BaseException (see
        # llm_client.py's class docstring) so a circuit-breaker abort can
        # never be swallowed by an `except Exception` deep in the module
        # pipeline into a "failed module, continue" result — that would
        # defeat the breaker's job of stopping the whole run. But that same
        # property means it also bypasses `except Exception` below, and — if
        # left to escape a Celery task boundary unhandled — isn't recorded
        # as a normal FAILURE by Celery at all (verified empirically: under
        # the real -P prefork pool it crashes the worker child outright
        # instead). CircuitBreakerError raised directly in this task's own
        # thread (e.g. during global-artifact generation above) is a plain
        # Python exception here, so it's caught fine. One tripped inside a
        # module's process_single_module_task is instead caught there and
        # re-raised as CircuitBreakerTaskFailure — a plain Exception Celery
        # records correctly — which this orchestrator's blocking chain
        # .get() then re-raises; both land here and are handled identically.
        #
        # Never retried: the breaker only trips after the LLM client's own
        # bounded per-call retry ladder (app/services/source_code_pipeline)
        # already exhausted itself across 8 consecutive timeouts, so the
        # provider/config issue is already confirmed persistent — a Celery
        # retry would just re-run every already-processed module from
        # scratch (S3 re-download, global artifacts, LLM calls) only to hit
        # the same wall again. Fail this source immediately instead (see
        # _handle_source_code_task_exception). Nothing else is dispatched
        # afterwards: chain(*module_chains) is a single
        # sequential chain (see _run_pipeline_orchestrator), so a link failing
        # here already stops every later module for THIS source/project —
        # no separate task-revocation step is needed, and no process-global
        # state (the circuit breaker, PipelineOrchestrator's run guard) is
        # left dirty for another project's pipeline landing on this worker
        # afterwards (see LLMClient.pipeline_run_guard's finally block).
        #
        # A cancel request can race with the breaker tripping (both stem from
        # the same burst of LLM timeouts) — check for that first so a
        # cancelled run is finalized as cancelled, not stomped to failed.
        if _handle_source_code_cancellation(
            request_id=request_id,
            task_db_id=task_db_id,
            project_id=project_id,
            source_id=source_id,
        ):
            logger.info(
                "Circuit breaker tripped for source_id=%s (project_id=%s) after the "
                "request was already cancelled — finalizing as cancelled, not failed: %s",
                source_id,
                project_id,
                exc,
            )
            return {"source_id": source_id, "status": TASK_STATUS_CANCELLED, "error": str(exc)}
        return _handle_source_code_task_exception(
            source_id,
            exc,
            "_parse_code_task (circuit breaker)",
            task_db_id=task_db_id,
            project_id=project_id,
            error_reason="circuit_breaker",
        )
    except (CreditBalanceExhaustedError, CreditBalanceExhaustedTaskFailure) as exc:
        # Same passthrough pattern as the CircuitBreakerError branch above,
        # for the same two reasons (BaseException-ness must reach here
        # without being swallowed by `except Exception` deep in the
        # pipeline, but must not escape this Celery task boundary
        # unhandled). CreditBalanceExhaustedError raised directly in this
        # task's own thread (e.g. during global-artifact generation) is a
        # plain Python exception here, so it's caught fine; one tripped
        # inside a module's process_single_module_task is caught there and
        # re-raised as CreditBalanceExhaustedTaskFailure, which this
        # orchestrator's blocking chain .get() re-raises — both land here.
        #
        # Never retried: this is an account-wide provider billing/quota
        # failure — every other module still queued behind the one that hit
        # it would fail identically, so retrying (here or per-module) would
        # only burn time to rediscover the same wall. Nothing else is
        # dispatched afterwards: chain(*module_chains) is a single
        # sequential chain (see _run_pipeline_orchestrator), so a link
        # failing here already stops every later module for THIS
        # source/project — no separate Redis-queue purge or task-revocation
        # step is needed.
        #
        # A cancel request can race with this — check for that first so a
        # cancelled run is finalized as cancelled, not stomped to failed.
        if _handle_source_code_cancellation(
            request_id=request_id,
            task_db_id=task_db_id,
            project_id=project_id,
            source_id=source_id,
        ):
            logger.info(
                "Credit balance/quota exhausted for source_id=%s (project_id=%s) "
                "after the request was already cancelled — finalizing as "
                "cancelled, not failed: %s",
                source_id,
                project_id,
                exc,
            )
            return {"source_id": source_id, "status": TASK_STATUS_CANCELLED, "error": str(exc)}
        return _handle_source_code_task_exception(
            source_id,
            exc,
            "_parse_code_task (credit balance/quota exhausted)",
            task_db_id=task_db_id,
            project_id=project_id,
            error_reason="credit_exhausted",
        )
    except (NonRetryableLLMError, NonRetryableLLMTaskFailure) as exc:
        # Same passthrough pattern as the CircuitBreakerError/
        # CreditBalanceExhaustedError branches above, for every OTHER
        # non-retryable reason (authentication, invalid model, invalid
        # request, context-length-exceeded, content policy, permission
        # denied). NonRetryableLLMError raised directly in this task's own
        # thread is a plain Python exception here, so it's caught fine; one
        # raised inside a module's process_single_module_task is caught
        # there and re-raised as NonRetryableLLMTaskFailure, which this
        # orchestrator's blocking chain .get() re-raises — both land here.
        #
        # Never retried: this is a deterministic, non-retryable failure —
        # every other module still queued behind the one that hit it would
        # fail identically (or the input itself is unfixable by retrying),
        # so retrying would only burn time to rediscover the same wall.
        # Nothing else is dispatched afterwards: chain(*module_chains) is a
        # single sequential chain, so a link failing here already stops
        # every later module for THIS source/project.
        #
        # A cancel request can race with this — check for that first so a
        # cancelled run is finalized as cancelled, not stomped to failed.
        classification = getattr(exc, "classification", None)
        error_reason = classification.reason.value if classification else None
        if _handle_source_code_cancellation(
            request_id=request_id,
            task_db_id=task_db_id,
            project_id=project_id,
            source_id=source_id,
        ):
            logger.info(
                "Non-retryable LLM error for source_id=%s (project_id=%s) after the "
                "request was already cancelled — finalizing as cancelled, not failed: %s",
                source_id,
                project_id,
                exc,
            )
            return {"source_id": source_id, "status": TASK_STATUS_CANCELLED, "error": str(exc)}
        return _handle_source_code_task_exception(
            source_id,
            exc,
            "_parse_code_task (non-retryable LLM error)",
            task_db_id=task_db_id,
            project_id=project_id,
            error_reason=error_reason,
        )
    except ConcurrentPipelineError as exc:
        # LLMClient.pipeline_run_guard's process-wide single-flight lock
        # (app/services/source_code_pipeline) rejected this run because
        # another pipeline currently holds it in this worker process —
        # e.g. a different project's task sharing this worker child
        # (--max-tasks-per-child). Transient contention, but still fails
        # immediately rather than retrying: see
        # _handle_source_code_task_exception's docstring.
        return _handle_source_code_task_exception(
            source_id,
            exc,
            "_parse_code_task (pipeline single-flight guard busy)",
            task_db_id=task_db_id,
            project_id=project_id,
        )
    except Exception as exc:
        # No Celery retry for source code: app/services/source_code_pipeline
        # already retries LLM calls internally, so this is always a genuine,
        # already-exhausted failure — retrying the whole task here would only
        # redo already-completed work. See
        # _handle_source_code_task_exception's docstring.
        return _handle_source_code_task_exception(
            source_id,
            exc,
            "_parse_code_task",
            task_db_id=task_db_id,
            project_id=project_id,
        )

    status = result.get("status", SOURCE_STATUS_RUNNING)

    # Cancellation is already finalized (source/ingestion marked cancelled)
    # inside _run_pipeline_orchestrator's own checkpoint — don't re-mark it
    # running here, that would clobber the terminal cancelled status.
    if status == TASK_STATUS_CANCELLED:
        logger.info(
            "_parse_code_task cancelled: source_id=%s",
            source_id,
        )
        return {"source_id": source_id, "status": status, "summary": result}

    # _run_pipeline_orchestrator now blocks until every module chain finishes,
    # so a terminal status here is already fully finalized (status/events/
    # cleanup all handled inside _persist_single_module_task's completion
    # branch) — don't re-mark it "running" below, that would clobber it.
    if status in (SOURCE_STATUS_FAILED, SOURCE_STATUS_READY_FOR_REVIEW):
        logger.info(
            "_parse_code_task finished: source_id=%s status=%s",
            source_id,
            status,
        )
        return {"source_id": source_id, "status": status, "summary": result}

    # status defaulted to "running" because the chain's last link
    # (_persist_single_module_task) didn't return a terminal status — the
    # only way that happens is a Redis error while checking/incrementing the
    # completion counter for the pipeline's last module (see its own
    # `except Exception` there). The module's Neo4j data was still persisted
    # successfully; only the completion signal was lost. Source/SourceIngestion
    # are already sitting at "running" untouched (nothing set them to
    # anything else), so there is nothing terminal here to clobber — but
    # also nothing to usefully re-write: _mark_status/emit_task_event would
    # just re-confirm the same value the row already has.
    logger.warning(
        "_parse_code_task: no terminal status observed for source_id=%s after "
        "the module chain finished — likely a Redis error during the last "
        "module's completion check (see _persist_single_module_task); the "
        "pipeline may be stuck at 'running' with no further checkpoint to "
        "finalize it. summary=%s",
        source_id,
        json.dumps(result, default=str, sort_keys=True),
    )
    return {"source_id": source_id, "status": status, "summary": result}


async def _async_parse_code(
    project_id: str,
    source_id: str,
    task_db_id: str | None = None,
    skip_processing: bool = False,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Async implementation of the source-code parsing pipeline."""
    from app.db.unit_of_work import UnitOfWork
    from app.workers._task_helpers import (
        _download_source_zip_from_s3,
        _extract_zip_to_codebase_folder,
    )

    with UnitOfWork() as uow:
        source = uow.sources.get_by_uuid(UUID(source_id))
        if source is None or not source.storage_key:
            raise ValueError(f"Source {source_id} not found or has no storage_key")
        storage_key = source.storage_key

    # ── 1. Download ZIP from S3 ──────────────────────────────────────────────
    zip_bytes = await _download_source_zip_from_s3(source_id, storage_key)

    # ── 2. Extract ZIP to /temp/source_codes/{project_id}/codebase ──────────
    codebase_dir = _extract_zip_to_codebase_folder(zip_bytes, project_id)
    logger.info("Extracted codebase to: %s", codebase_dir)

    # ── 3. Run Pipeline Orchestrator ─────────────────────────────────────────
    summary = await _run_pipeline_orchestrator(
        project_id=project_id,
        source_id=source_id,
        codebase_dir=codebase_dir,
        task_db_id=task_db_id,
        skip_processing=skip_processing,
        request_id=request_id,
    )

    logger.info(
        "_async_parse_code skip processing=%s for project_id=%s source_id=%s",
        skip_processing,
        project_id,
        source_id,
    )

    return summary


def _record_global_artifacts_activity(
    *, project_id: str, task_db_id: str | None, source_id: str, modules_detected: int
) -> None:
    """Log an activity-feed entry for a completed global-artifacts generation stage."""
    record_activity(
        project_id=UUID(project_id),
        activity_type=ActivityType.SOURCE_CODE_GLOBAL_ARTIFACTS_COMPLETED,
        summary=SUMMARY_ACTIVITY_SOURCE_CODE_GLOBAL_ARTIFACTS_COMPLETED,
        message=MSG_ACTIVITY_SOURCE_CODE_GLOBAL_ARTIFACTS_COMPLETED.format(
            modules_detected=modules_detected
        ),
        actor_user_id=resolve_actor_from_task(task_db_id),
        data={"source_id": source_id, "modules_detected": modules_detected},
    )


async def _run_pipeline_orchestrator(
    project_id: str,
    source_id: str,
    codebase_dir: str,
    task_db_id: str | None = None,
    skip_processing: bool = False,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Run the pipeline orchestrator to generate global and module-specific artifacts.

    Args:
        project_id:   Project UUID
        source_id:    Source UUID
        codebase_dir: Path to the extracted codebase
        task_db_id:   Optional task DB identifier for event emission
        request_id:   Cooperative-cancellation request id shared by every
            task/subtask this source's processing fans out to.

    Returns:
        Summary dict with modules_dispatched count.
    """
    # Normalized once so every use below (PipelineContext, both checkpoints,
    # and the per-module chain-dispatch call) sees the same id the cancel
    # flag is actually stored under for a plain single-source upload (see
    # _handle_source_code_cancellation's docstring re: the task_db_id fallback).
    request_id = request_id or task_db_id

    from pathlib import Path

    from app.db.unit_of_work import UnitOfWork
    from app.services.source_code_pipeline.pipeline_orchestrator import (
        PipelineContext,
        PipelineOrchestrator,
    )
    from app.services.source_code_pipeline.src.ai.llm_client import PipelineRunCancelled

    # ── Retrieve configuration from the source's ingestion batch ─────────────
    # These fields describe the whole upload batch, not an individual file,
    # so they live on SourceIngestion (source.source_ingestion) rather than
    # directly on the Source row.
    with UnitOfWork() as uow:
        source = uow.sources.get_by_uuid(UUID(source_id))
        if source is None:
            raise ValueError(f"Source {source_id} not found")

        ingestion = source.source_ingestion

        source_language = getattr(ingestion, "source_language", None)
        frontend_stack = getattr(ingestion, "frontend_stack", None)
        backend_stack = getattr(ingestion, "backend_stack", None)
        infrastructure_stack = getattr(ingestion, "infrastructure_stack", None)
        architecture_stack = getattr(ingestion, "architecture_stack", None)
        database_stack = getattr(ingestion, "database_stack", None)
        coding_standard = getattr(ingestion, "coding_standard", None)
        database_strategy = getattr(ingestion, "database_strategy", None)
        architecture = getattr(ingestion, "architecture", None)
        security = getattr(ingestion, "security", None)
        source_layout_type = getattr(ingestion, "source_layout_type", None)

        target_stack = {
            "frontend": frontend_stack,
            "backend": backend_stack,
            "infrastructure": infrastructure_stack,
            "architecture": architecture_stack,
            "database": database_stack,
        }
        modernization_manifesto = {
            "coding_standard": coding_standard,
            "database_strategy": database_strategy,
            "architecture": architecture,
            "security": security,
        }

    project_dir = Path(codebase_dir).parent  # /temp/source_codes/{project_id}

    # ── Configure project ────────────────────────────────────────────────────
    from app.workers.document_task_stages import _get_project_llm_options  # noqa: PLC0415

    options = _get_project_llm_options(project_id=project_id)
    logger.info("Configuring pipeline for project_id=%s", project_id)

    configure_project_kwargs = {
        "project_dir": project_dir,
        "source_paradigm": source_language,
        "target_stack": target_stack,
        "modernization_manifesto": modernization_manifesto,
        "source_layout_type": source_layout_type,
        "options": options,
    }

    # START: Testing and debugging: dump the configure_project kwargs to a JSON file for inspection
    from app.utils.common import dump_json_debug  # noqa: PLC0415

    dump_json_debug(
        "source_code_configure_project_input.json",
        {**configure_project_kwargs, "options": {k: v for k, v in options.items() if k != "llm_api_key"}},
        base_dir="temp/source_codes",
    )
    # END: Testing and debugging: dump the configure_project kwargs to a JSON file for inspection

    config_path = PipelineOrchestrator.configure_project(**configure_project_kwargs)

    # ── Create pipeline context ──────────────────────────────────────────────
    context = PipelineContext(
        project_dir=str(project_dir),
        config_path=str(config_path),
        source_dir=codebase_dir,
        skip_processing=skip_processing,
        request_id=request_id,
        llm_api_key=options.get("llm_api_key"),
    )

    logger.info(
        "_run_pipeline_orchestrator skip processing=%s for project_id=%s source_id=%s",
        skip_processing,
        project_id,
        source_id,
    )

    # ── Initialise pipeline ──────────────────────────────────────────────────
    pipeline = PipelineOrchestrator(context)

    # ── Cancellation checkpoint: before the (potentially long) global-artifact
    # LLM generation starts, so a cancel requested during S3 download/extract
    # doesn't waste that work too.
    if _handle_source_code_cancellation(
        request_id=request_id,
        task_db_id=task_db_id,
        project_id=project_id,
        source_id=source_id,
    ):
        return {"modules_dispatched": 0, "status": "cancelled"}

    # ── Stage 1: Generate global artifacts ───────────────────────────────────
    emit_task_event(
        task_db_id=task_db_id,
        project_id=project_id,
        task_type="source_process",
        status=SOURCE_STATUS_RUNNING,
        stage="source_code.global_artifacts.started",
        progress=65,
        meta={"source_id": source_id},
    )
    logger.info("Generating global artifacts for project_id=%s", project_id)
    try:
        _add_source_code_stage(source_id, SourceIngestionStage.BUILDING_CODE_DEPENDENCY_GRAPH)
        dependency_graph = pipeline.generate_code_dependency_graph()
        _add_source_code_stage(source_id, SourceIngestionStage.DISCOVERING_MODULES)
        global_artifacts = pipeline.discover_modules(dependency_graph=dependency_graph)
    except PipelineRunCancelled:
        # The global phase is a long, LLM-heavy stretch with no per-stage
        # checkpoint of its own, so the LLM client's check is what stops it.
        # PipelineRunCancelled is a BaseException — it would otherwise escape
        # this task's `except Exception` and skip cancellation bookkeeping
        # entirely, leaving the run stuck reading "running".
        logger.info(
            "Global artifact generation cancelled for project_id=%s source_id=%s "
            "— no module chains dispatched.",
            project_id,
            source_id,
        )
        _finalize_source_code_cancellation(
            request_id=request_id,
            task_db_id=task_db_id,
            project_id=project_id,
            source_ids=[source_id] if source_id else None,
        )
        return {"modules_dispatched": 0, "status": "cancelled"}

    modules_detected = len(global_artifacts.get("filtered_modules", []))
    emit_task_event(
        task_db_id=task_db_id,
        project_id=project_id,
        task_type="source_process",
        status=SOURCE_STATUS_RUNNING,
        stage="source_code.global_artifacts.completed",
        progress=72,
        meta={
            "source_id": source_id,
            "modules_detected": modules_detected,
        },
    )
    _update_source_ingestion_fields(
        source_ids=[source_id],
        fields={"tot_modules_from_global_artifact": modules_detected},
    )
    _record_global_artifacts_activity(
        project_id=project_id,
        task_db_id=task_db_id,
        source_id=source_id,
        modules_detected=modules_detected,
    )

    # Domain-knowledge/architecture-document generation happens later, in
    # _persist_single_module_task once every module has actually finished
    # processing — module_manifest.json only gets its DDD-quality names
    # (via refine_module_names) as each module's Stage 3 completes, and the
    # two generators both read that file. Generating them here, right after
    # generate_global_artifacts() and before any module has run, would see
    # only the pre-refinement manifest.

    # ── Cancellation checkpoint: after global-artifact generation, before
    # dispatching per-module chains, so a cancel requested mid-generation
    # stops the run instead of fanning out every module's chain.
    if _handle_source_code_cancellation(
        request_id=request_id,
        task_db_id=task_db_id,
        project_id=project_id,
        source_id=source_id,
    ):
        return {"modules_dispatched": 0, "status": "cancelled"}

    # ── Stage 2: Dispatch all modules in parallel via Celery chains ──────────
    filtered_modules: list[dict[str, Any]] = global_artifacts.get("filtered_modules", [])
    logger.info("Processing %d modules for project_id=%s", len(filtered_modules), project_id)

    # Removed hard [:3] cap that silently dropped 18 of 21 modules.
    # For controlled local testing only, set MODULE_TEST_CAP=3 (or any int)
    # in your environment before starting the worker.
    # _test_cap_env = os.getenv("MODULE_TEST_CAP")
    # _test_cap_env = 1
    # if _test_cap_env:
    #     try:
    #         _cap = int(_test_cap_env)
    #         filtered_modules = filtered_modules[:_cap]
    #         logger.warning(
    #             "MODULE_TEST_CAP=%d active — only %d module(s) will be processed "
    #             "for project_id=%s. Unset MODULE_TEST_CAP for production.",
    #             _cap,
    #             len(filtered_modules),
    #             project_id,
    #         )
    #     except ValueError:
    #         logger.warning("MODULE_TEST_CAP env var is not a valid integer — ignoring.")

    _add_source_code_stage(source_id, SourceIngestionStage.EXTRACTING_REQUIREMENTS)

    if filtered_modules:
        emit_task_event(
            task_db_id=task_db_id,
            project_id=project_id,
            task_type="source_process",
            status=SOURCE_STATUS_RUNNING,
            stage="source_code.module_processing.started",
            progress=75,
            meta={"source_id": source_id, "total_modules": len(filtered_modules)},
        )

        total = len(filtered_modules)

        # Build one sequential (process -> persist) chain per module.
        # All module chains are composed into a single outer chain so
        # modules are processed one at a time.  Switch back to
        # group(module_chains).apply_async() to restore parallel execution.
        module_chains = [
            chain(
                _process_single_module_task.si(
                    str(project_dir),
                    str(config_path),
                    codebase_dir,
                    module_data,
                    global_artifacts["artifacts_enriched"],
                    global_artifacts["module_manifest"],
                    skip_processing,
                    project_id=str(project_id),
                    request_id=request_id,
                    source_id=source_id,
                    task_db_id=task_db_id,
                ),
                _persist_single_module_task.s(
                    project_id=project_id,
                    source_id=source_id,
                    project_dir=str(project_dir),
                    config_path=str(config_path),
                    codebase_dir=codebase_dir,
                    skip_processing=skip_processing,
                    task_db_id=task_db_id,
                    total_modules=total,
                    request_id=request_id,
                    module_name=module_data.get("module_name")
                    if isinstance(module_data, dict)
                    else None,
                ),
            )
            for module_data in filtered_modules
        ]

        # Sequential dispatch: all module chains run one after another.
        # apply_async() is synchronous Celery I/O; run it in a thread so
        # the async event-loop is not blocked while publishing.
        async_result = await asyncio.to_thread(chain(*module_chains).apply_async)

        logger.info(
            "Dispatched %d module chains sequentially (single chain) for project_id=%s — "
            "blocking until the last module's persistence completes.",
            total,
            project_id,
        )

        # Block until the chain's final link (the last module's
        # _persist_single_module_task call, which sets result["status"] once
        # module_done_count reaches total_modules) finishes, so this
        # orchestrator returns the pipeline's real terminal status
        # (failed/ready_for_review) instead of always "running".
        # REQUIRES tasks.parse_code (QUEUE_SOURCE_CODE_PARSING) to run on a
        # worker pool separate from the module processing/persistence queues
        # it waits on here — otherwise a saturated shared pool can deadlock
        # (every slot blocked in this .get() with none left to run the
        # module tasks it's waiting for). Satisfied by the i4_worker3 pool
        # (see app/core/celery_app.py's instance-4 notes / docker-compose.yml)
        # — don't merge source_code_parsing back onto worker1's queue list.
        # See .claude/rules/workers.md.
        # disable_sync_subtasks=False is required: Celery refuses a bare
        # result.get() inside a task body otherwise.
        final_module_result = await asyncio.to_thread(async_result.get, disable_sync_subtasks=False)
        pipeline_status = (
            final_module_result.get("status", SOURCE_STATUS_RUNNING)
            if isinstance(final_module_result, dict)
            else SOURCE_STATUS_RUNNING
        )

        logger.info(
            "All %d module chains finished for project_id=%s source_id=%s — pipeline status=%s",
            total,
            project_id,
            source_id,
            pipeline_status,
        )
    else:
        # No modules were discovered — no chain is ever dispatched, so nothing
        # would otherwise transition this source out of "running". Treat it as
        # a terminal failure (there is no backlog to review) instead of
        # leaving the pipeline stuck forever.
        logger.error(
            "No modules detected for project_id=%s source_id=%s — marking "
            "source-code pipeline as FAILED (nothing to process).",
            project_id,
            source_id,
        )
        pipeline_status = SOURCE_STATUS_FAILED
        _mark_status(
            source_id,
            SOURCE_STATUS_FAILED,
            task_db_id=task_db_id,
            project_id=project_id,
            stage=f"source_code.pipeline.{SourceIngestionStatus.FAILED.value}",
        )
        emit_task_event(
            task_db_id=task_db_id,
            project_id=project_id,
            task_type="source_process",
            status=SourceIngestionStatus.FAILED.value,
            stage=f"source_code.pipeline.{SourceIngestionStatus.FAILED.value}",
            progress=100,
            meta={"source_id": source_id, "failed_modules": 0, "total_modules": 0},
        )
        _finalize_source_code_pipeline(
            project_id=project_id,
            source_id=source_id,
            status=SourceIngestionStatus.FAILED.value,
            task_db_id=task_db_id,
            error="No modules were detected in the codebase — nothing to process.",
        )
        _cleanup_project_folder_task.apply_async(
            args=[project_id],
            countdown=30,  # seconds of grace period, mirrors the module-completion path
        )

    return {
        "modules_dispatched": len(filtered_modules),
        "group_specs_stored": 0,  # accumulated per-chain
        "backlog_stories_stored": 0,
        "srs_evidence_stored": 0,
        "status": pipeline_status,
    }


# ── Feature MFU regeneration (single feature, human feedback) ──────────────


def _map_acceptance_criteria_for_regeneration(raw_ac_list: Any) -> list[dict[str, Any]]:
    """Normalize regeneration-output acceptance criteria to the UserStoryModel shape."""
    if not isinstance(raw_ac_list, list):
        return []

    mapped: list[dict[str, Any]] = []
    for ac in raw_ac_list:
        if not isinstance(ac, dict):
            continue
        mapped_ac: dict[str, Any] = {
            "type": ac.get("path_type") or ac.get("type") or "",
            "given": ac.get("given") or "",
            "when": ac.get("when") or "",
            "then": ac.get("then") or "",
        }
        ac_id = ac.get("id")
        if ac_id is not None:
            mapped_ac["id"] = str(ac_id)
            mapped_ac["ac_code"] = str(ac_id)
        if ac.get("l2_source_ref") is not None:
            mapped_ac["l2_source_ref"] = str(ac.get("l2_source_ref"))
        mapped.append(mapped_ac)
    return mapped


def _normalize_comparable(value: Any) -> str:
    """Canonical string form of a field's value for content-equality checks."""
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True, default=str)
    return str(value).strip()


def _feature_content_changed(
    existing: Any,
    *,
    name: str,
    description: str | None,
    functions: list,
    l2_sources: list,
) -> bool:
    """True if the regenerated feature payload differs from what's stored.

    ``fea_code`` is deliberately excluded — the AI mints a fresh code on every
    regeneration even when the underlying feature is untouched. ``sources``
    (RFP-document evidence) is untouched by regeneration and therefore not
    diffed here — only ``l2_sources`` (source-code pipeline references) can
    change on a regenerated feature.
    """
    if existing is None:
        return True
    existing_functions = [
        {
            "fun_code": fn.fun_code,
            "name": fn.name,
            "description": fn.description,
            "func_src_ref": fn.func_src_ref,
        }
        for fn in existing.functions or []
    ]
    return (
        _normalize_comparable(existing.name) != _normalize_comparable(name)
        or _normalize_comparable(existing.description) != _normalize_comparable(description)
        or _normalize_comparable(existing_functions) != _normalize_comparable(functions)
        or _normalize_comparable(existing.l2_sources) != _normalize_comparable(l2_sources)
    )


_STORY_CONTENT_FIELDS = (
    "title",
    "description",
    "as_a",
    "i_want_to",
    "so_that",
    "technical_notes",
    "story_points",
    "acceptance_criteria",
    "nfrs",
    "l2_sources",
)


def _story_content_changed(existing: Any, model: Any) -> bool:
    """True if the regenerated story payload differs from what's stored.

    ``user_story_code`` isn't compared — `model`'s id is deterministically
    derived from it, so a matching `existing` row already implies it's the same.
    """
    if existing is None:
        return True
    return any(
        _normalize_comparable(getattr(existing, field_name))
        != _normalize_comparable(getattr(model, field_name))
        for field_name in _STORY_CONTENT_FIELDS
    )


async def _upsert_one_regenerated_story(
    *,
    us_repo: Any,
    project_uuid: UUID,
    feature_id: str,
    story: dict[str, Any],
    source_ingestion_id: str | None = None,
) -> str | None:
    """Build and, if its content actually changed, upsert a single regenerated user story.

    Returns ``"added"``/``"updated"`` when a write happened, or ``None`` when
    the story is identical to what's already stored (no write, no status/
    feedback_change_type/sync-flag mutation) or the payload has no story id.
    """
    from app.models.neo4j.module_feature_model import ChangeType
    from app.models.neo4j.user_story_model import UserStoryModel

    story_code = str(story.get("id") or "")
    if not story_code:
        return None

    user_story_id = str(uuid5(NAMESPACE_URL, f"{project_uuid}:{story_code}"))
    existing = await us_repo.get_user_story_detail_for_project(
        project_id=project_uuid, user_story_id=user_story_id
    )
    # Fetched by id alone (not the project-scoped traversal above) so a
    # not-yet-established relationship link can't cause this to miss an
    # existing story and silently reset its version to 1.
    current_version = await us_repo.get_user_story_version_by_id(user_story_id)
    next_version = current_version + 1 if current_version is not None else INITIAL_ENTITY_VERSION

    model = UserStoryModel(
        id=user_story_id,
        user_story_code=story_code,
        title=str(story.get("title") or ""),
        description=story.get("description"),
        consensus=1.0,
        status=UserStoryStatus.READY.value,
        version=next_version,
        feature_id=feature_id,
        project_id=project_uuid,
        source_ingestion_id=source_ingestion_id,
        as_a=story.get("as_a"),
        i_want_to=story.get("i_want_to"),
        so_that=story.get("so_that"),
        acceptance_criteria=_map_acceptance_criteria_for_regeneration(
            story.get("acceptance_criteria")
        ),
        nfrs=_normalize_story_nfrs(story.get("nfrs") or story.get("non_functional_requirements")),
        technical_notes=story.get("technical_notes"),
        story_points=story.get("story_points"),
        l2_sources=_normalize_l2_sources(story.get("l2_sources")),
    )

    if not _story_content_changed(existing, model):
        return None

    change_type = ChangeType.UPDATED.value if existing is not None else ChangeType.ADDED.value
    model.feedback_change_type = change_type

    if existing is not None:
        await us_repo.snapshot_user_story_version(user_story_id)
    await us_repo.bulk_upsert_user_stories_for_project(
        project_id=project_uuid, user_stories=[model]
    )
    await us_repo.change_user_story_status(user_story_id, UserStoryStatus.READY.value)
    await us_repo.update_user_story_sync_flags(
        user_story_id, is_jira_synced=False, is_tap_synced=False
    )
    return change_type


def _normalize_l2_sources(raw_sources: Any) -> list[str]:
    """Extract the pipeline's ``l2_sources`` (bare source-id strings, e.g.
    ``"SRS::MFU-001::BATCH::STEP-001"``) as a plain string list.

    Distinct from ``sources`` (RFP-document {source_id, pages} evidence) —
    regeneration never touches ``sources``, only ``l2_sources``.
    """
    return [source for source in (raw_sources or []) if isinstance(source, str) and source]


async def _apply_feature_regeneration(
    *,
    mf_repo: Any,
    project_uuid: UUID,
    feature_id: str,
    module_id: str,
    feature_payload: dict[str, Any],
    source_ingestion_id: str | None = None,
) -> tuple[bool, str | None]:
    """Diff the regenerated feature payload against what's stored and write if changed.

    Returns ``(feature_updated, feature_change_type)`` — `feature_change_type`
    is ``None`` when the content was identical to what's already stored (no
    write happened at all).
    """
    from app.models.neo4j.module_feature_model import ChangeType

    existing_feature = await mf_repo.get_feature_for_module(project_uuid, module_id, feature_id)
    new_name = str(feature_payload.get("title") or "")
    new_description = feature_payload.get("description")
    new_functions = _normalize_functions_payload(feature_payload.get("functions") or [])
    new_l2_sources = _normalize_l2_sources(feature_payload.get("l2_sources"))

    if not _feature_content_changed(
        existing_feature,
        name=new_name,
        description=new_description,
        functions=new_functions,
        l2_sources=new_l2_sources,
    ):
        return False, None

    await mf_repo.snapshot_feature_version(project_uuid, feature_id)
    feature_updated = await mf_repo.update_feature(
        project_id=project_uuid,
        feature_id=feature_id,
        fea_code=str(feature_payload.get("id") or ""),
        name=new_name,
        description=new_description,
        functions_json=json.dumps(new_functions),
        sources_json=json.dumps(existing_feature.sources if existing_feature else []),
        l2_sources_json=json.dumps(new_l2_sources),
        feedback_change_type=ChangeType.UPDATED.value,
        source_ingestion_id=source_ingestion_id,
    )
    if not feature_updated:
        return False, None

    await mf_repo.update_feature_sync_flags(
        project_uuid, module_id, feature_id, is_jira_synced=False, is_tap_synced=False
    )
    return True, ChangeType.UPDATED.value


async def _persist_feature_regeneration_result(
    *,
    project_id: str,
    feature_id: str,
    module_id: str,
    features_stories: dict[str, Any] | None,
    source_ingestion_id: str | None = None,
) -> dict[str, Any]:
    """Apply a successful feature-regeneration result to the target Feature and its user stories.

    Compares the AI's regenerated feature/story content against what's
    currently stored before writing anything:
    - Unchanged: left untouched entirely — no write, no status change, no
      ``feedback_change_type``/sync-flag mutation.
    - Changed (existing feature, or an existing story with different content):
      written with ``feedback_change_type="UPDATED"`` and
      ``is_jira_synced``/``is_tap_synced`` reset to False (their previously
      synced content is now stale).
    - Newly added (a story id that doesn't exist yet): written with
      ``feedback_change_type="ADDED"`` and the sync flags reset to False.

    A single-feature MFU regeneration always yields exactly one feature in
    ``features_stories.features`` — that entry (not matched by id, since the
    AI regenerates fresh feature/story codes) is applied to the *existing*
    ``feature_id`` the caller targeted. Story ids are re-derived deterministically
    (uuid5 of project_id + user_story_code), matching the convention used at
    initial source-code ingestion, so re-running regeneration on the same MFU
    updates the same UserStory nodes in place instead of duplicating them.
    """
    from app.models.neo4j.module_feature_model import ChangeType
    from app.repositories.neo4j.module_feature_repository import ModuleFeatureRepository
    from app.repositories.neo4j.user_story_repository import UserStoryRepository

    features_stories = features_stories if isinstance(features_stories, dict) else {}
    features = features_stories.get("features")
    feature_payload = features[0] if isinstance(features, list) and features else None
    if not isinstance(feature_payload, dict):
        return {
            "feature_updated": False,
            "feature_change_type": None,
            "stories_added": 0,
            "stories_updated": 0,
            "stories_unchanged": 0,
        }

    mf_repo = ModuleFeatureRepository()
    us_repo = UserStoryRepository()
    project_uuid = UUID(project_id)

    feature_updated, feature_change_type = await _apply_feature_regeneration(
        mf_repo=mf_repo,
        project_uuid=project_uuid,
        feature_id=feature_id,
        module_id=module_id,
        feature_payload=feature_payload,
        source_ingestion_id=source_ingestion_id,
    )

    stories_added = 0
    stories_updated = 0
    stories_unchanged = 0
    for story in feature_payload.get("user_stories") or []:
        if not isinstance(story, dict):
            continue
        change_type = await _upsert_one_regenerated_story(
            us_repo=us_repo,
            project_uuid=project_uuid,
            feature_id=feature_id,
            story=story,
            source_ingestion_id=source_ingestion_id,
        )
        if change_type == ChangeType.ADDED.value:
            stories_added += 1
        elif change_type == ChangeType.UPDATED.value:
            stories_updated += 1
        else:
            stories_unchanged += 1

    return {
        "feature_updated": bool(feature_updated),
        "feature_change_type": feature_change_type,
        "stories_added": stories_added,
        "stories_updated": stories_updated,
        "stories_unchanged": stories_unchanged,
    }


async def _persist_feature_regeneration_result_by_mfu(
    *,
    project_id: str,
    mod_code: str,
    mfu_id: str,
    features_stories: dict[str, Any] | None,
    source_ingestion_id: str | None = None,
) -> dict[str, Any]:
    """Resolve the target Feature from ``(mod_code, mfu_id)`` and persist its regeneration result.

    The caller no longer pins a ``feature_id`` up front — feedback items only
    carry ``module_id``/``mfu_id`` — so the Feature is looked up here via
    `ModuleFeatureRepository.get_feature_by_mod_code_and_mfu`, then delegated
    to `_persist_feature_regeneration_result` with its *real* internal
    ``feature_id``/``module_id`` (fixing what would otherwise be a mismatch
    between the pipeline-facing ``mod_code`` and the internal Neo4j module id
    the repository's diff/update queries key off).
    """
    from app.repositories.neo4j.module_feature_repository import ModuleFeatureRepository

    existing_feature = await ModuleFeatureRepository().get_feature_by_mod_code_and_mfu(
        UUID(project_id), mod_code, mfu_id
    )
    if existing_feature is None:
        return {
            "feature_updated": False,
            "feature_change_type": None,
            "stories_added": 0,
            "stories_updated": 0,
            "stories_unchanged": 0,
            "error": f"no stored feature found for module={mod_code} mfu={mfu_id}",
        }

    return await _persist_feature_regeneration_result(
        project_id=project_id,
        feature_id=existing_feature.id,
        module_id=existing_feature.module_id,
        features_stories=features_stories,
        source_ingestion_id=source_ingestion_id,
    )


def _run_one_feature_mfu_revise_bucket(
    *,
    project_id: str,
    bucket: dict[str, Any],
    module_metadata_by_code: dict[str, dict[str, Any]],
    source_paradigm: str,
    workspace_dir: Path,
    skip_processing: bool,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Run Stage 5 revise for one grouped ``(module_id, mfu_id)`` feedback bucket.

    Reconstructs this MFU's spec files into `workspace_dir` (shared across all
    buckets in the request — each bucket writes to its own
    ``module_id/stage4_specs/mfu_id`` subtree, and buckets run strictly
    sequentially, so there is no cross-bucket collision) from that module's
    stored `SourceCodeMetadata`, then runs the revise pipeline against a
    structured ``feedback_spec`` built from the bucket.
    """
    from app.services.module_feature_service import ModuleFeatureService
    from app.services.source_code_pipeline.feedback_grouping import FeedbackGroupingService
    from app.services.source_code_pipeline.pipeline_orchestrator import (
        PipelineContext,
        PipelineOrchestrator,
    )

    bucket_module_id = bucket.get("module_id")
    bucket_mfu_id = bucket.get("mfu_id")
    feedback_spec = FeedbackGroupingService.compose_revise_feedback_spec_from_feature_bucket(bucket)

    module_meta = module_metadata_by_code.get(bucket_module_id) or {}
    srs_files, config_naming_map, features_stories = ModuleFeatureService._build_regeneration_specs(
        module_meta.get("module_response"), bucket_mfu_id
    )
    module_manifest = module_meta.get("module_manifest")

    input_dir = workspace_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    from app.workers.document_task_stages import _get_project_llm_options  # noqa: PLC0415

    options = _get_project_llm_options(project_id=project_id)
    config_path = PipelineOrchestrator.configure_project(
        project_dir=workspace_dir,
        source_paradigm=source_paradigm,
        options=options,
    )
    context = PipelineContext(
        project_dir=str(workspace_dir),
        config_path=str(config_path),
        source_dir=str(input_dir),
        skip_processing=skip_processing,
        # Without this the regeneration run has no cancel key at all, so every
        # checkpoint — the LLM client's included — silently no-ops and the
        # cancel button cannot stop a regeneration once it has started.
        request_id=request_id,
        llm_api_key=options.get("llm_api_key"),
    )
    pipeline = PipelineOrchestrator(context)

    revise_input = {
        "module_id": bucket_module_id,
        "mfu_id": bucket_mfu_id,
        "feedback_spec": feedback_spec,
        "mode": "edit",
        "srs_files": srs_files,
        "config_naming_map": config_naming_map,
        "module_manifest": module_manifest,
        "features_stories": features_stories,
        "source_paradigm": source_paradigm,
    }

    # START: Testing and debugging: dump the revise input payload and result to JSON files for inspection
    from app.utils.common import dump_json_debug

    dump_json_debug(
        f"revise_input_{bucket_module_id}_{bucket_mfu_id}.json",
        revise_input,
        base_dir="temp/feature_or_user_story_regenerate_for_soruce_code",
    )
    # END: Testing and debugging: dump the revise input payload and result to JSON files for inspection

    result = pipeline.process_mfu_revise_with_reconstruction(revise_input)

    # START: Testing and debugging: dump the revise result payload to a JSON file for inspection
    dump_json_debug(
        f"revise_result_{bucket_module_id}_{bucket_mfu_id}.json",
        result,
        base_dir="temp/feature_or_user_story_regenerate_for_soruce_code",
    )
    # END: Testing and debugging: dump the revise result payload to a JSON file for inspection

    if isinstance(result, dict):
        # Belt-and-suspenders: process_mfu_revise_with_reconstruction already
        # echoes these back, but a bucket-level identity is required even on
        # malformed/unexpected results so the caller can always attribute an
        # outcome to a target.
        result.setdefault("module_id", bucket_module_id)
        result.setdefault("mfu_id", bucket_mfu_id)
    return (
        result
        if isinstance(result, dict)
        else {
            "status": "failed",
            "module_id": bucket_module_id,
            "mfu_id": bucket_mfu_id,
            "error": "process_mfu_revise_with_reconstruction returned a non-dict result",
        }
    )


async def _async_run_feature_mfu_regeneration(
    *,
    project_id: str,
    feedback_items: list[dict[str, Any]],
    module_metadata_by_code: dict[str, dict[str, Any]],
    source_paradigm: str,
    workspace_dir: Path,
    skip_processing: bool = False,
    request_id: str | None = None,
) -> list[dict[str, Any]]:
    """Group feedback by MFU and run Stage 5 feature regeneration for each group.

    The pipeline no longer has access to the original extracted codebase (it
    is deleted shortly after ingestion completes) — `workspace_dir` is a
    fresh, task-scoped directory that
    `PipelineOrchestrator.process_mfu_revise_with_reconstruction` rebuilds each
    MFU's spec files into, one at a time, from that MFU's module's stored
    `SourceCodeMetadata` (looked up via `module_metadata_by_code`).

    `feedback_items` (mod_code/mfu_id/user_story_code/overall_feedback/
    specific_feedback dicts) is grouped into per-``(mod_code, mfu_id)``
    buckets via `FeedbackGroupingService`, and each bucket is run against the
    revise pipeline in turn. Returns one result dict per bucket.

    `skip_processing=True` bypasses the LLM call entirely and loads a
    canned sample result instead — for local testing only.
    """
    from app.services.source_code_pipeline.feedback_grouping import FeedbackGroupingService

    # `feedback_items` carry `mod_code` (the pipeline-facing module code,
    # matching the API request field) and `user_story_code` (the pipeline
    # story id used to scope story-level feedback) — group_feedbacks_feature_wise's
    # internal grouping model keys buckets by `module_id` and scopes stories by
    # `user_story_id`, so translate both here.
    feedback_items_for_grouping = [
        {
            **item,
            "module_id": item.get("mod_code"),
            "user_story_id": item.get("user_story_code"),
        }
        for item in feedback_items
    ]
    grouped_response = FeedbackGroupingService.group_feedbacks_feature_wise(
        {"project_id": project_id, "feedbacks": feedback_items_for_grouping}
    )
    feature_buckets = grouped_response.get("features", [])

    def _run_all() -> list[dict[str, Any]]:
        return [
            _run_one_feature_mfu_revise_bucket(
                project_id=project_id,
                bucket=bucket,
                module_metadata_by_code=module_metadata_by_code,
                source_paradigm=source_paradigm,
                workspace_dir=workspace_dir,
                skip_processing=skip_processing,
                request_id=request_id,
            )
            for bucket in feature_buckets
        ]

    return await asyncio.to_thread(_run_all)


def _update_feature_regeneration_ingestion_status(
    ingestion_id: str | None,
    status: str,
    *,
    mark_completed: bool = False,
    error: str | None = None,
) -> None:
    """Best-effort: update the per-request SourceIngestion row's lifecycle status.

    *error* (only meaningful when *status* is FAILED) is recorded onto the
    SourceIngestion's ``errors``.
    """
    fields: dict[str, Any] = {"status": status}
    if mark_completed:
        fields["completed_at"] = datetime.now(UTC)

    _update_source_ingestion_fields_by_id(ingestion_id=ingestion_id, fields=fields)
    if status == SourceIngestionStatus.FAILED.value:
        _add_source_ingestion_error_by_id(ingestion_id=ingestion_id, error=error or "")


def _persist_feature_regeneration_buckets(
    *,
    project_id: str,
    bucket_results: list[dict[str, Any]],
    source_ingestion_id: str | None = None,
) -> list[dict[str, Any]]:
    """Persist each successful revise-bucket result; collect one outcome per target.

    A failure in one MFU's bucket is recorded as a failed outcome rather than
    raised, so it doesn't discard results already earned by the other buckets
    in the same request.
    """
    bucket_outcomes: list[dict[str, Any]] = []
    for bucket_result in bucket_results:
        bucket_module_id = (bucket_result or {}).get("module_id")
        bucket_mfu_id = (bucket_result or {}).get("mfu_id")

        if not isinstance(bucket_result, dict) or bucket_result.get("status") != "success":
            error = (bucket_result or {}).get(
                "error", "Feature regeneration did not return a success result"
            )
            logger.error(
                "regenerate_feature_mfu_task: bucket failed project_id=%s module_id=%s mfu_id=%s error=%s",
                project_id,
                bucket_module_id,
                bucket_mfu_id,
                error,
            )
            bucket_outcomes.append(
                {
                    "module_id": bucket_module_id,
                    "mfu_id": bucket_mfu_id,
                    "status": SOURCE_INGESTION_STATUS_FAILED,
                    "error": error,
                }
            )
            continue

        persisted = _run_async(
            _persist_feature_regeneration_result_by_mfu(
                project_id=project_id,
                mod_code=bucket_module_id,
                mfu_id=bucket_mfu_id,
                features_stories=bucket_result.get("features_stories")
                or bucket_result.get("result"),
                source_ingestion_id=source_ingestion_id,
            )
        )
        bucket_outcomes.append(
            {
                "module_id": bucket_module_id,
                "mfu_id": bucket_mfu_id,
                "status": SOURCE_INGESTION_STATUS_FAILED
                if persisted.get("error")
                else SOURCE_INGESTION_STATUS_READY_FOR_REVIEW,
                "final_status": bucket_result.get("final_status"),
                "review_guidance": bucket_result.get("review_guidance"),
                **persisted,
            }
        )
    return bucket_outcomes


def _record_feature_regeneration_activity(
    *,
    project_id: str,
    task_db_id: str | None,
    target_dicts: list[dict[str, str]],
    bucket_outcomes: list[dict[str, Any]],
) -> None:
    """Log an activity-feed entry for a completed feature/MFU feedback regeneration."""
    record_activity(
        project_id=UUID(project_id),
        activity_type=ActivityType.SOURCE_CODE_FEEDBACK_REGENERATED,
        summary=SUMMARY_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATED,
        message=MSG_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATED.format(
            feature_count=len(target_dicts)
        ),
        actor_user_id=resolve_actor_from_task(task_db_id),
        data={"targets": target_dicts, "results": bucket_outcomes},
    )


def _notify_feature_regeneration_status(
    *,
    project_id: str,
    status: str,
    target_dicts: list[dict[str, str]] | None = None,
    error: str | None = None,
    error_reason: str | None = None,
) -> None:
    """Best-effort: notify the project owner and every assigned member about
    a feature/MFU feedback regeneration's start or outcome.

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

        total_targets = len(target_dicts or [])
        if status == SourceIngestionStatus.RUNNING.value:
            title = "Feature Regeneration from Feedback Started"
            message = (
                f"Feature regeneration from feedback has started for {total_targets} "
                f'feature(s) in "{project.name}".'
            )
            notification_type = NotificationType.INFO
        elif status == SourceIngestionStatus.READY_FOR_REVIEW.value:
            title = "Features Regenerated from Feedback"
            message = (
                f"{total_targets} feature(s) regenerated from feedback are ready "
                f'for review in "{project.name}".'
            )
            notification_type = NotificationType.SUCCESS
        elif status == SourceIngestionStatus.FAILED.value:
            title = "Feature Regeneration Failed"
            message = f'Feature regeneration from feedback failed for "{project.name}".'
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
                        "targets": target_dicts,
                        "error_reason": error_reason,
                    },
                )
            except Exception:
                logger.warning(
                    "_notify_feature_regeneration_status: failed to notify project_id=%s "
                    "user_id=%s",
                    project_id,
                    user_id,
                    exc_info=True,
                )
    except Exception:
        logger.warning(
            "_notify_feature_regeneration_status: failed for project_id=%s status=%s",
            project_id,
            status,
            exc_info=True,
        )


def _run_feature_mfu_regeneration_task(
    *,
    self,
    project_id: str,
    feedback_items: list[dict[str, Any]],
    module_metadata_by_code: dict[str, dict[str, Any]],
    source_paradigm: str,
    task_db_id: str | None = None,
    ingestion_id: str | None = None,
    skip_processing: bool = False,
) -> dict[str, Any]:
    from app.core.enums.source_ingestion_status import SourceIngestionStatus
    from app.core.llm_errors import NonRetryableLLMError
    from app.services.source_code_pipeline.src.ai.llm_client import (
        CircuitBreakerError,
        ConcurrentPipelineError,
        CreditBalanceExhaustedError,
        PipelineRunCancelled,
    )
    from app.workers._task_helpers import describe_non_retryable_llm_error

    stage_prefix = "feature_regeneration"
    task_type = "feature_regeneration"
    targets = sorted({(item["mod_code"], item["mfu_id"]) for item in feedback_items})
    target_dicts = [{"module_id": mod_code, "mfu_id": mfu_id} for mod_code, mfu_id in targets]
    event_meta = {"targets": target_dicts}

    if mark_cancelled_and_check(
        request_id=task_db_id,
        task_db_id=task_db_id,
        project_id=project_id,
        task_type=task_type,
        stage=f"{stage_prefix}.cancelled",
    ):
        _update_feature_regeneration_ingestion_status(
            ingestion_id, SourceIngestionStatus.CANCELLED.value
        )
        record_activity(
            project_id=UUID(project_id),
            activity_type=ActivityType.SOURCE_CODE_FEEDBACK_REGENERATION_CANCELLED,
            summary=SUMMARY_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATION_CANCELLED,
            message=MSG_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATION_CANCELLED.format(
                feature_count=len(target_dicts)
            ),
            actor_user_id=resolve_actor_from_task(task_db_id),
            data={"targets": target_dicts, "ingestion_id": ingestion_id},
        )
        return {"project_id": project_id, "status": "cancelled"}

    emit_task_event(
        task_db_id=task_db_id,
        project_id=project_id,
        task_type=task_type,
        status=SOURCE_INGESTION_STATUS_RUNNING,
        stage=f"{stage_prefix}.started",
        progress=10,
        meta=event_meta,
    )
    _notify_feature_regeneration_status(
        project_id=project_id,
        status=SourceIngestionStatus.RUNNING.value,
        target_dicts=target_dicts,
    )
    record_activity(
        project_id=UUID(project_id),
        activity_type=ActivityType.SOURCE_CODE_FEEDBACK_REGENERATION_STARTED,
        summary=SUMMARY_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATION_STARTED,
        message=MSG_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATION_STARTED.format(
            feature_count=len(target_dicts)
        ),
        actor_user_id=resolve_actor_from_task(task_db_id),
        data={"targets": target_dicts, "ingestion_id": ingestion_id},
    )

    project_root = Path(__file__).resolve().parents[2]
    workspace_dir = (
        project_root
        / "temp"
        / "source_codes"
        / f"{project_id}_regenerate"
        / (task_db_id or self.request.id)
    )

    try:
        workspace_dir.mkdir(parents=True, exist_ok=True)
        bucket_results = _run_async(
            _async_run_feature_mfu_regeneration(
                project_id=project_id,
                feedback_items=feedback_items,
                module_metadata_by_code=module_metadata_by_code,
                source_paradigm=source_paradigm,
                workspace_dir=workspace_dir,
                skip_processing=skip_processing,
                # A regeneration never sets an explicit request_id, so its
                # ProjectTask row stores its own id as the request_id and that
                # is the key the cancel flag lands under (see
                # _handle_source_code_cancellation's docstring).
                request_id=task_db_id,
            )
        )

        if not bucket_results:
            error = "feedback_items resolved to no module/mfu buckets"
            emit_task_event(
                task_db_id=task_db_id,
                project_id=project_id,
                task_type=task_type,
                status=SOURCE_INGESTION_STATUS_FAILED,
                stage=f"{stage_prefix}.failed",
                progress=100,
                error=error,
                meta=event_meta,
            )
            _update_feature_regeneration_ingestion_status(
                ingestion_id, SourceIngestionStatus.FAILED.value, error=error
            )
            _notify_feature_regeneration_status(
                project_id=project_id,
                status=SourceIngestionStatus.FAILED.value,
                target_dicts=target_dicts,
                error=error,
            )
            record_activity(
                project_id=UUID(project_id),
                activity_type=ActivityType.SOURCE_CODE_FEEDBACK_REGENERATION_FAILED,
                summary=SUMMARY_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATION_FAILED,
                message=MSG_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATION_FAILED.format(error=error),
                actor_user_id=resolve_actor_from_task(task_db_id),
                data={"targets": target_dicts, "ingestion_id": ingestion_id, "error": error},
            )
            return {
                "project_id": project_id,
                "status": SOURCE_INGESTION_STATUS_FAILED,
                "error": error,
            }

        # Buckets already ran sequentially inside _async_run_feature_mfu_regeneration.
        bucket_outcomes = _persist_feature_regeneration_buckets(
            project_id=project_id,
            bucket_results=bucket_results,
            source_ingestion_id=ingestion_id,
        )

        any_succeeded = any(
            outcome["status"] == SOURCE_INGESTION_STATUS_READY_FOR_REVIEW
            for outcome in bucket_outcomes
        )
        # A succeeded regeneration still has its feedback_change_type-flagged
        # Feature/UserStory nodes pending human accept/reject, so the
        # ingestion goes to ready_for_review (mirroring
        # _run_story_feedback_patch_task) rather than completed — it only
        # reaches completed once try_complete_open_feedback_or_incremental_ingestions
        # sees every flagged node resolved.
        overall_status = (
            SOURCE_INGESTION_STATUS_READY_FOR_REVIEW
            if any_succeeded
            else SOURCE_INGESTION_STATUS_FAILED
        )

        bucket_error = (
            None
            if any_succeeded
            else "; ".join(
                f"{o.get('module_id')}/{o.get('mfu_id')}: {o.get('error')}"
                for o in bucket_outcomes
                if o.get("status") == SOURCE_INGESTION_STATUS_FAILED
            )
        )

        emit_task_event(
            task_db_id=task_db_id,
            project_id=project_id,
            task_type=task_type,
            status=overall_status,
            stage=f"{stage_prefix}.completed" if any_succeeded else f"{stage_prefix}.failed",
            progress=100,
            meta={**event_meta, "results": bucket_outcomes},
        )
        _update_feature_regeneration_ingestion_status(
            ingestion_id,
            SourceIngestionStatus.READY_FOR_REVIEW.value
            if any_succeeded
            else SourceIngestionStatus.FAILED.value,
            mark_completed=any_succeeded,
            error=bucket_error,
        )
        if any_succeeded:
            _add_run_stage_by_id(
                ingestion_id=ingestion_id, stage=SourceIngestionStage.READY_FOR_REVIEW
            )
            _record_feature_regeneration_activity(
                project_id=project_id,
                task_db_id=task_db_id,
                target_dicts=target_dicts,
                bucket_outcomes=bucket_outcomes,
            )
            _notify_feature_regeneration_status(
                project_id=project_id,
                status=SourceIngestionStatus.READY_FOR_REVIEW.value,
                target_dicts=target_dicts,
            )
        else:
            _notify_feature_regeneration_status(
                project_id=project_id,
                status=SourceIngestionStatus.FAILED.value,
                target_dicts=target_dicts,
                error=bucket_error,
            )
            record_activity(
                project_id=UUID(project_id),
                activity_type=ActivityType.SOURCE_CODE_FEEDBACK_REGENERATION_FAILED,
                summary=SUMMARY_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATION_FAILED,
                message=MSG_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATION_FAILED.format(
                    error=(bucket_error or "")[:200]
                ),
                actor_user_id=resolve_actor_from_task(task_db_id),
                data={"targets": target_dicts, "ingestion_id": ingestion_id, "error": bucket_error},
            )
        return {
            "project_id": project_id,
            "status": overall_status,
            "results": bucket_outcomes,
        }

    except PipelineRunCancelled:
        # The LLM client refuses to issue a call on a cancelled run and raises
        # this. It is a BaseException, so it would otherwise sail past the
        # `except Exception` below and leave the ProjectTask/SourceIngestion
        # rows stuck on "running" with no terminal event ever emitted.
        logger.info(
            "regenerate_feature_mfu_task: cancelled for project_id=%s targets=%s "
            "— discarding partial output.",
            project_id,
            target_dicts,
        )
        emit_task_event(
            task_db_id=task_db_id,
            project_id=project_id,
            task_type=task_type,
            status=TASK_STATUS_CANCELLED,
            stage=f"{stage_prefix}.cancelled",
            progress=100,
            meta=event_meta,
        )
        _update_feature_regeneration_ingestion_status(
            ingestion_id, SourceIngestionStatus.CANCELLED.value
        )
        return {"project_id": project_id, "status": TASK_STATUS_CANCELLED}

    except SoftTimeLimitExceeded:
        logger.error(
            "regenerate_feature_mfu_task: SoftTimeLimitExceeded for project_id=%s targets=%s",
            project_id,
            target_dicts,
        )
        emit_task_event(
            task_db_id=task_db_id,
            project_id=project_id,
            task_type=task_type,
            status=SOURCE_INGESTION_STATUS_FAILED,
            stage=f"{stage_prefix}.failed",
            progress=100,
            error=MSG_FEATURE_REGENERATION_TIME_LIMIT_EXCEEDED,
            meta=event_meta,
        )
        _update_feature_regeneration_ingestion_status(
            ingestion_id,
            SourceIngestionStatus.FAILED.value,
            error=MSG_FEATURE_REGENERATION_TIME_LIMIT_EXCEEDED,
        )
        _notify_feature_regeneration_status(
            project_id=project_id,
            status=SourceIngestionStatus.FAILED.value,
            target_dicts=target_dicts,
            error=MSG_FEATURE_REGENERATION_TIME_LIMIT_EXCEEDED,
        )
        record_activity(
            project_id=UUID(project_id),
            activity_type=ActivityType.SOURCE_CODE_FEEDBACK_REGENERATION_FAILED,
            summary=SUMMARY_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATION_FAILED,
            message=MSG_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATION_FAILED.format(
                error=MSG_FEATURE_REGENERATION_TIME_LIMIT_EXCEEDED
            ),
            actor_user_id=resolve_actor_from_task(task_db_id),
            data={
                "targets": target_dicts,
                "ingestion_id": ingestion_id,
                "error": MSG_FEATURE_REGENERATION_TIME_LIMIT_EXCEEDED,
            },
        )
        return {
            "project_id": project_id,
            "status": SOURCE_INGESTION_STATUS_FAILED,
            "error": MSG_FEATURE_REGENERATION_TIME_LIMIT_EXCEEDED,
        }

    except (ConcurrentPipelineError, CircuitBreakerError) as exc:
        # LLMClient.pipeline_run_guard's process-wide single-flight lock
        # (app/services/source_code_pipeline) rejected this run because
        # another pipeline currently holds it in this worker process. Fail
        # immediately rather than falling into the except Exception branch
        # below's Celery retry — no Celery/Redis-level retry layer is added
        # on top of the pipeline's own retry handling.
        #
        # CircuitBreakerError is grouped in here (not a separate branch)
        # because it needs the exact same "fail immediately, no retry"
        # treatment — but it's deliberately a BaseException (see
        # llm_client.py's class docstring), so unlike ConcurrentPipelineError
        # it would NOT be caught by `except Exception` below at all: left
        # unhandled it escapes this task entirely uncaught, which under a
        # real -P prefork worker crashes the child process outright (never
        # resolves to FAILURE, just hangs at PENDING) rather than recording
        # a normal failure — see
        # docs/CircuitBreakerError_Handling_Issue_Implications.docx.
        logger.error(
            "regenerate_feature_mfu_task: %s for project_id=%s targets=%s: %s",
            "circuit breaker tripped"
            if isinstance(exc, CircuitBreakerError)
            else "pipeline single-flight guard rejected the run",
            project_id,
            target_dicts,
            exc,
        )
        emit_task_event(
            task_db_id=task_db_id,
            project_id=project_id,
            task_type=task_type,
            status=SOURCE_INGESTION_STATUS_FAILED,
            stage=f"{stage_prefix}.failed",
            progress=100,
            error=str(exc),
            meta=event_meta,
        )
        error_reason = "circuit_breaker" if isinstance(exc, CircuitBreakerError) else None
        _update_feature_regeneration_ingestion_status(
            ingestion_id, SourceIngestionStatus.FAILED.value, error=str(exc)
        )
        _notify_feature_regeneration_status(
            project_id=project_id,
            status=SourceIngestionStatus.FAILED.value,
            target_dicts=target_dicts,
            error=str(exc),
            error_reason=error_reason,
        )
        record_activity(
            project_id=UUID(project_id),
            activity_type=ActivityType.SOURCE_CODE_FEEDBACK_REGENERATION_FAILED,
            summary=SUMMARY_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATION_FAILED,
            message=MSG_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATION_FAILED.format(
                error=str(exc)[:200]
            ),
            actor_user_id=resolve_actor_from_task(task_db_id),
            data={
                "targets": target_dicts,
                "ingestion_id": ingestion_id,
                "error": str(exc),
                "llm_error_reason": error_reason,
            },
        )
        return {
            "project_id": project_id,
            "status": SOURCE_INGESTION_STATUS_FAILED,
            "error": str(exc),
        }

    except (CreditBalanceExhaustedError, NonRetryableLLMError) as exc:
        # CONFIRMED GAP FIX: unlike every other source-code-pipeline task,
        # this one previously had NO handling for CreditBalanceExhaustedError
        # at all — it's a BaseException (see llm_client.py's class
        # docstring), so it was not caught by the `except Exception` below,
        # and would escape this task entirely uncaught. Under this
        # deployment's real -P prefork pool that crashes the worker child
        # outright (never resolves to FAILURE, just hangs at PENDING)
        # instead of recording a normal failure — see
        # docs/CircuitBreakerError_Handling_Issue_Implications.docx for the
        # same mechanism already fixed for CircuitBreakerError above.
        #
        # NonRetryableLLMError covers every other non-retryable reason
        # (authentication, invalid model, invalid request,
        # context-length-exceeded, content policy, permission denied) —
        # same "abort the whole request immediately, no retry" contract:
        # the bucket loop in _async_run_feature_mfu_regeneration is
        # sequential (confirmed), so this exception aborts it immediately;
        # buckets already computed in this pass are not persisted (only
        # persisted after every bucket in the request has been attempted),
        # matching "stop immediately, don't continue."
        classification = getattr(exc, "classification", None)
        error_detail = describe_non_retryable_llm_error(classification) if classification else str(exc)
        error_reason = classification.reason.value if classification else "credit_exhausted"
        logger.error(
            "regenerate_feature_mfu_task: non-retryable LLM error (reason=%s) for "
            "project_id=%s targets=%s: %s",
            error_reason,
            project_id,
            target_dicts,
            error_detail,
        )
        emit_task_event(
            task_db_id=task_db_id,
            project_id=project_id,
            task_type=task_type,
            status=SOURCE_INGESTION_STATUS_FAILED,
            stage=f"{stage_prefix}.failed",
            progress=100,
            error=error_detail,
            meta=event_meta,
        )
        _update_feature_regeneration_ingestion_status(
            ingestion_id, SourceIngestionStatus.FAILED.value, error=error_detail
        )
        _notify_feature_regeneration_status(
            project_id=project_id,
            status=SourceIngestionStatus.FAILED.value,
            target_dicts=target_dicts,
            error=error_detail,
            error_reason=error_reason,
        )
        record_activity(
            project_id=UUID(project_id),
            activity_type=ActivityType.SOURCE_CODE_FEEDBACK_REGENERATION_FAILED,
            summary=SUMMARY_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATION_FAILED,
            message=MSG_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATION_FAILED.format(
                error=error_detail[:200]
            ),
            actor_user_id=resolve_actor_from_task(task_db_id),
            data={
                "targets": target_dicts,
                "ingestion_id": ingestion_id,
                "error": error_detail,
                "llm_error_reason": error_reason,
            },
        )
        return {
            "project_id": project_id,
            "status": SOURCE_INGESTION_STATUS_FAILED,
            "error": error_detail,
        }

    except Exception as exc:
        # No Celery-level retry, same reasoning as the ConcurrentPipelineError
        # branch above and _handle_source_code_task_exception elsewhere in
        # this file: app/services/source_code_pipeline already retries LLM
        # calls internally, so a Celery retry on top would only redo
        # already-completed work to hit the same already-exhausted failure
        # again. This used to retry once (60s backoff) before failing —
        # inconsistent with every other source-code-pipeline task, which
        # fails immediately.
        logger.exception(
            "regenerate_feature_mfu_task failed: project_id=%s targets=%s error=%s",
            project_id,
            target_dicts,
            exc,
        )
        emit_task_event(
            task_db_id=task_db_id,
            project_id=project_id,
            task_type=task_type,
            status=SOURCE_INGESTION_STATUS_FAILED,
            stage=f"{stage_prefix}.failed",
            progress=100,
            error=str(exc),
            meta=event_meta,
        )
        _update_feature_regeneration_ingestion_status(
            ingestion_id, SourceIngestionStatus.FAILED.value, error=str(exc)
        )
        _notify_feature_regeneration_status(
            project_id=project_id,
            status=SourceIngestionStatus.FAILED.value,
            target_dicts=target_dicts,
            error=str(exc),
        )
        record_activity(
            project_id=UUID(project_id),
            activity_type=ActivityType.SOURCE_CODE_FEEDBACK_REGENERATION_FAILED,
            summary=SUMMARY_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATION_FAILED,
            message=MSG_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATION_FAILED.format(
                error=str(exc)[:200]
            ),
            actor_user_id=resolve_actor_from_task(task_db_id),
            data={"targets": target_dicts, "ingestion_id": ingestion_id, "error": str(exc)},
        )
        return {
            "project_id": project_id,
            "status": SOURCE_INGESTION_STATUS_FAILED,
            "error": str(exc),
        }

    finally:
        try:
            if workspace_dir.exists():
                shutil.rmtree(workspace_dir)
        except Exception:
            logger.warning(
                "Failed to clean up feature-regeneration workspace: %s",
                workspace_dir,
                exc_info=True,
            )


@celery_app.task(
    bind=True,
    name="tasks.parse_code.regenerate_feature_mfu",
    acks_late=True,
    soft_time_limit=TASK_AI_SOFT_TIME_LIMIT,
    time_limit=TASK_AI_TIME_LIMIT,
)
def regenerate_feature_mfu_task(
    self,
    project_id: str,
    feedback_items: list[dict[str, Any]],
    module_metadata_by_code: dict[str, dict[str, Any]],
    source_paradigm: str,
    task_db_id: str | None = None,
    ingestion_id: str | None = None,
    skip_processing: bool = False,
) -> dict[str, Any]:
    """Regenerate one or more source-code-derived features' MFUs via Stage 5, guided by human feedback.

    `feedback_items` is a list of ``{mod_code, mfu_id, user_story_code,
    overall_feedback, specific_feedback}`` dicts, grouped by ``(mod_code,
    mfu_id)`` into structured ``feedback_spec`` payloads — one per group.
    `module_metadata_by_code` maps each referenced ``mod_code`` to its stored
    ``{module_response, module_manifest}`` (used to rebuild that MFU's spec
    files for `PipelineOrchestrator.process_mfu_revise_with_reconstruction`
    against an ephemeral, task-scoped workspace). Each group's target Feature
    (and its user stories) is resolved and updated in Neo4j from its
    ``module_id``/``mfu_id`` once regeneration succeeds. `ingestion_id` is the
    per-request `SourceIngestion` row created for this regeneration — flipped
    from `running` to `completed`/`failed` once this task finishes.
    `skip_processing=True` bypasses the LLM call and loads a canned sample
    result — for local testing only.
    """
    return _run_feature_mfu_regeneration_task(
        self=self,
        project_id=project_id,
        feedback_items=feedback_items,
        module_metadata_by_code=module_metadata_by_code,
        source_paradigm=source_paradigm,
        task_db_id=task_db_id,
        ingestion_id=ingestion_id,
        skip_processing=skip_processing,
    )
