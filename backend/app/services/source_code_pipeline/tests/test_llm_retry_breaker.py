"""
Offline unit test for the LLMClient timeout-retry cap + circuit-breaker
(no network, no real litellm calls — litellm.completion is monkeypatched).

Covers the four cost-safety fixes:
  1. Timeout-classified errors use the small dedicated budget (timeout_max_retries,
     default 1) -> at most 2 attempts, NOT the generic max_retries budget.
  3. A generic transient error uses max_retries -> attempts bounded there.
  4. The aggregate circuit-breaker trips after N consecutive failures and then
     fast-fails subsequent calls WITHOUT hitting the provider.
"""

import json
from pathlib import Path
import sys
import tempfile
import time as _time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# No real backoff sleeps during the test.
_time.sleep = lambda *a, **k: None

# If litellm isn't installed in this environment, inject a minimal stub BEFORE
# importing the client so LITELLM_AVAILABLE is True and litellm.completion is
# patchable. The tests never reach a successful call, so only `completion`
# (monkeypatched below) and a couple of attributes are needed.
try:
    import litellm  # noqa: F401
except ImportError:
    import types as _types

    _stub = _types.ModuleType("litellm")
    _stub.suppress_debug_info = True
    _stub.drop_params = True
    _stub.completion = lambda *a, **k: None
    _stub.completion_cost = lambda *a, **k: 0.0
    _stub.supports_response_schema = lambda *a, **k: False
    sys.modules["litellm"] = _stub

import src.ai.llm_client as C  # noqa: E402


class APITimeoutError(Exception):
    """Type name contains 'apitimeout'/'timeout' -> classified as a timeout."""


class ServiceUnavailableError(Exception):
    """Generic transient (503) — NOT a timeout."""


def _reset_breaker():
    b = C._BREAKER
    b.start_time = None
    b.consecutive_timeout_failures = 0
    b.est_cost_usd = 0.0
    b.tripped_reason = None


def _make_client(tmp, scoped=True, **llm_over):
    llm = {
        "active_provider": "openai",
        "max_retries": 3,
        "timeout_max_retries": 1,
        "request_timeout_seconds": 1800,
        "circuit_breaker": {
            "max_consecutive_failures": 4,
            "max_est_cost_usd": 0,  # disabled for the test
            "max_run_seconds": 0,  # disabled for the test
        },
    }
    llm.update(llm_over)
    p = Path(tmp) / "project_config.json"
    p.write_text(json.dumps({"llm": llm}), encoding="utf-8")
    client = C.LLMClient(config_path=str(p), model_override="openai/gpt-4o")
    # Opt into the timeout breaker by default (as SRS / feature-story stages do).
    client.set_timeout_breaker_scope(scoped)
    return client


class _Counter:
    def __init__(self, exc):
        self.n = 0
        self.exc = exc

    def __call__(self, *a, **k):
        self.n += 1
        raise self.exc


def _fake_response(text="OK-SPEC-BODY " * 40):
    import types as _t

    msg = _t.SimpleNamespace(content=text)
    ch = _t.SimpleNamespace(message=msg, finish_reason="stop")
    return _t.SimpleNamespace(choices=[ch])


class _LadderProbe:
    """Records (timeout, reasoning_effort) per attempt; times out for the first
    `fail_until` attempts, then succeeds."""

    def __init__(self, fail_until):
        self.calls = []
        self.fail_until = fail_until

    def __call__(self, *a, **k):
        self.calls.append((k.get("timeout"), k.get("reasoning_effort")))
        if len(self.calls) <= self.fail_until:
            raise APITimeoutError("timed out")
        return _fake_response()


def _make_reasoning_client(tmp, provider="anthropic", **cb):
    """A scoped client on a reasoning provider, with a 4-rung quality-first ladder."""
    llm = {
        "active_provider": provider,
        "max_retries": 2,
        "timeout_max_retries": 3,
        "request_timeout_seconds": 100,
        "timeout_fallback": {
            "enabled": True,
            "ladder": [
                {},
                {"timeout_add_seconds": 50},
                {"timeout_add_seconds": 100},
                {"timeout_add_seconds": 100, "reasoning_effort_step": -1},
            ],
        },
        "circuit_breaker": {
            "max_consecutive_failures": 99,
            "max_est_cost_usd": 0,
            "max_run_seconds": 0,
        },
    }
    p = Path(tmp) / "project_config.json"
    p.write_text(json.dumps({"llm": llm}), encoding="utf-8")
    return C.LLMClient(config_path=str(p)).set_timeout_breaker_scope(True)


