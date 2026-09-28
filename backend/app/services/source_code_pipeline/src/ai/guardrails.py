"""
Guardrails v4.0 (RIP Edition) - Architectural Integrity & De-duplication Gate.

Key Features:
- Silent Global Integrity: Detects if artifacts are assigned to multiple MFUs.
- Audit Trail Logging: Records duplicates for reference without crashing the pipeline.
- Risk-Driven Escalation: Hard-enforces complexity tiers based on architectural risk flags.
- Vocabulary Sync: Synchronized dynamically with PluginRegistry.
- Zero-Division Protection: Hardened structural math for connectivity.
- Headless Guardrails: Ensures 100% batch coverage and tracks dynamic MFU paradigm distribution.
"""

from ..scanner.plugin_registry import PluginRegistry
from ..utils.vocabulary_sanitizer import SYSTEM_RISK_FLAGS


def apply_guardrails(result: dict, artifacts: dict) -> dict:
    """
    Applies guardrails in-place and returns a structured report.
    """
    report = {
        "summary": {},
        "mfu_findings": {},
        "orphans": [],
        "audit_trail": {"de_duplicated_items": []},
    }

    # TASK 4: Initialize Dynamic Vocabulary
    # SYSTEM_RISK_FLAGS is the single source of truth (vocabulary_sanitizer.py).
    # Importing it here eliminates the previously divergent local definition.
    registry = PluginRegistry()
    plugin_flags = set(registry.get_all_risk_flags() or [])
    allowed_risks = plugin_flags.union(SYSTEM_RISK_FLAGS)

    # 1. Integrity Check: Identify and log Double-Counting
    _resolve_double_counting(result, report)

    # 2. Iterate MFUs for Local Guardrails
    for mfu in result.get("mfus", []):
        mfu_id = mfu.get("id", "UNKNOWN")
        mfu_findings = {"violations": [], "warnings": []}

        _apply_basic_guardrails(mfu, mfu_findings, allowed_risks)
        _enforce_connectivity(mfu, artifacts, mfu_findings)
        _sync_complexity_label(mfu)

        report["mfu_findings"][mfu_id] = mfu_findings

    # 3. Global Orphan Sweeper
    _apply_orphan_sweeper(result, artifacts, report)

    # 4. Compile Summary
    _finalize_summary(result, report)

    return report


# ---------------------------------------------------------
# Local Guardrails
# ---------------------------------------------------------


def _apply_basic_guardrails(mfu: dict, mfu_findings: dict, allowed_risks: set):
    # 1. Cap Confidence
    if mfu.get("ai_confidence", 1.0) > 0.95:
        mfu["ai_confidence"] = 0.95
        mfu_findings["warnings"].append(
            {
                "code": "CONFIDENCE_CAPPED",
                "message": "AI Confidence capped at 0.95 (System Maximum).",
            }
        )

    # 2. Risk Flags validation against Dynamic Registry
    risks = set(mfu.get("risk_flags", []))

    # Bypass validation if registry didn't load properly, else strictly enforce
    if allowed_risks and "none" not in allowed_risks:
        invalid_risks = risks - allowed_risks
        if invalid_risks:
            mfu_findings["violations"].append(
                {
                    "code": "INVALID_RISK_FLAG",
                    "message": f"Unauthorized risk flags detected: {invalid_risks}",
                    "evidence": {"invalid": list(invalid_risks)},
                }
            )
            mfu["risk_flags"] = list(risks.intersection(allowed_risks))


def _sync_complexity_label(mfu: dict):
    """
    Escalates the complexity tier logically based on the presence of
    architectural risk flags and artifact volume.
    """
    risks = set(mfu.get("risk_flags", []))
    current_complexity = mfu.get("complexity", "Simple")

    # Tier 1 Escalation: Critical Risks
    critical_triggers = {
        "external_dependency",
        "integration_risk",
        "concurrency_risk",
        "hardcoded_business_rules",
        "complex_state_management",
        "critical_unmapped_anchor",
    }

    # Tier 2 Escalation: Complex Risks
    complex_triggers = {
        "dynamic_sql",
        "transaction_control",
        "heavy_db_logic",
        "legacy_ui_coupling",
        "data_transformation_heavy",
    }

    if risks.intersection(critical_triggers):
        mfu["complexity"] = "Critical"
    elif risks.intersection(complex_triggers):
        if current_complexity != "Critical":
            mfu["complexity"] = "Complex"
    elif len(mfu.get("artifacts", [])) > 5:
        if current_complexity not in ["Critical", "Complex"]:
            mfu["complexity"] = "Medium"


