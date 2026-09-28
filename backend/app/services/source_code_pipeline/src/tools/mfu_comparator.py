import json
from pathlib import Path


def load_mfus(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("mfus", [])


def summarize(mfus):
    """
    Summarizes MFU data for comparison.
    Tracks Day 4 fields (execution_track, traceability_chain)
    and uses 'id' as the primary key. MEU logic has been strictly removed.
    """
    summary = {}
    for m in mfus:
        summary[m["id"]] = {
            "id": m["id"],
            "name": m["name"],
            "complexity": m.get("complexity", "Unknown"),
            "execution_track": m.get("execution_track", "Unknown-Track"),  # Polyglot default
            "traceability_chain": m.get("traceability_chain", []),
            "risk_flags": set(m.get("risk_flags", [])),  # Set for diffing
            "artifacts": set(m.get("artifacts", [])),
        }
    return summary


def compare(proposed_mfus_path: Path, final_mfus_path: Path, out_path: Path):
    """
    Performs a deep comparison between AI-Proposed MFUs and Review-Agent Audited MFUs.
    Provides a deterministic audit trail of AI-driven architectural corrections.
    """
    proposed = summarize(load_mfus(proposed_mfus_path))
    final = summarize(load_mfus(final_mfus_path))

    # Prepare report structure with serializable containers
    report = {"mfus_proposed_snapshot": [], "mfus_final_snapshot": [], "differences": []}

    # Populate serializable copies for the report
    for m_id, m in proposed.items():
        item = m.copy()
        item["artifacts"] = sorted(list(m["artifacts"]))
        item["risk_flags"] = sorted(list(m["risk_flags"]))
        report["mfus_proposed_snapshot"].append(item)

    for m_id, m in final.items():
        item = m.copy()
        item["artifacts"] = sorted(list(m["artifacts"]))
        item["risk_flags"] = sorted(list(m["risk_flags"]))
        report["mfus_final_snapshot"].append(item)

    # Diff by MFU ID
    all_ids = set(proposed.keys()) | set(final.keys())

    for m_id in sorted(all_ids):
        p = proposed.get(m_id)
        f = final.get(m_id)

        if not p:
            f_serial = f.copy()
            f_serial["artifacts"] = sorted(list(f["artifacts"]))
            f_serial["risk_flags"] = sorted(list(f["risk_flags"]))
            report["differences"].append(
                {"mfu_id": m_id, "change_type": "ADDED_BY_REVIEWER", "final_state": f_serial}
            )
        elif not f:
            p_serial = p.copy()
            p_serial["artifacts"] = sorted(list(p["artifacts"]))
            p_serial["risk_flags"] = sorted(list(p["risk_flags"]))
            report["differences"].append(
                {"mfu_id": m_id, "change_type": "REMOVED_BY_REVIEWER", "proposed_state": p_serial}
            )
        else:
            # Artifact Diff
            added_arts = f["artifacts"] - p["artifacts"]
            removed_arts = p["artifacts"] - f["artifacts"]

            # Risk Flag Diff (The "Surgical" Fix)
            added_risks = f["risk_flags"] - p["risk_flags"]
            removed_risks = p["risk_flags"] - f["risk_flags"]

            # Traceability Chain Diff
            p_trace = set(p["traceability_chain"])
            f_trace = set(f["traceability_chain"])
            traceability_added = f_trace - p_trace
            traceability_removed = p_trace - f_trace
            traceability_changed = bool(
                traceability_added
                or traceability_removed
                or p["traceability_chain"] != f["traceability_chain"]
            )

            # Detect any meaningful change in scope, track, or risk
            if (
                added_arts
                or removed_arts
                or p["complexity"] != f["complexity"]
                or p["execution_track"] != f["execution_track"]
                or added_risks
                or removed_risks
                or traceability_changed
            ):
                diff_entry = {
                    "mfu_id": m_id,
                    "mfu_name": f["name"],
                    "change_type": "MODIFIED",
                    "complexity_proposed": p["complexity"],
                    "complexity_final": f["complexity"],
                    "track_proposed": p["execution_track"],
                    "track_final": f["execution_track"],
                    "traceability_changed": traceability_changed,
                    "traceability_added": sorted(list(traceability_added)),
                    "traceability_removed": sorted(list(traceability_removed)),
                    "artifacts_added": sorted(list(added_arts)),
                    "artifacts_removed": sorted(list(removed_arts)),
                    "risks_added": sorted(list(added_risks)),
                    "risks_removed": sorted(list(removed_risks)),
                }

                # Architectural Alerts: Highlight critical reviewer actions synced with System Flags
                notes = []
                if "external_dependency" in added_risks or "integration_risk" in added_risks:
                    notes.append(
                        "SURGICAL ESCALATION: Reviewer enforced integration/external risk floor missed by proposer."
                    )
                if p["execution_track"] != f["execution_track"]:
                    notes.append(
                        "TRACK CORRECTION: Reviewer corrected the execution paradigm track."
                    )
                if "orphan_bucket" in added_risks or "critical_unmapped_anchor" in added_risks:
                    notes.append(
                        "ORPHAN ALERT: Reviewer or Guardrail flagged unmapped anchors or isolated buckets."
                    )

                if notes:
                    diff_entry["special_notes"] = notes

                report["differences"].append(diff_entry)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[COMPARE] Audit Report written to {out_path}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="MFU Audit Comparator")
    parser.add_argument("--proposed", required=True, help="Path to mfus_proposed.json")
    parser.add_argument("--final", required=True, help="Path to mfus_final.json")
    parser.add_argument("--out", required=True, help="Output path for audit report JSON")
    args = parser.parse_args()

    compare(Path(args.proposed), Path(args.final), Path(args.out))
