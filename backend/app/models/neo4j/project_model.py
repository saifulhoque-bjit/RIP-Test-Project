"""Internal domain model for a Project node in Neo4j."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(slots=True)
class ProjectNode:
    """Represents a :Project node as read from or written to Neo4j.

    Fields mirror the properties set by
    ``Neo4jProjectRepository.upsert_project_node``.
    """

    id: UUID
    name: str
    status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
