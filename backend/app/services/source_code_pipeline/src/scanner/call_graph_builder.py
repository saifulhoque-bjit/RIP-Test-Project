"""
Call-graph enrichments (Refactored for global graph + module slicing + Polyglot)

Primary responsibilities:
- enrich_with_call_graph: take a scanner output (artifacts_detected.json) and
  compute graph fields (calls, called_by, depth, role, reuse, graph_metrics).
  Resolves cross-paradigm polyglot dependencies while respecting strict AST boundaries.
  This is deterministic and idempotent.

- slice_enriched_for_module: helper to extract a module-specific enriched artifacts
  file from a global enriched artifact set while preserving graph fields and
  cross-module reference visibility.
"""

from collections import defaultdict, deque
import json
import os
from pathlib import Path
from typing import Any


def _get_project_root() -> Path:
    """Dynamically resolve the root of the project to safely load config files."""
    cwd = Path(os.getcwd())
    if (cwd / "config").exists():
        return cwd
    script_dir = Path(__file__).resolve().parent
    for parent in [script_dir] + list(script_dir.parents):
        if (parent / "config").exists():
            return parent
    return cwd


def _initialize_legacy_defaults(artifact: dict):
    """
    Ensures polyglot artifacts have the base legacy structure to pass schema validation.
    Standardizes on the new 'edges' schema while explicitly preserving AST line numbers.
    """
    if "metrics" not in artifact:
        artifact["metrics"] = {
            "lines_of_code": artifact.get("loc", 0),
            "script_lines": artifact.get("loc", 0),
        }
    if "structure" not in artifact:
        artifact["structure"] = {"function_count": 0, "event_count": 0}

    if "signals" not in artifact:
        flags = artifact.get("landmine_details", {}).get("flags", [])

        ext_dep_flags = {
            "external_dll_call",
            "ole_automation",
            "external_api_call",
            "registry_access",
            "ibm_db2_dependency",
        }
        has_ext_dep = any(f in flags for f in ext_dep_flags)

        artifact["signals"] = {
            "embedded_sql": "embedded_sql_found" in flags or "embedded_sql" in flags,
            "dynamic_sql": "dynamic_sql_found" in flags or "dynamic_sql_detected" in flags,
            "transaction_control": "transaction_control_detected" in flags
            or "mainframe_cics_coupling" in flags,
            "external_dependency": has_ext_dep,
        }

    if "edges" not in artifact:
        artifact["edges"] = []

    # Migrate legacy array formats safely without deduplicating different lines
    deps = artifact.pop("dependencies", [])
    for d in deps:
        if not any(
            e.get("target") == d.get("target")
            and e.get("line_start") == d.get("line_start", 0)
            and e.get("line") == d.get("line", 0)
            for e in artifact["edges"]
        ):
            artifact["edges"].append(
                {
                    "target": d.get("target", ""),
                    "type": d.get("type", "legacy_dep"),
                    "line": d.get("line", 0),
                    "line_start": d.get("line_start", 0),
                    "line_end": d.get("line_end", 0),
                    "column": d.get("column", 0),
                }
            )

    refs = artifact.pop("references", [])
    for r in refs:
        if not any(e.get("target") == r.get("id") for e in artifact["edges"]):
            artifact["edges"].append(
                {
                    "target": r.get("id", ""),
                    "type": f"legacy_{r.get('strength', 'strong')}",
                    "line": 0,
                    "line_start": 0,
                    "line_end": 0,
                    "column": 0,
                }
            )


def _stitch_global_edges(artifacts: list):
    """
    Translates plugin-provided 'edges' into concrete links by matching
    against the global identity universe (IDs and file names).
    Handles cross-paradigm hops while protecting strict AST boundaries.
    """
    strict_map = {}
    fuzzy_map = {}

    # 1. Build lookup dictionaries
    for a in artifacts:
        aid = a["id"]
        strict_map[aid.upper()] = aid

        fname = a.get("file_name", "")
        if fname:
            fuzzy_map[fname.upper()] = aid
            parts = fname.rsplit(".", 1)
            if parts:
                fuzzy_map[parts[0].upper()] = aid

    # 2. Stitch dependencies with Spatial/AST awareness
    for a in artifacts:
        for edge in a.get("edges", []):
            target = edge.get("target", "")
            if not target:
                edge["resolved"] = False
                continue

            is_ast = "ast" in edge.get("type", "").lower()
            target_upper = target.upper()

            # Strict lookup (Always preferred)
            resolved_id = strict_map.get(target_upper)

            # Fuzzy fallback (ONLY if it's a regex edge. AST logic is precise and shouldn't be guessed)
            if not resolved_id and not is_ast:
                resolved_id = fuzzy_map.get(target_upper)

            if resolved_id:
                edge["target"] = resolved_id
                edge["resolved"] = True
            else:
                edge["resolved"] = False


