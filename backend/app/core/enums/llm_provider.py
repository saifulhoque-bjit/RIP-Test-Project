"""Canonical LLM provider identifiers, shared by Tenant.llm_providers and Project.llm_provider."""

from __future__ import annotations

from enum import Enum


class LLMProvider(str, Enum):
    ANTHROPIC = "anthropic"
    DEEPSEEK = "deepseek"
    OPENAI = "openai"
    GOOGLE = "google"


LLM_PROVIDER_VALUES: tuple[str, ...] = tuple(provider.value for provider in LLMProvider)

# Human-readable labels for API responses.
LLM_PROVIDER_DISPLAY_LABELS: dict[str, str] = {
    LLMProvider.ANTHROPIC.value: "Anthropic",
    LLMProvider.DEEPSEEK.value: "DeepSeek",
    LLMProvider.OPENAI.value: "OpenAI",
    LLMProvider.GOOGLE.value: "Google",
}
