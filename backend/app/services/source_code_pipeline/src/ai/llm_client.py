"""
LLMClient: Provider-agnostic LLM gateway powered by litellm.

OVERVIEW
--------
Drop-in replacement for direct ``openai.OpenAI()`` usage across the RIP pipeline.
Reads the active provider and model from ``project_config.json["llm"]`` so that
switching providers requires zero code changes — only a config edit.

SUPPORTED PROVIDERS (out of the box)
--------------------------------------
  openai    — GPT-4o, GPT-5, o1, o3  (env: OPENAI_API_KEY)
  anthropic — Claude 3.5/3.7 Sonnet  (env: ANTHROPIC_API_KEY)
  gemini    — Gemini 2.0/2.5         (env: GEMINI_API_KEY)
  deepseek  — DeepSeek Chat/Reasoner  (env: DEEPSEEK_API_KEY)

USAGE
-----
    from src.ai.llm_client import LLMClient

    # Basic completion (free-form text, e.g. markdown blueprints)
    client = LLMClient()
    text = client.complete(system_prompt, user_prompt)

    # JSON object output
    text = client.complete(system_prompt, user_prompt,
                           response_format={"type": "json_object"})

    # Structured output (json_schema — falls back to json_object for non-OpenAI)
    text = client.complete(system_prompt, user_prompt,
                           response_format={"type": "json_schema",
                                            "json_schema": schema_dict})

    # Use the provider's reasoning model for this call
    text = client.complete(system_prompt, user_prompt,
                           use_reasoning_model=True)

CONFIGURATION
-------------
Add (or edit) the "llm" key in project_config.json.  See project_config.json
for a fully commented example of every supported provider and parameter.
"""

import contextlib
import json
import logging
import os
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional

# Suppress LiteLLM's pre-load warnings for unused AWS providers (Bedrock / SageMaker).
# These fire at *import time* when botocore is not installed — they are harmless but
# confusing.  Setting LITELLM_LOG and the Python logger level BEFORE the import
# prevents them from reaching the console entirely.
os.environ.setdefault("LITELLM_LOG", "ERROR")
logging.getLogger("LiteLLM").setLevel(logging.ERROR)

# ── CRITICAL: force litellm to use its BUNDLED (local) model-cost/context map ──
# By default litellm fetches its price/context map over the network on first use
# (GET https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_
# context_window.json). This fetch is NOT bounded by the per-call `timeout` kwarg
# (that only covers the LLM API request) and happens BEFORE the request is sent —
# especially for model strings litellm doesn't know (e.g. deepseek-v4-pro). In a
# container/corporate network where raw.githubusercontent.com is slow or firewalled
# with no TCP reset, that GET blocks indefinitely and the whole run hangs with NO
# LLM request ever made — the exact "request not coming / hangs" symptom seen in the
# enrich and SRS stages. Forcing the local map (set BEFORE `import litellm`) makes
# litellm load the bundled backup instantly with zero network I/O. Override by
# exporting LITELLM_LOCAL_MODEL_COST_MAP=False only if you have reliable network AND
# want live pricing.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

try:
    import litellm

    litellm.suppress_debug_info = True  # silence per-call verbose/debug noise
    litellm.drop_params = True  # silently drop unsupported params per provider
    # Belt-and-suspenders: never let litellm's own remote map fetch run even if the
    # env var above was somehow cleared. (No-op if the attribute isn't present.)
    try:
        litellm.model_cost  # touch to ensure the local map is materialised now
    except Exception:
        pass
    LITELLM_AVAILABLE = True
except ImportError:
    LITELLM_AVAILABLE = False

# ─────────────────────────────────────────────────────────────────────────────
# Streaming helpers (Anthropic non-streaming ~10-min ceiling mitigation)
#
# Anthropic validates that a NON-streaming request will not exceed a ~10-minute
# timeout; long generations (large output + adaptive thinking) must stream. These
# helpers let the blocking watchdog path stream internally and hand the rest of the
# client an object identical in shape to a non-streaming response — so extraction,
# finish_reason handling and cost metering are unchanged.
#
# Design notes / gaps guarded (see STREAMING_TEST_PLAN.md §7):
#   * §7.1 thinking-exclusion: we read choices[0].message.content, into which
#     litellm places ONLY the answer text; reasoning arrives as reasoning_content
#     on a separate field, so thinking never leaks into the spec.
#   * §7.2 finish_reason: litellm maps Anthropic 'max_tokens' -> 'length' (verified
#     on 1.85.1), so the existing truncation guard fires unchanged.
#   * §7.3 cumulative usage: stream_chunk_builder takes the final cumulative usage
#     chunk (not a sum); verified populated on 1.85.1.
#   * §7.4 connection cleanup: the iterator is always closed in a finally block so
#     an abandoned/errored stream cannot leak the HTTP connection.
# ─────────────────────────────────────────────────────────────────────────────
def _is_request_cancelled(request_id: Optional[str]) -> bool:
    """Best-effort cancellation check usable from any thread.

    Unlike ``_raise_if_run_cancelled``, this takes *request_id* explicitly
    rather than reading it off the ``_RUN_CANCEL`` thread-local — the
    thread-local is only ever set on the thread that entered
    ``pipeline_run_guard``, so it reads as empty from a spawned watchdog
    thread (see ``_completion_with_watchdog``). Fails open (returns False) on
    a Redis error or when the app package isn't importable (standalone/CLI
    use), same trade-off as ``_raise_if_run_cancelled``."""
    if not request_id:
        return False
    try:
        from app.core import task_control  # noqa: PLC0415
    except Exception:
        return False
    return task_control.is_request_cancelled(request_id)


def _stream_and_rebuild(
    kwargs: Dict[str, Any], request_id: Optional[str] = None, poll_every: int = 5
) -> Any:
    """Run ``litellm.completion(stream=True, ...)``, drain the chunk iterator, and
    reassemble a single non-streaming-shaped response via ``stream_chunk_builder``.

    Consumed synchronously by the caller (inside the watchdog thread) so the hard
    wall-clock ceiling still bounds the *whole* generation, not just iterator
    creation. The iterator is closed unconditionally so a stalled/abandoned stream
    releases its socket.

    When *request_id* is supplied, polls cancellation every *poll_every* chunks
    (same cadence as ``app.utils.cancellable_llm``) and aborts the stream via
    ``PipelineRunCancelled`` as soon as the run is seen cancelled — without
    this, a cancelled run's in-flight stream drains unconditionally until the
    provider finishes or the watchdog ceiling fires, since this function may
    run on a daemon thread where the usual thread-local cancel check is blind
    (see ``_is_request_cancelled``)."""
    stream = litellm.completion(**kwargs)
    chunks: List[Any] = []
    n = 0
    try:
        for chunk in stream:
            chunks.append(chunk)
            n += 1
            if (n == 1 or n % poll_every == 0) and _is_request_cancelled(request_id):
                raise PipelineRunCancelled(
                    f"[LLMClient] Run cancelled (request_id={request_id}) — aborting "
                    f"mid-stream after {n} chunk(s). No further LLM calls will be made "
                    "for this run."
                )
    finally:
        _close = getattr(stream, "close", None)
        if callable(_close):
            try:
                _close()
            except Exception:
                pass
    if not chunks:
        return None
    return litellm.stream_chunk_builder(chunks, messages=kwargs.get("messages"))


def _ensure_usage(response: Any, model: str, messages: Optional[List[Dict[str, Any]]]) -> Any:
    """Guarantee the response carries a non-zero ``usage`` so cost/token accounting
    can never silently read zero under streaming (STREAMING_TEST_PLAN.md §7.5 blind
    meter). If the reassembled response already has usage.total_tokens > 0 it is
    returned untouched; otherwise usage is estimated with ``token_counter`` and the
    response is flagged ``_usage_estimated = True``."""
    if response is None:
        return response
    u = getattr(response, "usage", None)
    if u is not None and getattr(u, "total_tokens", 0):
        return response
    try:
        prompt_text = "".join(
            m.get("content", "") for m in (messages or [])
            if isinstance(m.get("content"), str)
        )
        content = ""
        try:
            content = response.choices[0].message.content or ""
        except Exception:
            content = ""
        pt = litellm.token_counter(model=model, text=prompt_text) if prompt_text else 0
        ct = litellm.token_counter(model=model, text=content) if content else 0
        from litellm.types.utils import Usage as _Usage
        response.usage = _Usage(
            prompt_tokens=pt, completion_tokens=ct, total_tokens=pt + ct
        )
        setattr(response, "_usage_estimated", True)
        print(
            f"[LLMClient] [USAGE FALLBACK] streamed response carried no usage; "
            f"estimated via token_counter (prompt={pt}, completion={ct}). "
            f"Cost metering will not read zero."
        )
    except Exception:
        pass  # non-fatal: breaker's timeout/wall-clock triggers still apply
    return response


class _RetryableResponseError(Exception):
    """Internal marker for a well-formed API call that returned an unusable
    response body — empty/null content, or a malformed envelope (no choices /
    no message). These are TRANSIENT provider glitches (observed on DeepSeek:
    HTTP 200 with finish_reason='stop' but content_len=0) and should be retried
    with back-off, not failed hard. Kept distinct from RuntimeError so the
    permanent 'our own diagnostics' re-raise path does not swallow the retry."""

    pass


class LLMTimeoutExhausted(RuntimeError):
    """Raised by _complete_impl when a call fails due to a TIMEOUT and its
    (small) timeout retry budget is exhausted. Distinct from the generic
    'Call failed' RuntimeError so the wrapper can count ONLY timeouts toward the
    circuit-breaker trip — a non-timeout failure (overflow, bad request, a lone
    non-timeout blip) fails that item loud but never aborts the run. Still a
    RuntimeError, so existing callers that catch RuntimeError/Exception behave
    exactly as before."""

    pass


class CircuitBreakerError(BaseException):
    """Raised when the process-level LLM circuit-breaker trips: too many
    consecutive failures, cumulative estimated spend over budget, or the run
    wall-clock exceeded. A deliberate, run-level ABORT whose sole job is to stop
    a runaway run (the weekend $650 timeout-storm incident) regardless of
    per-call retry caps.

    Subclasses **BaseException** (not Exception) on purpose — like SystemExit /
    KeyboardInterrupt — so the many broad ``except Exception`` handlers across the
    pipeline (call_llm_api, per-module run loops, ai_runner) do NOT swallow it
    and let the run limp on. It propagates straight to the CLI entry point, which
    catches it explicitly and exits cleanly. The retry loop never retries it
    because none of its ``except`` clauses match a BaseException."""

    pass


