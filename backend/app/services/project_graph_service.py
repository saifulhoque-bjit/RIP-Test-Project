"""Service layer for project-level Neo4j graph metadata operations."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.db.neo4j import get_neo4j_driver
from app.repositories.neo4j.project_metadata_repository import ProjectMetadataRepository


class ProjectGraphService:
    """Coordinates project metadata updates through the Neo4j repository."""

    def __init__(self, repository: ProjectMetadataRepository | None = None) -> None:
        self._repository = repository or ProjectMetadataRepository(get_neo4j_driver())

    def upsert_project_metadata(self, *, project_id: UUID) -> None:
        """Idempotently create a :ProjectMetadata node for the given project."""
        self._repository.upsert(project_id=project_id)

    def update_project_metadata(
        self,
        *,
        project_id: UUID,
        persona_glossary: list[dict[str, str]] | None = None,
        business_requirements: list[dict] | None = None,
        exclusions: list[str] | None = None,
    ) -> None:
        """Update metadata fields on the :ProjectMetadata node."""
        self._repository.update(
            project_id=project_id,
            persona_glossary=persona_glossary,
            business_requirements=business_requirements,
            exclusions=exclusions,
        )

    def get_project_metadata(self, *, project_id: UUID) -> dict[str, Any]:
        """Return all metadata fields for the given project.

        Returns a dict with keys ``business_requirements``, ``exclusions``,
        and ``persona_glossary``.
        """
        return self._repository.get(project_id=project_id)

    def get_persona_glossary(self, *, project_id: UUID) -> list[dict]:
        """Return the persona_glossary list for the given project."""
        metadata = self._repository.get(project_id=project_id)
        return metadata["persona_glossary"]