def _enforce_connectivity(mfu: dict, artifacts: dict, mfu_findings: dict):
    # Map artifact graph data
    artifact_map = {a["id"]: a for a in artifacts.get("artifacts", [])}

    mfu_arts = mfu.get("artifacts", [])
    if len(mfu_arts) <= 1:
        return  # Single files don't need connectivity validation

    # Check for anchor presence (either UI or Batch Anchor)
    # Using strict Enterprise Taxonomy check
    has_anchor = any(
        artifact_map.get(a, {}).get("type") in ["ui_anchor", "batch_anchor"] for a in mfu_arts
    )

    if not has_anchor:
        if "orphan_bucket" not in mfu.setdefault("risk_flags", []):
            mfu["risk_flags"].append("orphan_bucket")
        mfu_findings["violations"].append(
            {
                "code": "NO_ANCHOR",
                "message": "MFU contains multiple artifacts but no identifiable entry point or orchestrator.",
                "evidence": {"artifacts": mfu_arts},
            }
        )
        return

    # Check internal cohesion
    internal_edges = 0
    possible_edges = len(mfu_arts) * (len(mfu_arts) - 1)

    for art_id in mfu_arts:
        data = artifact_map.get(art_id, {})
        calls = set(data.get("graph", {}).get("calls", []))
        called_by = set(data.get("graph", {}).get("called_by", []))

        # How many of this artifact's connections are ALSO inside this MFU?
        internal_edges += len(calls.intersection(set(mfu_arts)))
        internal_edges += len(called_by.intersection(set(mfu_arts)))

    if possible_edges > 0:
        connectivity_ratio = internal_edges / possible_edges
        if connectivity_ratio < 0.15:  # Less than 15% cohesive
            if "weak_connectivity" not in mfu.setdefault("risk_flags", []):
                mfu["risk_flags"].append("weak_connectivity")

            mfu_findings["warnings"].append(
                {
                    "code": "WEAK_CONNECTIVITY",
                    "message": f"Low internal cohesion detected ({round(connectivity_ratio * 100, 1)}%). Grouping may be artificial.",
                    "evidence": {"ratio": connectivity_ratio},
                }
            )

    if len(mfu_arts) > 15:
        if "oversized_mfu" not in mfu.setdefault("risk_flags", []):
            mfu["risk_flags"].append("oversized_mfu")
        mfu_findings["violations"].append(
            {
                "code": "OVERSIZED_MFU",
                "message": f"MFU contains {len(mfu_arts)} artifacts. Risk of monolithic grouping.",
                "evidence": {"count": len(mfu_arts)},
            }
        )


# ---------------------------------------------------------
# Global Integrity (Double-Counting & Orphans)
# ---------------------------------------------------------


def _resolve_double_counting(result: dict, report: dict):
    """
    Identifies artifacts assigned to more than one MFU.
    Silently resolves by keeping the artifact in the MFU with the highest confidence,
    and logs the deduplication in the audit trail.
    """
    artifact_to_mfus = {}
    for mfu in result.get("mfus", []):
        for art in mfu.get("artifacts", []):
            if art not in artifact_to_mfus:
                artifact_to_mfus[art] = []
            artifact_to_mfus[art].append(
                {"mfu_id": mfu.get("id"), "confidence": mfu.get("ai_confidence", 0.0)}
            )

    de_duped_log = []

    for art, assignments in artifact_to_mfus.items():
        if len(assignments) > 1:
            # Sort by highest confidence
            assignments.sort(key=lambda x: x["confidence"], reverse=True)
            winner = assignments[0]["mfu_id"]
            losers = [a["mfu_id"] for a in assignments[1:]]

            de_duped_log.append({"artifact": art, "kept_in": winner, "removed_from": losers})

            # Physically remove from loser MFUs
            for mfu in result.get("mfus", []):
                if mfu.get("id") in losers:
                    if art in mfu.get("artifacts", []):
                        mfu["artifacts"].remove(art)

    report["audit_trail"]["de_duplicated_items"] = de_duped_log


def _apply_orphan_sweeper(result, artifacts, report):
    expected_ids = {a["id"] for a in artifacts.get("artifacts", [])}

    # Separate purely isolated/dead code from calculation
    isolated_ids = {
        a["id"]
        for a in artifacts.get("artifacts", [])
        if a.get("graph", {}).get("role") == "isolated"
    }

    assigned_ids = set()
    for mfu in result.get("mfus", []):
        assigned_ids.update(mfu.get("artifacts", []))

    # General Coverage (Including everything)
    covered_ids = assigned_ids.intersection(expected_ids)
    total = len(expected_ids)
    covered = len(covered_ids)
    pct = (covered / total * 100) if total else 100.0
    pct = min(100.0, pct)

    # Batch/Headless Coverage (Strict Rule)
    batch_anchors = {
        a["id"] for a in artifacts.get("artifacts", []) if a.get("type") == "batch_anchor"
    }
    covered_batch = assigned_ids.intersection(batch_anchors)
    total_batch = len(batch_anchors)
    batch_pct = (len(covered_batch) / total_batch * 100) if total_batch else 100.0

    # Active Orphans (Excluding known isolated/dead code)
    active_orphans = sorted((expected_ids - assigned_ids) - isolated_ids)

    # We do NOT create an automatic Orphan Bucket MFU.
    # We leave them unassigned so the Comparator flags them as true orphans.
    report["orphans"] = active_orphans

    if pct >= 85 and batch_pct == 100.0:
        level = "OK"
    elif pct >= 70 and batch_pct >= 80.0:
        level = "WARN"
    else:
        level = "FAIL"

    report["summary"]["coverage_pct"] = round(pct, 2)
    report["summary"]["batch_coverage_pct"] = round(batch_pct, 2)
    report["summary"]["coverage_level"] = level


# ---------------------------------------------------------
# Final summary & Distribution Tracking
# ---------------------------------------------------------


def _finalize_summary(result, report):
    violations = sum(len(v["violations"]) for v in report["mfu_findings"].values())
    warnings = sum(len(v["warnings"]) for v in report["mfu_findings"].values())

    # TASK 4: Dynamic MFU Paradigm Distribution Tracker
    track_distribution = {}
    for mfu in result.get("mfus", []):
        track = mfu.get("execution_track", "Unknown-Track")
        if track not in track_distribution:
            track_distribution[track] = 0
        track_distribution[track] += 1

    # Status is OK_DEDUPLICATED if collisions existed but were handled quietly
    has_dupes = len(report["audit_trail"]["de_duplicated_items"]) > 0

    report["summary"].update(
        {
            "mfu_count": len(result.get("mfus", [])),
            "track_distribution": track_distribution,
            "violations": violations,
            "warnings": warnings,
            "source_paradigm": "mixed_polyglot",
        }
    )
