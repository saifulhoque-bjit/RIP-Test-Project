"""
Stage 6 — Target Architecture Synthesis (hybrid: deterministic scaffold + concern-aware LLM generation + critic).

Produces ONE frozen, human-approved, source-language-independent target architecture that downstream code generation
consumes per feature. See RIP_TARGET_ARCHITECTURE_GENERATION_PROPOSAL.md (Option E).

Pipeline (derive_architecture):
  1. assemble_fact_base   — deterministic evidence: modules + execution tracks, cross-module edges (context map),
                            glossary, and distilled per-seed-module features/operations.
  2. build_skeleton       — deterministic backbone from project_config.json (target_stack + optional
                            target_architecture block, taken VERBATIM) + integration contracts from the context map
                            + module slicing. Any blank config field becomes an open_question.
  3. generate_derived     — LLM fills only the judgement sections (entities, codegen directives, nfrs-if-empty,
                            traceability, open_questions), constrained to the tech_allowlist. Grounded in the evidence.
  4. critic gate          — deterministic validators (schema, tech allowlist, layer acyclicity, component/layer refs,
                            traceability coverage) + LLM critic; failing runs trigger ONE bounded revision.
  5. generate_narrative   — LLM writes the human .md from the final JSON (describes, never invents).
  6. emit                 — _global/target_architecture.{json,md} + _global/_raw/architecture_*.txt.

approve_architecture freezes an immutable version; export_feature_bundle assembles the per-feature payload.

The LLM is injected as llm_complete(system_prompt, user_prompt, max_tokens)->str, so the whole stage is
unit-testable without a provider. All prompts are externalised to prompts/architecture/*.txt with embedded fallbacks.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re

SCHEMA_VERSION = "arch-1.0"
PROMPT_VERSION = "arch-1.0"

# Input budgets (chars). Generous on purpose: the reasoning models have ~1M-token context, and the critic/narrative
# MUST see the WHOLE architecture — truncating it hides tail sections (codegen_directives, security_rules) and causes
# false "missing section" findings. Only guard against pathological sizes.
_ARCH_BUDGET = 400000  # full architecture JSON passed to critic + narrative
_EVIDENCE_BUDGET = 200000  # distilled domain evidence
_SKELETON_BUDGET = 120000  # frozen skeleton passed to the derive step

_PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts" / "architecture"
_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "target_architecture.schema.json"


# --------------------------------------------------------------------------- helpers
def _prompt(name: str, fallback: str = "") -> str:
    p = _PROMPT_DIR / name
    try:
        if p.exists():
            return p.read_text(encoding="utf-8")
    except Exception:
        pass
    return fallback


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _extract_json(text: str, start: str, end: str) -> dict:
    """Tolerant extraction of a JSON object between markers (falls back to first {...} span)."""
    if not text:
        return {}
    body = text
    if start in text and end in text:
        body = text.split(start, 1)[1].split(end, 1)[0]
    else:
        i, j = body.find("{"), body.rfind("}")
        if i >= 0 and j > i:
            body = body[i : j + 1]
    try:
        return json.loads(body)
    except Exception:
        # last-ditch: strip trailing commas
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", body))
        except Exception:
            return {}


# --------------------------------------------------------------------------- loaders
def load_project_config(project_root, config_path: str | None = None) -> dict:
    root = Path(project_root)
    p = Path(config_path) if config_path else root / "project_config.json"
    return _load_json(p) or {}


def load_modules(project_root) -> list[dict]:
    g = Path(project_root) / "_global"
    data = _load_json(g / "module_manifest.json") or {}
    return data.get("modules", []) if isinstance(data, dict) else []


def parse_context_edges(project_root) -> list[dict]:
    """Cross-module call edges from context_map.mermaid: 'Ma -->|weight| Mb' with node labels 'Mn[\"Name\"]'."""
    g = Path(project_root) / "_global"
    p = g / "context_map.mermaid"
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8")
    labels = dict(re.findall(r'(\bM\d+)\["([^"]+)"\]', text))
    edges = []
    for a, w, b in re.findall(r"(\bM\d+)\s*-->\s*\|?\s*(\d+)?\s*\|?\s*(\bM\d+)", text):
        edges.append(
            {"from": labels.get(a, a), "to": labels.get(b, b), "weight": int(w) if w else None}
        )
    return edges


def load_glossary(project_root) -> list[dict]:
    g = Path(project_root) / "_global"
    d = _load_json(g / "domain_glossary.json") or {}
    if isinstance(d, dict):
        return d.get("glossary", [])
    return d if isinstance(d, list) else []


def collect_feature_evidence(
    project_root, seed_module_ids: list[str], max_features_per_module: int = 40
) -> list[dict]:
    """Walk modules/<mid>/stage4_specs/*/features_stories.json and distil features (id, title, description, ops)."""
    root = Path(project_root)
    out = []
    for mid in seed_module_ids:
        mdir = root / "modules" / mid / "stage4_specs"
        if not mdir.exists():
            continue
        feats = []
        for fs in sorted(mdir.glob("*/features_stories.json")):
            data = _load_json(fs) or {}
            for f in data.get("features", []):
                ops = [fn.get("label", "")[:120] for fn in (f.get("functions") or [])][:8]
                feats.append(
                    {
                        "id": f.get("id"),
                        "title": f.get("title"),
                        "description": (f.get("description") or "")[:300],
                        "operations": ops,
                    }
                )
            if len(feats) >= max_features_per_module:
                break
        out.append({"module_id": mid, "features": feats[:max_features_per_module]})
    return out


