"""
Domain-Driven **macro onboarding** generator.

Turns the module derivation into a 10,000-foot, developer-facing system overview for people who do NOT know
the domain — the missing narrative layer above the per-screen SRS/feature specs. Consumes artifacts the
pipeline already produces:

  * `_global/source_modules.json`  — modules (bounded contexts) + subdomain type + member files + assignment
  * `_global/domain_glossary.json` — ubiquitous language
  * `_global/artifacts_enriched.json` — call-graph edges (→ deterministic context map) + entrypoints

The **context map, bounded contexts, subdomain classification and glossary are computed deterministically**
from the data (grounded, not invented). The LLM only writes the interpretive narrative it can't derive —
system purpose, macro cross-module workflows, external dependencies, and a "where to start" reading path —
and is injected as `llm_complete(system_prompt, user_prompt, max_tokens) -> str` for testability.

Outputs: `_global/system_onboarding.md` (+ embedded Mermaid) and `_global/context_map.mermaid`.
Everything is labelled AI-derived / pending SME validation, and every claim ties back to modules/files.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
import json
from pathlib import Path
import re

_PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts" / "domain"


def _prompt(name: str, fallback: str) -> str:
    p = _PROMPT_DIR / name
    try:
        if p.exists():
            return p.read_text(encoding="utf-8")
    except Exception:
        pass
    return fallback


SYNTH_SYSTEM = (
    "You are writing a concise, macro-level onboarding brief for a developer new to a legacy business system "
    "and its domain. You are given the already-derived bounded contexts (modules), their subdomain types, a "
    "domain glossary, a module-to-module dependency map, and the entry-point screens. Write ONLY the "
    "interpretive narrative that cannot be computed from that structure: (1) a 2–3 sentence system PURPOSE in "
    "business terms; (2) 3–6 macro PRIMARY WORKFLOWS that cross modules (end-to-end business journeys), each "
    "with ordered steps naming the modules involved; (3) likely EXTERNAL DEPENDENCIES (databases, reporting "
    "engines, third-party controls) inferred from the evidence; (4) a WHERE-TO-START reading path. If the GLOSSARY "
    "provided is empty, ALSO produce a concise glossary of the key domain terms a newcomer must know. Ground "
    "everything in the given modules/terms; do not invent modules or features. Return ONLY JSON between markers."
)

SYNTH_USER_TMPL = (
    "BOUNDED CONTEXTS (module | subdomain | description):\n%%MODULES%%\n\n"
    "GLOSSARY:\n%%GLOSSARY%%\n\n"
    "KEY DOMAIN ENTITIES (tables/objects, and the modules that touch them):\n%%ENTITIES%%\n\n"
    "MODULE DEPENDENCY MAP (caller -> callee, weight = cross-module calls):\n%%CONTEXT_MAP%%\n\n"
    "ENTRY-POINT SCREENS:\n%%ENTRYPOINTS%%\n\n"
    "OUTPUT — ONLY this JSON between the markers:\n"
    "<<<ONBOARDING>>>\n"
    '{"system_purpose": "…",\n'
    ' "primary_workflows": [ {"name": "…", "modules": ["…"], "steps": ["…"]} ],\n'
    ' "external_dependencies": ["…"],\n'
    ' "where_to_start": ["…"],\n'
    ' "glossary": [ {"term": "…", "definition": "…"} ]  // only if the provided glossary was empty\n'
    " }\n"
    "<<<ENDONBOARDING>>>"
)


def _load(root: Path, rel: str, default):
    p = root / rel
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _resolve_module_model(g: Path) -> dict | None:
    """Return a normalised module model from whichever source exists, so onboarding works for BOTH
    source-map-reduce projects AND normal folder/top-down projects:
      1. source_modules.json (derive-modules-source) — richest: subdomain, entities, glossary.
      2. module_manifest.json (top-down Stage 2.5/2.7) — modules + per-module artifacts + is_shared.
    Returns {modules, assignment, glossary, data_model, source, enriched} or None."""
    enriched = _load(g, "artifacts_enriched.json", {})
    arts = enriched if isinstance(enriched, list) else enriched.get("artifacts", [])
    loc_by = {}
    for a in arts:
        if isinstance(a, dict):
            loc_by[a.get("id") or a.get("file_name")] = (a.get("metrics") or {}).get("loc", 0)

    # Freshness guard: source_modules.json is written ONLY by derive-modules-source
    # runs; a top-down/hybrid run writes module_manifest.json and never overwrites an
    # old source_modules.json. Because _global is often reused across projects, a
    # STALE source_modules.json from a previous (different) project would otherwise be
    # preferred here and poison the onboarding with the wrong domain. So only honour
    # source_modules.json when it is at least as NEW as module_manifest.json (i.e.
    # produced by the current run); otherwise fall through to the manifest below.
    _src_p = g / "source_modules.json"
    _man_p = g / "module_manifest.json"
    _src_fresh = True
    if _src_p.exists() and _man_p.exists():
        try:
            _src_fresh = _src_p.stat().st_mtime >= _man_p.stat().st_mtime
        except OSError:
            _src_fresh = True
    if _src_fresh:
        src = _load(g, "source_modules.json", None)
    else:
        print("[DDD-ONBOARD] Ignoring stale source_modules.json (older than "
              "module_manifest.json) — using the current run's manifest instead.")
        src = None
    if src and src.get("modules") and not src.get("_placeholder"):
        gl = (_load(g, "domain_glossary.json", {}) or {}).get("glossary", [])
        if isinstance(gl, dict):
            gl = gl.get("glossary", [])
        return {
            "modules": src["modules"],
            "assignment": src.get("assignment", {}),
            "glossary": gl or [],
            "data_model": src.get("data_model", []),
            "source": "source-mapreduce",
            "enriched": enriched,
        }

    man = _load(g, "module_manifest.json", None)
    if man:
        raw = man.get("modules", man) if isinstance(man, dict) else man
        items = list(raw.values()) if isinstance(raw, dict) else raw
        modules, assignment = [], {}
        for m in items:
            if not isinstance(m, dict):
                continue
            nm = m.get("module_name") or m.get("module_id")
            if not nm:
                continue
            art_ids = m.get("artifacts") or m.get("artifact_ids") or []
            modules.append(
                {
                    "module_name": nm,
                    "description": m.get("description", ""),
                    "subdomain_type": "generic" if m.get("is_shared") else "supporting",
                    "file_ids": art_ids,
                    "file_count": len(art_ids),
                    "loc": sum(loc_by.get(x, 0) for x in art_ids),
                    "low_confidence_files": [],
                }
            )
            for x in art_ids:
                assignment.setdefault(x, nm)
        if modules:
            return {
                "modules": modules,
                "assignment": assignment,
                "glossary": [],
                "data_model": [],
                "source": "top-down-manifest",
                "enriched": enriched,
            }
    return None


def build_context_map(assignment: dict[str, str], enriched: dict) -> list[dict]:
    """Deterministic module→module edges: aggregate cross-module artifact call edges. Grounded in the graph."""
    arts = enriched if isinstance(enriched, list) else enriched.get("artifacts", [])
    weights: Counter = Counter()
    for a in arts:
        if not isinstance(a, dict):
            continue
        src = a.get("id") or a.get("file_name")
        sm = assignment.get(src)
        if not sm:
            continue
        edges = a.get("edges") or a.get("graph", {}).get("edges") or []
        for e in edges:
            tgt = (
                e
                if isinstance(e, str)
                else (
                    e.get("to") or e.get("target") or e.get("id") if isinstance(e, dict) else None
                )
            )
            tm = assignment.get(tgt)
            if tm and tm != sm:
                weights[(sm, tm)] += 1
    return [
        {"from": a, "to": b, "weight": w}
        for (a, b), w in sorted(weights.items(), key=lambda x: -x[1])
    ]


def _entrypoints(enriched: dict, assignment: dict[str, str]) -> list[dict]:
    arts = enriched if isinstance(enriched, list) else enriched.get("artifacts", [])
    eps = []
    for a in arts:
        if not isinstance(a, dict):
            continue
        sig = a.get("signals") or {}
        role = str(a.get("type", "")) + " " + str(sig)
        if "entrypoint" in role.lower() or sig.get("is_entrypoint"):
            fid = a.get("id") or a.get("file_name")
            eps.append({"file": a.get("file_name", fid), "module": assignment.get(fid, "?")})
    return eps[:20]


def _risk(
    modules: list[dict], ctx: list[dict], assignment: dict[str, str], enriched: dict
) -> dict[str, dict]:
    """Per-module risk signals: size (loc/files), coupling (in+out context edges), and legacy landmines
    (from artifacts_enriched). All grounded — no LLM."""
    coupling: Counter = Counter()
    for e in ctx:
        coupling[e["from"]] += e["weight"]
        coupling[e["to"]] += e["weight"]
    arts = enriched if isinstance(enriched, list) else enriched.get("artifacts", [])
    landmines: Counter = Counter()
    for a in arts:
        if not isinstance(a, dict):
            continue
        m = assignment.get(a.get("id") or a.get("file_name"))
        lm = a.get("landmines") or []
        if m and lm:
            landmines[m] += len(lm) if isinstance(lm, list) else 1
    out = {}
    for m in modules:
        nm = m["module_name"]
        out[nm] = {
            "loc": m.get("loc", 0),
            "files": m.get("file_count", len(m.get("file_ids", []))),
            "coupling": coupling.get(nm, 0),
            "landmines": landmines.get(nm, 0),
        }
    return out


def _mermaid(modules: list[dict], ctx: list[dict]) -> str:
    idx = {m["module_name"]: f"M{i}" for i, m in enumerate(modules)}
    lines = ["graph LR"]
    for m in modules:
        st = {"core": "core", "supporting": "supp", "generic": "gen"}.get(
            m.get("subdomain_type", "supporting"), "supp"
        )
        lines.append(f'  {idx[m["module_name"]]}["{m["module_name"]}"]:::{st}')
    for e in ctx:
        if e["from"] in idx and e["to"] in idx:
            lines.append(f"  {idx[e['from']]} -->|{e['weight']}| {idx[e['to']]}")
    lines += [
        "classDef core fill:#ffe6e6,stroke:#c00;",
        "classDef supp fill:#e6f0ff,stroke:#06c;",
        "classDef gen fill:#eee,stroke:#999;",
    ]
    return "\n".join(lines)


def _parse_onboarding(text: str) -> dict:
    text = (text or "").strip()
    m = re.search(r"<<<ONBOARDING>>>(.*?)<<<ENDONBOARDING>>>", text, re.DOTALL)
    raw = (m.group(1) if m else text).strip()
    a, b = raw.find("{"), raw.rfind("}")
    if a != -1 and b != -1:
        raw = raw[a : b + 1]
    raw = re.sub(r",(\s*[\]}])", r"\1", raw)
    return json.loads(raw, strict=False)


def _render_markdown(
    modules, glossary, ctx, narrative, data_model, risk, low_conf, generated_at
) -> str:
    core = [m for m in modules if m.get("subdomain_type") == "core"]
    supp = [m for m in modules if m.get("subdomain_type") == "supporting"]
    gen = [m for m in modules if m.get("subdomain_type") == "generic"]
    L = []
    L.append("# System Onboarding — Domain Overview (macro)\n")
    L.append(
        f"> **AI-derived from legacy source — pending SME validation.** Generated {generated_at}. "
        "Structure (contexts, dependency map, data model, glossary, risk) is computed from the code; the "
        "narrative (purpose, workflows) is interpretive. Correct as needed.\n"
    )

    L.append("## 1. What this system does\n")
    L.append((narrative.get("system_purpose") or "_(not synthesised)_") + "\n")

    L.append("## 2. Ubiquitous language (glossary)\n")
    if glossary:
        L.append("| Term | Meaning |\n|---|---|")
        L += [f"| {g.get('term', '')} | {g.get('definition', '')} |" for g in glossary]
        L.append("")
    else:
        L.append("_(no glossary derived)_\n")

    L.append("## 3. Bounded contexts (modules)\n")
    L.append("| Module | Subdomain | Files | LOC | Responsibility |\n|---|---|--:|--:|---|")
    for m in modules:
        L.append(
            f"| {m['module_name']} | {m.get('subdomain_type', '?')} | "
            f"{m.get('file_count', len(m.get('file_ids', [])))} | {m.get('loc', 0):,} | {m.get('description', '')} |"
        )
    L.append("")

    L.append("## 4. Core vs supporting vs generic\n")
    L.append(f"- **Core (business heart):** {', '.join(m['module_name'] for m in core) or '—'}")
    L.append(f"- **Supporting:** {', '.join(m['module_name'] for m in supp) or '—'}")
    L.append(f"- **Generic / shared:** {', '.join(m['module_name'] for m in gen) or '—'}\n")

    L.append("## 5. Context map (how modules interact)\n")
    L.append("```mermaid")
    L.append(_mermaid(modules, ctx))
    L.append("```\n")
    if ctx:
        L.append(
            "Strongest dependencies: "
            + "; ".join(f"{e['from']} → {e['to']} ({e['weight']})" for e in ctx[:8])
            + "\n"
        )

    L.append("## 6. Key domain entities / data model\n")
    if data_model:
        L.append("| Entity / table | Used by modules |\n|---|---|")
        for d in data_model[:40]:
            mods = d.get("used_by_modules", [])
            shared = " **(shared — integration point)**" if len(mods) > 1 else ""
            L.append(f"| {d.get('entity', '')} | {', '.join(mods)}{shared} |")
        L.append(
            "\n_Entities used by more than one module are cross-context integration points — treat their "
            "schema/ownership carefully during migration._\n"
        )
    else:
        L.append("_(no entities extracted)_\n")

    L.append("## 7. Primary workflows (macro)\n")
    for w in narrative.get("primary_workflows", []) or []:
        L.append(f"**{w.get('name', '')}** — modules: {', '.join(w.get('modules', []))}")
        L += [f"  {i}. {s}" for i, s in enumerate(w.get("steps", []), 1)]
        L.append("")
    if not narrative.get("primary_workflows"):
        L.append("_(none synthesised)_\n")

    L.append("## 8. External dependencies\n")
    L += [f"- {d}" for d in (narrative.get("external_dependencies", []) or ["_(none inferred)_"])]
    L.append("")

    L.append("## 9. Risk hotspots (where risk concentrates)\n")
    L.append("| Module | LOC | Files | Coupling | Landmines |\n|---|--:|--:|--:|--:|")
    ranked = sorted(
        modules,
        key=lambda m: -(
            risk.get(m["module_name"], {}).get("loc", 0)
            + 50 * risk.get(m["module_name"], {}).get("coupling", 0)
            + 100 * risk.get(m["module_name"], {}).get("landmines", 0)
        ),
    )
    for m in ranked:
        r = risk.get(m["module_name"], {})
        L.append(
            f"| {m['module_name']} | {r.get('loc', 0):,} | {r.get('files', 0)} | {r.get('coupling', 0)} | {r.get('landmines', 0)} |"
        )
    L.append("\n_Higher LOC + coupling + landmines = migrate with more care / earlier scrutiny._\n")

    L.append("## 10. Where to start\n")
    L += [f"- {s}" for s in (narrative.get("where_to_start", []) or ["_(not synthesised)_"])]
    L.append("")

    L.append("## 11. Open questions / low-confidence (SME review)\n")
    if low_conf:
        L.append(
            "The analyser was **low-confidence** on these files — verify their domain placement with an SME:"
        )
        L += [f"- {f}" for f in low_conf[:40]]
    else:
        L.append("_None flagged low-confidence._")
    L.append("")
    return "\n".join(L)


def generate_onboarding(
    project_root, llm_complete: Callable[[str, str, int], str], max_tokens: int = 6000
) -> dict | None:
    root = Path(project_root)
    g = root / "_global"
    model = _resolve_module_model(g)
    if not model or not model["modules"]:
        return None
    modules = model["modules"]
    assignment = model["assignment"]
    glossary = model["glossary"]
    data_model = model["data_model"]
    enriched = model["enriched"]
    module_source = model["source"]
    ctx = build_context_map(assignment, enriched)  # deterministic, grounded
    eps = _entrypoints(enriched, assignment)
    risk = _risk(modules, ctx, assignment, enriched)  # deterministic, grounded
    low_conf = sorted({f for m in modules for f in m.get("low_confidence_files", [])})
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%MZ")

    mod_lines = "\n".join(
        f"- {m['module_name']} | {m.get('subdomain_type', '?')} | {m.get('description', '')}"
        for m in modules
    )
    glo_lines = (
        "\n".join(f"- {x.get('term', '')}: {x.get('definition', '')}" for x in glossary) or "(none)"
    )
    ent_lines = (
        "\n".join(
            f"- {d['entity']} (used by: {', '.join(d.get('used_by_modules', []))})"
            for d in data_model[:40]
        )
        or "(none)"
    )
    ctx_lines = "\n".join(f"- {e['from']} -> {e['to']} ({e['weight']})" for e in ctx) or "(none)"
    ep_lines = "\n".join(f"- {e['file']} [{e['module']}]" for e in eps) or "(none)"
    user = (
        _prompt("onboarding.user.txt", SYNTH_USER_TMPL)
        .replace("%%MODULES%%", mod_lines)
        .replace("%%GLOSSARY%%", glo_lines)
        .replace("%%ENTITIES%%", ent_lines)
        .replace("%%CONTEXT_MAP%%", ctx_lines)
        .replace("%%ENTRYPOINTS%%", ep_lines)
    )
    try:
        narrative = _parse_onboarding(
            llm_complete(_prompt("onboarding.system.txt", SYNTH_SYSTEM), user, max_tokens)
        )
    except Exception:
        narrative = {}  # still emit the deterministic structure

    if not glossary and narrative.get(
        "glossary"
    ):  # top-down source has no glossary → use synthesised
        glossary = [
            {
                "term": (x.get("term") or "").strip(),
                "definition": (x.get("definition") or "").strip(),
            }
            for x in narrative["glossary"]
            if isinstance(x, dict) and x.get("term")
        ]

    (g / "context_map.mermaid").write_text(_mermaid(modules, ctx), encoding="utf-8")
    md = _render_markdown(
        modules, glossary, ctx, narrative, data_model, risk, low_conf, generated_at
    )
    (g / "system_onboarding.md").write_text(md, encoding="utf-8")
    return {
        "module_source": module_source,
        "modules": len(modules),
        "context_edges": len(ctx),
        "glossary_terms": len(glossary),
        "entities": len(data_model),
        "low_confidence": len(low_conf),
        "workflows": len(narrative.get("primary_workflows", []) or []),
        "onboarding_path": str(g / "system_onboarding.md"),
    }
