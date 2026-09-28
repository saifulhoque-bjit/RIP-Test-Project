from pathlib import Path

import pandas as pd


def export_comparison(report: dict, base_path: Path):
    """
    Exports comparison report to CSV and Excel.
    Revised: Removed MEU columns and synced with new Comparator keys.
    base_path = projects/.../mfu_comparison_report (without extension)
    """

    rows = []

    for d in report.get("differences", []):
        # We extract the newly tracked risk fields to expose technical debt
        rows.append(
            {
                "MFU Name": d.get("mfu_name", d.get("mfu_id", "Unknown")),
                "Risks Added": ", ".join(d.get("risks_added", [])),
                "Risks Removed": ", ".join(d.get("risks_removed", [])),
                "Artifacts Added": ", ".join(d.get("artifacts_added", [])),
                "Artifacts Removed": ", ".join(d.get("artifacts_removed", [])),
                "Surgical Alert": ", ".join(d.get("special_notes", [])),
            }
        )

    if not rows:
        print("[EXPORT] No differences to export.")
        return

    df = pd.DataFrame(rows)

    csv_path = base_path.with_suffix(".csv")
    xlsx_path = base_path.with_suffix(".xlsx")

    # 1. Export CSV (Base Pandas - Always Works)
    try:
        # Use utf-8-sig to ensure Excel handles special characters correctly
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        print(f"[EXPORT] CSV written to   {csv_path}")
    except Exception as e:
        print(f"[ERROR] CSV export failed: {e}")

    # 2. Export Excel (Requires openpyxl - Handled Gracefully)
    try:
        df.to_excel(xlsx_path, index=False)
        print(f"[EXPORT] Excel written to {xlsx_path}")
    except ImportError:
        print(
            "[WARN] 'openpyxl' library missing. Skipping Excel generation. (Run 'pip install openpyxl' to fix)"
        )
    except Exception as e:
        print(f"[ERROR] Excel export failed (is the file open?): {e}")
