import json
import os
from pathlib import Path
import re
from typing import Any

# Dynamically import PluginRegistry to decouple static constraints
from ..scanner.plugin_registry import PluginRegistry

# Provider-agnostic LLM client (litellm backend)
try:
    from ..ai.llm_client import LLMClient
except ImportError:
    LLMClient = None

# Module-level lazy singleton — avoids re-reading project_config.json on every call
_llm_client_singleton = None
_runtime_api_key = None


def set_runtime_api_key(api_key: str | None) -> None:
    global _runtime_api_key, _llm_client_singleton
    if api_key != _runtime_api_key:
        _llm_client_singleton = None
    _runtime_api_key = api_key


def _get_llm_client():
    """
    Returns the module-level LLMClient singleton for fast, non-reasoning tasks.
    Returns None if unavailable, triggering the offline mock fallback.
    """
    global _llm_client_singleton
    if _llm_client_singleton is not None:
        return _llm_client_singleton

    if LLMClient is None:
        print("[HYBRID] LLMClient unavailable. Running in offline mock mode.")
        return None

    try:
        # Use reasoning model (deepseek-v4-pro) for all module discovery calls
        _llm_client_singleton = LLMClient(use_reasoning_model=True, api_key=_runtime_api_key)
        return _llm_client_singleton
    except Exception as e:
        print(f"[HYBRID] LLMClient init failed: {e}. Running in offline mock mode.")
        return None


_VALID_CLUSTERING_STRATEGIES = ("auto", "folder", "bfs", "ai")


def _read_clustering_strategy() -> str:
    """
    Read stage2_5.clustering_strategy from project_config.json.

    Values (single knob controlling Stage-2.5 module discovery):
      auto   — AI-primary semantic clustering, with deterministic fallback to
               folder (unique_folders >= 3) else lexical BFS. DEFAULT; unchanged.
      folder — Force one-module-per-folder grouping. Skips ALL Stage-2.5 LLM
               calls (both budget derivation and AI clustering). Best for repos
               already cleanly organized one module per folder (many PB apps).
      bfs    — Force lexical leading-token BFS grouping (still derives budget).
      ai     — Force AI-primary (same as auto: AI first, deterministic fallback).

    Mirrors LLMClient's config search so the SAME active project_config.json is
    read. Unknown/missing value -> 'auto'.
    """
    import os as _os

    candidates: list[Path] = []
    envp = _os.getenv("RIP_LLM_CONFIG_PATH")
    if envp:
        candidates.append(Path(envp))
    candidates.append(Path(_os.getcwd()) / "projects/sample_project/project_config.json")
    module_dir = Path(__file__).resolve().parent
    for anc in [module_dir] + list(module_dir.parents):
        candidates.append(anc / "project_config.json")

    for c in candidates:
        try:
            if c.exists():
                cfg = json.loads(c.read_text(encoding="utf-8-sig"))
                val = (
                    str(cfg.get("stage2_5", {}).get("clustering_strategy", "auto")).strip().lower()
                )
                return val if val in _VALID_CLUSTERING_STRATEGIES else "auto"
        except Exception:
            continue
    return "auto"


# -----------------------------------------------------------------------------
# 1. Task 5.1: Strict Enterprise Architectural Weight Range Table
# -----------------------------------------------------------------------------
ARCHITECTURAL_WEIGHT_RANGES = {
    "w_graph": {
        "min": 0.40,
        "max": 0.80,
        "default": 0.50,
        "description": "AST Call-Graph priority bounds. Scales up in flat/monolithic layouts.",
    },
    "w_data": {
        "min": 0.20,
        "max": 0.60,
        "default": 0.30,
        "description": "Database and schema lineage priority bounds. Scales up in high-DML systems.",
    },
    "w_folder": {
        "min": 0.00,
        "max": 0.40,
        "default": 0.20,
        "description": "Physical directory tree proximity bounds. Drops to min in flat repositories.",
    },
}


class MVPWeightCompiler:
    """
    Lightweight runtime engine that evaluates codebase topology
    and selects the ideal weight matrix within configured ranges.
    """

    @staticmethod
    def compile_runtime_weights(
        enriched_artifacts: dict[str, Any], final_candidates: list[Any]
    ) -> tuple[float, float, float]:
        print(
            "[MVP-TUNER] Evaluating repository structural telemetry for runtime weight allocation..."
        )

        # --- ENTERPRISE FIX: Defensive Initialization ---
        # Initialize variables to avoid 'unbound local variable' scope errors
        w_graph = ARCHITECTURAL_WEIGHT_RANGES["w_graph"]["default"]
        w_data = ARCHITECTURAL_WEIGHT_RANGES["w_data"]["default"]
        w_folder = ARCHITECTURAL_WEIGHT_RANGES["w_folder"]["default"]

        if not enriched_artifacts or not final_candidates:
            print("[MVP-TUNER] Empty repository context. Returning enterprise baseline defaults.")
            return (w_graph, w_data, w_folder)

        total_files = len(enriched_artifacts)
        unique_folders = set()
        sql_component_count = 0

        for art_id, art_data in enriched_artifacts.items():
            file_path_str = art_data.get("path", "")
            if file_path_str:
                unique_folders.add(Path(file_path_str).parent.name)

            regions = art_data.get("landmines", {}).get("masked_regions", [])
            if not regions:
                regions = art_data.get("landmine_details", {}).get("masked_regions", [])

            if any(r.get("category") == "embedded_sql" for r in regions):
                sql_component_count += 1

        folder_cardinality = len(unique_folders)
        data_density_ratio = sql_component_count / total_files if total_files > 0 else 0.0

        r_graph = ARCHITECTURAL_WEIGHT_RANGES["w_graph"]
        r_data = ARCHITECTURAL_WEIGHT_RANGES["w_data"]
        r_folder = ARCHITECTURAL_WEIGHT_RANGES["w_folder"]

        if folder_cardinality <= 1:
            print("[MVP-TUNER] Profile Detected: Flat/Monolithic repository layout.")
            w_folder = r_folder["min"]
            if data_density_ratio > 0.40:
                w_data = r_data["max"]
                w_graph = 1.0 - w_data
            else:
                w_graph = r_graph["max"]
                w_data = 1.0 - w_graph
        else:
            print("[MVP-TUNER] Profile Detected: Structured Multi-Folder repository layout.")
            w_folder = min(r_folder["max"], r_folder["default"] + (folder_cardinality * 0.02))
            remaining_budget = 1.0 - w_folder
            if data_density_ratio > 0.30:
                w_data = min(r_data["max"], r_data["default"] + (data_density_ratio * 0.2))
                w_graph = remaining_budget - w_data
            else:
                w_graph = min(r_graph["max"], r_graph["default"] + (1.0 - data_density_ratio) * 0.2)
                w_data = remaining_budget - w_graph

        # Final mathematical normalization
        w_graph = max(r_graph["min"], min(r_graph["max"], round(w_graph, 3)))
        w_data = max(r_data["min"], min(r_data["max"], round(w_data, 3)))
        w_folder = max(r_folder["min"], min(r_folder["max"], round(w_folder, 3)))

        # Guarantee parity (Sum = 1.0)
        total_sum = w_graph + w_data + w_folder
        if total_sum != 1.0:
            w_graph = round(w_graph + (1.0 - total_sum), 3)

        print(
            f"[MVP-TUNER] Runtime compiled selection Matrix -> [w_graph: {w_graph}, w_data: {w_data}, w_folder: {w_folder}]"
        )
        return w_graph, w_data, w_folder


