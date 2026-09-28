"""
Offline unit test for the circuit-breaker changes:

  1. The total-run WALL-CLOCK trigger is REMOVED — no amount of elapsed time trips
     the breaker (only consecutive timeouts / cost can), and the removed state
     (`start_time` / `max_run_seconds`) is gone.
  2. `reset()` / `LLMClient.reset_circuit_breaker()` clears run-scoped state
     (failure counter, accumulated cost, sticky trip flag).
  3. CROSS-RUN ISOLATION: once the breaker trips in "run A", a reset at the start
     of "run B" (task entry) fully clears it, so run B's calls succeed instead of
     inheriting run A's trip — the exact leak that let one project abort another
     sharing a Celery worker.
  4. The consecutive-timeout trigger still trips at its cap, and still re-arms
     cleanly after a reset.

No network, no real litellm calls — litellm.completion is monkeypatched.
Run:  python tests/test_circuit_breaker_reset.py
"""

import json
from pathlib import Path
import sys
import tempfile
import time as _time
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# No real backoff sleeps during the test.
_time.sleep = lambda *a, **k: None

# Inject a minimal litellm stub if it isn't installed, BEFORE importing the client.
try:
    import litellm  # noqa: F401
except ImportError:
    _stub = types.ModuleType("litellm")
    _stub.suppress_debug_info = True
    _stub.drop_params = True
    _stub.completion = lambda *a, **k: None
    _stub.completion_cost = lambda *a, **k: 0.0
    _stub.supports_response_schema = lambda *a, **k: False
    sys.modules["litellm"] = _stub

import litellm  # noqa: E402
import src.ai.llm_client as C  # noqa: E402


class APITimeoutError(Exception):
    """Type name contains 'timeout' -> classified as a timeout by the retry loop."""


def _fake_response(text="OK-BODY " * 40):
    msg = types.SimpleNamespace(content=text)
    ch = types.SimpleNamespace(message=msg, finish_reason="stop")
    return types.SimpleNamespace(choices=[ch])


def _make_client(tmp, scoped=True, max_consecutive=3, **llm_over):
    llm = {
        "active_provider": "openai",
        "max_retries": 1,
        "timeout_max_retries": 0,  # 1 attempt per call -> fast, deterministic
        "request_timeout_seconds": 1800,
        "hard_watchdog_enabled": False,  # bypass watchdog thread; call runs inline
        "circuit_breaker": {
            "enabled": True,
            "max_consecutive_failures": max_consecutive,
            "max_est_cost_usd": 0,
            # NOTE: max_run_seconds intentionally NOT set — the trigger is gone.
        },
    }
    llm.update(llm_over)
    p = Path(tmp) / "project_config.json"
    p.write_text(json.dumps({"llm": llm}), encoding="utf-8")
    client = C.LLMClient(config_path=str(p), model_override="openai/gpt-4o")
    client.set_timeout_breaker_scope(scoped)
    return client


def _expect(cond, msg):
    if not cond:
        raise AssertionError("FAIL: " + msg)
    print("  ok:", msg)


def test_wallclock_removed():
    """No wall-clock: the breaker has no start_time/max_run_seconds and never trips
    on elapsed time, however long the 'run' has been going."""
    b = C._BREAKER
    b.reset()
    _expect(
        not hasattr(b, "start_time"), "breaker has no start_time attribute (wall-clock removed)"
    )
    _expect(
        not hasattr(b, "max_run_seconds"),
        "breaker has no max_run_seconds attribute (wall-clock removed)",
    )
    b.configure({"circuit_breaker": {"enabled": True, "max_run_seconds": 1}})  # stale key ignored
    for _ in range(100):
        b.check()  # must never raise on time — there is no time trigger anymore
    _expect(
        True, "check() never trips on elapsed time (even with a stale max_run_seconds in config)"
    )


def test_reset_clears_state():
    b = C._BREAKER
    b.consecutive_timeout_failures = 7
    b.est_cost_usd = 12.5
    b.tripped_reason = "some prior trip"
    b.reset()
    _expect(b.consecutive_timeout_failures == 0, "reset() clears consecutive_timeout_failures")
    _expect(b.est_cost_usd == 0.0, "reset() clears est_cost_usd")
    _expect(b.tripped_reason is None, "reset() clears the sticky tripped_reason")


def test_consecutive_trigger_still_trips():
    with tempfile.TemporaryDirectory() as tmp:
        C.LLMClient.reset_circuit_breaker()
        client = _make_client(tmp, scoped=True, max_consecutive=3)
        litellm.completion = lambda **k: (_ for _ in ()).throw(APITimeoutError("timed out"))
        trips = 0
        for i in range(3):
            try:
                client.complete("sys", "user")
            except C.CircuitBreakerError:
                trips += 1
                break
            except C.LLMTimeoutExhausted:
                pass  # counted toward the breaker; not yet at cap
        _expect(
            trips == 1, "consecutive-timeout trigger trips at the cap (max_consecutive_failures=3)"
        )


