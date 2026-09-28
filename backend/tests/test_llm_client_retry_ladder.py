"""Tests for LLMClient's own internal retry ladder
(app/services/source_code_pipeline/src/ai/llm_client.py's _complete_impl).

Covers TC-3.1 and TC-3.4 from
docs/testing/source_code_pipeline_user_test_cases.md — Section 3 ("No retry
from Redis/Celery — retry is handled inside the AI pipeline service"):

  TC-3.1: a transient AI error is retried internally (not by Celery) and
          recovers if possible.
  TC-3.4: a permanent AI error (quota exhausted / content blocked / bad
          request) fails fast without wasting time on repeated retries.

Scope note: this is a large, provider-agnostic file (~1900 lines) covering
many providers/config combinations. These tests exercise only the core
retry/classification loop in `_complete_impl` with the default ("openai")
provider and a mocked `litellm.completion` — not the full provider matrix,
the adaptive timeout ladder, or streaming, which would need a much larger
follow-up.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core.llm_errors import LLMErrorReason, NonRetryableLLMError
from app.services.source_code_pipeline.src.ai.llm_client import (
    CreditBalanceExhaustedError,
    LLMClient,
    LLMTimeoutExhausted,
)

_MODULE = "app.services.source_code_pipeline.src.ai.llm_client"


def _make_client(*, max_retries: int = 2, timeout_max_retries: int = 1) -> LLMClient:
    """A default-config client with the hard watchdog disabled (so
    litellm.completion runs inline, no background thread) and a known,
    small retry budget. reset_circuit_breaker() keeps the process-level
    singleton breaker (see _CircuitBreaker's own docstring) from leaking
    failure counts between tests."""
    LLMClient.reset_circuit_breaker()
    client = LLMClient(use_reasoning_model=False)
    client._llm_cfg["max_retries"] = max_retries
    client._llm_cfg["timeout_max_retries"] = timeout_max_retries
    client._llm_cfg["hard_watchdog_enabled"] = False
    return client


def _response(content: str = "ok", finish_reason: str = "stop") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)]
    )


# Named to match litellm's own normalised exception class names, since
# _complete_impl classifies transient/permanent/timeout off
# type(exc).__name__ (provider-agnostic — see its own comments).
class InternalServerError(Exception):
    pass


class BadRequestError(Exception):
    pass


class APITimeoutError(Exception):
    pass


class AuthenticationError(Exception):
    pass


class PermissionDeniedError(Exception):
    pass


class NotFoundError(Exception):
    pass


class ContextWindowExceededError(Exception):
    pass


class TestTransientErrorRetriedInternally:
    """TC-3.1: a transient error is retried by the AI service layer itself —
    Celery never sees it as a failure requiring its own retry."""

    def test_recovers_after_one_transient_error(self):
        client = _make_client(max_retries=2)
        with (
            patch(
                f"{_MODULE}.litellm.completion",
                side_effect=[InternalServerError("503 Service Unavailable"), _response("hello")],
            ) as mock_completion,
            patch("time.sleep"),
        ):
            result = client.complete("system", "user")

        assert result == "hello"
        assert mock_completion.call_count == 2

    def test_fails_after_exhausting_the_transient_budget(self):
        """max_retries=2 allows 3 attempts total; a transient error on every
        attempt must fail after exactly that many, not loop forever."""
        client = _make_client(max_retries=2)
        with (
            patch(
                f"{_MODULE}.litellm.completion",
                side_effect=InternalServerError("503 Service Unavailable"),
            ) as mock_completion,
            patch("time.sleep"),
            pytest.raises(RuntimeError, match="Call failed"),
        ):
            client.complete("system", "user")

        assert mock_completion.call_count == 3

    def test_timeout_uses_its_own_smaller_retry_budget(self):
        """Timeouts get a separate, smaller cap (timeout_max_retries) instead
        of the generic transient budget — an expensive timed-out call isn't
        retried the full budget."""
        client = _make_client(max_retries=5, timeout_max_retries=1)
        with (
            patch(
                f"{_MODULE}.litellm.completion",
                side_effect=APITimeoutError("Request timed out after 600s"),
            ) as mock_completion,
            patch("time.sleep"),
            pytest.raises(LLMTimeoutExhausted),
        ):
            client.complete("system", "user")

        # timeout_max_retries=1 -> 2 attempts, not 6 (which max_retries=5 would allow).
        assert mock_completion.call_count == 2


class TestPermanentErrorFailsFast:
    """TC-3.4: a non-recoverable AI error fails immediately, without
    wasting time on repeated internal retries."""

    def test_bad_request_fails_on_first_attempt(self):
        """A permanent, non-credit classification (here: INVALID_REQUEST) now
        raises NonRetryableLLMError (a BaseException) instead of a plain
        RuntimeError — so a Celery task boundary can abort the whole
        module/bucket run instead of just failing this one call, the same
        "abort the whole run" contract CreditBalanceExhaustedError already
        has. See app/workers/source_code_task.py's NonRetryableLLMError
        handling and app/workers/_task_helpers.py's NonRetryableLLMTaskFailure."""
        client = _make_client(max_retries=3)
        with (
            patch(
                f"{_MODULE}.litellm.completion",
                side_effect=BadRequestError("Invalid request: missing required field"),
            ) as mock_completion,
            patch("time.sleep") as mock_sleep,
            pytest.raises(NonRetryableLLMError) as exc_info,
        ):
            client.complete("system", "user")

        assert exc_info.value.classification.reason == LLMErrorReason.INVALID_REQUEST
        mock_completion.assert_called_once()
        mock_sleep.assert_not_called()

    def test_authentication_error_fails_on_first_attempt(self):
        client = _make_client(max_retries=3)
        with (
            patch(
                f"{_MODULE}.litellm.completion",
                side_effect=AuthenticationError("Invalid API key provided"),
            ) as mock_completion,
            patch("time.sleep") as mock_sleep,
            pytest.raises(NonRetryableLLMError) as exc_info,
        ):
            client.complete("system", "user")

        assert exc_info.value.classification.reason == LLMErrorReason.AUTHENTICATION
        mock_completion.assert_called_once()
        mock_sleep.assert_not_called()

    def test_permission_denied_fails_on_first_attempt(self):
        client = _make_client(max_retries=3)
        with (
            patch(
                f"{_MODULE}.litellm.completion",
                side_effect=PermissionDeniedError("Access to this resource is denied"),
            ) as mock_completion,
            patch("time.sleep") as mock_sleep,
            pytest.raises(NonRetryableLLMError) as exc_info,
        ):
            client.complete("system", "user")

        assert exc_info.value.classification.reason == LLMErrorReason.PERMISSION_DENIED
        mock_completion.assert_called_once()
        mock_sleep.assert_not_called()

    def test_invalid_model_fails_on_first_attempt(self):
        client = _make_client(max_retries=3)
        with (
            patch(
                f"{_MODULE}.litellm.completion",
                side_effect=NotFoundError("The model 'gpt-nonexistent' does not exist"),
            ) as mock_completion,
            patch("time.sleep") as mock_sleep,
            pytest.raises(NonRetryableLLMError) as exc_info,
        ):
            client.complete("system", "user")

        assert exc_info.value.classification.reason == LLMErrorReason.INVALID_MODEL
        mock_completion.assert_called_once()
        mock_sleep.assert_not_called()

    def test_context_length_exceeded_fails_on_first_attempt(self):
        client = _make_client(max_retries=3)
        with (
            patch(
                f"{_MODULE}.litellm.completion",
                side_effect=ContextWindowExceededError(
                    "This model's maximum context length is 128000 tokens — context_length_exceeded"
                ),
            ) as mock_completion,
            patch("time.sleep") as mock_sleep,
            pytest.raises(NonRetryableLLMError) as exc_info,
        ):
            client.complete("system", "user")

        assert exc_info.value.classification.reason == LLMErrorReason.CONTEXT_LENGTH_EXCEEDED
        mock_completion.assert_called_once()
        mock_sleep.assert_not_called()

    def test_billing_exhaustion_fails_fast_with_actionable_message(self):
        """Billing/quota depletion is a special-cased permanent failure even
        though a 429 would otherwise look like a transient rate limit — see
        _complete_impl's _BILLING_EXHAUSTED_MSGS. Raised as
        CreditBalanceExhaustedError (a BaseException, like CircuitBreakerError)
        rather than a plain RuntimeError, so a Celery task boundary can abort
        the whole run instead of just failing this one call — see
        app/workers/_task_helpers.py's CreditBalanceExhaustedTaskFailure."""
        client = _make_client(max_retries=3)
        with (
            patch(
                f"{_MODULE}.litellm.completion",
                side_effect=Exception("RESOURCE_EXHAUSTED: insufficient_quota for this project"),
            ) as mock_completion,
            patch("time.sleep") as mock_sleep,
            pytest.raises(CreditBalanceExhaustedError, match="billing/credits exhausted"),
        ):
            client.complete("system", "user")

        mock_completion.assert_called_once()
        mock_sleep.assert_not_called()

    def test_anthropic_credit_balance_too_low_fails_fast(self):
        """Real-world Anthropic wording ("Your credit balance is too low to
        access the Anthropic API") must also be classified as billing
        exhaustion — it doesn't match any of the other provider-specific
        phrases (Gemini/OpenAI/DeepSeek), so needs its own explicit match in
        _BILLING_EXHAUSTED_MSGS."""
        client = _make_client(max_retries=3)
        with (
            patch(
                f"{_MODULE}.litellm.completion",
                side_effect=Exception(
                    "AnthropicException - {\"type\":\"error\",\"error\":{\"type\":"
                    "\"invalid_request_error\",\"message\":\"Your credit balance is "
                    "too low to access the Anthropic API. Please go to Plans & "
                    "Billing to upgrade or purchase credits.\"}}"
                ),
            ) as mock_completion,
            patch("time.sleep") as mock_sleep,
            pytest.raises(CreditBalanceExhaustedError, match="billing/credits exhausted"),
        ):
            client.complete("system", "user")

        mock_completion.assert_called_once()
        mock_sleep.assert_not_called()

    def test_content_policy_filter_fails_fast_without_retry(self):
        """Empty content from a content-policy filter is a permanent outcome,
        distinct from an ordinary empty/malformed response (which IS
        retried) — see _complete_impl's finish_reason handling."""
        client = _make_client(max_retries=3)
        with (
            patch(
                f"{_MODULE}.litellm.completion",
                return_value=_response(content=None, finish_reason="content_filter"),
            ) as mock_completion,
            patch("time.sleep") as mock_sleep,
            pytest.raises(RuntimeError, match="content policy filter"),
        ):
            client.complete("system", "user")

        mock_completion.assert_called_once()
        mock_sleep.assert_not_called()
