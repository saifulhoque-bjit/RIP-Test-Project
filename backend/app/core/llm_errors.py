"""Centralized LLM provider error classification.

Single source of truth for deciding whether an exception raised by an LLM
provider call (OpenAI, Anthropic, Google Gemini, DeepSeek) is worth retrying
or should fail the pipeline immediately. Shared by:

- The RFP LangGraph pipeline (``app/services/rfp_pipeline_v2_graph_service/``),
  which raises :class:`NonRetryableLLMError` from a node to bypass LangGraph's
  ``RetryPolicy`` (its retry wrapper only ever catches ``Exception``, never
  ``BaseException`` — see ``langgraph/pregel/_retry.py``).
- The source-code pipeline's LLM gateway
  (``app/services/source_code_pipeline/src/ai/llm_client.py``), which uses
  this module's table lookup for its own permanent-vs-transient decision
  while keeping its own exception types and retry-ladder control flow.

Classification is intentionally fail-open: an exception that doesn't match
any known non-retryable pattern is classified ``retryable=True`` /
``UNKNOWN`` rather than guessed as fatal — a wrong "retryable" guess costs an
extra attempt or two, but a wrong "non-retryable" guess kills a pipeline run
that a normal retry would have recovered.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from langchain_core.exceptions import ContextOverflowError

from app.core.messages import (
    MSG_LLM_AUTHENTICATION,
    MSG_LLM_CONTENT_POLICY,
    MSG_LLM_CONTEXT_LENGTH_EXCEEDED,
    MSG_LLM_CREDIT_EXHAUSTED,
    MSG_LLM_INVALID_MODEL,
    MSG_LLM_INVALID_REQUEST,
    MSG_LLM_PERMISSION_DENIED,
)


class LLMErrorReason(str, Enum):
    CREDIT_EXHAUSTED = "credit_exhausted"
    AUTHENTICATION = "authentication"
    INVALID_MODEL = "invalid_model"
    INVALID_REQUEST = "invalid_request"
    CONTEXT_LENGTH_EXCEEDED = "context_length_exceeded"
    CONTENT_POLICY = "content_policy"
    PERMISSION_DENIED = "permission_denied"
    RATE_LIMIT = "rate_limit"
    SERVER_ERROR = "server_error"
    CONNECTION_ERROR = "connection_error"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


# Reasons that mean "retrying the identical request cannot succeed".
NON_RETRYABLE_REASONS = frozenset(
    {
        LLMErrorReason.CREDIT_EXHAUSTED,
        LLMErrorReason.AUTHENTICATION,
        LLMErrorReason.INVALID_MODEL,
        LLMErrorReason.INVALID_REQUEST,
        LLMErrorReason.CONTEXT_LENGTH_EXCEEDED,
        LLMErrorReason.CONTENT_POLICY,
        LLMErrorReason.PERMISSION_DENIED,
    }
)

_USER_MESSAGE_BY_REASON: dict[LLMErrorReason, str] = {
    LLMErrorReason.CREDIT_EXHAUSTED: MSG_LLM_CREDIT_EXHAUSTED,
    LLMErrorReason.AUTHENTICATION: MSG_LLM_AUTHENTICATION,
    LLMErrorReason.PERMISSION_DENIED: MSG_LLM_PERMISSION_DENIED,
    LLMErrorReason.INVALID_MODEL: MSG_LLM_INVALID_MODEL,
    LLMErrorReason.INVALID_REQUEST: MSG_LLM_INVALID_REQUEST,
    LLMErrorReason.CONTEXT_LENGTH_EXCEEDED: MSG_LLM_CONTEXT_LENGTH_EXCEEDED,
    LLMErrorReason.CONTENT_POLICY: MSG_LLM_CONTENT_POLICY,
}


@dataclass(frozen=True)
class LLMErrorClassification:
    retryable: bool
    reason: LLMErrorReason
    provider: str | None
    message: str
    user_message: str


class NonRetryableLLMError(BaseException):
    """Signals a permanent (deterministic) LLM provider failure.

    Deliberately subclasses ``BaseException`` (not ``Exception``) — like
    ``SystemExit``/``KeyboardInterrupt``, and like this codebase's existing
    ``CreditBalanceExhaustedError``/``CircuitBreakerError`` in the
    source-code pipeline's ``llm_client.py`` — so it passes straight through
    any broad ``except Exception`` handler in a pipeline node, and is never
    seen by LangGraph's node-level ``RetryPolicy`` (which only ever catches
    ``Exception``). Raising this from an RFP LangGraph node aborts that
    node's retries and propagates out of the compiled graph immediately.
    """

    def __init__(
        self,
        classification: LLMErrorClassification | str,
        *,
        original: Exception | None = None,
    ) -> None:
        if isinstance(classification, str):
            classification = LLMErrorClassification(
                retryable=False,
                reason=LLMErrorReason.UNKNOWN,
                provider=None,
                message=classification,
                user_message=classification,
            )
        self.classification = classification
        self.original = original
        super().__init__(classification.message)

    def __str__(self) -> str:
        return self.classification.message


class CreditBalanceExhaustedError(NonRetryableLLMError):
    """``NonRetryableLLMError`` specific to provider billing/credit/quota
    exhaustion (:attr:`LLMErrorReason.CREDIT_EXHAUSTED`) — kept as a distinct
    subclass so callers can catch this specific, actionable condition."""

    def __init__(
        self,
        classification: LLMErrorClassification | str = "",
        *,
        original: Exception | None = None,
    ) -> None:
        if isinstance(classification, str):
            classification = LLMErrorClassification(
                retryable=False,
                reason=LLMErrorReason.CREDIT_EXHAUSTED,
                provider=None,
                message=classification or MSG_LLM_CREDIT_EXHAUSTED,
                user_message=MSG_LLM_CREDIT_EXHAUSTED,
            )
        super().__init__(classification, original=original)


# ── Classification tables ───────────────────────────────────────────────
# Ported from the source-code pipeline's proven, provider-agnostic
# litellm-normalized-exception classifier (app/services/source_code_pipeline/
# src/ai/llm_client.py) — kept verbatim where possible so both pipelines
# agree on what counts as retryable.

_BILLING_EXHAUSTED_MSGS = (
    "credits are depleted",
    "prepayment credit",
    "insufficient balance",
    "insufficient_quota",
    "insufficient funds",
    "out of credits",
    "exceeded your current quota",
    "check your plan and billing",
    "billing hard limit",
    "quota has been exhausted",
    "credit balance is too low",
    "credit balance too low",
    "upgrade or purchase credits",
    "plans & billing",
)

# Some providers (observed: DeepSeek) surface a TRANSIENT server-side
# timeout/capacity failure as an HTTP 400 BadRequestError rather than a
# 5xx/timeout class. These carry an unmistakable "try again later" style
# message — detected explicitly so the permanent-type check below doesn't
# suppress a legitimate retry.
_WRAPPED_TRANSIENT_MSGS = (
    "unable to start processing",
    "timeout limit",
    "please try again later",
    "try again later",
)

_PERM_REASON_BY_TYPE: tuple[tuple[str, LLMErrorReason], ...] = (
    ("authentication", LLMErrorReason.AUTHENTICATION),
    ("permissiondenied", LLMErrorReason.PERMISSION_DENIED),
    ("contentpolicy", LLMErrorReason.CONTENT_POLICY),
    ("notfound", LLMErrorReason.INVALID_MODEL),
    ("contextwindow", LLMErrorReason.CONTEXT_LENGTH_EXCEEDED),
    ("badrequest", LLMErrorReason.INVALID_REQUEST),
    ("unprocessable", LLMErrorReason.INVALID_REQUEST),
)

_TRANSIENT_TYPES = (
    "internalservererror",
    "badgateway",
    "serviceunavailable",
    "ratelimit",
    "timeout",
    "apiconnection",
    "apitimeout",
    "serverdisconnected",
    "overloaded",
)
_TRANSIENT_CODES = ("500", "502", "503", "504", "529", "429")
_TRANSIENT_MSGS = (
    "timeout",
    "timed out",
    "overloaded",
    "temporarily unavailable",
    "unavailable",
    "peer closed connection",
    "incomplete chunked read",
    "incomplete read",
    "connection reset",
    "connection aborted",
    "connection error",
    "remotedisconnected",
    "server disconnected",
    "eof occurred",
    "reset by peer",
    "try again",
)

# Google/Gemini `ClientError.status` values (google.genai.errors), mapped to
# a reason. Anything not listed here falls through to the generic tables.
_GOOGLE_STATUS_REASON: dict[str, tuple[bool, LLMErrorReason]] = {
    "RESOURCE_EXHAUSTED": (True, LLMErrorReason.RATE_LIMIT),
    "PERMISSION_DENIED": (False, LLMErrorReason.PERMISSION_DENIED),
    "UNAUTHENTICATED": (False, LLMErrorReason.AUTHENTICATION),
    "INVALID_ARGUMENT": (False, LLMErrorReason.INVALID_REQUEST),
    "NOT_FOUND": (False, LLMErrorReason.INVALID_MODEL),
    "UNAVAILABLE": (True, LLMErrorReason.SERVER_ERROR),
    "INTERNAL": (True, LLMErrorReason.SERVER_ERROR),
    "ABORTED": (True, LLMErrorReason.SERVER_ERROR),
    "DEADLINE_EXCEEDED": (True, LLMErrorReason.TIMEOUT),
}


def _reason_for_transient(exc_type_lower: str, exc_str_lower: str) -> LLMErrorReason:
    if "ratelimit" in exc_type_lower or "429" in exc_str_lower:
        return LLMErrorReason.RATE_LIMIT
    if "timeout" in exc_type_lower or "timeout" in exc_str_lower or "timed out" in exc_str_lower:
        return LLMErrorReason.TIMEOUT
    if any(k in exc_type_lower for k in ("apiconnection", "serverdisconnected")) or any(
        k in exc_str_lower
        for k in (
            "connection reset",
            "connection aborted",
            "connection error",
            "peer closed connection",
            "reset by peer",
        )
    ):
        return LLMErrorReason.CONNECTION_ERROR
    return LLMErrorReason.SERVER_ERROR


def _classify_google(exc: Exception, *, provider: str | None) -> LLMErrorClassification | None:
    """Google/Gemini-specific unwrap.

    ``langchain_google_genai`` wraps every 4xx ``google.genai.errors.ClientError``
    into a generic, attribute-less ``ChatGoogleGenerativeAIError`` — the real
    signal (``.status``/``.code``) survives only on ``exc.__cause__``. A raw
    5xx ``ServerError`` is never wrapped and propagates as-is. Returns
    ``None`` (defer to the generic tables) when `exc` isn't Google-shaped or
    its status isn't one we recognize.
    """
    try:
        from google.genai import errors as genai_errors  # noqa: PLC0415
    except Exception:
        return None

    if isinstance(exc, genai_errors.ServerError):
        return LLMErrorClassification(True, LLMErrorReason.SERVER_ERROR, provider, str(exc), "")

    type_name = type(exc).__name__
    if isinstance(exc, genai_errors.ClientError):
        cause = exc
    elif type_name in ("ChatGoogleGenerativeAIError", "GoogleGenerativeAIError"):
        cause = getattr(exc, "__cause__", None)
        if isinstance(cause, genai_errors.ServerError):
            return LLMErrorClassification(True, LLMErrorReason.SERVER_ERROR, provider, str(exc), "")
        if not isinstance(cause, genai_errors.ClientError):
            return None
    else:
        return None

    status = (cause.status or "").upper()
    mapping = _GOOGLE_STATUS_REASON.get(status)
    if mapping is None:
        return None
    retryable, reason = mapping
    user_message = "" if retryable else _USER_MESSAGE_BY_REASON.get(reason, "")
    return LLMErrorClassification(retryable, reason, provider, str(exc), user_message)


def classify_llm_error(exc: Exception, *, provider: str | None = None) -> LLMErrorClassification:
    """Classify an exception raised by an LLM provider call.

    Checks, in order: universal context-overflow (all 4 providers share
    ``langchain_core.exceptions.ContextOverflowError``), billing/credit
    exhaustion (message-based, provider-agnostic), Google/Gemini-specific
    status unwrapping, permanent type/message matches, transient
    type/code/message matches, and finally a safe retryable/UNKNOWN default.
    """
    if isinstance(exc, ContextOverflowError):
        return LLMErrorClassification(
            retryable=False,
            reason=LLMErrorReason.CONTEXT_LENGTH_EXCEEDED,
            provider=provider,
            message=str(exc),
            user_message=MSG_LLM_CONTEXT_LENGTH_EXCEEDED,
        )

    exc_str = str(exc)
    _s = exc_str.lower()
    _t = type(exc).__name__.lower()

    if any(m in _s for m in _BILLING_EXHAUSTED_MSGS):
        return LLMErrorClassification(
            retryable=False,
            reason=LLMErrorReason.CREDIT_EXHAUSTED,
            provider=provider,
            message=exc_str,
            user_message=MSG_LLM_CREDIT_EXHAUSTED,
        )

    google_classification = _classify_google(exc, provider=provider)
    if google_classification is not None:
        return google_classification

    looks_wrapped_transient = any(m in _s for m in _WRAPPED_TRANSIENT_MSGS)
    if not looks_wrapped_transient:
        for type_substr, reason in _PERM_REASON_BY_TYPE:
            if type_substr in _t:
                return LLMErrorClassification(
                    retryable=False,
                    reason=reason,
                    provider=provider,
                    message=exc_str,
                    user_message=_USER_MESSAGE_BY_REASON[reason],
                )
        if "invalid_api_key" in _s or "invalid api key" in _s:
            return LLMErrorClassification(
                False, LLMErrorReason.AUTHENTICATION, provider, exc_str, MSG_LLM_AUTHENTICATION
            )
        if "content_policy" in _s:
            return LLMErrorClassification(
                False, LLMErrorReason.CONTENT_POLICY, provider, exc_str, MSG_LLM_CONTENT_POLICY
            )
        if "context_length" in _s or "context window" in _s:
            return LLMErrorClassification(
                False,
                LLMErrorReason.CONTEXT_LENGTH_EXCEEDED,
                provider,
                exc_str,
                MSG_LLM_CONTEXT_LENGTH_EXCEEDED,
            )

    if (
        any(x in _t for x in _TRANSIENT_TYPES)
        or any(c in exc_str for c in _TRANSIENT_CODES)
        or any(m in _s for m in _TRANSIENT_MSGS)
    ):
        return LLMErrorClassification(
            retryable=True,
            reason=_reason_for_transient(_t, _s),
            provider=provider,
            message=exc_str,
            user_message="",
        )

    return LLMErrorClassification(
        retryable=True,
        reason=LLMErrorReason.UNKNOWN,
        provider=provider,
        message=exc_str,
        user_message="",
    )
