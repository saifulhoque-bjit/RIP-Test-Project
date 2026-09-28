"""End-to-end simulation of EVERY LLM timeout/fallback path (offline, no network).

PART 1 — runs the deterministic path assertions (timeout cap, generic-transient
         budget, circuit-breaker trip, scope gating, adaptive ladder escalation,
         provider-aware reasoning downgrade + tagging, deep-merge, GPT-5).
PART 2 — spawns a subprocess that loads the REAL project config, forces every
         call to time out, and proves the process EXITS with code 2 once a scoped
         item exhausts its timeout attempts (initial + N retries) — cleanly,
         escaping the broad except-Exception swallows, no traceback.

Run: python tests/simulate_fallback.py
"""

import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def part1_paths():
    print("=" * 74)
    print("PART 1 — every fallback path (deterministic offline assertions)")
    print("=" * 74)
    sys.path.insert(0, str(ROOT / "tests"))
    import test_llm_retry_breaker as suite

    suite.run()


def part2_exit():
    print("\n" + "=" * 74)
    print("PART 2 — process EXIT after a scoped item's timeouts are exhausted")
    print("=" * 74)
    driver = ROOT / "tests" / "_fallback_exit_driver.py"
    proc = subprocess.run([sys.executable, str(driver)], capture_output=True, text=True)
    # Show the driver + LLMClient logs so the escalation and exit are visible.
    for line in proc.stdout.splitlines():
        print("   " + line)
    if proc.stderr.strip():
        print("   [stderr tail]", proc.stderr.strip()[-400:])
    assert proc.returncode == 2, f"expected clean exit code 2, got {proc.returncode}"
    assert "EXIT via circuit-breaker" in proc.stdout, "breaker exit message missing"
    assert "Traceback" not in proc.stderr, "exit should be clean (no traceback)"
    print(
        f"\n  ok: process exited with code {proc.returncode} via circuit-breaker (clean, no traceback)"
    )


def show_config():
    cfg = json.loads(
        (ROOT / "projects/sample_project/project_config.json").read_text(encoding="utf-8")
    )["llm"]
    cb = cfg["circuit_breaker"]
    adds = [r.get("timeout_add_seconds", 0) for r in cfg["timeout_fallback"]["ladder"]]
    print("\n" + "=" * 74)
    print("Config the simulation used (projects/sample_project/project_config.json):")
    print("=" * 74)
    print(f"   active_provider      : {cfg['active_provider']}")
    print(f"   max_retries          : {cfg['max_retries']}   (non-timeout transients)")
    print(
        f"   timeout_max_retries  : {cfg['timeout_max_retries']}   (=> {cfg['timeout_max_retries'] + 1} attempts per item)"
    )
    print(f"   request_timeout_secs : {cfg['request_timeout_seconds']}")
    print(
        f"   breaker              : consecutive_timeouts>={cb['max_consecutive_failures']}, "
        f"cost={cb['max_est_cost_usd']}(off), wall={cb['max_run_seconds']}s"
    )
    print(f"   ladder timeouts/attempt: {[cfg['request_timeout_seconds'] + a for a in adds]}")


if __name__ == "__main__":
    part1_paths()
    part2_exit()
    show_config()
    print(
        "\nSIMULATION COMPLETE — every fallback path verified; the process exits "
        "cleanly (code 2) once a scoped item's timeouts are exhausted."
    )
