"""Unit tests for SettingService — shared Setting payload backed by Neo4j."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.schemas.setting_schema import (
    ProjectSettings,
    SettingResponse,
    SourceCodePipelineSettings,
)
from app.services.setting_service import SettingService


def _make_setting_response() -> SettingResponse:
    return SettingResponse(
        project=ProjectSettings(llm_providers=[]),
        source_code_pipeline=SourceCodePipelineSettings(
            source_languages=[],
            frontend_stacks=[],
            backend_stacks=[],
            database_stacks=[],
            infrastructure_stacks=[],
            architecture_stacks=[],
        ),
    )


class TestGetOrSeedDefaultSettings:
    def test_returns_existing_settings_without_seeding(self):
        repo = MagicMock()
        existing = _make_setting_response()
        repo.get_setting.return_value = existing

        result = SettingService(repository=repo).get_or_seed_default_settings()

        assert result is existing
        repo.seed_default_setting.assert_not_called()

    def test_seeds_default_settings_when_missing(self):
        repo = MagicMock()
        repo.get_setting.return_value = None
        seeded = _make_setting_response()
        repo.seed_default_setting.return_value = seeded

        result = SettingService(repository=repo).get_or_seed_default_settings()

        assert result is seeded
        repo.seed_default_setting.assert_called_once()


class TestSeedDefaultSettings:
    def test_forces_seed_and_returns_result(self):
        repo = MagicMock()
        seeded = _make_setting_response()
        repo.seed_default_setting.return_value = seeded

        result = SettingService(repository=repo).seed_default_settings()

        assert result is seeded
        repo.seed_default_setting.assert_called_once()
