"""Internal domain model for a Source node in Neo4j."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(slots=True)
class SourceNode:
    """Represents a :Source node as read from or written to Neo4j.

    Fields mirror the Level-1 properties written by
    ``Neo4jSourceRepository.create_level1_sources``.
    ``file_path`` maps to ``storage_key`` on the ORM model.
    """

    id: UUID
    project_id: UUID
    name: str
    file_type: str
    status: str
    mime_type: str | None = None
    file_path: str | None = None
    source_type: str | None = None
    checksum_sha256: str | None = None
    is_incremental: bool | None = None
    user_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