def _propagate_landmine_signals(by_id: dict):
    """
    Bubbles up ALL boolean landmine signals from callees to callers dynamically.
    """
    changed = True
    while changed:
        changed = False
        for aid, art in by_id.items():
            parent_sigs = art.setdefault("signals", {})

            for edge in art.get("edges", []):
                # Only propagate through safely resolved internal dependencies
                if not edge.get("resolved", False):
                    continue

                callee_id = edge.get("target")
                callee = by_id.get(callee_id)

                if not callee:
                    continue

                child_sigs = callee.get("signals", {})

                for sig_key, sig_val in child_sigs.items():
                    if isinstance(sig_val, bool) and sig_val is True:
                        if not parent_sigs.get(sig_key):
                            parent_sigs[sig_key] = True
                            changed = True


def enrich_with_call_graph(input_path: Path, output_path: Path) -> None:
    """
    Read input artifacts (scanner output) and write enriched artifacts with graph and reuse.
    """
    data = json.loads(Path(input_path).read_text(encoding="utf-8"))
    artifacts = data.get("artifacts", [])

    # ------------------------------------------------------------------
    # Pre-processing: Polyglot Normalization & Edge Resolution
    # ------------------------------------------------------------------
    for a in artifacts:
        _initialize_legacy_defaults(a)

    _stitch_global_edges(artifacts)

    by_id = {a["id"]: a for a in artifacts}
    valid_ids = set(by_id.keys())

    # ------------------------------------------------------------------
    # Propagate Landmine Signals BEFORE building the final graph
    # ------------------------------------------------------------------
    _propagate_landmine_signals(by_id)

    forward = defaultdict(set)
    backward = defaultdict(set)
    forward_soft = defaultdict(set)
    backward_soft = defaultdict(set)
    forward_external = defaultdict(set)
    backward_external = defaultdict(set)
    unresolved_refs = set()

    # ------------------------------------------------------------------
    # Build strong + soft graphs safely using resolved Edges schema
    # ------------------------------------------------------------------
    for a in artifacts:
        src = a["id"]
        for edge in a.get("edges", []):
            tgt = edge.get("target")
            resolved = edge.get("resolved", False)

            if not tgt or tgt == src:
                continue

            edge_type = edge.get("type", "").lower()
            strength = "weak" if "weak" in edge_type else "strong"

            if resolved and tgt in valid_ids:
                # Known target: add to the mathematical graph
                if strength == "strong":
                    forward[src].add(tgt)
                    backward[tgt].add(src)
                    forward_soft[src].add(tgt)
                    backward_soft[tgt].add(src)
                elif strength == "weak":
                    forward_soft[src].add(tgt)
                    backward_soft[tgt].add(src)
            else:
                unresolved_refs.add(tgt)
                forward_external[src].add(tgt)
                backward_external[tgt].add(src)

    # ------------------------------------------------------------------
    # Determine entrypoints (Phase 4 Polyglot Fix - Data-Driven Ontology)
    # ------------------------------------------------------------------
    mapping_path = _get_project_root() / "config" / "archetype_mapping.json"
    type_mapping = {}
    if mapping_path.exists():
        try:
            type_mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    core_anchors = {"ui_anchor", "batch_anchor", "interface"}
    entrypoints = []

    for a in artifacts:
        aid = a["id"]
        raw_type = str(a.get("type", "unknown")).lower().strip()
        strict_archetype = type_mapping.get(raw_type, raw_type)

        a["type"] = strict_archetype

        if strict_archetype in core_anchors or not backward.get(aid) and forward.get(aid):
            entrypoints.append(aid)

    if not entrypoints:
        entrypoints = [aid for aid in valid_ids if aid not in backward or not backward[aid]]

    # ------------------------------------------------------------------
    # Depth from Anchor (strong) & Traceability Chain
    # ------------------------------------------------------------------
    depth = {}
    traceability = {ep: [ep] for ep in entrypoints}

    for ep in entrypoints:
        queue = deque([(ep, 0, [ep])])
        visited = set()
        while queue:
            node, d, path = queue.popleft()
            if node in visited:
                continue
            visited.add(node)

            if node not in depth or d < depth[node]:
                depth[node] = d

            if node not in traceability or len(path) < len(traceability[node]):
                traceability[node] = path

            for nxt in sorted(forward.get(node, [])):
                queue.append((nxt, d + 1, path + [nxt]))

    # ------------------------------------------------------------------
    # Depth from Anchor (soft)
    # ------------------------------------------------------------------
    depth_soft = {}
    for ep in entrypoints:
        queue = deque([(ep, 0)])
        visited = set()
        while queue:
            node, d = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            if node not in depth_soft or d < depth_soft[node]:
                depth_soft[node] = d
            for nxt in sorted(forward_soft.get(node, [])):
                queue.append((nxt, d + 1))

    # ------------------------------------------------------------------
    # Enrich artifacts with graph info
    # ------------------------------------------------------------------
    for a in artifacts:
        aid = a["id"]

        callers = sorted(backward.get(aid, []))
        callees = sorted(forward.get(aid, []))
        callers_soft = sorted(backward_soft.get(aid, []))
        callees_soft = sorted(forward_soft.get(aid, []))
        calls_external = sorted(forward_external.get(aid, []))
        called_by_external = sorted(backward_external.get(aid, []))

        callers_all = callers + called_by_external
        callees_all = callees + calls_external
        callers_soft_all = callers_soft + called_by_external
        callees_soft_all = callees_soft + calls_external

        if depth.get(aid) == 0:
            role = "entrypoint"
        elif callers_all and callees_all:
            role = "orchestrator"
        elif callers_all:
            role = "helper"
        else:
            role = "isolated"

        if depth_soft.get(aid) == 0:
            role_soft = "entrypoint"
        elif callers_soft_all and callees_soft_all:
            role_soft = "orchestrator"
        elif callers_soft_all:
            role_soft = "helper"
        else:
            role_soft = "isolated"

        # Schema compliance strictly enforced here while spatial edges remain safe in `a["edges"]`
        a["graph"] = {
            "calls": callees,
            "called_by": callers,
            "depth_from_anchor": depth.get(aid),
            "role": role,
            "calls_soft": callees_soft,
            "called_by_soft": callers_soft,
            "depth_from_anchor_soft": depth_soft.get(aid),
            "role_soft": role_soft,
            "calls_external": calls_external,
            "called_by_external": called_by_external,
        }

        a["graph_metrics"] = {
            "incoming_edges": len(callers),
            "outgoing_edges": len(callees),
            "is_entry_point": (role == "entrypoint" or role_soft == "entrypoint"),
            "traceability_chain": traceability.get(aid, []),
        }

    # ------------------------------------------------------------------
    # Reuse detection (STRONG graph)
    # ------------------------------------------------------------------
    reachable_from = defaultdict(set)
    for ep in entrypoints:
        queue = deque([ep])
        visited = set()
        while queue:
            node = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            reachable_from[node].add(ep)
            for nxt in forward.get(node, []):
                queue.append(nxt)

    for a in artifacts:
        aid = a["id"]
        used_by = sorted(reachable_from.get(aid, []))
        fan_in = len(used_by)
        a["reuse"] = {
            "fan_in": fan_in,
            "used_by_entrypoints": used_by,
            "is_shared_service": fan_in >= 2,
        }

    # ------------------------------------------------------------------
    # Write output deterministically
    # ------------------------------------------------------------------
    artifacts.sort(key=lambda x: x.get("id", ""))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps({"artifacts": artifacts}, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"[GRAPH] Enriched artifacts written to {output_path}")
    print(f"[GRAPH] entrypoints: {len(entrypoints)} (examples: {entrypoints[:5]})")
    print(
        f"[GRAPH] unresolved external references: {len(unresolved_refs)} (examples: {sorted(list(unresolved_refs))[:10]})"
    )

    orchestrators = sum(1 for a in artifacts if a.get("graph", {}).get("role") == "orchestrator")
    isolated = sum(1 for a in artifacts if a.get("graph", {}).get("role") == "isolated")
    print(f"[GRAPH] roles summary: orchestrators={orchestrators}, isolated={isolated}")


