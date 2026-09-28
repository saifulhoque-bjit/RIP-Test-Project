"""Business logic for SRS evidence graph workflows."""

from __future__ import annotations

from app.repositories.neo4j.srs_evidence_repository import SRSEvidenceRepository


class SRSEvidenceService:
    """Validates and persists SRS evidence rows for project-scoped source processing."""

    def __init__(self, repository: SRSEvidenceRepository | None = None) -> None:
        self._repository = repository or SRSEvidenceRepository()

    async def upsert_many_for_project(
        self,
        *,
        project_id: str,
        rows: list[dict] | None,
    ) -> int:
        return await self._repository.upsert_many_for_project(
            project_id=project_id,
            rows=rows,
        )
