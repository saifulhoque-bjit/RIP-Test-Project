"""Schemas for SourceCodeMetadata payloads and responses."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class SourceCodeMetadataInfo(BaseModel):
    """Per-module source-code pipeline metadata."""

    id: str
    module_id: str | None = None
    module_response: Any = None
    module_manifest: Any = None
    source_id: str | None = None
    project_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SourceCodeMetadataSchema(BaseModel):
    """SourceCodeMetadata shape emitted by source-code processing before persistence."""

    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    source_id: str
    module_id: str
    created_at: str
    updated_at: str
    # Full per-module pipeline result, carried through for storage as-is.
    module_response: Any = None
    # module_manifest.json contents, fetched via PipelineOrchestrator.get_module_manifest().
    module_manifest: Any = None
