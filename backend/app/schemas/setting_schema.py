"""Schemas for the shared Settings payload returned by the API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SettingOption(BaseModel):
    key: str
    value: str


class SourceLanguageManifesto(BaseModel):
    coding_standards: str
    database_strategy: str
    architecture: str
    security: str


# Every registry enum's catalog entry is a {value: label} map — ``label``
# falls back to ``value`` for enums with no dedicated display-labels mapping,
# so every entry can be looked up the same way. The non-enum additions
# (rfp_baseline_stages_status_map, feedback_or_incremental_stages_status_map,
# source_code_baseline_stages_status_map) are each a stage-key ->
# status-values map instead — see EnumCatalogService.get_enum_catalog.
EnumCatalogValue = dict[str, str] | dict[str, list[str]]


class LlmModel(BaseModel):
    id: str
    name: str


class LlmProvider(BaseModel):
    id: str
    name: str
    models: list[LlmModel]


class ProjectSettings(BaseModel):
    llm_providers: list[LlmProvider]


class SourceCodePipelineSettings(BaseModel):
    source_languages: list[SettingOption]
    source_language_manifestos: dict[str, SourceLanguageManifesto] = Field(
        default_factory=dict,
    )
    frontend_stacks: list[SettingOption]
    backend_stacks: list[SettingOption]
    database_stacks: list[SettingOption]
    infrastructure_stacks: list[SettingOption]
    architecture_stacks: list[SettingOption]


class SettingResponse(BaseModel):
    project: ProjectSettings
    source_code_pipeline: SourceCodePipelineSettings
