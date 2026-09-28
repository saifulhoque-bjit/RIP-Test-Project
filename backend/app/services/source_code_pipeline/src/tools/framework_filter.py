"""
Framework / vendor-library exclusion — keeps standard library code (e.g. the PowerBuilder Foundation Classes)
out of scanning and module derivation, so those stages see only the client's BUSINESS code.

Why: a large legacy repo can be dominated by a framework library (Shinetsu PB: ~522 of ~937 files are PFC).
That library is NOT reverse-engineered into business modules — in the target it maps to framework equivalents.
Excluding it (a) makes flattening/derivation tractable and (b) removes noise from module discovery.

Single source of truth = the per-language profile in prompts/language_profiles.yaml, key `library_excludes`
({dirs:[...], patterns:[...]}). This mirrors the existing per-language `framework_patterns` mechanism but is a
NARROW, dedicated hard-exclusion list (vendor library only), so it never removes business objects. A project can
override or extend via `source_filters` in project_config.json. A tiny built-in fallback covers the case where the
YAML is unavailable.

The filter is used by: the staging script (stage_business_source.py), Stage 1 scan (build_global_index), and
Stage 2.5 source module derivation (source_module_clustering.load_units).
"""

from __future__ import annotations

from collections.abc import Callable
import fnmatch
import json
from pathlib import Path

# source_paradigm (project_config) -> profile key in language_profiles.yaml
_PARADIGM_TO_PROFILE = {
    "pb": "PowerBuilder",
    "powerbuilder": "PowerBuilder",
    "vb6": "VB6",
    "vb": "VB6",
    "cobol": "COBOL",
    "rpg": "RPG",
}

# Built-in fallback used ONLY when the YAML profile can't be read. Mirrors the YAML library_excludes.
_BUILTIN_FALLBACK = {
    "PowerBuilder": {
        "dirs": ["pfc", "pfe"],
        "patterns": ["pfc_*", "pfe_*", "w_pfc_*", "u_pfc_*", "n_cst_*", "d_pfc*", "m_pfc*"],
    },
}

_YAML_PATH = Path(__file__).resolve().parents[2] / "prompts" / "language_profiles.yaml"


def _profile_key(paradigm: str | None) -> str | None:
    return _PARADIGM_TO_PROFILE.get((paradigm or "").lower().strip())


def _yaml_library_excludes(paradigm: str | None) -> dict[str, list]:
    key = _profile_key(paradigm)
    if not key:
        return {}
    try:
        import yaml  # PyYAML

        data = yaml.safe_load(_YAML_PATH.read_text(encoding="utf-8")) or {}
        prof = (data.get("profiles") or {}).get(key) or {}
        lib = prof.get("library_excludes") or {}
        return {"dirs": list(lib.get("dirs") or []), "patterns": list(lib.get("patterns") or [])}
    except Exception:
        return dict(_BUILTIN_FALLBACK.get(key, {}))


def load_filters(config: dict | None = None, paradigm: str | None = None) -> dict:
    """Resolve the effective exclusion filters. Precedence: YAML per-language library_excludes (unless
    source_filters.use_paradigm_defaults is false) merged with project_config source_filters overrides."""
    config = config or {}
    paradigm = paradigm or (config.get("project", {}) or {}).get("source_paradigm")
    sf = config.get("source_filters", {}) or {}
    use_defaults = sf.get("use_paradigm_defaults", True)

    dirs, globs = set(), []
    if use_defaults:
        base = _yaml_library_excludes(paradigm)
        dirs |= {str(d).lower() for d in base.get("dirs", [])}
        globs += [str(g) for g in base.get("patterns", [])]
    dirs |= {str(d).lower() for d in sf.get("exclude_dirs", [])}
    globs += [str(g) for g in sf.get("exclude_globs", [])]
    # de-dupe globs preserving order
    globs = list(dict.fromkeys(globs))
    return {
        "exclude_dirs": dirs,
        "exclude_globs": globs,
        "paradigm": paradigm,
        "active": bool(dirs or globs),
    }


def is_framework(path, filters: dict) -> bool:
    """True if `path` (a file path or bare name) is vendor-library/framework per the filters."""
    if not filters or not filters.get("active"):
        return False
    p = Path(str(path))
    if filters["exclude_dirs"] and {part.lower() for part in p.parts} & filters["exclude_dirs"]:
        return True
    name = p.name.lower()
    return any(fnmatch.fnmatch(name, g.lower()) for g in filters["exclude_globs"])


def partition(paths, filters: dict) -> tuple[list, list]:
    """Split into (business, framework)."""
    business, framework = [], []
    for p in paths:
        (framework if is_framework(p, filters) else business).append(p)
    return business, framework


def load_project_config(project_root, config_path: str | None = None) -> dict:
    p = Path(config_path) if config_path else Path(project_root) / "project_config.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def filters_for_project(project_root, config_path: str | None = None) -> dict:
    return load_filters(load_project_config(project_root, config_path))


def make_matcher(
    config: dict | None = None, paradigm: str | None = None
) -> tuple[Callable[[object], bool], dict]:
    """Return (matcher(path)->bool, filters) for the scanner. Matcher is falsy-safe when no filters apply."""
    filters = load_filters(config, paradigm)
    return (lambda path: is_framework(path, filters)), filters
