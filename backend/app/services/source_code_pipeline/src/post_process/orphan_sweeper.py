"""
Orphan Sweeper - The "Safety Net" for Coverage.
Ensures 100% artifact coverage by mechanically bucketing unassigned artifacts.

Revised (Polyglot v4.2):
- Polyglot & Topology Awareness: Uses Day 3 mathematical graph_metrics for Dead Code isolation.
- Pure Enterprise Taxonomy: Removed legacy 'pb_' hardcodes. Uses semantic substring matching for graceful fallbacks.
- Unmapped Anchor Alert: Creates a critical bucket if Phase 2 fails to map an Entry Point.
- External Dependency Awareness: Enforces complexity escalation for system-level bridges.
- Dead Code Protection: Artifacts with system dependencies are shielded from "Dead" classification.
- Enhanced Risk Flags: Propagates integration_risk to the mechanical MFUs.
- Strict Schema Compliance: Injects mandatory 'execution_track' and 'traceability_chain'.
"""

from typing import Any


def sweep_orphans(mfus_proposed: dict, artifacts_enriched: dict) -> dict:
    """
    Identifies artifacts not present in any MFU and assigns them to mechanical buckets.
    Returns the updated mfus_proposed dictionary.
    """

    # 1. Identify Assigned Artifacts
    assigned_ids: set[str] = set()
    for mfu in mfus_proposed.get("mfus", []):
        assigned_ids.update(mfu.get("artifacts", []))

    # 2. Identify Orphans
    all_artifacts_map = {a["id"]: a for a in artifacts_enriched.get("artifacts", [])}
    all_ids = set(all_artifacts_map.keys())
    orphan_ids = sorted(list(all_ids - assigned_ids))

    if not orphan_ids:
        print("[SWEEPER] No orphans found. 100% coverage.")
        return mfus_proposed

    print(f"[SWEEPER] Found {len(orphan_ids)} orphans. Categorizing...")

    # 3. Create Buckets with Dependency Tracking
    buckets = {
        "unmapped_anchor": {"ids": [], "loc": 0, "has_ext_dep": False},  # Critical Failure Bucket
        "utility": {"ids": [], "loc": 0, "has_ext_dep": False},
        "ui": {"ids": [], "loc": 0, "has_ext_dep": False},
        "batch": {"ids": [], "loc": 0, "has_ext_dep": False},
        "data": {"ids": [], "loc": 0, "has_ext_dep": False},
        "dead": {"ids": [], "loc": 0, "has_ext_dep": False},
    }

    for oid in orphan_ids:
        art = all_artifacts_map.get(oid)
        if not art:
            continue

        # Get Metrics and Signals
        metrics = art.setdefault("metrics", {})
        signals = art.get("signals", {})

        # Polyglot LOC fallback & Normalization
        loc = art.get("loc", metrics.get("script_lines", 0) + metrics.get("lines_of_code", 0))
        if "script_lines" not in metrics:
            metrics["script_lines"] = loc
        if "lines_of_code" not in metrics:
            metrics["lines_of_code"] = loc

        is_ext_dep = signals.get("external_dependency", False)

        # Classification Logic
        bucket_key = _classify_orphan(art, is_ext_dep)

        buckets[bucket_key]["ids"].append(oid)
        buckets[bucket_key]["loc"] += loc
        if is_ext_dep:
            buckets[bucket_key]["has_ext_dep"] = True

    # 4. Generate Mechanical MFUs with Explicit Tracks
    new_mfus = []

    # Bucket 0: Critical Failure (Unmapped Entry Points)
    if buckets["unmapped_anchor"]["ids"]:
        new_mfus.append(
            _create_bucket_mfu(
                "SYS-ALERT-01",
                "CRITICAL: Unmapped Feature Anchors",
                buckets["unmapped_anchor"],
                "These are verified entry points that the AI failed to map to an MFU. IMMEDIATE REVIEW REQUIRED.",
                "Mixed-Track",
            )
        )

    # Bucket A: Shared Utilities (NVOs/Functions/shared_logic)
    if buckets["utility"]["ids"]:
        new_mfus.append(
            _create_bucket_mfu(
                "SYS-UTIL-01",
                "Shared Utilities & Libraries (Orphans)",
                buckets["utility"],
                "Likely shared logic or non-visual objects missed by feature grouping.",
                "Mixed-Track",
            )
        )

    # Bucket B: UI (Windows/Menus/ui_anchor) - High Risk if missed
    if buckets["ui"]["ids"]:
        new_mfus.append(
            _create_bucket_mfu(
                "SYS-UI-01",
                "Ungrouped Screens & Menus (Orphans)",
                buckets["ui"],
                "Loose UI elements not linked to specific flows. Requires manual review.",
                "UI-Track",
            )
        )

    # Bucket B2: Batch (JCL/COBOL/batch_anchor) - High Risk if missed
    if buckets["batch"]["ids"]:
        new_mfus.append(
            _create_bucket_mfu(
                "SYS-BATCH-01",
                "Ungrouped Batch & Headless Logic (Orphans)",
                buckets["batch"],
                "Batch logic not linked to specific modules.",
                "Batch-Track",
            )
        )

    # Bucket C: Data (DWs/Structures/data_provider)
    if buckets["data"]["ids"]:
        new_mfus.append(
            _create_bucket_mfu(
                "SYS-DATA-01",
                "Ungrouped Data Objects (Orphans)",
                buckets["data"],
                "Data providers or structures without clear feature ownership.",
                "Mixed-Track",
            )
        )

    # Bucket D: No detected references (NOT confirmed dead — call graph may under-link some reference types)
    if buckets["dead"]["ids"]:
        new_mfus.append(
            _create_bucket_mfu(
                "SYS-DEAD-01",
                "Unreferenced in Call Graph — Verify (Orphans)",
                buckets["dead"],
                "No static references detected by the CURRENT call graph. This is NOT confirmed dead code: some "
                "reference types are not yet fully edge-traced (e.g. PowerBuilder global-function f_* calls and "
                "structure references), so widely-used shared utilities can appear here FALSELY. Do NOT drop these — "
                "verify, and migrate genuine utilities as shared services.",
                "Mixed-Track",
            )
        )

    # 5. Merge and Return
    mfus_proposed["mfus"].extend(new_mfus)
    return mfus_proposed


