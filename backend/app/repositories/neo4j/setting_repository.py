"""Neo4j repository for shared application Setting payload."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from neo4j import Driver

from app.schemas.setting_schema import (
    LlmModel,
    LlmProvider,
    ProjectSettings,
    SettingOption,
    SettingResponse,
    SourceLanguageManifesto,
    SourceCodePipelineSettings,
)


class SettingRepository:
    """Provides low-cost read/write operations for a single Setting node."""

    _SETTING_NAME = "default"
    _SOURCE_LANGUAGES = {
        "pb": "Power Builder",
        "cobol": "COBOL",
        "vb6": "Visual Basic 6",
    }

    _DEFAULT_PAYLOAD: dict[str, list[object]] = {
        "llm_providers": [
            {
                "id": "anthropic",
                "name": "Anthropic",
                "models": [
                    {"id": "claude-sonnet-5", "name": "Claude Sonnet 5"},
                    {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6"},
                    {"id": "claude-opus-4-8", "name": "Claude Opus 4.8"},
                ],
            },
            {
                "id": "openai",
                "name": "OpenAI",
                "models": [
                    {"id": "gpt-5.2-pro", "name": "GPT 5.2 Pro"},
                ],
            },
            {
                "id": "google",
                "name": "Google",
                "models": [
                    {"id": "gemini-pro-latest", "name": "Gemini 3.1 Pro"},
                ],
            },
            {
                "id": "deepseek",
                "name": "DeepSeek",
                "models": [
                    {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro"},
                ],
            },
        ],
        "source_languages": [
            {"key": "pb", "value": "Power Builder"},
            {"key": "cobol", "value": "COBOL"},
            {"key": "vb6", "value": "Visual Basic 6"},
        ],
        "frontend_stacks": [
            {"key": "react/typescript", "value": "React/TypeScript"},
            {"key": "angular", "value": "Angular"},
            {"key": "vue_js", "value": "Vue.js"},
        ],
        "backend_stacks": [
            {"key": "java_spring_boot", "value": "Java Spring Boot"},
            {"key": "node_js", "value": "Node.js"},
            {"key": "django", "value": "Django"},
        ],
        "database_stacks": [
            {"key": "postgresql", "value": "PostgreSQL"},
            {"key": "mysql", "value": "MySQL"},
            {"key": "mongodb", "value": "MongoDB"},
            {"key": "oracle_database", "value": "Oracle Database"},
            {"key": "microsoft_sql_server", "value": "Microsoft SQL Server"},
        ],
        "infrastructure_stacks": [
            {"key": "aws_cloud", "value": "AWS Cloud"},
            {"key": "azure_cloud", "value": "Azure Cloud"},
            {"key": "google_cloud_platform", "value": "Google Cloud Platform"},
            {"key": "on_premise", "value": "On-Premise"},
        ],
        "architecture_stacks": [
            {"key": "monolithic", "value": "Monolithic"},
            {"key": "layered_monolithic", "value": "Layered Monolithic"},
            {"key": "microservices", "value": "Microservices"},
            {"key": "serverless", "value": "Serverless"},
        ],
    }

    def __init__(self, driver: Driver) -> None:
        self._driver = driver

    def get_setting(self) -> SettingResponse | None:
        """Read the Setting node as a typed schema response."""
        cypher = (
            "MATCH (s:Setting {name: $name}) "
            "RETURN "
            "s.llm_providers AS llm_providers, "
            "s.source_languages AS source_languages, "
            "s.frontend_stacks AS frontend_stacks, "
            "s.backend_stacks AS backend_stacks, "
            "s.database_stacks AS database_stacks, "
            "s.infrastructure_stacks AS infrastructure_stacks, "
            "s.architecture_stacks AS architecture_stacks"
        )
        with self._driver.session() as session:
            record = session.execute_read(
                lambda tx: tx.run(cypher, name=self._SETTING_NAME).single()
            )
        if record is None:
            return None
        return self._to_schema(record)

    def seed_default_setting(self) -> SettingResponse:
        """Idempotently create/update the default Setting node and return it."""
        serialized_payload = self._serialize_payload(self._DEFAULT_PAYLOAD)
        cypher = (
            "MERGE (s:Setting {name: $name}) "
            "SET s.llm_providers = $llm_providers, "
            "    s.source_languages = $source_languages, "
            "    s.frontend_stacks = $frontend_stacks, "
            "    s.backend_stacks = $backend_stacks, "
            "    s.database_stacks = $database_stacks, "
            "    s.infrastructure_stacks = $infrastructure_stacks, "
            "    s.architecture_stacks = $architecture_stacks, "
            "    s.updated_at = datetime(), "
            "    s.created_at = coalesce(s.created_at, datetime()) "
            "REMOVE s.llm_models "
            "RETURN "
            "s.llm_providers AS llm_providers, "
            "s.source_languages AS source_languages, "
            "s.frontend_stacks AS frontend_stacks, "
            "s.backend_stacks AS backend_stacks, "
            "s.database_stacks AS database_stacks, "
            "s.infrastructure_stacks AS infrastructure_stacks, "
            "s.architecture_stacks AS architecture_stacks"
        )
        with self._driver.session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    name=self._SETTING_NAME,
                    **serialized_payload,
                ).single()
            )
        return self._to_schema(record)

    @staticmethod
    def _to_schema(record: dict) -> SettingResponse:
        return SettingResponse(
            project=ProjectSettings(
                llm_providers=SettingRepository._normalize_llm_providers(
                    record.get("llm_providers")
                ),
            ),
            source_code_pipeline=SourceCodePipelineSettings(
                source_languages=SettingRepository._normalize_source_languages(
                    record.get("source_languages")
                ),
                source_language_manifestos=SettingRepository._load_manifestos(),
                frontend_stacks=SettingRepository._normalize_options(record.get("frontend_stacks")),
                backend_stacks=SettingRepository._normalize_options(record.get("backend_stacks")),
                database_stacks=SettingRepository._normalize_options(record.get("database_stacks")),
                infrastructure_stacks=SettingRepository._normalize_options(
                    record.get("infrastructure_stacks")
                ),
                architecture_stacks=SettingRepository._normalize_options(
                    record.get("architecture_stacks")
                ),
            ),
        )

    @classmethod
    def _normalize_source_languages(cls, raw: object) -> list[SettingOption]:
        options = cls._normalize_options(raw)
        return [
            SettingOption(
                key=option.key,
                value=cls._SOURCE_LANGUAGES[option.key],
            )
            for option in options
            if option.key in cls._SOURCE_LANGUAGES
        ]

    @classmethod
    def _load_manifestos(cls) -> dict[str, SourceLanguageManifesto]:
        return {
            source_language: cls._load_manifesto(source_language)
            for source_language in cls._SOURCE_LANGUAGES
        }

    @staticmethod
    @lru_cache(maxsize=3)
    def _load_manifesto(source_language: str) -> SourceLanguageManifesto:
        config_path = (
            Path(__file__).resolve().parents[2]
            / "services"
            / "source_code_pipeline"
            / f"{source_language}-project_config.json"
        )
        try:
            with config_path.open(encoding="utf-8") as config_file:
                manifesto = json.load(config_file).get("modernization_manifesto", {})
        except (OSError, json.JSONDecodeError):
            manifesto = {}

        return SourceLanguageManifesto(
            coding_standards=str(manifesto.get("Coding Standards", "")),
            database_strategy=str(manifesto.get("Database Strategy", "")),
            architecture=str(manifesto.get("Architecture", "")),
            security=str(manifesto.get("Security", "")),
        )

    @staticmethod
    def _normalize_options(raw: object) -> list[SettingOption]:
        if not isinstance(raw, list):
            return []

        options: list[SettingOption] = []
        for item in raw:
            option = SettingRepository._parse_option(item)
            if option is not None:
                options.append(option)
        return options

    @staticmethod
    def _normalize_llm_providers(raw: object) -> list[LlmProvider]:
        if not isinstance(raw, list):
            return []

        providers: list[LlmProvider] = []
        for item in raw:
            provider = SettingRepository._parse_llm_provider(item)
            if provider is not None:
                providers.append(provider)
        return providers

    @staticmethod
    def _parse_llm_provider(item: object) -> LlmProvider | None:
        parsed = SettingRepository._decode_json_item(item)
        if not isinstance(parsed, dict):
            return None
        return SettingRepository._provider_from_dict(parsed)

    @staticmethod
    def _decode_json_item(item: object) -> object:
        if not isinstance(item, str):
            return item
        stripped = item.strip()
        if not stripped:
            return None
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _provider_from_dict(data: dict[object, object]) -> LlmProvider | None:
        provider_id = str(data.get("id") or "").strip()
        name = str(data.get("name") or "").strip()
        if not provider_id or not name:
            return None
        models = SettingRepository._parse_llm_models(data.get("models"))
        return LlmProvider(id=provider_id, name=name, models=models)

    @staticmethod
    def _parse_llm_models(raw: object) -> list[LlmModel]:
        if not isinstance(raw, list):
            return []
        models: list[LlmModel] = []
        for item in raw:
            model = SettingRepository._llm_model_from_dict(item)
            if model is not None:
                models.append(model)
        return models

    @staticmethod
    def _llm_model_from_dict(item: object) -> LlmModel | None:
        if not isinstance(item, dict):
            return None
        model_id = str(item.get("id") or "").strip()
        model_name = str(item.get("name") or "").strip()
        if model_id and model_name:
            return LlmModel(id=model_id, name=model_name)
        return None

    @staticmethod
    def _parse_option(item: object) -> SettingOption | None:
        if isinstance(item, dict):
            return SettingRepository._option_from_dict(item)
        if isinstance(item, str):
            return SettingRepository._option_from_string(item)
        return None

    @staticmethod
    def _option_from_dict(data: dict[object, object]) -> SettingOption | None:
        key = str(data.get("key") or "").strip()
        value = str(data.get("value") or "").strip()
        if key and value:
            return SettingOption(key=key, value=value)
        return None

    @staticmethod
    def _option_from_string(text: str) -> SettingOption | None:
        stripped = text.strip()
        if not stripped:
            return None

        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return SettingOption(key=stripped, value=stripped)

        if isinstance(parsed, dict):
            option = SettingRepository._option_from_dict(parsed)
            if option is not None:
                return option

        return SettingOption(key=stripped, value=stripped)

    @staticmethod
    def _serialize_payload(payload: dict[str, list[object]]) -> dict[str, list[str]]:
        """Convert option maps to JSON strings since Neo4j properties cannot store maps."""
        return {
            field: [json.dumps(option, separators=(",", ":")) for option in options]
            for field, options in payload.items()
        }
