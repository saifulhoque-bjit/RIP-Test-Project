"""Internal domain models for GroupSpec workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class GroupSpecModel:
    """Domain model for GroupSpec details linked to SRS evidence."""

    id: str
    mod_code: str | None = None
    fea_code: str | None = None
    filename: str | None = None
    storage_key: str | None = None
    source_id: str | None = None
    project_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
