"""
Compare two module-derivation engines on the SAME codebase — turns "map-reduce is better" into a measured
result. Reads two module→artifact assignments (e.g. hybrid Stage 2.5 `module_manifest.json` vs source
map-reduce `source_modules.json`) and reports:

  * module counts per engine + shared artifact coverage
  * Rand Index and Adjusted Rand Index (chance-corrected agreement of the two groupings)
  * a cross-tab: how each engine-A module's artifacts distribute across engine-B modules (and the reverse),
    so splits/merges are visible
  * whether the groupings are identical

Purely deterministic (no LLM). The comparison is engine-agnostic — it only needs artifact→module maps.
"""

from __future__ import annotations

from collections import defaultdict
import json
from math import comb
from pathlib import Path


def _assignment_from_manifest(path: Path) -> dict[str, str]:
    """Hybrid module_manifest.json → {artifact_id: module_name}, via each module's artifacts[]."""
    d = json.loads(path.read_text(encoding="utf-8"))
    mods = d.get("modules", d if isinstance(d, list) else [])
    out = {}
    for m in mods.values() if isinstance(mods, dict) else mods:
        if not isinstance(m, dict):
            continue
        label = m.get("module_name") or m.get("module_id") or "?"
        for a in m.get("artifacts") or m.get("file_ids") or []:
            out[a] = label
    return out


def _assignment_from_source(path: Path) -> dict[str, str]:
    """source_modules.json → {artifact_id: module_name}. Prefers the explicit assignment map."""
    d = json.loads(path.read_text(encoding="utf-8"))
    if d.get("_placeholder"):
        return {}
    if isinstance(d.get("assignment"), dict) and d["assignment"]:
        return dict(d["assignment"])
    out = {}
    for m in d.get("modules", []):
        for f in m.get("file_ids") or [m2["id"] for m2 in m.get("files", [])] or []:
            out[f] = m.get("module_name", "?")
    return out


def load_assignment(path: str) -> dict[str, str]:
    p = Path(path)
    d = json.loads(p.read_text(encoding="utf-8"))
    # source_modules has 'mode'/'data_model'; manifest has 'modules' w/ module_id
    if isinstance(d, dict) and (d.get("mode") == "source-mapreduce" or "data_model" in d):
        return _assignment_from_source(p)
    return _assignment_from_manifest(p)


def _rand_indices(a: dict[str, str], b: dict[str, str]) -> tuple[float, float, int]:
    """Rand Index + Adjusted Rand Index over the artifacts present in BOTH assignments (contingency-table
    formula, O(n) in cells)."""
    common = sorted(set(a) & set(b))
    n = len(common)
    if n < 2:
        return (1.0, 1.0, n)
    cross: dict[tuple[str, str], int] = defaultdict(int)
    rows: dict[str, int] = defaultdict(int)
    cols: dict[str, int] = defaultdict(int)
    for x in common:
        cross[(a[x], b[x])] += 1
        rows[a[x]] += 1
        cols[b[x]] += 1
    sum_c = sum(comb(v, 2) for v in cross.values())
    sum_a = sum(comb(v, 2) for v in rows.values())
    sum_b = sum(comb(v, 2) for v in cols.values())
    total = comb(n, 2)
    ri = (total - sum_a - sum_b + 2 * sum_c) / total
    exp = (sum_a * sum_b) / total if total else 0
    denom = 0.5 * (sum_a + sum_b) - exp
    ari = (sum_c - exp) / denom if denom else 1.0
    return (round(ri, 4), round(ari, 4), n)


def _crosstab(a: dict[str, str], b: dict[str, str]):
    common = set(a) & set(b)
    tab: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for x in common:
        tab[a[x]][b[x]] += 1
    rows = []
    for am, dist in sorted(tab.items(), key=lambda kv: -sum(kv[1].values())):
        rows.append(
            {
                "module_A": am,
                "n": sum(dist.values()),
                "maps_to_B": dict(sorted(dist.items(), key=lambda kv: -kv[1])),
            }
        )
    return rows


def compare(path_a: str, path_b: str, label_a="A", label_b="B") -> dict:
    a, b = load_assignment(path_a), load_assignment(path_b)
    ri, ari, n_common = _rand_indices(a, b)
    return {
        "engine_A": label_a,
        "engine_B": label_b,
        "modules_A": len(set(a.values())),
        "modules_B": len(set(b.values())),
        "artifacts_A": len(a),
        "artifacts_B": len(b),
        "artifacts_common": n_common,
        "only_in_A": sorted(set(a) - set(b))[:50],
        "only_in_B": sorted(set(b) - set(a))[:50],
        "rand_index": ri,
        "adjusted_rand_index": ari,
        "identical_grouping": ari == 1.0,
        "A_to_B_crosstab": _crosstab(a, b),
        "B_to_A_crosstab": _crosstab(b, a),
    }


def write_report(project_root, path_a, path_b, label_a="hybrid", label_b="source") -> dict | None:
    rep = compare(path_a, path_b, label_a, label_b)
    out = Path(project_root) / "_global" / "module_derivation_compare.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
    rep["report_path"] = str(out)
    return rep
