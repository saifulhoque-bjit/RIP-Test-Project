"""Schemas for GroupSpec payloads and responses."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class GroupSpecInfo(BaseModel):
    """GroupSpec details linked to an SRS evidence entry."""

    id: str
    mod_code: str | None = None
    fea_code: str | None = None
    filename: str | None = None
    storage_key: str | None = None
    source_id: str | None = None
    project_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SourceCodeGroupSpecSchema(BaseModel):
    """GroupSpec shape emitted by source-code processing before persistence."""

    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    source_id: str
    mod_code: str
    fea_code: str
    filename: str
    storage_key: str | None = None
    created_at: str
    updated_at: str
    # Markdown content read from the pipeline's staged spec file.
    # Carried through to GroupSpecService so it can be uploaded to S3.
    content: Any = None