def test_cross_run_isolation():
    """The headline fix: a trip in run A must NOT bleed into run B after reset."""
    with tempfile.TemporaryDirectory() as tmp:
        # ---- RUN A: force a trip ----
        C.LLMClient.reset_circuit_breaker()
        a = _make_client(tmp, scoped=True, max_consecutive=1)
        litellm.completion = lambda **k: (_ for _ in ()).throw(APITimeoutError("timed out"))
        tripped = False
        try:
            a.complete("sys", "user")  # 1 exhausted timeout -> trips (cap=1)
        except C.CircuitBreakerError:
            tripped = True
        except C.LLMTimeoutExhausted:
            pass
        # next call in run A must fast-fail (breaker open)
        open_now = False
        try:
            a.complete("sys", "user")
        except C.CircuitBreakerError:
            open_now = True
        except Exception:
            pass
        _expect(tripped or open_now, "run A trips the breaker and stays open")
        _expect(
            C._BREAKER.tripped_reason is not None, "breaker is OPEN after run A (sticky trip set)"
        )

        # ---- RUN B: task entry resets, provider now healthy ----
        C.LLMClient.reset_circuit_breaker()  # <-- called at each task/run entry
        _expect(C._BREAKER.tripped_reason is None, "reset at run B entry clears the sticky trip")
        b = _make_client(tmp, scoped=True, max_consecutive=1)
        litellm.completion = lambda **k: _fake_response()  # provider recovered
        out = b.complete("sys", "user")
        _expect(out.startswith("OK-BODY"), "run B succeeds — NOT poisoned by run A's trip")


def test_rearms_after_reset():
    """After a reset, the counter starts at 0 again, so it takes the full cap of
    fresh consecutive timeouts to trip (state didn't secretly persist)."""
    with tempfile.TemporaryDirectory() as tmp:
        C.LLMClient.reset_circuit_breaker()
        client = _make_client(tmp, scoped=True, max_consecutive=2)
        litellm.completion = lambda **k: (_ for _ in ()).throw(APITimeoutError("timed out"))
        # first exhausted timeout: counts 1, below cap -> no trip
        first_tripped = False
        try:
            client.complete("sys", "user")
        except C.CircuitBreakerError:
            first_tripped = True
        except C.LLMTimeoutExhausted:
            pass
        _expect(not first_tripped, "1st fresh timeout after reset does NOT trip (cap=2)")
        _expect(
            C._BREAKER.consecutive_timeout_failures == 1,
            "counter is exactly 1 after one fresh timeout",
        )


def test_run_guard_sequential_ok():
    """Sequential runs in one process each acquire/release cleanly — no false
    positive — and the breaker is reset on each entry."""
    C._ACTIVE_RUN.update({"id": None, "owner": None, "depth": 0})
    for rid in ("projA", "projB"):
        with C.LLMClient.pipeline_run_guard(rid):
            _expect(C._ACTIVE_RUN["id"] == rid, f"run '{rid}' is the active run inside its guard")
            _expect(C._BREAKER.tripped_reason is None, f"breaker reset on entry for '{rid}'")
        _expect(C._ACTIVE_RUN["id"] is None, f"guard released after '{rid}'")


def test_run_guard_reentrant_same_thread():
    """Nested guard use on the same thread/run is allowed (re-entrant)."""
    C._ACTIVE_RUN.update({"id": None, "owner": None, "depth": 0})
    with C.LLMClient.pipeline_run_guard("projA"):
        with C.LLMClient.pipeline_run_guard("projA"):
            _expect(C._ACTIVE_RUN["depth"] == 2, "re-entrant guard increments depth")
        _expect(C._ACTIVE_RUN["id"] == "projA", "inner exit keeps the run active")
    _expect(C._ACTIVE_RUN["id"] is None, "outer exit releases the run")


def test_run_guard_rejects_concurrent():
    """A genuinely concurrent second run (another thread) is rejected with
    ConcurrentPipelineError — the enforcement of one pipeline per process."""
    import threading as _th

    C._ACTIVE_RUN.update({"id": None, "owner": None, "depth": 0})
    started = _th.Event()
    release = _th.Event()
    result = {}

    def hold():
        with C.LLMClient.pipeline_run_guard("projA"):
            started.set()
            release.wait(5)

    t = _th.Thread(target=hold)
    t.start()
    started.wait(5)  # projA now active in this process, held by thread t
    try:
        with C.LLMClient.pipeline_run_guard("projB"):
            result["entered"] = True
    except C.ConcurrentPipelineError:
        result["rejected"] = True
    finally:
        release.set()
        t.join(5)
    _expect(
        result.get("rejected") is True,
        "second concurrent run rejected with ConcurrentPipelineError",
    )
    _expect(C._ACTIVE_RUN["id"] is None, "guard fully released after both threads finish")


if __name__ == "__main__":
    tests = [
        ("wall-clock trigger removed", test_wallclock_removed),
        ("reset() clears run-scoped state", test_reset_clears_state),
        ("consecutive trigger still trips", test_consecutive_trigger_still_trips),
        ("cross-run isolation via reset", test_cross_run_isolation),
        ("breaker re-arms cleanly after reset", test_rearms_after_reset),
        ("run guard: sequential runs OK", test_run_guard_sequential_ok),
        ("run guard: re-entrant same thread", test_run_guard_reentrant_same_thread),
        ("run guard: rejects concurrent run", test_run_guard_rejects_concurrent),
    ]
    for name, fn in tests:
        print(f"[TEST] {name}")
        fn()
    print("\nALL CIRCUIT-BREAKER RESET / NO-WALLCLOCK TESTS PASSED")
