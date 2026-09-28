"""Internal domain models for SourceCodeMetadata workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class SourceCodeMetadataModel:
    """Domain model for per-module source-code pipeline metadata."""

    id: str
    module_id: str | None = None
    module_response: Any = None
    module_manifest: Any = None
    source_id: str | None = None
    project_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
