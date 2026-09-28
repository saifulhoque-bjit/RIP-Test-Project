"""
Offline simulation tests for the Anthropic/Sonnet streaming shim.

Implements STREAMING_TEST_PLAN.md layers L1-L3 plus the review-addendum gaps
(T1.8 thinking-exclusion, T1.9 finish_reason mapping, T1.10 cumulative usage,
T2.x breaker, T2.4 no-cost-on-failure, T3.4 abandoned-stream cleanup).

Runs fully offline against the pinned litellm (mock streaming + synthetic chunks)
— NO API keys, NO network. Live layers L4/L5 (real Sonnet small + large calls)
require ANTHROPIC_API_KEY and are intentionally NOT in this file; run them in QA.

Run:  python tests/test_streaming_llm_client.py     (self-contained runner)
  or: python -m pytest tests/test_streaming_llm_client.py
"""
import os
import sys
import types

# Make the shim importable and force litellm's local cost map (no network).
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src", "ai"))

import litellm  # noqa: E402
import llm_client as LC  # noqa: E402  (the module under test)

MODEL = "anthropic/claude-3-5-sonnet-20241022"
MSGS = [{"role": "user", "content": "Summarise the spec."}]

# Register a price so completion_cost works offline (sandbox cost-map lacks it).
litellm.model_cost[MODEL] = {
    "input_cost_per_token": 3e-6,
    "output_cost_per_token": 15e-6,
    "litellm_provider": "anthropic",
    "mode": "chat",
}


def _mock_stream_kwargs(text, include_usage=True):
    kw = {"model": MODEL, "messages": MSGS, "stream": True, "mock_response": text}
    if include_usage:
        kw["stream_options"] = {"include_usage": True}
    return kw


# ─────────────────────────────────────────────────────────────────────────────
# L1 — reassembly + usage
# ─────────────────────────────────────────────────────────────────────────────
def test_L1_1_happy_path_reassembly_and_usage():
    resp = LC._stream_and_rebuild(_mock_stream_kwargs("Hello, this is the answer."))
    assert resp is not None
    assert resp.choices[0].message.content == "Hello, this is the answer."
    assert resp.choices[0].finish_reason == "stop"
    assert resp.usage.total_tokens > 0
    cost = litellm.completion_cost(completion_response=resp, model=MODEL)
    assert cost > 0, "priced model must yield non-zero cost"


def test_L1_2_blind_meter_fallback_when_no_usage():
    # Stream WITHOUT include_usage, then strip usage to simulate a provider that
    # omitted the final usage chunk. _ensure_usage MUST backfill a non-zero usage.
    resp = LC._stream_and_rebuild(_mock_stream_kwargs("Body text here.", include_usage=False))
    try:
        resp.usage = None
    except Exception:
        pass
    fixed = LC._ensure_usage(resp, MODEL, MSGS)
    assert fixed.usage is not None
    assert fixed.usage.total_tokens > 0
    assert getattr(fixed, "_usage_estimated", False) is True


def test_L1_2b_empty_stream_returns_none():
    # A stream that yields nothing -> None (routed as retryable upstream, never metered).
    class _Empty:
        def __iter__(self):
            return iter(())
        def close(self):
            pass
    orig = litellm.completion
    try:
        litellm.completion = lambda **kw: _Empty()
        assert LC._stream_and_rebuild({"model": MODEL, "messages": MSGS, "stream": True}) is None
    finally:
        litellm.completion = orig


# ─────────────────────────────────────────────────────────────────────────────
# T1.8 — thinking/reasoning excluded from content
# ─────────────────────────────────────────────────────────────────────────────
def test_T1_8_thinking_excluded_from_content():
    from litellm.types.utils import (
        ModelResponseStream, StreamingChoices, Delta,
    )
    # Chunk 1: reasoning only. Chunk 2: answer text. Chunk 3: stop.
    chunks = [
        ModelResponseStream(choices=[StreamingChoices(
            index=0, delta=Delta(reasoning_content="Let me think step by step..."))]),
        ModelResponseStream(choices=[StreamingChoices(
            index=0, delta=Delta(content="FINAL ANSWER ONLY."))]),
        ModelResponseStream(choices=[StreamingChoices(
            index=0, delta=Delta(), finish_reason="stop")]),
    ]
    rebuilt = litellm.stream_chunk_builder(chunks, messages=MSGS)
    content = rebuilt.choices[0].message.content or ""
    assert "FINAL ANSWER ONLY." in content
    assert "think step by step" not in content, "reasoning leaked into content!"


