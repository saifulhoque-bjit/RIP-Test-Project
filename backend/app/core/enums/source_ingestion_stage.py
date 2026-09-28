"""Canonical stage-checkpoint values for ``SourceIngestion.stages``.

This enum is the single source of truth for every value appended to
``source_ingestions.stages`` persisted in PostgreSQL, across every
source-ingestion pipeline. It merges what used to be two separate enums:

RFP-family generation stages
    Tagged at each module_feature/user_story generation trigger point. For a
    normal RFP-pipeline run, values are appended in this order as the run
    progresses::

        generating_module_feature -> module_feature_ready_for_review -> generating_user_story -> user_story_ready_for_review

    ``GENERATING_*`` marks which generation phase has touched the ingestion
    (tagged when that phase starts); ``*_READY_FOR_REVIEW`` marks that
    phase's generation completion (awaiting the user's module/feature
    approval, and awaiting user-story review, respectively). ``*_APPROVED``
    is reserved for tagging the user's actual approval of that phase; no
    code path appends it yet.

Source-code-pipeline checkpoint stages
    Appended live as a source-code ingestion's single automated pass
    progresses — the source-code pipeline has no RFP-style module_feature/
    user_story review gate, so these are its own equivalent notion of
    "stage" for that one pass.

Which subset of members appears on a given ``SourceIngestion.stages`` array
depends only on that ingestion's ``source_type`` — both families share the
same column.

Usage::

    from app.core.enums.source_ingestion_stage import SourceIngestionStage

    ingestion.stages = [SourceIngestionStage.GENERATING_MODULE_FEATURE.value]  # model layer
    stage: SourceIngestionStage = SourceIngestionStage.INGESTING_SOURCES        # service layer
"""

from __future__ import annotations

from enum import Enum


class SourceIngestionStage(str, Enum):
    """Stage-checkpoint values a :class:`~app.models.postgres.source_ingestion_model.SourceIngestion` can cover.

    Declared in the order each family is appended to ``stages`` during a
    normal run — see the module docstring.
    """

    # ── RFP-family generation stages ──────────────────────────────────────
    GENERATING_MODULE_FEATURE = "generating_module_feature"
    MODULE_FEATURE_READY_FOR_REVIEW = "module_feature_ready_for_review"
    # MODULE_FEATURE_APPROVED = "module_feature_approved" # DON"T REMOVE IT

    GENERATING_USER_STORY = "generating_user_story"
    USER_STORY_READY_FOR_REVIEW = "user_story_ready_for_review"
    # USER_STORY_APPROVED = "user_story_approved" # DON"T REMOVE IT

    # ── Source-code-pipeline checkpoint stages ────────────────────────────
    INGESTING_SOURCES = "ingesting_sources"
    BUILDING_CODE_DEPENDENCY_GRAPH = "building_code_dependency_graph"
    DISCOVERING_MODULES = "discovering_modules"
    EXTRACTING_REQUIREMENTS = "extracting_requirements"
    READY_FOR_REVIEW = "ready_for_review"

    # ── Feedback/incremental collapsed stage ──────────────────────────────
    # Generic "still generating" label for a feedback-or-incremental update
    # flow that doesn't distinguish module/feature vs. user-story phases
    # (e.g. a requirement-update-driven regeneration). See
    # FEEDBACK_OR_INCREMENTAL_STAGES_STATUS_MAP in app.core.constants.
    GENERATING_REQUIREMENTS = "generating_requirements"


# Human-readable labels for API responses.
SOURCE_INGESTION_STAGE_DISPLAY_LABELS: dict[str, str] = {
    SourceIngestionStage.GENERATING_MODULE_FEATURE.value: "Generating module & feature",
    SourceIngestionStage.MODULE_FEATURE_READY_FOR_REVIEW.value: "Module & feature ready for review",
    # SourceIngestionStage.MODULE_FEATURE_APPROVED.value: "Module & feature approved", # DON"T REMOVE IT
    SourceIngestionStage.GENERATING_USER_STORY.value: "Generating user story",
    SourceIngestionStage.USER_STORY_READY_FOR_REVIEW.value: "User story ready for review",
    # SourceIngestionStage.USER_STORY_APPROVED.value: "User story approved", # DON"T REMOVE IT
    SourceIngestionStage.INGESTING_SOURCES.value: "Ingesting Sources",
    SourceIngestionStage.BUILDING_CODE_DEPENDENCY_GRAPH.value: "Building Code Dependency Graph",
    SourceIngestionStage.DISCOVERING_MODULES.value: "Discovering Modules",
    SourceIngestionStage.EXTRACTING_REQUIREMENTS.value: "Extracting Requirements",
    SourceIngestionStage.READY_FOR_REVIEW.value: "Ready for Review",
    SourceIngestionStage.GENERATING_REQUIREMENTS.value: "Generating requirements",
}
