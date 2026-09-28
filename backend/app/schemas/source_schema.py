"""Pydantic schemas for the Source domain.

Separate request / response models enforce strict API boundaries.
``extra='forbid'`` on request schemas prevents mass assignment.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from app.core.enums.source_status import SOURCE_STATUS_DISPLAY_LABELS
from app.core.enums.source_type import SourceType
from app.core.messages import (
    DESC_LINK_URL,
    DESC_SOURCE_TYPE,
    MSG_LINK_URL_HTTPS_REQUIRED,
    MSG_LINK_URL_MISSING_HOST,
    MSG_LINK_URL_TOO_LONG,
)

# ── Response schemas ───────────────────────────────────────────────────────


class CreatedByInfo(BaseModel):
    """Compact user reference for the source creator."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str | None


class SourceResponse(BaseModel):
    """Full source record returned by single-upload, detail, and list APIs."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    original_name: str
    relative_path: str | None
    storage_key: str | None
    file_size_bytes: int
    mime_type: str
    file_type: str
    upload_type: str
    batch_id: UUID | None
    source_type: str | None
    status: str
    checksum_sha256: str | None
    link_url: str | None
    is_deleted: bool
    created_by: CreatedByInfo
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def resolve_created_by(cls, data: Any) -> Any:
        """Map the ORM ``uploader`` relationship to the ``created_by`` nested field."""
        if hasattr(data, "uploader") and data.uploader is not None:
            # Wrap the ORM object in a read-only proxy that overrides only
            # ``created_by``.  This avoids mutating the live SQLAlchemy
            # session-tracked object — direct mutation of source.created_by
            # overwrites the UUID column with a User object, causing the next
            # flush to emit: UPDATE sources SET created_by = <User>, which
            # psycopg2 cannot adapt to UUID.
            uploader = data.uploader

            class _Proxy:
                created_by = uploader  # noqa: E731

                def __getattr__(self, name: str) -> Any:
                    return getattr(data, name)

            return _Proxy()
        return data

    @computed_field  # type: ignore[misc]
    @property
    def status_display(self) -> str:
        """Human-readable status label for the source's current lifecycle state."""
        return SOURCE_STATUS_DISPLAY_LABELS.get(self.status, self.status)


class SourceListResponse(BaseModel):
    """Paginated list wrapper for source records."""

    model_config = ConfigDict(from_attributes=True)

    items: list[SourceResponse]
    total: int
    skip: int
    limit: int


class AiSourceDownloadResponse(BaseModel):
    """Result payload for AI-team local source download preparation."""

    local_path: str
    content_type: str
    original_name: str


# ── Bulk-upload response ───────────────────────────────────────────────────


class BulkUploadItemResult(BaseModel):
    """Per-file outcome within a bulk upload response."""

    filename: str
    relative_path: str | None = None
    status: str  # 'uploaded' | 'failed' | 'duplicate'
    status_code: int  # HTTP-equivalent: 201 uploaded | 409 duplicate | 422 validation | 500 failed
    source_id: UUID | None = None
    error: str | None = None


class BulkUploadResponse(BaseModel):
    """Summary returned after a bulk upload request."""

    batch_id: UUID
    total: int
    succeeded: int
    failed: int
    results: list[BulkUploadItemResult]


# ── Incremental bulk-upload response ─────────────────────────────────────────


class IncrementalUploadItemResult(BaseModel):
    """Per-file outcome within an incremental bulk upload response."""

    filename: str
    status: str  # 'uploaded' | 'failed' | 'duplicate'
    status_code: int
    source_id: UUID | None = None
    error: str | None = None


class IncrementalBulkUploadResponse(BaseModel):
    """Immediate response after an incremental bulk upload request.

    Files are uploaded and saved synchronously.  The AI pipeline
    (fragment parsing + backlog update) is queued as a background
    Celery task.  Subscribe to ``/ws/projects/{project_id}`` and
    watch for ``task_id`` events to receive pipeline progress and
    the final result.  ``task_id`` is ``None`` when every file in the
    batch was rejected during pre-validation (unsupported type, too
    large, or a duplicate) — no ingestion or task was started, and
    ``results`` holds the full per-file rejection reasons.
    """

    batch_id: UUID
    task_id: str | None = None
    total: int
    succeeded: int
    failed: int
    results: list[IncrementalUploadItemResult]


# ── Link-upload request ──────────────────────────────────────────────────


class LinkUploadRequest(BaseModel):
    """Request body for the link-based source upload endpoint."""

    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    link_url: AnyHttpUrl = Field(
        ...,
        description=DESC_LINK_URL,
        examples=["https://github.com/owner/repo"],
    )
    source_type: SourceType = Field(..., description=DESC_SOURCE_TYPE)
    description: str | None = Field(default=None, max_length=2_000)

    @field_validator("link_url", mode="before")
    @classmethod
    def validate_link_url(cls, v: object) -> object:
        """Enforce HTTPS-only and reject structurally invalid URLs early.

        Runs *before* Pydantic parses the value as ``AnyHttpUrl`` so that
        the error is surfaced as a clean 422 field validation error rather
        than bubbling up from the download layer.
        """
        if not isinstance(v, str):
            return v  # let AnyHttpUrl handle non-string types

        stripped = v.strip()

        if len(stripped) > 2_048:
            raise ValueError(MSG_LINK_URL_TOO_LONG)

        if not stripped.startswith("https://"):
            raise ValueError(MSG_LINK_URL_HTTPS_REQUIRED)

        # Ensure a non-empty hostname follows the scheme.
        after_scheme = stripped[len("https://") :]
        if not after_scheme or after_scheme.startswith("/"):
            raise ValueError(MSG_LINK_URL_MISSING_HOST)

        return stripped


# ── Bulk-delete request / response ────────────────────────────────────────


class BulkDeleteRequest(BaseModel):
    """Request body for deleting multiple sources in one request."""

    model_config = ConfigDict(extra="forbid")

    source_ids: list[UUID] = Field(..., min_length=1)


class BulkDeleteItemResult(BaseModel):
    """Per-source outcome within a bulk delete response."""

    source_id: UUID
    status: str  # 'deleted' | 'not_found' | 'forbidden' | 'failed'
    status_code: int  # HTTP-equivalent: 204 deleted | 404 not_found | 403 forbidden | 500 failed
    error: str | None = None


class BulkDeleteResponse(BaseModel):
    """Summary returned after a bulk delete request."""

    total: int
    succeeded: int
    failed: int
    results: list[BulkDeleteItemResult]