# -----------------------------------------------------------------------------
# 2. ProjectSizeAssessor — Computes project telemetry for LLM budget derivation
# -----------------------------------------------------------------------------
class ProjectSizeAssessor:
    """
    Analyses a fully-enriched artifact graph and distills it into a compact
    telemetry payload.  This payload drives the two-call LLM budget derivation
    (structural tier + semantic filename) that replaces the old hard-coded
    module count.

    All values are computed from data already present in artifacts_enriched.json
    — zero additional I/O or LLM calls are required.
    """

    @staticmethod
    def assess(enriched_artifacts: dict[str, Any], paradigm: str = "unknown") -> dict[str, Any]:
        """
        Parameters
        ----------
        enriched_artifacts : dict  {artifact_id -> artifact_dict}
            The full enriched artifact map as loaded by build_hybrid_manifest.
        paradigm : str
            Source paradigm string from project_config (e.g. "pb", "vb6", "cobol").

        Returns
        -------
        dict  with the following keys:
            total_artifacts     int   — total number of artifacts in the graph
            total_anchors       int   — count of ui_anchor + batch_anchor archetypes
            anchor_ratio        float — total_anchors / total_artifacts (0–1)
            total_loc           int   — sum of lines_of_code across all artifacts
            avg_loc             float — mean LOC per artifact
            max_loc             int   — largest single artifact by LOC
            total_call_edges    int   — total resolved outgoing call edges
            graph_density       float — edges / (n*(n-1)) for n > 1, else 0
            isolated_count      int   — artifacts with role == "isolated"
            isolated_ratio      float — isolated_count / total_artifacts (0–1)
            shared_service_count int  — artifacts with reuse.fan_in >= 2
            unique_folders      int   — count of distinct parent directory names
            paradigm            str   — forwarded from parameter
            artifact_ids        list  — all artifact IDs (names), for semantic call
        """
        if not enriched_artifacts:
            return {
                "total_artifacts": 0,
                "total_anchors": 0,
                "anchor_ratio": 0.0,
                "total_loc": 0,
                "avg_loc": 0.0,
                "max_loc": 0,
                "total_call_edges": 0,
                "graph_density": 0.0,
                "isolated_count": 0,
                "isolated_ratio": 0.0,
                "shared_service_count": 0,
                "unique_folders": 0,
                "paradigm": paradigm,
                "artifact_ids": [],
            }

        total_artifacts = len(enriched_artifacts)
        total_anchors = 0
        total_loc = 0
        max_loc = 0
        total_call_edges = 0
        isolated_count = 0
        shared_service_count = 0
        unique_folders: set = set()
        artifact_ids: list = []

        for art_id, art in enriched_artifacts.items():
            artifact_ids.append(art_id)

            # Anchor detection — covers both archetype name variants
            art_type = str(art.get("type", "")).lower()
            if art_type in ("ui_anchor", "batch_anchor"):
                total_anchors += 1

            # LOC
            loc = art.get("metrics", {}).get("lines_of_code", 0) or 0
            total_loc += loc
            if loc > max_loc:
                max_loc = loc

            # Call edges (resolved outgoing only — avoids counting external phantoms)
            calls = art.get("graph", {}).get("calls", [])
            total_call_edges += len(calls) if isinstance(calls, list) else 0

            # Graph role
            role = art.get("graph", {}).get("role", "")
            if role == "isolated":
                isolated_count += 1

            # Shared service (reused by multiple entrypoints)
            if art.get("reuse", {}).get("fan_in", 0) >= 2:
                shared_service_count += 1

            # Folder diversity
            path_str = art.get("path", "")
            if path_str:
                unique_folders.add(Path(path_str).parent.name)

        n = total_artifacts
        graph_density = round(total_call_edges / (n * (n - 1)), 6) if n > 1 else 0.0
        avg_loc = round(total_loc / n, 2) if n > 0 else 0.0
        anchor_ratio = round(total_anchors / n, 4) if n > 0 else 0.0
        isolated_ratio = round(isolated_count / n, 4) if n > 0 else 0.0

        return {
            "total_artifacts": total_artifacts,
            "total_anchors": total_anchors,
            "anchor_ratio": anchor_ratio,
            "total_loc": total_loc,
            "avg_loc": avg_loc,
            "max_loc": max_loc,
            "total_call_edges": total_call_edges,
            "graph_density": graph_density,
            "isolated_count": isolated_count,
            "isolated_ratio": isolated_ratio,
            "shared_service_count": shared_service_count,
            "unique_folders": len(unique_folders),
            "paradigm": paradigm,
            "artifact_ids": artifact_ids,
        }


# -----------------------------------------------------------------------------
# 3. derive_module_budget — Dual-signal LLM budget derivation
# -----------------------------------------------------------------------------
# Size-tier table baked into the structural prompt.
# Tier boundaries drive consistent classification across providers and versions.
_TIER_TABLE_TEXT = """
SIZE TIER TABLE (use this to classify the project):
| Tier | Artifacts  | Total LOC    | Anchors   | Typical Module Range |
|------|-----------|--------------|-----------|----------------------|
| XS   | < 30      | < 3 000      | < 5       | 2 – 5                |
| S    | 30 – 100  | 3 000–15 000 | 5 – 15    | 5 – 10               |
| M    | 100 – 300 | 15 000–60 000| 15 – 40   | 10 – 20              |
| L    | 300 – 800 | 60 000–200 000| 40 – 100 | 15 – 30              |
| XL   | > 800     | > 200 000    | > 100     | 25 – 50              |

Rules:
1. Classify into exactly ONE tier (XS/S/M/L/XL).
2. recommended_count MUST be an integer within the tier's typical range.
3. If signals conflict (e.g. low LOC but many anchors), use anchor count as
   the tie-breaker — anchors are the strongest modularity signal.
4. confidence must be one of: "High", "Medium", "Low".
   - High  : all three signals (artifacts, LOC, anchors) agree on the same tier.
   - Medium: two of three signals agree.
   - Low   : signals conflict or data is sparse/unusual.
"""

_STRUCTURAL_SYSTEM_PROMPT = (
    "You are a senior software architect specialising in legacy system analysis. "
    "You will receive structural telemetry for a codebase and must classify its "
    "size tier, then recommend how many provisional modules to create for the "
    "initial spec-generation pass.\n\n"
    + _TIER_TABLE_TEXT
    + "\nRespond with valid JSON only — no commentary outside the JSON object."
)

_SEMANTIC_SYSTEM_PROMPT = (
    "You are a senior software architect specialising in legacy system domain analysis. "
    "You will receive a flat list of artifact identifiers (file/object names without "
    "extensions) from a legacy codebase.\n\n"
    "Your task:\n"
    "1. Identify distinct BUSINESS DOMAIN tokens visible in the naming patterns "
    "   (e.g. 'payroll', 'employee', 'inventory', 'batch', 'report').\n"
    "2. Count how many independent business modules those names suggest.\n"
    "3. Rate your confidence:\n"
    "   - High  : names are clearly domain-labelled (frmPayroll, w_zaiko, COBPAY001-style prefixes).\n"
    "   - Medium: some domain tokens visible but others are ambiguous.\n"
    "   - Low   : names are opaque codes (PGMACV001, BPXR042) — you cannot reliably infer domains.\n\n"
    "Respond with valid JSON only — no commentary outside the JSON object."
)