# ---------------------------------------------------------------------
# Module slicing utility (Option B: Universal Slicing Engine)
# ---------------------------------------------------------------------
def slice_enriched_for_module(
    global_enriched_path: Path, module_data: Any, output_path: Path
) -> None:
    """
    Create a module-scoped enriched artifacts file by filtering a global enriched set.
    Supports BOTH Logical Slicing (via AI Manifest entry_points) and Physical Slicing (fallback).
    """
    global_data = json.loads(Path(global_enriched_path).read_text(encoding="utf-8"))
    artifacts = global_data.get("artifacts", [])

    if not artifacts:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps({"artifacts": []}), encoding="utf-8")
        return

    # 1. LOGICAL SLICING (AI Manifest provided artifact list)
    if isinstance(module_data, dict):
        # ENTERPRISE FIX — use the explicit artifact membership list from the manifest.
        #
        # The previous implementation did a BFS traversal from entry_points following
        # all calls/calls_soft edges.  This caused a critical inflation bug:
        #   MOD-APP-LABO-STOCK (8 physical files) → 267 artifacts after BFS
        # because the 8 local artifacts call shared functions (f_commit, f_connect,
        # f_insert …) which transitively reach the entire call graph.
        #
        # The correct behaviour is direct membership filtering: Stage 2.5
        # (build_hybrid_manifest) has already determined exactly which artifact IDs
        # belong to each module and recorded them in module_data["artifacts"].
        # Signals from transitive dependencies are already bubbled up into each
        # artifact's "signals" block by _propagate_landmine_signals in Stage 2,
        # so the LLM has full risk context without needing to see 259 extra files.
        #
        # Fallback chain (in priority order):
        #   1. module_data["artifacts"]   — canonical membership list (Stage 2.5)
        #   2. module_data["entry_points"] — anchors only (legacy / edge-case guard)
        #   3. Empty slice with error log  — hard failure guard
        module_id = module_data.get("module_id", "UNKNOWN")
        member_ids = set(module_data.get("artifacts", []))

        if not member_ids:
            # Fallback: manifest was written before "artifacts" key was added;
            # use entry_points as a best-effort direct filter (no BFS).
            entry_points = module_data.get("entry_points", [])
            if not entry_points:
                print(
                    f"[GRAPH-SLICE] Error: Logical module '{module_id}' has neither "
                    f"'artifacts' nor 'entry_points'. Cannot slice."
                )
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(json.dumps({"artifacts": []}), encoding="utf-8")
                return
            member_ids = set(entry_points)
            print(
                f"[GRAPH-SLICE] WARN: Module '{module_id}' has no 'artifacts' list — "
                f"falling back to entry_points ({len(member_ids)} anchors)."
            )

        module_artifacts = [a for a in artifacts if a["id"] in member_ids]

    # 2. PHYSICAL SLICING (Directory Fallback)
    elif isinstance(module_data, Path):
        module_root_resolved = module_data.resolve()

        def belongs(a):
            p = a.get("path")
            if not p:
                return False
            try:
                return Path(p).resolve().is_relative_to(module_root_resolved)
            except Exception:
                try:
                    Path(p).resolve().relative_to(module_root_resolved)
                    return True
                except Exception:
                    return False

        module_artifacts = [a for a in artifacts if belongs(a)]

    else:
        print("[GRAPH-SLICE] Error: Invalid module_data type. Must be dict or Path.")
        return

    module_artifacts.sort(key=lambda x: x.get("id", ""))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps({"artifacts": module_artifacts}, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    mod_name = (
        module_data.get("module_id")
        if isinstance(module_data, dict)
        else getattr(module_data, "name", str(module_data))
    )
    print(
        f"[GRAPH-SLICE] Wrote {len(module_artifacts)} artifacts for module '{mod_name}' → {output_path.name}"
    )