def run():
    if not C.LITELLM_AVAILABLE:
        print("  SKIP: litellm not installed in this environment")
        return
    with tempfile.TemporaryDirectory() as t:
        # ── Fix #1: timeout retries cap at 1 (2 attempts total) ───────────────
        _reset_breaker()
        client = _make_client(t)
        counter = _Counter(APITimeoutError("Request timed out"))
        C.litellm.completion = counter
        raised = None
        try:
            client.complete("sys", "user")
        except C.CircuitBreakerError as e:
            raise AssertionError(f"should not trip breaker on 1 call: {e}")
        except RuntimeError as e:
            raised = e
        assert raised is not None, "expected RuntimeError after timeout budget exhausted"
        assert counter.n == 2, f"timeout should give 2 attempts (1 retry), got {counter.n}"
        print(f"  ok: timeout capped at {counter.n} attempts (1 retry)")

        # ── Fix #3: a generic transient uses the larger max_retries budget ────
        _reset_breaker()
        client = _make_client(t)
        counter = _Counter(ServiceUnavailableError("503 service unavailable"))
        C.litellm.completion = counter
        try:
            client.complete("sys", "user")
        except RuntimeError:
            pass
        assert counter.n == 4, f"transient max_retries=3 should give 4 attempts, got {counter.n}"
        print(f"  ok: generic transient used full budget ({counter.n} attempts)")

        # ── Fix #4: consecutive-failure breaker trips and then fast-fails ─────
        _reset_breaker()
        client = _make_client(t)
        counter = _Counter(APITimeoutError("timed out"))
        C.litellm.completion = counter
        tripped_on = None
        for i in range(1, 6):
            try:
                client.complete("sys", "user")
            except C.CircuitBreakerError:
                tripped_on = i
                break
            except RuntimeError:
                continue
        assert tripped_on == 4, (
            f"breaker should trip on the 4th consecutive timeout, got {tripped_on}"
        )
        calls_at_trip = counter.n
        # A further call must fast-fail on check() WITHOUT invoking the provider.
        try:
            client.complete("sys", "user")
            raise AssertionError("breaker should stay open and raise on next call")
        except C.CircuitBreakerError:
            pass
        assert counter.n == calls_at_trip, (
            f"open breaker must not call the provider (calls {calls_at_trip} -> {counter.n})"
        )
        print(f"  ok: timeout breaker tripped on failure #{tripped_on}; open breaker fast-fails")

        # ── Scope: a NON-timeout failure never trips the breaker (cap=1) ──────
        _reset_breaker()
        client = _make_client(
            t,
            max_retries=0,
            circuit_breaker={
                "max_consecutive_failures": 1,
                "max_est_cost_usd": 0,
                "max_run_seconds": 0,
            },
        )
        counter = _Counter(ServiceUnavailableError("503 service unavailable"))
        C.litellm.completion = counter
        for _ in range(4):
            try:
                client.complete("sys", "user")
            except C.CircuitBreakerError:
                raise AssertionError("non-timeout failure must NOT trip the timeout breaker")
            except RuntimeError:
                pass
        assert counter.n >= 4, "non-timeout calls should keep running (item fails, run continues)"
        print("  ok: non-timeout failures never trip the breaker (run continues)")

        # ── Scope: a timeout on an UN-SCOPED client never trips the breaker ───
        _reset_breaker()
        client = _make_client(
            t,
            scoped=False,
            max_retries=0,
            circuit_breaker={
                "max_consecutive_failures": 1,
                "max_est_cost_usd": 0,
                "max_run_seconds": 0,
            },
        )
        counter = _Counter(APITimeoutError("timed out"))
        C.litellm.completion = counter
        for _ in range(3):
            try:
                client.complete("sys", "user")
            except C.CircuitBreakerError:
                raise AssertionError("un-scoped stage timeout must NOT trip the breaker")
            except RuntimeError:
                pass
        assert counter.n >= 3, "un-scoped timeouts should fail their item without aborting"
        print("  ok: timeouts outside SRS/feature-story scope never trip the breaker")

        # ── Ladder: timeout escalates per rung; reasoning kept until last ─────
        _reset_breaker()
        client = _make_reasoning_client(t)
        probe = _LadderProbe(fail_until=2)  # succeeds on attempt 3 (index 2)
        C.litellm.completion = probe
        out = client.complete("sys", "user")
        assert out and "OK-SPEC-BODY" in out, "ladder call should have returned content"
        # base=100; rungs add 0, 50, 100 -> reasoning stays default 'high' through attempt 3
        assert probe.calls == [(100, "high"), (150, "high"), (200, "high")], probe.calls
        assert client.last_recovery and client.last_recovery["attempt"] == 3
        assert client.last_recovery["degraded_reasoning"] is False
        print(f"  ok: ladder escalated timeout {[c[0] for c in probe.calls]}, reasoning kept high")

        # ── Ladder: last rung drops reasoning one PROVIDER-AWARE level ────────
        _reset_breaker()
        client = _make_reasoning_client(t)
        probe = _LadderProbe(fail_until=3)  # succeeds on attempt 4 (index 3, last rung)
        C.litellm.completion = probe
        out = client.complete("sys", "user")
        # Anthropic scale is [low,medium,high,max]; default 'high', step -1 -> 'medium'
        assert probe.calls[-1] == (200, "medium"), probe.calls
        assert client.last_recovery["degraded_reasoning"] is True
        assert client.last_recovery["reasoning_effort"] == "medium"
        print("  ok: last rung downgraded reasoning high -> medium (provider-aware) + tagged")

        # ── Ladder resets per item: a fresh first-attempt success is clean ────
        _reset_breaker()
        probe = _LadderProbe(fail_until=0)  # succeeds immediately
        C.litellm.completion = probe
        client.complete("sys", "user")
        assert probe.calls[0] == (100, "high"), probe.calls
        assert client.last_recovery is None, "first-attempt success must not be a recovery"
        print("  ok: ladder resets per item (fresh call starts at base timeout / default effort)")

        # ── Deep merge: a project provider block that OMITS reasoning_effort_levels
        #    still inherits it from defaults, so the downgrade works. ───────────
        _reset_breaker()
        llm = {
            "active_provider": "anthropic",
            "max_retries": 2,
            "timeout_max_retries": 3,
            "request_timeout_seconds": 100,
            # Project overrides anthropic but does NOT restate reasoning_effort_levels:
            "providers": {"anthropic": {"_reasoning_params": {"reasoning_effort": "high"}}},
            "timeout_fallback": {
                "enabled": True,
                "ladder": [
                    {},
                    {"timeout_add_seconds": 50},
                    {"timeout_add_seconds": 100},
                    {"timeout_add_seconds": 100, "reasoning_effort_step": -1},
                ],
            },
            "circuit_breaker": {
                "max_consecutive_failures": 99,
                "max_est_cost_usd": 0,
                "max_run_seconds": 0,
            },
        }
        p = Path(t) / "project_config.json"
        p.write_text(json.dumps({"llm": llm}), encoding="utf-8")
        client = C.LLMClient(config_path=str(p)).set_timeout_breaker_scope(True)
        assert client._provider_cfg.get("reasoning_effort_levels") == [
            "low",
            "medium",
            "high",
            "max",
        ], "project provider block should inherit reasoning_effort_levels from defaults"
        probe = _LadderProbe(fail_until=3)
        C.litellm.completion = probe
        client.complete("sys", "user")
        assert probe.calls[-1] == (200, "medium"), probe.calls
        print("  ok: project provider block inherits effort scale (deep merge) — downgrade works")

        # ── OpenAI GPT-5: classified reasoning + ladder downgrades high->medium ─
        _reset_breaker()
        client = _make_reasoning_client(t, provider="openai")
        assert client.is_reasoning_model(client.active_reasoning_model), (
            "GPT-5 must be classified as a reasoning model"
        )
        probe = _LadderProbe(fail_until=3)
        C.litellm.completion = probe
        client.complete("sys", "user")
        # GPT-5 scale minimal|low|medium|high, default high, step -1 -> medium
        assert probe.calls[-1] == (200, "medium"), probe.calls
        print("  ok: OpenAI GPT-5 reasoning + last-rung downgrade high -> medium")

    print("\nALL LLM RETRY/BREAKER TESTS PASSED")


if __name__ == "__main__":
    run()
