"""SQLAlchemy ORM model for SourceIngestion.

A ``SourceIngestion`` groups the Source rows uploaded together in a single
batch (mirroring ``sources.batch_id``) and tracks generation rollups for
that batch — how many modules/features/user stories exist for it, and when
the generating_module_feature / generating_user_story stages started and completed.

It also carries the batch-level upload metadata (description, source
language, technology-stack fields, incremental-pipeline flags) that used to
be duplicated onto every ``Source`` row in the batch — since these describe
the whole ingestion, not an individual file, they live here once instead.

Column reference
────────────────
id                        — PK (UUID)
project_id                — FK → projects.id (CASCADE); every ingestion belongs to a project
run_code                  — human-readable sequential code, e.g. "RUN-1001", generated
                            from the project_run_code_seq sequence
source_type               — rfp | additional_rfp | source_code | meeting_notes | requirement_update
stages                    — SourceIngestionStage values recording which of module_feature/
                            user_story generation (or, for source_code, which pipeline
                            checkpoint) has touched this ingestion so far; appended to,
                            never overwritten, each time a stage transition occurs
status                    — running | ready_for_review | completed | failed (default "running");
                            which generation phase completed is distinguished via ``stages``
                            (SourceIngestionStage), not a distinct status value
description               — optional free-text note from the uploader
source_language           — primary source language (e.g. Power Builder, COBOL, Java) (nullable)
frontend_stack            — target frontend technology stack (nullable)
backend_stack             — target backend technology stack (nullable)
infrastructure_stack      — target infrastructure / cloud platform (nullable)
architecture_stack        — target architecture pattern (nullable)
database_stack            — target database technology (nullable)
coding_standard           — coding standard / style guide to apply (nullable)
database_strategy         — database access / replication strategy (nullable)
architecture              — architecture approach / guidance (nullable)
security                  — security mechanism (nullable)
is_incremental            — True when this ingestion was uploaded via the incremental pipeline (default False)
user_message              — optional user-supplied context message for the incremental pipeline (nullable)
skip_processing           — skip full AI pipeline processing and use sample/cached artifacts (default False)
source_layout_type        — modular (Modular) | flat (Non Modular) | auto (Unknown);
                            source-code layout shape — see
                            app.core.enums.source_layout_type.SourceLayoutType.
                            NULL when source_type isn't "source_code" (nullable, no default)
entity_json               — optional JSONB bag for feedback-driven-regeneration context on
                            ingestions not tied to an uploaded Source, e.g.
                            {"feedback": str, "module_ids": [...], "feature_ids": [...]} (nullable)

Field applicability by source_type (enforced in SourceIngestionRepository.create_ingestion,
not at the DB level):
  source_code                              — source_language, the technology-stack fields,
                                              and source_layout_type apply.
  anything except rfp and source_code      — is_incremental and user_message apply.
  rfp                                      — none of the above apply.
description and skip_processing apply regardless of source_type.
tot_modules_from_global_artifact — count of modules detected during global-artifact
                            discovery (source-code pipeline only), before per-module
                            processing runs and before any module can fail; populated
                            from ``len(global_artifacts["filtered_modules"])`` (default 0)
tot_modules               — count of modules generated (or, for an incremental-update batch,
                            added) for this ingestion (default 0)
tot_features              — count of features generated/added for this ingestion (default 0)
tot_user_stories          — count of user stories generated/added for this ingestion (default 0)
tot_modules_failed        — count of modules that failed processing (source-code pipeline
                            only); updated once per ingestion as each module finishes
                            (default 0)
tot_modules_updated       — count of modules updated for this ingestion (default 0)
tot_features_updated      — count of features updated for this ingestion (default 0)
tot_user_stories_updated  — count of user stories updated for this ingestion (default 0)
tot_modules_deleted       — count of modules deleted for this ingestion (default 0)
tot_features_deleted      — count of features deleted for this ingestion (default 0)
tot_user_stories_deleted  — count of user stories deleted for this ingestion (default 0)
Updated/deleted counts are produced by any pipeline that mutates an existing
backlog for this ingestion — the incremental-update pipeline and
feedback-driven regeneration alike — not the incremental pipeline only.
tot_modules_accepted      — count of pending changes accepted for modules tagged by this
                            ingestion, via incremental-update or feedback-driven review
                            (default 0)
tot_modules_rejected      — count of pending changes rejected for modules tagged by this
                            ingestion (default 0)
tot_features_accepted     — count of pending changes accepted for features tagged by this
                            ingestion (default 0)
tot_features_rejected     — count of pending changes rejected for features tagged by this
                            ingestion (default 0)
tot_user_stories_accepted — count of pending changes accepted for user stories tagged by
                            this ingestion (default 0)
tot_user_stories_rejected — count of pending changes rejected for user stories tagged by
                            this ingestion (default 0)
These six counters replace per-decision ActivityLog entries: every
accept/reject call bumps one counter instead of writing a feed row, so a
review batch's running total lives on the ingestion instead of flooding the
activity feed.
no_changes_explanation    — set only by the incremental-update pipeline when it proposed
                            no changes at all; the AI's plain-language explanation of why
                            (e.g. the submitted notes didn't warrant any backlog change).
                            NULL on any run that produced changes, and on non-incremental
                            ingestions (nullable).
errors                    — every error message recorded for this ingestion's pipeline
                            run(s); appended to (never overwritten) each time a stage
                            transitions to "failed", mirroring the append-only `stages`
                            array. Empty for an ingestion that never failed (default
                            empty list).
mod_fea_gen_started_at    — module/feature generation start timestamp (nullable)
mod_fea_gen_completed_at  — module/feature generation completion timestamp (nullable)
user_story_gen_started_at — user story generation start timestamp (nullable)
user_story_gen_completed_at — user story generation completion timestamp (nullable)
started_at                — start timestamp for this ingestion's pipeline run — incremental
                            update or feedback-driven regeneration alike (nullable)
completed_at               — completion timestamp for this ingestion's pipeline run (nullable)
created_at                — auto timestamp (UTC)
updated_at                — auto-updated timestamp (UTC)
deleted_at                — soft-delete timestamp (UTC); NULL means not deleted
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums.source_ingestion_status import SourceIngestionStatus
from app.core.enums.source_type import SourceType
from app.db.base import Base

if TYPE_CHECKING:
    from app.models.postgres.project_model import Project


class SourceIngestion(Base):
    __tablename__ = "source_ingestions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    run_code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)

    source_type: Mapped[str] = mapped_column(
        SAEnum(
            SourceType,
            name="source_type_enum",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
            create_type=False,
        ),
        nullable=False,
    )

    stages: Mapped[list[str]] = mapped_column(
        ARRAY(String(40)), nullable=False, default=list
    )  # SourceIngestionStage values, e.g. generating_module_feature | user_story_ready_for_review

    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default=SourceIngestionStatus.RUNNING.value
    )  # running | ready_for_review | completed | failed

    # ── Batch-level upload metadata ────────────────────────────────────────
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_language: Mapped[str | None] = mapped_column(String(255), nullable=True)
    frontend_stack: Mapped[str | None] = mapped_column(String(255), nullable=True)
    backend_stack: Mapped[str | None] = mapped_column(String(255), nullable=True)
    infrastructure_stack: Mapped[str | None] = mapped_column(String(255), nullable=True)
    architecture_stack: Mapped[str | None] = mapped_column(String(255), nullable=True)
    database_stack: Mapped[str | None] = mapped_column(String(255), nullable=True)
    coding_standard: Mapped[str | None] = mapped_column(String(255), nullable=True)
    database_strategy: Mapped[str | None] = mapped_column(String(255), nullable=True)
    architecture: Mapped[str | None] = mapped_column(String(255), nullable=True)
    security: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_incremental: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
    user_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    skip_processing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_layout_type: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default=None
    )  # modular | flat | auto; NULL unless source_type is "source_code"

    # Feedback-driven-regeneration context for ingestions not tied to an
    # uploaded Source — e.g. {"feedback": "...", "module_ids": [...], "feature_ids": [...]}.
    entity_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=None)

    # Set only by the incremental-update pipeline when it proposed no changes
    # at all — the AI's plain-language explanation of why. NULL on any run
    # that produced changes, and on non-incremental ingestions.
    no_changes_explanation: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    # Every error message recorded for this ingestion's pipeline run(s) —
    # appended to, never overwritten, mirroring `stages`.
    errors: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)

    tot_modules_from_global_artifact: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    tot_modules: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tot_features: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tot_user_stories: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tot_modules_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tot_modules_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tot_features_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tot_user_stories_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tot_modules_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tot_features_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tot_user_stories_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tot_modules_accepted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tot_modules_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tot_features_accepted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tot_features_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tot_user_stories_accepted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tot_user_stories_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    mod_fea_gen_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    mod_fea_gen_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    user_story_gen_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    user_story_gen_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    project: Mapped[Project] = relationship("Project", foreign_keys=[project_id], viewonly=True)

    __table_args__ = (
        Index("ix_source_ingestions_project_id_deleted_at", "project_id", "deleted_at"),
        Index("ix_source_ingestions_project_id_status", "project_id", "status"),
    )