def derive_module_budget(telemetry: dict[str, Any], llm_client: Any) -> dict[str, Any]:
    """
    Derives the provisional module count (target_module_count) from two
    independent LLM calls (model driven by project_config.json):

      Call 1 — Structural : telemetry metrics  → tier classification + count + confidence
      Call 2 — Semantic   : artifact ID list   → domain inference   + count + confidence

    The two counts are combined via confidence-weighted average and silently
    clamped to [1, total_anchors] — no user configuration required.

    Parameters
    ----------
    telemetry   : dict   output of ProjectSizeAssessor.assess()
    llm_client  : LLMClient | None

    Returns
    -------
    dict with keys:
        target_count        int   — final module budget (use this downstream)
        structural          dict  — raw Call-1 response
        semantic            dict  — raw Call-2 response
        combination_log     str   — human-readable derivation trace
        offline_mode        bool  — True when LLM was unavailable
    """

    # ------------------------------------------------------------------
    # Shared JSON schemas for structured output
    # ------------------------------------------------------------------
    structural_schema = {
        "name": "structural_budget_schema",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "size_tier": {
                    "type": "string",
                    "enum": ["XS", "S", "M", "L", "XL"],
                    "description": "Project size tier from the tier table",
                },
                "recommended_count": {
                    "type": "integer",
                    "description": "Provisional module count within the tier's typical range",
                },
                "confidence": {
                    "type": "string",
                    "enum": ["High", "Medium", "Low"],
                    "description": "Confidence in the classification",
                },
                "rationale": {
                    "type": "string",
                    "description": "One-sentence justification citing specific metric values",
                },
            },
            "required": ["size_tier", "recommended_count", "confidence", "rationale"],
            "additionalProperties": False,
        },
    }

    semantic_schema = {
        "name": "semantic_budget_schema",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "domains_found": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of distinct business domain tokens identified",
                },
                "recommended_count": {
                    "type": "integer",
                    "description": "Suggested number of modules based on domain vocabulary",
                },
                "confidence": {
                    "type": "string",
                    "enum": ["High", "Medium", "Low"],
                    "description": "Confidence that names reflect real domain boundaries",
                },
                "rationale": {
                    "type": "string",
                    "description": "One-sentence justification citing naming evidence",
                },
            },
            "required": ["domains_found", "recommended_count", "confidence", "rationale"],
            "additionalProperties": False,
        },
    }

    # ------------------------------------------------------------------
    # Confidence → numeric weight mapping
    # ------------------------------------------------------------------
    CONFIDENCE_WEIGHTS = {"High": 0.70, "Medium": 0.50, "Low": 0.30}

    total_anchors = max(telemetry.get("total_anchors", 1), 1)

    def _weighted_combine(s_result: dict, f_result: dict) -> dict[str, Any]:
        """Combine two LLM results via confidence-weighted average."""
        w_s = CONFIDENCE_WEIGHTS.get(s_result.get("confidence", "Low"), 0.30)
        w_f = CONFIDENCE_WEIGHTS.get(f_result.get("confidence", "Low"), 0.30)
        count_s = max(int(s_result.get("recommended_count", 1)), 1)
        count_f = max(int(f_result.get("recommended_count", 1)), 1)

        raw = (count_s * w_s + count_f * w_f) / (w_s + w_f)
        combined = max(1, min(round(raw), total_anchors))

        log = (
            f"Structural: tier={s_result.get('size_tier', '?')} "
            f"count={count_s} conf={s_result.get('confidence', '?')} (w={w_s}) | "
            f"Semantic: domains={len(f_result.get('domains_found', []))} "
            f"count={count_f} conf={f_result.get('confidence', '?')} (w={w_f}) | "
            f"WeightedAvg={raw:.2f} → clamped to {combined} "
            f"(ceiling=total_anchors={total_anchors})"
        )
        return {"target_count": combined, "combination_log": log}

    # ------------------------------------------------------------------
    # Offline path — LLM unavailable
    # ------------------------------------------------------------------
    if llm_client is None:
        # Structural offline: classify tier by artifact count alone
        n = telemetry.get("total_artifacts", 0)
        anchors = telemetry.get("total_anchors", 1)
        if n < 30:
            tier, s_count = "XS", max(2, min(anchors, 5))
        elif n < 100:
            tier, s_count = "S", max(5, min(anchors, 10))
        elif n < 300:
            tier, s_count = "M", max(10, min(anchors, 20))
        elif n < 800:
            tier, s_count = "L", max(15, min(anchors, 30))
        else:
            tier, s_count = "XL", max(25, min(anchors, 50))

        offline_structural = {
            "size_tier": tier,
            "recommended_count": s_count,
            "confidence": "Medium",
            "rationale": f"Offline classification: {n} artifacts → tier {tier}.",
        }
        # Semantic offline: one domain per 8 anchors (rough heuristic)
        f_count = max(1, min(round(anchors / 8), anchors))
        offline_semantic = {
            "domains_found": [],
            "recommended_count": f_count,
            "confidence": "Low",
            "rationale": "Offline mode: semantic domain inference unavailable.",
        }
        combined = _weighted_combine(offline_structural, offline_semantic)
        print(f"[BUDGET] OFFLINE mode | {combined['combination_log']}")
        return {
            "target_count": combined["target_count"],
            "structural": offline_structural,
            "semantic": offline_semantic,
            "combination_log": combined["combination_log"],
            "offline_mode": True,
        }

    # ------------------------------------------------------------------
    # Call 1 — Structural telemetry
    # ------------------------------------------------------------------
    structural_result: dict[str, Any] = {}
    try:
        structural_user = json.dumps(
            {
                "project_telemetry": {
                    "total_artifacts": telemetry["total_artifacts"],
                    "total_anchors": telemetry["total_anchors"],
                    "anchor_ratio": telemetry["anchor_ratio"],
                    "total_loc": telemetry["total_loc"],
                    "avg_loc_per_artifact": telemetry["avg_loc"],
                    "max_loc": telemetry["max_loc"],
                    "total_call_edges": telemetry["total_call_edges"],
                    "graph_density": telemetry["graph_density"],
                    "isolated_count": telemetry["isolated_count"],
                    "isolated_ratio": telemetry["isolated_ratio"],
                    "shared_service_count": telemetry["shared_service_count"],
                    "unique_folder_count": telemetry["unique_folders"],
                    "paradigm": telemetry["paradigm"],
                }
            },
            indent=2,
        )

        raw = llm_client.complete(
            system_prompt=_STRUCTURAL_SYSTEM_PROMPT,
            user_prompt=structural_user,
            response_format={"type": "json_schema", "json_schema": structural_schema},
            temperature=0.1,
        )
        structural_result = json.loads(raw)
        print(
            f"[BUDGET] Structural call → tier={structural_result.get('size_tier')} "
            f"count={structural_result.get('recommended_count')} "
            f"conf={structural_result.get('confidence')}"
        )
    except Exception as exc:
        print(f"[BUDGET][WARN] Structural LLM call failed: {exc}. Using fallback values.")
        structural_result = {
            "size_tier": "M",
            "recommended_count": max(5, min(10, total_anchors)),
            "confidence": "Low",
            "rationale": f"Fallback after LLM error: {exc}",
        }

    # ------------------------------------------------------------------
    # Call 2 — Semantic / filename analysis
    # ------------------------------------------------------------------
    semantic_result: dict[str, Any] = {}
    try:
        artifact_ids = telemetry.get("artifact_ids", [])
        semantic_user = json.dumps(
            {"paradigm": telemetry["paradigm"], "artifact_identifiers": artifact_ids},
            ensure_ascii=False,
        )

        raw = llm_client.complete(
            system_prompt=_SEMANTIC_SYSTEM_PROMPT,
            user_prompt=semantic_user,
            response_format={"type": "json_schema", "json_schema": semantic_schema},
            temperature=0.1,
        )
        semantic_result = json.loads(raw)
        print(
            f"[BUDGET] Semantic  call → domains={len(semantic_result.get('domains_found', []))} "
            f"count={semantic_result.get('recommended_count')} "
            f"conf={semantic_result.get('confidence')}"
        )
    except Exception as exc:
        print(f"[BUDGET][WARN] Semantic LLM call failed: {exc}. Using fallback values.")
        semantic_result = {
            "domains_found": [],
            "recommended_count": max(
                1, min(structural_result.get("recommended_count", 5), total_anchors)
            ),
            "confidence": "Low",
            "rationale": f"Fallback after LLM error: {exc}",
        }

    # ------------------------------------------------------------------
    # Combine + clamp
    # ------------------------------------------------------------------
    combined = _weighted_combine(structural_result, semantic_result)
    print(f"[BUDGET] Final → {combined['combination_log']}")

    return {
        "target_count": combined["target_count"],
        "structural": structural_result,
        "semantic": semantic_result,
        "combination_log": combined["combination_log"],
        "offline_mode": False,
    }


# -----------------------------------------------------------------------------
# 3. Task 5.2: Seed-Based Agglomerative Hierarchical Clustering Logic
# -----------------------------------------------------------------------------
def generate_hybrid_clusters(
    final_candidates: list,
    enriched_artifacts: dict,
    registry: PluginRegistry,
    w_graph: float,
    w_data: float,
    w_folder: float,
) -> dict:
    """
    Combines control-flow metrics, data lineage mappings, and workspace locations
    to dynamically group assets into distinct structural business modules.
    """
    seeds = {c["data"]["id"]: c for c in final_candidates}
    clusters = {}

    for seed_id, seed_data in seeds.items():
        clusters[seed_id] = {
            "module_root_id": seed_id,
            "strict_arch": seed_data["strict_archetype"],
            "entry_points": [seed_id],
            "associated_elements": set(seed_data["context_items"]),
            "max_score": seed_data["score"],
            "shared_tables": set(),
        }

        regions = enriched_artifacts.get(seed_id, {}).get("landmines", {}).get("masked_regions", [])
        if not regions:
            regions = (
                enriched_artifacts.get(seed_id, {})
                .get("landmine_details", {})
                .get("masked_regions", [])
            )
        for region in regions:
            if region.get("category") == "embedded_sql":
                clusters[seed_id]["shared_tables"].add(seed_id)

    for art_id, art_data in enriched_artifacts.items():
        if art_id in seeds:
            continue

        file_path_str = art_data.get("path", "")
        if not file_path_str:
            continue
        ext = Path(file_path_str).suffix.lower().strip()

        # Resolve individual asset extensions dynamically at runtime via registry lookup
        plugin = registry.get_plugin_for_extension(ext)
        if not plugin:
            continue

        best_seed = None
        min_distance = float("inf")

        for seed_id, cluster in clusters.items():
            seed_meta = enriched_artifacts.get(seed_id, {})

            # Dimension A: Call Graph Connection (AST Shortest Path Check)
            is_direct_dependency = art_id in cluster["associated_elements"]
            graph_dist = 1.0 if is_direct_dependency else 4.0

            # Dimension B: Data Lineage Connection (Shared SQL/Data Tables Check)
            art_regions = art_data.get("landmines", {}).get("masked_regions", [])
            if not art_regions:
                art_regions = art_data.get("landmine_details", {}).get("masked_regions", [])
            has_shared_data_lineage = any(
                r.get("category") == "embedded_sql" for r in art_regions
            ) and (seed_id in cluster["shared_tables"])
            data_dist = 0.5 if has_shared_data_lineage else 2.0

            # Dimension C: Physical Path Proximity (Workspace Proximity Check)
            seed_path_str = seed_meta.get("path", "")
            same_folder = False
            if file_path_str and seed_path_str:
                same_folder = Path(file_path_str).parent == Path(seed_path_str).parent
            folder_dist = 0.5 if same_folder else 3.0

            # Distance Cost Summation
            total_distance = (
                (graph_dist * w_graph) + (data_dist * w_data) + (folder_dist * w_folder)
            )

            if total_distance < min_distance:
                min_distance = total_distance
                best_seed = seed_id

        if best_seed and min_distance < 2.5:
            clusters[best_seed]["entry_points"].append(art_id)
            calls = art_data.get("graph", {}).get("calls", [])
            if isinstance(calls, list):
                clusters[best_seed]["associated_elements"].update(calls)
            loc = art_data.get("metrics", {}).get("lines_of_code", 0)
            clusters[best_seed]["max_score"] = max(
                clusters[best_seed]["max_score"], round(loc / 50.0, 2)
            )

    return clusters