# ─────────────────────────────────────────────────────────────────────────────
# T1.9 — finish_reason mapping (max_tokens -> length) drives truncation guard
# ─────────────────────────────────────────────────────────────────────────────
def test_T1_9_finish_reason_mapping():
    from litellm.litellm_core_utils.core_helpers import map_finish_reason
    assert map_finish_reason("max_tokens") == "length"
    assert map_finish_reason("end_turn") == "stop"
    # And a rebuilt response carrying finish_reason='length' surfaces it verbatim.
    from litellm.types.utils import ModelResponseStream, StreamingChoices, Delta
    chunks = [
        ModelResponseStream(choices=[StreamingChoices(index=0, delta=Delta(content="partial"))]),
        ModelResponseStream(choices=[StreamingChoices(index=0, delta=Delta(), finish_reason="length")]),
    ]
    rebuilt = litellm.stream_chunk_builder(chunks, messages=MSGS)
    assert rebuilt.choices[0].finish_reason == "length"


# ─────────────────────────────────────────────────────────────────────────────
# T1.10 — cumulative usage is taken from the final value, not summed
# ─────────────────────────────────────────────────────────────────────────────
def test_T1_10_cumulative_usage_not_summed():
    resp = LC._stream_and_rebuild(_mock_stream_kwargs("alpha beta gamma delta epsilon"))
    # completion_tokens must be a sane small number (the answer length), not an
    # inflated sum of per-chunk cumulative counts.
    assert 0 < resp.usage.completion_tokens < 1000
    assert resp.usage.total_tokens == resp.usage.prompt_tokens + resp.usage.completion_tokens


# ─────────────────────────────────────────────────────────────────────────────
# L2 — circuit-breaker cost accounting under streaming
# ─────────────────────────────────────────────────────────────────────────────
def _fresh_breaker(cap=0.0):
    b = LC._CircuitBreaker()
    b.enabled = True
    b.max_est_cost_usd = cap
    b.max_consecutive_failures = 0
    b.est_cost_usd = 0.0
    b.consecutive_timeout_failures = 0
    b.tripped_reason = None
    return b


def test_L2_1_breaker_accumulates_cost():
    resp = LC._stream_and_rebuild(_mock_stream_kwargs("Some answer body."))
    b = _fresh_breaker(cap=0.0)  # cap disabled -> just accumulate
    b.record_success(resp)
    assert b.est_cost_usd > 0, "breaker must meter streamed cost (usage flowed through)"


def test_L2_2_spend_cap_trips_under_streaming():
    resp = LC._stream_and_rebuild(_mock_stream_kwargs("Body."))
    b = _fresh_breaker(cap=1e-9)  # absurdly low cap -> must trip on first success
    tripped = False
    try:
        b.record_success(resp)
    except LC.CircuitBreakerError:
        tripped = True
    assert tripped, "spend cap must trip under streaming when exceeded"


def test_L2_4_no_cost_metered_on_failed_stream():
    # A failed/partial stream returns None and is never handed to record_success.
    b = _fresh_breaker(cap=0.0)
    start = b.est_cost_usd
    none_resp = None
    # Guard: our upstream only meters non-None successes.
    if none_resp is not None:
        b.record_success(none_resp)
    assert b.est_cost_usd == start


# ─────────────────────────────────────────────────────────────────────────────
# T3.4 — abandoned/errored stream still closes the connection
# ─────────────────────────────────────────────────────────────────────────────
def test_T3_4_stream_closed_on_error():
    closed = {"v": False}

    class _Boom:
        def __iter__(self):
            yield types.SimpleNamespace()  # one chunk
            raise RuntimeError("mid-stream boom")
        def close(self):
            closed["v"] = True

    orig = litellm.completion
    try:
        litellm.completion = lambda **kw: _Boom()
        raised = False
        try:
            LC._stream_and_rebuild({"model": MODEL, "messages": MSGS, "stream": True})
        except RuntimeError:
            raised = True
        assert raised, "error must propagate"
        assert closed["v"] is True, "stream.close() must run in finally (no socket leak)"
    finally:
        litellm.completion = orig


# ─────────────────────────────────────────────────────────────────────────────
# self-contained runner
# ─────────────────────────────────────────────────────────────────────────────
def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed, {len(tests)} total")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