def _subdomain_hint(project_root) -> dict[str, str]:
    """Best-effort {module_name -> subdomain_type} from source_modules.json (names may not match manifest ids)."""
    d = _load_json(Path(project_root) / "_global" / "source_modules.json") or {}
    out = {}
    for m in d.get("modules", []) if isinstance(d, dict) else []:
        name, sd = m.get("module_name"), m.get("subdomain_type")
        if name and sd:
            out[name] = str(sd).lower()
    return out


def select_seed_modules(project_root, modules: list[dict], k: int = 5) -> dict:
    """Deterministic, coverage-based, transparent auto-selection of 3-5 representative seed modules for Stage 6.

    Guarantees online + batch + shared/infra representation (when such modules exist), prefers 'core' subdomains,
    then fills remaining slots by size (artifact count). Fully deterministic (ties break by module_id) so the frozen
    architecture is reproducible. Returns {seeds, rationale, coverage, uncovered} for provenance + human transparency.
    """
    n = len(modules)
    if n == 0:
        return {
            "seeds": [],
            "rationale": [],
            "coverage": {},
            "uncovered": ["no modules in manifest"],
        }

    subdom = _subdomain_hint(project_root)

    def track(m):
        return (m.get("execution_track") or "").lower()

    def size(m):
        return len(m.get("artifacts") or [])

    def is_core(m):
        return subdom.get(m.get("module_name"), "") == "core"

    # stable ordering for all "best of" choices
    ordered = sorted(modules, key=lambda m: (-size(m), str(m.get("module_id"))))

    picked, seen, rationale = [], set(), []

    def add(m, reason):
        mid = m.get("module_id")
        if mid and mid not in seen:
            seen.add(mid)
            picked.append(mid)
            rationale.append(
                {
                    "module_id": mid,
                    "module_name": m.get("module_name"),
                    "execution_track": m.get("execution_track"),
                    "is_shared": bool(m.get("is_shared")),
                    "artifacts": size(m),
                    "reason": reason,
                }
            )
            return True
        return False

    if n <= k:  # "3-5 as available" — few modules: use them all
        for m in ordered:
            add(m, "few modules available; all included")
    else:
        # 1. coverage picks (deterministic: best-by-size within each category)
        got = {"batch": False, "online": False, "shared": False}
        for m in ordered:
            if not got["batch"] and "batch" in track(m) and not m.get("is_shared"):
                got["batch"] = add(m, "coverage: batch/execution track")
        for m in ordered:
            if (
                not got["online"]
                and (("online" in track(m)) or ("ui" in track(m)))
                and not m.get("is_shared")
            ):
                got["online"] = add(m, "coverage: online/UI track")
        for m in ordered:
            if not got["shared"] and m.get("is_shared"):
                got["shared"] = add(m, "coverage: shared/infrastructure")
        # 2. prefer remaining 'core' subdomain modules
        for m in ordered:
            if len(picked) >= k:
                break
            if is_core(m):
                add(m, "core subdomain")
        # 3. fill remaining by size
        for m in ordered:
            if len(picked) >= k:
                break
            add(m, "representative by size")

    tracks = {track(m) for m in modules}
    coverage = {
        "online": any("online" in t or "ui" in t for t in tracks),
        "batch": any("batch" in t for t in tracks),
        "mixed": any("mixed" in t for t in tracks),
        "shared": any(m.get("is_shared") for m in modules),
        "selected": len(picked),
        "available": n,
    }
    uncovered = []
    picked_tracks = {track(next(m for m in modules if m.get("module_id") == mid)) for mid in picked}
    if coverage["batch"] and not any("batch" in t for t in picked_tracks):
        uncovered.append("a batch module exists but none was selected")
    if coverage["online"] and not any(("online" in t or "ui" in t) for t in picked_tracks):
        uncovered.append("an online/UI module exists but none was selected")
    return {
        "seeds": picked[:k],
        "rationale": rationale[:k],
        "coverage": coverage,
        "uncovered": uncovered,
    }


