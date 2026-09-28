"""
Source-based module derivation via map-reduce (+ file names) — TOP-DOWN, decided up front.

An alternative to identifier-only Stage 2.5 clustering and to the MFU-description bottom-up path. It reads
the actual source at full fidelity in call-graph-proximity batches, extracts a per-file domain understanding
(MAP), then decides the module set + ubiquitous-language glossary over the aggregated understanding (REDUCE).
Because it is top-down (modules chosen before MFUs), the result flows through the normal pipeline with no
promotion / ID rewrite — IDs are minted under the right module from the start.

Quality guards baked in (see RIP_BOTTOMUP_MODULE_DERIVATION analyses):
  * PROXIMITY BATCHING — files are ordered by call-graph adjacency so each file is judged alongside its
    collaborators (keeps shared utilities from being mis-domained). Falls back to name/insertion order.
  * STRUCTURED MAP OUTPUT — a controlled per-file schema (not prose) so the reduce clusters consistent fields.
  * FILE NAMES as an explicit signal — strong when meaningful (frmInvoice), harmless when opaque (pat010).
  * SHARED/INFRA BUCKET + coverage — every file is placed exactly once; unplaced/shared files go to a single
    "Shared & Infrastructure" module rather than spawning singletons.
  * SUBDOMAIN CLASSIFICATION — each module tagged core / supporting / generic (feeds the DDD onboarding).

The LLM is injected as `llm_complete(system_prompt, user_prompt, max_tokens) -> str`, so the stage is
unit-testable without the provider stack.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

SHARED_MODULE = "Shared & Infrastructure"

# Prompts are externalised to prompts/domain/*.txt (tunable without code changes); the constants below are
# the embedded fallback used only when the file is missing.
_PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts" / "domain"


def _prompt(name: str, fallback: str) -> str:
    p = _PROMPT_DIR / name
    try:
        if p.exists():
            return p.read_text(encoding="utf-8")
    except Exception:
        pass
    return fallback


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

MAP_SYSTEM = (
    "You are a domain analyst reverse-engineering a legacy business system. For EACH source file in the batch, "
    "state what business capability it implements, judging by its CODE (embedded SQL/table names, business "
    "logic, UI labels, comments) and its FILE NAME. Ignore technical boilerplate. If a file is a generic "
    "utility / shared infrastructure (paging, SQL builders, encryption, base classes, splash/about screens), "
    "mark it shared. Use the domain's own vocabulary. Return ONLY JSON between the markers."
)

MAP_USER_TMPL = (
    "ALL FILES IN THE SYSTEM (names only, for context):\n%%FILE_INDEX%%\n\n"
    "BATCH TO ANALYSE (full content):\n%%BATCH%%\n\n"
    "OUTPUT — ONLY this JSON between the markers:\n"
    "<<<MAP>>>\n"
    '{"files": [\n'
    '  {"id": "<file id>", "primary_domain": "…business capability…", '
    '"key_entities": ["table/entity"], "operations": ["verb noun"], '
    '"is_shared": false, "confidence": "high|medium|low"}\n'
    "]}\n"
    "<<<ENDMAP>>>"
)

REDUCE_SYSTEM = (
    "You are a software architect grouping a legacy system into business MODULES (bounded contexts) from "
    "per-file domain notes. Discover the natural number of modules yourself (do not target a count). Give each "
    "a concise DDD-style business name, a one-line description, and classify its subdomain as 'core' (the "
    "system's competitive/business heart), 'supporting' (needed but not differentiating), or 'generic' "
    "(auth, shared utilities, cross-cutting). Also emit a ubiquitous-language GLOSSARY of the key domain terms. "
    "RULES: every file id must be placed in exactly ONE module; put shared/generic/utility files into a single "
    "'"
    + SHARED_MODULE
    + "' module rather than inventing singletons; prefer a handful of substantial modules; "
    "use only the evidence given; do not invent files. Echo each file id EXACTLY as listed below "
    "(they are lowercase, without file extension). Return ONLY JSON between the markers."
)

REDUCE_USER_TMPL = (
    "PER-FILE DOMAIN NOTES:\n%%NOTES%%\n\n"
    "CALL-GRAPH ADJACENCY (files that call each other tend to belong together):\n%%ADJACENCY%%\n\n"
    "OUTPUT — ONLY this JSON between the markers:\n"
    "<<<MODULES>>>\n"
    '{"modules": [\n'
    '  {"module_name": "…", "description": "…", "subdomain_type": "core|supporting|generic", '
    '"file_ids": ["…"]}\n'
    " ],\n"
    ' "glossary": [ {"term": "…", "definition": "…"} ]}\n'
    "<<<ENDMODULES>>>"
)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def _read_text(p: Path, cap: int = 8000) -> str:
    try:
        t = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return t if len(t) <= cap else t[:cap] + "\n…(truncated)…"


def load_units(project_root, enriched_path: str | None = None) -> list[dict]:
    """Read artifacts_enriched.json into units: {id, file_name, path, type, edges:[neighbor ids], loc}."""
    root = Path(project_root)
    ep = Path(enriched_path) if enriched_path else root / "_global" / "artifacts_enriched.json"
    data = json.loads(ep.read_text(encoding="utf-8"))
    arts = (
        data
        if isinstance(data, list)
        else data.get("artifacts", list(data.values()) if isinstance(data, dict) else [])
    )
    units = []
    for a in arts:
        if not isinstance(a, dict):
            continue
        edges = a.get("edges") or a.get("graph", {}).get("edges") or []
        neigh = []
        for e in edges:
            if isinstance(e, str):
                neigh.append(e)
            elif isinstance(e, dict):
                neigh.append(e.get("to") or e.get("target") or e.get("id") or "")
        sig = a.get("signals") or {}
        is_entry = (
            "entrypoint" in str(a.get("type", "")).lower() or bool(sig.get("is_entrypoint"))
            if isinstance(sig, dict)
            else False
        )
        units.append(
            {
                "id": a.get("id") or a.get("file_name"),
                "file_name": a.get("file_name") or a.get("id"),
                "path": a.get("path") or "",
                "type": a.get("type", ""),
                "edges": [n for n in neigh if n],
                "loc": (a.get("metrics") or {}).get("loc", 0),
                "is_entry": bool(is_entry),
            }
        )

    # Vendor-library/framework exclusion (e.g. PowerBuilder PFC): drop library files so module derivation
    # sees only business code. Safety net — also applied at scan time. No-op when no filters apply.
    try:
        from .framework_filter import filters_for_project, is_framework

        filters = filters_for_project(root)
        if filters.get("active"):
            before = len(units)
            units = [
                u
                for u in units
                if not is_framework(u.get("path") or u.get("file_name", ""), filters)
            ]
            dropped = before - len(units)
            if dropped:
                print(
                    f"[SRC-MODULES] Framework exclusion: dropped {dropped} vendor-library file(s); "
                    f"{len(units)} business unit(s) remain."
                )
    except Exception:
        pass

    return units


# ---------------------------------------------------------------------------
# Naming-quality auto-detector — decides hybrid vs source WITHOUT the user having
# to know. The two engines rely on different signals: hybrid trusts file/folder
# NAMES; source reads CODE. When names are numbered / opaque / non-latin the name
# signal is noise and hybrid mislabels, so we route to source. When names carry
# domain words, hybrid's cheaper signal is trustworthy.
# ---------------------------------------------------------------------------

# Common structural prefixes that are NOT domain words on their own (VB6/PB/COBOL).
_NAME_AFFIXES = {
    "frm",
    "cls",
    "mod",
    "bas",
    "ctl",
    "usr",
    "rpt",
    "dlg",
    "w",
    "u",
    "d",
    "n",
    "pgm",
    "cbl",
    "cpy",
    "wk",
    "ws",
    "tbl",
    "vw",
    "sp",
    "fn",
    "app",
    "sub",
}


def _name_is_meaningful(file_name: str) -> bool:
    """A file name is 'meaningful' if, after stripping the extension and common
    structural affixes, it still contains a word-like alphabetic token (>=4 latin
    letters, at least one vowel). camelCase / snake_case / kebab-case are split.
    Numbered/opaque names (pat010, WK07320, PGM0001) and non-latin-only names
    (Japanese object names) return False."""
    if not file_name:
        return False
    stem = re.sub(r"\.[A-Za-z0-9]{1,6}$", "", str(file_name))  # drop extension
    # split on non-letters AND camelCase boundaries
    parts = re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+", stem)
    for tok in parts:
        low = tok.lower()
        if low in _NAME_AFFIXES:
            continue
        if len(low) >= 4 and re.search(r"[aeiou]", low):
            return True
    return False


def assess_naming_quality(file_names: list[str], threshold: float = 0.4) -> dict:
    """Score how much the file names carry domain meaning and pick an engine.

    Returns {engine, opaque_fraction, meaningful_fraction, n_files, threshold,
    sample_opaque, sample_meaningful, reason}. engine == 'source' when the opaque
    fraction is at/above `threshold` (names can't be trusted), else 'hybrid'."""
    names = [n for n in file_names if n]
    n = len(names)
    if n == 0:
        return {
            "engine": "hybrid",
            "opaque_fraction": 0.0,
            "meaningful_fraction": 0.0,
            "n_files": 0,
            "threshold": threshold,
            "sample_opaque": [],
            "sample_meaningful": [],
            "reason": "no file names available; defaulting to hybrid",
        }
    meaningful = [x for x in names if _name_is_meaningful(x)]
    opaque = [x for x in names if x not in meaningful]
    opaque_frac = len(opaque) / n
    engine = "source" if opaque_frac >= threshold else "hybrid"
    reason = (
        f"{len(opaque)}/{n} ({opaque_frac:.0%}) of file names are numbered/opaque/non-latin, "
        f"{'>=' if engine == 'source' else '<'} the {threshold:.0%} threshold — "
        + (
            "names cannot be trusted, deriving modules from SOURCE CODE."
            if engine == "source"
            else "names carry domain meaning, using the cheaper HYBRID name/graph signal."
        )
    )
    return {
        "engine": engine,
        "opaque_fraction": round(opaque_frac, 4),
        "meaningful_fraction": round(1 - opaque_frac, 4),
        "n_files": n,
        "threshold": threshold,
        "sample_opaque": opaque[:8],
        "sample_meaningful": meaningful[:8],
        "reason": reason,
    }


def choose_engine(project_root, enriched_path: str | None = None, threshold: float = 0.4) -> dict:
    """Load the enriched artifacts and decide hybrid vs source from file naming quality.
    Never raises — on any error it falls back to hybrid with the error in `reason`."""
    try:
        units = load_units(project_root, enriched_path)
        return assess_naming_quality([u.get("file_name") for u in units], threshold)
    except Exception as e:  # pragma: no cover - defensive
        return {
            "engine": "hybrid",
            "opaque_fraction": 0.0,
            "meaningful_fraction": 0.0,
            "n_files": 0,
            "threshold": threshold,
            "sample_opaque": [],
            "sample_meaningful": [],
            "reason": f"auto-detection failed ({e}); defaulting to hybrid",
        }


def proximity_order(units: list[dict]) -> list[dict]:
    """Greedy DFS over the (undirected) call graph so neighbours are adjacent. Falls back to input order."""
    by_id = {u["id"]: u for u in units}
    adj: dict[str, set] = {u["id"]: set() for u in units}
    for u in units:
        for n in u["edges"]:
            if n in adj:
                adj[u["id"]].add(n)
                adj[n].add(u["id"])
    seen, ordered = set(), []
    for u in units:  # deterministic start order
        if u["id"] in seen:
            continue
        stack = [u["id"]]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            ordered.append(by_id[cur])
            stack.extend(sorted(adj[cur] - seen, reverse=True))
    return ordered


def make_batches(units: list[dict], project_root, max_chars: int = 60000) -> list[list[dict]]:
    """Pack proximity-ordered units into batches under a character budget (each unit carries its source)."""
    root = Path(project_root)
    # One-time basename index over input/ (avoids an expensive recursive glob per file).
    idx: dict[str, Path] = {}
    inp = root / "input"
    if inp.exists():
        for p in inp.rglob("*"):
            if p.is_file():
                idx.setdefault(p.name.lower(), p)
    batches, cur, size = [], [], 0
    for u in units:
        src = ""
        cand = (root / u["path"]) if u["path"] else None
        if cand and cand.exists():
            src = _read_text(cand)
        if not src and u.get("file_name"):
            p = idx.get(u["file_name"].lower())
            if p:
                src = _read_text(p)
        u["_src"] = src
        n = len(src) + len(u["file_name"]) + 40
        if cur and size + n > max_chars:
            batches.append(cur)
            cur, size = [], 0
        cur.append(u)
        size += n
    if cur:
        batches.append(cur)
    return batches


# ---------------------------------------------------------------------------
# MAP / REDUCE
# ---------------------------------------------------------------------------


def _parse_block(text: str, tag: str) -> dict:
    text = (text or "").strip()
    m = re.search(rf"<<<{tag}>>>(.*?)<<<END{tag}>>>", text, re.DOTALL)
    raw = (m.group(1) if m else text).strip()
    a, b = raw.find("{"), raw.rfind("}")
    if a != -1 and b != -1:
        raw = raw[a : b + 1]
    raw = re.sub(r",(\s*[\]}])", r"\1", raw)
    return json.loads(raw, strict=False)


def run_map(
    units: list[dict], batches: list[list[dict]], llm_complete, max_tokens=8000
) -> dict[str, dict]:
    """Per-file domain notes keyed by file id. Only ids present in the batch are honoured."""
    index = "\n".join(f"- {u['id']} ({u['file_name']})" for u in units)
    notes: dict[str, dict] = {}
    for batch in batches:
        valid = {u["id"] for u in batch}
        body = "\n".join(
            f"----- FILE id={u['id']} name={u['file_name']} -----\n{u.get('_src', '')}"
            for u in batch
        )
        user = (
            _prompt("module_map.user.txt", MAP_USER_TMPL)
            .replace("%%FILE_INDEX%%", index)
            .replace("%%BATCH%%", body)
        )
        try:
            obj = _parse_block(
                llm_complete(_prompt("module_map.system.txt", MAP_SYSTEM), user, max_tokens), "MAP"
            )
        except Exception:
            continue
        for f in obj.get("files", []):
            fid = f.get("id")
            if fid in valid:
                notes[fid] = {
                    "primary_domain": (f.get("primary_domain") or "").strip(),
                    "key_entities": f.get("key_entities") or [],
                    "operations": f.get("operations") or [],
                    "is_shared": bool(f.get("is_shared", False)),
                    "confidence": f.get("confidence", "medium"),
                }
    return notes


def _norm_token(s) -> str:
    """Normalise a file reference for tolerant matching: basename, lowercase, strip a known source extension."""
    s = str(s or "").strip().replace("\\", "/").split("/")[-1].lower()
    return re.sub(r"\.(cls|frm|bas|ctl|vbp|pco|cbl|cpy|sr[a-z]|md|txt|pbl|pbd)$", "", s)


def _domain_fallback(units: list[dict], notes: dict[str, dict]) -> dict:
    (
        """Deterministic safety net when the reduce LLM yields no usable modules: group files by their MAP
    primary_domain; fold singletons + shared/unknown into '"""
        + SHARED_MODULE
        + """'. Guarantees a
    multi-module result whenever MAP found diverse domains (never a single collapsed bucket)."""
    )
    from collections import defaultdict

    groups: dict[str, list] = defaultdict(list)
    for u in units:
        n = notes.get(u["id"], {})
        dom = (n.get("primary_domain") or "").strip()
        key = SHARED_MODULE if (n.get("is_shared") or not dom or dom.lower() == "shared") else dom
        groups[key].append(u["id"])
    shared = groups.pop(SHARED_MODULE, [])
    modules, assignment = [], {}
    for dom, fids in sorted(groups.items(), key=lambda x: -len(x[1])):
        if len(fids) >= 2:
            modules.append(
                {
                    "module_name": dom,
                    "description": "(grouped by extracted domain — reduce fallback)",
                    "subdomain_type": "supporting",
                    "file_ids": fids,
                }
            )
            for f in fids:
                assignment[f] = dom
        else:
            shared += fids
    if shared:
        modules.append(
            {
                "module_name": SHARED_MODULE,
                "description": "Shared/infrastructure and unclustered files.",
                "subdomain_type": "generic",
                "file_ids": shared,
            }
        )
        for f in shared:
            assignment[f] = SHARED_MODULE
    return {"modules": modules, "assignment": assignment}


def run_reduce(
    units: list[dict], notes: dict[str, dict], llm_complete, project_root=None, max_tokens=8000
) -> dict:
    """Decide the module set + glossary over the aggregated per-file notes. File ids are resolved TOLERANTLY
    (the model may echo names/casing/extensions), and a deterministic domain fallback triggers if the reduce
    yields <2 real modules — so a mismatch can never collapse everything into the shared bucket."""
    valid_ids = {u["id"] for u in units}
    resolver = {}  # normalised token -> canonical unit id
    for u in units:
        resolver.setdefault(_norm_token(u["id"]), u["id"])
        resolver.setdefault(_norm_token(u["file_name"]), u["id"])
    name = {u["id"]: u["file_name"] for u in units}
    lines = []
    for u in units:
        n = notes.get(u["id"], {})
        lines.append(
            f"- {u['id']} ({u['file_name']}): {n.get('primary_domain', '(no note)')}"
            + (
                f" | entities: {', '.join(n.get('key_entities', [])[:5])}"
                if n.get("key_entities")
                else ""
            )
            + (" | SHARED" if n.get("is_shared") else "")
        )
    adj = []
    for u in units:
        nb = [name.get(e, e) for e in u["edges"] if e in valid_ids][:6]
        if nb:
            adj.append(f"- {u['file_name']} -> {', '.join(nb)}")
    user = (
        _prompt("module_reduce.user.txt", REDUCE_USER_TMPL)
        .replace("%%NOTES%%", "\n".join(lines))
        .replace("%%ADJACENCY%%", "\n".join(adj) or "(none)")
    )
    raw = ""
    try:
        raw = llm_complete(_prompt("module_reduce.system.txt", REDUCE_SYSTEM), user, max_tokens)
        obj = _parse_block(raw, "MODULES")
    except Exception:
        obj = {}

    modules, assignment, dropped = [], {}, 0
    for m in obj.get("modules", []):
        if not isinstance(m, dict):
            continue
        nm = (m.get("module_name") or "").strip()
        rids = []
        for f in m.get("file_ids") or []:
            rid = f if f in valid_ids else resolver.get(_norm_token(f))  # tolerant resolution
            if rid and rid not in assignment:
                rids.append(rid)
            elif not rid:
                dropped += 1
        if not nm or not rids:
            continue
        modules.append(
            {
                "module_name": nm,
                "description": (m.get("description") or "").strip(),
                "subdomain_type": (m.get("subdomain_type") or "supporting").strip().lower(),
                "file_ids": rids,
            }
        )
        for f in rids:
            assignment.update({f: nm})

    glossary = [
        {"term": (g.get("term") or "").strip(), "definition": (g.get("definition") or "").strip()}
        for g in obj.get("glossary", [])
        if isinstance(g, dict) and g.get("term")
    ]

    # Safety net: if the reduce produced fewer than 2 real (non-shared) modules, fall back to domain grouping.
    non_shared = [m for m in modules if m["module_name"] != SHARED_MODULE]
    if len(non_shared) < 2:
        if project_root and raw:
            try:
                rd = Path(project_root) / "_global" / "_raw"
                rd.mkdir(parents=True, exist_ok=True)
                (rd / "source_reduce.txt").write_text(raw, encoding="utf-8")
            except Exception:
                pass
        fb = _domain_fallback(units, notes)
        modules, assignment = fb["modules"], fb["assignment"]
    else:
        # coverage: any unplaced file → Shared & Infrastructure
        unplaced = [u["id"] for u in units if u["id"] not in assignment]
        if unplaced:
            shared = next((m for m in modules if m["module_name"] == SHARED_MODULE), None)
            if not shared:
                shared = {
                    "module_name": SHARED_MODULE,
                    "description": "Shared/infrastructure and unclustered files.",
                    "subdomain_type": "generic",
                    "file_ids": [],
                }
                modules.append(shared)
            for f in unplaced:
                shared["file_ids"].append(f)
                assignment[f] = SHARED_MODULE

    return {
        "modules": modules,
        "assignment": assignment,
        "glossary": glossary,
        "reduce_dropped": dropped,
        "used_fallback": len(non_shared) < 2,
    }


def write_outputs(project_root, units, result, notes: dict[str, dict]) -> dict:
    """Persist the module decision + per-file domain notes (entities/confidence/loc) so the DDD onboarding
    can build a data model, sizing, risk and low-confidence lists without re-reading source."""
    root = Path(project_root)
    gdir = root / "_global"
    gdir.mkdir(parents=True, exist_ok=True)
    by = {u["id"]: u for u in units}
    name = {u["id"]: u["file_name"] for u in units}
    data_model: dict[str, set] = {}  # entity -> set(modules) (shared entity = integration point)
    modules_out = []
    for m in result["modules"]:
        files, entities, loc_total, low_conf = [], set(), 0, []
        for f in m["file_ids"]:
            n = notes.get(f, {})
            u = by.get(f, {})
            files.append(
                {
                    "id": f,
                    "file_name": name.get(f, f),
                    "domain": n.get("primary_domain", ""),
                    "entities": n.get("key_entities", []),
                    "confidence": n.get("confidence", "medium"),
                    "loc": u.get("loc", 0),
                }
            )
            for e in n.get("key_entities", []):
                entities.add(e)
                data_model.setdefault(e, set()).add(m["module_name"])
            loc_total += u.get("loc", 0)
            if n.get("confidence") == "low":
                low_conf.append(name.get(f, f))
        modules_out.append(
            {
                **m,
                "files": files,
                "key_entities": sorted(entities),
                "loc": loc_total,
                "file_count": len(files),
                "low_confidence_files": low_conf,
            }
        )
    src_modules = {
        "mode": "source-mapreduce",
        "modules": modules_out,
        "assignment": result["assignment"],
        "file_count": len(units),
        "data_model": [
            {"entity": e, "used_by_modules": sorted(ms)}
            for e, ms in sorted(data_model.items(), key=lambda x: (-len(x[1]), x[0]))
        ],
    }
    (gdir / "source_modules.json").write_text(
        json.dumps(src_modules, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (gdir / "domain_glossary.json").write_text(
        json.dumps({"glossary": result["glossary"]}, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return src_modules


def _slug_module_id(name: str, taken: set) -> str:
    """Derive a MOD-<SLUG> id from a business name (matches RIP's ~28-char convention), deduped."""
    base = (
        re.sub(r"[^A-Za-z0-9]+", "-", str(name or "")).strip("-").upper()[:24].strip("-")
        or "MODULE"
    )
    mid = f"MOD-{base}"
    i = 2
    while mid in taken:
        mid = f"MOD-{base[:20].strip('-')}-{i}"
        i += 1
    taken.add(mid)
    return mid


def emit_module_manifest(project_root, units: list[dict], result: dict, manifest_path) -> dict:
    """Adapter: turn the source map-reduce module decision into a schema-valid module_manifest.json that the
    existing pipeline consumes unchanged (discover_modules / per-module slice / Stage 5 / infra filter).

    Stamps the downstream-contract fields the reduce doesn't produce:
      * module_id      — MOD-<slug> (deduped)
      * execution_track — 'Mixed-Track' (neutral superset; the per-MFU track is re-derived at Stage 4)
      * entry_points   — call-graph entrypoints in the module, else all its files (mirrors hybrid behaviour)
      * artifacts      — the module's file ids (drives the per-module graph slice)
      * is_shared / module_class — ONLY the coverage 'Shared & Infrastructure' bucket is flagged
        infrastructure (so the class filter skips just it); every real module — including 'generic'
        subdomains like System Administration — stays 'business' and gets features. At most one is_shared.
    """
    by = {u["id"]: u for u in units}
    taken, out = set(), []
    for m in result["modules"]:
        is_shared = m["module_name"] == SHARED_MODULE
        fids = list(m.get("file_ids", []))
        eps = [f for f in fids if by.get(f, {}).get("is_entry")] or fids
        out.append(
            {
                "module_id": _slug_module_id(m["module_name"], taken),
                "module_name": m["module_name"],
                "description": m.get("description", ""),
                "execution_track": "Mixed-Track",
                "entry_points": eps,
                "artifacts": fids,
                "is_shared": bool(is_shared),
                "module_class": "infrastructure" if is_shared else "business",
                "subdomain_type": m.get(
                    "subdomain_type", "supporting"
                ),  # extra (allowed) — feeds DDD onboarding
            }
        )
    # safety valve (mirrors hybrid): at most one is_shared module
    flagged = [m for m in out if m["is_shared"]]
    if len(flagged) > 1:
        for m in out:
            m["is_shared"] = m["module_name"] == SHARED_MODULE
    p = Path(manifest_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"modules": out}, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "modules_written": len(out),
        "infrastructure": sum(1 for m in out if m["module_class"] == "infrastructure"),
    }


def derive_modules_from_source(
    project_root,
    llm_complete,
    enriched_path=None,
    manifest_path=None,
    max_chars=60000,
    max_tokens=8000,
) -> dict | None:
    units = load_units(project_root, enriched_path)
    if not units:
        return None
    ordered = proximity_order(units)
    batches = make_batches(ordered, project_root, max_chars=max_chars)
    notes = run_map(units, batches, llm_complete, max_tokens=max_tokens)
    result = run_reduce(
        units, notes, llm_complete, project_root=project_root, max_tokens=max_tokens
    )
    out = write_outputs(project_root, units, result, notes)
    manifest_info = None
    if manifest_path:  # pipeline mode: also emit the consumed manifest
        manifest_info = emit_module_manifest(project_root, units, result, manifest_path)
    return {
        "files": len(units),
        "batches": len(batches),
        "mapped": len(notes),
        "modules": [m["module_name"] for m in result["modules"]],
        "glossary_terms": len(result["glossary"]),
        "reduce_dropped": result.get("reduce_dropped", 0),
        "used_domain_fallback": result.get("used_fallback", False),
        "manifest_written": bool(manifest_path),
        "manifest_info": manifest_info,
    }
