"""Tests for the centralized LLM error classifier (app/core/llm_errors.py).

Covers all 4 providers' distinct exception shapes (OpenAI, Anthropic,
DeepSeek via OpenAI-compatible SDK, Google/Gemini's wrapped-cause shape) and
verifies the fail-open default (an unrecognized exception stays retryable).
"""

from __future__ import annotations

from google.genai import errors as genai_errors
import httpx
from langchain_anthropic.chat_models import AnthropicContextOverflowError
from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError
from langchain_openai.chat_models.base import OpenAIContextOverflowError
import openai
import pytest

from app.core.llm_errors import (
    CreditBalanceExhaustedError,
    LLMErrorReason,
    NonRetryableLLMError,
    classify_llm_error,
)


def _response(status_code: int = 400) -> httpx.Response:
    return httpx.Response(status_code, request=httpx.Request("POST", "https://example.com"))


class TestContextOverflow:
    def test_openai_context_overflow_is_non_retryable(self):
        exc = OpenAIContextOverflowError("too many tokens", response=_response(400), body=None)
        result = classify_llm_error(exc, provider="openai")
        assert result.retryable is False
        assert result.reason == LLMErrorReason.CONTEXT_LENGTH_EXCEEDED

    def test_anthropic_context_overflow_is_non_retryable(self):
        exc = AnthropicContextOverflowError("prompt is too long", response=_response(400), body=None)
        result = classify_llm_error(exc, provider="anthropic")
        assert result.retryable is False
        assert result.reason == LLMErrorReason.CONTEXT_LENGTH_EXCEEDED

    def test_google_context_overflow_is_non_retryable(self):
        from langchain_google_genai.chat_models import GoogleContextOverflowError

        exc = GoogleContextOverflowError(
            code=400,
            response_json={"error": {"message": "exceeds the maximum number of tokens allowed"}},
            response=None,
        )
        result = classify_llm_error(exc, provider="google")
        assert result.retryable is False
        assert result.reason == LLMErrorReason.CONTEXT_LENGTH_EXCEEDED


class TestBillingExhaustion:
    def test_anthropic_credit_balance_message_is_non_retryable(self):
        exc = openai.BadRequestError(
            "Your credit balance is too low to access the Anthropic API. Please go to "
            "Plans & Billing to upgrade or purchase credits.",
            response=_response(400),
            body=None,
        )
        result = classify_llm_error(exc, provider="anthropic")
        assert result.retryable is False
        assert result.reason == LLMErrorReason.CREDIT_EXHAUSTED
        assert result.user_message

    def test_openai_insufficient_quota_is_non_retryable(self):
        exc = openai.RateLimitError(
            "You exceeded your current quota, please check your plan and billing details.",
            response=_response(429),
            body=None,
        )
        result = classify_llm_error(exc, provider="openai")
        assert result.retryable is False
        assert result.reason == LLMErrorReason.CREDIT_EXHAUSTED

    def test_google_billing_message_embedded_in_wrapper_is_non_retryable(self):
        cause = genai_errors.ClientError(
            429,
            {"error": {"message": "prepayment credits are depleted", "status": "RESOURCE_EXHAUSTED"}},
        )
        try:
            raise ChatGoogleGenerativeAIError(f"Error calling model 'gemini' ({cause.status}): {cause}") from cause
        except ChatGoogleGenerativeAIError as exc:
            result = classify_llm_error(exc, provider="google")
        assert result.retryable is False
        assert result.reason == LLMErrorReason.CREDIT_EXHAUSTED


class TestPermanentErrors:
    def test_authentication_error_is_non_retryable(self):
        exc = openai.AuthenticationError("Invalid API key", response=_response(401), body=None)
        result = classify_llm_error(exc, provider="openai")
        assert result.retryable is False
        assert result.reason == LLMErrorReason.AUTHENTICATION

    def test_permission_denied_is_non_retryable(self):
        exc = openai.PermissionDeniedError("Access denied", response=_response(403), body=None)
        result = classify_llm_error(exc, provider="openai")
        assert result.retryable is False
        assert result.reason == LLMErrorReason.PERMISSION_DENIED

    def test_not_found_model_is_non_retryable(self):
        exc = openai.NotFoundError("model not found", response=_response(404), body=None)
        result = classify_llm_error(exc, provider="openai")
        assert result.retryable is False
        assert result.reason == LLMErrorReason.INVALID_MODEL

    def test_bad_request_is_non_retryable(self):
        exc = openai.BadRequestError("invalid parameter", response=_response(400), body=None)
        result = classify_llm_error(exc, provider="deepseek")
        assert result.retryable is False
        assert result.reason == LLMErrorReason.INVALID_REQUEST

    def test_deepseek_wrapped_transient_400_stays_retryable(self):
        exc = openai.BadRequestError("please try again later", response=_response(400), body=None)
        result = classify_llm_error(exc, provider="deepseek")
        assert result.retryable is True