def suggest_seed_modules(modules: list[dict], k: int = 5, project_root=".") -> list[str]:
    """Backward-compatible thin wrapper returning just the id list."""
    return select_seed_modules(project_root, modules, k)["seeds"]


# --------------------------------------------------------------------------- fact base + skeleton
def assemble_fact_base(project_root, seed_modules: list[str], config: dict) -> dict:
    modules = load_modules(project_root)
    return {
        "modules": [
            {
                "module_id": m.get("module_id"),
                "module_name": m.get("module_name"),
                "execution_track": m.get("execution_track"),
                "is_shared": bool(m.get("is_shared")),
                "description": (m.get("description") or "")[:240],
            }
            for m in modules
        ],
        "context_edges": parse_context_edges(project_root),
        "glossary": load_glossary(project_root),
        "seed_modules": seed_modules,
        "feature_evidence": collect_feature_evidence(project_root, seed_modules),
        "modernization_manifesto": config.get("modernization_manifesto", {}),
    }


def _config_sha256(config: dict) -> str:
    return hashlib.sha256(
        json.dumps(config, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def build_skeleton(config: dict, fact_base: dict, model_name: str | None = None) -> dict:
    """Deterministic backbone. target_architecture config block is taken VERBATIM; blanks become open_questions."""
    proj = config.get("project", {}) or {}
    stack = proj.get("target_stack", {}) or {}
    ta = config.get("target_architecture", {}) or {}
    open_q: list[dict] = []
    oq_seq = [0]

    def need(field, value, impact):
        if value:
            return value
        oq_seq[0] += 1
        open_q.append(
            {
                "id": f"ARC-OQ-CFG-{oq_seq[0]:03d}",
                "question": f"target_architecture.{field} not set in project_config.json; a default was proposed.",
                "impact": impact,
            }
        )
        return value

    tech_allowlist = ta.get("tech_allowlist") or []
    if not tech_allowlist:
        # derive a minimal allowlist from the declared stack so the critic still has something to enforce
        tech_allowlist = [
            v
            for v in (
                stack.get("frontend"),
                stack.get("backend"),
                stack.get("database"),
                stack.get("batch"),
            )
            if v
        ]
        need(
            "tech_allowlist",
            None,
            "Critic cannot fully enforce anti-hallucination without an explicit allowlist.",
        )

    architecture_style = ta.get("architecture_style") or stack.get("architecture") or "layered"
    layers = ta.get("layers") or _default_layers(architecture_style)
    if not ta.get("layers"):
        need(
            "layers",
            None,
            "Layering was defaulted from architecture_style; confirm dependency rules.",
        )
    component_types = ta.get("component_types") or []
    if not component_types:
        need(
            "component_types",
            None,
            "No component-type catalog; codegen placement/naming will be generator-proposed.",
        )

    # --- Tier B: topology (monolith-first default) + system inputs (verbatim) + seeded decision ledger ---
    topology = ta.get("topology") or "modular-monolith"
    system_arch = {k: v for k, v in (ta.get("system") or {}).items() if not str(k).startswith("_")}
    ledger: list[dict] = []
    stack_arch = stack.get("architecture")
    _conf_topo = ta.get("topology")
    ledger.append(
        {
            "id": "DL-CFG-001",
            "area": "topology",
            "decision": topology,
            "rationale": (
                "set explicitly in config"
                if _conf_topo
                else "defaulted to modular-monolith (maximises autonomy for unattended generation)"
            ),
            "grounded_in": [
                "config.target_architecture.topology" if _conf_topo else "default (autonomy-first)"
            ],
            "confidence": "high" if _conf_topo else "medium",
            "reversibility": "hard",
            "escalated": False,
        }
    )
    if stack_arch and stack_arch.lower().replace(" ", "-").replace(
        "-", ""
    ) not in topology.lower().replace("-", ""):
        ledger.append(
            {
                "id": "DL-CFG-002",
                "area": "topology",
                "decision": f"refined coarse target_stack.architecture '{stack_arch}' to topology '{topology}'",
                "rationale": "autonomy-first: the simplest topology meeting requirements maximises unattended runnability.",
                "grounded_in": ["config.project.target_stack.architecture"],
                "confidence": "medium",
                "reversibility": "hard",
                "escalated": False,
            }
        )

    skeleton = {
        "schema_version": SCHEMA_VERSION,
        "provenance": {
            "generated_at": _now(),
            "project_id": proj.get("id"),
            "project_config_sha256": _config_sha256(config),
            "seed_modules": fact_base.get("seed_modules", []),
            "prompt_version": PROMPT_VERSION,
            "model": model_name,
            "status": "draft",
            "frozen_version": None,
            "approved_by": None,
            "approved_at": None,
        },
        "target_platform": {
            "frontend": stack.get("frontend"),
            "backend": stack.get("backend"),
            "batch": stack.get("batch") or (ta.get("batch_strategy") or {}).get("technology"),
            "infrastructure": stack.get("infrastructure"),
            "database": stack.get("database"),
            "build": ta.get("build"),
            "runtime": ta.get("runtime"),
            "tech_allowlist": tech_allowlist,
        },
        "architecture_style": architecture_style,
        "topology": topology,
        "system_architecture": system_arch,
        "decision_ledger": ledger,
        "layers": layers,
        "component_types": component_types,
        "cross_cutting": ta.get("cross_cutting") or [],
        "nfrs": ta.get("nfrs") or [],
        "folder_structure": ta.get("folder_structure") or {},
        "naming_conventions": ta.get("naming_conventions") or {},
        "batch_strategy": ta.get("batch_strategy") or {},
        "data_architecture": {
            "persistence": (ta.get("data") or {}).get("persistence"),
            "transaction_boundaries": (ta.get("data") or {}).get("transaction_boundaries"),
            "entities": [],
        },
        "integration_contracts": _edges_to_contracts(fact_base.get("context_edges", [])),
        "module_slicing": [
            {
                "module_id": m["module_id"],
                "module_name": m["module_name"],
                "execution_track": m["execution_track"],
                "is_shared": m["is_shared"],
            }
            for m in fact_base.get("modules", [])
        ],
        "codegen_directives": {},
        "open_questions": open_q,
        "traceability": [],
    }
    return skeleton


def _default_layers(style: str) -> list[dict]:
    return [
        {
            "id": "ARC-LYR-001",
            "name": "Presentation",
            "responsibility": "UI; no business logic.",
            "allowed_dependencies": ["ARC-LYR-002"],
        },
        {
            "id": "ARC-LYR-002",
            "name": "API",
            "responsibility": "Controllers/DTOs; auth enforcement.",
            "allowed_dependencies": ["ARC-LYR-003"],
        },
        {
            "id": "ARC-LYR-003",
            "name": "Application",
            "responsibility": "Use-case services; transactions.",
            "allowed_dependencies": ["ARC-LYR-004", "ARC-LYR-005"],
        },
        {
            "id": "ARC-LYR-004",
            "name": "Domain",
            "responsibility": "Entities/rules (framework-free).",
            "allowed_dependencies": [],
        },
        {
            "id": "ARC-LYR-005",
            "name": "Infrastructure",
            "responsibility": "Repositories/adapters.",
            "allowed_dependencies": ["ARC-LYR-004"],
        },
    ]


def _edges_to_contracts(edges: list[dict]) -> list[dict]:
    out = []
    for i, e in enumerate(edges, 1):
        out.append(
            {
                "id": f"ARC-INT-{i:03d}",
                "from_module": e.get("from"),
                "to_module": e.get("to"),
                "kind": "sync",
                "interface": "REST",
                "weight": e.get("weight"),
            }
        )
    return out


# --------------------------------------------------------------------------- LLM steps
def generate_derived(
    skeleton: dict,
    fact_base: dict,
    llm_complete: Callable[[str, str, int], str],
    critic_feedback: str = "",
    max_tokens: int = 6000,
) -> dict:
    sys_p = _prompt(
        "derive.system.txt", "You are a software architect. Return only JSON between the markers."
    )
    usr_t = _prompt(
        "derive.user.txt",
        "SKELETON:\n%%SKELETON%%\n\nEVIDENCE:\n%%EVIDENCE%%\n%%CRITIC_FEEDBACK%%\n<<<ARCH>>>{}<<<ENDARCH>>>",
    )
    usr = (
        usr_t.replace("%%SKELETON%%", json.dumps(skeleton, ensure_ascii=False)[:_SKELETON_BUDGET])
        .replace("%%EVIDENCE%%", json.dumps(fact_base, ensure_ascii=False)[:_EVIDENCE_BUDGET])
        .replace(
            "%%CRITIC_FEEDBACK%%",
            ("PREVIOUS REVIEW — fix these issues:\n" + critic_feedback) if critic_feedback else "",
        )
    )
    raw = llm_complete(sys_p, usr, max_tokens) or ""
    return {"raw": raw, "data": _extract_json(raw, "<<<ARCH>>>", "<<<ENDARCH>>>")}


def merge_generated(skeleton: dict, derived: dict) -> dict:
    arch = json.loads(json.dumps(skeleton))  # deep copy
    d = derived or {}
    da = d.get("data_architecture") or {}
    if da.get("entities"):
        arch["data_architecture"]["entities"] = da["entities"]
    for k in ("persistence", "transaction_boundaries"):
        if da.get(k) and not arch["data_architecture"].get(k):
            arch["data_architecture"][k] = da[k]
    if d.get("nfrs") and not arch.get("nfrs"):
        arch["nfrs"] = d["nfrs"]
    if d.get("codegen_directives"):
        arch["codegen_directives"] = d["codegen_directives"]
    if d.get("open_questions"):
        arch["open_questions"] = (arch.get("open_questions") or []) + d["open_questions"]
    if d.get("traceability"):
        arch["traceability"] = (arch.get("traceability") or []) + d["traceability"]
    return arch


def generate_system_view(
    skeleton: dict,
    fact_base: dict,
    llm_complete: Callable[[str, str, int], str],
    critic_feedback: str = "",
    max_tokens: int = 5000,
) -> dict:
    """Tier B (decide-not-ask): the LLM DECIDES the system/platform architecture, filling blanks left by config and
    recording each choice in the decision ledger (escalating only high-risk/irreversible ones)."""
    sys_p = _prompt(
        "system_view.system.txt",
        "You are a platform architect. Decide the system architecture. Return only JSON.",
    )
    usr_t = _prompt(
        "system_view.user.txt",
        "SKELETON:\n%%SKELETON%%\n\nEVIDENCE:\n%%EVIDENCE%%\n%%CRITIC_FEEDBACK%%\n<<<SYS>>>{}<<<ENDSYS>>>",
    )
    usr = (
        usr_t.replace("%%SKELETON%%", json.dumps(skeleton, ensure_ascii=False)[:_SKELETON_BUDGET])
        .replace("%%EVIDENCE%%", json.dumps(fact_base, ensure_ascii=False)[:_EVIDENCE_BUDGET])
        .replace(
            "%%CRITIC_FEEDBACK%%",
            ("PREVIOUS REVIEW — fix these issues:\n" + critic_feedback) if critic_feedback else "",
        )
    )
    raw = llm_complete(sys_p, usr, max_tokens) or ""
    return {"raw": raw, "data": _extract_json(raw, "<<<SYS>>>", "<<<ENDSYS>>>")}


def merge_system_view(arch: dict, sysgen: dict) -> dict:
    """Merge Tier-B output. Config-set system values WIN (verbatim); the LLM only fills blanks. Ledger entries append
    (deduped by id). Topology stays config-driven (skeleton)."""
    d = sysgen or {}
    sa = dict(arch.get("system_architecture") or {})
    for k, v in (d.get("system_architecture") or {}).items():
        cur = sa.get(k)
        if isinstance(cur, dict) and isinstance(v, dict):
            for kk, vv in v.items():
                if not cur.get(kk) and vv:
                    cur[kk] = vv
            sa[k] = cur
        elif cur in (None, "", [], {}) and v not in (None, "", [], {}):
            sa[k] = v
    arch["system_architecture"] = sa
    seen = {e.get("id") for e in arch.get("decision_ledger", [])}
    for e in d.get("decision_ledger") or []:
        if e.get("id") not in seen:
            arch.setdefault("decision_ledger", []).append(e)
            seen.add(e.get("id"))
    return arch


def generate_narrative(
    arch: dict, llm_complete: Callable[[str, str, int], str], max_tokens: int = 6000
) -> str:
    sys_p = _prompt(
        "narrative.system.txt",
        "Write the target architecture document in Markdown from the JSON. Add nothing.",
    )
    usr_t = _prompt(
        "narrative.user.txt", "FINAL ARCHITECTURE JSON:\n%%ARCH%%\n\nWrite the document now."
    )
    usr = usr_t.replace("%%ARCH%%", json.dumps(arch, ensure_ascii=False, indent=2)[:_ARCH_BUDGET])
    return llm_complete(sys_p, usr, max_tokens) or ""


def llm_critic(
    arch: dict,
    fact_base: dict,
    llm_complete: Callable[[str, str, int], str],
    max_tokens: int = 4000,
) -> dict:
    sys_p = _prompt(
        "critic.system.txt",
        "Review the architecture. Return only JSON verdict between the markers.",
    )
    usr_t = _prompt(
        "critic.user.txt",
        "ARCH:\n%%ARCH%%\n\nEVIDENCE:\n%%EVIDENCE%%\n<<<REVIEW>>>{}<<<ENDREVIEW>>>",
    )
    usr = usr_t.replace("%%ARCH%%", json.dumps(arch, ensure_ascii=False)[:_ARCH_BUDGET]).replace(
        "%%EVIDENCE%%", json.dumps(fact_base, ensure_ascii=False)[:_EVIDENCE_BUDGET]
    )
    raw = llm_complete(sys_p, usr, max_tokens) or ""
    return _extract_json(raw, "<<<REVIEW>>>", "<<<ENDREVIEW>>>") or {
        "verdict": "pass",
        "issues": [],
    }


# --------------------------------------------------------------------------- deterministic validators
def deterministic_validate(arch: dict) -> list[dict]:
    """Schema + structural checks that must NEVER be overridden by the LLM. Returns a list of issue dicts."""
    issues: list[dict] = []
    # 1. JSON schema
    schema = _load_json(_SCHEMA_PATH)
    if schema:
        try:
            from jsonschema import Draft7Validator

            for e in Draft7Validator(schema).iter_errors(arch):
                issues.append(
                    {
                        "rule": "schema",
                        "severity": "blocking",
                        "element": "/".join(str(x) for x in e.path),
                        "problem": e.message[:160],
                    }
                )
        except ImportError:
            pass  # jsonschema optional at runtime; skip gracefully

    layer_ids = {l.get("id") for l in arch.get("layers", [])} | {
        l.get("name") for l in arch.get("layers", [])
    }

    # 2. component_type.layer references a defined layer
    for c in arch.get("component_types", []):
        if c.get("layer") and c["layer"] not in layer_ids:
            issues.append(
                {
                    "rule": "consistent",
                    "severity": "blocking",
                    "element": c.get("name"),
                    "problem": f"component_type layer '{c['layer']}' is not a defined layer.",
                }
            )

    # 3. no cycle in layer allowed_dependencies
    graph = {
        l.get("id"): [d for d in (l.get("allowed_dependencies") or [])]
        for l in arch.get("layers", [])
    }
    if _has_cycle(graph):
        issues.append(
            {
                "rule": "consistent",
                "severity": "blocking",
                "element": "layers",
                "problem": "layer allowed_dependencies contain a cycle.",
            }
        )

    # 4. tech allowlist present
    if not (arch.get("target_platform") or {}).get("tech_allowlist"):
        issues.append(
            {
                "rule": "feasible",
                "severity": "blocking",
                "element": "target_platform.tech_allowlist",
                "problem": "tech_allowlist is empty; anti-hallucination cannot be enforced.",
            }
        )

    # 5. traceability coverage for generated ARC-ENT-* entities
    traced = {t.get("arc_id") for t in arch.get("traceability", [])}
    for ent in arch.get("data_architecture", {}).get("entities") or []:
        if ent.get("id") and ent["id"] not in traced:
            issues.append(
                {
                    "rule": "grounded",
                    "severity": "minor",
                    "element": ent.get("id"),
                    "problem": "entity has no traceability derived_from entry.",
                }
            )

    # 6. Tier B — the architecture must be RUNNABLE (system decisions present + consistent with topology)
    sa = arch.get("system_architecture") or {}
    topo = (arch.get("topology") or "").lower()
    identity = sa.get("identity") or {}
    if not (isinstance(identity, dict) and identity.get("issuer")):
        issues.append(
            {
                "rule": "system",
                "severity": "blocking",
                "element": "system_architecture.identity.issuer",
                "problem": "authentication ISSUANCE is undefined (an issuer/auth module that mints tokens is required, not just enforcement).",
            }
        )
    do = str(sa.get("data_ownership") or "")
    if not do:
        issues.append(
            {
                "rule": "system",
                "severity": "blocking",
                "element": "system_architecture.data_ownership",
                "problem": "data ownership is undefined (required to generate a runnable persistence layer).",
            }
        )
    elif "monolith" in topo and "per-service" in do.lower():
        issues.append(
            {
                "rule": "system",
                "severity": "blocking",
                "element": "system_architecture.data_ownership",
                "problem": f"data_ownership '{do}' contradicts modular-monolith topology (expected schema/module ownership).",
            }
        )
    if not (isinstance(sa.get("testing"), dict) and sa["testing"].get("acceptance_gate")):
        issues.append(
            {
                "rule": "system",
                "severity": "minor",
                "element": "system_architecture.testing.acceptance_gate",
                "problem": "no acceptance gate defining what 'the app runs' means.",
            }
        )
    if not sa.get("data_migration"):
        issues.append(
            {
                "rule": "system",
                "severity": "minor",
                "element": "system_architecture.data_migration",
                "problem": "no data-migration approach decided.",
            }
        )
    # high-risk decisions must be escalated in the ledger (review-by-exception)
    ledger = arch.get("decision_ledger") or []
    hi = {e.get("area") for e in ledger if e.get("escalated")}
    for area, keypath in (
        ("identity", identity),
        ("data_ownership", do),
        ("data_migration", sa.get("data_migration")),
    ):
        if keypath and area not in hi:
            issues.append(
                {
                    "rule": "system",
                    "severity": "minor",
                    "element": f"decision_ledger[{area}]",
                    "problem": f"high-risk decision '{area}' is not marked escalated=true for the human gate.",
                }
            )
    return issues


def _has_cycle(graph: dict[str, list[str]]) -> bool:
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}

    def dfs(n):
        color[n] = GRAY
        for m in graph.get(n, []):
            if m not in color:
                continue
            if color[m] == GRAY or (color[m] == WHITE and dfs(m)):
                return True
        color[n] = BLACK
        return False

    return any(color[n] == WHITE and dfs(n) for n in graph)


def _issues_text(issues: list[dict]) -> str:
    return "\n".join(
        f"- [{i.get('rule')}/{i.get('severity')}] {i.get('element')}: {i.get('problem')}"
        + (f"  FIX: {i['fix']}" if i.get("fix") else "")
        for i in issues
    )


# --------------------------------------------------------------------------- orchestration
def derive_architecture(
    project_root,
    llm_complete: Callable[[str, str, int], str],
    seed_modules: list[str] | None = None,
    config_path: str | None = None,
    max_iters: int = 2,
    out_dir: str | None = None,
    model_name: str | None = None,
) -> dict:
    root = Path(project_root)
    config = load_project_config(root, config_path)
    modules = load_modules(root)
    if seed_modules:
        seed = seed_modules
        selection = {
            "method": "manual",
            "seeds": seed,
            "rationale": [],
            "coverage": {},
            "uncovered": [],
        }
    else:
        selection = select_seed_modules(root, modules)
        selection["method"] = "auto"
        seed = selection["seeds"]
    fact_base = assemble_fact_base(root, seed, config)
    skeleton = build_skeleton(config, fact_base, model_name=model_name)
    # transparency: record how seeds were chosen + flag any architectural concern left unrepresented
    skeleton["provenance"]["seed_selection"] = selection
    for u in selection.get("uncovered", []):
        skeleton["open_questions"].append(
            {
                "id": f"ARC-OQ-SEL-{len(skeleton['open_questions']) + 1:03d}",
                "question": f"Seed-module selection did not cover: {u}. Consider overriding --arch-modules.",
                "impact": "The architecture may under-represent this concern.",
            }
        )

    raws: list[str] = []
    critic_feedback = ""
    arch = merge_generated(skeleton, {})
    review = {"verdict": "fail", "issues": []}
    for _ in range(max(1, max_iters)):
        gen = generate_derived(skeleton, fact_base, llm_complete, critic_feedback)  # Tier A
        sysgen = generate_system_view(
            skeleton, fact_base, llm_complete, critic_feedback
        )  # Tier B (decide-not-ask)
        raws.append(
            "=== TIER A (derive) ===\n"
            + gen.get("raw", "")
            + "\n\n=== TIER B (system) ===\n"
            + sysgen.get("raw", "")
        )
        arch = merge_generated(skeleton, gen.get("data"))
        arch = merge_system_view(arch, sysgen.get("data"))
        det = deterministic_validate(arch)
        review = llm_critic(arch, fact_base, llm_complete)
        blocking = [i for i in det if i.get("severity") == "blocking"] + [
            i for i in (review.get("issues") or []) if i.get("severity") == "blocking"
        ]
        if not blocking and str(review.get("verdict", "")).lower() == "pass":
            review["deterministic_issues"] = det
            break
        critic_feedback = _issues_text(det + (review.get("issues") or []))
        review["deterministic_issues"] = det

    narrative = generate_narrative(arch, llm_complete)
    paths = _emit(root, arch, narrative, raws, review, out_dir)
    ledger = arch.get("decision_ledger", [])
    return {
        "architecture": arch,
        "review": review,
        "seed_modules": seed,
        "json_path": paths["json"],
        "md_path": paths["md"],
        "ledger_path": paths["ledger"],
        "topology": arch.get("topology"),
        "decisions": len(ledger),
        "escalations": [e for e in ledger if e.get("escalated")],
        "passed": not [i for i in (review.get("issues") or []) if i.get("severity") == "blocking"]
        and not [
            i for i in review.get("deterministic_issues", []) if i.get("severity") == "blocking"
        ],
    }


def _emit(
    root: Path, arch: dict, narrative: str, raws: list[str], review: dict, out_dir: str | None
) -> dict:
    g = Path(out_dir) if out_dir else root / "_global"
    (g / "_raw").mkdir(parents=True, exist_ok=True)
    jpath = g / "target_architecture.json"
    mpath = g / "target_architecture.md"
    lpath = g / "decision_ledger.json"
    jpath.write_text(json.dumps(arch, ensure_ascii=False, indent=2), encoding="utf-8")
    mpath.write_text(
        narrative or "# Target Architecture\n\n(narrative unavailable)\n", encoding="utf-8"
    )
    lpath.write_text(
        json.dumps(arch.get("decision_ledger", []), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (g / "_raw" / "architecture_derive.txt").write_text(
        "\n\n===ITER===\n\n".join(raws), encoding="utf-8"
    )
    (g / "_raw" / "architecture_review.json").write_text(
        json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"json": str(jpath), "md": str(mpath), "ledger": str(lpath)}


# --------------------------------------------------------------------------- approval / freeze
def approve_architecture(
    project_root, approved_by: str, frozen_version: str | None = None, out_dir: str | None = None
) -> dict:
    """Freeze the current draft: stamp approval + write an immutable versioned copy codegen references."""
    g = Path(out_dir) if out_dir else Path(project_root) / "_global"
    jpath = g / "target_architecture.json"
    arch = _load_json(jpath)
    if not arch:
        raise FileNotFoundError(f"No draft architecture at {jpath}; run derive-architecture first.")
    version = frozen_version or _next_version(g)
    arch["provenance"].update(
        {
            "status": "frozen",
            "frozen_version": version,
            "approved_by": approved_by,
            "approved_at": _now(),
        }
    )
    jpath.write_text(json.dumps(arch, ensure_ascii=False, indent=2), encoding="utf-8")
    frozen = g / f"target_architecture.{version}.json"
    frozen.write_text(json.dumps(arch, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"frozen_version": version, "frozen_path": str(frozen), "approved_by": approved_by}


def _next_version(g: Path) -> str:
    existing = [p.stem.split(".")[-1] for p in g.glob("target_architecture.v*.json")]
    nums = [int(v[1:]) for v in existing if re.fullmatch(r"v\d+", v)]
    return f"v{(max(nums) + 1) if nums else 1}"


# --------------------------------------------------------------------------- per-feature export
def export_feature_bundle(project_root, feature_id: str, out_dir: str | None = None) -> dict:
    """Assemble the downstream codegen payload for ONE feature: frozen architecture + the feature's SRS +
    feature-story + related source files + its module's domain slice."""
    root = Path(project_root)
    g = root / "_global"
    arch = _load_json(g / "target_architecture.json") or {}
    if arch.get("provenance", {}).get("status") != "frozen":
        raise RuntimeError(
            "Target architecture is not frozen; approve it before exporting feature bundles."
        )

    module_prefix = feature_id.rsplit("-", 3)[0] if feature_id.count("-") >= 3 else feature_id
    story = _find_feature(root, feature_id)
    if not story:
        raise FileNotFoundError(f"Feature {feature_id} not found under any module's stage4_specs.")

    mfu_dir = story["mfu_dir"]
    srs_files = [str(p) for p in sorted(Path(mfu_dir).glob("*.md"))]
    related_source = _related_source(root, story.get("module_id"))
    glossary = load_glossary(root)

    bundle = {
        "feature_id": feature_id,
        "module_id": story.get("module_id"),
        "target_architecture": arch,
        "srs_files": srs_files,
        "feature_story": story["feature"],
        "related_source_files": related_source,
        "domain_slice": {
            "glossary": glossary,
            "module": next(
                (
                    m
                    for m in arch.get("module_slicing", [])
                    if m.get("module_id") == story.get("module_id")
                ),
                {},
            ),
        },
    }
    outp = Path(out_dir) if out_dir else g / "export"
    outp.mkdir(parents=True, exist_ok=True)
    dest = outp / f"bundle_{feature_id}.json"
    dest.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    bundle["bundle_path"] = str(dest)
    return bundle


def _find_feature(root: Path, feature_id: str) -> dict | None:
    for fs in (root / "modules").glob("*/stage4_specs/*/features_stories.json"):
        data = _load_json(fs) or {}
        for f in data.get("features", []):
            if f.get("id") == feature_id:
                return {"feature": f, "module_id": data.get("module_id"), "mfu_dir": str(fs.parent)}
    return None


def _related_source(root: Path, module_id: str | None) -> list[str]:
    if not module_id:
        return []
    detected = (
        _load_json(root / "modules" / module_id / "stage1_scanner" / "artifacts_detected.json")
        or {}
    )
    arts = detected.get("artifacts", detected) if isinstance(detected, dict) else detected
    out = []
    for a in arts if isinstance(arts, list) else []:
        p = a.get("path") if isinstance(a, dict) else None
        if p:
            out.append(p)
    return out[:200]
