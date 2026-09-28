"""Tests that the RFP LangGraph pipeline's `generate`/`critic` nodes classify
LLM errors and either fail fast (non-retryable) or keep LangGraph's existing
`RetryPolicy(max_attempts=3, retry_on=(Exception,))` behavior unchanged
(retryable).

Covers all 5 graph files: Module Feature Generation/Regeneration
(`graph_module_feature.py`), User Story Generation/Regeneration
(`graph_agile_backlog.py`), User Story Regeneration by feedback
(`graph_agile_backlog_patch.py`), and Incremental Updates
(`graph_incremental.py`, `graph_incremental_selector.py`).

For each graph, the first LLM call made is inside `node_generate` — forcing
that call to fail is enough to exercise the new classify-and-raise-or-reraise
logic without needing to drive the graph all the way to `node_critic`.

The non-retryable assertion is the core contract: `NonRetryableLLMError` is a
`BaseException`, so it must propagate out of the module's public `run_*`
entry point uncaught (that function's own `except Exception` cannot catch
it) — proving it also bypassed LangGraph's node-level `RetryPolicy`, whose
retry wrapper only ever catches `Exception` (verified against the installed
`langgraph/pregel/_retry.py`). The retryable assertion proves the opposite:
the existing `max_attempts=3` retry ladder still runs to completion and the
`run_*` function still returns its normal `{"status": "failed", ...}` dict.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import openai
import pytest

from app.core.llm_errors import NonRetryableLLMError

_OPTIONS = {"llm_provider": "openai", "llm_model": "gpt-4o", "request_id": None}


def _auth_error() -> openai.AuthenticationError:
    response = httpx.Response(401, request=httpx.Request("POST", "https://example.com"))
    return openai.AuthenticationError("Invalid API key", response=response, body=None)


def _rate_limit_error() -> openai.RateLimitError:
    response = httpx.Response(429, request=httpx.Request("POST", "https://example.com"))
    return openai.RateLimitError("rate limited", response=response, body=None)


@pytest.fixture(autouse=True)
def _no_retry_delay():
    """LangGraph sleeps between retries (1s, 2s, ... per RetryPolicy) — make
    the retryable-exhaustion tests instant instead of ~3s each."""
    with patch("langgraph.pregel._retry.asyncio.sleep", new=AsyncMock(return_value=None)):
        yield


class TestGraphModuleFeature:
    _MOD = "app.services.rfp_pipeline_v2_graph_service.graph_module_feature"

    @pytest.mark.asyncio
    async def test_non_retryable_error_fails_fast_without_retry(self):
        from app.services.rfp_pipeline_v2_graph_service.graph_module_feature import (
            run_module_feature,
        )

        mock_invoke = AsyncMock(side_effect=_auth_error())
        with (
            patch(f"{self._MOD}.get_llm", return_value=MagicMock()),
            patch(f"{self._MOD}.ainvoke_cancellable", mock_invoke),
            patch(f"{self._MOD}.ainvoke_cancellable_structured", mock_invoke),
            pytest.raises(NonRetryableLLMError),
        ):
            await run_module_feature(fragments="RFP content", options=_OPTIONS)
        assert mock_invoke.call_count == 1

    @pytest.mark.asyncio
    async def test_retryable_error_keeps_existing_retry_behavior(self):
        from app.services.rfp_pipeline_v2_graph_service.graph_module_feature import (
            run_module_feature,
        )

        mock_invoke = AsyncMock(side_effect=_rate_limit_error())
        with (
            patch(f"{self._MOD}.get_llm", return_value=MagicMock()),
            patch(f"{self._MOD}.ainvoke_cancellable", mock_invoke),
            patch(f"{self._MOD}.ainvoke_cancellable_structured", mock_invoke),
        ):
            result = await run_module_feature(fragments="RFP content", options=_OPTIONS)
        assert result["status"] == "failed"
        assert mock_invoke.call_count == 3


class TestGraphAgileBacklog:
    _MOD = "app.services.rfp_pipeline_v2_graph_service.graph_agile_backlog"

    @pytest.mark.asyncio
    async def test_non_retryable_error_fails_fast_without_retry(self):
        from app.services.rfp_pipeline_v2_graph_service.graph_agile_backlog import (
            run_agile_backlog,
        )

        mock_invoke = AsyncMock(side_effect=_auth_error())
        with (
            patch(f"{self._MOD}.get_llm", return_value=MagicMock()),
            patch(f"{self._MOD}.ainvoke_cancellable", mock_invoke),
            patch(f"{self._MOD}.ainvoke_cancellable_structured", mock_invoke),
            pytest.raises(NonRetryableLLMError),
        ):
            await run_agile_backlog(
                fragments="RFP content", modules_and_features="M/F", options=_OPTIONS
            )
        assert mock_invoke.call_count == 1

    @pytest.mark.asyncio
    async def test_retryable_error_keeps_existing_retry_behavior(self):
        from app.services.rfp_pipeline_v2_graph_service.graph_agile_backlog import (
            run_agile_backlog,
        )

        mock_invoke = AsyncMock(side_effect=_rate_limit_error())
        with (
            patch(f"{self._MOD}.get_llm", return_value=MagicMock()),
            patch(f"{self._MOD}.ainvoke_cancellable", mock_invoke),
            patch(f"{self._MOD}.ainvoke_cancellable_structured", mock_invoke),
        ):
            result = await run_agile_backlog(
                fragments="RFP content", modules_and_features="M/F", options=_OPTIONS
            )
        assert result["status"] == "failed"
        assert mock_invoke.call_count == 3


class TestGraphAgileBacklogPatch:
    _MOD = "app.services.rfp_pipeline_v2_graph_service.graph_agile_backlog_patch"

    @pytest.mark.asyncio
    async def test_non_retryable_error_fails_fast_without_retry(self):
        from app.services.rfp_pipeline_v2_graph_service.graph_agile_backlog_patch import (
            run_agile_backlog_patch,
        )

        mock_invoke = AsyncMock(side_effect=_auth_error())
        with (
            patch(f"{self._MOD}.get_llm", return_value=MagicMock()),
            patch(f"{self._MOD}.ainvoke_cancellable", mock_invoke),
            patch(f"{self._MOD}.ainvoke_cancellable_structured", mock_invoke),
            pytest.raises(NonRetryableLLMError),
        ):
            await run_agile_backlog_patch(
                story_feedbacks=[],
                feature_contexts=[],
                persona_glossary="",
                valid_sources="",
                options=_OPTIONS,
            )
        assert mock_invoke.call_count == 1

    @pytest.mark.asyncio
    async def test_retryable_error_keeps_existing_retry_behavior(self):
        from app.services.rfp_pipeline_v2_graph_service.graph_agile_backlog_patch import (
            run_agile_backlog_patch,
        )

        mock_invoke = AsyncMock(side_effect=_rate_limit_error())
        with (
            patch(f"{self._MOD}.get_llm", return_value=MagicMock()),
            patch(f"{self._MOD}.ainvoke_cancellable", mock_invoke),
            patch(f"{self._MOD}.ainvoke_cancellable_structured", mock_invoke),
        ):
            result = await run_agile_backlog_patch(
                story_feedbacks=[],
                feature_contexts=[],
                persona_glossary="",
                valid_sources="",
                options=_OPTIONS,
            )
        assert result["status"] == "failed"
        assert mock_invoke.call_count == 3


class TestGraphIncremental:
    _MOD = "app.services.rfp_pipeline_v2_graph_service.graph_incremental"

    @pytest.mark.asyncio
    async def test_non_retryable_error_fails_fast_without_retry(self):
        from app.services.rfp_pipeline_v2_graph_service.graph_incremental import (
            run_incremental_update,
        )

        mock_invoke = AsyncMock(side_effect=_auth_error())
        with (
            patch(f"{self._MOD}.get_llm", return_value=MagicMock()),
            patch(f"{self._MOD}.ainvoke_cancellable", mock_invoke),
            patch(f"{self._MOD}.ainvoke_cancellable_structured", mock_invoke),
            pytest.raises(NonRetryableLLMError),
        ):
            await run_incremental_update(backlog="{}", meeting_notes="[]", options=_OPTIONS)
        assert mock_invoke.call_count == 1

    @pytest.mark.asyncio
    async def test_retryable_error_keeps_existing_retry_behavior(self):
        from app.services.rfp_pipeline_v2_graph_service.graph_incremental import (
            run_incremental_update,
        )

        mock_invoke = AsyncMock(side_effect=_rate_limit_error())
        with (
            patch(f"{self._MOD}.get_llm", return_value=MagicMock()),
            patch(f"{self._MOD}.ainvoke_cancellable", mock_invoke),
            patch(f"{self._MOD}.ainvoke_cancellable_structured", mock_invoke),
        ):
            result = await run_incremental_update(
                backlog="{}", meeting_notes="[]", options=_OPTIONS
            )
        assert result["status"] == "failed"
        assert mock_invoke.call_count == 3


class TestGraphIncrementalSelector:
    _MOD = "app.services.rfp_pipeline_v2_graph_service.graph_incremental_selector"

    @pytest.mark.asyncio
    async def test_non_retryable_error_fails_fast_without_retry(self):
        from app.services.rfp_pipeline_v2_graph_service.graph_incremental_selector import (
            run_incremental_selector,
        )

        mock_invoke = AsyncMock(side_effect=_auth_error())
        with (
            patch(f"{self._MOD}.get_llm", return_value=MagicMock()),
            patch(f"{self._MOD}.ainvoke_cancellable", mock_invoke),
            pytest.raises(NonRetryableLLMError),
        ):
            await run_incremental_selector(note="a note", items=[], options=_OPTIONS)
        assert mock_invoke.call_count == 1

    @pytest.mark.asyncio
    async def test_retryable_error_keeps_existing_retry_behavior(self):
        from app.services.rfp_pipeline_v2_graph_service.graph_incremental_selector import (
            run_incremental_selector,
        )

        mock_invoke = AsyncMock(side_effect=_rate_limit_error())
        with (
            patch(f"{self._MOD}.get_llm", return_value=MagicMock()),
            patch(f"{self._MOD}.ainvoke_cancellable", mock_invoke),
        ):
            result = await run_incremental_selector(note="a note", items=[], options=_OPTIONS)
        assert result["status"] == "failed"
        assert mock_invoke.call_count == 3
