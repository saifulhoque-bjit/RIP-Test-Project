"""Project-level AI workflow orchestration tasks.

Implements a phase-driven pipeline for bulk uploads:

Phase 1: Parse all sources -> create fragments -> generate module/features.
Phase 2: Apply human feedback to module/features.
Phase 3: Generate user story backlog.
Phase 4: Regenerate backlog with feedback.
"""

from __future__ import annotations

from typing import Any

from app.core.celery_app import celery_app
from app.core.constants import (
    TASK_AI_SOFT_TIME_LIMIT,
    TASK_AI_TIME_LIMIT,
    TASK_MAX_RETRIES,
    TASK_PARSING_SOFT_TIME_LIMIT,
    TASK_PARSING_TIME_LIMIT,
)
from app.workers.document_task_stages import (
    _run_module_feature_regeneration_task,
    _run_parse_document_task,
    _run_source_module_feature_generation_task,
    _run_story_feedback_patch_task,
    _run_user_story_backlog_task,
)


@celery_app.task(
    bind=True,
    name="tasks.parse_document",
    max_retries=TASK_MAX_RETRIES,
    acks_late=True,
    soft_time_limit=TASK_PARSING_SOFT_TIME_LIMIT,
    time_limit=TASK_PARSING_TIME_LIMIT,
)
def parse_document_task(
    self,
    project_id: str,
    source_ids: list[str],
    task_db_id: str | None = None,
    skip_processing: bool = False,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Phase 1a: Parse uploaded sources into Fragment records.

    For each source: download from S3, extract content, persist Fragment rows.
    Successfully-parsed source IDs are forwarded to generate_modules_and_features_task
    on the source_ai queue so the two workloads can be scaled and retried
    independently.
    """
    return _run_parse_document_task(
        project_id=project_id,
        source_ids=source_ids,
        task_db_id=task_db_id,
        dispatch_generate_task=lambda pid,
        psource_ids,
        ptask_db_id,
        pskip_processing: generate_modules_and_features_task.apply_async(
            args=[pid, psource_ids, ptask_db_id, pskip_processing, request_id],
        ),
        skip_processing=skip_processing,
        request_id=request_id,
    )


@celery_app.task(
    bind=True,
    name="tasks.modules_and_features.generate_modules_and_features",
    max_retries=TASK_MAX_RETRIES,
    acks_late=True,
    soft_time_limit=TASK_PARSING_SOFT_TIME_LIMIT,
    time_limit=TASK_PARSING_TIME_LIMIT,
)
def generate_modules_and_features_task(
    self,
    project_id: str,
    source_ids: list[str],
    task_db_id: str | None = None,
    skip_processing: bool = False,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Phase 1b: Generate module/feature nodes from parsed fragments via AI.

    Dispatched by parse_document_task with the IDs of successfully-parsed
    sources.  Collects their Fragment records, invokes the AI pipeline, and
    persists the ModuleFeature graph in Neo4j.  Retries with exponential
    backoff; sources are only marked failed after all retries are exhausted.
    """
    return _run_source_module_feature_generation_task(
        self=self,
        project_id=project_id,
        source_ids=source_ids,
        task_db_id=task_db_id,
        skip_processing=skip_processing,
        request_id=request_id,
    )


@celery_app.task(
    bind=True,
    name="tasks.modules_and_features.regenerate_modules_and_features",
    max_retries=TASK_MAX_RETRIES,
    acks_late=True,
    soft_time_limit=TASK_AI_SOFT_TIME_LIMIT,
    time_limit=TASK_AI_TIME_LIMIT,
)
def regenerate_modules_and_features_task(
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
    """Phase 2: refine module/features with human feedback."""
    return _run_module_feature_regeneration_task(
        self=self,
        project_id=project_id,
        source_ids=source_ids,
        fragments=fragments,
        modules_and_features=modules_and_features,
        feedback=feedback,
        task_db_id=task_db_id,
        skip_processing=skip_processing,
        ingestion_id=ingestion_id,
    )


@celery_app.task(
    bind=True,
    name="tasks.user_stories.generate_user_story",
    max_retries=TASK_MAX_RETRIES,
    acks_late=True,
    soft_time_limit=TASK_AI_SOFT_TIME_LIMIT,
    time_limit=TASK_AI_TIME_LIMIT,
)
def generate_user_story_task(
    self,
    project_id: str,
    fragments: list[dict[str, Any]],
    modules_and_features: str | dict[str, Any],
    task_db_id: str | None = None,
    skip_processing: bool = False,
    source_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Phase 3: Initial user story generation from approved modules/features.

    Called when modules/features are first approved.  No existing user stories
    or user feedback — this is always a clean-slate generation run.

    `source_ids` identifies the originating upload's sources so the ingestion
    bookkeeping updates the *source-linked* SourceIngestion row — the same one
    the ``user_story`` stage was tagged onto — instead of whichever row happens
    to be newest (which, after a feedback ``modules/regenerate``, is the
    dedicated regeneration run and must not be touched here).
    """
    return _run_user_story_backlog_task(
        self=self,
        project_id=project_id,
        fragments=fragments,
        modules_and_features=modules_and_features,
        user_stories="",
        feedback="",
        stage_prefix="user_story.generation",
        task_type="story_generation",
        task_db_id=task_db_id,
        skip_processing=skip_processing,
        source_ids=source_ids,
    )


@celery_app.task(
    bind=True,
    name="tasks.user_stories.regenerate_user_story",
    max_retries=TASK_MAX_RETRIES,
    acks_late=True,
    soft_time_limit=TASK_AI_SOFT_TIME_LIMIT,
    time_limit=TASK_AI_TIME_LIMIT,
)
def regenerate_user_story_task(
    self,
    project_id: str,
    fragments: list[dict[str, Any]],
    modules_and_features: str | dict[str, Any],
    user_stories: str | list[dict[str, Any]],
    feedback: str,
    task_db_id: str | None = None,
    skip_processing: bool = False,
    ingestion_id: str | None = None,
) -> dict[str, Any]:
    """Phase 4: User story regeneration driven by explicit user feedback.

    User story IDs are deterministic (uuid5 of project_id + user_story_code),
    so the repository MERGE updates existing nodes in place. User stories are
    not deleted during regeneration: codes omitted by AI remain unchanged and
    newly returned codes produce new nodes.
    """
    return _run_user_story_backlog_task(
        self=self,
        project_id=project_id,
        fragments=fragments,
        modules_and_features=modules_and_features,
        user_stories=user_stories,
        feedback=feedback,
        stage_prefix="user_story.regeneration",
        task_type="story_regeneration",
        task_db_id=task_db_id,
        skip_processing=skip_processing,
        ingestion_id=ingestion_id,
    )


@celery_app.task(
    bind=True,
    name="tasks.user_stories.regenerate_by_feedback",
    max_retries=TASK_MAX_RETRIES,
    acks_late=True,
    soft_time_limit=TASK_AI_SOFT_TIME_LIMIT,
    time_limit=TASK_AI_TIME_LIMIT,
)
def regenerate_user_stories_by_feedback_task(
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
    """Targeted patch regeneration: apply per-story feedback via run_agile_backlog_patch."""
    return _run_story_feedback_patch_task(
        self=self,
        project_id=project_id,
        story_feedbacks=story_feedbacks,
        feature_contexts=feature_contexts,
        persona_glossary=persona_glossary,
        valid_sources=valid_sources,
        story_code_to_feature_id=story_code_to_feature_id,
        options=options,
        task_db_id=task_db_id,
        ingestion_id=ingestion_id,
        skip_processing=skip_processing,
    )