# -----------------------------------------------------------------------------
# 4a. generate_folder_clusters — Option C (primary path, non-flat codebases)
# -----------------------------------------------------------------------------
def generate_folder_clusters(
    enriched_artifacts: dict[str, Any],
    registry: PluginRegistry,
) -> list[dict[str, Any]]:
    """
    Groups artifacts by their physical parent folder to form provisional modules.
    This is the primary provisional-grouping strategy for codebases where
    unique_folders >= 3.

    Algorithm
    ---------
    1. Bucket every artifact into its parent folder name.
    2. Within each bucket identify anchor artifacts (ui_anchor / batch_anchor)
       as entry_points.  If a bucket has no anchors, promote all its members
       as entry_points (handles pure-utility folders).
    3. Collect shared-service artifacts (reuse.fan_in >= 2) into a dedicated
       MOD-SHARED bucket that spans all folders.
    4. Derive a safe module_id from the folder name.
    5. Return a list of provisional module dicts with the same schema expected
       by build_hybrid_manifest (module_id, module_name, description,
       execution_track, entry_points, provisional=True).

    Parameters
    ----------
    enriched_artifacts : dict  {art_id -> artifact_dict}
    registry           : PluginRegistry  (used for extension → archetype lookup)

    Returns
    -------
    list of provisional module dicts
    """
    from collections import defaultdict

    # ------------------------------------------------------------------
    # Step 1: bucket artifacts by parent folder
    # ------------------------------------------------------------------
    folder_buckets: dict[str, list[str]] = defaultdict(list)
    shared_service_ids: list[str] = []

    _anchor_types = {"ui_anchor", "batch_anchor"}

    for art_id, art in enriched_artifacts.items():
        # Shared services belong to MOD-SHARED, not a single folder module.
        # IMPORTANT: ui_anchor / batch_anchor artifacts are EXCLUDED from
        # shared_service_ids even when fan_in >= 2 (e.g. a login screen called
        # from multiple places).  Anchors carry direct user-facing value and
        # must stay in their folder/business module; only true utilities
        # (NVOs, helpers, data services) should route to MOD-SHARED.
        art_type = str(art.get("type", "")).lower()
        if art.get("reuse", {}).get("fan_in", 0) >= 2 and art_type not in _anchor_types:
            shared_service_ids.append(art_id)
            # Still allow them to appear in their folder bucket so the folder
            # module is not left empty — shared membership is intentional.

        path_str = art.get("path", "")
        folder_name = Path(path_str).parent.name if path_str else "_root"
        if not folder_name or folder_name in (".", ""):
            folder_name = "_root"
        folder_buckets[folder_name].append(art_id)

    # ------------------------------------------------------------------
    # Step 2: build provisional modules per folder
    # ------------------------------------------------------------------
    modules: list[dict[str, Any]] = []
    anchor_types = {"ui_anchor", "batch_anchor"}

    for folder_name, art_ids in sorted(folder_buckets.items()):
        # Identify entry points (anchors) within this folder
        entry_points = [
            aid
            for aid in art_ids
            if str(enriched_artifacts.get(aid, {}).get("type", "")).lower() in anchor_types
        ]
        # If no anchors in folder, promote all members as entry points
        if not entry_points:
            entry_points = list(art_ids)

        # Derive execution track from anchor archetypes in this folder
        has_ui = any(
            str(enriched_artifacts.get(aid, {}).get("type", "")).lower() == "ui_anchor"
            for aid in entry_points
        )
        has_batch = any(
            str(enriched_artifacts.get(aid, {}).get("type", "")).lower() == "batch_anchor"
            for aid in entry_points
        )
        if has_ui and has_batch:
            exec_track = "Mixed-Track"
        elif has_batch:
            exec_track = "Batch-Track"
        else:
            exec_track = "UI-Track"

        safe_id = re.sub(r"[^A-Z0-9-]", "-", folder_name.upper()[:16]).strip("-")
        module_id = f"MOD-{safe_id}"
        # Guard: "MOD-SHARED" is reserved exclusively for the cross-cutting
        # shared-services bucket (Step 3).  If a physical folder happens to
        # produce the same ID (e.g. a folder literally named "shared"),
        # disambiguate with the "-FLD" suffix so module IDs stay unique.
        if module_id == "MOD-SHARED":
            module_id = "MOD-SHARED-FLD"

        modules.append(
            {
                "module_id": module_id,
                "module_name": folder_name,  # provisional — semantic naming deferred
                "description": (
                    f"Provisional module derived from folder '{folder_name}'. "
                    "Semantic naming deferred to Stage 5."
                ),
                "execution_track": exec_track,
                "entry_points": entry_points,
                # Full membership list — consumed by slice_enriched_for_module so it
                # can filter by exact set membership instead of BFS traversal.
                "artifacts": sorted(art_ids),
                "provisional": True,
                "source_folder": folder_name,
            }
        )

    # ------------------------------------------------------------------
    # Step 3: MOD-SHARED bucket for cross-cutting shared services
    # ------------------------------------------------------------------
    if shared_service_ids:
        modules.append(
            {
                "module_id": "MOD-SHARED",
                "module_name": "Shared Services",
                "description": (
                    "Cross-cutting shared services reused by multiple entry points "
                    "(fan_in >= 2). Semantic naming deferred to Stage 5."
                ),
                "execution_track": "Mixed-Track",
                "entry_points": sorted(set(shared_service_ids)),
                # Full membership list for downstream slicing consistency.
                "artifacts": sorted(set(shared_service_ids)),
                "provisional": True,
                "source_folder": "_shared",
            }
        )

    print(
        f"[FOLDER-CLUSTER] Generated {len(modules)} provisional modules "
        f"from {len(folder_buckets)} physical folder(s). "
        f"Shared services bucket: {len(shared_service_ids)} artifact(s)."
    )
    return modules


# -----------------------------------------------------------------------------
# 4b. generate_bfs_clusters — Fallback F4 (flat codebases, unique_folders < 3)
# -----------------------------------------------------------------------------
# Composite-affinity tunables (config-ready: a future project_config.stage25 block
# can override these without code changes). Defaults chosen so structural evidence
# dominates when present, with lexical carrying sparse graphs.
_BFS_AFFINITY_WEIGHTS = {"structural": 1.0, "coupling": 0.5, "lexical": 0.3}
_BFS_MIN_AFFINITY = 1e-9  # merge only pairs whose affinity is strictly positive
# A dependency shared by more than this FRACTION of all artifacts is treated as
# INFRASTRUCTURE (e.g. the DB connection, a data-access layer, a global-var module)
# and contributes ZERO structural affinity — it links the whole app, not a domain,
# so counting it would chain every feature into one mega-module. IDF alone is not
# enough: summed over several shared infra deps it stays positive and still merges.
_BFS_INFRA_FANIN_FRACTION = 0.25  # config-ready
_BFS_INFRA_FANIN_FLOOR = 4  # never treat a dep shared by < this many as infra
_BFS_MIN_TOKEN_LEN = 3
# Generic name tokens that carry no domain signal (language-neutral stoplist).
_BFS_TOKEN_STOPWORDS = {
    "frm",
    "cls",
    "mod",
    "form",
    "class",
    "module",
    "bas",
    "ctl",
    "dsr",
    "srw",
    "sru",
    "srd",
    "cbl",
    "cpy",
    "jcl",
    "main",
    "sub",
    "dlg",
    "dialog",
    "new",
    "edit",
    "add",
    "del",
    "delete",
    "update",
    "view",
    "tmp",
    "temp",
    "test",
    "util",
    "utils",
    "common",
    "base",
    "app",
    "win",
    "ctrl",
    "obj",
    "data",
    "info",
    "mgr",
    "manager",
    "svc",
    "service",
    "impl",
    "the",
    "and",
}


def _bfs_tokenize(name: str) -> set:
    """Language-neutral name tokenizer for lexical affinity. Splits on separators,
    camelCase boundaries, and letter/digit boundaries; lowercases; drops generic
    stopwords, pure digits, and sub-minimal tokens. Uses the original-case file
    name (camelCase preserved) so VB6 'frmUserRecAE' -> {'user','rec'} while COBOL
    'COPAUS0C' -> {'copaus'} — each stack contributes whatever lexical signal it has."""
    import re as _re

    if not name:
        return set()
    stem = str(name).rsplit(".", 1)[0]
    parts = _re.split(
        r"[_\-.\s]+|(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])",
        stem,
    )
    toks = set()
    for p in parts:
        p = p.strip().lower()
        if len(p) >= _BFS_MIN_TOKEN_LEN and not p.isdigit() and p not in _BFS_TOKEN_STOPWORDS:
            toks.add(p)
    return toks


