"""Canonical lifecycle statuses for SourceIngestion.

This enum is the single source of truth for ``source_ingestions.status``
persisted in PostgreSQL. An ingestion starts ``uploaded`` while its files are
being received, moves to ``queued`` once accepted for processing, then
``running`` while its batch is being processed. Both RFP module/feature
generation completion (awaiting the user's module/feature approval, which
then kicks off user-story generation) and user-story generation completion —
as well as the source-code/incremental pipelines' single-pass completion —
move it to ``ready_for_review``; which phase actually finished is
distinguished via ``SourceIngestion.stages`` (see
``app.core.enums.source_ingestion_stage.SourceIngestionStage``), not via a
distinct status value. It becomes ``completed`` once review/approval
is done, ``failed`` if the batch could not be processed, or ``cancelled`` if
the run was cancelled via the task-cancellation API before it reached a
terminal state.

Usage::

    from app.core.enums.source_ingestion_status import SourceIngestionStatus

    ingestion.status = SourceIngestionStatus.RUNNING.value   # model layer
    status: SourceIngestionStatus = SourceIngestionStatus.FAILED  # service layer
"""

from __future__ import annotations

from enum import Enum


class SourceIngestionStatus(str, Enum):
    """Lifecycle states for a :class:`~app.models.postgres.source_ingestion_model.SourceIngestion`."""

    UPLOADED = "uploaded"
    QUEUED = "queued"
    RUNNING = "running"
    FAILED = "failed"
    CANCELLED = "cancelled"
    READY_FOR_REVIEW = "ready_for_review"
    COMPLETED = "completed"


# Human-readable labels for API responses.
SOURCE_INGESTION_STATUS_DISPLAY_LABELS: dict[str, str] = {
    SourceIngestionStatus.UPLOADED.value: "Uploaded",
    SourceIngestionStatus.QUEUED.value: "Queued",
    SourceIngestionStatus.RUNNING.value: "Running",
    SourceIngestionStatus.FAILED.value: "Failed",
    SourceIngestionStatus.CANCELLED.value: "Cancelled",
    SourceIngestionStatus.READY_FOR_REVIEW.value: "Ready for review",
    SourceIngestionStatus.COMPLETED.value: "Completed",
}
