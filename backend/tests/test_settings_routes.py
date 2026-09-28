"""Unit tests for routes/v1/settings.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.routes.v1.settings import get_enum_catalog, get_settings
from app.schemas.setting_schema import (
    LlmModel,
    LlmProvider,
    ProjectSettings,
    SettingOption,
    SettingResponse,
    SourceCodePipelineSettings,
)
from tests.conftest import make_user


def _setting_payload() -> SettingResponse:
    return SettingResponse(
        project=ProjectSettings(
            llm_providers=[
                LlmProvider(
                    id="anthropic",
                    name="Anthropic",
                    models=[
                        LlmModel(id="claude-sonnet-4-6", name="Claude Sonnet 4.6"),
                        LlmModel(id="claude-opus-4-8", name="Claude Opus 4.8"),
                    ],
                ),
                LlmProvider(
                    id="openai",
                    name="OpenAI",
                    models=[LlmModel(id="gpt-5.2-pro", name="GPT 5.2 Pro")],
                ),
                LlmProvider(
                    id="google",
                    name="Google",
                    models=[LlmModel(id="gemini-pro-latest", name="Gemini 3.1 Pro")],
                ),
                LlmProvider(
                    id="deepseek",
                    name="DeepSeek",
                    models=[LlmModel(id="deepseek-v4-pro", name="DeepSeek V4 Pro")],
                ),
            ],
        ),
        source_code_pipeline=SourceCodePipelineSettings(
            source_languages=[
                SettingOption(key="PowerBuilder", value="PowerBuilder"),
                SettingOption(key="COBOL", value="COBOL"),
                SettingOption(key="VB6", value="VB6"),
                SettingOption(key="JavaScript", value="JavaScript"),
                SettingOption(key="Python", value="Python"),
                SettingOption(key="Java", value="Java"),
            ],
            frontend_stacks=[
                SettingOption(key="React/TypeScript", value="React/TypeScript"),
                SettingOption(key="Angular JS", value="Angular JS"),
                SettingOption(key="Vue.js", value="Vue.js"),
            ],
            backend_stacks=[
                SettingOption(key="Java Spring Boot", value="Java Spring Boot"),
                SettingOption(key="Node.js", value="Node.js"),
                SettingOption(key="Django", value="Django"),
            ],
            database_stacks=[
                SettingOption(key="PostgreSQL", value="PostgreSQL"),
                SettingOption(key="MySQL", value="MySQL"),
                SettingOption(key="MongoDB", value="MongoDB"),
            ],
            infrastructure_stacks=[
                SettingOption(key="AWS Cloud", value="AWS Cloud"),
                SettingOption(key="Azure Cloud", value="Azure Cloud"),
                SettingOption(key="Google Cloud Platform", value="Google Cloud Platform"),
            ],
            architecture_stacks=[
                SettingOption(key="Monolithic", value="Monolithic"),
                SettingOption(key="Layered Monolithic", value="Layered Monolithic"),
                SettingOption(key="Microservices", value="Microservices"),
                SettingOption(key="Serverless", value="Serverless"),
            ],
        ),
    )


@pytest.mark.asyncio
async def test_get_settings_returns_expected_shape() -> None:
    current_user = make_user()
    payload = _setting_payload()

    with patch("app.routes.v1.settings.SettingService") as mock_service_cls:
        service = MagicMock()
        service.get_or_seed_default_settings.return_value = payload
        mock_service_cls.return_value = service

        result = await get_settings(_current_user=current_user)

    service.get_or_seed_default_settings.assert_called_once_with()
    assert result.success is True
    assert result.data is not None
    assert result.data.project.llm_providers[0].id == "anthropic"
    assert result.data.project.llm_providers[0].name == "Anthropic"
    assert result.data.project.llm_providers[0].models[0].id == "claude-sonnet-4-6"
    assert result.data.project.llm_providers[0].models[0].name == "Claude Sonnet 4.6"
    assert result.data.source_code_pipeline.backend_stacks[0].key == "Java Spring Boot"
    assert result.data.source_code_pipeline.backend_stacks[0].value == "Java Spring Boot"


def test_get_enum_catalog_returns_expected_shape() -> None:
    current_user = make_user()
    payload = {
        "project_status": ["active", "inactive", "archived", "deleted"],
        "tenant_status": ["pending_invitation", "active", "inactive", "suspended"],
    }

    with patch("app.routes.v1.settings.EnumCatalogService") as mock_service_cls:
        service = MagicMock()
        service.get_enum_catalog.return_value = payload
        mock_service_cls.return_value = service

        result = get_enum_catalog(_current_user=current_user)

    service.get_enum_catalog.assert_called_once_with()
    assert result.success is True
    assert result.data == payload