def _bfs_first_token(name: str) -> str:
    """Return the first meaningful (domain-leading) name token, preserving order.
    Legacy artifacts are named domain-first (frmVanCollection -> 'van', COPAUS0C ->
    'copaus'), so the leading token is the most reliable DOMAIN key and — unlike a
    trailing action word (...PrintOp / ...Viewer) — never bridges unrelated
    domains. Same split/stoplist rules as _bfs_tokenize, but ordered."""
    import re as _re

    if not name:
        return ""
    stem = str(name).rsplit(".", 1)[0]
    parts = _re.split(
        r"[_\-.\s]+|(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])",
        stem,
    )
    for p in parts:
        p = p.strip().lower()
        if len(p) >= _BFS_MIN_TOKEN_LEN and not p.isdigit() and p not in _BFS_TOKEN_STOPWORDS:
            return p
    return ""


def generate_bfs_clusters(
    enriched_artifacts: dict[str, Any],
    target_count: int,
) -> list[dict[str, Any]]:
    """
    Provisional module grouping for flat / single-folder codebases.

    Algorithm
    ---------
    1. Extract BFS traceability chains already computed in Stage 2
       (graph_metrics.traceability_chain per entrypoint artifact).
    2. Separate shared-service artifacts (reuse.fan_in >= 2) → MOD-SHARED.
    3. If raw BFS cluster count > target_count, merge the closest pair
       (highest shared-dependency overlap) iteratively until the count
       reaches target_count.
    4. Any artifact not covered by any chain (truly orphaned) is appended
       to the nearest cluster by direct call-graph adjacency, or to
       MOD-SHARED as a last resort.

    Parameters
    ----------
    enriched_artifacts : dict  {art_id -> artifact_dict}
    target_count       : int   from derive_module_budget() — the agglomeration cap

    Returns
    -------
    list of provisional module dicts (same schema as generate_folder_clusters)
    """
    from collections import defaultdict

    # ------------------------------------------------------------------
    # Step 1: collect shared services
    # ------------------------------------------------------------------
    _bfs_anchor_types = {"ui_anchor", "batch_anchor"}
    shared_service_ids: set = {
        aid
        for aid, art in enriched_artifacts.items()
        if art.get("reuse", {}).get("fan_in", 0) >= 2
        and str(art.get("type", "")).lower() not in _bfs_anchor_types
    }

    # ------------------------------------------------------------------
    # Step 2: build raw BFS clusters from traceability chains
    # ------------------------------------------------------------------
    # Each entrypoint's traceability_chain is its BFS-reachable set,
    # computed during Stage 2 (call_graph_builder.enrich_with_call_graph).
    # Use it directly — zero extra computation.
    raw_clusters: dict[str, set] = {}  # root_id -> set of member art_ids

    for art_id, art in enriched_artifacts.items():
        chain = art.get("graph_metrics", {}).get("traceability_chain", [])
        if not chain:
            continue
        root = chain[0]
        if root not in raw_clusters:
            raw_clusters[root] = set()
        raw_clusters[root].update(chain)

    # Purge shared-service roots from raw_clusters.
    # A shared service whose traceability chain starts with itself will have
    # created a singleton cluster above.  It must not become an independent
    # module — it belongs exclusively in the MOD-SHARED bucket (Step 4).
    # NOTE: shared-service *members* that appear inside another root's chain
    # are left untouched; only root entries are removed here.
    for sid in shared_service_ids:
        raw_clusters.pop(sid, None)

    # Artifacts with no traceability chain get their own singleton cluster,
    # unless they are already a shared service.
    covered = set(mid for members in raw_clusters.values() for mid in members)
    for art_id in enriched_artifacts:
        if art_id not in covered and art_id not in shared_service_ids:
            raw_clusters[art_id] = {art_id}

    print(
        f"[BFS-CLUSTER] Raw BFS clusters: {len(raw_clusters)} | "
        f"Target: {target_count} | Shared services: {len(shared_service_ids)}"
    )

    # ------------------------------------------------------------------
    # Step 3: lexical-primary grouping (deterministic; AI-fallback baseline)
    # ------------------------------------------------------------------
    # Group the structural BFS seeds by their LEADING DOMAIN TOKEN. Structural
    # cohesion is already captured inside each seed (a program plus its dependency
    # chain) - that is structural's legitimate role. Structural affinity must NOT
    # drive cross-seed merges: in a homogeneous CRUD app every feature couples to
    # the same shared framework, so structural-driven agglomeration collapses
    # distinct domains into one mega-module. The reliable DOMAIN signal is the
    # leading name token (frmVanCollection -> 'van'; COPAUS0C / COPAUS1C ->
    # 'copaus'); a trailing action word (...PrintOp / ...Viewer) can never bridge
    # domains. Module count follows the app's real domain count (target_count is
    # advisory). Deterministic + language-neutral; this is also the FALLBACK the
    # AI semantic-clustering layer degrades to when the LLM is unavailable.
    def _seed_lexkey(root: str) -> str:
        fn = (enriched_artifacts.get(root, {}) or {}).get("file_name") or root
        return _bfs_first_token(fn)

    by_key: dict[str, list] = defaultdict(list)
    keyless: list = []
    for root in sorted(raw_clusters.keys()):
        k = _seed_lexkey(root)
        if k:
            by_key[k].append(root)
        else:
            keyless.append(root)

    grouped: dict[str, set] = {}
    for k in sorted(by_key.keys()):
        roots = sorted(by_key[k])
        survivor = roots[0]
        members: set = set()
        for r in roots:
            members |= raw_clusters[r]
        grouped[survivor] = members
    for r in keyless:  # no domain token -> keep as its own seed
        grouped[r] = set(raw_clusters[r])

    raw_clusters = grouped
    print(
        f"[BFS-CLUSTER] Lexical-primary grouping: {len(raw_clusters)} domain module(s) "
        f"(leading-token families; soft target={target_count})."
    )

    # ------------------------------------------------------------------
    # Step 4: build provisional module dicts
    # ------------------------------------------------------------------
    anchor_types = {"ui_anchor", "batch_anchor"}
    modules: list[dict[str, Any]] = []
    ungrouped_ids: list[str] = []

    for root_id, members in sorted(raw_clusters.items()):
        member_list = sorted(members)

        # Residual handling: a lone NON-anchor artifact that attracted no cohesive
        # merge is not a feature module — collect it into MOD-UNGROUPED (an honest
        # "couldn't confidently group" bucket) rather than emit a 1-artifact
        # pseudo-module. Anchor singletons (a standalone screen/program) ARE
        # legitimate modules and are kept.
        if (
            len(member_list) == 1
            and str(enriched_artifacts.get(member_list[0], {}).get("type", "")).lower()
            not in anchor_types
        ):
            ungrouped_ids.extend(member_list)
            continue

        entry_points = [
            aid
            for aid in member_list
            if str(enriched_artifacts.get(aid, {}).get("type", "")).lower() in anchor_types
        ]
        if not entry_points:
            entry_points = member_list  # promote all if no anchors found

        has_ui = any(
            str(enriched_artifacts.get(aid, {}).get("type", "")).lower() == "ui_anchor"
            for aid in entry_points
        )
        has_batch = any(
            str(enriched_artifacts.get(aid, {}).get("type", "")).lower() == "batch_anchor"
            for aid in entry_points
        )
        if has_ui and has_batch:
            exec_track = "Mixed-Track"
        elif has_batch:
            exec_track = "Batch-Track"
        else:
            exec_track = "UI-Track"

        safe_id = re.sub(r"[^A-Z0-9-]", "-", root_id.upper()[:16]).strip("-")
        module_id = f"MOD-{safe_id}"

        modules.append(
            {
                "module_id": module_id,
                "module_name": root_id,  # provisional — semantic naming deferred
                "description": (
                    f"Provisional module rooted at '{root_id}' via BFS traceability. "
                    "Semantic naming deferred to Stage 5."
                ),
                "execution_track": exec_track,
                "entry_points": entry_points,
                # Full membership list — consumed by slice_enriched_for_module so it
                # can filter by exact set membership instead of BFS traversal.
                "artifacts": member_list,
                "provisional": True,
                "source_folder": "_flat",
            }
        )

    # MOD-UNGROUPED — honest residual bucket for low/zero-affinity non-anchor
    # singletons (leftover utilities/fragments). Flagged for human review; keeps the
    # module set clean without fabricating a cohesive-looking mega-module.
    if ungrouped_ids:
        modules.append(
            {
                "module_id": "MOD-UNGROUPED",
                "module_name": "Ungrouped Artifacts",
                "description": (
                    "Artifacts with no confident structural or lexical affinity to any "
                    "module (low-coupling utilities / leftovers). Flagged for human "
                    "review — not a cohesive feature module. Semantic naming deferred."
                ),
                "execution_track": "Mixed-Track",
                "entry_points": sorted(ungrouped_ids),
                "artifacts": sorted(ungrouped_ids),
                "provisional": True,
                "source_folder": "_ungrouped",
            }
        )

    # MOD-SHARED for cross-cutting services
    if shared_service_ids:
        modules.append(
            {
                "module_id": "MOD-SHARED",
                "module_name": "Shared Services",
                "description": (
                    "Cross-cutting shared services reused by multiple entry points "
                    "(fan_in >= 2). Semantic naming deferred to Stage 5."
                ),
                "execution_track": "Mixed-Track",
                "entry_points": sorted(shared_service_ids),
                # Full membership list for downstream slicing consistency.
                "artifacts": sorted(shared_service_ids),
                "provisional": True,
                "source_folder": "_shared",
            }
        )

    return modules