def _classify_orphan(art: dict[str, Any], is_ext_dep: bool) -> str:
    """
    Decides which bucket an artifact belongs to.
    Uses Day 3 graph_metrics for mathematical dead-code isolation.
    Fully refactored to rely on Enterprise Archetypes and generic semantic fallbacks.
    """
    atype = art.get("type", "unknown").lower()
    graph = art.get("graph", {})
    graph_metrics = art.get("graph_metrics", {})

    # Mathematical topology metrics (with fallbacks for older runs)
    incoming_edges = graph_metrics.get("incoming_edges", len(graph.get("called_by", [])))
    is_entry_point = graph_metrics.get("is_entry_point", graph.get("role") == "entrypoint")

    # 0. Critical Alert: Unmapped Anchors
    if is_entry_point:
        return "unmapped_anchor"

    # 1. Dead Code Detection (Mathematical)
    # Protection: Never label as Dead if it's an Entry Point or a System/External Bridge.
    if incoming_edges == 0 and not is_entry_point and not is_ext_dep:
        return "dead"

    # 2. Type-based Classification (Strict Enterprise Taxonomy + Semantic Fallback)
    # LLMEnricher normalizes these, but we keep generic substring checks just in case
    # the pipeline ran in graph-only mode (bypassing LLMEnricher) and raw plugin types leaked.
    if (
        atype == "ui_anchor"
        or "window" in atype
        or "form" in atype
        or "screen" in atype
        or "menu" in atype
    ):
        return "ui"
    elif atype == "batch_anchor" or "batch" in atype or "jcl" in atype or "job" in atype:
        return "batch"
    elif atype == "data_provider" or "datawindow" in atype or "table" in atype or "sql" in atype:
        return "data"
    else:
        # Matches: shared_logic, interface, unresolved, nvo, proxy, class
        return "utility"


def _create_bucket_mfu(mid: str, name: str, bucket_data: dict, reason: str, track: str) -> dict:
    """
    Creates a valid MFU object for the bucket.
    Enforces a "Complexity Floor" for external dependencies and strict Phase 4 schemas.
    """
    count = len(bucket_data["ids"])
    total_loc = bucket_data["loc"]
    has_ext_dep = bucket_data.get("has_ext_dep", False)

    # Determine Complexity based on Volume (LOC)
    if total_loc > 10000:
        complexity = "Critical"
    elif total_loc > 3000:
        complexity = "Complex"
    elif total_loc > 1000:
        complexity = "Medium"
    else:
        complexity = "Simple"

    # ENFORCEMENT: Complexity Floor for External Dependencies
    # If the bucket contains a Win32 API/DLL wrapper, it cannot be Simple or Medium.
    if has_ext_dep and complexity in ["Simple", "Medium"]:
        complexity = "Complex"

    risk_flags = ["orphan_bucket"]
    if has_ext_dep:
        risk_flags.extend(["external_dependency", "integration_risk"])

    # Critical Alert Flag
    if mid == "SYS-ALERT-01":
        risk_flags.extend(["critical_unmapped_anchor", "ai_classification_failure"])

    return {
        "id": mid,
        "name": f"{name} ({count} items)",
        "description": f"Mechanical bucket containing {count} artifacts with approx {total_loc} LOC. {reason}",
        "execution_track": track,
        "traceability_chain": bucket_data["ids"][:1] if bucket_data["ids"] else [],
        "artifacts": bucket_data["ids"],
        "complexity": complexity,
        "risk_flags": risk_flags,
        "ai_confidence": 1.0,
        "rationale": (
            f"Mechanically grouped. Total LOC: {total_loc}. "
            f"Complexity set to {complexity} due to {'external dependencies detected' if has_ext_dep else 'volume'}."
        ),
    }
