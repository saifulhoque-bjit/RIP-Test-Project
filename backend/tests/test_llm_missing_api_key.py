"""A missing provider API key fails module/feature generation at once, with a
reason that tells the user where to fix it.

A missing key cannot be fixed by retrying: every retry rebuilds the same
client with the same (absent) key. ``get_llm`` raises
``MissingLLMApiKeyError``, ``classify_llm_error`` marks it non-retryable, and
the module/feature graph turns it into ``NonRetryableLLMError`` so the Celery
task fails fast instead of waiting through its retry ladder.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.clients.llm_factory import get_llm
from app.core.exceptions import AIWorkflowError, MissingLLMApiKeyError
from app.core.llm_errors import LLMErrorReason, NonRetryableLLMError, classify_llm_error


@pytest.fixture(autouse=True)
def _no_retry_delay():
    with patch("langgraph.pregel._retry.asyncio.sleep", new=AsyncMock(return_value=None)):
        yield


@pytest.mark.parametrize(
    ("provider", "label"),
    [
        ("anthropic", "Anthropic"),
        ("openai", "OpenAI"),
        ("google", "Google"),
        ("deepseek", "DeepSeek"),
    ],
)
@pytest.mark.parametrize("api_key", [None, ""])
def test_get_llm_without_key_points_to_provider_settings(provider, label, api_key):
    with pytest.raises(MissingLLMApiKeyError) as exc_info:
        get_llm(provider=provider, model_name="any-model", api_key=api_key)

    message = str(exc_info.value)
    assert message == (
        f"The API key for the {label} LLM provider has not been set yet. "
        "Please set it up from Clients & Providers settings."
    )
    assert "_API_KEY" not in message
    # Callers that already catch AIWorkflowError keep working.
    assert isinstance(exc_info.value, AIWorkflowError)


def test_missing_key_is_classified_non_retryable():
    exc = MissingLLMApiKeyError("Anthropic")

    classification = classify_llm_error(exc, provider="anthropic")

    assert classification.retryable is False
    assert classification.reason is LLMErrorReason.AUTHENTICATION
    assert classification.user_message == str(exc)


@pytest.mark.asyncio
async def test_module_feature_graph_fails_fast_when_key_missing():
    from app.services.rfp_pipeline_v2_graph_service.graph_module_feature import (
        run_module_feature,
    )

    options = {
        "llm_provider": "anthropic",
        "llm_model": "claude-sonnet-4-20250514",
        "llm_api_key": None,
        "request_id": None,
    }
    with pytest.raises(NonRetryableLLMError) as exc_info:
        await run_module_feature(fragments="RFP content", options=options)

    classification = exc_info.value.classification
    assert classification.reason is LLMErrorReason.AUTHENTICATION
    assert "Clients & Providers settings" in classification.user_message