# -----------------------------------------------------------------------------
# 4c. Utilities
# -----------------------------------------------------------------------------
def get_project_root() -> Path:
    """Dynamically resolve the root of the project to prevent Pathing crashes."""
    cwd = Path(os.getcwd())
    if (cwd / "prompts").exists():
        return cwd
    script_dir = Path(__file__).resolve().parent
    for parent in [script_dir] + list(script_dir.parents):
        if (parent / "prompts").exists():
            return parent
    return cwd


def load_prompt(prompt_name: str) -> str:
    """Reads the prompt text safely from the /prompts directory."""
    prompt_path = get_project_root() / "prompts" / prompt_name
    if not prompt_path.exists():
        print(f"[WARN] Prompt file not found: {prompt_path}. Using fallback system instructions.")
        return "You are an AI architect. Analyze the provided context. Output JSON with module_name, description, and execution_track."
    return prompt_path.read_text(encoding="utf-8")


def call_llm(system_prompt: str, user_data: str) -> dict[str, Any]:
    """
    Calls the LLM API via LLMClient. Includes a deterministic mock fallback if
    running locally without API keys or libraries.
    Model selection is fully driven by project_config.json — no hardcoded overrides.
    """
    client = _get_llm_client()

    if not client:
        try:
            parsed_user = json.loads(user_data)
            is_ui = "ui_anchor" in str(parsed_user.get("type", "")).lower()
        except:
            is_ui = True

        return {
            "module_name": "Mock Auto-Discovered Module",
            "description": "Auto-generated mock description due to missing API key.",
            "execution_track": "UI-Track" if is_ui else "Batch-Track",
            "ai_semantic_confidence": "Medium",
            "confidence_note": "Generated via Offline Mock.",
            "status": "APPROVED",
        }

    try:
        if "critic" in system_prompt.lower() or "reviewer" in system_prompt.lower():
            schema = {
                "name": "module_critic_schema",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "enum": ["APPROVED", "REJECTED"]},
                        "feedback": {
                            "type": "string",
                            "description": "Actionable feedback or empty if approved",
                        },
                    },
                    "required": ["status", "feedback"],
                    "additionalProperties": False,
                },
            }
        else:
            schema = {
                "name": "module_discovery_schema",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "module_name": {
                            "type": "string",
                            "description": "DDD domain specific name max 4 words",
                        },
                        "description": {
                            "type": "string",
                            "description": "Max 2 sentence functional description",
                        },
                        "execution_track": {
                            "type": "string",
                            "enum": ["UI-Track", "Batch-Track", "Mixed-Track"],
                            "description": "Strict paradigm execution routing track",
                        },
                        "ai_semantic_confidence": {
                            "type": "string",
                            "enum": ["High", "Medium", "Low"],
                        },
                        "confidence_note": {
                            "type": "string",
                            "description": "Citing specific SQL or structural context evidence",
                        },
                    },
                    "required": [
                        "module_name",
                        "description",
                        "execution_track",
                        "ai_semantic_confidence",
                        "confidence_note",
                    ],
                    "additionalProperties": False,
                },
            }

        kwargs = {
            "system_prompt": system_prompt,
            "user_prompt": user_data,
            "response_format": {"type": "json_schema", "json_schema": schema},
            "temperature": 0.2,
        }

        raw_text = client.complete(**kwargs)
        return json.loads(raw_text)
    except Exception as e:
        print(f"[ERROR] LLM Call Failed: {e}")
        return {}


# -----------------------------------------------------------------------------
# 5. Core Logic
# -----------------------------------------------------------------------------
def run_generator_critic_loop(
    artifact_data: dict,
    context_items: list,
    discovery_prompt: str,
    critic_prompt: str,
    telemetry: dict = None,
) -> dict:
    """Executes the LLM Generator-Critic loop with a circuit breaker."""
    max_retries = 2

    user_context = json.dumps(
        {
            "entry_point": artifact_data.get("id"),
            "type": artifact_data.get("type"),
            "dependencies": context_items,
        }
    )

    for attempt in range(max_retries + 1):
        gen_response = call_llm(discovery_prompt, user_context)
        if not gen_response:
            break

        critic_input = json.dumps(
            {
                "context": user_context,
                "proposal": gen_response,
                "architectural_telemetry": telemetry or {},
            }
        )
        critic_choice = call_llm(critic_prompt, critic_input)

        if critic_choice and critic_choice.get("status", "").upper() == "APPROVED":
            return {
                "module_name": gen_response.get("module_name", "Unnamed Module"),
                "description": gen_response.get("description", "No description provided."),
                "execution_track": gen_response.get("execution_track", "Mixed-Track"),
                "ai_semantic_confidence": gen_response.get("ai_semantic_confidence", "Unknown"),
                "confidence_note": gen_response.get("confidence_note", "Approved by Critic."),
                "llm_iterations": attempt + 1,
            }
        else:
            feedback = (
                critic_choice.get("feedback", "Generic rejection")
                if critic_choice
                else "No response"
            )
            user_context += f"\nCRITIC FEEDBACK: {feedback}"

    is_ui = "ui_anchor" in str(artifact_data.get("type", "")).lower()
    safe_id = re.sub(r"[^A-Z0-9-]", "-", artifact_data.get("id", "UNK").upper()[:12])

    return {
        "module_name": f"Module_{safe_id}_[NEEDS_REVIEW]",
        "description": "LLM loop failed to reach consensus. Manual review required.",
        "execution_track": "UI-Track" if is_ui else "Batch-Track",
        "ai_semantic_confidence": "Low",
        "confidence_note": "AI failed to produce a valid architectural justification or reached retry limit.",
        "llm_iterations": max_retries + 1,
    }


def calculate_polyglot_score(artifact_data: dict, reuse_data: dict, strict_archetype: str) -> float:
    """Calculates Architectural Gravity (Topological Score)."""
    score = 0.0

    loc = artifact_data.get("metrics", {}).get("lines_of_code", 0)
    fan_in = reuse_data.get("fan_in", 0)
    landmine = artifact_data.get("landmine_score", 0)

    score += (loc / 50.0) + (landmine * 20.0)

    if strict_archetype == "ui_anchor":
        score += 100.0
    elif strict_archetype == "batch_anchor":
        score += 70.0

    if fan_in == 0:
        score += 40.0
    elif fan_in > 15:
        score -= 100.0

    return round(score, 2)


# -----------------------------------------------------------------------------
# 5c. AI-primary semantic clustering (with deterministic fallback + validation)
# -----------------------------------------------------------------------------
def _extract_json_array(text: str):
    """Tolerantly extract a JSON array (or {'modules':[...]}) from an LLM response.
    Handles code fences and leading/trailing prose. Returns a list or None."""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t).strip()
    s, e = t.find("["), t.rfind("]")
    if s != -1 and e != -1 and e > s:
        try:
            obj = json.loads(t[s : e + 1])
            if isinstance(obj, list):
                return obj
        except Exception:
            pass
    s, e = t.find("{"), t.rfind("}")
    if s != -1 and e != -1 and e > s:
        try:
            obj = json.loads(t[s : e + 1])
            if isinstance(obj, dict) and isinstance(obj.get("modules"), list):
                return obj["modules"]
        except Exception:
            pass
    return None