class CreditBalanceExhaustedError(BaseException):
    """Raised when a provider rejects a call because the account's API
    credit balance/quota is exhausted (e.g. Anthropic's "Your credit balance
    is too low to access the Anthropic API", OpenAI's ``insufficient_quota``,
    DeepSeek's "insufficient balance"). Unlike a generic permanent error
    (bad request, auth, content-policy — which only fails the one item),
    this is a PROVIDER-ACCOUNT-WIDE condition: every remaining LLM call in
    this run — this module, and every module still queued behind it — would
    fail identically, so this aborts the whole run immediately instead of
    grinding through every remaining module to hit the same wall one at a
    time.

    Subclasses **BaseException** (not Exception) for the same reason as
    :class:`CircuitBreakerError` — so the many broad ``except Exception``
    handlers across the pipeline (``call_llm_api``, per-module run loops,
    ``ai_runner``) do NOT swallow it into a false "failed module, continue"
    outcome. Never retried: it is raised only after the retry ladder in
    ``_complete_impl`` has already classified the error as a permanent
    billing/quota condition, so a retry would just burn time to hit the
    identical error again."""

    pass


class PipelineRunCancelled(BaseException):
    """Raised when the active pipeline run has been cancelled by the user.

    Checked immediately before every LLM call is issued, and again before
    each retry. It does **not** abort a call already in flight — that request
    is already paid for, and this deployment's blocking ``litellm.completion``
    cannot be interrupted anyway. What it stops is every *subsequent* call:
    once the flag is seen, no new request is sent and no timed-out request is
    retried.

    That distinction is the whole point. A single call can occupy
    ``request_timeout_seconds`` (600s) plus the watchdog margin, and the retry
    loop will do that up to ``max(max_retries, timeout_max_retries)`` times —
    roughly 35 minutes for one wedged call, before which cancellation checks
    at any stage boundary simply never get a chance to run. Bounding
    cancellation latency at one in-flight call requires stopping here.

    Subclasses **BaseException** for the same reason as
    :class:`CircuitBreakerError`, and it is not optional: the pipeline is full
    of broad ``except Exception`` handlers (``call_llm_api``, per-MFU loops,
    ``ai_runner``, the orchestrator's per-stage handlers) that would otherwise
    swallow this and let the cancelled run limp on to the next item — making
    the check useless. ``PipelineOrchestrator.process_single_module`` catches
    it explicitly and converts it to the ``{"status": "cancelled"}`` contract
    the Celery layer already understands.
    """


class LLMWatchdogTimeout(Exception):
    """Raised when the hard wall-clock watchdog abandons a ``litellm.completion``
    call that exceeded its ceiling — i.e. litellm/httpx failed to honour their own
    ``timeout``, or the stall was in pre-flight work the timeout does not cover
    (provider resolution, cost/context-map access, tokenizer init, a proxy-wedged
    socket). Message deliberately contains the word 'timeout' so the retry
    classifier in ``_complete_impl`` routes it through the normal timeout path
    (dedicated retry budget + circuit-breaker), exactly like a litellm.Timeout."""

    pass


# Count of watchdog worker threads that were ABANDONED (the call outlived its
# ceiling and we returned control without being able to kill the thread). Bounded
# so a truly wedged provider can't grow threads without limit; decremented when an
# abandoned call eventually finishes. Module-level: shared across LLMClient
# instances in the process.
_LIVE_WATCHDOGS = 0
_LIVE_WATCHDOGS_LOCK = threading.Lock()


class _CircuitBreaker:
    """Process-wide aggregate guard around LLM calls. Bounds a run with two
    INDEPENDENT triggers so a provider-wide stall or a runaway cost can never
    repeat the weekend incident:

        consecutive_timeout_failures >= max_consecutive_failures  -> abort
        est_cost_usd                 >= max_est_cost_usd          -> abort

    Thresholds come from project_config ``llm.circuit_breaker`` and are all
    configurable; set ``enabled: false`` to disable. State is MODULE-LEVEL (see
    the ``_BREAKER`` singleton), so it MUST be reset at the start of each pipeline
    run via ``reset()`` — otherwise counters/trip-flag leak across independent runs
    that share a process (the cross-project trip observed when two pipelines ran in
    one Celery worker). See ``LLMClient.reset_circuit_breaker()``.

    NOTE — the total-run WALL-CLOCK trigger was removed on purpose. A fixed
    elapsed-time cap cannot tell a healthy long run (our mid-size projects run
    36-48h) from a runaway, so it only ever produced false aborts; and its
    process-anchored ``start_time`` was a source of cross-run leakage. Per-call time
    is bounded by the request timeout + hard watchdog, and a genuine provider stall
    is caught by the consecutive-timeout trigger below — which needs no notion of
    total run time. That is the right, scale-independent protection.

    IMPORTANT — the consecutive trigger counts **TIMEOUTS ONLY**, and only for
    the stages that opt in (SRS / spec generation and feature/user-story
    generation, via ``LLMClient.set_timeout_breaker_scope(True)``). A non-timeout
    failure (overflow, bad request, a lone non-timeout blip) fails its item loud
    but never trips the breaker; a timeout in an un-scoped stage (module
    discovery, architecture) likewise doesn't. This is deliberate: the abort is
    reserved for the exact failure mode that caused the $650 — timeouts in the
    large-context generation stages.

    ``est_cost_usd`` is summed from SUCCESSFUL calls only (litellm cost map);
    it is disabled by default (max_est_cost_usd=0)."""

    def __init__(self):
        self.enabled = True
        self.max_consecutive_failures = 8
        self.max_est_cost_usd = 0.0
        self.consecutive_timeout_failures = 0
        self.est_cost_usd = 0.0
        self.tripped_reason = None

    def configure(self, llm_cfg: dict[str, Any]) -> None:
        cb = (llm_cfg or {}).get("circuit_breaker") or {}
        self.enabled = bool(cb.get("enabled", True))
        self.max_consecutive_failures = int(cb.get("max_consecutive_failures", 8) or 0)
        self.max_est_cost_usd = float(cb.get("max_est_cost_usd", 0.0) or 0.0)

    def reset(self) -> None:
        """Clear all RUN-SCOPED state. Call once at the start of each pipeline run
        (task entry) so a fresh run never inherits a prior run's failure count or a
        sticky trip — the leak that let one project's trip abort another project
        sharing the same worker process."""
        self.consecutive_timeout_failures = 0
        self.est_cost_usd = 0.0
        self.tripped_reason = None
        print(f"[LLMClient][Breaker] reset — {self._status_text()}")

    def _status_text(self) -> str:
        state = "OPEN" if self.tripped_reason else "CLOSED"
        cost_cap = f"${self.max_est_cost_usd:.2f}" if self.max_est_cost_usd else "disabled"
        return (
            f"state={state}, timeout_failures={self.consecutive_timeout_failures}/"
            f"{self.max_consecutive_failures or 'disabled'}, "
            f"estimated_cost=${self.est_cost_usd:.2f}/{cost_cap}"
        )

    def _trip(self, reason: str) -> None:
        self.tripped_reason = reason
        print(
            f"[LLMClient][Trip] Circuit-breaker TRIPPED — {reason}. "
            f"{self._status_text()}. Aborting the run."
        )
        raise CircuitBreakerError(
            f"[LLMClient] Circuit-breaker ABORTED the run — {reason}. This is a "
            f"safety stop to prevent runaway cost/time (see the weekend $650 "
            f"timeout-storm incident). Adjust llm.circuit_breaker thresholds or "
            f"fix the underlying provider issue, then re-run."
        )

    def check(self) -> None:
        """Call BEFORE each LLM call: re-trips fast if the breaker is already open."""
        if not self.enabled:
            return
        if self.tripped_reason:
            self._trip(self.tripped_reason)

    def record_success(self, response: Any) -> None:
        """Reset the consecutive-timeout counter and add this call's cost."""
        if not self.enabled:
            return
        had_timeout_failures = self.consecutive_timeout_failures
        self.consecutive_timeout_failures = 0
        try:
            import litellm as _ll

            c = _ll.completion_cost(completion_response=response)
            if c:
                self.est_cost_usd += float(c)
        except Exception:
            pass  # cost map miss is non-fatal; the other triggers still apply
        if had_timeout_failures:
            print(f"[LLMClient][Breaker] success reset timeout count — {self._status_text()}")
        if self.max_est_cost_usd and self.est_cost_usd >= self.max_est_cost_usd:
            self._trip(
                f"estimated spend ${self.est_cost_usd:.2f} >= ${self.max_est_cost_usd:.2f} cap"
            )

    def record_timeout_failure(self) -> None:
        """Count a fully-failed TIMEOUT from an opted-in stage; trip on the cap."""
        if not self.enabled:
            return
        self.consecutive_timeout_failures += 1
        if (
            self.max_consecutive_failures
            and self.consecutive_timeout_failures >= self.max_consecutive_failures
        ):
            self._trip(
                f"{self.consecutive_timeout_failures} consecutive LLM timeout(s) >= "
                f"{self.max_consecutive_failures} cap (provider stall in a "
                f"large-context generation stage)"
            )
        else:
            print(
                f"[LLMClient][Breaker] timeout failure recorded — {self._status_text()}"
            )


# Module-level singleton: shared by all LLMClient instances in this process.
_BREAKER = _CircuitBreaker()


class ConcurrentPipelineError(RuntimeError):
    """Raised when a second pipeline run tries to start in a process that is already
    running one. The pipeline relies on PROCESS-GLOBAL state (this circuit breaker,
    the plugin registry, etc.), so two pipelines in one process corrupt each other —
    the cross-project circuit-breaker trip we observed when two projects landed in
    the same Celery worker. Deliberately a plain ``Exception`` (not BaseException) so
    Celery ``autoretry_for=(Exception,)`` re-queues the rejected task onto another
    (idle) process. Fix the deployment to run ONE pipeline per process (prefork
    pool, one task per child)."""

    pass


# Tracks the single pipeline run allowed to be active in THIS process. Guarded by a
# lock; owner-thread aware so nested/re-entrant guard use on the same run is fine,
# while a genuinely concurrent second run (another thread/greenlet) is rejected.
_ACTIVE_RUN: dict[str, Any] = {"id": None, "owner": None, "depth": 0}
_ACTIVE_RUN_LOCK = threading.Lock()

