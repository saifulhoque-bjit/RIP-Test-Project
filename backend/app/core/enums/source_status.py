"""Canonical source-processing lifecycle statuses.

This enum is the single source of truth for source.status persisted in PostgreSQL
and streamed to frontend source WebSocket clients.
"""

from __future__ import annotations

from enum import Enum


class SourceProcessingStatus(str, Enum):
    UPLOADED = "uploaded"
    QUEUED = "queued"
    RUNNING = "running"
    READY_FOR_REVIEW = "ready_for_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


SOURCE_PROCESSING_STATUS_VALUES: tuple[str, ...] = tuple(
    status.value for status in SourceProcessingStatus
)

# Human-readable labels for API responses.
SOURCE_STATUS_DISPLAY_LABELS: dict[str, str] = {
    SourceProcessingStatus.UPLOADED.value: "Uploaded",
    SourceProcessingStatus.QUEUED.value: "Queued",
    SourceProcessingStatus.RUNNING.value: "Running",
    SourceProcessingStatus.READY_FOR_REVIEW.value: "Ready for review",
    SourceProcessingStatus.COMPLETED.value: "Completed",
    SourceProcessingStatus.FAILED.value: "Failed",
    SourceProcessingStatus.CANCELLED.value: "Cancelled",
}
