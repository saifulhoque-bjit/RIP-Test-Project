"""
Stage 5 Helper Library — feature_story_helpers.py
===================================================
All standalone utility functions, constants, and the _Log class used by
FeatureStoryAgent (feature_story_agent.py).

Extracted from feature_story_agent.py to improve maintainability.
FeatureStoryAgent imports everything from this module via a wildcard import.

Contents
--------
  _Log                    Structured ReAct logging helper
  Constants               MAX_ITERATIONS, prompt/schema paths, file names,
                          technology-signal sets, SRS file patterns
  JSON helpers            _heal_json_escapes, _extract_output_json,
                          _extract_stories_json, _extract_patches,
                          _extract_critic_result
  Coverage helpers        _detect_coverage_gaps, _extract_corrections_json,
                          _merge_story_corrections, _build_corrector_prompt
  Prompt I/O              _load_prompt
  SRS / file utilities    _resolve_focal_artifact, _find_focal_srs_index,
                          _find_srs_files, _read_srs_content,
                          _extract_srs_executive_summary,
                          _build_srs_content_focal_only,
                          _build_focal_only_srs_content
  Bounding-box correction _correct_bounding_box_controls,
                          _extract_uiblueprint_executive_summary
  Scaffold builders       _build_manifest_scaffold,
                          _extract_focal_functions, _is_dispatch,
                          _build_focal_anchor_functions_block
  Resolution utilities    _load_module_context, _resolve_module_metadata,
                          _resolve_technology, _normalise_tech_tag,
                          _resolve_srs_type, _build_language_hints_block,
                          _extract_mfu_seq, _extract_srs_zone_content
  Manifest builders       _build_empty_manifest, _build_skeleton_output,
                          _make_ascii_slug, _sanitise_l2_ids
  L2 / evidence           _validate_l2_refs, _enrich_evidence_ac_ids,
                          _check_ac_gherkin_quality, _safe_str
  Prompt builders         _build_stage5a_prompt, _build_stage5b_prompt,
                          _build_critic_prompt
  Story refinement        _needs_5c_refinement
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re

from .srs_linker import (
    _S3_SKIP,  # type: ignore  # non-control header/value denylist
    _detect_srs_document_type,  # type: ignore  # used by _normalize_manifest
    _is_physical_ctrl_name,  # type: ignore  # fact-model cleanliness filter
)

# ---------------------------------------------------------------------------
# Stage 5 Language Profile Registry — optional, graceful fallback if absent
# ---------------------------------------------------------------------------
try:
    from ..scanner.stage5_language_profile import Stage5ProfileRegistry  # type: ignore

    _STAGE5_PROFILE_REGISTRY_AVAILABLE = True
except ImportError:
    _STAGE5_PROFILE_REGISTRY_AVAILABLE = False
    Stage5ProfileRegistry = None  # type: ignore


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_ITERATIONS = 1

_PIPELINE_ROOT = Path(__file__).resolve().parents[2]

STAGE5A_GENERATOR_PROMPT_PATH = _PIPELINE_ROOT / "prompts" / "05a_feature_manifest_generator.txt"
STAGE5B_GENERATOR_PROMPT_PATH = _PIPELINE_ROOT / "prompts" / "05b_story_generator.txt"
STORY_CRITIC_PROMPT_PATH = _PIPELINE_ROOT / "prompts" / "05_feature_story_critic.txt"
STAGE5C_REFINER_PROMPT_PATH = _PIPELINE_ROOT / "prompts" / "05c_evidence_refiner.txt"
STAGE5B_CORRECTOR_PROMPT_PATH = _PIPELINE_ROOT / "prompts" / "05b_story_corrector.txt"

FEATURE_MANIFEST_SCHEMA_PATH = _PIPELINE_ROOT / "schemas" / "feature_manifest.schema.json"
FEATURES_STORIES_SCHEMA_PATH = _PIPELINE_ROOT / "schemas" / "features_stories.schema.json"

MANIFEST_FILE_NAME = "feature_manifest.json"
OUTPUT_FILE_NAME = "features_stories.json"
# Note: both files are written directly into mfu_dir (no subdir).
# Neo4j exporter and other consumers must use the same flat path.

# Technology tag detection — source artifact name signals
_PB_SIGNALS = {"w_", "n_", "u_", "dw_", "cb_", "sle_", "mle_", "em_", "ddlb_", "tab_", "gbx_"}
_VB6_SIGNALS = {".frm", ".bas", ".cls", "form_", "module_", "class_"}
_COBOL_SIGNALS = {".cbl", ".cob", ".pco", "perform ", "exec sql", "working-storage"}

# SRS file name patterns Stage 4 produces (in order of preference)
SRS_FILE_PATTERNS = [
    "*UIBlueprint*.md",
    "*ui_blueprint*.md",
    "*APIContracts*.md",
    "*api_contracts*.md",
    "*BatchBlueprint*.md",
    "*batch_blueprint*.md",
    "*.md",
]


# ---------------------------------------------------------------------------
# ReAct helper: structured logging
# ---------------------------------------------------------------------------
class _Log:
    PREFIX = "[STAGE5]"

    @staticmethod
    def observe(msg: str):
        print(f"{_Log.PREFIX} [OBSERVE] {msg}")

    @staticmethod
    def reason(msg: str):
        print(f"{_Log.PREFIX} [REASON]  {msg}")

    @staticmethod
    def act(msg: str):
        print(f"{_Log.PREFIX} [ACT]     {msg}")

    @staticmethod
    def warn(msg: str):
        print(f"{_Log.PREFIX} [WARN]    {msg}")

    @staticmethod
    def err(msg: str):
        print(f"{_Log.PREFIX} [ERROR]   {msg}")

    @staticmethod
    def ok(msg: str):
        print(f"{_Log.PREFIX} [OK]      {msg}")


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------
def _heal_json_escapes(text: str) -> str:
    r"""
    Self-healing pass for invalid JSON escape sequences produced by reasoning LLMs.

    Root cause: LLMs sometimes embed Windows file paths or regex patterns inside
    JSON string values without doubling the backslash.  Examples:
      - Windows path:  "source_path": "projects\\modules\\MOD-MASTER\\stage4_specs"
                       LLM writes:    "projects\modules\MOD-MASTER\stage4_specs"
                       JSON sees:     \m, \s — invalid escapes → JSONDecodeError
      - Regex pattern: "pattern": "\w+" — \w is invalid in JSON
      - Unicode:       "\\U0001F600"    — \\\U (uppercase) is not valid JSON

    Valid JSON escape sequences: \" \\ \/ \b \f \n \r \t \\uXXXX (4 lowercase hex)
    Everything else after a backslash is invalid.

    Strategy: walk character-by-character; when a lone backslash is found before
    an invalid escape character, double it (\\X → \\\\X).  This preserves valid
    escapes and turns invalid ones into literal backslashes.

    This is intentionally conservative — it only fixes the well-known LLM patterns
    and always retries json.loads() on the original first.
    """
    result = []
    i = 0
    _valid_single = set('"\\/ bfnrt')  # chars that legally follow \ in JSON

    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt in _valid_single:
                # Valid single-char escape (\n \t \r \" \\ etc.) — keep both chars
                result.append(ch)
                result.append(nxt)
                i += 2
            elif nxt == "u" and i + 5 <= len(text):
                hex_part = text[i + 2 : i + 6]
                if len(hex_part) == 4 and all(c in "0123456789abcdefABCDEF" for c in hex_part):
                    # Valid \\uXXXX — keep as-is (5 chars: \ u X X X X)
                    result.append(ch)
                    result.append(nxt)
                    result.append(hex_part)
                    i += 6
                else:
                    # Malformed \\u — escape the backslash, keep the 'u'
                    result.append("\\\\")
                    result.append(nxt)
                    i += 2
            else:
                # Invalid escape (e.g. \m \s \w \\U \p \j) —
                # replace backslash with \\ so the char becomes a literal value
                result.append("\\\\")
                result.append(nxt)
                i += 2
        else:
            result.append(ch)
            i += 1

    return "".join(result)


def _extract_output_json(raw_text: str) -> dict | None:
    """
    Extracts JSON from <OUTPUT>...</OUTPUT> tags.
    Falls back to bare JSON parse if tags are absent.
    Applies _heal_json_escapes() on parse failure before giving up.
    """
    match = re.search(r"<OUTPUT>\s*(.*?)\s*</OUTPUT>", raw_text, re.DOTALL)
    if match:
        json_str = match.group(1).strip()
        json_str = re.sub(r"^```[a-z]*\n?", "", json_str)
        json_str = re.sub(r"\n?```$", "", json_str)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            _Log.warn(f"JSON inside <OUTPUT> is malformed: {e}")
            # Self-healing retry: fix invalid escape sequences and try again
            healed = _heal_json_escapes(json_str)
            if healed != json_str:
                try:
                    result = json.loads(healed)
                    _Log.observe("JSON self-healing succeeded (invalid escapes fixed).")
                    return result
                except json.JSONDecodeError as e2:
                    _Log.warn(f"JSON self-healing failed: {e2}")
            return None

    try:
        return json.loads(raw_text.strip())
    except json.JSONDecodeError:
        return None


def _extract_stories_json(raw_text: str) -> list | None:
    """
    Extracts the user_stories JSON array from <STORIES>...</STORIES> tags.
    Falls back to a bracket-counting array extractor when tags are absent or
    when the tagged content cannot be parsed.

    Three-tier extraction strategy:
      Tier 1 — <STORIES>…</STORIES> tagged content (primary)
      Tier 2 — _heal_json_escapes() self-healing retry on malformed JSON
      Tier 3 — Bracket-counting scan for the first '[' … ']' JSON array
                in the response, which handles large responses where the LLM
                omitted the STORIES wrapper or where the regex non-greedy
                match was defeated by nested objects.

    Returns a list (possibly empty) or None on complete parse failure.
    """

    def _try_parse_array(s: str) -> list | None:
        """Attempt JSON parse; return list or None."""
        s = re.sub(r"^```[a-z]*\n?", "", s.strip())
        s = re.sub(r"\n?```$", "", s)
        try:
            r = json.loads(s)
            return r if isinstance(r, list) else None
        except json.JSONDecodeError:
            healed = _heal_json_escapes(s)
            if healed != s:
                try:
                    r = json.loads(healed)
                    if isinstance(r, list):
                        _Log.observe("STORIES JSON self-healing succeeded.")
                        return r
                except json.JSONDecodeError:
                    pass
            return None

    # ── Tier 1: tagged content ────────────────────────────────────────────────
    match = re.search(r"<STORIES>\s*(.*?)\s*</STORIES>", raw_text, re.DOTALL)
    if match:
        result = _try_parse_array(match.group(1))
        if result is not None:
            return result
        _Log.warn(
            f"JSON inside <STORIES> tag could not be parsed "
            f"(len={len(match.group(1))}). Falling back to bracket scan."
        )

    # ── Tier 2: bracket-counting scan ────────────────────────────────────────
    # Find the FIRST '[' in the text and extract the balanced array up to its
    # matching ']'. This handles large responses (>10K chars) where the simple
    # non-greedy regex r'\[\s*\{.*?\}\s*\]' stops at the first ']' it sees
    # inside a nested object, producing a truncated and unparseable fragment.
    start = raw_text.find("[")
    if start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(raw_text)):
            ch = raw_text[i]
            if esc:
                esc = False
                continue
            if ch == "\\" and in_str:
                esc = True
                continue
            if ch == '"' and not esc:
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    candidate = raw_text[start : i + 1]
                    result = _try_parse_array(candidate)
                    if result is not None:
                        _Log.observe(
                            f"STORIES bracket-scan succeeded (extracted {len(result)} stories)."
                        )
                        return result
                    break  # balanced array found but still not valid JSON

    return None


def _extract_patches(raw_text: str) -> list | None:
    """
    Extracts the patches JSON array from <PATCHES>...</PATCHES> tags.
    Returns a list of patch dicts, or None on parse failure.
    Used by Stage 5c to apply AI evidence refinement.
    """
    m = re.search(r"<PATCHES>\s*(.*?)\s*</PATCHES>", raw_text, re.DOTALL)
    if m:
        snippet = m.group(1).strip()
        snippet = re.sub(r"^```[a-z]*\s*|\s*```$", "", snippet, flags=re.MULTILINE).strip()
        try:
            result = json.loads(snippet)
            return result if isinstance(result, list) else None
        except json.JSONDecodeError as e:
            _Log.warn(f"<PATCHES> JSON malformed: {e}")
            healed = _heal_json_escapes(snippet)
            if healed != snippet:
                try:
                    result = json.loads(healed)
                    _Log.observe("PATCHES JSON self-healing succeeded.")
                    return result if isinstance(result, list) else None
                except json.JSONDecodeError:
                    pass
    return None


def _needs_5c_refinement(story: dict) -> bool:
    """
    Returns True when a story has srs_evidence entries with low precision
    that warrant AI refinement by Stage 5c.

    Triggers on:
      highlight_type == "file_level" or "inherited"  — v1 triggers (unchanged)
      precision == "fuzzy" or "file"                 — v2 addition: catches
        fuzzy-resolved entries (last-segment literal scan) that the linker
        tagged as imprecise but did NOT mark as "file_level". Without this,
        fuzzy entries were silently passed through as "section" type, bypassing
        Stage 5c even though they need refinement.
    """
    evidence = story.get("srs_evidence", [])
    if not evidence:
        return False
    return any(
        e.get("highlight_type") in ("file_level", "inherited")
        or e.get("precision") in ("fuzzy", "file")
        for e in evidence
    )


def _extract_critic_result(raw_text: str) -> tuple[str, str, str, list]:
    """
    Returns (status, feedback, suggested_fix, failed_story_ids) from critic XML output.
    status is 'PASS' or 'FAIL'. Falls back to 'FAIL' on parse error.
    failed_story_ids is a list of story ID strings (may be empty).
    """
    status_m = re.search(r"<STATUS>\s*(PASS|FAIL)\s*</STATUS>", raw_text, re.DOTALL)
    feedback_m = re.search(r"<FEEDBACK>\s*(.*?)\s*</FEEDBACK>", raw_text, re.DOTALL)
    fix_m = re.search(r"<SUGGESTED_FIX>\s*(.*?)\s*</SUGGESTED_FIX>", raw_text, re.DOTALL)
    ids_m = re.search(r"<FAILED_STORY_IDS>\s*(\[.*?\])\s*</FAILED_STORY_IDS>", raw_text, re.DOTALL)

    status = status_m.group(1).strip() if status_m else "FAIL"
    feedback = feedback_m.group(1).strip() if feedback_m else "(no feedback parsed)"
    suggested_fix = fix_m.group(1).strip() if fix_m else ""

    failed_ids: list = []
    if ids_m:
        try:
            parsed = json.loads(ids_m.group(1))
            if isinstance(parsed, list):
                failed_ids = [str(x) for x in parsed if x]
        except (json.JSONDecodeError, ValueError):
            pass

    return status, feedback, suggested_fix, failed_ids


# ---------------------------------------------------------------------------
# Stage 5b+ Correction helpers
# ---------------------------------------------------------------------------


def _detect_coverage_gaps(feature: dict, stories: list) -> list:
    """
    Returns L2 IDs from feature.l2_sources[] that have no corresponding story.
    Checks story.l2_sources[] and every AC l2_source_ref.
    """
    covered: set = set()
    for story in stories:
        for l2 in story.get("l2_sources", []):
            covered.add(l2)
        for ac in story.get("acceptance_criteria", []):
            ref = ac.get("l2_source_ref", "")
            if ref:
                covered.add(ref)
    return [l2 for l2 in feature.get("l2_sources", []) if l2 not in covered]


def _extract_corrections_json(raw_text: str) -> dict | None:
    """
    Parse <CORRECTIONS>{...}</CORRECTIONS> from Stage 5b+ corrector response.
    Falls back to a bare JSON array wrapped in {"stories": [...]} if the LLM
    omits the outer object.
    """
    obj_m = re.search(r"<CORRECTIONS>\s*(\{.*?\})\s*</CORRECTIONS>", raw_text, re.DOTALL)
    if obj_m:
        try:
            return json.loads(obj_m.group(1))
        except json.JSONDecodeError:
            pass
    arr_m = re.search(r"<CORRECTIONS>\s*(\[.*?\])\s*</CORRECTIONS>", raw_text, re.DOTALL)
    if arr_m:
        try:
            return {"new_function_entries": [], "stories": json.loads(arr_m.group(1))}
        except json.JSONDecodeError:
            pass
    return None


def _merge_story_corrections(
    originals: list,
    corrections: list,
    remove_ids: list | None = None,
) -> list:
    """
    Merges corrected/new stories into the original list.
      - Deleted story (ID in remove_ids):  excluded from output entirely.
      - Corrected story (matching ID):     replaces the original in-place, preserving order.
      - New story (no matching ID in originals): appended at the end.
      - Passing story (not in corrections, not in remove_ids): kept unchanged.

    remove_ids is sourced from the corrector's "remove_story_ids" output key and
    represents stories the QA critic flagged for deletion (e.g., pure-navigation
    stories with no business value).
    """
    _remove: frozenset = frozenset(remove_ids or [])
    correction_map = {s.get("id", ""): s for s in corrections}
    result: list = []
    seen_ids: set = set()
    for orig in originals:
        oid = orig.get("id", "")
        if oid in _remove:
            seen_ids.add(oid)  # mark as handled — do not append
            continue
        result.append(correction_map.get(oid, orig))
        seen_ids.add(oid)
    for cid, story in correction_map.items():
        if cid not in seen_ids:
            result.append(story)
    return result


def _build_corrector_prompt(
    template: str,
    feature: dict,
    failing_stories: list,
    coverage_gap_l2ids: list,
    suggested_fix: str,
    srs_content: str,
    mfu_id: str,
    module_id: str,
    technology_tag: str,
    next_story_seq: int,
    language_hints: str = "",
) -> str:
    feature_id_prefix = feature.get("id", "UNKNOWN-F1")
    prompt = template
    prompt = prompt.replace("{FEATURE_JSON}", json.dumps(feature, ensure_ascii=False, indent=2))
    prompt = prompt.replace(
        "{FAILING_STORIES_JSON}", json.dumps(failing_stories, ensure_ascii=False, indent=2)
    )
    prompt = prompt.replace(
        "{COVERAGE_GAP_L2_IDS}", json.dumps(coverage_gap_l2ids, ensure_ascii=False)
    )
    prompt = prompt.replace("{CRITIC_SUGGESTED_FIX}", suggested_fix)
    prompt = prompt.replace("{SRS_CONTENT}", srs_content)
    prompt = prompt.replace("{MFU_ID}", mfu_id)
    prompt = prompt.replace("{MODULE_ID}", module_id)
    prompt = prompt.replace("{TECHNOLOGY_TAG}", technology_tag)
    prompt = prompt.replace("{NEXT_STORY_SEQ}", str(next_story_seq))
    prompt = prompt.replace("{FEATURE_ID_PREFIX}", feature_id_prefix)
    # {LANGUAGE_HINTS}: language-specific constraints applied during correction.
    prompt = prompt.replace("{LANGUAGE_HINTS}", language_hints)
    return prompt


# ---------------------------------------------------------------------------
# Prompt loaders
# ---------------------------------------------------------------------------
def _load_prompt(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path.absolute()}")
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# SRS file discovery — Layer 1: Focal SRS Ordering (DEFECT-2 enterprise fix)
# ---------------------------------------------------------------------------


def _is_framework_artifact(name: str, patterns) -> bool:
    """
    #20 — Language-agnostic framework/menu/ancestor classifier.

    Case-insensitive glob match of an artifact id/name against a list of framework
    patterns supplied by the active language profile (Stage5LanguageProfile.
    framework_patterns) and/or project_config overrides. Returns True when the
    artifact is a reusable framework / navigation-menu / ancestor unit, which Stage 5b
    treats with relaxed business-value gates.

    Extensibility: adding a new legacy language = add a `framework_patterns:` list to
    its profile in language_profiles.yaml. No code change here. An empty pattern list
    means nothing is classified as framework (safe default).
    """
    import fnmatch

    if not name or not patterns:
        return False
    nl = str(name).strip().lower()
    if not nl:
        return False
    for p in patterns:
        ps = str(p).strip().lower()
        if ps and fnmatch.fnmatchcase(nl, ps):
            return True
    return False


def _resolve_focal_artifact(mfu_dir: Path) -> str | None:
    """
    Reads config_naming_map.json from the MFU directory and returns the focal
    artifact name.

    Priority order (mirrors Stage 4 intent):
      1. traceability_chain[0]  — the direct traceability anchor (most precise)
      2. source_artifacts[0]    — the ordered primary source list
      3. None                   — graceful fallback; caller preserves original order

    The focal artifact is the one whose SRS document is the authoritative anchor
    for feature title, classification, and bounding_box controls derivation.
    Placing it at position 0 in the SRS list counters LLM context-position primacy
    bias: without explicit ordering the LLM would anchor on the alphabetically
    first file regardless of business relevance.
    """
    config_file = mfu_dir / "config_naming_map.json"
    if not config_file.exists():
        return None
    try:
        cfg = json.loads(config_file.read_text(encoding="utf-8"))
        # Priority 1: traceability_chain (direct focal anchor)
        tc = cfg.get("traceability_chain", [])
        if tc and isinstance(tc, list) and tc[0]:
            return str(tc[0]).strip()
        # Priority 2: source_artifacts (first = highest priority by Stage 4 convention)
        sa = cfg.get("source_artifacts", [])
        if sa and isinstance(sa, list) and sa[0]:
            return str(sa[0]).strip()
    except Exception:
        pass
    return None


def _find_focal_srs_index(srs_files: list[Path], artifact_name: str) -> int:
    """
    Returns the index of the SRS file that best matches the focal artifact name.

    Matching strategy (ordered by specificity):
      1. Exact suffix: stem ends with '_{artifact_name}' (e.g. '1_UIBlueprint_w_login' for 'w_login')
      2. Contains:     '_{artifact_name}' appears anywhere in the stem
      3. Prefix match: stem starts with '{artifact_name}_'

    Returns -1 if no match found.
    """
    artifact_lower = artifact_name.lower()
    # Strategy 1: exact suffix match
    for i, f in enumerate(srs_files):
        stem_lower = f.stem.lower()
        if stem_lower.endswith(f"_{artifact_lower}") or stem_lower == artifact_lower:
            return i
    # Strategy 2: contains match (artifact name as a delimited segment)
    for i, f in enumerate(srs_files):
        stem_lower = f.stem.lower()
        if f"_{artifact_lower}" in stem_lower:
            return i
    # Strategy 3: prefix match
    for i, f in enumerate(srs_files):
        stem_lower = f.stem.lower()
        if stem_lower.startswith(f"{artifact_lower}_"):
            return i
    return -1


def _find_srs_files(mfu_dir: Path) -> list[Path]:
    """
    Discovers SRS .md files for an MFU directory and promotes the FOCAL artifact's
    SRS to position 0 in the returned list.

    Without explicit ordering the LLM anchors on the alphabetically-first file
    due to context-position primacy bias (DEFECT-2 root cause).  This function
    reads config_naming_map.json to identify the traceability anchor and reorders
    the list so the focal SRS is always first.

    Fallback: if config_naming_map.json is absent or no match is found, the
    original alphabetical order is preserved (safe for test/standalone runs).
    """
    found: list[Path] = []
    seen: set[Path] = set()
    for pattern in SRS_FILE_PATTERNS:
        for p in sorted(mfu_dir.glob(pattern)):
            if p.is_file() and p not in seen:
                found.append(p)
                seen.add(p)

    if not found:
        return found

    # Attempt focal artifact reordering using config_naming_map.json
    focal_artifact = _resolve_focal_artifact(mfu_dir)
    if focal_artifact:
        focal_idx = _find_focal_srs_index(found, focal_artifact)
        if focal_idx > 0:
            focal_file = found.pop(focal_idx)
            found.insert(0, focal_file)
            _Log.observe(
                f"Focal SRS reordered → position 0: '{focal_file.name}' "
                f"(artifact: {focal_artifact})"
            )
        elif focal_idx == 0:
            _Log.observe(
                f"Focal SRS already at position 0: '{found[0].name}' (artifact: {focal_artifact})"
            )
        else:
            _Log.warn(
                f"Focal artifact '{focal_artifact}' not matched to any SRS file "
                f"in {mfu_dir.name} — preserving alphabetical order"
            )
    else:
        _Log.observe(
            f"No config_naming_map.json focal data for {mfu_dir.name} — "
            f"using alphabetical SRS order"
        )

    return found


def _read_srs_content(srs_files: list[Path]) -> str:
    """
    Assembles SRS document content with structural markers that guide LLM anchoring.

    The first file (position 0 after focal reordering by _find_srs_files) is
    wrapped as [FOCAL ENTRYPOINT] — the authoritative source for feature title,
    classification, controls, and S.5 events.

    All subsequent files are wrapped as [SUPPORTING CONTEXT] to signal that
    they provide supplementary detail only and must not override the focal
    document's classification decisions.

    This is Layer 1 of the DEFECT-2 enterprise fix: structural prompt signals
    that reinforce the focal ordering established by _find_srs_files().
    """
    parts = []
    for i, f in enumerate(srs_files):
        try:
            content = f.read_text(encoding="utf-8")
            if i == 0:
                parts.append(f"[FOCAL ENTRYPOINT: {f.name}]\n{content}\n[/FOCAL ENTRYPOINT]")
            else:
                parts.append(f"[SUPPORTING CONTEXT: {f.name}]\n{content}\n[/SUPPORTING CONTEXT]")
        except Exception as e:
            _Log.warn(f"Could not read SRS file {f}: {e}")
    return "\n\n".join(parts)


def _extract_srs_executive_summary(content: str, max_chars: int = 400) -> str:
    """
    Extracts the most informative short description from an SRS document for use
    in the Stage 5a condensed supporting-context summary.

    Priority order (works for UIBlueprint, APIContracts, and BatchBlueprint):
      1. Numbered items inside the Executive Forensic Summary (TIER 1.3 / 1.4)
         → e.g. "Business Mission: ..." bullet
      2. Standalone **Business Mission:** bullet
      3. Modern Endpoint Route line (API specs)
      4. step_name + description (BatchSpec JSON)
      5. First substantive non-heading line > 30 chars
    """
    # Priority 1: Executive Forensic Summary numbered items
    exec_m = re.search(
        r"##\s+1\.[34]\s+Executive Forensic Summary.*?\n(.*?)(?=\n##|\Z)",
        content,
        re.DOTALL | re.IGNORECASE,
    )
    if exec_m:
        missions = re.findall(r"\d+\.\s+\*\*[^:*]+:\*\*\s+([^\n]+)", exec_m.group(1))
        if missions:
            return " | ".join(m.strip() for m in missions[:2])[:max_chars]
        return exec_m.group(1).strip()[:max_chars]

    # Priority 2: standalone **Business Mission:** line
    bm = re.search(r"\*\*Business Mission:\*\*\s*([^\n]+)", content, re.IGNORECASE)
    if bm:
        return bm.group(1).strip()[:max_chars]

    # Priority 3: Modern Endpoint Route (API Contracts)
    ep = re.search(r"Modern Endpoint Route[:\*\s]+`?([^\n`]+)`?", content, re.IGNORECASE)
    if ep:
        return f"Endpoint: {ep.group(1).strip()}"[:max_chars]

    # Priority 4: BatchSpec step_name in JSON
    step_m = re.search(r'"step_name"\s*:\s*"([^"]+)"', content)
    if step_m:
        return f"Batch step: {step_m.group(1)}"[:max_chars]

    # Priority 5: first substantive line
    for line in content.split("\n"):
        s = line.strip()
        if s and not s.startswith("#") and not s.startswith("*") and len(s) > 30:
            return s[:max_chars]

    return content[:max_chars].replace("\n", " ")


def _build_srs_content_focal_only(srs_files: list[Path]) -> str:
    """
    Condensed SRS assembly for Stage 5a when the supporting file count exceeds
    the focal_only_threshold (default: 8).

    Motivation
    ----------
    Stage 5a only needs to classify the feature and count its functions.
    The FOCAL ENTRYPOINT (UIBlueprint / BatchBlueprint / primary APISpec) already
    contains the complete S.5 event table, S.3 controls, and business mission.
    Concatenating dozens of supporting API specs triggers reasoning-budget
    saturation in DeepSeek v4-pro: the model exhausts its 384K thinking budget
    analysing the massive context and produces a short, unparseable final answer.

    This function:
      - Includes the FOCAL ENTRYPOINT in full (always, regardless of size)
      - Replaces each SUPPORTING CONTEXT file with a ~400-char executive summary
        (business mission / endpoint route / batch step description)
      - Appends a file inventory so Stage 5a can reference correct l2_sources

    Language support
    ----------------
    Event-driven (PowerBuilder / VB6): focal = UIBlueprint; supporting = API specs.
      The UIBlueprint S.5 event table is the complete function inventory.
    Procedural / batch (COBOL): focal = BatchBlueprint; supporting = auxiliary specs.
      The BatchBlueprint job_steps list is the complete function inventory.
    In both cases the focal document is self-sufficient for Stage 5a.
    Stage 5b always receives the full SRS content for story-level detail.
    """
    if not srs_files:
        return ""

    parts = []

    # Always include FOCAL ENTRYPOINT in full
    try:
        focal_content = srs_files[0].read_text(encoding="utf-8")
        parts.append(
            f"[FOCAL ENTRYPOINT: {srs_files[0].name}]\n{focal_content}\n[/FOCAL ENTRYPOINT]"
        )
    except Exception as e:
        _Log.warn(f"Could not read focal SRS file {srs_files[0]}: {e}")

    if len(srs_files) == 1:
        return parts[0] if parts else ""

    # Supporting files: brief executive summary only
    supporting_summaries = []
    for f in srs_files[1:]:
        try:
            content = f.read_text(encoding="utf-8")
            summary = _extract_srs_executive_summary(content, max_chars=400)
            supporting_summaries.append(f"  [{f.name}]\n  {summary}")
        except Exception as e:
            supporting_summaries.append(f"  [{f.name}] (unreadable: {e})")

    if supporting_summaries:
        parts.append(
            f"[SUPPORTING CONTEXT SUMMARY — {len(srs_files) - 1} additional file(s)]\n"
            f"These files contain per-entity endpoint contracts and data schemas.\n"
            f"Full content is available in Stage 5b for story generation.\n"
            f"For Stage 5a feature classification, executive summaries are shown below.\n"
            f"Reference these file names in l2_sources and srs_section_scope as needed.\n\n"
            + "\n\n".join(supporting_summaries)
            + "\n[/SUPPORTING CONTEXT SUMMARY]"
        )

    return "\n\n".join(parts)


def _build_focal_only_srs_content(srs_files: list[Path]) -> str:
    """
    Truly focal-only SRS content: the UIBlueprint (or focal BatchSpec/APISpec) only.
    No executive summaries of supporting files.

    Used for:
    - Stage 5a primary content when files > focal_only_threshold (user's simplified 2-path approach)
    - Stage 5a fallback content on any parse/validation failure

    The UIBlueprint contains everything Stage 5a needs:
      S.1 screen identity, S.3 controls (verbatim), S.4 validation, S.5 events.
    Sending fewer tokens reduces reasoning saturation and control name hallucination.
    """
    if not srs_files:
        return ""
    try:
        content = srs_files[0].read_text(encoding="utf-8")
        return f"[FOCAL ENTRYPOINT: {srs_files[0].name}]\n{content}\n[/FOCAL ENTRYPOINT]\n"
    except Exception as e:
        _Log.warn(f"Could not read focal SRS file {srs_files[0]}: {e}")
        return ""


def _correct_bounding_box_controls(manifest: dict, mfu_dir: Path, mfu_id: str) -> dict:
    """
    Deterministic S.3 control correction pass — Gate 7c prevention.

    Removes controls from bounding_box.controls[] that do NOT appear verbatim in
    the S.3 section of the SRS, using the SRSPhysicalLinker index as the source
    of truth.  The linker already indexes every S.3 row verbatim during evidence
    hydration — this pass reuses that infrastructure at zero extra API cost.

    Why this is needed:
      When Stage 5a uses condensed or focal-only content, the LLM sometimes
      invents plausible-sounding control names (e.g., 'dw_master_list' instead
      of 'dw_1', 'cb_insert' instead of 'cb_rowins').  These invented names pass
      all structural validation checks but fail Gate 7c verbatim cross-check.

    Behaviour:
      - Hallucinated names (not in S.3 index) → removed, logged as WARN
      - Verbatim S.3 names → kept unchanged
      - s3_row_refs[] updated to match corrected controls[]
      - Infrastructure features → skipped (controls[] should be [] already)
      - If linker unavailable → manifest returned unchanged (non-blocking)

    Language-agnostic: the linker handles PowerBuilder (dw_N, cb_*), VB6
    (Command*, txtField*), and COBOL (WS-LINE-*) equally.
    """
    try:
        from .srs_linker import SRSPhysicalLinker  # type: ignore

        linker = SRSPhysicalLinker(mfu_dir)

        # Build verbatim S.3 name sets from linker index (case-insensitive lookup).
        #
        # Two dicts are maintained:
        #   s3_names       — ALL S.3 entries across every file in the MFU directory.
        #                    Used for HALLUCINATION FILTERING on non-empty controls[].
        #   focal_s3_names — S.3 entries from the FOCAL entrypoint SRS file ONLY.
        #                    Used for AUTO-INJECT when controls[] is empty.
        #
        # Why split?  Multi-screen MFUs (e.g., a main window + supporting popup +
        # address-entry window) share the same MFU directory.  The linker correctly
        # indexes ALL their S.3 rows, but the bounding_box for a given feature must
        # reference ONLY the physical controls of the focal entrypoint screen.
        # Injecting all-file S.3 entries contaminates controls[] with DataWindow
        # column names and popup buttons that belong to other screens, causing Gate 7c
        # failures.  Focal filtering prevents this while keeping hallucination removal
        # (which validates against the full index) intact.
        _focal_srs_files = _find_srs_files(mfu_dir)
        focal_filename = _focal_srs_files[0].name if _focal_srs_files else None

        # ── #33 — SRS data-object names (Gate-7c deadlock backstop) ──────────────────
        # DataWindow/DataStore object definitions (e.g. 'dw_tana_syonin') sometimes appear
        # as rows in the S.3 table, but they are the data PROVIDERS, not placed UI controls
        # — each has its own SRS artifact file ('1_APISpec_dw_tana_syonin.md'). A control
        # whose name equals one of THIS MFU's artifact basenames is therefore a data object,
        # not a control. The critic flags these under Gate 7c, but the story corrector can
        # NEVER fix the bounding box, so the correction loop deadlocks and exhausts. We drop
        # them deterministically HERE so the bounding box is clean before the first critic
        # pass — preventing the deadlock at its source rather than recovering from it.
        _srs_artifact_names = set()
        for _f in _focal_srs_files:
            _an = re.sub(r"^\d+_[A-Za-z][A-Za-z0-9]*_", "", _f.stem).strip().lower()
            if _an:
                _srs_artifact_names.add(_an)

        s3_names: dict[str, str] = {}  # all files — lowercase → verbatim
        focal_s3_names: dict[str, str] = {}  # focal file only — lowercase → verbatim

        prefix_lower = f"srs::{mfu_id.lower()}::s3::"
        for key, entry in linker.index.items():
            if key.startswith(prefix_lower):
                ts = entry.get("target_string", "")
                if ts:
                    # ── TRCE-* filter ──────────────────────────────────────────────
                    # The linker annotation-strip (Fix 6) removes "(TRCE-UI-xxx-Ln)"
                    # suffixes from control names.  If the user's srs_linker.py is an
                    # older version that skips the strip, the trace ID itself leaks into
                    # target_string (e.g. "TRCE-UI-ecbeof-L6").  Such entries must be
                    # excluded from both maps; injecting them into bounding_box.controls[]
                    # always fails Gate 7c because no LLM-generated story references a
                    # raw trace ID as a control name.
                    if ts.upper().startswith("TRCE-"):
                        _Log.observe(
                            f"S.3 index [{mfu_id}]: skipping TRCE-* entry '{ts}' "
                            f"— annotation strip artefact from older srs_linker.py."
                        )
                        continue
                    # ── Physical-control-name filter (fact-model cleanliness) ──────
                    # Some S.3 tables (e.g. menu/datawindow layouts) leak column
                    # HEADERS and CELL VALUES into the S.3 index — "Direct Shortcut",
                    # "Data Type", "DataWindow", "Logic", "Enabled Condition", etc. —
                    # which are NOT physical control names. Injecting them into
                    # bounding_box.controls[] is exactly what the critic (correctly)
                    # flags under Gate 7c. We drop them HERE so the deterministic
                    # fact model is clean *before* the critic ever sees it — the
                    # critic then agrees by construction instead of entering a
                    # FAIL→relaxed-gates loop it can never satisfy (the story
                    # corrector cannot edit the bounding box). _is_physical_ctrl_name
                    # is the same heuristic srs_linker uses, so this stays consistent
                    # and language-agnostic (identifiers with separators/digits/known
                    # shapes pass; bare prose/header words are rejected).
                    # authoritative=True: every S3:: entry in the linker index comes
                    # from the COLUMN-AUTHORITATIVE S.3 path (srs_linker._idx_ui
                    # suppresses the heuristic fallback), so these cells are physical
                    # control names by construction. This stops the GUI-biased all-caps
                    # prose heuristic from dropping legitimate COBOL/BMS field names
                    # (ACCTID, SEL0001…) — the Gate-7c/7d control-traceability deadlock.
                    # The _S3_SKIP prose-header guard is retained.
                    if not _is_physical_ctrl_name(ts, authoritative=True) or ts.lower() in _S3_SKIP:
                        _Log.observe(
                            f"S.3 index [{mfu_id}]: dropping non-control S.3 entry "
                            f"'{ts}' — not a physical control name (table header/"
                            f"value/prose). Keeps fact-model controls[] clean."
                        )
                        continue
                    # #33 — drop DataWindow/DataStore data-object names (match an SRS
                    # artifact file): these are data providers, not placed controls.
                    if ts.lower() in _srs_artifact_names:
                        _Log.observe(
                            f"S.3 index [{mfu_id}]: dropping '{ts}' — matches an SRS "
                            f"artifact name (DataWindow/DataStore data object / screen "
                            f"definition), not a placed UI control. (Gate-7c deadlock fix)"
                        )
                        continue
                    lts = ts.lower()
                    if lts not in s3_names:
                        s3_names[lts] = ts
                    # Case-insensitive comparison: on Linux the SRS linker may
                    # index filenames with different capitalisation than the
                    # focal_filename passed by the caller.
                    #
                    # Gate-7c root cause C: a control shared between the focal window and a
                    # sibling/child window is, under the linker's first-wins dedup, attributed
                    # to whichever file sorts first (often the child). Relying only on the
                    # canonical `file_name` therefore drops focal controls (e.g. cb_close,
                    # dw_data) and the bounding_box ends up missing controls the critic sees
                    # in the focal S.3 — an unfixable deadlock. The linker now records EVERY
                    # file whose S.3 lists the control in `_s3_files`; treat the control as
                    # focal if the focal file is the canonical owner OR appears in that set.
                    if focal_filename:
                        _focal_lc = focal_filename.lower()
                        _owner_lc = (entry.get("file_name") or "").lower()
                        _membership = getattr(linker, "s3_file_membership", None) or {}
                        _member_files = {(f or "").lower() for f in (_membership.get(key) or ())}
                        if _owner_lc == _focal_lc or _focal_lc in _member_files:
                            if lts not in focal_s3_names:
                                focal_s3_names[lts] = ts

        if not s3_names:
            # When the linker S.3 index is empty, any controls[] the LLM has written
            # are hallucinated — there is nothing in the SRS to verify them against.
            # This applies to ALL focal document types:
            #   UIBlueprint  — form screen; S.3 can be empty if the form has no
            #                  interactive controls, but any controls[] must then be
            #                  fabricated by the LLM.
            #   BatchSpec / APIContracts — procedural or API-only; never has an
            #                  S.3 section, so any controls[] are definitionally
            #                  hallucinated (e.g. ["txtMonth","cmdRun","lblStatus"]
            #                  for a batch form whose SRS has no S.3 table).
            # Language-agnostic: covers VB6, COBOL, RPG, PowerBuilder, etc.
            _doc_type_tag = (
                "BatchSpec/APIContracts"
                if any(
                    t in (focal_filename or "")
                    for t in ("_BatchSpec_", "_APISpec_", "_BatchBlueprint_")
                )
                else "UIBlueprint"
            )
            for feature in manifest.get("features", []):
                if feature.get("is_infrastructure"):
                    continue
                bbox = feature.get("bounding_box", {})
                if bbox.get("controls"):
                    _Log.warn(
                        f"S.3 correction [{feature.get('id', mfu_id)}]: "
                        f"S.3 index empty ({_doc_type_tag}) — "
                        f"clearing {len(bbox['controls'])} hallucinated control(s): "
                        f"{bbox['controls'][:5]}" + ("..." if len(bbox["controls"]) > 5 else "")
                    )
                    bbox["controls"] = []
                    bbox["s3_row_refs"] = []
                else:
                    _Log.observe(
                        f"S.3 correction [{feature.get('id', mfu_id)}]: "
                        f"S.3 index empty ({_doc_type_tag}) — controls[] already empty, nothing to clear."
                    )
                # Fix A — purge hallucinated SRS::S3:: refs from l2_sources[]
                # When S.3 is empty, any SRS::<mfu_id>::S3::* entries in l2_sources
                # are fabricated by the LLM and must be removed deterministically.
                # This prevents Gate 7d from re-firing because the critic sees these
                # refs and infers controls exist even after controls[] is cleared.
                _s3_prefix = f"SRS::{mfu_id}::S3::".lower()
                _orig_l2 = feature.get("l2_sources") or []
                _clean_l2 = [ls for ls in _orig_l2 if not ls.lower().startswith(_s3_prefix)]
                if len(_clean_l2) < len(_orig_l2):
                    _purged = [ls for ls in _orig_l2 if ls.lower().startswith(_s3_prefix)]
                    _Log.warn(
                        f"S.3 correction [{feature.get('id', mfu_id)}]: "
                        f"S.3 empty — purged {len(_purged)} hallucinated S3:: l2_source(s): "
                        f"{_purged[:3]}" + ("..." if len(_purged) > 3 else "")
                    )
                    feature["l2_sources"] = _clean_l2
            return manifest

        _Log.observe(
            f"S.3 correction [{mfu_id}]: {len(s3_names)} total S.3 entries across all files, "
            f"{len(focal_s3_names)} from focal '{focal_filename or 'unknown'}'."
        )

        # ── Control-less FOCAL object guard (Gate 7c) ─────────────────────────
        # The focal SRS file was IDENTIFIED but its S.3 section defines ZERO physical
        # controls — a framework ancestor (e.g. uo_anc_dw), an API-only unit, or any
        # control-less base object. Its bounding_box cannot be grounded in the focal
        # screen, so any controls the LLM emitted are ungroundable and must be cleared
        # to [] — NOT validated/retained against companion files' S.3, and NOT re-
        # injected from companion controls by the never-empty guarantee below. Both of
        # those would place foreign or fabricated controls on this feature and hard-fail
        # Gate 7c (the MOD-ANCES / APP-LABO-STOCK case). Empty controls[] is the correct,
        # critic-accepted state for a control-less object (matches the passing ancestor
        # manifest). This is DISTINCT from a focal-detection failure (focal_filename
        # unknown) — there focal_s3_names may be empty spuriously, so we preserve the
        # existing all-files fallback below rather than wiping legitimate controls.
        _focal_controlless = bool(focal_filename) and not focal_s3_names

        for feature in manifest.get("features", []):
            if feature.get("is_infrastructure"):
                continue

            if _focal_controlless:
                _bb = feature.get("bounding_box", {})
                if _bb.get("controls"):
                    _Log.warn(
                        f"S.3 correction [{feature.get('id', mfu_id)}]: focal "
                        f"'{focal_filename}' defines no S.3 controls — clearing "
                        f"{len(_bb['controls'])} ungroundable control(s): "
                        f"{_bb['controls'][:5]}" + ("..." if len(_bb["controls"]) > 5 else "")
                    )
                    _bb["controls"] = []
                    _bb["s3_row_refs"] = []
                else:
                    _Log.observe(
                        f"S.3 correction [{feature.get('id', mfu_id)}]: focal "
                        f"'{focal_filename}' has no S.3 controls — controls[] already empty."
                    )
                # Purge fabricated SRS::S3:: refs from l2_sources[] so the critic does
                # not re-infer controls (mirrors the S.3-index-empty branch above).
                _pfx = f"SRS::{mfu_id}::S3::".lower()
                _o = feature.get("l2_sources") or []
                _c = [ls for ls in _o if not ls.lower().startswith(_pfx)]
                if len(_c) < len(_o):
                    feature["l2_sources"] = _c
                    _Log.warn(
                        f"S.3 correction [{feature.get('id', mfu_id)}]: focal has no "
                        f"controls — purged {len(_o) - len(_c)} fabricated S3:: l2_source(s)."
                    )
                continue

            bbox = feature.get("bounding_box", {})
            original = bbox.get("controls", [])
            if not original:
                # ── BatchBlueprint guard ───────────────────────────────────────
                # BatchSpec/BatchBlueprint focal files have no UI S.3 section.
                # Falling back to all-files injection pulls companion CICS /
                # UIBlueprint S.3 entries into the batch feature bounding_box —
                # a batch feature must have controls[] = [] unless the focal
                # file itself defines screen controls (rare combined CICS+batch).
                # Use the canonical document-type resolver rather than ad-hoc
                # string matching — consistent with every other guard in the pipeline.
                _is_batch_focal = _detect_srs_document_type(focal_filename or "") in {
                    "BatchBlueprint",
                    "BatchSpec",
                }
                if _is_batch_focal and not focal_s3_names:
                    _Log.observe(
                        f"S.3 auto-inject [{feature.get('id', '?')}]: "
                        f"BatchBlueprint focal '{focal_filename}' has no S.3 entries — "
                        f"skipping all-files fallback, controls[] stays empty."
                    )
                    continue
                # Auto-inject: populate controls[] from the FOCAL SRS file's S.3 entries
                # only.  Using focal_s3_names prevents DataWindow column names and
                # controls from supporting/popup screens from leaking into bounding_box.
                # Falls back to all s3_names only when focal filtering yields nothing
                # (e.g., single-file MFU where focal == all, or focal detection fails).
                inject_controls = (
                    list(focal_s3_names.values()) if focal_s3_names else list(s3_names.values())
                )
                inject_source = (
                    f"focal '{focal_filename}'"
                    if focal_s3_names
                    else "all files (focal filter empty — fallback)"
                )
                if inject_controls:
                    bbox["controls"] = inject_controls
                    bbox["s3_row_refs"] = [f"SRS::{mfu_id}::S3::{c}" for c in inject_controls]
                    _Log.ok(
                        f"S.3 auto-inject [{feature.get('id', '?')}]: "
                        f"populated {len(inject_controls)} controls[] from {inject_source} "
                        f"(bounding_box.controls was empty)."
                    )
                continue

            # BUG-3 fix: validate non-empty controls[] against the FOCAL SRS file
            # only, not all SRS files in the MFU directory.  Using s3_names (all
            # files) allows controls from co-located foreign forms (e.g. frmLogin
            # controls appearing in a modmain MFU) to pass through undetected —
            # they are real S.3 controls, just from the wrong screen.
            # focal_s3_names is already built above for this exact purpose; it
            # contains only S.3 entries whose file_name == the focal SRS file.
            # Fallback to s3_names when focal_s3_names is empty (single-file MFU
            # or focal detection failure) to preserve existing behaviour.
            _filter_set = focal_s3_names if focal_s3_names else s3_names

            # ── VB6 control-array reconciliation (Gate 7c) ────────────────────
            # VB6 (and similar) control arrays appear in S.3 as indexed instances
            # — e.g. 'picLine(0)', 'picLine(1)' — but the LLM routinely emits the
            # BASE name ('picLine') in bounding_box.controls[]. A plain verbatim
            # check drops the base name as "hallucinated", the critic then FAILs
            # Gate 7c ("not present verbatim in S.3"), and the story-only corrector
            # can never repair a feature-level control name → the run exhausts
            # (observed on CardDemo/SMIAS: picLine, obPrintOp, txtEntry). Here we
            # map an unmatched base name onto the real indexed S.3 entries instead
            # of removing it. MONOTONIC: a name that previously matched still
            # matches; a name that would have been removed is now either mapped to
            # real S.3 instances or (if no indexed variant exists) still removed.
            _indexed_variants: dict = {}
            for _lk, _verb in _filter_set.items():
                _mm = re.match(r"^(.+?)\(\d+\)$", _lk)
                if _mm:
                    _indexed_variants.setdefault(_mm.group(1), []).append(_verb)

            corrected: list = []
            removed: list = []
            _did_expand = False
            _corr_seen: set = set()
            for c in original:
                cl = c.lower()
                if cl in _filter_set:
                    if c not in _corr_seen:
                        corrected.append(c)
                        _corr_seen.add(c)
                elif cl in _indexed_variants:
                    _added = [v for v in _indexed_variants[cl] if v not in _corr_seen]
                    for _v in _added:
                        corrected.append(_v)
                        _corr_seen.add(_v)
                    _did_expand = True
                    _Log.ok(
                        f"S.3 correction [{feature.get('id', '?')}]: control-array "
                        f"base name '{c}' expanded to indexed S.3 instance(s) "
                        f"{_added} (Gate 7c verbatim match)."
                    )
                else:
                    removed.append(c)

            # Never-empty guarantee (Gate 7d): if validation removes EVERY control
            # (the LLM wrote only hallucinated names) but the focal SRS S.3 DOES
            # define physical controls, re-inject the authoritative focal set instead
            # of leaving controls[] empty — an empty bounding_box on a non-infrastructure
            # feature fails Gate 7d and the story-only corrector loop cannot repair it.
            # Language-agnostic: the focal set comes verbatim from the linker S.3 index.
            if not corrected and _filter_set:
                corrected = (
                    list(focal_s3_names.values()) if focal_s3_names else list(s3_names.values())
                )
                _Log.ok(
                    f"S.3 correction [{feature.get('id', '?')}]: all {len(original)} "
                    f"LLM control(s) were hallucinated; re-injected "
                    f"{len(corrected)} authoritative S.3 control(s) from focal."
                )

            if removed or _did_expand:
                if removed:
                    _Log.warn(
                        f"S.3 correction [{feature.get('id', '?')}]: "
                        f"removed {len(removed)} hallucinated control(s): {removed}"
                    )
                _Log.ok(
                    f"S.3 correction [{feature.get('id', '?')}]: "
                    f"{len(corrected)} verbatim control(s) retained: {corrected}"
                )
                bbox["controls"] = corrected
                bbox["s3_row_refs"] = [f"SRS::{mfu_id}::S3::{c}" for c in corrected]
                # P3: purge hallucinated S.3 refs from feature.l2_sources[] too.
                # bounding_box.controls[] and l2_sources[] must stay in sync —
                # a control removed from bbox as hallucinated cannot remain as a
                # traceability claim in l2_sources. Only genuine REMOVALS are purged;
                # control-array base names that were EXPANDED (picLine → picLine(0),
                # picLine(1)) are additionally re-injected into l2_sources so the
                # feature-level traceability matches the corrected bounding box.
                # Language-agnostic: compares normalised ref strings regardless of technology.
                removed_lower = {c.lower() for c in removed}
                original_l2 = feature.get("l2_sources", [])
                filtered_l2 = [
                    ref
                    for ref in original_l2
                    if not (
                        ref.upper().startswith(f"SRS::{mfu_id.upper()}::S3::")
                        and ref.split("::")[-1].lower() in removed_lower
                    )
                ]
                if len(filtered_l2) < len(original_l2):
                    n_purged = len(original_l2) - len(filtered_l2)
                    feature["l2_sources"] = filtered_l2
                    _Log.observe(
                        f"S.3 correction [{feature.get('id', '?')}]: "
                        f"purged {n_purged} hallucinated S.3 ref(s) from l2_sources[]."
                    )
                if _did_expand:
                    # Ensure every corrected (indexed) control has a matching
                    # feature-level l2_source so Gate 1c/7c stay consistent; drop
                    # any now-orphaned base-name S3 ref that was expanded away.
                    _cur_l2 = feature.get("l2_sources", [])
                    _corr_lower = {c.lower() for c in corrected}
                    _pruned_l2 = [
                        ref
                        for ref in _cur_l2
                        if not (
                            ref.upper().startswith(f"SRS::{mfu_id.upper()}::S3::")
                            and ref.split("::")[-1].lower() not in _corr_lower
                        )
                    ]
                    _existing = {r for r in _pruned_l2}
                    for c in corrected:
                        _ref = f"SRS::{mfu_id}::S3::{c}"
                        if _ref not in _existing:
                            _pruned_l2.append(_ref)
                            _existing.add(_ref)
                    feature["l2_sources"] = _pruned_l2
            else:
                _Log.observe(
                    f"S.3 correction [{feature.get('id', '?')}]: "
                    f"all {len(corrected)} controls verbatim — no changes needed."
                )

    except ImportError:
        # Review-fix A4: do not swallow silently. Controls were NOT verified against the
        # SRS S.3, so hallucinated controls could survive. Log loudly and mark the manifest
        # so the downstream relaxed-gates carve-out refuses to relax controls gates.
        _Log.err(
            "S.3 correction: SRSPhysicalLinker not available — bounding_box controls were "
            "NOT verified against the SRS S.3 for this MFU; controls gates must NOT be relaxed."
        )
        manifest.setdefault("generation_metadata", {})["s3_correction_error"] = "linker_unavailable"
    except Exception as e:
        _Log.err(
            f"S.3 correction [{mfu_id}] FAILED — bounding_box controls were NOT verified "
            f"against the SRS S.3; hallucinated controls may survive. Controls gates must "
            f"NOT be relaxed downstream. Error: {e}"
        )
        manifest.setdefault("generation_metadata", {})["s3_correction_error"] = str(e)[:300]

    return manifest


def _extract_uiblueprint_executive_summary(srs_files: list[Path], max_chars: int = 3000) -> str:
    """
    Extracts the TIER 1 executive summary from the focal UIBlueprint — the most
    information-dense section in the fewest characters.
    Returns up to max_chars of content starting from the file beginning through
    the end of TIER 1 (before TIER 2 begins).
    Used to keep the LLM enrichment call tiny (~3K chars input vs 43K full file).
    """
    if not srs_files:
        return ""
    try:
        content = srs_files[0].read_text(encoding="utf-8")
        # Stop at TIER 2 to keep only the executive summary
        for boundary in ("# TIER 2", "## 2.1", "---\n\n# TIER 2"):
            idx = content.find(boundary)
            if idx > 0:
                return content[: min(idx, max_chars)]
        return content[:max_chars]
    except Exception as e:
        _Log.warn(f"Could not extract UIBlueprint executive summary: {e}")
        return ""


def _build_manifest_scaffold(
    linker,
    mfu_id: str,
    module_id: str,
    module_name: str,
    source_path: str,
    technology_tag: str,
    mfu_seq: str,
    srs_files: list,
) -> dict:
    """
    Builds the deterministic fields of a feature manifest using SRSPhysicalLinker.

    Extracted deterministically (zero hallucination risk):
      functions[]           ← S.5 EVENT-NNN entries from linker index
      bounding_box.controls ← S.3 verbatim physical names from linker index
      s3_row_refs[]         ← constructed from controls
      feature_id            ← {MODULE_PREFIX}-{MFU_SEQ}-F1
      is_infrastructure     ← False when any S.5 events exist
      category              ← Functional / Infrastructure
      l2_sources            ← S.5 event IDs + top S.3 control refs

    LLM-enriched separately (title, description, business_context).
    """
    module_prefix = module_id[4:] if module_id.startswith("MOD-") else module_id
    feature_id = f"{module_prefix}-{mfu_seq}-F1"

    # ── S.5 events → functions[] ─────────────────────────────────────────────
    prefix_s5 = f"srs::{mfu_id.lower()}::s5::event-"
    s5_entries: dict = {}
    for key, entry in linker.index.items():
        if key.startswith(prefix_s5):
            num = key[len(prefix_s5) :]
            s5_entries[num] = entry

    functions: list = []
    l2_s5: list = []
    for i, (num, entry) in enumerate(sorted(s5_entries.items()), 1):
        label = entry.get("target_string", f"S.5 Event {num}")[:120]
        fn_id = f"{feature_id}-FN{i}"
        l2_ref = f"SRS::{mfu_id}::S5::EVENT-{num}"
        functions.append({"id": fn_id, "label": label, "l2_source_ref": l2_ref})
        l2_s5.append(l2_ref)

    # ── S.3 controls → verbatim controls[] ───────────────────────────────────
    # SRSPhysicalLinker uses column-aware S.3 indexing (srs_linker.py):
    # it detects the 'Physical Name' column from the S.3 header row and indexes
    # ONLY values from that column. No heuristic filtering needed here.
    prefix_s3 = f"srs::{mfu_id.lower()}::s3::"
    # Values that must never appear as physical control names.
    # "None"/"none" arises when a linker table cell is Python-None
    # and gets stringified via str(None); the others are defensive guards.
    _INVALID_CTRL_NAMES = {"none", "null", "n/a", "undefined", "unknown", ""}
    controls_map: dict = {}  # lowercase → verbatim original
    for key, entry in linker.index.items():
        if key.startswith(prefix_s3):
            ts = entry.get("target_string")  # None if key missing
            if (
                ts is not None
                and ts.strip()
                and ts.lower() not in _INVALID_CTRL_NAMES
                and not ts.upper().startswith("TRCE-")
                and ts.lower() not in controls_map
            ):
                controls_map[ts.lower()] = ts
    controls = list(controls_map.values())
    s3_refs = [f"SRS::{mfu_id}::S3::{c}" for c in controls]
    l2_s3 = s3_refs[:15]  # top controls in l2_sources

    is_infra = len(functions) == 0
    focal_name = srs_files[0].name if srs_files else source_path

    return {
        "id": feature_id,
        "title": module_name,  # placeholder — LLM enriches
        "description": "",  # LLM enriches
        "source": f"SRS UIBlueprint — {focal_name}",
        "category": "Infrastructure" if is_infra else "Functional",
        "functions": functions,
        "business_context": {"current_state": "", "target_state": ""},
        "success_criteria": [],
        "l2_sources": list(dict.fromkeys(l2_s5 + l2_s3)),
        "bounding_box": {
            "id": f"BBOX::{mfu_id}::FULL_SCREEN",
            "zone_id": "FULL_SCREEN",
            "zone_label_en": "Full Screen",
            "zone_label_jp": "Full Screen",
            "screen_position": "full",
            "controls": controls,
            "s3_row_refs": s3_refs,
            "highlight_color": "#8B5CF6",
        },
        "is_infrastructure": is_infra,
        "srs_section_scope": [
            f"S.5 Events: EVENT-001 through EVENT-{str(len(functions)).zfill(3)}",
            f"S.3 Controls: {len(controls)} verbatim entries",
        ],
    }


# ---------------------------------------------------------------------------
# Focal Functions Extractor — language-agnostic S.5/BATCH function injection
# ---------------------------------------------------------------------------


def _extract_focal_functions(linker, mfu_id: str, srs_type: str = "") -> list[dict]:
    """
    Extracts verbatim function labels from the SRSPhysicalLinker index.

    Language-agnostic priority chain:
      Priority 1: S5::EVENT-NNN   — UIBlueprint event table rows
                                    (PowerBuilder, VB6, any event-driven UI)
      Priority 2: BATCH::STEP-NNN — BatchBlueprint job_steps[]
                                    (COBOL, RPG, Natural/ADABAS, any batch/procedural)
      Priority 3: empty list      — API-only or unknown → LLM derives freely

    Returns a list of dicts, each with:
      seq         : 1-based sequence number
      l2_ref      : canonical linker key  (e.g. "SRS::MFU-001::S5::EVENT-001")
      label       : verbatim event/step name (never inferred — from SRS text)
      description : first 120 chars of exact_quote for context
      source_type : "ui_event" | "batch_step"

    This list is used by _build_focal_anchor_functions_block() to pre-fill the
    {FOCAL_ANCHOR_FUNCTIONS} placeholder in the 05a prompt, ensuring function
    labels are 100% verbatim and never hallucinated.
    """
    if linker is None:
        return []

    mfu_lower = mfu_id.lower()

    # ── Priority 1: UIBlueprint S.5 events (event-driven languages) ──────────
    # Skipped for BatchBlueprint MFUs: _idx_batch dual-registers S5::EVENT-NNN
    # aliases for every JSON job_step, so Priority 1 would always fire for COBOL
    # even though the COBOL SRS document contains no EVENT-NNN text.  The linker
    # alias exists only for VB6 BatchBlueprint where EVENT-NNN IS present in the
    # markdown event table.  Gate on srs_type to preserve correct routing.
    prefix_s5 = f"srs::{mfu_lower}::s5::event-"
    s5_entries = sorted(
        [(k, v) for k, v in linker.index.items() if k.startswith(prefix_s5)],
        key=lambda x: x[0],
    )
    if s5_entries and srs_type != "BatchBlueprint":
        result = []
        for i, (k, v) in enumerate(s5_entries):
            num = k[len(prefix_s5) :]
            result.append(
                {
                    "seq": i + 1,
                    "l2_ref": f"SRS::{mfu_id}::S5::EVENT-{num}",
                    "label": (v.get("target_string") or "").strip(),
                    "description": (v.get("exact_quote") or "")[:400].strip(),
                    "source_type": "ui_event",
                    "operation_type": (v.get("operation_type") or "").strip(),
                }
            )
        _Log.observe(
            f"[_extract_focal_functions] {mfu_id}: {len(result)} S.5 events "
            f"(UIBlueprint — event-driven)"
        )
        return result

    # ── Priority 2: BatchBlueprint BATCH::STEP-NNN (procedural/batch) ────────
    prefix_batch = f"srs::{mfu_lower}::batch::step-"
    batch_entries = sorted(
        [
            (k, v)
            for k, v in linker.index.items()
            if k.startswith(prefix_batch) and k[len(prefix_batch) :].isdigit()
        ],
        key=lambda x: x[0],
    )
    if batch_entries:
        result = []
        for i, (k, v) in enumerate(batch_entries):
            num = k[len(prefix_batch) :].zfill(3)
            result.append(
                {
                    "seq": i + 1,
                    "l2_ref": f"SRS::{mfu_id}::BATCH::STEP-{num}",
                    "label": (v.get("target_string") or "").strip(),
                    "description": (v.get("exact_quote") or "")[:120].strip(),
                    "source_type": "batch_step",
                    # Batch steps are always procedural processing phases — never dispatch.
                    # operation_type defaults to "" so _is_dispatch() returns False for all.
                    "operation_type": "",
                }
            )
        _Log.observe(
            f"[_extract_focal_functions] {mfu_id}: {len(result)} BATCH steps "
            f"(BatchBlueprint — procedural/batch)"
        )
        return result

    # ── Priority 3: No deterministic source — LLM derives from full SRS ──────
    _Log.observe(
        f"[_extract_focal_functions] {mfu_id}: no S5 events or BATCH steps found — "
        f"LLM will derive function labels freely from SRS context."
    )
    return []


# ---------------------------------------------------------------------------
# Dispatch classification helper — used by _build_focal_anchor_functions_block
# ---------------------------------------------------------------------------

# Operation Type values that represent pure navigation/dispatch events.
# These are window-routing actions with no independent business data logic:
#   "Navigation"              — OpenSheet / OpenSheetWithParm calls
#   "Navigation/Exit"         — Close MDI / exit application
#   "Navigation/Permission"   — RBAC-gated OpenSheet (still routing, not CRUD)
#   "Initialisation/UX"       — SetPointer hourglass, title refresh (no business state)
# Conservative: empty/unknown operation_type → treat as NON-dispatch (inject verbatim).
_DISPATCH_OP_RE = re.compile(
    r"(?i)^navigation(?:[/ ]|$)|^initialisation/ux$|^initialization/ux$",
)


def _is_dispatch(fn: dict) -> bool:
    """
    Returns True if a focal function entry is a pure navigation/dispatch event
    that should be grouped rather than injected verbatim.

    Conservative default: if operation_type is empty/unknown, returns False
    so the entry is treated as non-dispatch and injected verbatim.  This
    prevents silent loss of business-critical events with missing metadata.
    """
    op = (fn.get("operation_type") or "").strip()
    if not op:
        return False  # unknown → safe side: inject verbatim
    return bool(_DISPATCH_OP_RE.match(op))


def _artifact_from_srs_filename(name: str) -> str:
    """'1_UIBlueprint_uo_pub_kongrpcd.md' -> 'uo_pub_kongrpcd' (the physical object name)."""
    stem = name[:-3] if name.lower().endswith(".md") else name
    return re.sub(r"^\d+_[A-Za-z][A-Za-z0-9]*_", "", stem).strip()


def _canon_screen_type(raw: str, is_focal: bool) -> str:
    r = (raw or "").lower()
    if "modal" in r or "popup" in r:
        return "Modal"
    if "ancestor" in r:
        return "Ancestor"
    if "entrypoint" in r or "anchor" in r:
        return "Entrypoint"
    if "data_provider" in r or "datawindow" in r:
        return "Component"
    return "Entrypoint" if is_focal else "Screen"


def _parse_preflight_inventory(ui_paths: list) -> dict:
    """Parse the SRS PRE-FLIGHT INVENTORY markdown table to enrich screens with the
    legacy physical file path / type / role. Robust to format variance — returns {} when
    the table is absent (caller falls back to filename-only screen records)."""
    inv: dict = {}
    for p in ui_paths:
        try:
            text = Path(p).read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        cols = None
        for ln in text.splitlines():
            if "|" not in ln:
                if cols is not None and not ln.strip():
                    cols = None  # table ended
                continue
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            low = [c.lower() for c in cells]
            if cols is None:
                if any("artifact id" in c for c in low) and any(
                    ("source path" in c) or ("role" in c) for c in low
                ):
                    cols = {}
                    for i, c in enumerate(low):
                        if "artifact id" in c:
                            cols["id"] = i
                        elif "source path" in c:
                            cols["path"] = i
                        elif "type" in c:
                            cols["type"] = i
                        elif "role" in c:
                            cols["role"] = i
                continue
            if all(set(c) <= set(":- ") for c in cells):  # separator row
                continue
            if "id" in cols and len(cells) > cols["id"]:
                aid = cells[cols["id"]]
                if not aid or aid.lower() == "artifact id":
                    continue
                inv[aid] = {
                    "type": cells[cols["type"]]
                    if "type" in cols and len(cells) > cols["type"]
                    else "",
                    "path": cells[cols["path"]]
                    if "path" in cols and len(cells) > cols["path"]
                    else "",
                    "role": cells[cols["role"]]
                    if "role" in cols and len(cells) > cols["role"]
                    else "",
                }
    return inv


_ASCII_HDR_RE = re.compile(r"(?im)^#{1,6}[^\n]*(?:visual\s+zone\s+map|ascii\s+layout)")
_MD_HDR_RE = re.compile(r"(?m)^#{1,6}\s")


def _extract_ascii_layout(srs_path):
    """#43 — pull the S.2.1 'Visual Zone Map (ASCII Layout)' block from a UIBlueprint SRS.
    Returns a dict {text, section, start_line, end_line} (1-based, inclusive — the line range
    of the ASCII art within the SRS file, so the UI can fetch it precisely), or None when the
    section is absent. Deterministic markdown-section extraction."""
    try:
        text = Path(srs_path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    m = _ASCII_HDR_RE.search(text)
    if not m:
        return None
    nl = text.find("\n", m.start())
    section = (
        (text[m.start() : nl] if nl != -1 else text[m.start() :])
        .strip()
        .lstrip("#")
        .strip(" *")
        .strip()
    )
    start = nl + 1 if nl != -1 else len(text)
    nxt = _MD_HDR_RE.search(text, start)  # section ends at the next markdown header
    body = text[start : nxt.start() if nxt else len(text)]
    fence = re.search(r"```[A-Za-z0-9]*\s*\n(.*?)\n?```", body, re.DOTALL)
    if fence:
        block = fence.group(1).strip("\n")
        block_abs = start + fence.start(1)  # absolute offset of the fenced content
    else:
        block = body.strip("\n")
        block_abs = start + (len(body) - len(body.lstrip("\n")))
    if len(block.strip()) < 10:
        return None
    start_line = text.count("\n", 0, block_abs) + 1
    return {
        "text": block[:6000],
        "section": section or "Visual Zone Map (ASCII Layout)",
        "start_line": start_line,
        "end_line": start_line + block.count("\n"),
    }


def _build_screen_registry(mfu_dir, srs_files: list, source_path: str = "") -> list:
    """#43 — deterministic registry of legacy SCREENS for an MFU. Each UIBlueprint SRS file
    is one screen (artifact = filename stem); enriched from the PRE-FLIGHT INVENTORY. API/
    Batch specs are data providers, not screens. Returns [] for headless MFUs."""
    ui_files = [f for f in (srs_files or []) if "uiblueprint" in Path(f).name.lower()]
    if not ui_files:
        return []
    focal_art = _artifact_from_srs_filename(Path(source_path).name) if source_path else None
    # Parse the FOCAL file's PRE-FLIGHT inventory only — it enumerates every artifact with
    # its role RELATIVE TO THIS MFU (entrypoint + modal + data providers). Each non-focal SRS
    # lists ITSELF as the primary anchor, so merging all files would mislabel the modal popup
    # as an entrypoint.
    _focal_file = (
        next((f for f in ui_files if _artifact_from_srs_filename(Path(f).name) == focal_art), None)
        or ui_files[0]
    )
    inv = _parse_preflight_inventory([_focal_file])
    out = []
    for f in ui_files:
        art = _artifact_from_srs_filename(Path(f).name)
        meta = inv.get(art, {})
        is_focal = art == focal_art
        lay = _extract_ascii_layout(f)
        out.append(
            {
                "artifact_id": art,
                "screen_id": art,  # best-effort canonical id (physical name); refine from S.1 later
                "physical_file": (meta.get("path") or "").strip() or None,
                "type": _canon_screen_type(meta.get("type"), is_focal),
                "role": (meta.get("role") or "").strip(),
                "srs_file": Path(f).name,
                "is_focal": is_focal,
                "ascii_layout": lay["text"] if lay else None,
                "ascii_layout_ref": {
                    "srs_file": Path(f).name,
                    "section": lay["section"] if lay else "S.2.1 Visual Zone Map (ASCII Layout)",
                    "start_line": lay["start_line"] if lay else None,
                    "end_line": lay["end_line"] if lay else None,
                },
            }
        )
    if out and not any(s["is_focal"] for s in out):
        out[0]["is_focal"] = True
    return out


def _screens_for_story(story: dict, registry: list) -> list:
    """#43 — which screen(s) a story touches + the controls/events it exercises on each,
    derived deterministically from the story's already-hydrated srs_evidence file_names."""
    if not registry:
        return []
    file_to_art = {s["srs_file"]: s["artifact_id"] for s in registry}
    by_art = {s["artifact_id"]: s for s in registry}
    touched: dict = {}
    events: dict = {}
    order: list = []
    for ev in story.get("srs_evidence", []):
        art = file_to_art.get(ev.get("file_name", ""))
        if not art:
            continue
        if art not in touched:
            touched[art], events[art] = set(), set()
            order.append(art)
        role = ev.get("evidence_role", "")
        if role == "ui_control" and ev.get("target_string"):
            touched[art].add(ev["target_string"])
        if role == "event_trigger" and "::S5::" in ev.get("l2_id", ""):
            events[art].add(ev["l2_id"].split("::")[-1])
    refs = []
    for art in order:
        s = by_art.get(art, {})
        refs.append(
            {
                "artifact_id": art,
                "screen_id": s.get("screen_id"),
                "srs_file": s.get("srs_file"),
                "ascii_layout_ref": s.get(
                    "ascii_layout_ref"
                ),  # {srs_file, section, start_line, end_line}
                "controls_touched": sorted(touched[art]),
                "events_covered": sorted(events[art]),
            }
        )
    return refs


def _function_overview(description: str) -> str:
    """Fix5(item1) — extract a clean business-overview phrase from a focal function's S.5
    event row (the SRS table row stored in `description`) so the function label reads as a
    sub-feature, not a bare event name. Returns '' when no clean overview is available.
    Language-agnostic: operates on the markdown table structure, not on language content."""
    if not description:
        return ""
    d = description.strip()
    if "|" in d:  # markdown table row: | # | Event Name | Overview | ... |
        cells = [c.strip() for c in d.split("|") if c.strip()]
        cands = [
            c
            for c in cells[1:]
            if len(c) > 20
            and " " in c
            and not c.lower().startswith(("jp:", "en:", "srs::", "trce-"))
        ]
        ov = cands[0] if cands else ""
    else:
        ov = d
    ov = ov.rstrip(" .,-—:")
    return ov if len(ov) >= 20 else ""


def _build_deterministic_functions(
    focal_fns: list, feature_id: str, nav_collapse_min: int = 8
) -> list:
    """
    #27 (M1 + P1) — Build a DETERMINISTIC functions[] from the focal-functions list so
    the Stage-5b story CEILING is stable run-to-run, instead of LLM-derived (which swung
    e.g. 6 vs 18 for the identical SRS). This mirrors the deterministic controls[]
    auto-inject — closing the asymmetry where controls[] was deterministic but
    functions[] (the ceiling) was not.

    Policy:
      • Each NON-DISPATCH S.5 event → one function (verbatim label + l2_source_ref).
        Labels come straight from the SRS S.5 table (traceable, never hallucinated).
      • DISPATCH (navigation / OpenSheet-style) events — gated by COUNT, not by the
        focal artifact's NAME:
          - GENUINE navigation hub (>= nav_collapse_min dispatch events) → collapse into
            ONE grouped navigation function (P1) — avoids one trivial "navigate to X"
            story per menu item while still representing the navigation capability.
          - FEWER than the threshold → keep them as INDIVIDUAL verbatim functions.
        Why count, not focal name: a focal-name "menu gate" misfires — e.g. PowerBuilder
        'menu_dw' is *named* like a menu but is a clipboard context-menu (not navigation),
        while an authentication window ('main') has session events (close / auto-logoff /
        terminate / continue) that _is_dispatch over-flags as "Navigation". A genuine
        navigation hub is characterised by having MANY dispatch targets; auth/clipboard
        have only a few. Gating on count therefore (a) restores coverage for non-hub
        features — auth's session events stay INDIVIDUAL → real stories instead of one
        bogus "navigate to screens" function — and (b) is fully LANGUAGE-AGNOSTIC (no
        per-language menu patterns; works for COBOL/VB6/PB alike). The threshold is
        config-overridable (stage5.nav_collapse_min_dispatch) for enterprise tuning.
      • No focal functions → return [] (caller leaves the manifest's functions[] as-is).

    Returns schema-conformant FeatureFunction dicts: {id, label, l2_source_ref}.
    """
    if not focal_fns or not feature_id:
        return []
    # Collapse dispatch into one nav function ONLY when there are enough navigation
    # targets that per-item stories would be redundant noise. Below this, individual
    # verbatim functions give better (and correctly-labelled) coverage.
    nav_collapse_min = max(2, int(nav_collapse_min or 8))
    non_dispatch = [f for f in focal_fns if not _is_dispatch(f)]
    dispatch = [f for f in focal_fns if _is_dispatch(f)]

    # Not a navigation hub (too few dispatch events) → treat them as individual functions
    # (verbatim, honest labels) rather than forcing a misleading navigation grouping.
    if 0 < len(dispatch) < nav_collapse_min:
        non_dispatch = non_dispatch + dispatch
        dispatch = []

    out: list = []
    seq = 0
    for f in non_dispatch:
        seq += 1
        base = (f.get("label") or "").strip() or f"Operation {seq}"
        # Fix5(item1) — enrich the bare event label with its business overview so the
        # function reads as a sub-feature ("Item Change Validation — Validates an entered
        # code against the database and updates the name field"). Falls back to the bare
        # label when no clean overview is available.
        _ov = _function_overview(f.get("description") or "")
        label = f"{base} — {_ov}" if (_ov and _ov.lower() not in base.lower()) else base
        out.append(
            {
                "id": f"{feature_id}-FN{seq}",
                "label": label[:240],
                "l2_source_ref": (f.get("l2_ref") or "").strip(),
            }
        )
    if dispatch:
        # Genuine navigation menu: one grouped function. Label is honest about being a
        # group; l2_source_ref uses a GAP marker (the group spans many events, so no
        # single S.5 event truthfully represents it — avoids the label/ref mismatch the
        # critic caught). GAP:: refs are accepted by the critic.
        seq += 1
        _mfu = (
            (dispatch[0].get("l2_ref") or "SRS::UNKNOWN::").split("::")[1]
            if "::" in (dispatch[0].get("l2_ref") or "")
            else "UNKNOWN"
        )
        out.append(
            {
                "id": f"{feature_id}-FN{seq}",
                "label": f"Navigate to the {len(dispatch)} target screens reached from this feature",
                "l2_source_ref": f"GAP::{_mfu}::NAVIGATION_DISPATCH_GROUP",
            }
        )
    return out


def _build_focal_anchor_functions_block(
    functions: list[dict],
    mfu_id: str,
    event_inline_max: int = 20,
    batch_inline_max: int = 50,
) -> str:
    """
    Builds the {FOCAL_ANCHOR_FUNCTIONS} injection block from pre-extracted functions.

    Injection strategy — three paths:

    INLINE (count ≤ threshold OR all events are non-dispatch):
      Inject every entry verbatim.  LLM must use exact labels.

    SEMANTIC-FILTER (count > threshold AND mixed dispatch/non-dispatch):
      Inject non-dispatch events verbatim (CRUD, Validation, Data Entry, etc.)
      Append a condensed grouping note for the dispatch subset.
      LLM gets verbatim constraints on the events that matter AND grouping
      guidance for the navigation-only events.

    PURE-DISPATCH condensed (count > threshold AND all events are dispatch):
      No verbatim injection — all events are OpenSheet-style routing.
      Emit count + domain-grouping instruction only; no misleading sample.
      LLM reads the full S.5 table from SRS and groups by business domain.

    Thresholds are configurable per-provider via project_config.json:
      llm.providers.{name}.event_inline_max   (default: 20)
      llm.providers.{name}.batch_inline_max   (default: 50)
    Batch steps always default to INLINE because they are never dispatch.
    """
    if not functions:
        return (
            "[FOCAL_ANCHOR_FUNCTIONS: NOT AVAILABLE]\n"
            "No S.5 events or batch steps were indexed for this MFU.\n"
            "Derive function labels freely from the SRS content (API endpoints, "
            "batch phases, or narrative processing descriptions).\n"
        )

    source_type = functions[0].get("source_type", "ui_event")
    if source_type == "batch_step":
        header_type = "Batch Steps from BatchBlueprint (procedural/COBOL)"
        _inline_max = batch_inline_max
    else:
        header_type = "S.5 Events from UIBlueprint (event-driven)"
        _inline_max = event_inline_max

    # ── Semantic filter: only triggers when count exceeds threshold ───────────
    if len(functions) > _inline_max:
        dispatch = [fn for fn in functions if _is_dispatch(fn)]
        non_dispatch = [fn for fn in functions if not _is_dispatch(fn)]

        # ── Path A: ALL events are dispatch (pure Navigation/Dispatch MDI shell)
        if not non_dispatch:
            lines = [
                f"[HIGH EVENT COUNT — {len(functions)} {header_type}, all Navigation/Dispatch]",
                f"⚠️  All {len(functions)} events are Navigation/Dispatch routing actions.",
                "    Injecting verbatim labels would bloat the prompt without adding value.",
                "    Derive functions DIRECTLY from the SRS [FOCAL ENTRYPOINT] S.5 table.",
                "",
                "MANDATORY — Apply Navigation/Dispatch grouping (DIRECTIVE 8a):",
                "  → Group dispatch events by business domain section → 1 function per group.",
                "  → Lifecycle events (Arrange, Exit, Timer) → 1 function each.",
                "  → Target: 6–12 clustered function entries total.",
                "  → Function labels MUST use verbatim menu/event text from the SRS S.5 table.",
                "  → l2_source_ref = the first (most representative) event L2 ID in each group.",
            ]
            return "\n".join(lines)

        # ── Path B: ALL events are non-dispatch — fall through to INLINE
        #    (inject all verbatim regardless of count; these are all business-critical)
        if not dispatch:
            pass  # handled by INLINE block below

        # ── Path C: MIXED — inject non-dispatch verbatim, condense dispatch
        else:
            lines = [
                f"[SEMANTIC-FILTERED FUNCTIONS — {len(non_dispatch)} non-dispatch of "
                f"{len(functions)} total {header_type}]",
                "",
                f"The following {len(non_dispatch)} events are NON-DISPATCH (business-critical).",
                "These labels were extracted VERBATIM from the SRS. Use them exactly.",
                "",
            ]
            for fn in non_dispatch:
                lines.append(f'FN{fn["seq"]} → {fn["l2_ref"]} | "{fn["label"]}"')
                if fn.get("description"):
                    lines.append(f"      Context: {fn['description']}")
            lines += [
                "",
                "RULES FOR NON-DISPATCH ENTRIES ABOVE:",
                f"  1. Create exactly {len(non_dispatch)} function entries — one per FN line.",
                "  2. label MUST contain the verbatim text from the FN line.",
                f"  3. l2_source_ref MUST be the L2 ID shown (SRS::{mfu_id}::...).",
                "  4. DO NOT invent labels not present in this list.",
                "",
                f"ADDITIONALLY — {len(dispatch)} Navigation/Dispatch events are present in the SRS S.5 table.",
                "  → Do NOT enumerate them individually.",
                "  → Group them by business domain section → 1 function per domain (DIRECTIVE 8a).",
                "  → Derive labels verbatim from the SRS S.5 table; target 3–6 grouped entries.",
            ]
            return "\n".join(lines)

    # ── INLINE mode: list every entry verbatim ────────────────────────────────
    lines = [
        f"[MANDATORY PRE-EXTRACTED FUNCTIONS — {len(functions)} {header_type}]",
        "These labels were extracted VERBATIM from the SRS by the linker.",
        "You MUST use them as-is for function labels — do NOT paraphrase or invent new names.",
        "",
    ]
    for fn in functions:
        lines.append(f'FN{fn["seq"]} → {fn["l2_ref"]} | "{fn["label"]}"')
        if fn.get("description"):
            lines.append(f"      Context: {fn['description']}")

    lines += [
        "",
        "RULES:",
        f"  1. Create exactly {len(functions)} function entries (one per FN above).",
        "     Exception: apply Navigation/Dispatch grouping ONLY if the events are",
        "     structurally identical dispatch-only actions with no business logic.",
        "  2. label MUST contain the verbatim text from the FN line above.",
        "     Enrich with domain vocabulary ONLY — never replace the core label.",
        f"  3. l2_source_ref MUST be the L2 ID shown above (SRS::{mfu_id}::...).",
        "  4. DO NOT invent function labels not present in this list.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module Context Loader — Layer 2: Module Context Injection (DEFECT-2 fix)
# ---------------------------------------------------------------------------


def _load_module_context(project_root: Path, module_id: str) -> dict:
    """
    Loads the refined module name and business purpose description from the
    global module_manifest.json produced by Stage 2.7.

    Running Stage 5 AFTER Stage 2.7 (pipeline resequencing) guarantees that
    the manifest contains the DDD-quality names generated by the semantic
    rehydration pass — not the provisional folder-derived names from Stage 2.5.

    The returned data is injected as a [MODULE CONTEXT] block prepended to
    the SRS content sent to Stage 5a, providing top-down domain framing that
    prevents wrong-module misclassification even when supporting context
    documents from other modules appear in the same MFU directory.

    Search order for module_manifest.json:
      1. {project_root}/_global/module_manifest.json
      2. {project_root}/projects/sample_project/_global/module_manifest.json
         (legacy path for runs started from workspace root)

    Returns
    -------
    dict
        {'module_name': str, 'description': str} — both may be empty strings
        on any failure (graceful fallback: caller proceeds without context).
    """
    candidates = [
        project_root / "_global" / "module_manifest.json",
        project_root / "projects" / "sample_project" / "_global" / "module_manifest.json",
    ]
    manifest_path: Path | None = None
    for c in candidates:
        if c.exists():
            manifest_path = c
            break

    if manifest_path is None:
        return {}

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        for mod in data.get("modules", []):
            if mod.get("module_id") == module_id:
                return {
                    "module_name": mod.get("module_name", ""),
                    "description": mod.get("description", ""),
                }
    except Exception as e:
        _Log.warn(f"Could not load module context from {manifest_path}: {e}")
    return {}


# ---------------------------------------------------------------------------
# Metadata resolution
# ---------------------------------------------------------------------------
def _resolve_module_metadata(mfu_dir: Path, module_dir: Path) -> dict:
    """
    Returns {'mfu_id', 'module_id', 'module_name'} from config_naming_map.json
    or falls back to directory names.
    """
    config_file = mfu_dir / "config_naming_map.json"
    if config_file.exists():
        try:
            cfg = json.loads(config_file.read_text(encoding="utf-8"))
            return {
                "mfu_id": cfg.get("mfu_id", mfu_dir.name),
                "module_id": cfg.get("module", module_dir.name),
                "module_name": cfg.get("mfu_name", cfg.get("module_name", mfu_dir.name)),
            }
        except Exception:
            pass
    return {
        "mfu_id": mfu_dir.name,
        "module_id": module_dir.name,
        "module_name": module_dir.name,
    }


def _resolve_technology(mfu_dir: Path, project_root: Path) -> str:
    """
    Determines the source technology tag for this MFU.
    Resolution order:
      1. project_config.json — located via RIP_LLM_CONFIG_PATH env var (set by CLI),
         then by scanning project_root and projects/*/ subdirectories.
         Reads "project.source_paradigm" (standard RIP structure) as well as the
         legacy top-level fields "language", "source_language", and "technology".
      2. config_naming_map.json in mfu_dir (reads "source_language" or "technology" field)
      3. Heuristic: examine source artifact names in config_naming_map.json naming_map keys
      4. Default: "PowerBuilder"
    """
    # 1. Try project_config.json
    # Build candidate list: env var first (highest priority — set by CLI to the
    # active project config path), then direct root paths, then projects/*/ subdirs.
    cfg_names = (
        "project_config.json",
        "pb_project_config.json",
        "vb6_project_config.json",
        "cbl_project_config.json",
    )
    config_candidates: list = []

    env_cfg = os.environ.get("RIP_LLM_CONFIG_PATH")
    if env_cfg:
        config_candidates.append(Path(env_cfg))

    for cfg_name in cfg_names:
        config_candidates.append(project_root / cfg_name)

    projects_dir = project_root / "projects"
    if projects_dir.is_dir():
        for sub in sorted(projects_dir.iterdir()):
            if sub.is_dir():
                for cfg_name in cfg_names:
                    config_candidates.append(sub / cfg_name)

    for cfg_path in config_candidates:
        if not cfg_path.exists():
            continue
        try:
            raw = cfg_path.read_bytes()
            # Strip BOM if present
            if raw.startswith(b"\xef\xbb\xbf"):
                raw = raw[3:]
            cfg = json.loads(raw.decode("utf-8", errors="replace"))
            # Standard RIP structure: project.source_paradigm (e.g. "vb6", "pb", "cobol")
            # Also accept legacy top-level keys for backward compatibility.
            lang = (
                cfg.get("project", {}).get("source_paradigm")
                or cfg.get("language")
                or cfg.get("source_language")
                or cfg.get("technology")
            )
            if lang:
                return _normalise_tech_tag(lang)
        except Exception:
            pass

    # 2. Try config_naming_map.json in mfu_dir
    nm_path = mfu_dir / "config_naming_map.json"
    if nm_path.exists():
        try:
            cfg = json.loads(nm_path.read_text(encoding="utf-8"))
            lang = cfg.get("source_language") or cfg.get("technology")
            if lang:
                return _normalise_tech_tag(lang)

            # 3. Heuristic: examine naming_map keys
            naming_map = cfg.get("naming_map", {})
            all_keys = " ".join(naming_map.keys()).lower()
            if any(sig in all_keys for sig in ["w_", "u_", "dw_", "n_", "cb_", "sle_"]):
                return "PowerBuilder"
            if any(sig in all_keys for sig in [".frm", "form_", "class_"]):
                return "VB6"
            if any(sig in all_keys for sig in ["perform", "exec sql", "pic x"]):
                return "COBOL"
        except Exception:
            pass

    # 4. Default
    return "PowerBuilder"


def _normalise_tech_tag(raw: str) -> str:
    """Maps raw language strings to canonical technology tags."""
    raw_lower = raw.strip().lower()
    if "powerbuilder" in raw_lower or raw_lower in ("pb", "powerbuilder"):
        return "PowerBuilder"
    if "vb6" in raw_lower or "visual basic" in raw_lower or raw_lower == "vb":
        return "VB6"
    if "cobol" in raw_lower:
        return "COBOL"
    if "java" in raw_lower:
        return "Java"
    return "Generic"


def _resolve_srs_type(focal_fns: list, focal_filename: str = "") -> str:
    """
    Derives the SRS document type from focal-function source_type and focal filename.

    Used to select the correct l2_source_ref format from the language profile
    (e.g. BATCH::STEP for BatchBlueprint, S5::EVENT for UIBlueprint).

    Resolution priority (language-agnostic):
      1. focal_fns[0].source_type == "batch_step"  → BatchBlueprint
         (COBOL/RPG batch: linker detected BATCH::STEP entries)
      2. focal_filename contains "_BatchSpec_"      → BatchBlueprint
         (VB6/PB batch forms whose S.5 entries are indexed as ui_event, not
          batch_step — filename is the authoritative fallback in this case)
      3. focal_filename contains "_APISpec_"        → APIContracts
         (API-only MFUs: utility modules, shared services with no UI)
      4. focal_filename contains "_UIBlueprint_"    → UIBlueprint  (explicit)
      5. Default                                    → UIBlueprint  (safe fallback)

    Args:
        focal_fns:      List of focal function dicts from _extract_focal_functions().
                        Each dict has a "source_type" key.
        focal_filename: Name (not full path) of the focal SRS file. Used to detect
                        API-spec-only MFUs that have no S.5 events and no batch steps.

    Returns:
        "BatchBlueprint" | "APIContracts" | "UIBlueprint"
    """
    if focal_fns and focal_fns[0].get("source_type") == "batch_step":
        return "BatchBlueprint"
    if focal_filename and "_BatchSpec_" in focal_filename:
        return "BatchBlueprint"
    # Use _detect_srs_document_type for API detection to stay in sync with the
    # canonical classification logic.  A hard-coded "_APISpec_" substring check
    # missed "_APIContracts_" filenames which _detect_srs_document_type returns
    # as "APIContracts".
    if focal_filename and _detect_srs_document_type(focal_filename) == "APIContracts":
        return "APIContracts"
    return "UIBlueprint"


def _build_language_hints_block(
    registry,  # Stage5ProfileRegistry | None
    technology_tag: str,
    srs_type: str,
) -> str:
    """
    Returns the fully-rendered {LANGUAGE_HINTS} block for the given language
    and SRS type, delegating to the Stage5ProfileRegistry.

    Graceful-degradation contract:
      - If registry is None (import failed or YAML missing) → ""
      - If technology_tag has no profile → "" (Generic fallback handled by registry)
      - If rendering raises unexpectedly → "" (never propagates to caller)

    The returned string is a self-contained, prompt-ready block or "".
    """
    if registry is None:
        return ""
    try:
        return registry.render_hints_block(technology_tag, srs_type)
    except Exception as _e:
        _Log.warn(f"[LANGUAGE_HINTS] Rendering failed for '{technology_tag}': {_e}")
        return ""


def _extract_mfu_seq(mfu_id: str) -> str:
    """
    Extracts the zero-padded 3-digit sequence from mfu_id.
    Examples: 'MFU-001' → '001', 'MFU-002A' → '002', 'MFU-007' → '007'

    Non-standard IDs (e.g. 'SYS-ALERT-01') do NOT follow the MFU-NNN pattern.
    Using re.search on them would extract '01' → '001', colliding with MFU-001.
    Instead, we generate a hash-based sequence in the 900–998 range to guarantee
    no collision with real MFU sequences (which start at 001).
    """
    import hashlib

    # Standard MFU-NNN or MFU-NNN<suffix> pattern
    std_match = re.match(r"^MFU-(\d+)", mfu_id)
    if std_match:
        return std_match.group(1).zfill(3)[:3]
    # Non-standard ID — use MD5 hash to derive a stable, collision-free sequence 900–998
    h = hashlib.md5(mfu_id.encode()).hexdigest()
    seq = 900 + (int(h[:4], 16) % 99)
    return str(seq)


# ---------------------------------------------------------------------------
# SRS zone content extraction
# ---------------------------------------------------------------------------
def _extract_srs_zone_content(srs_content: str, feature: dict) -> str:
    """
    Returns the SRS content for Stage 5b story generation.

    Under the 1-MFU = 1-Feature principle, each feature covers the ENTIRE MFU
    (bounding_box.zone_id = "FULL_SCREEN"). There is no zone-based sub-selection
    to perform — Stage 5b receives the complete SRS so it can derive stories
    across all S.5 events, S.3 controls, and S.4 validation rules.

    The full SRS content is always returned. The previous zone-filtering logic
    has been removed because it was designed for the now-deprecated multi-feature
    decomposition model.
    """
    return srs_content


# ---------------------------------------------------------------------------
# Empty/skeleton builders
# ---------------------------------------------------------------------------
def _build_empty_manifest(meta: dict, source_path: str, technology_tag: str) -> dict:
    """Returns a minimal feature_manifest.json when Stage 5a fails to parse."""
    mfu_id = meta["mfu_id"]
    return {
        "schema_version": "5a.1",
        "mfu_id": mfu_id,
        "mfu_seq": _extract_mfu_seq(mfu_id),
        "module_id": meta["module_id"],
        "module_name": meta["module_name"],
        "module_description": meta.get("module_description", ""),
        "source_path": source_path,
        "technology_tag": technology_tag,
        "l1_source": {
            "id": f"SRS::{mfu_id}::UNKNOWN",
            "document_type": "SRS_UIBlueprint",
            "path": source_path,
            "mfu_id": mfu_id,
        },
        "features": [],
        "generation_metadata": {
            "generator_model": "unknown",
            "iteration_count": 1,
            "final_status": "FAIL_PARSE_ERROR",
            "generated_at": datetime.now(UTC).isoformat(),
        },
    }


def _build_skeleton_output(meta: dict, source_path: str) -> dict:
    """Returns a minimal features_stories.json skeleton when everything fails."""
    mfu_id = meta["mfu_id"]
    return {
        "schema_version": "5.0",
        "mfu_id": mfu_id,
        "module_id": meta["module_id"],
        "module_name": meta["module_name"],
        "module_description": meta.get(
            "module_description", ""
        ),  # caller overrides with business desc
        "source_path": source_path,
        "l1_source": {
            "id": f"SRS::{mfu_id}::UNKNOWN",
            "document_type": "SRS_UIBlueprint",
            "path": source_path,
            "mfu_id": mfu_id,
        },
        "features": [],
        "generation_metadata": {
            "generator_model": "unknown",
            "critic_model": "unknown",
            "iteration_count": 1,
            "final_status": "FAIL_NO_PARSEABLE_OUTPUT",
            "critic_feedback_log": [],
            "generated_at": datetime.now(UTC).isoformat(),
        },
    }


# ---------------------------------------------------------------------------
# L2 ID ASCII sanitiser
# ---------------------------------------------------------------------------
def _make_ascii_slug(name: str) -> str:
    """
    Converts a control/rule name that may contain non-ASCII characters
    (Japanese, Chinese, Korean, etc.) into a stable ASCII-safe slug suitable
    for use as the final segment of an L2 ID.

    Strategy:
      1. If the name is already pure ASCII — return it unchanged.
      2. Extract the ASCII prefix (variable prefix like m_, cb_, st_ …).
      3. Append the first 6 hex chars of the UTF-8 MD5 hash for uniqueness.
    Examples:
      m_プリンタの設定  →  m_a3f81c
      m_システムの終了  →  m_0d92e5
      cb_新規登録       →  cb_7b3e14
    """
    import hashlib
    import re

    if name.isascii():
        return name
    prefix = ""
    for ch in name:
        if ch.isascii() and (ch.isalnum() or ch == "_"):
            prefix += ch
        else:
            break
    h6 = hashlib.md5(name.encode("utf-8")).hexdigest()[:6]
    slug = f"{prefix}{h6}" if prefix else f"ctrl_{h6}"
    return re.sub(r"[^\w]", "_", slug)


def _sanitise_l2_ids(obj: object) -> object:
    """
    Deep-walks a dict/list/str and rewrites every L2 ID whose final segment
    contains non-ASCII characters, using _make_ascii_slug().

    L2 ID pattern:  SRS::<mfu_id>::<section>::<segment>
    Only the <segment> (last :: token) is slugified — the prefix is stable.

    bounding_box.controls[] entries are intentionally NOT touched: they hold
    the raw legacy control names as-is, which the critic cross-checks against
    the S.3 section of the SRS. Only l2_sources, s3_row_refs, and
    acceptance_criteria[].l2_source_ref are sanitised.
    """
    import re

    _L2_RE = re.compile(r"^(SRS::[^:]+::[^:]+::)(.+)$")

    def _fix_l2(s: str) -> str:
        m = _L2_RE.match(s)
        if not m:
            return s
        prefix_part, segment = m.group(1), m.group(2)
        if segment.isascii():
            return s
        return prefix_part + _make_ascii_slug(segment)

    def _walk(node, in_controls: bool = False):
        if isinstance(node, str):
            # Only rewrite when we're NOT inside a controls[] list
            if not in_controls and node.startswith("SRS::"):
                return _fix_l2(node)
            return node
        if isinstance(node, list):
            return [_walk(item, in_controls) for item in node]
        if isinstance(node, dict):
            result = {}
            for k, v in node.items():
                # bounding_box.controls[] keeps original names for Gate 7 S.3 cross-check
                inside_controls = in_controls or k == "controls"
                result[k] = _walk(v, inside_controls)
            return result
        return node

    return _walk(obj)


# ---------------------------------------------------------------------------
# Post-processing: L2 ref validator and AC quality checker
# ---------------------------------------------------------------------------

# SQL keywords and COBOL-specific patterns (language-agnostic: covers VB6, PB, COBOL)
# SQL contamination detection for Gherkin "when" clauses.
#
# Design principle: distinguish SQL DML operations from UI button names.
# Japanese CRUD screens frequently have an "Update" (更新) button and a "Delete" (削除)
# button. "when the user clicks the Update button" is CORRECT Gherkin — the word
# "Update" refers to the button label, not a SQL statement.
#
# Rules for UPDATE/DELETE:
#   • "UPDATE <tablename>" or "UPDATE ... SET" → SQL DML → flag ✓
#   • "executes ... Update" / "system ... Update" → SQL context → flag ✓
#   • "clicks ... Update button" / "Update (更新)" → button name → DO NOT flag ✗
#
# Precise patterns used:
#   UPDATE/DELETE: require SQL-context indicators (table name after UPDATE, FROM after DELETE,
#                  or system/execute verbs before them) to avoid flagging button names.
#   SELECT/INSERT: flag whenever they appear — these are never UI control names.
#   Other SQL keywords (FROM table, WHERE, EXEC SQL, etc.): unchanged.
_SQL_KEYWORDS_RE = re.compile(
    r"("
    # ── Structural SQL constructs ─────────────────────────────────────────
    r"\bSELECT\b[^.\n]{0,80}\bFROM\b"  # SELECT ... FROM (requires FROM — avoids "select a tab")
    r"|\bINSERT\s+INTO\b"  # INSERT INTO
    r"|\bUPDATE\s+\w+\s+SET\b"  # UPDATE tablename SET (DML statement)
    r"|\b(?:system|executes?|runs?|performs?|calls?)\b[^.]{0,40}\bupdat\w*\b"  # system executes an update
    r"|\bDELETE\s+FROM\b"  # DELETE FROM
    r"|\b(?:system|executes?|runs?|performs?|calls?)\b[^.]{0,40}\bdelet\w*\b"  # system executes a delete
    r"|\bFROM\s+\w+\s+WHERE\b"  # FROM table WHERE (query fragment)
    r"|\bWHERE\s+\w+\s*[=<>]"  # WHERE col = (predicate)
    r"|\bEXEC\s+SQL\b"  # COBOL EXEC SQL
    r"|\bEXEC\s+CICS\b"  # COBOL CICS
    r"|\bJOIN\s+\w+\s+ON\b"  # JOIN table ON
    r"|\bOPEN\s+CURSOR\b|\bFETCH\s+\w+\s+INTO\b"  # cursor operations
    r"|\bDECLARE\s+\w+\s+CURSOR\b"  # DECLARE cursor
    # ── Multi-word technical implementation phrases ───────────────────────
    # These are unambiguously implementation-layer — never valid in Gherkin when-clauses.
    # Pattern: "database <SQL-verb> fails/errors" (catches MFU-002: "database UPDATE fails")
    r"|\bdatabase\b[^.]{0,40}\b(?:update|delete|insert|query|write|commit|rollback)\b[^.]{0,20}\b(?:fail|error|exception)\b"
    # Pattern: "SQL error / SQL exception / SQL statement" (bare SQL noun as technical artifact)
    r"|\bSQL\s+(?:error|exception|fail|statement|query|command)\b"
    # Pattern: "concurrency conflict" (database-layer race condition — implementation detail)
    r"|\bconcurrency\s+(?:conflict|violation|error|issue)\b"
    # Pattern: "transaction rollback / transaction fails" (DB transaction lifecycle)
    r"|\btransaction\s+(?:rollback|fail|abort|error|exception)\b"
    # Pattern: "deadlock / lock timeout / lock contention" (DB locking — implementation)
    r"|\b(?:deadlock|lock\s+timeout|lock\s+contention)\b"
    # Pattern: "stored procedure / trigger fires" (procedural DB construct)
    r"|\b(?:stored\s+procedure|trigger\s+fires?)\b"
    r")",
    re.IGNORECASE,
)
# Internal implementation call patterns (recordset opens, method dispatches)
_IMPL_CALL_RE = re.compile(
    r"(?:rs|conn|db|g_conn|g_db|recordset|connection)\s*\.\s*"
    r"(?:open|execute|query|fetch|retrieve|close)\s*\(",
    re.IGNORECASE,
)

# Synthetic REST slug pattern: API::POST_<anything>, API::GET_<anything>, etc.
_SYNTHETIC_SLUG_RE = re.compile(
    # Matches synthetic module-path slugs like POST_mod-frmbatchrun-mfu-003.
    # These were auto-generated from MFU module IDs and do not correspond to real
    # REST endpoint paths.  The negative lookahead (?!api[_/]) excludes real endpoint
    # slugs (e.g. POST_api_v1_payroll_export) that the G1/G2 prompt fix now generates.
    # Real endpoint slugs always start their path component with "api_" or "api/".
    r"^SRS::[^:]+::API::(?:POST|GET|PUT|DELETE|PATCH|HEAD|OPTIONS)_(?!api[_/])",
    re.IGNORECASE,
)
# Trace ID used as l2_ref: S5::TRCE-<anything>
_TRACE_ID_REF_RE = re.compile(r"^SRS::[^:]+::S5::TRCE-", re.IGNORECASE)


def _validate_l2_refs(stories: list, feature_l2_sources: list, mfu_id: str) -> list:
    """
    Post-processes parsed user stories to repair two classes of invalid l2_source_refs:

    1. SYNTHETIC REST SLUGS — fabricated by the LLM when the SRS has API-only documents
       with no real S5::EVENT anchors. Pattern: SRS::MFU-003::API::POST_mod-frmbatchrun-mfu-003
       These slugs are auto-generated module-path identifiers, not real SRS section anchors.
       The srs_linker fuzzy fallback cannot resolve them, resulting in empty srs_evidence.

    2. TRACE IDs AS L2 REFS — trace IDs (TRCE-UI-*, TRCE-IO-*) are source code line
       references injected into S.5 event descriptions. They are not section anchors.
       Pattern: SRS::MFU-005::S5::TRCE-UI-frmreportviewer-L8

    REPAIR STRATEGY (language-agnostic):
       Find the best valid fallback from feature_l2_sources[]:
         Priority 1 — S5::EVENT ref (most specific SRS anchor)
         Priority 2 — S3 control ref (control-level traceability)
         Priority 3 — first available ref in l2_sources[]
       Replace the invalid ref with the best fallback and log a WARN.

    Returns the repaired stories list. Input stories are not mutated.
    """

    # Build priority-ordered fallback from feature-level l2_sources.
    # TRCE refs and synthetic slugs are EXCLUDED from the fallback pool —
    # replacing an invalid ref with another TRCE/synthetic ref would still
    # fail Gate 5h; only canonical L2 IDs may serve as fallbacks.
    def _is_valid_l2(r: str) -> bool:
        return bool(r) and not _TRACE_ID_REF_RE.match(r) and not _SYNTHETIC_SLUG_RE.match(r)

    s5_events = [r for r in feature_l2_sources if "::S5::EVENT-" in r]
    s5_specs = [r for r in feature_l2_sources if "::S5::SPEC-" in r]
    s3_controls = [r for r in feature_l2_sources if "::S3::" in r]
    batch_steps = [
        r for r in feature_l2_sources if "::BATCH::STEP-" in r or "::BATCH::PARAM::" in r
    ]
    clean_all = [r for r in feature_l2_sources if _is_valid_l2(r)]

    # Priority: S5::EVENT > S5::SPEC > S3 control > BATCH step > any clean ref
    fallback_candidates = s5_events + s5_specs + s3_controls + batch_steps + clean_all
    best_fallback = fallback_candidates[0] if fallback_candidates else None

    def _needs_repair(ref: str) -> bool:
        if not ref:
            return False
        return bool(_SYNTHETIC_SLUG_RE.match(ref)) or bool(_TRACE_ID_REF_RE.match(ref))

    def _repair(ref: str, context_id: str) -> str:
        if best_fallback and best_fallback != ref:
            kind = "synthetic REST slug" if _SYNTHETIC_SLUG_RE.match(ref) else "trace-ID ref"
            _Log.warn(f"L2 ref repair [{mfu_id}/{context_id}]: {kind} '{ref}' → '{best_fallback}'")
            return best_fallback
        # No valid fallback available — leave as-is but still warn
        _Log.warn(
            f"L2 ref repair [{mfu_id}/{context_id}]: invalid ref '{ref}' "
            f"but no valid fallback found in feature l2_sources — leaving unchanged."
        )
        return ref

    repaired = []
    for story in stories:
        story = dict(story)  # shallow copy to avoid mutating input

        # Repair acceptance_criteria[].l2_source_ref
        acs = []
        for ac in story.get("acceptance_criteria", []):
            ac = dict(ac)
            ref = ac.get("l2_source_ref", "")
            if _needs_repair(ref):
                ac["l2_source_ref"] = _repair(ref, ac.get("id", "?"))
            acs.append(ac)
        story["acceptance_criteria"] = acs

        # Repair story-level l2_sources[]
        repaired_l2s = []
        for ref in story.get("l2_sources", []):
            repaired_l2s.append(_repair(ref, story.get("id", "?")) if _needs_repair(ref) else ref)
        # Deduplicate preserving order
        seen: set = set()
        deduped = []
        for r in repaired_l2s:
            if r not in seen:
                seen.add(r)
                deduped.append(r)
        story["l2_sources"] = deduped

        repaired.append(story)

    return repaired


def _enrich_evidence_ac_ids(story: dict) -> None:
    """
    Populates ac_ids on each srs_evidence entry for a story.

    For every evidence entry, finds all Acceptance Criteria in the same story
    whose l2_source_ref matches the evidence l2_id, and writes their IDs into
    evidence['ac_ids'].  This enables per-AC highlighting in the traceability
    viewer — clicking AC1 highlights only the evidence entries that back AC1,
    not the whole story's evidence set.

    Called deterministically after the linker hydrates srs_evidence — no LLM
    needed. Mutates the story dict in-place.
    """
    # Build map: l2_id → [ac_id, ...]
    l2_to_ac_ids: dict = {}
    for ac in story.get("acceptance_criteria", []):
        ref = ac.get("l2_source_ref", "")
        ac_id = ac.get("id", "")
        if ref and ac_id:
            l2_to_ac_ids.setdefault(ref, []).append(ac_id)

    for evidence in story.get("srs_evidence", []):
        l2_id = evidence.get("l2_id", "")
        evidence["ac_ids"] = l2_to_ac_ids.get(l2_id, [])


def _check_ac_gherkin_quality(stories: list, mfu_id: str) -> None:
    """
    Scans acceptance criteria 'when' clauses for implementation contamination.
    Issues WARN log entries for violations — non-mutating audit trail only.

    LANGUAGE-AGNOSTIC detection covers:
      * SQL keywords:  SELECT, FROM, WHERE, INSERT, UPDATE, DELETE
      * COBOL SQL:     EXEC SQL, EXEC CICS, OPEN <cursor>, FETCH, CURSOR
      * PB/VB6 calls:  rs.Open(), g_conn.Execute(), recordset.Retrieve()
      * File paths:    hardcoded path fragments (C:\\, /var/, \\\\server\\)

    This function is intentionally non-blocking — it logs and returns.
    The Critic prompt Gates 5g/5h provide the authoritative fail decision.
    """
    import re as _re

    _FILE_PATH_RE = _re.compile(r"(?:[A-Za-z]:\\\\|/var/|/etc/|\\\\\\\\\w)", re.IGNORECASE)

    for story in stories:
        sid = story.get("id", "UNKNOWN")
        for ac in story.get("acceptance_criteria", []):
            ac_id = ac.get("id", "?")
            when = ac.get("when", "")
            if not when:
                continue
            if _SQL_KEYWORDS_RE.search(when):
                m = _SQL_KEYWORDS_RE.search(when)
                _Log.warn(
                    f"AC quality [{mfu_id}/{ac_id}]: 'when' contains SQL keyword "
                    f"'{m.group(0).strip()}' — move implementation details to technical_notes."
                )
            elif _IMPL_CALL_RE.search(when):
                m = _IMPL_CALL_RE.search(when)
                _Log.warn(
                    f"AC quality [{mfu_id}/{ac_id}]: 'when' contains method call "
                    f"'{m.group(0).strip()}' — move to technical_notes."
                )
            elif _FILE_PATH_RE.search(when):
                m = _FILE_PATH_RE.search(when)
                _Log.warn(
                    f"AC quality [{mfu_id}/{ac_id}]: 'when' contains file path "
                    f"'{m.group(0).strip()}' — move to technical_notes."
                )


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------


def _safe_str(value: str) -> str:
    """
    Strip characters that would make a JSON string unparseable when the LLM
    echoes them back: ASCII control chars U+0000–U+001F (except tab/LF/CR
    which are common in source code and safe in prompt text), Unicode
    surrogates U+D800–U+DFFF, and Unicode 'control picture' symbols
    U+2400–U+243F (e.g. U+2426 ␦ SYMBOL FOR SUBSTITUTE) that some Windows
    terminals render as control characters.
    """
    result = []
    for ch in value:
        cp = ord(ch)
        # Keep normal printable ASCII + tabs/newlines/CR inside SRS content
        if cp < 0x20 and ch not in "\t\n\r":
            continue
        # Drop Unicode surrogates
        if 0xD800 <= cp <= 0xDFFF:
            continue
        # Drop "Symbols for Control Pictures" block (U+2400–U+243F)
        if 0x2400 <= cp <= 0x243F:
            result.append(" ")  # replace with space rather than drop silently
            continue
        result.append(ch)
    return "".join(result)


def _build_stage5a_prompt(
    template: str,
    srs_content: str,
    mfu_id: str,
    module_id: str,
    module_name: str,
    source_path: str,
    technology_tag: str,
    mfu_seq: str = "",
    module_context: dict | None = None,
    focal_anchor: str = "",
    focal_anchor_functions: str = "",
    language_hints: str = "",
) -> str:
    """
    Builds the Stage 5a Feature Manifest generator prompt.

    Layer 2 — Module Context Injection:
    When module_context is provided (from _load_module_context()), a [MODULE CONTEXT]
    block is prepended to the SRS content before placeholder substitution.  This block
    contains the refined DDD-quality module_name and business purpose description from
    Stage 2.7, giving the LLM authoritative top-down domain framing before it reads
    any SRS document.

    Combined with the [FOCAL ENTRYPOINT] / [SUPPORTING CONTEXT] markers added by
    _read_srs_content() (Layer 1), and DIRECTIVE 0 in the prompt template (Layer 3),
    this is the core of the DEFECT-2 three-layer enterprise fix.

    focal_anchor_functions (Layer 4 — Function Label Injection):
    Pre-extracted verbatim function labels from the linker index, injected via
    {FOCAL_ANCHOR_FUNCTIONS} placeholder in the prompt template.  Eliminates
    hallucinated CRUD labels.  Language-agnostic: works for UIBlueprint S.5 events
    (PB/VB6) and BatchBlueprint job_steps (COBOL/RPG/batch) identically.
    When empty, the placeholder is replaced with an LLM-derive fallback instruction.
    """
    # Layer 2: prepend [MODULE CONTEXT] block when refined manifest data is available
    if module_context and (module_context.get("module_name") or module_context.get("description")):
        ctx_name = _safe_str(module_context.get("module_name", ""))
        ctx_desc = _safe_str(module_context.get("description", ""))
        module_ctx_block = (
            f"[MODULE CONTEXT]\n"
            f"Module: {ctx_name}\n"
            f"Business Purpose: {ctx_desc}\n"
            f"[/MODULE CONTEXT]\n\n"
        )
        enriched_srs = module_ctx_block + srs_content
        _Log.observe(f"[MODULE CONTEXT] injected for {mfu_id}: module='{ctx_name}'")
    else:
        enriched_srs = srs_content

    prompt = template
    prompt = prompt.replace("{SRS_CONTENT}", enriched_srs)
    prompt = prompt.replace("{MFU_ID}", _safe_str(mfu_id))
    prompt = prompt.replace("{MFU_SEQ}", mfu_seq or _extract_mfu_seq(mfu_id))
    prompt = prompt.replace("{MODULE_ID}", _safe_str(module_id))
    prompt = prompt.replace("{MODULE_NAME}", _safe_str(module_name))
    prompt = prompt.replace("{SOURCE_PATH}", source_path)
    prompt = prompt.replace("{TECHNOLOGY_TAG}", technology_tag)
    # {FOCAL_ANCHOR}: injected from config_naming_map.json traceability_chain[0].
    # Prompt file contains the detailed behavioral rules for how to use this anchor;
    # Python only supplies the dynamic artifact name value.
    prompt = prompt.replace("{FOCAL_ANCHOR}", _safe_str(focal_anchor))
    # {FOCAL_ANCHOR_FUNCTIONS}: pre-extracted verbatim function labels from the linker.
    # Language-agnostic: S.5 events (UIBlueprint) OR BATCH::STEP-NNN (BatchBlueprint).
    # Built by _build_focal_anchor_functions_block(); fallback text when empty.
    prompt = prompt.replace("{FOCAL_ANCHOR_FUNCTIONS}", focal_anchor_functions)
    # {LANGUAGE_HINTS}: language-specific vocabulary constraints and Gherkin guidance.
    # Populated by Stage5ProfileRegistry from prompts/language_profiles.yaml.
    # No-op (empty string replace) when registry not loaded or tag not found.
    prompt = prompt.replace("{LANGUAGE_HINTS}", language_hints)
    return prompt


def _build_stage5b_prompt(
    template: str,
    feature_entry: dict,
    srs_zone_content: str,
    mfu_id: str,
    module_id: str,
    module_name: str,
    technology_tag: str,
    functions_ceiling: int,
    language_hints: str = "",
    anchor_map: str = "",
) -> str:
    prompt = template
    prompt = prompt.replace(
        "{FEATURE_ENTRY}", json.dumps(feature_entry, ensure_ascii=False, indent=2)
    )
    prompt = prompt.replace("{SRS_ZONE_CONTENT}", srs_zone_content)
    prompt = prompt.replace("{MFU_ID}", _safe_str(mfu_id))
    prompt = prompt.replace("{MODULE_ID}", _safe_str(module_id))
    prompt = prompt.replace("{MODULE_NAME}", _safe_str(module_name))
    prompt = prompt.replace("{TECHNOLOGY_TAG}", technology_tag)
    prompt = prompt.replace("{FUNCTIONS_CEILING}", str(functions_ceiling))
    # {LANGUAGE_HINTS}: language-specific vocabulary and Gherkin constraints.
    prompt = prompt.replace("{LANGUAGE_HINTS}", language_hints)
    # {SRS_ANCHOR_MAP}: valid SRS anchor constraints (Option B injection).
    # Built from feature manifest data (bounding_box.controls + functions[]).
    # Empty string when anchor_index is empty — no-op replace.
    prompt = prompt.replace("{SRS_ANCHOR_MAP}", anchor_map)
    return prompt


def _build_critic_prompt(
    template: str,
    srs_content: str,
    generator_output: dict,
    language_hints: str = "",
) -> str:
    prompt = template
    prompt = prompt.replace("{SRS_CONTENT}", srs_content)
    # Inject raw JSON — critic prompt NOTE says tags are NOT expected
    prompt = prompt.replace(
        "{GENERATOR_OUTPUT}", json.dumps(generator_output, ensure_ascii=False, indent=2)
    )
    prompt = prompt.replace("{LANGUAGE_HINTS}", language_hints)
    return prompt


def _hydrate_all_srs_evidence(features: list, mfu_dir, mfu_id: str) -> None:
    """
    Final deterministic pass: hydrates ``srs_evidence`` on every user story in
    every non-infrastructure feature using SRSPhysicalLinker.

    Called once in _run_stage5b after the initial generation AND all correction
    passes are complete, so both original and corrected/added stories are covered
    in a single traversal.

    Three-layer collection strategy (Option D):
    ─────────────────────────────────────────────────────────────────────────────
    Layer 1 — Story-specific (LLM-sourced):
        * story["l2_sources"]               — primary story refs
        * AC["l2_source_ref"] for each AC   — acceptance-criteria refs not yet seen

    Layer 2 — Feature-inherited (deterministic enrichment):
        * feature["bounding_box"]["s3_row_refs"]   — every S3 control on this screen
        * feature["l2_sources"]                     — all feature-level L2 IDs
        Only entries NOT already collected in Layer 1 are added.
        Each resolved entry is tagged source="inherited_from_feature" so the UI viewer
        and Stage 5c can distinguish story-specific from feature-level evidence.

    Layer 3 — Validation enrichment (deterministic):
        For every S3 control now in scope (Layers 1+2), resolve any S4 validation-rule
        entries in the linker index whose control name or exact_quote match the S3 control.
        Tagged source="inherited_from_feature".  Guarantees validation rules backing
        the Gherkin AC conditions are always traceable — regardless of whether the LLM
        cited them in l2_sources.
    ─────────────────────────────────────────────────────────────────────────────

    After hydration, _enrich_evidence_ac_ids() is called per story to back-fill
    evidence["ac_ids"] from the AC l2_source_ref values (Layer 1 entries only).

    Mutates features list in-place.  Never raises — on linker init failure all
    stories get an empty srs_evidence list and a WARN is logged.
    """
    try:
        from .srs_linker import SRSPhysicalLinker  # type: ignore

        linker = SRSPhysicalLinker(mfu_dir)
    except Exception as _init_err:
        _Log.warn(
            f"srs_evidence hydration ({mfu_id}): SRSPhysicalLinker unavailable — "
            f"srs_evidence[] will be empty. Reason: {_init_err}"
        )
        for feature in features:
            for story in feature.get("user_stories", []):
                story.setdefault("srs_evidence", [])
        return

    mfu_id_lower = mfu_id.lower()

    def _s4_for_s3_controls(s3_refs_in_scope: list) -> list:
        """
        Layer 3 helper: for each S3 control L2 ID in scope, return any S4
        validation-rule L2 IDs from the linker index that reference that control.

        Two lookup strategies (applied in order, deduplicated):
          a) Direct name match — `srs::{mfu}::s4::{ctrl_name}` exists in index.
          b) Quote scan       — any `srs::{mfu}::s4::val-*` entry whose exact_quote
                               contains the control name string (case-insensitive).
        """
        s4_candidates: list = []
        seen_s4: set = set()

        for s3_ref in s3_refs_in_scope:
            # Extract control name from e.g. "SRS::MFU-001::S3::sle_member_cd"
            parts = s3_ref.split("::")
            if len(parts) < 4:
                continue
            ctrl_name = parts[-1].lower()

            # Strategy (a): direct control-keyed S4 entry
            direct_key = f"srs::{mfu_id_lower}::s4::{ctrl_name}"
            if direct_key in linker.index and direct_key not in seen_s4:
                seen_s4.add(direct_key)
                canonical = f"SRS::{mfu_id}::S4::{ctrl_name}"
                s4_candidates.append(canonical)

            # Strategy (b): scan val-NNN-NN entries for control name in exact_quote
            for idx_key, idx_val in linker.index.items():
                if (
                    f"srs::{mfu_id_lower}::s4::val-" in idx_key
                    and idx_key not in seen_s4
                    and ctrl_name in idx_val.get("exact_quote", "").lower()
                ):
                    seen_s4.add(idx_key)
                    suffix = idx_key.split(f"srs::{mfu_id_lower}::s4::")[1]
                    canonical = f"SRS::{mfu_id}::S4::{suffix}"
                    s4_candidates.append(canonical)

        return s4_candidates

    total_entries = 0
    total_stories = 0
    l2_layer1_total = 0
    l2_layer2_total = 0
    l2_layer3_total = 0

    for feature in features:
        if feature.get("is_infrastructure"):
            continue

        # Feature-level pools for Layer 2 enrichment
        feat_l2s = feature.get("l2_sources", [])
        bbox_s3 = feature.get("bounding_box", {}).get("s3_row_refs", [])
        feat_pool = list(dict.fromkeys(bbox_s3 + feat_l2s))  # S3 refs first for ordering

        for story in feature.get("user_stories", []):
            # ── Layer 1: story-specific (LLM-sourced) ───────────────────────
            seen_l2: set = set()
            layer1: list = []
            for l2 in story.get("l2_sources", []):
                if l2 and l2 not in seen_l2:
                    seen_l2.add(l2)
                    layer1.append(l2)
            for ac in story.get("acceptance_criteria", []):
                ref = ac.get("l2_source_ref", "")
                if ref and ref not in seen_l2:
                    seen_l2.add(ref)
                    layer1.append(ref)

            # ── Layer 2: feature-inherited S3 + feature l2_sources ──────────
            layer2: list = []
            for l2 in feat_pool:
                if l2 and l2 not in seen_l2:
                    seen_l2.add(l2)
                    layer2.append(l2)

            # ── Layer 3: S4 validations for every S3 now in scope ───────────
            s3_in_scope = [l2 for l2 in (layer1 + layer2) if "::S3::" in l2 or "::s3::" in l2]
            layer3: list = []
            for l2 in _s4_for_s3_controls(s3_in_scope):
                if l2 and l2 not in seen_l2:
                    seen_l2.add(l2)
                    layer3.append(l2)

            # ── Resolve all layers to evidence dicts ─────────────────────────
            evidence: list = []
            for l2 in layer1:
                e = linker.resolve_l2_id(l2)
                e["tier"] = "L1"
                evidence.append(e)

            for l2 in layer2:
                e = linker.resolve_l2_id(l2)
                e["source"] = "inherited_from_feature"
                e["tier"] = "L2"
                evidence.append(e)

            for l2 in layer3:
                e = linker.resolve_l2_id(l2)
                e["source"] = "inherited_from_feature"
                e["tier"] = "L3"
                evidence.append(e)

            story["srs_evidence"] = evidence
            _enrich_evidence_ac_ids(story)

            n1, n2, n3 = len(layer1), len(layer2), len(layer3)
            total_entries += len(evidence)
            total_stories += 1
            l2_layer1_total += n1
            l2_layer2_total += n2
            l2_layer3_total += n3

            if n2 or n3:
                _Log.observe(
                    f"srs_evidence [{story.get('id', '?')}]: "
                    f"L1={n1} L2={n2}(feature-inherited) L3={n3}(S4-validation) "
                    f"= {len(evidence)} total entries."
                )

    _Log.act(
        f"srs_evidence hydration ({mfu_id}): "
        f"{total_entries} entries across {total_stories} stories "
        f"[L1={l2_layer1_total} story-specific, "
        f"L2={l2_layer2_total} feature-inherited, "
        f"L3={l2_layer3_total} S4-validation]."
    )


# ---------------------------------------------------------------------------
# Post-pass helpers (called from feature_story_agent.py correction loop)
# ---------------------------------------------------------------------------

# Regex covering common legacy UI control-name conventions.
# Mirrors the detection patterns in critic Gate 7d.
# NOTE: frm\w+ is intentionally EXCLUDED — form names are containers, not controls.
# VB6 event suffixes (_Click, _Change, etc.) are stripped post-match.
_CTRL_NAMES_RE = re.compile(
    r"\b("
    r"(?:dw|cb|sle|mle|em|ddlb|pb)_\w+"  # PowerBuilder prefixes
    r"|(?:cmd|txt|lst|chk|opt|lbl)\w+"  # VB6 control prefixes (frm excluded)
    r"|WS-[A-Z][A-Z0-9-]+"  # COBOL working-storage names
    r")\b",
    re.IGNORECASE,
)

# VB6 event-handler suffixes appended to control names (e.g. cmdRun_Click → cmdRun).
_VB6_EVENT_SUFFIX_RE = re.compile(
    r"_(?:Click|DblClick|Change|GotFocus|LostFocus|MouseUp|MouseDown|"
    r"KeyPress|KeyDown|KeyUp|Resize|Load|Unload|Activate|Deactivate|"
    r"Enter|Exit|Validate|Scroll|RowColChange|ItemChanged|SelectionChanged)$",
    re.IGNORECASE,
)


def _inject_bounding_box_controls_from_notes(
    feature: dict,
    mfu_id: str,
    mfu_dir=None,
) -> tuple:
    """Gate 7d post-pass (BUG-FIX-A).

    If ``bounding_box.controls[]`` is empty on a non-infrastructure feature,
    scan every story's ``technical_notes`` for UI control-name patterns and
    inject any matches into ``bounding_box.controls[]``.

    This is called after each corrector pass so the critic sees the fixed
    bounding box in subsequent iterations.  It is also called once after the
    correction loop exits as a safety net (handles FAIL_CORRECTION_EXHAUSTED).

    The corrector output schema only allows ``stories``, ``new_function_entries``
    and ``remove_story_ids`` — it has no key for the feature-level bounding_box.
    This deterministic pass bridges that architectural gap without an extra LLM
    call.

    Parameters
    ----------
    feature  : dict — the feature dict to inspect/update.
    mfu_id   : str  — MFU identifier (e.g. "MFU-006") used for log messages.
    mfu_dir  : Path | None — when provided the S.3 linker index is checked; if
               S.3 is explicitly empty (no rows) injection is skipped entirely.
               This prevents the Gate 7d post-pass from re-injecting hallucinated
               control names extracted from LLM-generated technical_notes on
               forms whose SRS S.3 section is absent or says "None".

    Returns
    -------
    (updated_feature, was_modified) : tuple[dict, bool]
    """
    if feature.get("is_infrastructure", False):
        return feature, False

    # Fix B — S.3 empty guard ─────────────────────────────────────────────────
    # When mfu_dir is provided we can confirm whether the SRS S.3 section
    # actually exists.  If S.3 is empty, any control names the LLM wrote into
    # technical_notes are hallucinated.  Re-injecting them here would restart
    # the Gate 7d critic loop indefinitely (the three-way contradiction bug).
    # Skipping injection on S.3-empty MFUs is the correct behaviour: the S.3
    # correction pass (_correct_bounding_box_controls) has already cleared
    # controls[] deterministically, and that cleared state should be preserved.
    if mfu_dir is not None:
        try:
            from .srs_linker import SRSPhysicalLinker  # type: ignore

            _linker_chk = SRSPhysicalLinker(mfu_dir)
            _s3_prefix = f"srs::{mfu_id.lower()}::s3::"
            _has_s3 = any(k.startswith(_s3_prefix) for k in _linker_chk.index)
            if not _has_s3:
                _Log.observe(
                    f"[Gate7d-postpass] {feature.get('id', mfu_id)}: "
                    f"S.3 index empty — skipping control injection to prevent "
                    f"Gate 7d contradiction loop."
                )
                return feature, False
        except Exception:
            pass  # linker unavailable → proceed with injection (safe fallback)

    bb = feature.get("bounding_box") or {}
    controls = bb.get("controls") or []
    if controls:
        return feature, False  # already populated — nothing to do

    found: list = []
    seen: set = set()
    for story in feature.get("user_stories") or []:
        notes = story.get("technical_notes") or ""
        for m in _CTRL_NAMES_RE.finditer(str(notes)):
            ctrl = _VB6_EVENT_SUFFIX_RE.sub("", m.group(1))  # strip _Click etc.
            key = ctrl.lower()
            if key not in seen:
                seen.add(key)
                found.append(ctrl)

    if not found:
        return feature, False

    new_bb = {**bb, "controls": found}
    _Log.ok(
        f"[Gate7d-postpass] {feature.get('id', '?')}: injected {len(found)} "
        f"control(s) from technical_notes into bounding_box.controls: {found}"
    )
    return {**feature, "bounding_box": new_bb}, True


def _fix_empty_l2_source_refs(
    feature: dict,
    mfu_id: str,
) -> tuple:
    """Gate 5f post-pass (BUG-FIX-B).

    For any AC with an empty ``l2_source_ref``, substitute the best available
    fallback anchor so traceability is not silently broken.

    Fallback resolution order:
      1. First control in ``bounding_box.controls`` → ``SRS::<mfu_id>::S3::<ctrl>``
         (S3 refs are always valid for Negative Path ACs per QC-NP exception).
      2. ``SRS::<mfu_id>::S5::EVENT-001`` — the primary event of the MFU.

    This handles the common SRS-gap pattern where a VB6 / legacy form has no
    explicit error-handling event, leaving the corrector unable to assign a
    valid anchor for Negative Path / Edge Case ACs after three attempts.

    Returns
    -------
    (updated_feature, was_modified) : tuple[dict, bool]
    """
    controls = (feature.get("bounding_box") or {}).get("controls") or []
    if controls:
        fallback_ref = f"SRS::{mfu_id}::S3::{controls[0]}"
    else:
        fallback_ref = f"SRS::{mfu_id}::S5::EVENT-001"

    modified = False
    updated_stories: list = []

    for story in feature.get("user_stories") or []:
        updated_acs: list = []
        for ac in story.get("acceptance_criteria") or []:
            ref = ac.get("l2_source_ref") or ""
            if not ref:
                ac = {**ac, "l2_source_ref": fallback_ref}
                modified = True
                _Log.ok(
                    f"[l2ref-postpass] {ac.get('id', '?')} "
                    f"[{ac.get('path_type', '?')}]: "
                    f"empty l2_source_ref filled with fallback '{fallback_ref}'"
                )
            updated_acs.append(ac)
        updated_stories.append({**story, "acceptance_criteria": updated_acs})

    if modified:
        feature = {**feature, "user_stories": updated_stories}
    return feature, modified


# ══════════════════════════════════════════════════════════════════════════════
# COMBINATION APPROACH — SRS Anchor Map (Option B) + Normalizer (Option C P1)
# ══════════════════════════════════════════════════════════════════════════════
#
# Architecture:
#   Option B — _build_srs_anchor_index + _build_srs_anchor_map_block
#              Pre-compute valid SRS anchors from the feature manifest and inject
#              as a constrained reference block into the story GENERATOR prompt.
#              The LLM sees only valid anchors → hallucination prevented at source.
#
#   Option C — _normalize_stories + _normalize_manifest
#              Deterministic post-generation normalizer. Runs after every LLM
#              output (generator and corrector passes) before the critic sees the
#              stories. Enforces mechanical rules that the LLM critic currently
#              handles unreliably, breaking the retry loop for RC-2 and RC-3.
#
# Language-agnostic: all logic operates on the JSON structure and standard
# SRS anchor ID format (SRS::{mfu_id}::{section}::{name}).
# Compatible with VB6, PowerBuilder, COBOL, and any future stack.
# ══════════════════════════════════════════════════════════════════════════════

# ── Event classification regexes ─────────────────────────────────────────────
_ERROR_SIGNAL_RE = re.compile(
    r"\b(error|invalid|fail|reject|not found|missing|exceed|unauthori[sz]|"
    r"timeout|duplicate|crash|exception|violat|cancel|abort|rollback|"
    r"incorrect|wrong|prohibit|deny|denied)\b",
    re.IGNORECASE,
)
_SUCCESS_SIGNAL_RE = re.compile(
    r"\b(success|complet|sav|creat|updat|delet|submit|export|generat|"
    r"open|load|show|display|navig|add|insert|register|confirm|approv)\b",
    re.IGNORECASE,
)
# Boundary / guard / limit signals. These describe negative or edge conditions
# (page boundaries, guards, limits, "no more data") that ARE valid anchors for
# Negative Path / Edge Case ACs — even when the event's primary verb is a success
# word like "navigate/page/display". Counted on the error/negative side so guard
# and boundary events classify as error_event or mixed_event (both valid on
# negative paths). This resolves the Critic⇄normalizer GAP-vs-EVENT oscillation at
# its root: e.g. a "PF7 Backward (guarded by page-num > 1)" event is no longer a
# pure success_event, so the normalizer keeps it as a valid negative-path anchor.
# NOTE: no trailing \b — matches stems/inflections (guard→guarded/guarding,
# boundar→boundary/boundaries), mirroring how a stem set should behave.
_BOUNDARY_SIGNAL_RE = re.compile(
    r"\b(guard|boundar|already at|top of (?:the )?page|bottom of (?:the )?page|"
    r"first page|last page|no more|no further|beyond|end of (?:file|list|data|page)|"
    r"out[- ]of[- ]range|out of bounds|overflow|underflow|"
    r"not allowed|cannot|unable to|no (?:records|rows|data|results|match))",
    re.IGNORECASE,
)
# Navigation / key-press / control-transfer signals. An event describing the user
# pressing a key or activating a navigation control (Back/Cancel/Escape, a PF/PA
# AID key, a CICS XCTL/transfer, "return to the previous screen") is an ALTERNATE-
# FLOW event that is a legitimate anchor for Negative Path / Edge Case ACs — even
# though its verb ("navigate", "display", "reload") also matches _SUCCESS_SIGNAL_RE.
# Counted on the negative/edge-valid side (same treatment as boundary signals) so
# such events classify as mixed_event / error_event instead of pure success_event.
# This resolves the second face of the Critic⇄normalizer oscillation (observed on
# CardDemo MFU-002): a "PF3 Back → XCTL" or "ENTER pressed → reload" event is no
# longer forcibly reverted off a key-press edge-case AC by Rule 2.
# MONOTONIC: this can only move an event success_event → mixed/error (Rule 2 fires
# strictly less often); it never introduces a new substitution. Word boundaries
# keep tokens tight (e.g. "\bpress" will NOT match "compress"/"pressure"). The
# Critic's Gate 9a remains the semantic grounding arbiter.
_NAV_SIGNAL_RE = re.compile(
    r"\b(?:"
    r"pf\d{1,2}|pa\d|aid|xctl|"  # 3270/CICS AID keys & transfer
    r"press(?:ed|es)?|keypress|keystroke|"  # key interaction
    r"transfer control|transferred? to|"  # control transfer
    r"return(?:s|ed)? to (?:the )?(?:prior|previous|calling|summary|list|menu)|"
    r"back to (?:the )?(?:prior|previous|summary|list|menu)|"
    r"navigate(?:s|d)? (?:back|to)|go(?:es)? back|"
    r"escape key|esc key|back button|cancel button|previous screen|prior screen"
    r")\b",
    re.IGNORECASE,
)

# Path types that require a non-success anchor
_NEGATIVE_PATH_TYPES = frozenset({"Negative Path", "Edge Case", "negative path", "edge case"})


def _classify_event_type(text: str) -> str:
    """
    Classify an S.5 event description as 'success_event', 'error_event', or 'mixed_event'.
    Used by _build_srs_anchor_index to distinguish anchors valid for Happy Path
    from those valid for Negative Path / Edge Case ACs.

    Classification rules:
      • error_event  — error/boundary signals present, no success signals
      • success_event — success signals present (or no signals at all → initialisation/neutral)
      • mixed_event  — both signal types present → valid for any AC path type

    Boundary/guard signals (page limits, guards, "no more data") count on the
    error/negative side, so guard events (e.g. paging with a top/bottom-of-page
    guard) are classified mixed_event or error_event and remain valid negative-path
    anchors rather than being forced to a GAP marker.
    """
    # Navigation / key-press (alternate-flow) events are valid anchors for ANY AC
    # path type — a Back/PF-key/transfer event legitimately appears on happy,
    # negative, and edge scenarios alike. Classify them mixed_event directly so
    # they are neither reverted off negative/edge ACs by Rule 2 nor mis-listed as
    # error-only in the generator's anchor-map prompt block.
    if _NAV_SIGNAL_RE.search(text):
        return "mixed_event"
    # has_error := any signal that makes the event valid for a Negative/Edge AC —
    # i.e. true error signals or boundary/guard signals. Combined with has_success
    # below this yields mixed_event (valid for any path) or error_event.
    has_error = bool(_ERROR_SIGNAL_RE.search(text)) or bool(_BOUNDARY_SIGNAL_RE.search(text))
    has_success = bool(_SUCCESS_SIGNAL_RE.search(text))
    if has_error and has_success:
        return "mixed_event"
    if has_error:
        return "error_event"
    return "success_event"  # default: no error signals → success / neutral


def _build_srs_anchor_index(feature: dict, mfu_id: str) -> dict:
    """
    Build a validated anchor index for a single feature entry.

    Sources (in priority order):
      1. bounding_box.controls  → ``SRS::{mfu_id}::S3::{control}`` → "control"
      2. functions[].l2_source_ref (S5 events) → classify via _classify_event_type
      3. feature.l2_sources (all feature-level refs) → type inferred from section segment

    Returns
    -------
    dict mapping fully-qualified ref ID → anchor type string:
        "control"       — S.3 physical control name; valid for ANY AC path type
        "success_event" — S.5 event describing a success / completion scenario
        "error_event"   — S.5 event describing an error / failure scenario
        "mixed_event"   — S.5 event with both signals; valid for any path type
        "functional"    — API endpoint, BATCH step, or other non-S5 functional ref
        "rule"          — S.4 validation rule
    """
    index: dict = {}

    # ── Layer 1: S3 controls from bounding_box ──────────────────────────────
    bbox_controls = (feature.get("bounding_box") or {}).get("controls") or []
    for ctrl in bbox_controls:
        ref = f"SRS::{mfu_id}::S3::{ctrl}"
        index[ref] = "control"

    # ── Layer 2: S5 events from functions[] ─────────────────────────────────
    for fn in feature.get("functions") or []:
        if not isinstance(fn, dict):
            # LLM occasionally emits plain string IDs instead of {id,label,l2_source_ref}
            # objects.  The sanitizer in feature_story_agent.py Stage 5a normally strips
            # these before _build_srs_anchor_index is called, but guard here defensively
            # so a missed sanitizer never causes an AttributeError downstream.
            continue
        ref = fn.get("l2_source_ref", "")
        if not ref:
            continue
        if "::S5::" in ref:
            # Classify using label + description text
            label_text = fn.get("label") or fn.get("function_label") or ""
            desc_text = fn.get("description") or ""
            index[ref] = _classify_event_type(f"{label_text} {desc_text}")
        elif "::S4::" in ref:
            index[ref] = "rule"
        else:
            # API endpoint, BATCH step, etc.
            index[ref] = "functional"

    # ── Layer 3: feature-level l2_sources (fills gaps not in functions[]) ───
    for ref in feature.get("l2_sources") or []:
        if ref in index:
            continue  # already classified by Layer 2
        if "::S3::" in ref:
            index[ref] = "control"
        elif "::S4::" in ref:
            index[ref] = "rule"
        elif "::S5::" in ref:
            index[ref] = "success_event"  # conservative default
        elif "::API::" in ref or "::BATCH::" in ref:
            index[ref] = "functional"

    return index


def _build_srs_anchor_map_block(anchor_index: dict, mfu_id: str) -> str:
    """
    Format an anchor index as a constrained reference block for injection into
    the story generator prompt (``{SRS_ANCHOR_MAP}`` placeholder).

    The block:
      • Lists every valid anchor with its type and usage guidance
      • Calls out SRS-gap situations (no error events) with an explicit fallback mandate
      • Uses unambiguous language so the LLM cannot misinterpret the constraint

    Language-agnostic: anchor IDs use the standard ``SRS::{mfu_id}::S{N}::{name}``
    format, compatible with any source technology.
    """
    if not anchor_index:
        return ""

    controls = [r for r, t in anchor_index.items() if t == "control"]
    error_events = [r for r, t in anchor_index.items() if t == "error_event"]
    mixed_events = [r for r, t in anchor_index.items() if t == "mixed_event"]
    success_events = [r for r, t in anchor_index.items() if t == "success_event"]
    functional = [r for r, t in anchor_index.items() if t == "functional"]
    rules = [r for r, t in anchor_index.items() if t == "rule"]

    lines: list[str] = []
    lines.append("════════════════════════════════════════════════════════════")
    lines.append("📌  SRS ANCHOR CONSTRAINTS  (CRITICAL — DO NOT DEVIATE)")
    lines.append("════════════════════════════════════════════════════════════")
    lines.append("The ONLY valid l2_source_ref values for this MFU are listed below.")
    lines.append("DO NOT reference any anchor not in this list.")
    lines.append("DO NOT invent new event IDs (EVENT-003, EVENT-004, etc.).")
    lines.append("")

    lines.append("VALID ANCHORS:")
    for ref in controls:
        lines.append(f"  ✔ {ref}   [control — valid for ANY AC path type]")
    for ref in error_events:
        lines.append(
            f"  ✔ {ref}   [event — error/failure — valid for Happy Path, Negative Path, Edge Case]"
        )
    for ref in mixed_events:
        lines.append(f"  ✔ {ref}   [event — mixed — valid for any AC path type]")
    for ref in success_events:
        lines.append(f"  ✔ {ref}   [event — success/completion — use for Happy Path ONLY]")
    for ref in functional:
        lines.append(
            f"  ✔ {ref}   [functional ref — include in l2_sources; may be used in AC refs]"
        )
    for ref in rules:
        lines.append(f"  ✔ {ref}   [validation rule — valid for any AC path type]")
    lines.append("")

    # SRS-gap notice: no error events and no mixed events available
    # Three tiers:
    #   Tier 1 — error/mixed events exist: normal guidance
    #   Tier 2 — no error events but controls exist: mandate control ref for neg paths
    #   Tier 3 — no error events AND no controls: total SRS gap → mandate GAP:: placeholder
    has_error_anchors = bool(error_events or mixed_events or rules)
    if not has_error_anchors:
        if controls:
            # Tier 2: at least one control anchor available
            lines.append(
                "⚠  SRS-GAP NOTICE: This SRS contains NO error events and NO validation rules."
            )
            lines.append(
                "   For ALL Negative Path and Edge Case ACs you MUST use a control anchor."
            )
            lines.append(f'  → REQUIRED l2_source_ref for Negative/Edge ACs: "{controls[0]}"')
            lines.append(
                f"  DO NOT use success events ({', '.join(success_events[:2])}) for Negative/Edge ACs."
            )
            lines.append("  (Gate 9b will FAIL any Negative Path AC anchored to a success event.)")
        else:
            # Tier 3: total SRS gap — no controls and no error anchors
            gap_ref = f"GAP::{mfu_id}::MISSING_ERROR_ANCHOR"
            lines.append(
                "⚠  TOTAL SRS-GAP NOTICE: This SRS has NO error events, NO validation rules,"
            )
            lines.append(
                "   and NO control anchors. There is NO valid negative-path anchor in this SRS."
            )
            lines.append(
                "   For ALL Negative Path and Edge Case ACs you MUST use a GAP:: placeholder ref."
            )
            lines.append(f'  → REQUIRED l2_source_ref for Negative/Edge ACs: "{gap_ref}"')
            lines.append(
                f"  DO NOT use success events ({', '.join(success_events[:2])}) for Negative/Edge ACs."
            )
            lines.append(
                "  DO NOT invent event IDs. GAP:: refs document missing SRS coverage intentionally."
            )
            lines.append("  (Gate 9b will FAIL any Negative Path AC anchored to a success event.)")
    else:
        lines.append("ℹ  Error/mixed events are available for Negative Path and Edge Case ACs.")
        lines.append("   Use success events for Happy Path ACs only.")

    lines.append("════════════════════════════════════════════════════════════")
    return "\n".join(lines)


def _try_step_hyphenate(ref: str, anchor_index: dict):
    """Resolve BATCH/S5 STEP ref format mismatches before GAP substitution.

    The linker indexes batch steps as BATCH::STEP-001 (hyphen, 3-digit zero-padded).
    The LLM corrector may generate BATCH::STEP01, BATCH::STEP1, or BATCH::STEP-01
    based on the critic's suggestion.  Try all plausible variants before giving up.

    Returns the canonical key from anchor_index if a variant matches, else None.
    """
    import re as _re

    m = _re.match(r"(.*?::(?:BATCH|S5)::STEP)(\d+)$", ref, _re.IGNORECASE)
    if not m:
        return None
    prefix, digits = m.group(1), m.group(2)
    n = int(digits)
    for fmt in (f"{prefix}-{n:03d}", f"{prefix}-{n:02d}", f"{prefix}-{n}"):
        if fmt in anchor_index:
            return fmt
        # Case-insensitive match (anchor_index keys may be lower-cased)
        fmt_lower = fmt.lower()
        match = next((k for k in anchor_index if k.lower() == fmt_lower), None)
        if match:
            return match
    return None


# Allowed top-level keys on a UserStory object — MUST mirror
# schemas/features_stories.schema.json → …user_stories.items.properties (which is
# additionalProperties:false). Used to deterministically strip stray LLM-emitted keys
# (e.g. 'priority', a story-level 'l2_source_ref') that pass the critic but hard-fail
# JSON-schema validation. Keep in sync if the schema's story properties change.
_ALLOWED_STORY_KEYS = frozenset(
    {
        "id",
        "title",
        "as_a",
        "i_want_to",
        "so_that",
        "story_points",
        "acceptance_criteria",
        "l2_sources",
        "srs_evidence",
        "technical_notes",
        "migration_hint",
        "jira_issue_key",
        "test_case_ids",
        "non_functional_requirements",
        "screens",
    }
)

# Keyword → NonFunctionalRequirement.category (schema enum). Used to coerce string-form NFRs
# the LLM occasionally emits into the required object shape. Order matters (first match wins);
# falls back to 'Usability' (a safe, always-valid enum value).
_NFR_CATEGORY_RULES = (
    (
        "Performance",
        ("second", "latency", "throughput", "load", "response time", "performance", "concurren"),
    ),
    (
        "Security",
        ("secur", "injection", "auth", "permission", "encrypt", "credential", "xss", "csrf"),
    ),
    (
        "DataIntegrity",
        ("integrity", "rollback", "transaction", "consisten", "atomic", "constraint", "validation"),
    ),
    ("Auditability", ("audit", "log", "trace", "history", "who changed")),
    ("Availability", ("availab", "uptime", "failover", "recover", "downtime")),
    ("Compliance", ("complian", "gdpr", "regulat", "retention", "legal", "privacy")),
    (
        "Usability",
        ("modal", "usab", "accessib", "interaction", "keyboard", "focus", "tooltip", "dialog"),
    ),
)


def _infer_nfr_category(text: str) -> str:
    """Map a free-text NFR statement to a valid NonFunctionalRequirement.category enum value.
    Keyword-based, language-agnostic-ish (matches the English NFR phrasing the generator uses);
    defaults to 'Usability' which is always a valid enum member."""
    low = (text or "").lower()
    for category, kws in _NFR_CATEGORY_RULES:
        if any(k in low for k in kws):
            return category
    return "Usability"


def _normalize_story_ids(feature_id: str, stories: list) -> tuple:
    """Renumber story ids that don't match the schema pattern ^{feature_id}-S\\d+$ (e.g. the
    corrector's split ids 'S3a','S3b','S1a') to the next free '-S{n}' integer, cascading the new
    prefix into each of that story's AC ids. Deterministic, language-neutral; conforming ids are
    left untouched and the pass is idempotent. Returns (stories, changed)."""
    if not feature_id:
        return stories, False
    valid_re = re.compile(rf"^{re.escape(feature_id)}-S(\d+)$")
    used: set = set()
    for s in stories:
        m = valid_re.match(s.get("id", "") or "")
        if m:
            used.add(int(m.group(1)))

    def _next_free() -> int:
        n = 1
        while n in used:
            n += 1
        used.add(n)
        return n

    changed = False
    out: list = []
    for s in stories:
        sid = s.get("id", "") or ""
        if valid_re.match(sid):
            out.append(s)
            continue
        new_id = f"{feature_id}-S{_next_free()}"
        new_acs = []
        for ac in s.get("acceptance_criteria") or []:
            acid = ac.get("id", "") or ""
            if sid and acid.startswith(sid):
                ac = {**ac, "id": new_id + acid[len(sid) :]}
            new_acs.append(ac)
        out.append({**s, "id": new_id, "acceptance_criteria": new_acs})
        changed = True
        _Log.warn(
            f"[NORM] story id '{sid}' → '{new_id}' (non-conforming split id; "
            f"schema requires ^…-F#-S<digits>$; AC ids re-prefixed)."
        )
    return out, changed


def _normalize_stories(
    feature: dict,
    mfu_id: str,
    anchor_index: dict,
    srs_type: str = "",
) -> tuple:
    """
    Deterministic post-generation story normalizer (Option C — P1 rules).

    Runs after every LLM output (generator and corrector passes) BEFORE the
    critic evaluates the stories. Enforces the two highest-impact mechanical
    rules that the LLM critic / corrector currently handles unreliably.

    P1 Rule 1 — l2_source_ref validity:
        If an AC's l2_source_ref is not in the anchor_index (fabricated, invalid,
        or referencing a neighbouring MFU), substitute the best available fallback.

    P1 Rule 2 — Negative-path anchor type:
        If a Negative Path or Edge Case AC references a success_event anchor,
        substitute the best S3 control ref (always valid for negative paths per
        the QC-NP exception in the critic prompt).

    Fallback priority:
        1. First "control" in anchor_index   (S3 refs — unconditionally valid)
        2. First "error_event" in anchor_index
        3. First "mixed_event" in anchor_index
        4. First "rule" in anchor_index
        5. GAP::{mfu_id}::MISSING_ERROR_ANCHOR  (last resort — no valid anchor in SRS;
           passes through normalizer Rule 1 unchanged)

    Returns
    -------
    (updated_feature, was_modified) : tuple[dict, bool]
    """
    if not anchor_index:
        return feature, False  # no index → cannot validate → skip safely

    # Pre-compute GLOBAL fallback refs (used only as a last-resort safety net for
    # Happy-Path ACs with fabricated refs — NOT for Negative/Edge substitution).
    _controls = [r for r, t in anchor_index.items() if t == "control"]
    _err_events = [r for r, t in anchor_index.items() if t == "error_event"]
    _mixed_events = [r for r, t in anchor_index.items() if t == "mixed_event"]
    _rules = [r for r, t in anchor_index.items() if t == "rule"]

    # ── A+C fix: story-SCOPED, GAP-honest fallback selection ──────────────────
    # Old behaviour returned the GLOBAL first control (_controls[0]) for EVERY
    # negative/edge substitution, so one unrelated control (e.g. cb_update /
    # menuInsertPos) was stamped onto ~half of all ACs — meaningless traceability.
    #
    #   A — Story-scoped: prefer an anchor the CURRENT story already references,
    #       so the negative/edge AC traces to its own feature surface
    #       (Save→cb_update, Print→cb_print, …) instead of a global default.
    #   C — GAP-honest: when the story has no contextually-valid negative anchor,
    #       emit a GAP:: marker (critic-valid for Negative/Edge ACs) rather than a
    #       misleading global control.
    #
    # (B — mapping a success_event to its own S.5 triggering control — is NOT done
    #  here; it needs a srs_linker change and is tracked as a separate task.)
    def _story_local(story_refs: list, *types: str) -> list:
        """Refs within THIS story whose anchor_index type is one of `types`, in order."""
        return [r for r in story_refs if r and anchor_index.get(r) in types]

    def _best_negative_fallback(story_refs: list) -> str:
        """Best Negative/Edge anchor — story-scoped, then GAP-honest. No global control."""
        local_controls = _story_local(story_refs, "control")
        if local_controls:
            return local_controls[0]  # A: the story's own control
        local_neg = _story_local(story_refs, "error_event", "mixed_event")
        if local_neg:
            return local_neg[0]  # A: the story's own error/mixed event
        return f"GAP::{mfu_id}::NO_NEG_ANCHOR"  # C: honest gap, never a global control

    def _best_generic_fallback(story_refs: list) -> str:
        """Fallback for a fabricated ref on a (typically Happy-Path) AC.
        Prefer a story-local valid anchor; keep the global net as last resort
        because GAP:: is only critic-valid for Negative/Edge ACs."""
        local_valid = [r for r in story_refs if r and r in anchor_index]
        if local_valid:
            return local_valid[0]  # A: any anchor the story already uses
        if _controls:
            return _controls[0]  # global safety net (Happy-Path only)
        if _err_events:
            return _err_events[0]
        if _mixed_events:
            return _mixed_events[0]
        if _rules:
            return _rules[0]
        return f"GAP::{mfu_id}::NO_SRS_ANCHOR"

    modified = False
    updated_stories: list = []

    for story in feature.get("user_stories") or []:
        # Review-fix C1: operate on a copy so the sanitiser/coercion below never mutates
        # the caller's story dicts in place (they alias into current_features /
        # populated_features). Shallow-copy the dict, and copy the l2_sources list before
        # we append to it, so no shared nested list is mutated either.
        story = dict(story)
        if isinstance(story.get("l2_sources"), list):
            story["l2_sources"] = list(story["l2_sources"])
        # ── Schema-conformance sanitiser ──────────────────────────────────────
        # The generator/corrector LLM occasionally emits story-level keys absent from the
        # schema (user_story is additionalProperties:false) — most commonly 'priority' and a
        # story-level 'l2_source_ref' (that field belongs on each AC, not the story). These
        # pass the critic's gates but hard-fail JSON-schema validation → FAIL_SCHEMA_INVALID
        # (the KEIJYO/001 failure). Strip unknown top-level keys here so the story conforms by
        # construction. Traceability preserved: fold a stray story-level l2_source_ref into
        # l2_sources before dropping it.
        _extra_keys = [k for k in list(story.keys()) if k not in _ALLOWED_STORY_KEYS]
        if _extra_keys:
            _stray_ref = story.get("l2_source_ref")
            if _stray_ref:
                _ls = story.setdefault("l2_sources", [])
                if isinstance(_ls, list) and _stray_ref not in _ls:
                    _ls.append(_stray_ref)
            for _k in _extra_keys:
                story.pop(_k, None)
            modified = True
            _Log.warn(
                f"[NORM] {story.get('id', '?')}: dropped non-schema story key(s) "
                f"{_extra_keys} (schema is additionalProperties:false). "
                f"Any stray l2_source_ref folded into l2_sources."
            )

        # ── NFR shape coercion ────────────────────────────────────────────────
        # non_functional_requirements items must be objects {id, category, requirement}
        # (schema definition NonFunctionalRequirement, additionalProperties:false), but the
        # LLM sometimes emits plain strings (the MOD-MAIL/001 FAIL_SCHEMA_INVALID:
        # "'The popup must be modal …' is not of type 'object'"). Coerce string items into the
        # object shape — preserving the requirement text and inferring a valid category — so
        # the requirement is kept rather than lost, and the output validates.
        _nfrs = story.get("non_functional_requirements")
        # Top-level type coercion: the schema requires an ARRAY, but the LLM / Stage-5b+
        # correction pass sometimes emits a bare string ("" or a sentence) or null instead
        # of a list → FAIL_SCHEMA_INVALID ("'' is not of type 'array'"). Coerce to a list
        # first (empty string / null → []; non-empty string → single-item list) so the
        # item-level coercion below still applies and the output always validates.
        if not isinstance(_nfrs, list):
            _nfrs = [_nfrs.strip()] if isinstance(_nfrs, str) and _nfrs.strip() else []
            story["non_functional_requirements"] = _nfrs
            modified = True
        if isinstance(_nfrs, list) and any(not isinstance(x, dict) for x in _nfrs):
            _sid = story.get("id", mfu_id)
            _coerced = []
            for _i, _item in enumerate(_nfrs, 1):
                if isinstance(_item, dict):
                    _coerced.append(_item)
                    continue
                _txt = str(_item).strip()
                if not _txt:
                    continue
                _coerced.append(
                    {
                        "id": f"{_sid}-NFR{_i}",
                        "category": _infer_nfr_category(_txt),
                        "requirement": _txt[:1000],
                        "derived_from": "implied_gap",
                    }
                )
            story["non_functional_requirements"] = _coerced
            modified = True
            _Log.warn(
                f"[NORM] {story.get('id', '?')}: coerced {sum(1 for x in _nfrs if not isinstance(x, dict))} "
                f"string non_functional_requirement(s) into schema object shape."
            )

        # Anchors this story already references — drives the story-scoped fallback (A).
        _story_refs = [
            (a.get("l2_source_ref") or "") for a in (story.get("acceptance_criteria") or [])
        ]
        updated_acs: list = []
        for ac in story.get("acceptance_criteria") or []:
            ref = ac.get("l2_source_ref") or ""
            path_type = ac.get("path_type", "")
            ac_id = ac.get("id", "?")
            new_ref = ref

            # ── GAP:: exemption: explicit SRS-gap markers pass through both rules ──
            # GAP:: refs document intentional SRS coverage gaps (e.g., no error event
            # exists in the SRS for this scenario). They are not in the anchor index by
            # design and must NOT be substituted — doing so would cause a Gate 9b loop.
            if ref.startswith("GAP::"):
                updated_acs.append(ac)
                continue

            # ── BatchBlueprint S3:: ref guard ──────────────────────────────────
            # BatchBlueprint SRS files have no S.3 section by definition.
            # Any l2_source_ref of the form SRS::{mfu_id}::S3::* in a BatchBlueprint
            # AC is an LLM hallucination (often inherited from companion UIBlueprint
            # controls injected into the anchor_index).  Replace with a GAP:: marker
            # so the critic accepts it and the SRS gap is documented.
            if srs_type == "BatchBlueprint" and ref and f"::{mfu_id.lower()}::s3::" in ref.lower():
                # Preserve the original field name for traceability — the last
                # segment of the S3:: ref (e.g. "systemFaultError") identifies
                # which field the LLM was trying to reference.  This makes it
                # straightforward to audit which SRS gaps need remediation.
                _orig_field = ref.split("::")[-1] if "::" in ref else ref
                _gap_ref = f"GAP::{mfu_id}::No_S3_in_BatchBlueprint::{_orig_field}"
                _Log.warn(
                    f"[NORM] {ac_id} [{path_type}]: BatchBlueprint AC has invalid "
                    f"S3:: ref '{ref}' → replaced with GAP marker '{_gap_ref}'"
                )
                ac = {**ac, "l2_source_ref": _gap_ref}
                modified = True
                updated_acs.append(ac)
                continue

            # ── Rule 1: ref not in anchor index ─────────────────────────────
            if ref and ref not in anchor_index:
                # ── Hyphen-variant resolution (BATCH::STEP format drift) ──
                # Try BATCH::STEP01 → BATCH::STEP-001 before generic fallback.
                # The corrector may omit the hyphen or zero-padding when the
                # critic suggests the format; the linker always indexes with hyphen.
                hyphenated = _try_step_hyphenate(ref, anchor_index)
                if hyphenated:
                    new_ref = hyphenated
                    modified = True
                    _Log.ok(
                        f"[NORM] {ac_id} [{path_type}]: l2_source_ref '{ref}' "
                        f"hyphen-normalised to '{hyphenated}' "
                        f"(reason: step_format_mismatch)"
                    )
                else:
                    fallback = (
                        _best_negative_fallback(_story_refs)
                        if path_type in _NEGATIVE_PATH_TYPES
                        else _best_generic_fallback(_story_refs)
                    )
                    new_ref = fallback
                    modified = True
                    _Log.ok(
                        f"[NORM] {ac_id} [{path_type}]: l2_source_ref '{ref}' "
                        f"not in SRS anchor index → substituted '{fallback}' "
                        f"(reason: ref_not_in_index)"
                    )

            # ── Rule 2: success event anchoring a negative-path AC ───────────
            elif (
                ref
                and path_type in _NEGATIVE_PATH_TYPES
                and anchor_index.get(ref) == "success_event"
            ):
                fallback = _best_negative_fallback(_story_refs)
                if fallback != ref:
                    new_ref = fallback
                    modified = True
                    _Log.ok(
                        f"[NORM] {ac_id} [{path_type}]: l2_source_ref '{ref}' "
                        f"is a success_event — invalid for negative/edge path "
                        f"→ substituted '{fallback}' (reason: success_anchor_on_neg_path)"
                    )

            if new_ref != ref:
                ac = {**ac, "l2_source_ref": new_ref}
            updated_acs.append(ac)

        updated_stories.append({**story, "acceptance_criteria": updated_acs})

    # ── Story-ID conformance pass ─────────────────────────────────────────────
    # When the corrector SPLITS a story to satisfy Gate 6c (e.g. "split S1 into separate
    # intents"), it tends to mint sub-lettered ids ('S3a','S3b','S1a','S1b') that violate the
    # schema story-id pattern ^…-F\d+-S\d+$ → FAIL_SCHEMA_INVALID (TANA/010) or a malformed
    # split the critic can't accept (PDQS/001). Deterministically renumber any non-conforming
    # id to the next free -S{n} integer and cascade the new prefix into its AC ids. Language-
    # neutral; only touches non-conforming ids (conforming S1/S2/… are untouched).
    updated_stories, _ids_changed = _normalize_story_ids(feature.get("id", ""), updated_stories)
    if _ids_changed:
        modified = True

    if modified:
        feature = {**feature, "user_stories": updated_stories}
    return feature, modified


def _normalize_manifest(
    manifest: dict,
    mfu_id: str,
    focal_srs_files: list,
    functional_api_types: set[str],
) -> tuple:
    """
    Deterministic manifest normalizer (Option C — P1 Rule 3 / RC-3 fix).

    Fixes the deterministic scaffold gap: when both Stage 5a LLM calls fail,
    _build_manifest_scaffold sets is_infrastructure=(no S.5 events), which is
    True for APIContracts modules (headless-functional modules have no S.5 events
    by design). P10-guard explicitly skips det-fallback manifests, leaving no
    mechanism to flip is_infrastructure back to False for these modules.

    This normalizer runs AFTER P2 and P10 guards. For det-fallback manifests
    only: checks focal SRS file types against functional_api_types. If any SRS
    file in the MFU resolves to a functional_api_types type, overrides
    is_infrastructure=False and category="Functional" for all features.

    Non-det-fallback manifests: no-op (P10-guard already handled them correctly).

    Parameters
    ----------
    manifest : dict
        Parsed Stage 5a manifest.
    mfu_id : str
        Module Feature Unit identifier (for logging).
    focal_srs_files : list
        List of pathlib.Path objects for all SRS files in the MFU.
    functional_api_types : set[str]
        Configurable set of SRS types treated as functional (headless).

    Returns
    -------
    (updated_manifest, was_modified) : tuple[dict, bool]
    """
    # Only apply to det-fallback manifests — LLM manifests are already correct.
    is_det_fallback = (
        manifest.get("generation_metadata", {}).get("final_status") == "PASS_DETERMINISTIC_FALLBACK"
    )
    if not is_det_fallback:
        return manifest, False

    # Check if ANY focal SRS file resolves to a functional_api_types type
    is_functional_api = False
    matched_type = ""
    matched_file = ""
    for srs_path in focal_srs_files or []:
        fname = srs_path.name if hasattr(srs_path, "name") else str(srs_path)
        srs_type = _detect_srs_document_type(fname)
        if srs_type in functional_api_types:
            is_functional_api = True
            matched_type = srs_type
            matched_file = fname
            break

    if not is_functional_api:
        return manifest, False

    # Override is_infrastructure for all features in the det-fallback manifest
    modified = False
    for feat in manifest.get("features", []):
        if feat.get("is_infrastructure"):
            feat["is_infrastructure"] = False
            feat["category"] = "Functional"
            modified = True
            _Log.warn(
                f"[_normalize_manifest] {feat.get('id', mfu_id)}: "
                f"det-fallback manifest — overriding is_infrastructure=True→False "
                f"(focal SRS '{matched_file}' is functional_api_type '{matched_type}')."
            )

    return manifest, modified


# ---------------------------------------------------------------------------
# Gate 7c post-pass -- strip invalid / sentinel control names
# ---------------------------------------------------------------------------

_INVALID_CTRL_VALUES = frozenset({"none", "null", "n/a", "undefined", "unknown", ""})


def _strip_invalid_controls(feature: dict, mfu_id: str) -> tuple:
    """Gate 7c post-pass -- remove sentinel / invalid values from bounding_box.controls[].

    Strips "None", "null" and TRCE-* artefacts that the deterministic scaffold
    or LLM can inject.  Gate 7c always rejects them; this pass removes them
    deterministically so the corrector loop is not wasted.
    """
    bb = feature.get("bounding_box") or {}
    controls = bb.get("controls", []) or []
    s3_refs = bb.get("s3_row_refs", []) or []

    clean_controls = []
    clean_refs = []
    stripped = []

    for ctrl, ref in zip(controls, s3_refs, strict=False):
        val = (ctrl or "").strip()
        if val.lower() in _INVALID_CTRL_VALUES or val.upper().startswith("TRCE-"):
            stripped.append(repr(ctrl))
        else:
            clean_controls.append(ctrl)
            clean_refs.append(ref)

    # Handle controls[] longer than s3_row_refs[] (defensive).
    # Append a synthetic ref to keep controls[] / s3_row_refs[] in sync so
    # downstream parallel-iteration (zip) is never mis-aligned.
    for ctrl in controls[len(s3_refs) :]:
        val = (ctrl or "").strip()
        if val.lower() in _INVALID_CTRL_VALUES or val.upper().startswith("TRCE-"):
            stripped.append(repr(ctrl))
        else:
            clean_controls.append(ctrl)
            clean_refs.append(f"SRS::{mfu_id}::S3::{ctrl}")

    if not stripped:
        return feature, False

    _Log.warn(
        f"[Gate7c-postpass] {feature.get('id', mfu_id)}: stripped "
        f"{len(stripped)} invalid control(s) from bounding_box: {stripped}"
    )
    new_bb = {**bb, "controls": clean_controls, "s3_row_refs": clean_refs}
    return {**feature, "bounding_box": new_bb}, True


def _fix_zone_id(
    feature: dict,
    mfu_id: str,
    srs_type: str,
) -> tuple:
    """Gate 7b post-pass -- correct invalid bounding_box.zone_id values.

    Valid zone_id values are FULL_SCREEN (all UI-bearing SRS types) and
    API_LAYER (API, Batch, and other headless SRS types).  The LLM sometimes
    emits zone_id='' or zone_id='UNKNOWN' for batch features; this pass
    replaces those with the canonical value derived from srs_type.

    Parameters
    ----------
    feature : dict
        Individual feature dict from the manifest.
    mfu_id : str
        Module Feature Unit identifier (for logging).
    srs_type : str
        Resolved SRS document type (e.g. "BatchBlueprint", "UIBlueprint").

    Returns
    -------
    (updated_feature, was_modified) : tuple[dict, bool]
    """
    bb = feature.get("bounding_box") or {}
    current_zone = bb.get("zone_id", "")
    # Note: "BatchSpec" is intentionally absent.  _detect_srs_document_type
    # never returns that string — BATCHSPEC files are classified as
    # "BatchBlueprint".  Keeping a dead entry here would mask future bugs.
    # "BatchSpec" removed — see _detect_srs_document_type in srs_linker.py.
    _API_TYPES = {"APIContracts", "BatchBlueprint"}
    canonical = "API_LAYER" if srs_type in _API_TYPES else "FULL_SCREEN"
    _VALID_ZONES = {"FULL_SCREEN", "API_LAYER"}
    if current_zone in _VALID_ZONES:
        return feature, False
    new_bb = {**bb, "zone_id": canonical}
    _Log.warn(
        f"[Gate7b-postpass] {feature.get('id', mfu_id)}: "
        f"bounding_box.zone_id '{current_zone}' -> '{canonical}' "
        f"(srs_type={srs_type or 'unknown'})"
    )
    return {**feature, "bounding_box": new_bb}, True


# ---------------------------------------------------------------------------
# Gate 7e — NFR field-name normalisation
# ---------------------------------------------------------------------------
# The corrector LLM (and occasionally the generator) sometimes emits NFR
# objects using alternative field names that do not match the JSON schema:
#
#   quality_attribute  ->  category      (canonical)
#   criterion          ->  requirement   (canonical)
#   type               ->  category      (secondary alias)
#   description        ->  requirement   (secondary alias)
#   text               ->  requirement   (tertiary alias)
#
# The schema also constrains ``category`` to an enum; values outside the set
# are remapped to the closest match or the object is dropped.
# ---------------------------------------------------------------------------

_NFR_VALID_CATEGORIES = frozenset(
    {
        "Security",
        "Performance",
        "DataIntegrity",
        "Auditability",
        "Availability",
        "Compliance",
        "Usability",
    }
)

# Keyword -> canonical category mapping for unrecognised values.
_NFR_CATEGORY_MAP: dict = {
    "perf": "Performance",
    "perform": "Performance",
    "speed": "Performance",
    "latency": "Performance",
    "throughput": "Performance",
    "sec": "Security",
    "secure": "Security",
    "auth": "Security",
    "integrity": "DataIntegrity",
    "data": "DataIntegrity",
    "audit": "Auditability",
    "trace": "Auditability",
    "log": "Auditability",
    "avail": "Availability",
    "uptime": "Availability",
    "reliab": "Availability",
    "comply": "Compliance",
    "regulat": "Compliance",
    "legal": "Compliance",
    "usab": "Usability",
    "ux": "Usability",
    "accessib": "Usability",
}


def _normalize_nfr_fields(feature: dict, mfu_id: str) -> tuple:
    """Gate 7e post-pass -- rename aliased NFR field names and clamp
    ``category`` to the valid schema enum.

    Handles these LLM alias patterns:

    * ``quality_attribute`` / ``type`` / ``attribute``  ->  ``category``
    * ``criterion`` / ``description`` / ``text`` / ``statement``  ->  ``requirement``

    NFR objects that remain missing ``id``, ``category``, or ``requirement``
    after normalisation are dropped (to avoid writing schema-invalid JSON).

    Parameters
    ----------
    feature : dict
        Individual feature dict containing ``user_stories[].non_functional_requirements``.
    mfu_id : str
        MFU identifier (used in log messages only).

    Returns
    -------
    (updated_feature, was_modified) : tuple[dict, bool]
    """
    stories = feature.get("user_stories", [])
    if not stories:
        return feature, False

    modified = False
    new_stories = []

    for story in stories:
        nfrs = story.get("non_functional_requirements")
        if not nfrs:
            new_stories.append(story)
            continue

        new_nfrs = []
        nfr_dirty = False

        for nfr in nfrs:
            if not isinstance(nfr, dict):
                new_nfrs.append(nfr)
                continue

            nfr = dict(nfr)  # shallow copy -- safe to mutate

            # -- Rename aliased category field --------------------------------
            if "category" not in nfr:
                for alias in ("quality_attribute", "type", "attribute"):
                    if alias in nfr:
                        nfr["category"] = nfr.pop(alias)
                        nfr_dirty = True
                        break

            # -- Rename aliased requirement field -----------------------------
            if "requirement" not in nfr:
                for alias in ("criterion", "description", "text", "statement"):
                    if alias in nfr:
                        nfr["requirement"] = nfr.pop(alias)
                        nfr_dirty = True
                        break

            # -- Clamp category to valid enum ---------------------------------
            cat = nfr.get("category", "")
            if cat not in _NFR_VALID_CATEGORIES:
                cat_lower = cat.lower()
                mapped = None
                for valid in _NFR_VALID_CATEGORIES:
                    if valid.lower() == cat_lower:
                        mapped = valid
                        break
                if mapped is None:
                    for kw, canonical in _NFR_CATEGORY_MAP.items():
                        if kw in cat_lower:
                            mapped = canonical
                            break
                if mapped:
                    nfr["category"] = mapped
                    nfr_dirty = True
                    _Log.warn(
                        f"[Gate7e-NFR] {story.get('id', mfu_id)}: "
                        f"NFR category '{cat}' remapped to '{mapped}'."
                    )
                else:
                    _Log.warn(
                        f"[Gate7e-NFR] {story.get('id', mfu_id)}: "
                        f"NFR id='{nfr.get('id', '?')}' has unrecognisable "
                        f"category='{cat}' -- dropped."
                    )
                    nfr_dirty = True
                    continue  # skip append

            # -- Drop NFRs still missing required fields ----------------------
            if not nfr.get("id") or not nfr.get("category") or not nfr.get("requirement"):
                _Log.warn(
                    f"[Gate7e-NFR] {story.get('id', mfu_id)}: "
                    f"NFR id='{nfr.get('id', '?')}' still missing required "
                    f"field(s) after normalisation -- dropped."
                )
                nfr_dirty = True
                continue

            new_nfrs.append(nfr)

        if nfr_dirty:
            story = {**story, "non_functional_requirements": new_nfrs}
            modified = True

        new_stories.append(story)

    if modified:
        _Log.ok(f"[Gate7e-NFR] {feature.get('id', mfu_id)}: NFR field normalisation applied.")
        return {**feature, "user_stories": new_stories}, True
    return feature, False


# ────────────────────────────────────────────────────────────────────────────
# #42 edit-mode deterministic preservation (shared by CLI `revise` and the
# generator's pre-critic merge). Edit mode changes human-readable text only;
# every other field is derived/structural and is preserved from the pre-edit
# version so it cannot drift or degrade. (Reviewer feedback in the pipeline is
# attached to story/AC text; to re-derive NFRs, anchors, hints, etc. use
# regenerate.)
# ────────────────────────────────────────────────────────────────────────────
_STORY_EDITABLE = {
    "title",
    "as_a",
    "i_want_to",
    "so_that",
    "description",
    "technical_notes",
    "acceptance_criteria",
}
_AC_EDITABLE = {"given", "when", "then", "path_type", "title", "description"}


def _restore_derived_fields(doc: dict, old_feats: list, target_ids=None, reverted=None) -> int:
    """Preserve-by-default. For any story/AC that still matches the backup by id, restore EVERY
    field except the human-editable prose fields above. This stops derived/structural fields —
    grounding anchors (l2_source_ref / l2_sources), non_functional_requirements, migration_hint,
    story_points, screen_id, and any field added in future — from drifting or degrading when the
    reviewer only edited wording. Added stories/ACs (no backup match) are left as generated.
    Returns the number of fields restored.

    #42-transparency: when ``target_ids`` (the reviewer-selected stories) and a ``reverted``
    list are supplied, any restore that OVERWRITES a change the model made on a TARGET story is
    also appended to ``reverted`` as "STORY_ID.field" / "STORY_ID/AC_ID.field". Edit mode preserves
    derived/structural fields by design, so a reviewer change to such a field is intentionally NOT
    applied — recording it lets the caller tell the reviewer instead of dropping it silently.
    These two params are optional and default to the previous behaviour exactly (no reporting)."""
    import copy

    target_ids = set(target_ids or [])
    old_story = {}
    for f in old_feats or []:
        for s in f.get("user_stories") or []:
            old_story[s.get("id")] = s
    restored = 0
    for f in doc.get("features") or []:
        for s in f.get("user_stories") or []:
            ob = old_story.get(s.get("id"))
            if not ob:
                continue
            _is_target = s.get("id") in target_ids
            for k, v in ob.items():
                if k in _STORY_EDITABLE:
                    continue
                if s.get(k) != v:
                    if _is_target and reverted is not None:
                        reverted.append(f"{s.get('id')}.{k}")
                    s[k] = copy.deepcopy(v)
                    restored += 1
            oac = {a.get("id"): a for a in (ob.get("acceptance_criteria") or [])}
            for a in s.get("acceptance_criteria") or []:
                oa = oac.get(a.get("id"))
                if not oa:
                    continue
                for k, v in oa.items():
                    if k in _AC_EDITABLE:
                        continue
                    if a.get(k) != v:
                        if _is_target and reverted is not None:
                            reverted.append(f"{s.get('id')}/{a.get('id')}.{k}")
                        a[k] = copy.deepcopy(v)
                        restored += 1
    return restored


def _reconcile_edit(new_doc: dict, old_doc: dict, target_ids=None):
    """Deterministically ENFORCE edit-mode preservation against the pre-edit backup.

    The generator re-emits the whole document, so a prompt instruction to "preserve the
    rest" is only a hope — fields the reviewer never touched can still drift. This rebuilds
    the result from the KNOWN-GOOD backup (old_doc) and lets only the reviewer-selected
    content change:

      * target_ids given -> STRONG guarantee. Base = backup; for each feature, targeted
        stories are swapped in from new_doc and EVERY other story + all feature-level
        fields are copied verbatim from the backup. Model drift on untouched stories is
        discarded. (target comes from the UI node the reviewer selected — the pipeline's
        basket item nodeId — so we never have to guess the target from free text.)
      * target_ids empty -> BEST-EFFORT. Keep the model's text but restore derived/structural
        fields on any story/AC whose id still matches the backup, so anchors/NFRs/hints
        cannot silently drift. (Used for ad-hoc CLI runs / feature-wide feedback.)

    Returns (reconciled_doc, notes). Never raises — on malformed input it returns new_doc.
    """
    import copy

    notes = []
    try:
        if not (isinstance(new_doc, dict) and isinstance(old_doc, dict)):
            return new_doc, ["skipped: non-dict input"]
        new_feats = new_doc.get("features") or []
        old_feats = old_doc.get("features") or []
        if not new_feats or not old_feats:
            return new_doc, ["skipped: missing features (kept generator output)"]

        target_ids = set(target_ids or [])

        if target_ids:
            recon = copy.deepcopy(old_doc)
            if "generation_metadata" in new_doc:  # carry the fresh run's status/critic verdict
                recon["generation_metadata"] = copy.deepcopy(new_doc["generation_metadata"])
            for rf in recon.get("features", []):
                fid = rf.get("id")
                nf = next((f for f in new_feats if f.get("id") == fid), None)
                if nf is None and len(new_feats) == 1:
                    nf = new_feats[0]
                if nf is None:
                    notes.append(f"{fid}: no matching new feature; kept backup")
                    continue
                new_by_id = {s.get("id"): s for s in (nf.get("user_stories") or [])}
                old_stories = rf.get("user_stories") or []
                changed, missing = [], []
                for i, os_ in enumerate(old_stories):
                    sid = os_.get("id")
                    if sid not in target_ids:
                        continue  # preserved verbatim (already the backup copy)
                    repl = new_by_id.get(sid)
                    if repl is None:  # model may have renamed the id
                        n_stories = nf.get("user_stories") or []
                        if len(n_stories) == len(old_stories):
                            repl = n_stories[i]
                            notes.append(f"{sid}: not found by id, matched by position")
                        else:
                            missing.append(sid)
                            continue
                    old_stories[i] = repl
                    changed.append(sid)
                if changed:
                    notes.append(
                        f"{fid}: edited {changed}; {len(old_stories) - len(changed)} story(ies) preserved verbatim"
                    )
                if missing:
                    notes.append(
                        f"{fid}: WARNING target(s) {missing} not in new output — edit NOT applied to them (backup kept)"
                    )
            # Restore derived/structural fields WITHIN edited stories too: a text edit must not
            # move or downgrade (e.g. to GAP::) an anchor / NFR / hint on an unchanged AC.
            # #42-transparency: capture any restore that overrode a reviewer change on a TARGET
            # story so the caller can tell the reviewer their non-prose edit was not applied
            # (edit mode preserves derived/structural fields by design) — never drop it silently.
            _reverted = []
            ar = _restore_derived_fields(recon, old_feats, target_ids=target_ids, reverted=_reverted)
            if ar:
                notes.append(
                    f"restored {ar} derived/structural field(s) inside edited story(ies) from backup"
                )
            if _reverted:
                notes.append(
                    "NOT APPLIED — edit mode preserves derived/structural fields, so the following "
                    "reviewer change(s) on the targeted story were kept at their SRS-grounded backup "
                    "value: " + ", ".join(sorted(set(_reverted)))
                    + ". Change these through the proper path (e.g. re-grounding / NFR editing), not a prose edit."
                )
            return recon, notes

        # best-effort (no target): keep the model's text, restore derived fields by id-match.
        ar = _restore_derived_fields(new_doc, old_feats)
        if ar:
            notes.append(f"best-effort: restored {ar} derived/structural field(s) from backup")
        return new_doc, notes
    except Exception as e:  # never corrupt the run over a reconcile bug
        return new_doc, [f"error ({e}); kept generator output"]