# Cooperative-cancellation key for the run executing on THIS thread (see
# app/core/task_control.py), set by pipeline_run_guard. ``cancelled`` caches a
# positive result so once a run is known cancelled the unwind costs no further
# Redis round trips (the flag is sticky — it never goes back to False).
#
# Thread-local, NOT process-global like _ACTIVE_RUN, and deliberately so.
# _ACTIVE_RUN only assigns its fields when no run is currently active, because
# the second run is normally rejected outright. Under
# ``RIP_SINGLE_RUN_MODE=warn`` it is not rejected — it proceeds — and would
# then inherit the first run's cancel key: cancelling project A would abort
# project B's LLM calls, and B's own cancellation would never be seen. Keying
# this per thread makes that impossible regardless of the run-guard mode, and
# costs nothing (the check always runs on the thread that entered the guard).
_RUN_CANCEL = threading.local()


def _raise_if_run_cancelled(where: str) -> None:
    """Abort the run if the user cancelled it. No-op outside a guarded run.

    Deliberately reads the flag through the same Redis client every other
    checkpoint uses, so there is one source of truth. Fails open: a Redis
    error leaves the run going rather than killing work over a blip — the
    same trade ``task_control.is_request_cancelled`` already makes.
    """
    request_id = getattr(_RUN_CANCEL, "request_id", None)
    if not request_id:
        return
    if not getattr(_RUN_CANCEL, "cancelled", False):
        try:
            from app.core import task_control  # noqa: PLC0415
        except Exception:
            return  # standalone/CLI use without the app package importable
        if not task_control.is_request_cancelled(request_id):
            return
        _RUN_CANCEL.cancelled = True
    raise PipelineRunCancelled(
        f"[LLMClient] Run cancelled (request_id={request_id}) — skipping {where}. "
        "No further LLM calls will be made for this run."
    )


# ---------------------------------------------------------------------------
# Built-in provider defaults
# Used when project_config.json has no "llm" section, or as a merge base.
# ---------------------------------------------------------------------------
# Providers discovered AT RUNTIME to reject `response_format` (e.g. DeepSeek, which
# disabled it API-side). Populated by the self-healing path in complete() and consulted
# proactively on subsequent calls. Module-level so it persists across the per-call
# LLMClient instances within a single process run.
_RESPONSE_FORMAT_DISABLED: set = set()


_PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    # ── OpenAI ──────────────────────────────────────────────────────────────
    "openai": {
        "_env_key": "OPENAI_API_KEY",
        # GPT-5 is a reasoning model. model == reasoning_model so EVERY call is
        # classified reasoning (exact match) — RIP then uses max_completion_tokens
        # and sends NO temperature/top_p (GPT-5 returns HTTP 400 on non-default
        # sampling params, like Sonnet 5).
        "model": "gpt-5",
        "reasoning_model": "gpt-5",
        "reasoning_keywords": ["gpt-5"],
        # GPT-5 effort scale (fastest -> deepest). Base GPT-5 is
        # minimal|low|medium|high (API default medium); newer variants add
        # xhigh/max — set this list to match the exact model string in use.
        "reasoning_effort_levels": ["minimal", "low", "medium", "high"],
        # Hyper-parameters for the (unused) standard path.
        "temperature": 0.1,
        "max_tokens": 16384,
        # Reasoning-model output ceiling.
        "max_completion_tokens": 128000,
        "large_context_model": "gpt-5",
        "large_context_max_tokens": 128000,
        "large_context_input_threshold": 400000,  # chars; moot on the reasoning path
        # litellm unified reasoning knob → GPT-5 effort=high. A default is set so
        # the adaptive timeout ladder has a level to step DOWN from (high -> medium),
        # even though GPT-5's own API default is 'medium'.
        "_reasoning_params": {"reasoning_effort": "high"},
        "_notes": (
            "GPT-5 reasoning model. Effort scale minimal|low|medium|high (API "
            "default 'medium'; RIP sets 'high'). Rejects non-default temperature/"
            "top_p on the reasoning path — RIP suppresses them. Newer variants "
            "(5.1 Codex Max, 5.6) extend the scale (xhigh/max) and some drop "
            "'minimal'; update reasoning_effort_levels to the exact model."
        ),
    },
    # ── Anthropic ───────────────────────────────────────────────────────────
    "anthropic": {
        "_env_key": "ANTHROPIC_API_KEY",
        # Claude Sonnet 5 uses adaptive/extended thinking via the 'effort' param.
        # model==reasoning_model so every call is classified reasoning (exact match)
        # and NO temperature/top_p is sent — Sonnet 5 returns HTTP 400 on non-default
        # sampling params or manual 'thinking' blocks.
        "model": "anthropic/claude-sonnet-5",
        "reasoning_model": "anthropic/claude-sonnet-5",
        "reasoning_keywords": [],
        # Ordered reasoning-effort scale for this provider (low -> high). Used by
        # the adaptive timeout ladder to resolve a relative step (e.g. one level
        # below the default) and to validate any literal effort. Sonnet's top
        # level is 'max', NOT 'high'.
        "reasoning_effort_levels": ["low", "medium", "high", "max"],
        "temperature": 0.1,  # only used on the (unused) non-reasoning path
        "max_tokens": 128000,
        "max_completion_tokens": 128000,
        "large_context_model": "anthropic/claude-sonnet-5",
        "large_context_max_tokens": 128000,
        # Sonnet 5 has a 1M-token context window BY DEFAULT (no beta header). Its
        # tokenizer is ~30% denser (~3 chars/token), so 1M tokens ≈ ~3M chars.
        "large_context_input_threshold": 3000000,
        # litellm unified reasoning knob → Sonnet 5 effort=high (adaptive thinking).
        # Must NOT be a manual {"thinking": {...}} block (Sonnet 5 rejects that → 400).
        "_reasoning_params": {"reasoning_effort": "high"},
        "_notes": (
            "Claude Sonnet 5 adaptive thinking (effort: low|medium|high|max; we use "
            "'high', not 'max'). 128K max output tokens; 1M-token context window by "
            "DEFAULT (no beta header, standard long-context pricing). ~30%-denser "
            "tokenizer (~3M chars per 1M tokens). Manual 'thinking' blocks and "
            "non-default sampling params now 400; the reasoning path already "
            "suppresses temperature, and _reasoning_params uses reasoning_effort=high."
        ),
    },
    # ── Google Gemini ────────────────────────────────────────────────────────
    "gemini": {
        "_env_key": "GEMINI_API_KEY",
        # Gemini 3.5 Flash with extended thinking. model==reasoning_model so every
        # call runs Flash 3.5 in thinking mode (classified reasoning by exact match).
        "model": "gemini/gemini-3.5-flash",
        "reasoning_model": "gemini/gemini-3.5-flash",
        "reasoning_keywords": [],
        "reasoning_effort_levels": ["low", "medium", "high"],
        "temperature": 0.1,
        "max_tokens": 65536,
        "max_completion_tokens": 65536,
        "large_context_model": "gemini/gemini-3.5-flash",
        "large_context_max_tokens": 65536,
        # 1M-token input context (~3.5M chars headroom, mirrors DeepSeek).
        "large_context_input_threshold": 3500000,
        # litellm unified reasoning knob → Gemini 3.5 thinking_level=high (the
        # deepest/extended level). 'thinking_budget' is legacy/back-compat only.
        "_reasoning_params": {"reasoning_effort": "high"},
    },
    # ── DeepSeek ─────────────────────────────────────────────────────────────
    "deepseek": {
        "_env_key": "DEEPSEEK_API_KEY",
        # DeepSeek disabled the response_format request param API-side; suppress it and
        # rely on prompt JSON instructions + json_repair. Flip to True if restored.
        "supports_response_format": False,
        # deepseek-v4-pro: 1M token context, 384K max output, thinking mode default.
        # Same model string for both standard and reasoning calls — the model itself
        # supports non-thinking (fast) and thinking (deep) modes via a mode parameter.
        # deepseek-v4-flash = standard/fast model (no reasoning)
        # deepseek-v4-pro   = reasoning/thinking model (1M context, 384K output)
        "model": "deepseek/deepseek-v4-flash",
        "reasoning_model": "deepseek/deepseek-v4-pro",
        "reasoning_keywords": ["deepseek-v4-pro", "deepseek-reasoner"],
        # DeepSeek has NO reasoning-effort knob (thinking mode is on/off, the
        # effort param is ignored). Empty scale => the timeout ladder only
        # escalates the timeout for this provider, never the reasoning effort.
        "reasoning_effort_levels": [],
        "temperature": 0.1,
        # 384K = maximum output tokens supported by deepseek-v4-pro
        "max_tokens": 384000,
        "max_completion_tokens": 384000,
        # Large-context routing: deepseek-v4-pro has 1M context (≈4M chars).
        # Threshold set to 3.5M chars (875K tokens) to leave headroom for prompts.
        "large_context_model": "deepseek/deepseek-v4-pro",
        "large_context_max_tokens": 384000,
        "large_context_input_threshold": 3500000,
        "_notes": (
            "deepseek-v4-pro supports 1M token context and up to 384K output tokens. "
            "Thinking mode is the default; temperature is silently ignored in thinking "
            "mode — litellm drop_params=True handles this transparently. "
            "Both model and reasoning_model point to deepseek-v4-pro because the same "
            "model covers non-thinking (fast) and thinking (deep) modes."
        ),
    },
}

_DEFAULT_ACTIVE_PROVIDER = "openai"


