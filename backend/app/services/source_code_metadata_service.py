"""Business logic for SourceCodeMetadata graph workflows."""

from __future__ import annotations

import json
from typing import Any

from app.models.neo4j.source_code_metadata_model import SourceCodeMetadataModel
from app.repositories.neo4j.source_code_metadata_repository import SourceCodeMetadataRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class SourceCodeMetadataService:
    """Validates and persists SourceCodeMetadata rows for project-scoped source processing."""

    def __init__(self, repository: SourceCodeMetadataRepository | None = None) -> None:
        self._repository = repository or SourceCodeMetadataRepository()

    async def get_for_module(
        self,
        *,
        project_id: str,
        module_id: str,
    ) -> SourceCodeMetadataModel | None:
        """Return the stored SourceCodeMetadata for one module, or None."""
        return await self._repository.get_by_project_and_module(
            project_id=project_id,
            module_id=module_id,
        )

    async def upsert_many_for_project(
        self,
        *,
        project_id: str,
        rows: list[dict] | None,
    ) -> int:
        if not rows:
            return 0

        prepared_rows = self._serialize_rows(rows)

        count = await self._repository.upsert_many_for_project(
            project_id=project_id,
            rows=prepared_rows,
        )
        logger.info(
            "Stored SourceCodeMetadata rows in Neo4j for project_id=%s count=%d",
            project_id,
            count,
        )
        return count

    @staticmethod
    def _serialize_rows(rows: list[dict]) -> list[dict[str, Any]]:
        prepared_rows: list[dict[str, Any]] = []

        for row in rows:
            if not isinstance(row, dict):
                continue

            prepared_row = dict(row)
            prepared_row["module_response"] = json.dumps(
                prepared_row.get("module_response"), ensure_ascii=False, default=str
            )
            prepared_row["module_manifest"] = json.dumps(
                prepared_row.get("module_manifest"), ensure_ascii=False, default=str
            )
            prepared_rows.append(prepared_row)

        return prepared_rows
