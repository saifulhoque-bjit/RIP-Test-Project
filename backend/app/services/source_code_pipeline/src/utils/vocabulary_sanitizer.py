"""
Centralized vocabulary for MFU risk flags.
Ensures consistency between AI output and the dynamic JSON Schema.

Single source of truth
──────────────────────
SYSTEM_RISK_FLAGS is the authoritative set of pipeline-internal risk flags.
These flags are emitted by mechanical pipeline components (orphan_sweeper,
review_agent) rather than by the LLM, and therefore cannot be registered
through the PluginRegistry (which only manages technology-plugin vocabulary).

Any component that needs the COMPLETE allowed risk-flag vocabulary must use:

    plugin_flags ∪ SYSTEM_RISK_FLAGS

Currently that includes:
  • vocabulary_sanitizer.sanitize_mfu_vocabulary()  — filters LLM output
  • json_schema_validator.JSONSchemaValidator        — hydrates the $DYNAMIC_ENUM

Adding a new system flag: define it here ONLY. All validators and sanitizers
pick it up automatically at their next instantiation.
"""

from typing import Any

from ..scanner.plugin_registry import PluginRegistry

# ── System-level risk flags ───────────────────────────────────────────────────
# Emitted by pipeline machinery (orphan_sweeper, review_agent), NOT by the LLM.
# Must be whitelisted in every schema validator that checks risk_flags.
SYSTEM_RISK_FLAGS: frozenset = frozenset(
    {
        # ── orphan_sweeper.py ──────────────────────────────────────────────────
        "orphan_bucket",  # artifact not claimed by any AI-generated MFU
        "critical_unmapped_anchor",  # entry-point the AI failed to group
        "ai_classification_failure",  # classification could not be determined
        "integration_risk",  # external-dep bucket (emitted unconditionally
        # by _create_bucket_mfu when has_ext_dep=True)
        # ── guardrails.py (_enforce_connectivity) ─────────────────────────────
        "weak_connectivity",  # MFU has < 15% internal cohesion
        "oversized_mfu",  # MFU contains > 15 artifacts
    }
)


def sanitize_mfu_vocabulary(result: dict[str, Any]) -> dict[str, Any]:
    """
    Scans the MFU list and removes any risk flags that are not actively
    registered in the PluginRegistry or explicitly allowed as system flags.
    """
    if "mfus" not in result or not isinstance(result["mfus"], list):
        return result

    # Dynamically fetch allowed vocabulary
    registry = PluginRegistry()
    plugin_flags = set(registry.get_all_risk_flags() or [])
    allowed_risk_flags = plugin_flags.union(SYSTEM_RISK_FLAGS)

    # Safety bypass: If registry failed to load, don't wipe everything
    if not plugin_flags:
        print("[SANITIZER] Warning: Plugin registry empty. Skipping strict sanitization.")
        return result

    for mfu in result["mfus"]:
        if "risk_flags" in mfu and isinstance(mfu["risk_flags"], list):
            original_flags = mfu["risk_flags"]

            # Filter only for dynamically allowed enums
            sanitized_flags = [flag for flag in original_flags if flag in allowed_risk_flags]

            # Calculate what was stripped for internal logging
            hallucinated = set(original_flags) - set(sanitized_flags)
            if hallucinated:
                print(f"[SANITIZER] Removed unregistered/hallucinated risk flags: {hallucinated}")

            mfu["risk_flags"] = sanitized_flags

    return result