# ---------------------------------------------------------------------------
# LLMClient
# ---------------------------------------------------------------------------
class LLMClient:
    """
    Provider-agnostic LLM client for the RIP pipeline.

    Instantiation reads ``project_config.json["llm"]`` to determine which
    provider and model to use.  All provider-specific quirks (reasoning model
    parameters, token-limit fields, response-format compatibility) are handled
    internally so callers can use a single uniform ``complete()`` interface.

    Parameters
    ----------
    config_path : str | Path | None
        Explicit path to ``project_config.json``.  When omitted, the file is
        located automatically by walking up from this module's location and
        falling back to ``os.getcwd()``.
    model_override : str | None
        Hard-codes a specific litellm model string for every call, bypassing
        the config.  Useful for one-off overrides in tests or scripts.
        Example: ``"anthropic/claude-3-5-sonnet-20241022"``
    api_key : str | None
        Optional runtime API key. When provided, it takes precedence over the
        active provider's environment variable.
    use_reasoning_model : bool
        Default flag indicating whether this client instance should route calls
        to the provider's flagship reasoning model (e.g., gpt-5, o3).
    """

    def __init__(
        self,
        config_path: str | None = None,
        model_override: str | None = None,
        use_reasoning_model: bool = True,
        api_key: str | None = None,
    ):
        if not LITELLM_AVAILABLE:
            raise RuntimeError("[LLMClient] litellm is not installed. Run: pip install litellm")

        self._llm_cfg: dict[str, Any] = self._load_llm_config(config_path)
        self._provider_name: str = self._llm_cfg.get("active_provider", _DEFAULT_ACTIVE_PROVIDER)
        self._provider_cfg: dict[str, Any] = self._llm_cfg.get("providers", {}).get(
            self._provider_name, _PROVIDER_DEFAULTS.get("openai", {})
        )

        self._model_override: str | None = model_override
        self._use_reasoning_model: bool = use_reasoning_model

        # Whether THIS client's timeouts count toward the circuit-breaker trip.
        # Off by default; only the SRS (spec) and feature/user-story stages opt in
        # via set_timeout_breaker_scope(True), so a timeout elsewhere (module
        # discovery, architecture, enrichment) fails its item without aborting.
        self._breaker_timeout_scope: bool = False

        # Set by _complete_impl when a call SUCCEEDS on a retry via the adaptive
        # timeout ladder (None on a first-attempt success). Callers can read it to
        # tag reduced-reasoning recoveries for optional later regeneration.
        self.last_recovery: dict[str, Any] | None = None

        # Read and store the API key at construction time, then pass it
        # explicitly to every litellm.completion() call.  This is more reliable
        # than relying on litellm's env-var auto-detection, which can miss
        # Windows System-level variables that were set after the process started.
        self._api_key: str | None = api_key or self._validate_api_key()

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        response_format: dict | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        use_reasoning_model: bool | None = None,
        **extra_kwargs: Any,
    ) -> str:
        """
        Public entry point. Thin wrapper that guards the retry loop
        (``_complete_impl``) with the process-level circuit-breaker so aggregate
        cost / time / consecutive-failure limits abort a runaway run even though
        each call's own retries are capped. A healthy call passes straight
        through (the breaker only resets counters on success).
        """
        # Cancelled runs stop here: never issue a new request once the user
        # has cancelled. Checked ahead of the breaker because it is the
        # cheaper and more certain abort of the two.
        _raise_if_run_cancelled("this call")
        _BREAKER.configure(self._llm_cfg)
        _BREAKER.check()  # re-trips if already open / wall-clock exceeded
        try:
            return self._complete_impl(
                system_prompt,
                user_prompt,
                response_format,
                temperature,
                max_tokens,
                use_reasoning_model,
                **extra_kwargs,
            )
        except CircuitBreakerError:
            print("[LLMClient] Circuit-breaker ABORTED the run — see above for details.")
            raise  # already an abort — do not double-count
        except CreditBalanceExhaustedError:
            print("[LLMClient] Provider billing/credits exhausted — ABORTING the run.")
            raise  # already an abort — do not double-count
        except LLMTimeoutExhausted:
            # A timeout — but only count it toward the trip if THIS client is
            # scoped in (SRS / feature-story generation). Elsewhere it just fails
            # the item. record_timeout_failure() trips (raises) at the cap.
            if self._breaker_timeout_scope:
                _BREAKER.record_timeout_failure()
            print("[LLMClient] LLM call failed due to TIMEOUT and retry budget exhausted.")
            raise
        except Exception:
            print(
                "[LLMClient] LLM call failed due to a non-timeout error (overflow, bad request, etc.)."
            )
            raise  # non-timeout failures never trip the breaker

    def _completion_with_watchdog(self, kwargs: dict[str, Any], attempt_timeout: Any) -> Any:
        """Run ``litellm.completion(**kwargs)`` under a hard wall-clock ceiling.

        litellm's own ``timeout`` only bounds the HTTP request; it does NOT cover
        pre-flight work (provider resolution, cost/context-map access, tokenizer
        init) nor a socket wedged by a misbehaving proxy — the exact place the
        observed hang occurred, before any request was sent. This watchdog runs the
        call on a worker thread and waits at most ``ceiling`` seconds; if litellm
        hasn't returned by then it raises :class:`LLMWatchdogTimeout` so the caller's
        timeout-retry/circuit-breaker path takes over and the pipeline never blocks
        indefinitely. The ceiling sits a margin ABOVE the per-attempt timeout so
        litellm's own (clean) timeout normally fires first; the watchdog is the
        backstop for when it doesn't.

        Disable with ``llm.hard_watchdog_enabled: false`` (then the call runs inline).
        """
        # Read on this (caller) thread — _RUN_CANCEL is thread-local and unset
        # on the daemon thread _stream_and_rebuild may run on below, so it must
        # be captured here and passed through explicitly.
        run_request_id = getattr(_RUN_CANCEL, "request_id", None)

        if not bool(self._llm_cfg.get("hard_watchdog_enabled", True)):
            if kwargs.get("stream"):
                _r = _stream_and_rebuild(kwargs, request_id=run_request_id)
                return _ensure_usage(_r, kwargs.get("model"), kwargs.get("messages"))
            return litellm.completion(**kwargs)

        base = float(
            attempt_timeout
            or self._provider_cfg.get("request_timeout_seconds")
            or self._llm_cfg.get("request_timeout_seconds")
            or 1800
        )
        margin = float(self._llm_cfg.get("watchdog_margin_seconds", 120) or 0)
        ceiling = float(self._llm_cfg.get("hard_watchdog_seconds", 0) or 0) or (base + margin)
        max_live = int(self._llm_cfg.get("watchdog_max_live_threads", 8) or 8)

        global _LIVE_WATCHDOGS
        with _LIVE_WATCHDOGS_LOCK:
            if max_live <= _LIVE_WATCHDOGS:
                raise LLMWatchdogTimeout(
                    f"[LLMClient] watchdog: {_LIVE_WATCHDOGS} abandoned LLM call(s) "
                    f"still running (>= {max_live} cap) — provider appears wedged; "
                    f"failing this attempt as a timeout to avoid unbounded thread growth."
                )

        # A DAEMON thread — NOT a ThreadPoolExecutor. A pool's worker threads are
        # non-daemon and concurrent.futures joins them all in an atexit hook, so a
        # thread wedged in a hung litellm call would block interpreter shutdown /
        # Celery worker recycling forever (verified). A daemon thread is simply
        # abandoned at exit, so a stuck provider can never prevent the process from
        # exiting. We cannot kill the thread, so we track it (bounded by max_live)
        # and reconcile the counter when it eventually finishes.
        holder: dict[str, Any] = {}
        done = threading.Event()
        state = {"abandoned": False}

        def _run() -> None:
            try:
                # Streaming path (Anthropic ceiling mitigation): drain + rebuild
                # INSIDE this daemon thread so the watchdog ceiling still bounds the
                # whole generation. _ensure_usage guarantees non-zero usage so cost
                # metering never silently reads zero. Non-streaming path unchanged.
                if kwargs.get("stream"):
                    _resp = _stream_and_rebuild(kwargs, request_id=run_request_id)
                    holder["value"] = _ensure_usage(
                        _resp, kwargs.get("model"), kwargs.get("messages")
                    )
                else:
                    holder["value"] = litellm.completion(**kwargs)
            except BaseException as exc:  # noqa: BLE001
                # Capture EVERYTHING and re-raise on the caller thread so litellm's
                # own errors (Timeout, APIError, …) flow through the normal classifier
                # with their real types preserved.
                holder["error"] = exc
            finally:
                done.set()
                with _LIVE_WATCHDOGS_LOCK:
                    if state["abandoned"]:
                        globals()["_LIVE_WATCHDOGS"] = max(0, _LIVE_WATCHDOGS - 1)

        worker = threading.Thread(target=_run, name="llm-watchdog", daemon=True)
        worker.start()

        if not done.wait(timeout=ceiling):
            # Ceiling hit. Under the lock, decide whether the call is TRULY still
            # running (race: it may have just finished). Only count/abandon if it is,
            # so the live counter stays exact and the worker's finally decrements it.
            with _LIVE_WATCHDOGS_LOCK:
                if not done.is_set():
                    _LIVE_WATCHDOGS += 1
                    state["abandoned"] = True
                    _leaked = True
                else:
                    _leaked = False
            if _leaked:
                raise LLMWatchdogTimeout(
                    f"[LLMClient] Hard watchdog fired after {ceiling:.0f}s — abandoning "
                    f"this attempt (model='{kwargs.get('model')}', "
                    f"provider='{self._provider_name}'); litellm/httpx did not return "
                    f"within the ceiling (pre-flight or network stall). Routed as a timeout."
                )
            # else: finished in the race window — fall through and return its result.

        if "error" in holder:
            raise holder["error"]
        return holder["value"]

    def _complete_impl(
        self,
        system_prompt: str,
        user_prompt: str,
        response_format: dict | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        use_reasoning_model: bool | None = None,
        **extra_kwargs: Any,
    ) -> str:
        """
        Execute a chat completion and return the raw content string.

        Parameters
        ----------
        system_prompt : str
            The system / context message.
        user_prompt : str
            The user / task message.
        response_format : dict | None
            ``{"type": "json_object"}`` or
            ``{"type": "json_schema", "json_schema": {...}}``.
            json_schema automatically falls back to json_object for providers
            that do not support structured outputs.
        temperature : float | None
            Per-call override.  Ignored automatically for reasoning models.
        max_tokens : int | None
            Per-call override.  Ignored automatically for reasoning models
            (``max_completion_tokens`` is used instead).
        use_reasoning_model : bool | None
            When True, uses the provider's ``reasoning_model`` entry.
            If None, defaults to the setting passed during class initialization.
        **extra_kwargs
            Passed verbatim to ``litellm.completion()`` — escape hatch for
            any provider-specific parameter not covered above.

        Returns
        -------
        str
            Raw content string from the model.

        Raises
        ------
        RuntimeError
            Wraps any litellm / network error with provider context.
        """
        # Resolve the reasoning flag: use per-call override if provided, else instance default
        actual_use_reasoning = (
            use_reasoning_model if use_reasoning_model is not None else self._use_reasoning_model
        )

        model = self._resolve_model(actual_use_reasoning)
        is_reasoning = self._is_reasoning_model(model)

        # ── Large-context auto-routing ────────────────────────────────────────
        # If the combined prompt exceeds the configured input threshold (default
        # 400 000 chars ≈ 100 K tokens), transparently switch to the provider's
        # large_context_model and its corresponding max_tokens ceiling.
        total_input_chars = len(system_prompt) + len(user_prompt)
        lc_threshold = self._provider_cfg.get("large_context_input_threshold", 400_000)
        lc_model = self._provider_cfg.get("large_context_model")
        if lc_model and not is_reasoning and total_input_chars >= lc_threshold:
            print(
                f"[LLMClient] Large-context input detected ({total_input_chars:,} chars). "
                f"Auto-routing to '{lc_model}'."
            )
            model = lc_model
            if max_tokens is None:
                max_tokens = self._provider_cfg.get("large_context_max_tokens", 16384)

        # ── Base kwargs ──────────────────────────────────────────────────────
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        # ── Token limits ─────────────────────────────────────────────────────
        # Reasoning models: use max_completion_tokens; temperature forbidden.
        # Standard models:  use max_tokens + temperature.
        if is_reasoning:
            kwargs["max_completion_tokens"] = self._provider_cfg.get(
                "max_completion_tokens", 128000
            )
        else:
            kwargs["max_tokens"] = (
                max_tokens
                if max_tokens is not None
                else self._provider_cfg.get("max_tokens", 16384)
            )
            kwargs["temperature"] = (
                temperature
                if temperature is not None
                else self._provider_cfg.get("temperature", 0.1)
            )

        # ── Response format (with cross-provider adapter) ────────────────────
        # Suppressed for providers that don't support response_format — either by
        # config (supports_response_format=false) or discovered at runtime (e.g.
        # DeepSeek disabled it API-side). When suppressed we rely on the prompt's
        # JSON instruction + tolerant json_repair parsing in the callers.
        if response_format:
            if self._response_format_supported():
                kwargs.update(self._adapt_response_format(model, response_format))
            else:
                print(
                    f"[LLMClient] response_format suppressed for provider "
                    f"'{self._provider_name}' (unsupported/disabled) — relying on prompt "
                    f"JSON instruction + tolerant parsing."
                )

        # ── Provider-specific reasoning parameters ───────────────────────────
        # e.g. Anthropic: {"thinking": {"type": "enabled", "budget_tokens": N}}
        #      Gemini:    {"thinking_config": {"thinking_budget": N}}
        if is_reasoning:
            reasoning_params = self._provider_cfg.get("_reasoning_params", {})
            kwargs.update(reasoning_params)

        # ── API key — explicit injection (bypasses litellm env-var scan) ───────
        # Injecting the key directly is more reliable than relying on litellm's
        # auto-detection, particularly on Windows where System-level env vars
        # may not be visible to the running process.
        if self._api_key:
            kwargs["api_key"] = self._api_key

        # ── Per-request timeout — bound each attempt ─────────────────────────
        # litellm maps `timeout` onto the underlying httpx client and raises
        # litellm.Timeout on expiry, which the transient-retry loop below catches
        # and retries with backoff. Without this a stalled provider stream (the
        # mid-stream DeepSeek SSL-read hang we observed) blocks the run until the
        # process is killed. Tunable via llm.request_timeout_seconds (default
        # 1800s — generous so legitimate 1M-context + extended-thinking calls
        # finish rather than false-timing-out; cost is bounded by the retry cap
        # and circuit-breaker, not by a short timeout).
        # Provider-specific override wins over the global default. This lets each
        # provider carry the timeout its retry strategy needs WITHOUT them fighting
        # over one shared value: e.g. anthropic (streaming) wants a HIGH ceiling so
        # long streams aren't clipped, while deepseek (timeout-escalation ladder)
        # wants a LOW base (600) so the ladder can escalate meaningfully. Set it per
        # provider under providers.<name>.request_timeout_seconds; falls back to the
        # top-level llm.request_timeout_seconds when absent.
        _req_timeout = (
            self._provider_cfg.get("request_timeout_seconds")
            or self._llm_cfg.get("request_timeout_seconds")
        )
        if _req_timeout:
            kwargs.setdefault("timeout", float(_req_timeout))

        # ── Disable litellm's OWN internal retry layer ───────────────────────
        # litellm retries transient errors internally with its own backoff, which
        # invisibly STACKS on top of OUR single retry loop below — multiplying both
        # wall-clock time and cost, and producing long SILENT stalls (litellm sleeps
        # between its hidden retries without logging at ERROR level, so no request
        # appears). We own retries here, so force litellm to attempt exactly once.
        kwargs.setdefault("num_retries", 0)

        # ── Caller overrides (highest precedence) ────────────────────────────
        kwargs.update(extra_kwargs)

        # ── Execute with transient-error retry ──────────────────────────────
        # Retry policy for transient HTTP errors (502 Bad Gateway, 503 Service
        # Unavailable / "high demand", 429 rate limit, timeouts, connection drops).
        # These are server-side failures unrelated to the prompt content. A brief
        # spike (esp. Gemini 503 UNAVAILABLE) can outlast a couple of quick retries,
        # so the default budget is 5 attempts with CAPPED EXPONENTIAL BACKOFF + JITTER
        # (~2,4,8,16,30s), giving the provider up to ~60s to recover instead of
        # crashing the whole run. Tunable via project_config llm.max_retries.
        import random as _random_module
        import time as _time_module

        # Single small retry budget owned HERE (the one layer). Callers must NOT
        # stack their own LLM-call retry on top (that produced the 6x3=18 storm).
        # Default 2 -> at most 3 attempts for a generic transient error.
        _max_retries = int(self._llm_cfg.get("max_retries", 2) or 2)
        # Timeouts get a SEPARATE, hard cap (default 1): a timeout on a large
        # payload is expensive and rarely recovers on an identical re-send, so we
        # do not spend the full transient budget on it (and never shrink here —
        # payload sizing is a caller concern).
        _timeout_max_retries = int(self._llm_cfg.get("timeout_max_retries", 1) or 0)
        _BACKOFF_CAP = 30.0

        # ── Adaptive timeout-escalation ladder (SRS / feature-story only) ─────
        # When THIS client is scoped in and llm.timeout_fallback.enabled, each
        # retry escalates params to improve completion odds — QUALITY-FIRST:
        # raise the timeout first (same reasoning), and lower reasoning_effort
        # only on the last rung (last resort to force completion). One rung per
        # attempt (index 0 = initial); clamped to the last rung if there are more
        # attempts than rungs. Other stages use base params on every attempt.
        _tf_cfg = self._llm_cfg.get("timeout_fallback") or {}
        _ladder = _tf_cfg.get("ladder") or []
        # OPTIONAL provider allowlist. When present and non-empty, the ladder only
        # activates for these providers; every other provider falls back to base
        # params (no work-reduction, no timeout escalation) — i.e. behaves as if
        # there were no ladder. Empty/absent = apply to ALL providers (legacy).
        # This lets a provider-specific ladder (e.g. Anthropic's non-streaming
        # ceiling mitigation) live in a config WITHOUT throttling DeepSeek/others
        # if active_provider is switched.
        # Case-insensitive so a config typo ("Anthropic") can't silently disable
        # the gate. Provider names are lowercase internally.
        _tf_providers = [str(p).lower() for p in (_tf_cfg.get("providers") or [])]
        _provider_allowed = (not _tf_providers) or (str(self._provider_name).lower() in _tf_providers)
        _use_ladder = bool(
            self._breaker_timeout_scope and _tf_cfg.get("enabled", False) and _ladder and _provider_allowed
        )
        if _tf_cfg.get("enabled", False) and _ladder and not self._breaker_timeout_scope:
            print(
                "[LLMClient] timeout_fallback is configured but INACTIVE for this call "
                "(breaker scope disabled). Using fixed timeout/reasoning params."
            )

        # ── Streaming (Anthropic non-streaming ~10-min ceiling mitigation) ────
        # Opt-in per provider via llm.stream_providers (list, case-insensitive).
        # Default OFF (empty) so production behaviour is byte-identical until
        # explicitly enabled — ship dark, flip on in QA. include_usage guarantees
        # the final usage chunk so cost metering keeps working (STREAMING_TEST_PLAN
        # §7.5). §7.7 coherence: when streaming is active for THIS provider we also
        # DISABLE the work-reducing ladder for it — streaming carries the long call,
        # so there is no need to shrink effort/output to beat the clock.
        _stream_providers = [
            str(p).lower() for p in (self._llm_cfg.get("stream_providers") or [])
        ]
        _streaming_active = str(self._provider_name).lower() in _stream_providers
        if _streaming_active:
            kwargs["stream"] = True
            kwargs["stream_options"] = {"include_usage": True}
            if _use_ladder:
                print(
                    f"[LLMClient] streaming ON for provider '{self._provider_name}' "
                    f"— work-reducing ladder DISABLED for it (streaming handles long "
                    f"generations; timeout ladder is redundant)."
                )
                _use_ladder = False

        _base_timeout = float(_req_timeout) if _req_timeout else None
        _default_effort = (self._provider_cfg.get("_reasoning_params", {}) or {}).get(
            "reasoning_effort"
        )
        _effort_levels = self._provider_cfg.get("reasoning_effort_levels") or []
        self.last_recovery = None  # reset per call; set on a retry-success below
        self.last_truncated = False # reset per call; set True below if any attempt returns finish_reason='length'

        def _resolve_rung_effort(rung: dict[str, Any]) -> str | None:
            """Provider-aware effort for a ladder rung. Returns the default when
            the rung doesn't override it. Handles the fact that providers differ
            (Sonnet tops out at 'max', Gemini at 'high', DeepSeek has none)."""
            if not _effort_levels:
                return None  # provider has no effort knob (e.g. DeepSeek)
            lit = rung.get("reasoning_effort")
            if lit is not None:
                if lit in _effort_levels:
                    return lit
                print(
                    f"[LLMClient] ladder: reasoning_effort='{lit}' is not valid for "
                    f"provider '{self._provider_name}' (levels={_effort_levels}); "
                    f"keeping default '{_default_effort}'."
                )
                return _default_effort
            step = rung.get("reasoning_effort_step")
            if step and _default_effort in _effort_levels:
                idx = _effort_levels.index(_default_effort) + int(step)
                idx = max(0, min(len(_effort_levels) - 1, idx))
                return _effort_levels[idx]
            return _default_effort  # no override on this rung

        # The loop must allow the LARGER of the two budgets. (Previously bounded
        # by _max_retries only, which silently dropped the final attempt whenever
        # timeout_max_retries > max_retries.)
        _loop_max = max(_max_retries, _timeout_max_retries)

        def _backoff(attempt: int) -> float:
            base = min(_BACKOFF_CAP, 2.0 ** (attempt + 1))  # 2,4,8,16,30,30…
            return round(base + _random_module.uniform(0, min(2.0, base * 0.25)), 2)

        def _attempt_params_text() -> str:
            _t = kwargs.get("timeout")
            _eff = kwargs.get("reasoning_effort") if is_reasoning else None
            _eff_txt = _eff if _eff is not None else "n/a"
            _timeout_txt = f"{_t}s" if _t is not None else "n/a"
            return (
                f"timeout={_timeout_txt}, reasoning_effort={_eff_txt}, "
                f"ladder={'on' if _use_ladder else 'off'}"
            )

        for _attempt in range(_loop_max + 1):
            # Apply this attempt's ladder rung (scoped stages only). Recomputed
            # from scratch each attempt so a rung that doesn't override effort
            # RESTORES the provider default (rather than inheriting a prior
            # attempt's downgrade).
            _rung_label = "none"
            if _use_ladder:
                _rung_idx = min(_attempt, len(_ladder) - 1)
                _rung = _ladder[_rung_idx]
                _rung_label = f"{_rung_idx + 1}/{len(_ladder)}"
                if _base_timeout is not None:
                    kwargs["timeout"] = _base_timeout + float(
                        _rung.get("timeout_add_seconds", 0) or 0
                    )
                if is_reasoning:
                    _rung_effort = _resolve_rung_effort(_rung)
                    if _rung_effort:
                        kwargs["reasoning_effort"] = _rung_effort
                # Per-rung OUTPUT cap. Lets a ladder rung shrink the max output and
                # thus bound generation time — pairs with lowering effort to get a
                # heavy call under the provider's non-streaming (~10-min) ceiling.
                # OPT-IN: only fires when a rung declares "max_tokens", so any ladder
                # whose rungs omit it (e.g. the timeout-raising VB/PB ladders) is
                # completely unaffected. Reasoning models cap via max_completion_tokens;
                # standard models via max_tokens.
                _rung_mt = _rung.get("max_tokens")
                if _rung_mt:
                    if is_reasoning:
                        kwargs["max_completion_tokens"] = int(_rung_mt)
                    else:
                        kwargs["max_tokens"] = int(_rung_mt)
            try:
                # Request-side log: makes a pre-flight/in-flight hang attributable
                # (previously only the RESPONSE was logged, so a stall left no trace
                # of which call was in progress).
                _attempt_timeout = kwargs.get("timeout")
                _attempt_effort = kwargs.get("reasoning_effort")
                _effort_suffix = (
                    f", reasoning_effort={_attempt_effort}"
                    if _attempt_effort is not None
                    else ""
                )
                print(
                    f"[LLMClient] Sending request \u2192 model='{model}', "
                    f"attempt {_attempt + 1}/{_loop_max + 1}, timeout={_attempt_timeout}s, "
                    f"stream={'ON' if _streaming_active else 'OFF'}"
                    f"{_effort_suffix}"
                )
                # Hard wall-clock WATCHDOG around the call. litellm's `timeout` only
                # bounds the HTTP request itself; it does NOT cover litellm pre-flight
                # work (provider resolution, cost/context map access, tokenizer init)
                # nor a socket wedged by a misbehaving proxy. The watchdog guarantees
                # the pipeline can never block beyond a deterministic ceiling even if
                # litellm/httpx ignore their own timeout — the abandoned call surfaces
                # as a TIMEOUT and flows through the normal timeout-retry/breaker path.
                response = self._completion_with_watchdog(kwargs, _attempt_timeout)

                # ── Defensive response-envelope extraction ───────────────────
                # A malformed envelope (no choices / no message) is a transient
                # provider glitch, NOT a permanent error — extract defensively
                # and route it through the retry path via _RetryableResponseError.
                choices = getattr(response, "choices", None) or []
                if not choices:
                    raise _RetryableResponseError("response contained no choices")
                message = getattr(choices[0], "message", None)
                content = getattr(message, "content", None) if message else None
                finish_reason = getattr(choices[0], "finish_reason", "unknown")

                # Diagnostic log
                content_len = len(content) if content else 0
                print(
                    f"[LLMClient] Response received — model='{model}', "
                    f"finish_reason='{finish_reason}', content_len={content_len}"
                )

                # Guard: None or empty content.
                if not content:
                    fr = str(finish_reason).lower()
                    # content_filter is a PERMANENT policy outcome — retrying is
                    # futile and wrong. Everything else (esp. finish_reason='stop'
                    # with empty body, observed transiently on DeepSeek) is retried.
                    if "content_filter" in fr or "content-filter" in fr:
                        raise RuntimeError(
                            f"[LLMClient] Empty content from '{model}' due to "
                            f"content policy filter (finish_reason='{finish_reason}', "
                            f"provider='{self._provider_name}'). Not retryable."
                        )
                    raise _RetryableResponseError(
                        f"empty/null content (finish_reason='{finish_reason}')"
                    )

                # Truncation guard: non-empty body BUT the model hit the output cap
                # (finish_reason='length'). The content is usable and IS returned,
                # but the spec may be truncated — warn loudly and set a flag so
                # callers/QA can review or regenerate at a higher output budget.
                # Warning ONLY: no control-flow change (retrying would move to a
                # SMALLER rung, which cannot help). Applies to all providers/stages.
                if str(finish_reason).lower() == "length":
                    self.last_truncated = True
                    print(
                        f"[LLMClient] [TRUNCATION WARNING] finish_reason='length' — output "
                        f"reached the max_tokens cap (model='{model}', "
                        f"provider='{self._provider_name}'); this response may be truncated. "
                        f"Review or regenerate at a higher output budget."
                    )

                # Ladder recovery bookkeeping: a retry that SUCCEEDED. Record it
                # (and log prominently), flagging when it came back at REDUCED
                # reasoning so callers can tag the output for optional later
                # regeneration at full quality.
                if _use_ladder and _attempt > 0:
                    _used_effort = kwargs.get("reasoning_effort")
                    _degraded = bool(
                        _default_effort and _used_effort and _used_effort != _default_effort
                    )
                    self.last_recovery = {
                        "attempt": _attempt + 1,
                        "reasoning_effort": _used_effort,
                        "timeout": kwargs.get("timeout"),
                        "degraded_reasoning": _degraded,
                    }
                    _rmsg = (
                        f"[LLMClient] RECOVERED on attempt {_attempt + 1} "
                        f"(reasoning_effort={_used_effort}, timeout={kwargs.get('timeout')}s)"
                    )
                    if _degraded:
                        _rmsg += " — REDUCED reasoning; tag for optional full-quality regen"
                    print(_rmsg)

                # Success: reset the consecutive-failure counter and meter cost.
                # (May raise CircuitBreakerError if the cumulative spend cap is hit;
                # that propagates out via the `except RuntimeError: raise` below.)
                _BREAKER.record_success(response)
                return content

            except _RetryableResponseError as rre:
                # Well-formed call, unusable body → retry with back-off.
                if _attempt < _max_retries:
                    # Don't spend another attempt (or sit out its backoff) on
                    # a run the user has already cancelled.
                    _raise_if_run_cancelled("the remaining retries")
                    _wait = _backoff(_attempt)  # capped exponential + jitter
                    print(
                        f"[LLMClient] Empty/malformed response on attempt "
                        f"{_attempt + 1}/{_max_retries + 1} ({rre}). "
                        f"Params: {_attempt_params_text()}. Retrying in {_wait}s..."
                    )
                    _time_module.sleep(_wait)
                    continue  # retry
                # Retries exhausted — raise the informative diagnostic.
                raise RuntimeError(
                    f"[LLMClient] Empty/null content returned by '{model}' "
                    f"(provider='{self._provider_name}') after {_max_retries + 1} "
                    f"attempt(s): {rre}. If this persists, ensure the system prompt "
                    f"contains the word 'JSON' when using json_object response_format, "
                    f"and that the input does not exceed the model context window."
                ) from rre

            except RuntimeError:
                raise  # Re-raise our own diagnostics unchanged

            except Exception as exc:
                exc_type = type(exc).__name__
                exc_str = str(exc)
                _t = exc_type.lower()
                _s = exc_str.lower()

                # ── response_format self-healing (e.g. DeepSeek disabled it) ─────
                # A provider that rejects response_format returns a BadRequest like
                # "This response_format type is unavailable now". This is NOT a prompt
                # error: drop response_format, remember it for this provider for the rest
                # of the session (so later calls skip it proactively), and retry. The
                # prompts already instruct JSON and json_repair recovers any malformed
                # output, so suppressing the param does not hurt output quality.
                if "response_format" in _s and any(
                    m in _s
                    for m in (
                        "unavailable",
                        "unsupported",
                        "not support",
                        "not available",
                        "invalid_request",
                    )
                ):
                    if kwargs.pop("response_format", None) is not None:
                        _RESPONSE_FORMAT_DISABLED.add(self._provider_name)
                        print(
                            f"[LLMClient] Provider '{self._provider_name}' rejected "
                            f"response_format ({exc_str[:80]}). Dropped it (session-cached) "
                            f"and retrying without it — prompt JSON + tolerant parsing."
                        )
                        if _attempt < _max_retries:
                            continue  # retry immediately without response_format
                    # response_format not present (or retries exhausted) → fall through.

                # ── Centralized classification (shared with the RFP pipeline) ───
                # See app/core/llm_errors.py for the full provider-agnostic table
                # (billing/credit exhaustion, permanent vs. transient reasons,
                # context overflow, Google/Gemini-specific status unwrapping).
                # Lazy import: app.core.* is imported lazily throughout this
                # module (see _raise_if_run_cancelled) so this pipeline stays
                # usable standalone/CLI without the app package importable.
                from app.core.llm_errors import (  # noqa: PLC0415
                    LLMErrorReason,
                    NonRetryableLLMError,
                    classify_llm_error,
                )

                classification = classify_llm_error(exc, provider=self._provider_name)

                # Billing/credit/quota EXHAUSTION is PERMANENT for the WHOLE RUN,
                # not just this call: every other module still queued behind this
                # one would hit the identical wall, so this raises a dedicated
                # BaseException (CreditBalanceExhaustedError) instead of the
                # generic per-call RuntimeError below — the same "abort the whole
                # run" contract as CircuitBreakerError — rather than grinding
                # through every remaining module one at a time to rediscover the
                # same account-level failure.
                if classification.reason == LLMErrorReason.CREDIT_EXHAUSTED:
                    raise CreditBalanceExhaustedError(
                        f"[LLMClient] Provider billing/credits exhausted — "
                        f"provider='{self._provider_name}', model='{model}'. The API "
                        f"rejected the call with a credit/quota-depletion error (a "
                        f"permanent, account-wide condition, not a transient rate "
                        f"limit), so retrying — here or on any other module in this "
                        f"run — will not help. Fix by: (1) topping up billing/credits "
                        f"for this provider's account, (2) using an API key from a "
                        f"funded project, or (3) switching llm.active_provider to a "
                        f"funded provider (e.g. deepseek). Original error: "
                        f"{exc_str[:200]}"
                    ) from exc

                # Every OTHER non-retryable reason (auth, invalid model,
                # invalid request, context-length-exceeded, content policy,
                # permission denied) is ALSO account/config/input-deterministic
                # — retrying will not help, and per this pipeline's design,
                # a single module/bucket hitting one of these stops the WHOLE
                # run immediately (not just that one item), the same
                # "abort the whole run" contract as CircuitBreakerError/
                # CreditBalanceExhaustedError above. A dedicated BaseException
                # (NonRetryableLLMError) — not the generic per-call
                # RuntimeError below — is what makes that happen: it escapes
                # every broad `except Exception` between here and the Celery
                # task boundary.
                if not classification.retryable:
                    raise NonRetryableLLMError(classification, original=exc) from exc

                # TRANSIENT (worth retrying): only the reasons the classifier
                # positively matched as such — an UNMATCHED/UNKNOWN exception is
                # neither permanent nor transient here, matching this pipeline's
                # existing behavior of failing fast (no retry) rather than
                # burning a retry budget on something unrecognized.
                is_transient = classification.reason in (
                    LLMErrorReason.RATE_LIMIT,
                    LLMErrorReason.SERVER_ERROR,
                    LLMErrorReason.CONNECTION_ERROR,
                    LLMErrorReason.TIMEOUT,
                )

                # Timeouts use the smaller, dedicated budget (default 1) instead
                # of the generic transient budget — see _timeout_max_retries above.
                is_timeout = classification.reason == LLMErrorReason.TIMEOUT
                _eff_max = _timeout_max_retries if is_timeout else _max_retries

                if is_transient and _attempt < _eff_max:
                    # The big one: a timed-out call has already burned up to
                    # request_timeout_seconds + the watchdog margin. Retrying
                    # it on a cancelled run is what stretched cancellation out
                    # to ~35 minutes for a single wedged call.
                    _raise_if_run_cancelled("the remaining retries")
                    _wait = _backoff(_attempt)  # capped exponential + jitter
                    _kind = "Timeout" if is_timeout else "Transient API"
                    print(
                        f"[LLMClient] {_kind} error ({exc_type}) on attempt "
                        f"{_attempt + 1}/{_eff_max + 1}. "
                        f"Params: {_attempt_params_text()}. "
                        f"Retrying in {_wait}s... ({exc_str[:80]})"
                    )
                    _time_module.sleep(_wait)
                    continue  # retry

                # Permanent error or retries exhausted. A TIMEOUT gets its own
                # exception type so the wrapper can count it toward the breaker
                # (when scoped) while every other failure does not.
                if is_timeout:
                    raise LLMTimeoutExhausted(
                        f"[LLMClient] Timeout — provider='{self._provider_name}', "
                        f"model='{model}', {_eff_max + 1} attempt(s) exhausted "
                        f"({_attempt_params_text()}): {exc}"
                    ) from exc
                raise RuntimeError(
                    f"[LLMClient] Call failed — provider='{self._provider_name}', "
                    f"model='{model}': {exc}"
                ) from exc

    # ── Circuit-breaker scoping ───────────────────────────────────────────────

    def set_timeout_breaker_scope(self, enabled: bool = True) -> "LLMClient":
        """Opt this client instance's calls INTO the timeout circuit-breaker.

        Only the large-context generation stages enable this — SRS/spec
        generation and feature/user-story generation — so a timeout there counts
        toward the abort. Every other stage leaves it off, so its timeouts fail
        the item without aborting the run. Returns self for chaining."""
        self._breaker_timeout_scope = bool(enabled)
        return self

    @staticmethod
    def reset_circuit_breaker() -> None:
        """Reset the process-level circuit-breaker's run-scoped state (failure
        counter, accumulated cost, sticky trip flag).

        MUST be called once at the START of each pipeline run — i.e. at Celery task
        entry (``process_single_module``) and at the top of a local CLI run — BEFORE
        the first LLM call. The breaker is a module-level singleton, so a long-lived
        worker process reuses it across tasks; without this reset a prior run's
        failure count or trip flag leaks into the next run (and, when two pipelines
        share a worker, one project's trip aborts the other). Resetting per run makes
        the breaker effectively per-run.

        Prefer ``pipeline_run_guard()``, which resets AND enforces one run per
        process; call this directly only if you don't need the concurrency guard."""
        _BREAKER.reset()

    @classmethod
    @contextlib.contextmanager
    def pipeline_run_guard(
        cls, run_id: str, reset_breaker: bool = True, request_id: str | None = None
    ):
        """Enforce ONE pipeline run per process, and reset the circuit breaker at
        entry. Wrap the whole body of the pipeline task/run in this:

            with LLMClient.pipeline_run_guard(project_id):
                ... run the pipeline ...

        If another run is already active IN THIS PROCESS (a second project landed on
        the same worker via a shared-memory pool), it raises
        :class:`ConcurrentPipelineError` so the task fails fast and Celery re-queues
        it onto an idle process — rather than the two runs silently corrupting shared
        process-global state (circuit breaker, plugin registry, …). Under a correct
        prefork deployment (one task per child process) the guard never fires; it is
        a safety net that makes the invariant explicit.

        Re-entrant for the SAME thread/run (nested use is fine). Set env
        ``RIP_SINGLE_RUN_MODE=warn`` to log instead of raise during a staged rollout
        (default is enforce)."""
        tid = threading.get_ident()
        enforce = os.environ.get("RIP_SINGLE_RUN_MODE", "enforce").strip().lower() != "warn"
        with _ACTIVE_RUN_LOCK:
            active_id = _ACTIVE_RUN["id"]
            if active_id is not None and _ACTIVE_RUN["owner"] != tid:
                msg = (
                    f"[LLMClient] Refusing to start pipeline run '{run_id}' — run "
                    f"'{active_id}' is already active in this process (pid={os.getpid()}). "
                    f"Two pipelines must not share a process. Run the Celery worker "
                    f"with the prefork pool (one task per child process)."
                )
                if enforce:
                    raise ConcurrentPipelineError(msg)
                print(msg + " [RIP_SINGLE_RUN_MODE=warn — continuing anyway]")
            if active_id is None:
                _ACTIVE_RUN["id"] = run_id
                _ACTIVE_RUN["owner"] = tid
            _ACTIVE_RUN["depth"] += 1
            first = _ACTIVE_RUN["depth"] == 1

        # Cancellation key for this run, consulted before every LLM call (see
        # _raise_if_run_cancelled). Saved/restored rather than just cleared, so
        # a nested guard cannot drop the outer run's key on the way out; a
        # nested call that supplies none simply inherits the enclosing run's.
        _prev_cancel = (
            getattr(_RUN_CANCEL, "request_id", None),
            getattr(_RUN_CANCEL, "cancelled", False),
        )
        if request_id is not None:
            _RUN_CANCEL.request_id = request_id
            _RUN_CANCEL.cancelled = False
        if first and reset_breaker:
            cls.reset_circuit_breaker()  # fresh breaker per run (safe: single run here)
        try:
            yield
        finally:
            with _ACTIVE_RUN_LOCK:
                _ACTIVE_RUN["depth"] = max(0, _ACTIVE_RUN["depth"] - 1)
                if _ACTIVE_RUN["depth"] == 0:
                    _ACTIVE_RUN["id"] = None
                    _ACTIVE_RUN["owner"] = None
            _RUN_CANCEL.request_id, _RUN_CANCEL.cancelled = _prev_cancel

    # ── Convenience properties ────────────────────────────────────────────────

    @property
    def active_model(self) -> str:
        """The standard (non-reasoning) model string for the active provider."""
        return self._resolve_model(use_reasoning_model=False)

    @property
    def model(self) -> str:
        """
        Alias for active_model.
        Allows callers that read `client.model` (e.g. generation_metadata) to
        get the actual model string rather than 'unknown'.
        """
        return self.active_model

    @property
    def active_reasoning_model(self) -> str:
        """The reasoning model string for the active provider."""
        return self._resolve_model(use_reasoning_model=True)

    @property
    def effective_model(self) -> str:
        """
        The model string that this client instance will actually use for calls.
        Respects the instance-level ``use_reasoning_model`` flag set at construction.
        Use this for log messages and metadata — it is always accurate.

        Contrast with ``active_model`` (always the standard model) and
        ``active_reasoning_model`` (always the reasoning model), which are
        fixed regardless of the instance flag.
        """
        return self._resolve_model(self._use_reasoning_model)

    @property
    def active_provider(self) -> str:
        """Name of the currently active provider (e.g. 'openai', 'anthropic')."""
        return self._provider_name

    @property
    def max_completion_tokens(self) -> int:
        """
        The configured max_completion_tokens ceiling for the active provider.

        Callers should use this instead of hardcoding a provider-specific
        constant, so that switching providers requires only a config edit.
        """
        return self._provider_cfg.get("max_completion_tokens", 128000)

    @property
    def using_reasoning_model(self) -> bool:
        """
        True when this LLMClient instance was constructed with
        ``use_reasoning_model=True`` and will therefore route calls to the
        provider's reasoning / thinking model.

        Callers that need to branch on reasoning vs. standard behaviour
        (e.g. to decide whether to pass ``temperature``) should read this
        property rather than inspecting model name strings.
        """
        return self._use_reasoning_model

    # ──────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _validate_api_key(self) -> str | None:
        """
        Reads the active provider's API key from the environment and returns it.

        Also prints a warning at construction time if the key is absent so
        users catch misconfiguration early rather than getting a cryptic error
        on the first LLM call.

        Returns
        -------
        str | None
            The API key string, or None if no env-var is configured / found.
            The value is stored on ``self._api_key`` and injected explicitly
            into every ``litellm.completion()`` call, bypassing litellm's own
            env-var scan (which can miss Windows System-level variables that
            were set after the current process started).
        """
        env_key: str = self._provider_cfg.get("_env_key", "")
        if not env_key:
            return None  # Provider has no env-key requirement (e.g. local models)
        api_key = os.environ.get(env_key)
        if not api_key:
            print(
                f"[LLMClient] WARNING: environment variable '{env_key}' is not set. "
                f"Calls to provider '{self._provider_name}' will fail unless the key "
                "is provided another way (e.g. litellm proxy, ~/.litellm config)."
            )
        else:
            print(
                f"[LLMClient] API key loaded from env '{env_key}' "
                f"(provider: '{self._provider_name}', length: {len(api_key)})."
            )
        return api_key or None

    @staticmethod
    def _merge_providers(project_providers: dict[str, Any]) -> dict[str, Any]:
        """Deep-merge project provider overrides onto the built-in defaults, one
        level deep per provider. A project block overrides individual keys but
        keeps every default key it doesn't mention (so new default keys like
        reasoning_effort_levels are inherited without per-project edits). A
        provider the project defines but the defaults don't is passed through."""
        merged: dict[str, Any] = {}
        for name, default_cfg in _PROVIDER_DEFAULTS.items():
            override = project_providers.get(name) or {}
            merged[name] = {**default_cfg, **override}
        for name, cfg in project_providers.items():
            if name not in merged:
                merged[name] = cfg
        return merged

    def _load_llm_config(self, config_path: str | None) -> dict[str, Any]:
        """
        Locates and parses the ``"llm"`` section from ``project_config.json``.

        Search order:
          1. Explicit ``config_path`` argument
          2. ``project_config.json`` in ``os.getcwd()``
          3. Walk up from this file's location looking for ``project_config.json``

        If no ``"llm"`` section is present, returns the built-in defaults so
        the pipeline can always run without any config changes.
        """
        candidates: list[Path] = []

        # 0. Env-var override — highest priority (set by CLI via set_project_paths)
        env_cfg = os.getenv("RIP_LLM_CONFIG_PATH")
        if env_cfg:
            candidates.append(Path(env_cfg))

        # 1. Explicit caller argument
        if config_path:
            candidates.append(Path(config_path))

        # 2. CWD — most common execution context
        candidates.append(Path(os.getcwd()) / "projects/sample_project/project_config.json")

        # Walk up from this module's directory
        module_dir = Path(__file__).resolve().parent
        for ancestor in [module_dir] + list(module_dir.parents):
            candidate = ancestor / "project_config.json"
            if candidate not in candidates:
                candidates.append(candidate)

        for path in candidates:
            if path.exists():
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    llm_section = raw.get("llm")
                    if llm_section:
                        # Deep-merge: config providers override defaults,
                        # but missing providers still get built-in defaults.
                        merged: dict[str, Any] = {
                            "active_provider": llm_section.get(
                                "active_provider", _DEFAULT_ACTIVE_PROVIDER
                            ),
                            # Retry budget for transient provider errors (503/502/
                            # 429-rate-limit/timeout). Read here so llm.max_retries in
                            # project_config actually takes effect (previously the
                            # retry loop read a non-existent attribute and always used 2).
                            "max_retries": llm_section.get("max_retries", 2),
                            # Dedicated (small) retry cap for TIMEOUT-classified
                            # errors — kept separate from max_retries so an
                            # expensive timeout is not retried the full budget.
                            "timeout_max_retries": llm_section.get("timeout_max_retries", 1),
                            # Per-request wall-clock timeout (seconds). Bounds each
                            # litellm.completion attempt so a hung/stalled provider
                            # stream fails fast, instead of blocking forever. Default
                            # 1800s is deliberately generous for large 1M-context +
                            # extended-thinking generations; aggregate cost/time is
                            # bounded by the retry cap + circuit_breaker below.
                            "request_timeout_seconds": llm_section.get(
                                "request_timeout_seconds", 1800
                            ),
                            # Process-level aggregate safety net (see _CircuitBreaker).
                            "circuit_breaker": llm_section.get("circuit_breaker", {}),
                            # Adaptive per-attempt timeout-escalation ladder for the
                            # scoped stages (SRS / feature-story). See _complete_impl.
                            "timeout_fallback": llm_section.get("timeout_fallback", {}),
                            # Providers for which streaming is enabled (list, case-
                            # insensitive). Default [] = OFF everywhere. See the
                            # streaming block in _complete_impl.
                            "stream_providers": llm_section.get("stream_providers", []),
                            # Per-provider DEEP merge: a project's provider block
                            # overrides individual default keys but does NOT wipe the
                            # rest. This means new default keys (e.g.
                            # reasoning_effort_levels) are inherited automatically —
                            # projects don't have to re-declare them, and the
                            # provider-aware ladder works without per-project edits.
                            "providers": self._merge_providers(
                                llm_section.get("providers", {}) or {}
                            ),
                        }
                        print(
                            f"[LLMClient] Config loaded from {path.name} — "
                            f"active provider: '{merged['active_provider']}'"
                        )
                        return merged
                    else:
                        print(
                            f"[LLMClient] No 'llm' section in {path.name}. "
                            "Using built-in defaults (openai/gpt-4o)."
                        )
                except Exception as parse_err:
                    print(f"[LLMClient] Warning: could not parse {path}: {parse_err}")

        # Nothing found — use built-in defaults
        print("[LLMClient] project_config.json not found. Using built-in defaults.")
        return {
            "active_provider": _DEFAULT_ACTIVE_PROVIDER,
            "max_retries": 2,
            "timeout_max_retries": 1,
            "request_timeout_seconds": 1800,
            "circuit_breaker": {},
            "timeout_fallback": {},
            "stream_providers": [],
            "providers": dict(_PROVIDER_DEFAULTS),
        }

    def _resolve_model(self, use_reasoning_model: bool) -> str:
        """Returns the correct litellm model string for this call."""
        if self._model_override:
            return self._model_override
        key = "reasoning_model" if use_reasoning_model else "model"
        return self._provider_cfg.get(key, self._provider_cfg.get("model", "gpt-4o"))

    def _is_reasoning_model(self, model: str) -> bool:
        """
        Returns True when the given model string is a reasoning model.

        Detection strategy (in order of precedence):
          1. Exact match against the provider's configured ``reasoning_model``
             string.  This is the primary, zero-false-positive check — it works
             regardless of naming conventions and avoids substring collisions
             (e.g.  "gpt-5" would wrongly match "gpt-5-mini" in a pure keyword
             scan; exact match handles this correctly).
          2. Keyword-fallback: if the model string differs from the configured
             ``reasoning_model`` (e.g. a versioned alias or an override not
             listed in config), any keyword in ``reasoning_keywords`` that
             appears as a complete path-segment or dash-segment in the model
             string is accepted.  This keeps the door open for lightweight
             variant names without reintroducing false positives.

        Config is the single source of truth — adding a new provider's
        reasoning model requires only a ``project_config.json`` edit.
        """
        # ── Primary: exact config match ───────────────────────────────────────
        configured_reasoning = self._provider_cfg.get("reasoning_model", "")
        if configured_reasoning and model == configured_reasoning:
            return True

        # ── Secondary: segment-aware keyword fallback ─────────────────────────
        # Split model string on "/" and "-" to get discrete segments, then match
        # each keyword against whole segments only.  This prevents "gpt-5"
        # matching "gpt-5-mini" while still catching "o3" in "openai/o3-mini".
        keywords: list[str] = self._provider_cfg.get("reasoning_keywords", [])
        if not keywords:
            return False
        model_segments = set(
            seg.lower() for part in model.lower().split("/") for seg in part.split("-") if seg
        )
        return any(kw.lower() in model_segments for kw in keywords)

    # Public alias — callers use this; private _is_reasoning_model is the impl.
    def is_reasoning_model(self, model: str) -> bool:
        """
        Public interface for reasoning-model detection.

        Delegates to ``_is_reasoning_model``.  Callers (ai_runner, review_agent,
        etc.) **must** use this method rather than maintaining their own keyword
        lists — the provider config is the single source of truth.

        Parameters
        ----------
        model : str
            The litellm model string to test (e.g. ``"deepseek/deepseek-v4-pro"``).

        Returns
        -------
        bool
            True if the model is classified as a reasoning / thinking model.
        """
        return self._is_reasoning_model(model)

    def _response_format_supported(self) -> bool:
        """
        Whether the ACTIVE provider accepts the ``response_format`` request parameter.

        False when either (a) the provider block sets ``supports_response_format: false``
        (config-driven, per-provider — flip back to true when the provider restores it),
        or (b) the provider was discovered at runtime to reject it (session cache in
        _RESPONSE_FORMAT_DISABLED, populated by the self-healing path in complete()).
        """
        if self._provider_name in _RESPONSE_FORMAT_DISABLED:
            return False
        return bool(self._provider_cfg.get("supports_response_format", True))

    def _adapt_response_format(self, model: str, response_format: dict) -> dict[str, Any]:
        """
        Ensures ``response_format`` is compatible with the target provider.

        json_schema
            Sent as-is only if litellm reports the model supports structured
            outputs (currently OpenAI GPT-4o and newer).  Falls back to
            json_object for all other providers.

        json_object
            Passed through directly; litellm handles provider-level
            translation (e.g. system-prompt injection for Anthropic).

        Any other value
            Passed through verbatim.
        """
        fmt_type = response_format.get("type", "")

        if fmt_type == "json_schema":
            try:
                if litellm.supports_response_schema(model=model):
                    return {"response_format": response_format}
                else:
                    print(
                        f"[LLMClient] '{model}' does not support json_schema structured "
                        "outputs — falling back to json_object. "
                        "Existing JSON parsing in the caller handles this transparently."
                    )
                    return {"response_format": {"type": "json_object"}}
            except Exception:
                # supports_response_schema can raise for unknown models; default to json_object
                return {"response_format": {"type": "json_object"}}

        # json_object and any other format type: pass through as-is
        return {"response_format": response_format}
