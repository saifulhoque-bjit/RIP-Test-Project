"""Internal domain model for a ProjectMetadata node in Neo4j."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(slots=True)
class ProjectMetadataNode:
    """Represents a :ProjectMetadata node in Neo4j.

    One node exists per project, keyed by ``project_id``.  All complex fields
    (business_requirements, exclusions, persona_glossary)
    are stored as JSON strings because Neo4j properties cannot hold nested maps.
    """

    project_id: UUID
    id: UUID | None = None
    business_requirements: str = "[]"
    exclusions: str = "[]"
    persona_glossary: str = "[]"
    domain_knowledge_storage_key: str | None = None
    architecture_document_storage_key: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None