class TestGoogleStatusMapping:
    def _wrapped(self, status: str, code: int, message: str) -> ChatGoogleGenerativeAIError:
        cause = genai_errors.ClientError(code, {"error": {"message": message, "status": status}})
        try:
            raise ChatGoogleGenerativeAIError(f"Error calling model 'gemini' ({cause.status}): {cause}") from cause
        except ChatGoogleGenerativeAIError as exc:
            return exc

    def test_permission_denied_status_is_non_retryable(self):
        exc = self._wrapped("PERMISSION_DENIED", 403, "no access")
        result = classify_llm_error(exc, provider="google")
        assert result.retryable is False
        assert result.reason == LLMErrorReason.PERMISSION_DENIED

    def test_unauthenticated_status_is_non_retryable(self):
        exc = self._wrapped("UNAUTHENTICATED", 401, "bad api key")
        result = classify_llm_error(exc, provider="google")
        assert result.retryable is False
        assert result.reason == LLMErrorReason.AUTHENTICATION

    def test_invalid_argument_status_is_non_retryable(self):
        exc = self._wrapped("INVALID_ARGUMENT", 400, "bad param")
        result = classify_llm_error(exc, provider="google")
        assert result.retryable is False
        assert result.reason == LLMErrorReason.INVALID_REQUEST

    def test_unavailable_status_is_retryable(self):
        exc = self._wrapped("UNAVAILABLE", 503, "server busy")
        result = classify_llm_error(exc, provider="google")
        assert result.retryable is True
        assert result.reason == LLMErrorReason.SERVER_ERROR

    def test_raw_server_error_is_retryable(self):
        exc = genai_errors.ServerError(500, {"error": {"message": "internal error", "status": "INTERNAL"}})
        result = classify_llm_error(exc, provider="google")
        assert result.retryable is True
        assert result.reason == LLMErrorReason.SERVER_ERROR


class TestTransientErrors:
    def test_rate_limit_is_retryable(self):
        exc = openai.RateLimitError("rate limited", response=_response(429), body=None)
        result = classify_llm_error(exc, provider="openai")
        assert result.retryable is True
        assert result.reason == LLMErrorReason.RATE_LIMIT

    def test_service_unavailable_is_retryable(self):
        exc = openai.APIStatusError("service unavailable", response=_response(503), body=None)
        result = classify_llm_error(exc, provider="openai")
        assert result.retryable is True
        assert result.reason == LLMErrorReason.SERVER_ERROR

    def test_timeout_is_retryable(self):
        exc = openai.APITimeoutError(request=httpx.Request("POST", "https://example.com"))
        result = classify_llm_error(exc, provider="openai")
        assert result.retryable is True
        assert result.reason == LLMErrorReason.TIMEOUT

    def test_connection_reset_message_is_retryable(self):
        exc = RuntimeError("connection reset by peer")
        result = classify_llm_error(exc, provider="openai")
        assert result.retryable is True
        assert result.reason == LLMErrorReason.CONNECTION_ERROR


class TestUnknownDefault:
    def test_unrecognized_exception_defaults_to_retryable_unknown(self):
        result = classify_llm_error(ValueError("something odd happened"), provider="openai")
        assert result.retryable is True
        assert result.reason == LLMErrorReason.UNKNOWN


class TestNonRetryableLLMError:
    def test_carries_classification_and_original(self):
        original = openai.AuthenticationError("Invalid API key", response=_response(401), body=None)
        classification = classify_llm_error(original, provider="openai")
        exc = NonRetryableLLMError(classification, original=original)
        assert exc.classification is classification
        assert exc.original is original
        assert str(exc) == classification.message

    def test_is_a_base_exception_not_an_exception(self):
        assert issubclass(NonRetryableLLMError, BaseException)
        assert not issubclass(NonRetryableLLMError, Exception)

    def test_bypasses_a_bare_except_exception_handler(self):
        original = openai.AuthenticationError("Invalid API key", response=_response(401), body=None)
        classification = classify_llm_error(original, provider="openai")

        def _raise():
            raise NonRetryableLLMError(classification, original=original)

        with pytest.raises(NonRetryableLLMError):
            try:
                _raise()
            except Exception:
                pytest.fail("NonRetryableLLMError must not be caught by except Exception")


class TestCreditBalanceExhaustedError:
    def test_string_constructor_backward_compatible(self):
        exc = CreditBalanceExhaustedError("Your credit balance is too low")
        assert isinstance(exc, NonRetryableLLMError)
        assert exc.classification.reason == LLMErrorReason.CREDIT_EXHAUSTED
        assert str(exc) == "Your credit balance is too low"

    def test_from_classification(self):
        original = openai.RateLimitError(
            "You exceeded your current quota, please check your plan and billing details.",
            response=_response(429),
            body=None,
        )
        classification = classify_llm_error(original, provider="openai")
        exc = CreditBalanceExhaustedError(classification, original=original)
        assert exc.classification.reason == LLMErrorReason.CREDIT_EXHAUSTED
