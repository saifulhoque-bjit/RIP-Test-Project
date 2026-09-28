"""Unit tests for SRSEvidenceService — thin pass-through to SRSEvidenceRepository."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.srs_evidence_service import SRSEvidenceService


def _make_service() -> tuple[SRSEvidenceService, MagicMock]:
    repo = MagicMock()
    repo.upsert_many_for_project = AsyncMock(return_value=0)
    return SRSEvidenceService(repository=repo), repo


class TestUpsertManyForProject:
    async def test_delegates_rows_to_repository_and_returns_count(self):
        service, repo = _make_service()
        repo.upsert_many_for_project.return_value = 3
        rows = [{"id": "ev-1"}, {"id": "ev-2"}, {"id": "ev-3"}]

        result = await service.upsert_many_for_project(project_id="proj-1", rows=rows)

        assert result == 3
        repo.upsert_many_for_project.assert_awaited_once_with(project_id="proj-1", rows=rows)

    async def test_passes_none_rows_through_unchanged(self):
        service, repo = _make_service()
        repo.upsert_many_for_project.return_value = 0

        result = await service.upsert_many_for_project(project_id="proj-1", rows=None)

        assert result == 0
        repo.upsert_many_for_project.assert_awaited_once_with(project_id="proj-1", rows=None)

    async def test_propagates_repository_exception(self):
        service, repo = _make_service()
        repo.upsert_many_for_project.side_effect = RuntimeError("neo4j write failed")

        with pytest.raises(RuntimeError):
            await service.upsert_many_for_project(project_id="proj-1", rows=[{"id": "ev-1"}])