def generate_ai_clusters(enriched_artifacts: dict[str, Any], target_count: int, llm_client: Any):
    """AI-PRIMARY semantic module clustering. Uses the LLM's domain understanding to
    group artifacts into cohesive BUSINESS-CAPABILITY modules — resolving what the
    deterministic heuristics cannot (e.g. Daily/Monthly/Weekly SalesPrintOp -> one
    'Sales Reporting' module; Customer/AccCustomer/NCustomer -> 'Customer
    Management') and producing business names + descriptions in the same pass.

    Enterprise-safe by design:
      • Returns None on ANY failure/low-confidence so the caller falls back to the
        deterministic folder/lexical grouping (never a single point of failure).
      • Does NOT use response_format=json_object (rejected by some providers, e.g.
        DeepSeek); prompts for JSON in text and parses/repairs, riding the
        LLMClient empty/malformed-response retry.
      • Deterministic coverage validation: only known ids, each assigned once;
        unassigned artifacts routed to MOD-UNGROUPED for guaranteed 100% coverage.
    """
    if llm_client is None:
        return None
    ids = sorted(enriched_artifacts.keys())
    if len(ids) < 3:
        return None  # trivial — let the deterministic path handle it

    def _row(aid: str) -> str:
        a = enriched_artifacts.get(aid, {}) or {}
        fn = a.get("file_name") or aid
        typ = str(a.get("type", "")).lower() or "unknown"
        fanin = (a.get("reuse", {}) or {}).get("fan_in", 0)
        return f"{aid} | {fn} | {typ} | fan_in={fanin}"

    system_prompt = (
        "You are a Lead Modernization Architect performing MODULE IDENTIFICATION on a "
        "legacy application. Group the source artifacts into cohesive BUSINESS-CAPABILITY "
        "modules (business domains/features) — NOT by file type or technical layer. "
        "Related screens and their add/edit/view/print variants and reports belong to the "
        "SAME module (e.g. all Van* screens -> 'Route/Van Management'; Daily/Monthly/Weekly "
        "sales reports -> 'Sales Reporting'; Customer/AccCustomer/NCustomer -> 'Customer "
        "Management'). Put cross-cutting utilities/helpers (encryption, DB access, global "
        "variables, generic UI controls) into a SINGLE 'Shared / Common Services' module "
        'and mark ONLY that module "is_shared": true. Every genuine business-capability '
        'module MUST be "is_shared": false. At most ONE module may have is_shared=true. '
        "Genuine user-facing screens that carry their own business workflow (e.g. a "
        "login/authentication screen) belong in a BUSINESS module, NOT in Shared; put "
        "only purely-reusable UI infrastructure (splash/about, generic search/lookup/"
        "date-picker dialogs, message boxes) in the Shared module. "
        "RULES: assign EVERY artifact id to EXACTLY ONE module; use only the ids provided; "
        f"aim for roughly {target_count} modules but prefer correct business grouping over "
        "that number. Output ONLY a JSON array (no prose, no code fence). Each element: "
        '{"module_name": "<business domain, <=4 words>", '
        '"business_description": "<1-2 sentence functional summary>", '
        '"execution_track": "UI-Track|Batch-Track|Mixed-Track", '
        '"is_shared": true|false, '
        '"artifact_ids": ["id", ...]}.'
    )
    user_prompt = "ARTIFACT INVENTORY (id | file_name | role | fan_in):\n" + "\n".join(
        _row(a) for a in ids
    )

    print(
        f"[AI-CLUSTER] Attempting AI-primary semantic clustering of {len(ids)} artifacts "
        f"(single LLM call; may take up to the provider timeout, retries on empty response)..."
    )
    try:
        raw = llm_client.complete(
            system_prompt=system_prompt, user_prompt=user_prompt, temperature=0.1
        )
    except Exception as e:
        print(f"[AI-CLUSTER] LLM call failed ({e}). Falling back to deterministic grouping.")
        return None
    parsed = _extract_json_array(raw or "")
    if not parsed:
        print(
            "[AI-CLUSTER] Could not parse a JSON module array. Falling back to deterministic grouping."
        )
        return None

    valid_ids = set(enriched_artifacts.keys())
    anchor_types = {"ui_anchor", "batch_anchor"}
    assigned: set = set()
    staged = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        mids = [
            str(x)
            for x in (entry.get("artifact_ids") or [])
            if str(x) in valid_ids and str(x) not in assigned
        ]
        if not mids:
            continue
        assigned.update(mids)
        staged.append((entry, mids))

    coverage = (len(assigned) / len(valid_ids)) if valid_ids else 0.0
    if len(staged) < 2 or coverage < 0.5:
        print(
            f"[AI-CLUSTER] Low-confidence result (modules={len(staged)}, coverage={coverage:.0%}). "
            f"Falling back to deterministic grouping."
        )
        return None

    modules: list[dict[str, Any]] = []
    used_ids: set = set()
    for entry, mids in staged:
        name = (str(entry.get("module_name") or "Module")).strip() or "Module"
        track = entry.get("execution_track")
        if track not in ("UI-Track", "Batch-Track", "Mixed-Track"):
            has_ui = any(
                str(enriched_artifacts.get(m, {}).get("type", "")).lower() == "ui_anchor"
                for m in mids
            )
            has_b = any(
                str(enriched_artifacts.get(m, {}).get("type", "")).lower() == "batch_anchor"
                for m in mids
            )
            track = (
                "Mixed-Track" if (has_ui and has_b) else ("Batch-Track" if has_b else "UI-Track")
            )
        entry_points = [
            m
            for m in mids
            if str(enriched_artifacts.get(m, {}).get("type", "")).lower() in anchor_types
        ] or list(mids)
        safe = re.sub(r"[^A-Z0-9]+", "-", name.upper()).strip("-")[:24] or "MODULE"
        mid = f"MOD-{safe}"
        base, k = mid, 2
        while mid in used_ids:
            mid = f"{base}-{k}"
            k += 1
        used_ids.add(mid)
        # is_shared guard (corroboration): honor the AI's flag ONLY for non-UI-Track
        # modules. A UI-Track module carrying is_shared=true is almost certainly a
        # mislabel — never let it cause a skip. (The "at most one" guard is enforced
        # below, after all modules are built.)
        is_shared = bool(entry.get("is_shared", False)) and track != "UI-Track"
        modules.append(
            {
                "module_id": mid,
                "module_name": name,
                "description": (
                    str(entry.get("business_description") or "").strip()
                    or f"Business module '{name}' (AI-identified)."
                ),
                "execution_track": track,
                "entry_points": sorted(entry_points),
                "artifacts": sorted(mids),
                "is_shared": is_shared,
                "provisional": True,
                "source_folder": "_ai",
            }
        )

    unassigned = sorted(valid_ids - assigned)
    if unassigned:
        modules.append(
            {
                "module_id": "MOD-UNGROUPED",
                "module_name": "Ungrouped Artifacts",
                "description": (
                    "Artifacts the semantic pass did not assign; routed here for "
                    "100% coverage. Flagged for human review."
                ),
                "execution_track": "Mixed-Track",
                "entry_points": unassigned,
                "artifacts": unassigned,
                "is_shared": False,  # never skip ungrouped — needs human review
                "provisional": True,
                "source_folder": "_ungrouped",
            }
        )

    # ── is_shared "at most one" safety valve ──────────────────────────────────
    # If the model flagged more than one module as shared, the signal is ambiguous
    # — clear ALL flags and fail safe toward GENERATING (never risk skipping a real
    # business module). Exactly one → keep it (Stage 5 will skip that module).
    _flagged = [m for m in modules if m.get("is_shared")]
    if len(_flagged) > 1:
        print(
            f"[AI-CLUSTER] {len(_flagged)} modules flagged is_shared "
            f"({', '.join(m['module_id'] for m in _flagged)}) — ambiguous; clearing ALL "
            f"is_shared flags (fail-safe: Stage 5 will generate for every module)."
        )
        for m in _flagged:
            m["is_shared"] = False
    elif _flagged:
        print(
            f"[AI-CLUSTER] Shared/infrastructure module flagged: {_flagged[0]['module_id']} "
            f"({_flagged[0]['execution_track']}) — Stage 5 will skip feature/story generation for it."
        )

    print(
        f"[AI-CLUSTER] Semantic clustering: {len(modules)} business module(s); "
        f"coverage 100% ({len(assigned)} assigned + {len(unassigned)} ungrouped / {len(valid_ids)})."
    )
    return modules


class ModuleClusteringUnavailableError(RuntimeError):
    """Raised when Stage-2.5 AI-semantic clustering cannot produce a trustworthy
    module split on the 'flat'/'Unknown' paths — either the LLM is unavailable/flaky
    or the clustering came back low-confidence/invalid (``generate_ai_clusters``
    returns None in both cases).

    We fail fast instead of falling back to the old crude ``unique_folders >= 3``
    folder/BFS guess: a wrong module split silently drives a large, expensive
    downstream token spend (spec + feature/story generation across every module).
    This propagates to the Celery task handler, which marks the ingestion failed.
    The LLM-free 'folder' path (option 1 — user-asserted layout) is unaffected."""
    pass


