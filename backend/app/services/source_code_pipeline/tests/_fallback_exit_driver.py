"""Subprocess driver for the fallback simulation.

Loads the REAL project config, forces every LLM call to time out, and drives a
scoped (SRS-style) client until the circuit-breaker aborts. It routes each call
through a `call_llm_api`-style broad `except Exception` swallow to prove the
CircuitBreakerError (a BaseException) ESCAPES it, then catches it at the top with
the exact same handler as src/cli/main.py's __main__ guard and exits with code 2.

Run indirectly via tests/simulate_fallback.py (asserts the exit code).
"""

from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import time as _time

_time.sleep = lambda *a, **k: None  # skip backoff waits — instant simulation


class APITimeoutError(Exception):
    """Name contains 'timeout' -> classified as a timeout by LLMClient."""


# Force litellm.completion to always time out (stub if litellm is absent).
try:
    import litellm as _ll
except ImportError:
    _ll = types.ModuleType("litellm")
    _ll.suppress_debug_info = True
    _ll.drop_params = True
    _ll.completion_cost = lambda *a, **k: 0
    _ll.supports_response_schema = lambda *a, **k: False
    sys.modules["litellm"] = _ll


def _always_timeout(*a, **k):
    raise APITimeoutError("Connection timed out")


_ll.completion = _always_timeout

import src.ai.llm_client as C  # noqa: E402


def _call_llm_api_like(client):
    """Mirrors spec_extractor.call_llm_api: a broad `except Exception` that
    returns None on failure. A circuit-breaker abort (BaseException) must NOT be
    swallowed here — it has to escape and stop the run."""
    try:
        return client.complete("system", "user")
    except Exception as e:  # noqa: BLE001 (deliberately broad)
        if e.__class__.__name__ == "CircuitBreakerError":
            raise
        return None


def main():
    cfg = str(ROOT / "projects" / "sample_project" / "project_config.json")
    client = C.LLMClient(config_path=cfg).set_timeout_breaker_scope(True)
    for i in range(1, 21):
        print(f"[driver] --- SRS item {i}: calling (every attempt will time out) ---")
        out = _call_llm_api_like(client)
        print(f"[driver] item {i}: call returned {out!r}; run CONTINUES to next item")


if __name__ == "__main__":
    try:
        main()
    except BaseException as _abort:  # mirrors src/cli/main.py __main__
        if _abort.__class__.__name__ == "CircuitBreakerError":
            print(f"\n[driver] EXIT via circuit-breaker: {_abort}")
            sys.exit(2)
        raise