# -----------------------------------------------------------------------------
# 6. Orchestration
# -----------------------------------------------------------------------------
def build_hybrid_manifest(index_path: Path, enriched_path: Path, output_path: Path):
    """
    Stage 2.5 — Provisional Module Discovery.

    NEW FLOW (replaces the old 105-LLM-call naming loop):
    -------------------------------------------------------
    ① ProjectSizeAssessor  — compute project telemetry from enriched graph
    ② derive_module_budget — 2 × LLM calls (structural + semantic, model from config)
                             → target_count with confidence-weighted combine
    ③ Routing decision:
         unique_folders >= 3  →  generate_folder_clusters()   [Option C]
         unique_folders <  3  →  generate_bfs_clusters()      [Fallback F4]
    ④ Write module_manifest.json  (same schema as before — downstream unchanged)
       Write budget_report.json   (replaces discovery_review_notes.json for Stage 2.5)

    PRESERVED (for Stage 5 deferred semantic naming):
    --------------------------------------------------
    All legacy functions are kept intact and callable from outside:
      MVPWeightCompiler, generate_hybrid_clusters, run_generator_critic_loop,
      calculate_polyglot_score, call_llm (naming variant)
    They are simply not invoked during Stage 2.5 anymore.
    """
    print("[HYBRID] Stage 2.5 — Provisional Module Discovery starting...")

    # ------------------------------------------------------------------
    # Guard: required input files must exist
    # ------------------------------------------------------------------
    if not index_path.exists() or not enriched_path.exists():
        print(
            f"[ERROR] Required graph files missing.\n"
            f"  Index   : {index_path}\n"
            f"  Enriched: {enriched_path}"
        )
        return

    # ------------------------------------------------------------------
    # Load enriched artifact graph
    # ------------------------------------------------------------------
    try:
        with open(enriched_path, encoding="utf-8") as f:
            enr_list = json.load(f).get("artifacts", [])
        # Build id-keyed dict — canonical form used throughout this function
        enr: dict[str, Any] = {a["id"]: a for a in enr_list if "id" in a}
    except Exception as exc:
        print(f"[ERROR] Failed to read enriched graph: {exc}")
        return

    if not enr:
        print("[WARN] Enriched artifact graph is empty. Writing empty manifest.")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps({"modules": []}, indent=2), encoding="utf-8")
        return

    # ------------------------------------------------------------------
    # ① ProjectSizeAssessor — compute telemetry
    # ------------------------------------------------------------------
    # Derive paradigm from the project_config env var set by main.py,
    # or fall back to a safe default.
    paradigm = os.environ.get("RIP_SOURCE_PARADIGM", "unknown")

    print("[HYBRID] ① Computing project telemetry...")
    telemetry = ProjectSizeAssessor.assess(enr, paradigm=paradigm)
    print(
        f"[HYBRID]   Artifacts={telemetry['total_artifacts']} "
        f"Anchors={telemetry['total_anchors']} "
        f"LOC={telemetry['total_loc']} "
        f"Folders={telemetry['unique_folders']} "
        f"Paradigm={telemetry['paradigm']}"
    )

    # ------------------------------------------------------------------
    # ② + ③ Strategy-driven routing (project_config.json stage2_5.clustering_strategy)
    #     auto   — AI-primary semantic clustering, deterministic fallback (default)
    #     folder — force one-module-per-folder; SKIP all Stage-2.5 LLM calls
    #     bfs    — force lexical leading-token BFS (budget still derived)
    #     ai     — force AI-primary (same as auto)
    # ------------------------------------------------------------------
    FOLDER_STRATEGY_THRESHOLD = 3
    unique_folders = telemetry["unique_folders"]
    strategy = _read_clustering_strategy()
    print(f"[HYBRID]   clustering_strategy='{strategy}' (project_config.json stage2_5)")

    if strategy == "folder":
        # Deterministic, LLM-free path for repos already cleanly organized one
        # module per folder. No budget call, no AI call — the folders ARE the
        # modules. target_count is informational only for the audit report.
        target_count = unique_folders or telemetry["total_anchors"]
        budget = {
            "target_count": target_count,
            "offline_mode": True,
            "forced_strategy": "folder",
            "confidence": "n/a",
        }
        print(
            f"[HYBRID] ②③ FOLDER strategy forced — Stage-2.5 LLM skipped "
            f"(unique_folders={unique_folders})."
        )
        modules = generate_folder_clusters(enr, PluginRegistry())
        strategy_used = "folder"
    else:
        # AI/BFS both need the module budget.
        print("[HYBRID] ② Deriving module budget...")
        llm_client = _get_llm_client()
        budget = derive_module_budget(telemetry, llm_client)
        target_count = budget["target_count"]
        print(
            f"[HYBRID]   Budget resolved → target_count={target_count} "
            f"offline={budget['offline_mode']}"
        )

        if strategy == "bfs":
            print("[HYBRID] ③ Lexical BFS clustering forced (clustering_strategy='bfs').")
            modules = generate_bfs_clusters(enr, target_count)
            strategy_used = "bfs"
        else:
            # 'auto' / 'ai' — the paths used by "Modules not organized by folder"
            # (flat) and "Unknown". AI-primary SEMANTIC clustering is AUTHORITATIVE
            # here: it groups artifacts into business domains regardless of folder
            # layout. If it cannot produce a trustworthy grouping — the LLM is
            # unavailable/flaky, OR the result is low-confidence/invalid
            # (generate_ai_clusters returns None in BOTH cases) — we deliberately do
            # NOT fall back to a crude folder/BFS guess. A wrong module split silently
            # drives a large, expensive downstream token spend (spec + feature/story
            # generation across every module). Fail fast and abort BEFORE any of that
            # so the run can be retried, or re-submitted with an explicit layout.
            # NOTE: the LLM-free 'folder' path (option 1, user-asserted) is handled
            # above and is intentionally unaffected by this guard.
            modules = generate_ai_clusters(enr, target_count, llm_client)
            if modules:
                strategy_used = "ai-semantic"
                print(f"[HYBRID] ③ AI-semantic clustering selected ({len(modules)} module(s)).")
            else:
                raise ModuleClusteringUnavailableError(
                    "AI-semantic module clustering could not produce a trustworthy "
                    "result (the LLM was unavailable/flaky, or the clustering came back "
                    "low-confidence/invalid). Aborting Stage 2.5 BEFORE any expensive "
                    "downstream generation to avoid processing a wrong module split. "
                    "Retry once the provider is healthy, or re-submit selecting an "
                    "explicit layout ('Modules organized by folder' or 'Modules not "
                    "organized by folder')."
                )

    # ------------------------------------------------------------------
    # ④a Write module_manifest.json  (clean downstream schema)
    # ------------------------------------------------------------------
    # Strip internal audit fields before writing — downstream stages must
    # not receive 'provisional' or 'source_folder' flags.
    clean_modules = []
    for mod in modules:
        clean_modules.append(
            {
                "module_id": mod["module_id"],
                "module_name": mod["module_name"],
                "description": mod["description"],
                "execution_track": mod["execution_track"],
                "entry_points": mod["entry_points"],
                # Explicit membership list — required by slice_enriched_for_module
                # for exact-set filtering (avoids BFS inflation).
                # Internal audit fields (provisional, source_folder) are intentionally
                # excluded here; artifacts is a downstream-contract field, not audit.
                "artifacts": mod.get("artifacts", mod["entry_points"]),
                # Name-independent "shared/infrastructure" marker. Stage 5 skips
                # feature/story generation for is_shared modules (in addition to the
                # legacy stage5.skip_module_ids name list). Only the AI strategy sets
                # this today (guarded: at most one, non-UI-Track); folder/BFS default
                # False and continue to rely on the MOD-SHARED name skip.
                "is_shared": bool(mod.get("is_shared", False)),
            }
        )

    manifest_payload = {
        "_meta": {
            "stage": "2.5-provisional",
            "strategy": strategy_used,
            "total_modules": len(clean_modules),
            "total_anchors": telemetry["total_anchors"],
            "target_count": target_count,
        },
        "modules": clean_modules,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(manifest_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[HYBRID] ④ Wrote {len(clean_modules)} provisional module(s) → {output_path}")

    # ------------------------------------------------------------------
    # ④b Write budget_report.json  (full audit trail)
    # ------------------------------------------------------------------
    budget_report_path = output_path.parent / "budget_report.json"
    budget_report = {
        "_meta": {
            "stage": "2.5-provisional",
            "strategy": strategy_used,
        },
        "telemetry": telemetry,
        "budget": budget,
        "module_count": {
            "target": target_count,
            "actual": len(clean_modules),
            "delta": len(clean_modules) - target_count,
        },
    }
    budget_report_path.write_text(
        json.dumps(budget_report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[HYBRID] ④ Wrote budget audit report → {budget_report_path}")
    print(
        f"[HYBRID] Stage 2.5 complete. "
        f"Strategy={strategy_used} | "
        f"Modules={len(clean_modules)} | "
        f"Target={target_count} | "
        f"Offline={budget['offline_mode']}"
    )


# -----------------------------------------------------------------------------
# Entry Point
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Stage 2.5 — Provisional Module Discovery (Hybrid Manifest Generator)"
    )
    parser.add_argument("--index", required=True, help="Path to artifacts_index.json")
    parser.add_argument("--enriched", required=True, help="Path to artifacts_enriched.json")
    parser.add_argument("--output", required=True, help="Path to write module_manifest.json")
    args = parser.parse_args()

    build_hybrid_manifest(
        index_path=Path(args.index),
        enriched_path=Path(args.enriched),
        output_path=Path(args.output),
    )
