"""
Stage 5: Feature & User Story Agent (Two-Stage Option B Architecture)
======================================================================
ReAct orchestrator implementing a two-stage derivation pipeline.

1-MFU = 1-FEATURE PRINCIPLE:
  Each MFU encodes exactly one cohesive business outcome (MFU System Policy v4.2).
  Stage 5a therefore produces exactly ONE user-facing feature per MFU (or ONE
  infrastructure feature for purely technical MFUs). There is no zone-based
  sub-decomposition. Stage 5b generates 2–6 stories for that single feature,
  covering the entire MFU's S.5 events.

  STAGE 5a — Feature Manifest
    OBSERVE  → Read SRS file(s) for the target MFU
    REASON   → Classify MFU as user-facing or infrastructure
    ACT      → Call LLM Stage 5a generator → parse <OUTPUT> JSON
    ACT      → Write feature_manifest.json  (exactly 1 feature entry)

  STAGE 5b — Story Derivation (for the single feature)
    OBSERVE  → Read Feature Manifest
    REASON   → For the single non-infrastructure feature, use full SRS as context
    ACT      → Call LLM Stage 5b generator → parse <STORIES> JSON array
    ACT      → Assemble final features_stories.json
    ACT      → One-shot critic review (non-gating, quality logging only)
    ACT      → Write features_stories.json

Traceability layers produced:
  L1 — SRSDocument  (source document)
  L2 — SRSSection   (specific control row, event, validation rule)
  L3 — UserStory    (Agile story with Gherkin ACs)
  L4 — JiraIssue / TestCase (populated by CI/CD, placeholders written here)

Key design properties:
  * MAX_ITERATIONS = 1 per stage — no retry loops.
  * Output ALWAYS written for every MFU regardless of parse/critic outcome.
  * Feature IDs are MFU-discriminated (e.g. APP-LABO-STOCK-001-F1) — globally unique.
  * Feature ID always ends in F1 (one feature per MFU → always the first and only feature).
  * Infrastructure features (is_infrastructure=True or functions[]=[]) are suppressed in Stage 5b.
  * Technology tag injected into both stages for domain-specific persona derivation.
  * Full SRS content is passed to Stage 5b (no zone filtering needed).

Helper functions and constants live in feature_story_helpers.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import re

from ..ai.llm_client import LLMClient  # type: ignore
from ..utils.json_schema_validator import JSONSchemaValidator, SchemaValidationError  # type: ignore
from .srs_linker import _detect_srs_document_type  # type: ignore  # used by P2/P10 guards

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
# Helper functions, constants, _Log — all in feature_story_helpers.py
# ---------------------------------------------------------------------------
try:
    from .feature_story_helpers import *  # type: ignore  # noqa: F401,F403
    from .feature_story_helpers import (  # explicit re-export for IDE support
        FEATURE_MANIFEST_SCHEMA_PATH,
        FEATURES_STORIES_SCHEMA_PATH,
        MANIFEST_FILE_NAME,
        MAX_ITERATIONS,
        OUTPUT_FILE_NAME,
        STAGE5A_GENERATOR_PROMPT_PATH,
        STAGE5B_CORRECTOR_PROMPT_PATH,
        STAGE5B_GENERATOR_PROMPT_PATH,
        STAGE5C_REFINER_PROMPT_PATH,
        STORY_CRITIC_PROMPT_PATH,
        _build_corrector_prompt,
        _build_critic_prompt,
        _build_deterministic_functions,
        _build_empty_manifest,
        _build_focal_anchor_functions_block,
        _build_focal_only_srs_content,
        _build_language_hints_block,
        _build_manifest_scaffold,
        _build_screen_registry,
        _build_skeleton_output,
        _build_srs_anchor_index,
        _build_srs_anchor_map_block,
        _build_srs_content_focal_only,
        _build_stage5a_prompt,
        _build_stage5b_prompt,
        _check_ac_gherkin_quality,
        _correct_bounding_box_controls,
        _detect_coverage_gaps,
        _enrich_evidence_ac_ids,
        _extract_corrections_json,
        _extract_critic_result,
        _extract_focal_functions,
        _extract_mfu_seq,
        _extract_output_json,
        _extract_patches,
        _extract_srs_executive_summary,
        _extract_srs_zone_content,
        _extract_stories_json,
        _extract_uiblueprint_executive_summary,
        _find_focal_srs_index,
        _find_srs_files,
        _fix_empty_l2_source_refs,
        _fix_zone_id,
        _hydrate_all_srs_evidence,
        _inject_bounding_box_controls_from_notes,
        _is_dispatch,
        _is_framework_artifact,
        _load_module_context,
        _load_prompt,
        _Log,
        _make_ascii_slug,
        _merge_story_corrections,
        _needs_5c_refinement,
        _normalise_tech_tag,
        _normalize_manifest,
        _normalize_nfr_fields,
        _normalize_stories,
        _read_srs_content,
        _reconcile_edit,  # #42 edit-mode pre-critic merge (deterministic preservation)
        _resolve_focal_artifact,
        _resolve_module_metadata,
        _resolve_srs_type,
        _resolve_technology,
        _sanitise_l2_ids,
        _screens_for_story,
        _strip_invalid_controls,
        _validate_l2_refs,
    )
except ImportError as _e:
    raise ImportError(
        f"feature_story_helpers.py could not be imported: {_e}\n"
        "Ensure feature_story_helpers.py is present in the same directory."
    ) from _e


# ---------------------------------------------------------------------------
# #31 — Grounding-failure detector (anti-hallucination guard for relaxed gates)
# ---------------------------------------------------------------------------
# The relaxed-gates carve-out (#20) accepts a framework/menu/ancestor feature whose
# only failing gates are BUSINESS-VALUE/CONTROLS gates (which do not apply to framework
# code). It must NEVER relax GROUNDING gates — the anti-hallucination guardrails:
#   • Gate 9a — AC premise contradicts / is not derivable from the SRS
#   • Gate 5f — l2_source_ref points to an event/anchor that does not exist in the SRS
#   • Gate 8a — technical_notes cite controls/artifacts absent from the SRS
# When any of these fail, the content is FABRICATED, not merely "framework code we can't
# grade for business value" — so it must be flagged FAIL_HALLUCINATION, never PASS.
#
# Signal source is the critic's free-text feedback. Detection is biased toward RECALL
# (when in doubt, do NOT relax — flag for review) because shipping fabrication is the
# worst outcome. Verified 9/9 against captured benign (7c/6c/5b) vs grounding samples.
# Unambiguous fabrication phrases (rarely appear negated — unlike "contradict", which
# is dropped here in favour of the Gate-9a VERDICT below to avoid matching "no contradiction").
_FABRICATION_PHRASE_RE = re.compile(
    r"(?is)hallucinat"
    r"|non-?existent\s+event"
    r"|references?\s+(?:a\s+)?(?:non-?existent|invalid)\s+event"
    r"|do(?:es)?\s+not\s+exist\s+in\s+the\s+srs"
    r'|explicitly\s+states\s+"?none"?'
)
# Gate-5f "GAP-overuse" flavour: our OWN A+C normalizer substituted a GAP:: marker on a
# negative/edge path where the critic says a real anchor exists. This is a CORRECTABLE ref
# nit, NOT fabricated content — it must stay FAIL_CORRECTION_EXHAUSTED, not FAIL_HALLUCINATION.
_GAP_OVERUSE_RE = re.compile(
    r"(?i)replace|valid\s+(?:srs\s+)?(?:event|anchor)|anchor\s+exists"
    r"|missing_error_anchor|no_neg_anchor"
)


# Negation cues that turn a nearby "fail" into a PASS statement, e.g. the critic writing
# "not a hard fail under Gates 9a/9b" or "no contradiction, does not fail Gate 9a". Without
# this guard, the proximity match below reads such PASS explanations as a 9a/8a FAILURE and
# mis-fires the FAIL_HALLUCINATION guard (the ANCES/001 false positive).
_NEG_BEFORE_FAIL_RE = re.compile(
    r"(?is)\b(?:not|no|never|without|isn'?t|aren'?t|wasn'?t|weren'?t|"
    r"doesn'?t|don'?t|didn'?t|nor|bypass(?:e[ds])?|avoid(?:s|ed)?)\b[^.\n]{0,24}$"
)


def _nonneg_fail_iter(t: str):
    """Yield (start, end) of each 'fail…' token whose immediate preceding context is NOT a
    negation — so 'not a hard fail under Gates 9a/9b' (a PASS explanation) is skipped while a
    genuine 'Gate 9a: FAIL' is kept. This is what makes the grounding guard verdict-accurate."""
    for m in re.finditer(r"(?is)\bfail\w*", t):
        pre = t[max(0, m.start() - 28) : m.start()]
        if _NEG_BEFORE_FAIL_RE.search(pre):
            continue
        yield m.start(), m.end()


def _gate_failed(t: str, gate: str) -> bool:
    """True if the critic feedback reports `gate` (e.g. '9a','8a','5f') as FAILED.
    Verdict-driven AND negation-aware: only a NON-negated 'fail' token within the gate's own
    clause counts, so 'Gate 9a: PASS.' and 'not a hard fail under Gates 9a/9b' never match.
    Also covers the header form ('Gate 9 ... FAIL' + sub-gate id present)."""
    g = re.escape(gate)
    parent = gate[0]  # '9a' -> '9'
    for s, e in _nonneg_fail_iter(t):
        # 'fail' must sit within ~70 chars of the gate id, inside the same sentence/line.
        before = t[max(0, s - 70) : s]
        after = t[e : e + 70]
        if re.search(rf"(?i)\b{g}\b[^.\n]{{0,70}}$", before) or re.search(
            rf"(?i)^[^.\n]{{0,70}}\b{g}\b", after
        ):
            return True
        # Header form: 'Gate 9 … FAIL' on the same clause, with the sub-gate id present anywhere.
        if re.search(rf"(?is)gate\s*{parent}\b[^.\n]{{0,50}}$", before) and re.search(
            rf"(?i)\b{g}\b", t
        ):
            return True
    return False


def _has_grounding_failure(feedback: str) -> bool:
    """#31 (refined) — True only for GENUINE grounding failures: a Gate-9a SRS-contradiction,
    a Gate-8a absent-artifact failure, an unambiguous fabrication phrase, or a Gate-5f
    *fabricated/non-existent ref*. It deliberately does NOT flag a Gate-5f **GAP-overuse**
    (our normalizer's conservative GAP:: marker where a real anchor exists) — that is a
    correctable ref nit, not fabrication, so it stays FAIL_CORRECTION_EXHAUSTED. Verdict-driven
    (not prose 'contradict' matching) so 'no contradiction' / 'not contradicted' never trips it.
    Language-agnostic: keys off the language-neutral critic gate IDs."""
    if not feedback or not isinstance(feedback, str):
        return False
    t = feedback
    if _gate_failed(t, "9a") or _gate_failed(t, "8a"):
        return True
    if _FABRICATION_PHRASE_RE.search(t):
        return True
    if _gate_failed(t, "5f"):
        gap_overuse = ("gap::" in t.lower()) and bool(_GAP_OVERUSE_RE.search(t))
        return not gap_overuse
    return False


# ---------------------------------------------------------------------------
# #38 (MVP) — Self-describing failure guidance for the review UI (PM-facing)
# ---------------------------------------------------------------------------
# Deterministic (no LLM): maps the final_status (+ which gates failed) to a
# corrective-action recommendation a Project Manager can act on directly when
# opening a feature/MFU in the tree view. Emitted at FEATURE level (where the
# critic verdict lives). The interactive disposition workflow (approve/edit
# states, audit trail, edit-and-revalidate) is V1; this is the read-only,
# in-context guidance contract the app renders.
#
# Centralised guideline table = single source of truth so the rule the app
# ENFORCES and the text the PM READS can never drift apart. approve_as_is_allowed
# is hard-locked False for FAIL_HALLUCINATION / FAIL_SCHEMA_INVALID so a one-click
# approve can never re-open the fabrication hole #31 closed.
_REVIEW_GUIDELINES = {
    #  key          (severity,      recommended_action, approve_as_is, guideline)
    "NONE": ("none", "NONE", True, "Critic approved. No action required."),
    "SPOT_CHECK": (
        "low",
        "SPOT_CHECK",
        True,
        "Framework/menu/ancestor unit; business-value gates were waived (relaxed gates). Light spot-check only — safe to accept.",
    ),
    "RERUN": (
        "transient",
        "RERUN",
        False,
        "Transient generation error (empty response / network). Re-run this MFU; it will almost always pass. No manual edit needed.",
    ),
    "REGEN_SCHEMA": (
        "critical",
        "REGENERATE",
        False,
        "Output failed structural schema validation and was quarantined (.INVALID.json). Not approvable as-is — regenerate or hand-author.",
    ),
    "REGEN_HALLU": (
        "critical",
        "REGENERATE",
        False,
        "Content is NOT grounded in the SRS (fabricated / contradicts the SRS). Do NOT approve as-is. Regenerate (often after fixing the upstream SRS) or hand-author from the SRS. Accepting requires an explicit written justification + reviewer identity.",
    ),
    "APPROVE": (
        "trivial",
        "APPROVE_AS_IS",
        True,
        "Stories are sound; only the control list (Gate 7c) contains non-control noise. Trivial — safe to approve as-is.",
    ),
    "EDIT": (
        "substantive",
        "EDIT",
        False,
        "Critic gates failed and auto-correction was exhausted. Review the failure summary, edit the affected story/AC, then re-validate this MFU before accepting.",
    ),
    "REGEN": (
        "critical",
        "REGENERATE",
        False,
        "Generation did not produce a usable result. Regenerate.",
    ),
}


def _failing_gates(feedback: str) -> set:
    """Set of gate ids the critic reports as FAILED — negation-aware (reuses _nonneg_fail_iter,
    so 'not a hard fail under Gate 9a' is ignored). Only 'Gate <id>' mentions within ~70 chars
    of a non-negated 'fail' count. Used to decide single-gate relaxation precisely."""
    if not feedback or not isinstance(feedback, str):
        return set()
    gates: set = set()
    for s, e in _nonneg_fail_iter(feedback):
        # Scope to the FAIL's own clause (bounded by '.'/newline) so a separate
        # 'Gate 1-4,6-9: PASS.' sentence next to it is not miscounted as failing.
        b = max(feedback.rfind(".", 0, s), feedback.rfind("\n", 0, s))
        clause_before = feedback[b + 1 : s]
        _after_dots = [p for p in (feedback.find(".", e), feedback.find("\n", e)) if p != -1]
        clause_after = feedback[e : (min(_after_dots) if _after_dots else len(feedback))]
        for m in re.finditer(r"(?i)gate\s*([1-9][a-h]?)", clause_before):
            gates.add(m.group(1).lower())
        # sub-gate id stated AFTER 'fail' in the same clause (e.g. 'FAIL — sub-gate 5g')
        for m in re.finditer(r"(?i)\b([1-9][a-h])\b", clause_after):
            gates.add(m.group(1).lower())
    return gates


# Gherkin/when-clause contamination signature (Gate 5g) — the critic sometimes labels this
# failure as the parent "Gate 5" + a prose description rather than the explicit "5g" token.
_GHERKIN_5G_SIG = re.compile(
    r'(?is)(?:gherkin|implementation\s+contamination|"?when"?\s+clause)'
    r".{0,200}?(?:user[-\s]?initiated|user\s+action|system[-\s]?(?:event|initiated|internal)"
    r"|not\s+a\s+user|observable\s+user|implementation\s+detail)"
    r"|(?:user[-\s]?initiated|user\s+action|system[-\s]?(?:event|initiated))"
    r'.{0,200}?(?:"?when"?\s+clause|gherkin)'
)
# Signatures of OTHER Gate-5 sub-gates that are REAL coverage defects (must NOT be relaxed):
# 5a (too few ACs), 5b (Happy Path), 5c (Negative Path missing), 5d (Edge Case missing).
_OTHER_5_SUBGATE_SIG = re.compile(
    r"(?is)(?:no|missing|lacks?|absent|without|requires?|needs?|add\s+a)\b[^.\n]{0,60}"
    r"(?:negative\s+path|edge\s+case|happy\s+path)"
    r"|(?:negative\s+path|edge\s+case|happy\s+path)[^.\n]{0,40}"
    r"(?:missing|absent|is\s+required|not\s+present|lacking)"
    r"|(?:minimum|at\s+least|fewer\s+than|less\s+than|only)\s+(?:3|three|\d)\s+(?:ac|acceptance)"
)


def _is_5g_only_failure(feedback: str) -> bool:
    """True if the ONLY failing gate is 5g (Gherkin 'when'-clause implementation-detail phrasing).
    Per the MVP decision, a when-clause framed as a system event rather than a user action is a
    cosmetic phrasing nit, not a correctness defect — so a 5g-ONLY residual is relaxed (visible,
    spot-check) instead of hard-failing the MFU. Any other failing gate ⇒ NOT eligible.

    Robust to critic labelling: the failure is recognised both when the explicit '5g' sub-gate
    token appears AND when it is written as the parent 'Gate 5' + a Gherkin/when-clause prose
    description — but ONLY when no OTHER Gate-5 sub-gate (5a too-few-ACs, 5b/5c/5d missing
    path-type) is implicated, so genuine AC-coverage defects are never relaxed."""
    g = _failing_gates(feedback)
    if not g.issubset({"5", "5g"}):
        return False
    if "5g" in g:
        return True
    if "5" in g:
        # Parent-labelled Gate 5: relax only if it's the Gherkin/when-clause flavour AND not a
        # missing-path / too-few-ACs coverage defect.
        t = feedback or ""
        return bool(_GHERKIN_5G_SIG.search(t)) and not bool(_OTHER_5_SUBGATE_SIG.search(t))
    return False


def _is_controls_only_failure(feedback: str) -> bool:
    """True if a FAIL_CORRECTION_EXHAUSTED failure is confined to Gate 7c (controls) —
    i.e. cosmetic: the stories are sound, only bounding_box.controls[] has noise. Biased
    toward 'substantive' when ambiguous (auto-approve must not be offered for real defects)."""
    if not feedback or not isinstance(feedback, str):
        return False
    t = feedback
    has_7c = re.search(r"(?is)\b7c\b|gate\s*7\b[^.\n]{0,40}?fail|bounding_box\.controls", t)
    if not has_7c:
        return False
    if re.search(r"(?is)all\s+other\s+gates?\b[^.\n]{0,25}?pass", t):
        return True
    # Any OTHER gate (1-6, 8, 9) implicated in a FAIL -> substantive, not cosmetic.
    other_fail = re.search(r"(?is)(?:gate\s*)?\b([1-6][a-h]?|8[a-z]?|9[ab]?)\b[^.\n]{0,40}?fail", t)
    return not bool(other_fail)


# ---------------------------------------------------------------------------
# L1 source builder — document-type-aware (batch / API / UI)
# ---------------------------------------------------------------------------
def _build_l1_source(mfu_id: str, source_path: str, module_id: str, mfu_seq: str) -> dict:
    """Build a self-consistent l1_source node whose document_type matches the ACTUAL
    focal SRS document (detected from the file name), instead of hard-coding
    'SRS_UIBlueprint'. Fixes the deterministic/fallback paths stamping a UIBlueprint
    l1_source (and a phantom screen_id) onto a BatchSpec/APIContracts MFU. screen_id is
    emitted only for screen documents (UI/API); it is omitted for batch (schema-optional).
    Language-agnostic — keys off the SRS document type, not the source technology."""
    try:
        _kind = _detect_srs_document_type(Path(source_path).name) or "UIBlueprint"
    except Exception:
        _kind = "UIBlueprint"
    # doc kind → (id segment, schema document_type enum, is_screen)
    _MAP = {
        "UIBlueprint": ("UIBlueprint", "SRS_UIBlueprint", True),
        "APIContracts": ("APIContracts", "SRS_APIContracts", True),
        "BatchBlueprint": ("BatchBlueprint", "SRS_BatchBlueprint", False),
        "BatchSpec": ("BatchBlueprint", "SRS_BatchBlueprint", False),
    }
    _seg, _dtype, _is_screen = _MAP.get(_kind, ("UIBlueprint", "SRS_UIBlueprint", True))
    _l1 = {
        "id": f"SRS::{mfu_id}::{_seg}",
        "document_type": _dtype,
        "path": source_path,
        "mfu_id": mfu_id,
    }
    if _is_screen:
        _l1["screen_id"] = f"{module_id.replace('MOD-', '')}_SCREEN_{mfu_seq}"
    return _l1


# ---------------------------------------------------------------------------
# #38b — Critic gate glossary (PM-facing help content)
# ---------------------------------------------------------------------------
# Single source of truth for what each Critic gate means, so the review UI can
# render plain-English help next to the terse "Gate N: FAIL" failure_summary.
# Keep in sync with the critic prompt template's gate definitions.
_GATE_CATALOG = {
    1: (
        "Schema & IDs",
        "Output structure is valid: required fields present, ID patterns correct, "
        "business-architecture fields populated.",
    ),
    2: (
        "Persona governance",
        "Story personas are domain-appropriate and used consistently across the feature.",
    ),
    3: (
        "Story ID format",
        "Feature/story IDs derive correctly from the module id and MFU sequence; no Epic-style IDs.",
    ),
    4: (
        "“so_that” business value",
        "Every story's so_that clause states a specific, outcome-driven business value.",
    ),
    5: (
        "Acceptance-criteria rigour",
        "Each story has correct Gherkin ACs (one Happy / Negative / Edge path), and every "
        "“when” clause describes an observable USER action — not an internal system event.",
    ),
    6: (
        "Story sizing & validity",
        "Story count is within the function ceiling, points are valid Fibonacci values, and each "
        "story passes the four-condition validity test (state change, single intent, independently "
        "deliverable, non-collapsible).",
    ),
    7: (
        "Bounding box / S.3 controls",
        "Every control name in the bounding box exists VERBATIM in the SRS S.3 control inventory; "
        "the zone is valid for the feature type.",
    ),
    8: (
        "Technical-notes specificity",
        "technical_notes name concrete legacy artifacts (programs, paragraphs, events) and the "
        "migration_hint names concrete modern targets (endpoints, components).",
    ),
    9: (
        "AC evidence grounding",
        "Each AC's l2_source_ref is supported by the SRS: it does not contradict the source (9a), "
        "and negative/edge paths anchor to a real error event or an honest GAP marker (9b).",
    ),
}


def _build_gate_reference(feedback: str) -> list:
    """#38b — Build PM-facing help for the gates a critic summary FLAGGED.

    Best-effort: scans each line for a 'Gate N' mention that co-occurs with 'FAIL'
    (so passing gates listed in the summary are excluded) and returns their glossary
    entries. Falls back to the full catalog when no failing gate can be parsed, so the
    reviewer always gets an explanation. Deterministic — no LLM."""
    nums: list = []
    # Pair each 'Gate N[letter]' with a FAIL that follows it on the SAME line, within a
    # short window — so 'Gate 5: FAIL' and a one-line 'Gate 5g FAIL and Gate 7c FAIL' both
    # resolve to {5,7}, while 'Gate 1: PASS' and 'all other gates (1..8) pass' are ignored.
    # [^\n] keeps each match on its own line so a FAIL never leaks onto a passing gate.
    for _m in re.finditer(
        r"\bGate\s*(\d{1,2})[a-z]?[^\n]{0,60}?\bFAIL", feedback or "", re.IGNORECASE
    ):
        _n = int(_m.group(1))
        if _n in _GATE_CATALOG and _n not in nums:
            nums.append(_n)
    if not nums:
        nums = sorted(_GATE_CATALOG)  # fallback: full catalog when parsing finds nothing
    return [
        {"gate": _n, "name": _GATE_CATALOG[_n][0], "meaning": _GATE_CATALOG[_n][1]}
        for _n in sorted(nums)
    ]


def _build_review_guidance(final_status: str, feedback: str = "", failed_story_ids=None) -> dict:
    """Deterministic PM-facing review guidance for a feature's final_status (#38 MVP).
    Returns {severity, recommended_action, approve_as_is_allowed, guideline,
    failure_summary, flagged_story_ids}."""
    st = final_status or ""
    if st in ("PASS", "PASS_DETERMINISTIC_FALLBACK"):
        key = "NONE"
    elif st == "PASS_RELAXED_GATES":
        key = "SPOT_CHECK"
    elif st == "FAIL_PARSE_ERROR":
        key = "RERUN"
    elif st == "FAIL_SCHEMA_INVALID":
        key = "REGEN_SCHEMA"
    elif st == "FAIL_HALLUCINATION":
        key = "REGEN_HALLU"
    elif st == "FAIL_CORRECTION_EXHAUSTED":
        key = "APPROVE" if _is_controls_only_failure(feedback) else "EDIT"
    elif st in ("FAIL_MAX_RETRIES", "FAIL_NO_PARSEABLE_OUTPUT"):
        key = "REGEN"
    else:
        key = "EDIT"
    severity, action, approve_ok, guideline = _REVIEW_GUIDELINES[key]
    block = {
        "severity": severity,
        "recommended_action": action,
        "approve_as_is_allowed": bool(approve_ok),
        "guideline": guideline,
        "flagged_story_ids": list(failed_story_ids or []),
    }
    # Verbatim critic reason for non-clean statuses (authoritative; never paraphrased).
    if key not in ("NONE",) and feedback:
        block["failure_summary"] = str(feedback)[:2000]
        # #38b — attach plain-English help for the gate(s) the summary flagged, so the
        # reviewer isn't left decoding "Gate 5 / Gate 7". Referenced-gates-only (with
        # full-catalog fallback); deterministic and additive.
        block["gate_reference"] = _build_gate_reference(feedback)
    return block


# ---------------------------------------------------------------------------
# #42 — Human-feedback regeneration: prepend PM guidance to a generator prompt
# ---------------------------------------------------------------------------
def _prepend_human_guidance(prompt: str, guidance) -> str:
    """Prepend an authoritative HUMAN REVIEWER GUIDANCE banner to a Stage-5 generator
    prompt (#42). The banner is highest-priority BUT explicitly re-states the grounding
    constraint, so human steering can never license fabrication — the regenerated output
    is still re-validated by the critic + the #31 grounding gates."""
    if not guidance or not isinstance(prompt, str):
        return prompt
    banner = (
        "==================== HUMAN REVIEWER GUIDANCE (AUTHORITATIVE) ====================\n"
        "A human reviewer (Project Manager) has supplied the following correction for this\n"
        "MFU after reviewing a prior generation. Treat it as the HIGHEST-PRIORITY instruction;\n"
        "it overrides any contrary earlier interpretation. CRITICAL: you MUST still ground\n"
        "every statement strictly in the provided SRS — do NOT fabricate content to satisfy\n"
        "this guidance. If the guidance conflicts with the SRS, follow the SRS and note it.\n"
        f"GUIDANCE: {str(guidance).strip()}\n"
        "================================================================================\n\n"
    )
    return banner + prompt


# ---------------------------------------------------------------------------
# Option 1 (MVP) — reconstruct emergency-scaffold feature fields from stories
# ---------------------------------------------------------------------------
# Signal stamped by the Stage-5a emergency scaffold path (_run_stage5a). Any
# feature whose description begins with this prefix is on the deterministic
# fallback placeholder ("... requires manual review"). See _run_stage5a.
_SCAFFOLD_DESC_PREFIX = "Emergency fallback scaffold for"

# Orphan / unclustered / dead-code bucket markers (Stage 2.5 synthetic clusters).
# These MFUs are data-contract fragments with no consuming program; forcing the LLM
# to derive functional stories from them invites hallucination.
_ORPHAN_MFU_PREFIXES = ("SYS-DATA", "SYS-DEAD")
_ORPHAN_MODULE_MARKERS = ("orphan", "dead code", "ungrouped")


def _is_orphan_data_mfu(mfu_id: str, module_name: str = "") -> bool:
    """True for orphan/dead-code/unclustered data-only MFUs (SYS-DATA-*, SYS-DEAD-*,
    or an orphan/ungrouped/dead-code bucket name). Narrow by design: real MFUs are
    'MFU-NNN' in real modules and never match, so legitimate API MFUs are untouched."""
    mid = (mfu_id or "").upper()
    if any(mid.startswith(p) for p in _ORPHAN_MFU_PREFIXES):
        return True
    mn = (module_name or "").lower()
    return any(m in mn for m in _ORPHAN_MODULE_MARKERS)


# Reject an LLM description that is empty, too short/long, or an evasion/refusal.
_DESC_REJECT_SIGNALS = (
    "as an ai",
    "i cannot",
    "i'm sorry",
    "no srs",
    "not provided",
    "placeholder",
    "requires manual review",
    "unable to",
)


def _llm_feature_description(llm, stories: list, feat: dict) -> str:
    """Summarise the already-generated user stories into a concise business
    feature description via a single small LLM call. FAIL-SAFE: returns "" on any
    error / empty / low-quality response so the caller falls back to the
    deterministic description. Grounding is not at stake — this only rewrites a
    display-only field from content the critic already approved; the prompt
    forbids inventing scope beyond the stories.
    """
    try:
        lines = []
        for st in stories[:3]:
            if not isinstance(st, dict):
                continue
            t = (st.get("title") or "").strip()
            iw = (st.get("i_want_to") or "").strip()
            so = (st.get("so_that") or "").strip()
            tn = (st.get("technical_notes") or "").strip()[:400]
            if t:
                lines.append(f"- Story: {t}")
            if iw:
                lines.append(f"  Wants: {iw}")
            if so:
                lines.append(f"  So that: {so}")
            if tn:
                lines.append(f"  Technical notes: {tn}")
        digest = "\n".join(lines).strip()
        if not digest:
            return ""
        system_prompt = (
            "You are an enterprise business analyst. Write a concise FEATURE DESCRIPTION "
            "(2-3 sentences, plain prose, no markdown, no lists) that summarises ONLY the "
            "information in the user stories provided. Describe the business capability in "
            "plain language. Do NOT invent scope, endpoints, systems, or data not present in "
            "the stories. Do NOT mention that the text is auto-generated. Output the "
            "description text only — nothing else."
        )
        user_prompt = (
            f"Feature title: {(feat.get('title') or '').strip()}\n\n"
            f"User stories:\n{digest}\n\nFeature description:"
        )
        resp = llm.complete(system_prompt=system_prompt, user_prompt=user_prompt)
        if not isinstance(resp, str):
            return ""
        txt = resp.strip().strip("`").strip().strip('"').strip()
        if txt.lower().startswith("feature description:"):
            txt = txt.split(":", 1)[1].strip()
        low = txt.lower()
        if len(txt) < 40 or len(txt) > 1200:
            return ""
        if any(sig in low for sig in _DESC_REJECT_SIGNALS):
            return ""
        return txt
    except Exception:
        return ""


def _reconstruct_scaffold_features_from_stories(final_output: dict, llm=None) -> int:
    """Post-Stage-5b repair pass — Option 1 (MVP).

    Stage 5a can fall back to an emergency scaffold feature (placeholder
    description / business_context / functions) when the feature-manifest LLM
    call deflects. Stage 5b — a separate call — frequently still produces
    high-quality user stories for the SAME MFU. This pass rebuilds ONLY the
    scaffold feature's display fields from those stories, so the deliverable is
    readable and useful instead of a bare "requires manual review" placeholder.

    Strictly gated — a feature is rebuilt ONLY when BOTH hold:
        (a) its description carries the emergency-scaffold signal, AND
        (b) it has at least one user story to rebuild from.
    Normal-path, infrastructure, and story-less features are never touched.

    Structured fields stay DETERMINISTIC: function anchors are HARVESTED from
    the stories' existing (already-grounded) l2_source_ref values — real SRS
    anchors preferred over GAP markers, never fabricated — and success_criteria /
    business_context come straight from the stories' AC 'then' clauses,
    technical_notes and migration_hint.

    The free-text `description` is the one field that benefits from prose
    quality. When an `llm` client is supplied, a single small call SUMMARISES the
    already-generated stories into a crisp business description; the call is
    fail-safe — on any error, empty, or low-quality response it falls back to the
    deterministic story-intent description. When `llm` is None the deterministic
    description is used. Either way the text is explicitly labelled as
    story-derived / not independently source-verified, and provenance (including
    whether the LLM summary was used) is recorded in generation_metadata.

    Returns the number of features reconstructed.
    """
    if not isinstance(final_output, dict):
        return 0
    mfu_id = final_output.get("mfu_id", "UNKNOWN")
    biz_desc = (final_output.get("module_business_description") or "").strip()

    def _clean(v) -> str:
        return v.strip() if isinstance(v, str) else ""

    def _join_unique(values) -> str:
        seen, out = set(), []
        for v in values:
            v = _clean(v)
            if v and v not in seen:
                seen.add(v)
                out.append(v)
        return " ".join(out)

    def _story_anchor(story: dict) -> str:
        refs = []
        for s in story.get("l2_sources") or []:
            if isinstance(s, str) and s:
                refs.append(s)
        for ac in story.get("acceptance_criteria") or []:
            r = ac.get("l2_source_ref") if isinstance(ac, dict) else None
            if isinstance(r, str) and r:
                refs.append(r)
        real = [r for r in refs if not r.startswith("GAP::")]
        if real:
            return real[0]
        return refs[0] if refs else ""

    reconstructed_ids = []
    _llm_desc_ids: list = []
    for feat in final_output.get("features", []):
        if not isinstance(feat, dict):
            continue
        if not _clean(feat.get("description")).startswith(_SCAFFOLD_DESC_PREFIX):
            continue  # not on the scaffold — leave untouched
        if feat.get("is_infrastructure"):
            continue  # infra features legitimately carry no stories/functions
        stories = feat.get("user_stories") or []
        if not stories:
            continue  # nothing to rebuild from — keep the honest placeholder

        feat_id = feat.get("id", "F1")
        existing_ref = ""
        if feat.get("functions"):
            existing_ref = _clean(feat["functions"][0].get("l2_source_ref"))
        gap_ref = existing_ref or f"GAP::{mfu_id}::NO_PRIMARY_OPERATION"

        # functions[] — one per story, labelled from the story title, anchor harvested
        new_functions = []
        for i, st in enumerate(stories, start=1):
            anchor = _story_anchor(st) or gap_ref
            new_functions.append(
                {
                    "id": f"{feat_id}-FN{i}",
                    "label": _clean(st.get("title")) or f"Operation {i}",
                    "l2_source_ref": anchor,
                }
            )
        if new_functions:
            feat["functions"] = new_functions

        # description — deterministic story-intent lead (on-topic even for orphan
        # buckets whose scope differs from the module's main purpose), then an
        # optional fail-safe LLM prose polish. Provenance label always appended.
        _PROV = (
            " (Auto-derived from the generated user stories; not independently source-verified.)"
        )
        titles = [_clean(st.get("title")) for st in stories if _clean(st.get("title"))]
        s0 = stories[0] if isinstance(stories[0], dict) else {}
        _iw, _so = _clean(s0.get("i_want_to")), _clean(s0.get("so_that"))
        det_parts = []
        if _iw:
            det_parts.append(
                "Provides the ability to " + _iw + (f" so that {_so}" if _so else "") + "."
            )
        elif biz_desc:
            det_parts.append(biz_desc)
        if len(titles) > 1:
            det_parts.append("Scope: " + "; ".join(titles) + ".")
        det_desc = " ".join(det_parts).strip() or (biz_desc or _clean(feat.get("title")))

        desc_via_llm = False
        final_desc = det_desc
        if llm is not None:
            _polished = _llm_feature_description(llm, stories, feat)
            if _polished:
                final_desc = _polished
                desc_via_llm = True
        feat["description"] = final_desc + _PROV
        if desc_via_llm:
            _llm_desc_ids.append(feat_id)

        # business_context — current_state from technical_notes, target_state from migration_hint
        bc = feat.setdefault("business_context", {})
        cur = _join_unique(st.get("technical_notes", "") for st in stories)
        tgt = _join_unique(st.get("migration_hint", "") for st in stories)
        if cur:
            bc["current_state"] = cur
        if tgt:
            bc["target_state"] = tgt
        bc.setdefault("current_state", "")
        bc.setdefault("target_state", "")

        # success_criteria — Happy-Path AC 'then' clauses (fallback: all ACs)
        crit = []
        for st in stories:
            acs = st.get("acceptance_criteria") or []
            happy = [
                ac
                for ac in acs
                if isinstance(ac, dict) and _clean(ac.get("path_type")).lower().startswith("happy")
            ]
            for ac in happy or [a for a in acs if isinstance(a, dict)]:
                t = _clean(ac.get("then"))
                if t:
                    crit.append(t[:1].upper() + t[1:])
        if crit:
            feat["success_criteria"] = crit[:6]

        reconstructed_ids.append(feat_id)

    if reconstructed_ids:
        # Provenance lives in generation_metadata (schema: additionalProperties=True).
        # The Feature object forbids extra keys, so no per-feature flag is added.
        meta = final_output.setdefault("generation_metadata", {})
        meta["reconstructed_from_stories"] = True
        meta["reconstructed_feature_ids"] = reconstructed_ids
        # Record which features got an LLM-summarised description (vs deterministic).
        meta["reconstructed_description_via_llm"] = _llm_desc_ids or False

    return len(reconstructed_ids)


# ---------------------------------------------------------------------------
# Fix 4 — critic context-budget guard (oversized-MFU overflow prevention)
# ---------------------------------------------------------------------------
def _est_tokens(s: str) -> int:
    """Conservative (UPPER-BOUND) token estimate used by the critic-budget guard.

    Calibration note (MASTER/001 overflow root cause): the previous estimate used ASCII//4,
    but real BPE tokenisers split English prose + markdown tables + embedded code at ~3 chars
    per token (punctuation, identifiers and table pipes inflate the count). ASCII//4 therefore
    UNDER-counts an ASCII-heavy SRS by ~25-35%, so the trim branch in `_fit_srs_for_critic`
    was never entered and the real 1.11M-token payload was sent unchanged. We now estimate
    ASCII at /3 and non-ASCII (CJK/Japanese) at ~1 token/char, then apply a 1.05 safety
    factor so the estimate is a deliberate UPPER bound — guaranteeing the guard trims early
    enough to stay under the provider window. Slightly over-trimming an oversized MFU is safe;
    overflowing the context window is not."""
    if not s:
        return 0
    ascii_n = sum(1 for c in s if ord(c) < 128)
    raw = ascii_n / 3.0 + (len(s) - ascii_n) * 1.0
    return int(raw * 1.05)


def _fit_srs_for_critic(
    srs_content: str,
    manifest: dict,
    template: str,
    language_hints: str,
    max_tokens: int,
    mfu_id: str,
) -> str:
    """#Fix4 — trim the SRS sent to the critic so the critic call never exceeds the model
    context window (the MASTER/001 ContextWindowExceededError: 1.12M > 1.05M tokens). Keeps
    the head (focal SRS is at position 0 after reordering) and truncates the tail. Token-based
    + CJK-aware so it is correct for Japanese SRS. No-op for normally-sized MFUs."""
    try:
        import json as _json

        # default=str so a stray non-serialisable object (e.g. a set) NEVER throws here —
        # a throw would be swallowed below and silently disable trimming, re-introducing the
        # exact overflow this guard exists to prevent. We only need the size, not fidelity.
        try:
            _manifest_json = _json.dumps(manifest, ensure_ascii=False, default=str)
        except Exception:
            _manifest_json = str(manifest)
        overhead = (
            _est_tokens(template)
            + _est_tokens(language_hints or "")
            + _est_tokens(_manifest_json)
            + 20_000
        )
        avail = max(20_000, int(max_tokens) - overhead)
        if _est_tokens(srs_content) <= avail:
            return srs_content
        avg = _est_tokens(srs_content) / max(1, len(srs_content))  # tokens per char for THIS srs
        climit = max(40_000, int(avail / avg))
        _Log.warn(
            f"[CRITIC-BUDGET] {mfu_id}: SRS ~{_est_tokens(srs_content):,} tok exceeds critic "
            f"budget ~{avail:,} tok — trimming to ~{climit:,} chars (oversized MFU; focal head kept)."
        )
        return srs_content[:climit] + (
            "\n\n[... SRS TRUNCATED FOR CRITIC CONTEXT BUDGET — oversized MFU; "
            "focal and early sections retained. Critic validates against the retained portion ...]"
        )
    except Exception as _e:
        # Fail LOUD — never silently disable the context-budget guard.
        _Log.warn(f"[CRITIC-BUDGET] {mfu_id}: budget guard error ({_e}); sending SRS untrimmed.")
        return srs_content


# ---------------------------------------------------------------------------
# Core ReAct agent
# ---------------------------------------------------------------------------
class FeatureStoryAgent:
    """
    Two-stage ReAct agent that produces feature_manifest.json (Stage 5a)
    and features_stories.json (Stage 5b) for one MFU directory.

    Usage
    -----
    agent = FeatureStoryAgent(project_root=".")
    result = agent.run_for_mfu(mfu_dir=Path("modules/MOD-X/stage4_specs/MFU-001"))
    """

    def __init__(
        self,
        project_root: str = ".",
        stage5a_prompt_path: str | None = None,
        stage5b_prompt_path: str | None = None,
        critic_prompt_path: str | None = None,
        max_iterations: int = MAX_ITERATIONS,
        trigger_neo4j: bool = False,
        api_key: str | None = None,
    ):
        self.root = Path(project_root).resolve()
        self.max_iterations = max_iterations
        self.trigger_neo4j = trigger_neo4j
        self.api_key = api_key

        p5a = (
            Path(stage5a_prompt_path)
            if stage5a_prompt_path
            else (self.root / STAGE5A_GENERATOR_PROMPT_PATH)
        )
        p5b = (
            Path(stage5b_prompt_path)
            if stage5b_prompt_path
            else (self.root / STAGE5B_GENERATOR_PROMPT_PATH)
        )
        crit = (
            Path(critic_prompt_path)
            if critic_prompt_path
            else (self.root / STORY_CRITIC_PROMPT_PATH)
        )

        self._5a_template = _load_prompt(p5a)
        self._5b_template = _load_prompt(p5b)
        self._crit_template = _load_prompt(crit)

        # Stage 5b+ targeted correction pass (optional — graceful fallback if absent)
        p5b_corr = self.root / STAGE5B_CORRECTOR_PROMPT_PATH
        try:
            self._5b_corrector_template = _load_prompt(p5b_corr)
        except FileNotFoundError:
            self._5b_corrector_template = None
            _Log.warn("05b_story_corrector.txt not found — Stage 5b+ correction pass disabled.")

        # Stage 5c evidence refiner (optional — graceful fallback if absent)
        p5c = self.root / STAGE5C_REFINER_PROMPT_PATH
        try:
            self._5c_template = _load_prompt(p5c)
        except FileNotFoundError:
            self._5c_template = None
            _Log.warn("05c_evidence_refiner.txt not found — Stage 5c disabled.")

        # Base pipeline LLM (used for stages 1-4)
        self._llm = LLMClient(
            config_path=str(self.root / "project_config.json"), api_key=self.api_key
        )

        # Stage-5 tuning knobs (enterprise-overridable via project_config.json["stage5"]).
        # nav_collapse_min_dispatch: minimum number of navigation/dispatch events before
        # they are collapsed into ONE grouped navigation function. Below this, dispatch
        # events stay as individual functions (better coverage for non-hub features like
        # authentication windows). Safe default = 8; never below 2.
        self._nav_collapse_min = 8
        try:
            import json as _json

            with open(self.root / "project_config.json", encoding="utf-8") as _cf:
                _s5 = _json.load(_cf).get("stage5") or {}
            self._nav_collapse_min = max(2, int(_s5.get("nav_collapse_min_dispatch", 8)))
        except Exception:
            pass  # any read/parse issue → keep the safe default (8)

        # Fix4 — critic context budget (oversized-MFU overflow guard). Default ~780K (EST) tokens.
        # The provider hard limit is ~1.048M; combined with the UPPER-BOUND _est_tokens this
        # leaves ~250K of real headroom to absorb any residual estimation error so the trimmed
        # critic payload never exceeds the window. Override via stage5.critic_max_tokens.
        self._critic_max_tokens = 780000
        try:
            import json as _json

            with open(self.root / "project_config.json", encoding="utf-8") as _cf:
                _s5b = _json.load(_cf).get("stage5") or {}
            self._critic_max_tokens = max(100000, int(_s5b.get("critic_max_tokens", 780000)))
        except Exception:
            pass

        # Stage 5 dedicated LLM — uses the provider's reasoning model for high-quality
        # feature manifest and user story generation.  Resolution order:
        #   1. project_config.json["stage5"]["model"]  — explicit per-stage override
        #   2. Automatic: LLMClient(use_reasoning_model=True) reads
        #      llm.providers[active].reasoning_model from config — no hardcoding needed.
        # Switching providers or models requires only a project_config.json change.
        _s5_model = self._resolve_stage5_model(str(self.root / "project_config.json"))
        if _s5_model:
            self._stage5_llm = LLMClient(
                config_path=str(self.root / "project_config.json"),
                model_override=_s5_model,
                api_key=self.api_key,
            )
            _Log.ok(f"Stage 5 LLM: model_override='{_s5_model}'")
            # Feature/story generation opts INTO the timeout circuit-breaker.
            try:
                self._stage5_llm.set_timeout_breaker_scope(True)
            except AttributeError:
                pass
        else:
            # No stage5.model override — delegate model selection to LLMClient.
            # use_reasoning_model=True ensures the provider's highest-quality model
            # is used for Stage 5, as defined in llm.providers[active].reasoning_model.
            self._stage5_llm = LLMClient(
                config_path=str(self.root / "project_config.json"),
                use_reasoning_model=True,
                api_key=self.api_key,
            )
            _Log.ok(
                f"Stage 5 LLM: auto-selected reasoning model "
                f"({self._stage5_llm.active_reasoning_model})"
            )
            # Feature/story generation opts INTO the timeout circuit-breaker.
            try:
                self._stage5_llm.set_timeout_breaker_scope(True)
            except AttributeError:
                pass

        # Stage 5a condensed-mode threshold.
        # When an MFU has more SRS files than this, Stage 5a receives only the
        # focal document in full + executive summaries of supporting files.
        # Stage 5b always receives full SRS content (unchanged).
        # Configurable via project_config.json → stage5.focal_only_threshold.
        self._stage5a_threshold: int = self._resolve_stage5a_threshold(
            str(self.root / "project_config.json")
        )

        # UI-bearing SRS document types — used by P2-guard and P10-guard to
        # decide whether an MFU has a user-facing surface.
        # Configurable via project_config.json → stage5.ui_bearing_srs_types.
        self._ui_bearing_srs_types: set = self._resolve_ui_bearing_srs_types(
            str(self.root / "project_config.json")
        )
        _Log.ok(f"UI-bearing SRS types: {sorted(self._ui_bearing_srs_types)}")

        # Headless-but-functional SRS document types — used by P10-guard only.
        # MFUs whose focal SRS resolves to one of these types are treated as
        # functional (not infrastructure) even though they have no screen.
        # Configurable via project_config.json → stage5.functional_api_types.
        # BUG-2 fix: separates "has UI surface" from "has business logic" classification.
        self._functional_api_types: set = self._resolve_functional_api_types(
            str(self.root / "project_config.json")
        )
        if self._functional_api_types:
            _Log.ok(
                f"Functional API types (headless-functional): {sorted(self._functional_api_types)}"
            )
        else:
            _Log.ok(
                "Functional API types: none configured (all APIContracts treated as infrastructure)."
            )

        # Modules to skip in run_for_project() — shared/common modules that have
        # no independent business features.  Configurable via
        # project_config.json → stage5.skip_module_ids.
        self._skip_module_ids: set = self._resolve_skip_module_ids(
            str(self.root / "project_config.json")
        )
        if self._skip_module_ids:
            _Log.ok(f"Stage 5 skip list (shared/common modules): {sorted(self._skip_module_ids)}")

        # Name-INDEPENDENT skip: modules flagged is_shared=true in module_manifest.json
        # (e.g. AI-clustered "Application Framework / Common" whose id doesn't match the
        # legacy skip_module_ids). Guarded at the source (Stage 2.5: at most one, non-
        # UI-Track) and re-enforced here (non-UI-Track, fail-safe on ambiguity).
        self._shared_module_info: dict = self._resolve_shared_module_ids(self.root)
        self._shared_module_ids: set = set(self._shared_module_info.keys())
        if self._shared_module_ids:
            _Log.ok(
                f"Stage 5 is_shared skip set (flagged infrastructure, non-UI-Track): "
                f"{sorted(self._shared_module_ids)}"
            )

        # ── Language Profile Registry (Stage 5 only) ──────────────────────────
        # Loads prompts/language_profiles.yaml once at startup.  Used to inject
        # {LANGUAGE_HINTS} into story generator, corrector, and critic prompts.
        # Graceful fallback: if YAML missing or pyyaml not installed, registry
        # operates in no-op mode — {LANGUAGE_HINTS} is replaced with "".
        # Zero Stage 4 impact: plugin_base / plugin_registry not touched.
        if _STAGE5_PROFILE_REGISTRY_AVAILABLE:
            self._lang_registry = Stage5ProfileRegistry.load(project_root=self.root)
            if self._lang_registry.is_loaded:
                _Log.ok(
                    f"Stage5ProfileRegistry loaded: "
                    f"{self._lang_registry.list_registered_tags()} profiles."
                )
            else:
                _Log.warn(
                    "Stage5ProfileRegistry: language_profiles.yaml not found or "
                    "pyyaml not installed — {{LANGUAGE_HINTS}} will be empty."
                )
        else:
            self._lang_registry = None
            _Log.warn(
                "stage5_language_profile module not importable — "
                "{{LANGUAGE_HINTS}} will be empty for all MFUs."
            )

        _Log.ok(
            f"FeatureStoryAgent initialised (Option B two-stage) | "
            f"root={self.root} | max_iter={max_iterations} | "
            f"focal-anchor architecture (full SRS + attention guidance)"
        )

    # -----------------------------------------------------------------------
    # Critic call with context-budget guard (proactive trim + empirical retry)
    # -----------------------------------------------------------------------
    def _run_critic_call(self, srs_content, final_manifest, language_hints, mfu_id, system_prompt):
        """Run the critic LLM call with a two-tier context-window guard.

        Tier 1 (proactive): _fit_srs_for_critic trims by a CJK-aware token ESTIMATE.
        Tier 2 (empirical, authoritative): if the provider still rejects the call with a
        context-length error, we parse the ACTUAL 'maximum … requested …' token counts from
        the error, trim the SRS by that exact ratio (with a safety margin), rebuild the prompt
        and retry. This is self-calibrating — it does not depend on the estimate matching the
        provider's real tokeniser (dense CJK + markdown tables tokenise far below the generic
        chars/token ratios), which is why an estimate-only guard could silently miss. Trimming
        the SRS only ever reduces critic context (it still validates against the retained,
        focal-first portion); it never fabricates or alters story content."""
        fitted = _fit_srs_for_critic(
            srs_content,
            final_manifest,
            self._crit_template,
            language_hints,
            self._critic_max_tokens,
            mfu_id,
        )
        _ctx_re = re.compile(
            r"maximum context length is (\d+) tokens.*?requested (\d+) tokens",
            re.IGNORECASE | re.DOTALL,
        )
        last_exc = None
        for _attempt in range(4):
            prompt = _build_critic_prompt(
                template=self._crit_template,
                srs_content=fitted,
                generator_output=final_manifest,
                language_hints=language_hints,
            )
            try:
                return self._stage5_llm.complete(system_prompt=system_prompt, user_prompt=prompt)
            except Exception as e:
                last_exc = e
                m = _ctx_re.search(str(e))
                if not m or _attempt >= 3 or not fitted:
                    raise
                maximum, requested = int(m.group(1)), int(m.group(2))
                # Trim the SRS so the WHOLE payload fits, removing as LITTLE as possible to
                # preserve critic context quality. We scale current SRS length by the overage
                # ratio (maximum/requested) times a PROGRESSIVE safety factor: the first retry
                # is gentle (~5% margin) so we cut close to the true overage; the factor only
                # tightens on a subsequent retry if the gentle cut still overflows. The retry
                # loop guarantees convergence, so we can afford to start gentle. (The focal SRS
                # is at position 0, so trimming the tail drops companion/sibling specs, not the
                # focal screen the stories are about.)
                _safety = (0.95, 0.88, 0.80, 0.70)[min(_attempt, 3)]
                keep = max(0.05, (maximum / max(1, requested)) * _safety)
                new_len = max(20_000, int(len(fitted) * keep))
                if new_len >= len(fitted):
                    new_len = int(len(fitted) * 0.9)
                _Log.warn(
                    f"[CRITIC-BUDGET] {mfu_id}: provider context overflow "
                    f"(requested {requested:,} > max {maximum:,}); empirically trimming SRS "
                    f"{len(fitted):,}→{new_len:,} chars and retrying (attempt {_attempt + 2}/4)."
                )
                fitted = fitted[:new_len] + (
                    "\n\n[... SRS TRUNCATED FOR CRITIC CONTEXT BUDGET (empirical retry) — "
                    "oversized MFU; focal/early sections retained ...]"
                )
        if last_exc:
            raise last_exc

    # -----------------------------------------------------------------------
    # Fix5(item2): business-facing function-label polish
    # -----------------------------------------------------------------------
    @staticmethod
    def _parse_label_map(raw: str) -> dict:
        """Tolerantly parse the label-polish LLM response into
        {function_id: {"label": <title>, "description": <2-3 sentences>}}.

        Accepts a JSON object of shape
        {"labels": [{"id": "...", "label": "...", "description": "..."}, ...]}.
        Strips code fences; falls back to json_repair when strict json.loads fails.
        Returns {} on any failure so the caller keeps the deterministic labels."""
        if not raw or not isinstance(raw, str):
            return {}
        txt = raw.strip()
        if "```" in txt:
            # keep the content of the first fenced block, else strip stray fences
            m = re.search(r"```(?:json)?\s*(.*?)```", txt, re.DOTALL)
            txt = (m.group(1) if m else txt.replace("```", "")).strip()
        # isolate the outermost JSON object
        i, j = txt.find("{"), txt.rfind("}")
        if i != -1 and j != -1 and j > i:
            txt = txt[i : j + 1]
        obj = None
        try:
            obj = json.loads(txt)
        except Exception:
            try:
                import json_repair as _jr  # type: ignore

                obj = _jr.loads(txt)
            except Exception:
                return {}
        out: dict = {}
        if isinstance(obj, dict):
            _items = obj.get("labels") or obj.get("functions") or []
            if isinstance(_items, list):
                for it in _items:
                    if isinstance(it, dict):
                        _id = str(it.get("id") or "").strip()
                        _lb = str(it.get("label") or "").strip()
                        _ds = str(it.get("description") or "").strip()
                        if _id and (_lb or _ds):
                            out[_id] = {"label": _lb, "description": _ds}
        return out

    def _polish_function_labels(self, feature: dict, focal_fns: list, mfu_id: str) -> dict:
        """Rewrite verbatim event-handler function labels into concise BUSINESS
        sub-feature titles, grounded strictly in the SRS event evidence.

        Determinism is preserved: this ONLY rewrites the human-readable ``label``.
        Function ``id``, ``l2_source_ref`` and the function COUNT (the Stage-5b story
        ceiling) are never touched. The verbatim event name is retained as a trailing
        parenthetical so engineer-facing traceability survives alongside the business
        phrasing. Fully non-blocking: any failure keeps the deterministic labels."""
        fns = feature.get("functions") or []
        if not fns:
            return feature

        # SRS evidence per function, keyed by l2_source_ref (from the focal-functions list).
        _desc_by_ref: dict = {}
        for f in focal_fns or []:
            _ref = (f.get("l2_ref") or "").strip()
            if _ref:
                _desc_by_ref[_ref] = (f.get("description") or f.get("label") or "").strip()

        _items = []
        for fn in fns:
            _ref = (fn.get("l2_source_ref") or "").strip()
            # Skip the collapsed navigation-dispatch group (GAP:: ref): its label is
            # already an intentional business summary, not a bare event name.
            if _ref.startswith("GAP::"):
                continue
            _items.append(
                {
                    "id": fn.get("id", ""),
                    "current_label": (fn.get("label") or "")[:240],
                    "evidence": _desc_by_ref.get(_ref, (fn.get("label") or ""))[:400],
                }
            )
        if not _items:
            return feature

        _biz = feature.get("business_context") or {}
        _feature_title = feature.get("title", "")
        system_prompt = (
            "You are a business analyst turning legacy UI/event handlers into an Agile "
            "backlog. You rewrite each low-level function label into a concise, "
            "business-meaningful SUB-FEATURE title that a product owner would recognise. "
            "You never invent capabilities: every title must be supported by the supplied "
            "evidence. Output STRICT JSON only."
        )
        _payload = {
            "feature_title": _feature_title,
            "current_state": (_biz.get("current_state") or "")[:600],
            "functions": _items,
        }
        user_prompt = (
            "For each function, produce a business sub-feature TITLE and a short "
            "DESCRIPTION.\n\n"
            "RULES:\n"
            "1. `label` (title): 4–10 words, phrased as a user/business capability (what it "
            "lets the user do or what the system delivers), NOT the technical event name. "
            "Retain the original technical token in trailing parentheses for traceability, "
            "e.g. 'Launch the selected configuration module (lvMenu_DblClick)'.\n"
            "2. `description`: 2–3 complete sentences expanding the sub-feature — the user-facing "
            "behaviour, the trigger, and the outcome/business value. Written for a product "
            "owner, not a developer.\n"
            "3. Ground BOTH fields STRICTLY in the provided `evidence`. Do NOT add behaviour "
            "the evidence does not state; if the evidence is thin, stay close to it rather than "
            "inventing. No invented fields, records, or error paths.\n"
            "4. Be specific — avoid generic phrasing like 'Handle event' or 'Process the form'.\n"
            "5. Return EXACTLY one entry per input id — same ids, same count.\n\n"
            "Return JSON of the form:\n"
            '{ "labels": [ { "id": "<function id>", "label": "<business title (token)>", '
            '"description": "<2-3 sentence business explanation>" } ] }\n\n'
            f"INPUT:\n{json.dumps(_payload, ensure_ascii=False)}"
        )
        try:
            raw = self._stage5_llm.complete(system_prompt=system_prompt, user_prompt=user_prompt)
        except Exception as e:
            _Log.warn(
                f"[FN-POLISH] {feature.get('id', '?')}: label polish skipped (LLM error: {e}); keeping deterministic labels."
            )
            return feature

        mapping = self._parse_label_map(raw)
        if not mapping:
            _Log.warn(
                f"[FN-POLISH] {feature.get('id', '?')}: no parseable labels returned; keeping deterministic labels."
            )
            return feature

        _changed = 0
        _new_fns = []
        for fn in fns:
            _fid = fn.get("id", "")
            _entry = mapping.get(_fid) or {}
            _nl = (_entry.get("label") or "").strip()
            _nd = (_entry.get("description") or "").strip()
            _fn2 = dict(fn)
            _applied = False
            # Title: accept only a plausible, changed one-liner (guards against a
            # too-short or unchanged echo).
            if 8 <= len(_nl) <= 240 and _nl.lower() != (fn.get("label") or "").strip().lower():
                _fn2["label"] = _nl[:240]
                _applied = True
            # Description: accept a substantive 2-3 sentence body.
            if 20 <= len(_nd) <= 1200:
                _fn2["description"] = _nd[:1200]
                _applied = True
            if _applied:
                _new_fns.append(_fn2)
                _changed += 1
            else:
                _new_fns.append(fn)  # unchanged / GAP group / rejected → keep deterministic
        if _changed:
            feature = {**feature, "functions": _new_fns}
            _Log.ok(
                f"[FN-POLISH] {feature.get('id', '?')}: enriched {_changed}/{len(fns)} "
                f"function(s) with business sub-feature title + description "
                f"(ids/anchors/count unchanged)."
            )
        return feature

    def _trigger_neo4j_export(self):
        """Build the Stage-5 Feature & Story Neo4j graph after an all-modules `features` run.

        Mirrors the global export performed by `cmd_run`, but is FULLY GUARDED: by the time
        this runs, every per-module features_stories.json is already written, so a graph-export
        problem must never crash the command. (This method previously did not exist, which
        crashed the all-modules `features` path with AttributeError: '...has no attribute
        _trigger_neo4j_export'.) Best-effort — on any failure it logs a warning and returns;
        the graph can also be rebuilt later via the `run`/`neo4j` commands."""
        try:
            try:
                from .neo4j_exporter import Neo4jExporter  # type: ignore
            except Exception:
                from neo4j_exporter import Neo4jExporter  # type: ignore
            # Resolve the project base generically (the dir whose 'modules/' we just processed).
            proj_base = next((p.parent for p in self.root.glob("projects/*/modules")), self.root)
            exporter = Neo4jExporter(project_root=str(self.root))
            exporter.build_feature_story_graph()
            out_path = proj_base / "output" / "feature_story_graph.cypher"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            exporter.export(str(out_path))
            _Log.ok(f"[NEO4J] Stage-5 feature/story graph exported → {out_path}")
        except Exception as e:
            _Log.warn(f"[NEO4J] Stage-5 graph export skipped (non-blocking): {e}")

    # -----------------------------------------------------------------------
    # Stage 5 model resolution
    # -----------------------------------------------------------------------

    @staticmethod
    def _resolve_stage5_model(config_path: str) -> str | None:
        """
        Returns the explicit ``stage5.model`` override string, or ``None``.

        Only checks for an explicit ``project_config.json["stage5"]["model"]``
        key.  If none is found, the caller constructs
        ``LLMClient(use_reasoning_model=True)`` which resolves the provider's
        reasoning model automatically from config — no hardcoding needed here.

        This design means ``_resolve_stage5_model`` never touches provider names,
        model strings, or token limits.  All of that is LLMClient's responsibility.

        Config file search order (mirrors LLMClient._load_llm_config):
          1. RIP_LLM_CONFIG_PATH env var
          2. Explicit config_path argument (e.g. <cwd>/project_config.json)
          3. <cwd>/projects/sample_project/project_config.json
          4. Walk up from this module's directory
        """
        import os as _os

        candidates: list = []

        # 1. Env-var override — same priority as LLMClient
        env_cfg = _os.getenv("RIP_LLM_CONFIG_PATH")
        if env_cfg:
            candidates.append(Path(env_cfg))

        # 2. Explicit caller argument
        if config_path:
            candidates.append(Path(config_path))

        # 3. projects/sample_project sub-path — mirrors LLMClient candidate #2
        candidates.append(Path(_os.getcwd()) / "projects/sample_project/project_config.json")

        # 4. Walk up from this module's directory — mirrors LLMClient candidate #3+
        module_dir = Path(__file__).resolve().parent
        for ancestor in [module_dir] + list(module_dir.parents):
            candidate = ancestor / "project_config.json"
            if candidate not in candidates:
                candidates.append(candidate)

        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                cfg = json.loads(candidate.read_text(encoding="utf-8-sig"))
                # Only path: explicit stage5.model override in config.
                # If absent, caller uses LLMClient(use_reasoning_model=True) —
                # no provider name or model string is hardcoded here.
                stage5_model = cfg.get("stage5", {}).get("model")
                if stage5_model:
                    return stage5_model
            except Exception:
                continue

        return None

    @staticmethod
    def _resolve_stage5a_threshold(config_path: str) -> int:
        """
        Resolves the Stage 5a focal-only file count threshold.

        Resolution order (first valid integer wins):
          1. llm.providers.{active_provider}.stage5_focal_only_threshold
             Per-provider override — use when a specific model handles large
             contexts better or worse than the global default.
          2. stage5.focal_only_threshold
             Global default — applies to all providers unless overridden.
          3. Hardcoded default: 8

        When the number of SRS files for an MFU exceeds this threshold, Stage 5a
        switches to condensed mode (focal doc full + supporting file summaries).
        Stage 5b always receives the full SRS content regardless of threshold.

        Provider-specific guidance (set in llm.providers.{name}.stage5_focal_only_threshold).
        For 1M-context models the INPUT is no longer the limiter; the practical cap is
        the OUTPUT budget (generated stories + thinking tokens):
          DeepSeek v4-pro    → 20  (1M input, 384K output — most capable; largest headroom)
          Anthropic Sonnet 5 → 20  (1M input by DEFAULT (no beta header), 128K output — ample)
          Gemini 3.5 Flash   → 20  (1M input; 65K output — input fine, guard output
                                    truncation via batch_inline_max)
          OpenAI GPT-5/o3    → 8   (smaller input context; output shared with reasoning/thinking)
        """
        import os as _os

        candidates: list = []

        env_cfg = _os.getenv("RIP_LLM_CONFIG_PATH")
        if env_cfg:
            candidates.append(Path(env_cfg))
        if config_path:
            candidates.append(Path(config_path))
        candidates.append(Path(_os.getcwd()) / "projects/sample_project/project_config.json")
        module_dir = Path(__file__).resolve().parent
        for ancestor in [module_dir] + list(module_dir.parents):
            c = ancestor / "project_config.json"
            if c not in candidates:
                candidates.append(c)

        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                cfg = json.loads(candidate.read_text(encoding="utf-8-sig"))

                # Priority 1: per-provider override
                active = cfg.get("llm", {}).get("active_provider", "")
                if active:
                    provider_cfg = cfg.get("llm", {}).get("providers", {}).get(active, {})
                    per_provider = provider_cfg.get("stage5_focal_only_threshold")
                    if isinstance(per_provider, int) and per_provider > 0:
                        return per_provider

                # Priority 2: global stage5 default
                global_val = cfg.get("stage5", {}).get("focal_only_threshold")
                if isinstance(global_val, int) and global_val > 0:
                    return global_val

            except Exception:
                continue

        return 8  # Enterprise hardcoded default

    @staticmethod
    def _resolve_ui_bearing_srs_types(config_path: str) -> set:
        """
        Returns the set of SRS document types that indicate a user-facing surface.

        Any MFU whose focal SRS file resolves to one of these types is treated as
        functional (not infrastructure) by both P2-guard and P10-guard.

        Resolution order (first valid list wins):
          1. project_config.json["stage5"]["ui_bearing_srs_types"]
          2. Hardcoded default: {"UIBlueprint"}

        Config key (list of strings):
          "stage5": {
            "ui_bearing_srs_types": ["UIBlueprint", "BatchBlueprint"]
          }

        Add new SRS types here as new legacy technology stacks are onboarded —
        e.g. "JSPBlueprint", "ASPXBlueprint", "CobolScreenBlueprint".  No code
        changes are required; only a project_config.json update is needed.

        Uses the same config-file search order as _resolve_stage5a_threshold.
        """
        import os as _os

        candidates: list = []

        env_cfg = _os.getenv("RIP_LLM_CONFIG_PATH")
        if env_cfg:
            candidates.append(Path(env_cfg))
        if config_path:
            candidates.append(Path(config_path))
        candidates.append(Path(_os.getcwd()) / "projects/sample_project/project_config.json")
        module_dir = Path(__file__).resolve().parent
        for ancestor in [module_dir] + list(module_dir.parents):
            c = ancestor / "project_config.json"
            if c not in candidates:
                candidates.append(c)

        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                cfg = json.loads(candidate.read_text(encoding="utf-8-sig"))
                types_list = cfg.get("stage5", {}).get("ui_bearing_srs_types")
                if isinstance(types_list, list) and types_list:
                    return set(types_list)
            except Exception:
                continue

        return {"UIBlueprint"}  # Safe default: only UIBlueprint is UI-bearing

    @staticmethod
    def _resolve_skip_module_ids(config_path: str) -> set:
        """
        Returns the set of module IDs that Stage 5 should skip entirely.

        Shared/common modules (MOD-SHARED, MOD-COMMON, etc.) contain only
        utility code and cross-cutting infrastructure with no independent
        business features or user stories.  Running Stage 5 on them wastes
        LLM tokens and produces empty or infrastructure-only outputs.

        Supports both exact IDs and prefix wildcards using fnmatch syntax:
          "MOD-SHARED"   → exact match
          "MOD-SHARED*"  → prefix match (MOD-SHARED-VB6, MOD-SHARED-UTILS, …)

        Safe downstream: the Neo4j exporter handles missing features_stories.json
        gracefully.  The SRS linker indexes SRS source files directly, so L2/L3
        evidence in other modules is unaffected.

        Resolution order (first valid list wins):
          1. project_config.json["stage5"]["skip_module_ids"]
          2. Hardcoded default: set()  (nothing skipped unless explicitly configured)

        Config key (list of strings):
          "stage5": {
            "skip_module_ids": ["MOD-SHARED", "MOD-COMMON", "MOD-CORE"]
          }

        Uses the same config-file search order as _resolve_stage5a_threshold.
        """
        import fnmatch as _fnmatch  # noqa — used in run_for_project
        import os as _os

        candidates: list = []
        env_cfg = _os.getenv("RIP_LLM_CONFIG_PATH")
        if env_cfg:
            candidates.append(Path(env_cfg))
        if config_path:
            candidates.append(Path(config_path))
        candidates.append(Path(_os.getcwd()) / "projects/sample_project/project_config.json")
        module_dir_path = Path(__file__).resolve().parent
        for ancestor in [module_dir_path] + list(module_dir_path.parents):
            c = ancestor / "project_config.json"
            if c not in candidates:
                candidates.append(c)

        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                cfg = json.loads(candidate.read_text(encoding="utf-8-sig"))
                ids_list = cfg.get("stage5", {}).get("skip_module_ids")
                if isinstance(ids_list, list):  # empty list is valid (no skips)
                    return set(s.upper() for s in ids_list)
            except Exception:
                continue

        return set()  # Safe default: nothing skipped

    @staticmethod
    def _resolve_shared_module_ids(root) -> dict:
        """
        Returns {MODULE_ID_UPPER: {name, track, artifacts}} for modules that Stage 5
        should skip because they were flagged ``is_shared: true`` in module_manifest.json
        (name-independent counterpart to stage5.skip_module_ids).

        Safety valves (fail safe toward GENERATING — never risk dropping a business module):
          * Re-enforce the non-UI-Track guard here: a UI-Track module is NEVER treated as
            shared, even if flagged (a pure UI-Track module is almost certainly real screens).
            Mixed-Track / Batch-Track shared modules ARE honored — real shared modules
            routinely bundle utilities WITH shared UI shells (login/splash/common dialogs),
            so Mixed-Track is expected and correct to skip.
          * If MORE than one module qualifies, the signal is ambiguous → return {} (skip
            nothing) with a warning.
          * Emits a LOG-ONLY advisory for modules that look like infrastructure but are NOT
            flagged — it never skips them; a human decides.
        """
        root = Path(root)
        candidates = [
            root / "_global" / "module_manifest.json",
            root / "projects" / "sample_project" / "_global" / "module_manifest.json",
        ]
        manifest = None
        for c in candidates:
            if c.exists():
                try:
                    manifest = json.loads(c.read_text(encoding="utf-8-sig"))
                    break
                except Exception:
                    continue
        if not manifest:
            return {}

        mods = manifest.get("modules", []) or []
        flagged: dict = {}
        for m in mods:
            mid = str(m.get("module_id", "")).strip()
            track = str(m.get("execution_track", ""))
            if mid and bool(m.get("is_shared", False)) and track != "UI-Track":
                flagged[mid.upper()] = {
                    "name": m.get("module_name", mid),
                    "track": track,
                    "artifacts": list(m.get("artifacts") or m.get("entry_points") or []),
                }

        # Ambiguity fail-safe: never skip when >1 module claims shared status.
        if len(flagged) > 1:
            _Log.observe(
                f"[is_shared] {len(flagged)} modules flagged shared "
                f"({', '.join(sorted(flagged))}) — ambiguous; skipping NONE (fail-safe)."
            )
            return {}

        # Log-only advisory for suspected-but-unflagged infrastructure (never skips).
        _INFRA_HINT = (
            "framework",
            "shared",
            "common",
            "utilit",
            "infrastructur",
            "global",
            "helper",
        )
        for m in mods:
            mid = str(m.get("module_id", "")).strip().upper()
            if not mid or mid in flagged or bool(m.get("is_shared", False)):
                continue
            track = str(m.get("execution_track", ""))
            text = (str(m.get("module_name", "")) + " " + str(m.get("description", ""))).lower()
            if track != "UI-Track" and any(k in text for k in _INFRA_HINT):
                _Log.observe(
                    f"[is_shared][advisory] '{mid}' looks like shared/infrastructure but is "
                    f"NOT flagged — Stage 5 will generate features for it. If it is infra, add "
                    f"it to stage5.skip_module_ids or set is_shared in module_manifest.json."
                )

        return flagged

    @staticmethod
    def _ensure_condensation_notes(features: list, source_path: str = "") -> None:
        """
        Layer-1 deterministic guarantee (NO LLM call): every feature flagged
        is_infrastructure=true must carry a human-readable ``condensation_note``
        explaining why it has no user stories (for the review UI).

        Behaviour:
          * Fills the note ONLY when it is missing or blank — never overwrites a note
            the model already produced (that one is usually more specific).
          * Templated from fields already present on the feature (source citation /
            document kind / artifact stem) — so it is specific, not generic, and needs
            no network/AI. Works even on the emergency-fallback path where no AI ran.
          * No-op for non-infrastructure features.
        """
        import os as _os
        import re as _re

        _KIND = {
            "APISpec": "an internal API / library layer",
            "APIContracts": "an internal API / service contract",
            "BatchSpec": "a batch / background routine",
            "BatchBlueprint": "a batch / background routine",
            "UIBlueprint": "a reusable UI component",
        }
        for feat in features or []:
            if not feat.get("is_infrastructure"):
                continue
            if str(feat.get("condensation_note") or "").strip():
                continue  # keep the model-provided note
            base = _os.path.basename(str(feat.get("source") or source_path or ""))
            m = _re.search(r"\d+_([A-Za-z]+)_(.+?)\.(?:md|txt)", base)
            doc_kind, stem = (m.group(1), m.group(2)) if m else ("", "")
            kind_phrase = _KIND.get(doc_kind, "system-level infrastructure")
            who = f" ('{stem}')" if stem else ""
            feat["condensation_note"] = (
                f"Auto-classified as infrastructure: this feature represents "
                f"{kind_phrase}{who} with no direct end-user business workflow, so no "
                f"user stories are generated. Flagged for architectural review."
            )

    @staticmethod
    def _module_matches_skip(module_name: str, skip_ids: set) -> bool:
        """
        True when module_name matches any entry in skip_ids.
        Entries ending in '*' are treated as prefix wildcards (fnmatch);
        all others require an exact case-insensitive match.
        """
        import fnmatch as _fnmatch

        name_upper = module_name.upper()
        for pattern in skip_ids:
            if "*" in pattern or "?" in pattern:
                if _fnmatch.fnmatch(name_upper, pattern):
                    return True
            else:
                if name_upper == pattern:
                    return True
        return False

    @staticmethod
    def _resolve_functional_api_types(config_path: str) -> set:
        """
        Returns the set of SRS document types that represent headless-but-functional
        modules (API services, business logic services, batch processors with no
        UI surface).

        BUG-2 fix: MFUs whose focal SRS file resolves to a type in this set are
        treated as FUNCTIONAL (not infrastructure) by P10-guard, even though they
        have no user-facing screen.  This is architecturally distinct from
        ui_bearing_srs_types (which implies a user-facing surface):

          ui_bearing_srs_types → UIBlueprint, BatchBlueprint
            → downstream: React + API stories
          functional_api_types → APIContracts (and project-specific additions)
            → downstream: API-only (headless microservice) stories

        P10-guard logic:
          is_infrastructure = False  ←  ANY file in MFU matches ui_bearing_srs_types
                                        OR ANY file in MFU matches functional_api_types
          is_infrastructure = True   ←  NO file in MFU matches either set
                                        (dead code, startup stubs, utility bridges)

        Resolution order (first valid list wins):
          1. project_config.json["stage5"]["functional_api_types"]
          2. Hardcoded default: set()  (empty — no APIContracts treated as
             functional unless the project team explicitly opts in)

        Config key (list of strings):
          "stage5": {
            "functional_api_types": ["APIContracts"]
          }

        Language-agnostic: add new headless service types (e.g. "COBOLService",
        "RPGServiceProgram", "WSDLContract") as new legacy stacks are onboarded.
        No code changes required; only a project_config.json update is needed.

        Uses the same config-file search order as _resolve_stage5a_threshold.
        """
        import os as _os

        candidates: list = []

        env_cfg = _os.getenv("RIP_LLM_CONFIG_PATH")
        if env_cfg:
            candidates.append(Path(env_cfg))
        if config_path:
            candidates.append(Path(config_path))
        candidates.append(Path(_os.getcwd()) / "projects/sample_project/project_config.json")
        module_dir = Path(__file__).resolve().parent
        for ancestor in [module_dir] + list(module_dir.parents):
            c = ancestor / "project_config.json"
            if c not in candidates:
                candidates.append(c)

        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                cfg = json.loads(candidate.read_text(encoding="utf-8-sig"))
                types_list = cfg.get("stage5", {}).get("functional_api_types")
                if isinstance(types_list, list) and types_list:
                    return set(types_list)
            except Exception:
                continue

        return set()  # Safe default: empty — must be explicitly enabled per project

    # -----------------------------------------------------------------------
    # File I/O helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _write_json_safe(path: Path, data: dict) -> None:
        """
        Write a JSON file reliably on Windows/VirtioFS mounts.

        Root cause: os.replace() (rename) creates a new inode at the old path.
        The Linux VirtioFS client caches inodes and does NOT invalidate on rename,
        so subsequent reads from the Linux side return the old cached content.

        Fix: write directly to the target path in binary mode ("wb").
        "wb" opens the SAME inode (truncates in-place), and VirtioFS invalidates
        the Linux client's page cache for that inode on open-for-write — exactly
        the cache-coherency guarantee we need.  No temp file, no rename.
        """
        import os as _os

        json_bytes = (
            data
            if isinstance(data, bytes)
            else json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        )
        with open(path, "wb") as f:
            f.write(json_bytes)
            f.flush()
            try:
                _os.fsync(f.fileno())
            except OSError:
                pass

    def _write_manifest(self, manifest: dict, mfu_dir: Path) -> None:
        """Write feature_manifest.json into mfu_dir (always, even on parse failure)."""
        out_path = Path(mfu_dir) / MANIFEST_FILE_NAME
        self._write_json_safe(out_path, manifest)
        _Log.act(f"Written: {out_path}")

    def _write_output(self, output: dict, mfu_dir: Path) -> None:
        """Write features_stories.json into mfu_dir (always, even on parse failure)."""
        out_path = Path(mfu_dir) / OUTPUT_FILE_NAME
        self._write_json_safe(out_path, output)
        _Log.act(f"Written: {out_path}")

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def run_for_mfu(
        self,
        mfu_dir: Path,
        human_guidance: str | None = None,
        prior_output: dict | None = None,
        edit_targets=None,
        manifest_guidance: str | None = None,
        request_id: str | None = None,
    ) -> dict | None:
        """
        Full two-stage ReAct pipeline for a single MFU directory.

        Stage 5a: SRS → Feature Manifest (feature_manifest.json)
        Stage 5b: Feature Manifest + SRS → User Stories (features_stories.json)

        human_guidance (#42): optional PM-supplied instruction for a *human-feedback
        regeneration* (the `revise` CLI command / app review action). When provided, it is
        injected as an AUTHORITATIVE guidance block into the Stage 5a + 5b generator prompts,
        the output is fully re-validated through the critic + schema + the #31 grounding
        gates (so human feedback steers but the gates still guard), and the feedback is
        recorded in generation_metadata.human_feedback_log for audit.

        Returns the final features_stories dict, or None only if nothing at all
        could be produced (which should never happen given the skeleton fallback).
        """
        mfu_dir = Path(mfu_dir).resolve()
        module_dir = mfu_dir.parent.parent  # stage4_specs/../ = module root

        # ── OBSERVE ─────────────────────────────────────────────────────────
        _Log.observe(f"Scanning MFU directory: {mfu_dir}")
        srs_files = _find_srs_files(mfu_dir)
        if not srs_files:
            _Log.warn(f"No SRS files found in {mfu_dir}. Writing empty skeleton.")
            meta = _resolve_module_metadata(mfu_dir, module_dir)
            _ctx = _load_module_context(self.root, meta["module_id"])
            skeleton = _build_skeleton_output(meta, str(mfu_dir))
            skeleton["module_name"] = _ctx.get("module_name") or skeleton["module_name"]
            skeleton["module_business_description"] = _ctx.get("description", "")
            self._write_output(skeleton, mfu_dir)
            return None

        _Log.observe(f"Found {len(srs_files)} SRS file(s): {[f.name for f in srs_files]}")
        srs_content = _read_srs_content(srs_files)
        meta = _resolve_module_metadata(mfu_dir, module_dir)
        technology = _resolve_technology(mfu_dir, self.root)

        # source_path now reflects the FOCAL SRS file (position 0 after reordering)
        # rather than the alphabetically-first file (DEFECT-2 Layer 1 fix).
        source_path = str(srs_files[0].relative_to(self.root)) if srs_files else str(mfu_dir)

        mfu_id = meta["mfu_id"]
        module_id = meta["module_id"]
        module_name = meta["module_name"]

        # Layer 2: load refined module context from Stage 2.7 module_manifest.json
        module_context = _load_module_context(self.root, module_id)

        # #42 — stash PM human-feedback guidance as an instance attribute so every prompt
        # builder in this run (Stage 5a, 5a-fallback, 5b) can prepend it without threading it
        # through method signatures. Cleared in the finally block at the end of the run.
        self._human_guidance = (str(human_guidance).strip() or None) if human_guidance else None
        # #42 Fix — Stage 5a (manifest) gets a SEPARATE, lighter guidance. In edit mode the full
        # guidance renders the current stories; feeding that to the manifest generator makes it
        # echo a 'stories' array (rejected by the closed manifest schema) and wastes tokens. When
        # manifest_guidance is supplied (edit mode = plain feedback) Stage 5a uses it; otherwise it
        # falls back to the full guidance (regenerate / normal runs — behaviour unchanged).
        self._manifest_guidance = (
            (str(manifest_guidance).strip() or None) if manifest_guidance else self._human_guidance
        )
        if self._human_guidance:
            _Log.observe(
                f"[HUMAN-REVISION] {mfu_id}: regenerating with PM guidance "
                f"({len(self._human_guidance)} chars) — output will be re-validated by the critic + grounding gates."
            )

        _Log.observe(
            f"Resolved — MFU: {mfu_id} | Module: {module_id} ({module_name}) | "
            f"Technology: {technology}"
        )
        if module_context.get("module_name"):
            _Log.observe(
                f"Module context loaded: '{module_context['module_name']}' "
                f"(from Stage 2.7 manifest)"
            )

        # ── Stage 5a SRS content: condensed when file count exceeds threshold ──
        # When an MFU has many SRS files (e.g. 39 for an 18-tab master-data screen
        # or a COBOL program with many auxiliary data specs), concatenating all of
        # them causes reasoning-budget saturation in DeepSeek v4-pro: the model
        # exhausts its 384K thinking budget on the massive input and produces a
        # short, unparseable final answer.
        #
        # Fix: Stage 5a receives focal-only content (full focal doc + 400-char
        # executive summaries of supporting files). This gives the LLM everything
        # it needs for feature classification and function counting without the
        # noise of 30+ full API data schemas.
        #
        # Stage 5b always receives full srs_content — story generation needs the
        # complete endpoint contracts and data field details.
        #
        # Works for both event-driven (PB/VB6 UIBlueprint) and procedural (COBOL
        # BatchBlueprint): the focal document in both cases is self-sufficient for
        # Stage 5a (UIBlueprint has S.5 events; BatchBlueprint has job_steps).
        # Stage 5a content selection — simplified 2-path approach.
        # Always pre-compute focal-only content (UIBlueprint only, no summaries)
        # as the universal fallback and as the primary content for large MFUs.
        # ── STAGE 5a: Feature Manifest — focal anchor architecture ──────────
        # FOCAL ANCHOR: inject the primary entry-point artifact into the system
        # prompt to guide the LLM's attention without truncating the SRS content.
        # The full SRS is sent to BOTH Stage 5a and Stage 5b so the KV cache
        # for the large SRS prefix is reused across stages (>90% cache hit rate).
        focal_anchor = _resolve_focal_artifact(mfu_dir) or srs_files[0].stem

        # ── Layer 4: Deterministic function injection ─────────────────────
        # Extract verbatim function labels from the linker BEFORE Stage 5a so the
        # LLM receives them as pre-filled constraints, eliminating hallucinated CRUD.
        # Language-agnostic priority chain:
        #   UIBlueprint  (PB/VB6)  → S5::EVENT-NNN  (event-driven languages)
        #   BatchBlueprint (COBOL)  → BATCH::STEP-NNN (procedural/batch languages)
        #   API-only / unknown      → empty list → LLM derives freely (graceful fallback)
        #
        # Early SRS-type probe: use filename-only detection (no focal_fns yet) so
        # _extract_focal_functions can skip S5::EVENT-NNN Priority 1 for BatchBlueprint
        # MFUs.  _idx_batch dual-registers S5::EVENT-NNN aliases for COBOL job_steps;
        # without this gate Priority 1 always fires for COBOL and returns wrong refs.
        _focal_file_name = srs_files[0].name if srs_files else ""
        _early_srs_type = _resolve_srs_type([], focal_filename=_focal_file_name)
        try:
            from .srs_linker import SRSPhysicalLinker

            _linker_5a = SRSPhysicalLinker(mfu_dir)
            _focal_fns = _extract_focal_functions(_linker_5a, mfu_id, srs_type=_early_srs_type)
        except Exception as _e:
            _Log.warn(f"[Layer 4] Could not build linker for focal functions: {_e}")
            _focal_fns = []

        # ── #34 — passive (event-less) UIBlueprint guard ────────────────────────────
        # A UIBlueprint focal with ZERO S.5 events is a PASSIVE component (no user-actionable
        # operations). Left unconstrained, the generator free-associates a fabricated feature
        # (the MFU-006 'login' hallucination). We inject an authoritative grounding constraint
        # via the #42 guidance-prepend so the generator derives ONLY from the literal S.3
        # controls and prefers an infrastructure classification over invented workflows. The
        # output is still re-validated by the critic + the #31 grounding gates. Language-agnostic:
        # keys off srs_type + S.5 event count, no language-specific logic.
        if _early_srs_type == "UIBlueprint" and not _focal_fns:
            _passive = (
                "PASSIVE UI COMPONENT CONSTRAINT (AUTHORITATIVE): the focal SRS has NO S.5 events "
                "— it has no user-actionable operations. Do NOT invent any user workflow "
                "(no login/authentication, search, CRUD, navigation, save, etc.). Derive content "
                "STRICTLY from the literal S.3 controls/fields that are actually present. If the "
                "component exposes no user-visible behaviour of its own, set is_infrastructure=true "
                "with minimal or no user stories — never fabricate functionality to fill the gap."
            )
            self._human_guidance = (
                (self._human_guidance + "\n\n" + _passive)
                if getattr(self, "_human_guidance", None)
                else _passive
            )
            # the passive constraint is a manifest-relevant grounding rule → give it to Stage 5a too
            self._manifest_guidance = (
                (self._manifest_guidance + "\n\n" + _passive)
                if getattr(self, "_manifest_guidance", None)
                else _passive
            )
            _Log.observe(
                f"[PASSIVE-GUARD] {mfu_id}: event-less UIBlueprint focal — generator constrained "
                f"to S.3-grounded content (no free-association); critic + grounding gates still apply."
            )

        # Resolve per-provider inline thresholds from project_config.json.
        # Defaults: event_inline_max=20 (UI events), batch_inline_max=50 (COBOL steps).
        # Override via llm.providers.{name}.event_inline_max / batch_inline_max.
        _cfg_path = self.root / "project_config.json"
        try:
            _cfg_raw = json.loads(_cfg_path.read_text(encoding="utf-8-sig"))
            _active = _cfg_raw.get("llm", {}).get("active_provider", "")
            _prov = _cfg_raw.get("llm", {}).get("providers", {}).get(_active, {})
            _event_inline_max = int(_prov.get("event_inline_max", 20))
            _batch_inline_max = int(_prov.get("batch_inline_max", 50))
        except Exception:
            _event_inline_max, _batch_inline_max = 20, 50

        focal_anchor_functions = _build_focal_anchor_functions_block(
            _focal_fns,
            mfu_id,
            event_inline_max=_event_inline_max,
            batch_inline_max=_batch_inline_max,
        )
        _src_type = _focal_fns[0]["source_type"] if _focal_fns else "none"
        _threshold = _batch_inline_max if _src_type == "batch_step" else _event_inline_max
        if len(_focal_fns) > _threshold:
            _n_disp = sum(1 for f in _focal_fns if _is_dispatch(f))
            _n_nd = len(_focal_fns) - _n_disp
            if _n_nd == 0:
                _mode = "CONDENSED/pure-dispatch"
            elif _n_disp == 0:
                _mode = "INLINE/all-non-dispatch"
            else:
                _mode = f"SEMANTIC-FILTER/{_n_nd}nd+{_n_disp}disp"
        else:
            _mode = "INLINE"
        _Log.observe(
            f"[Layer 4] Focal functions: {len(_focal_fns)} entries "
            f"({_src_type}) | mode={_mode} | threshold={_threshold}"
        )

        # ── Layer 5: Language-specific hints ─────────────────────────────
        # Resolve the SRS document type from the focal-function source_type
        # (batch_step → BatchBlueprint, ui_event/none → UIBlueprint).
        # Used to select the correct l2_source_ref format from the profile.
        _focal_file_name = srs_files[0].name if srs_files else ""
        _srs_type = _resolve_srs_type(_focal_fns, focal_filename=_focal_file_name)
        language_hints = _build_language_hints_block(
            registry=self._lang_registry,
            technology_tag=technology,
            srs_type=_srs_type,
        )
        _Log.observe(
            f"[Layer 5] Language hints: technology='{technology}' | "
            f"srs_type='{_srs_type}' | hints_len={len(language_hints)}"
        )

        # ── STAGE 5a: Feature Manifest ────────────────────────────────────
        _Log.reason("Stage 5a: Deriving Feature Manifest from SRS...")
        _Log.observe(
            f"Stage 5a focal anchor: '{focal_anchor}' | {len(srs_files)} SRS file(s) | "
            f"full SRS sent (cache-compatible with Stage 5b)."
        )
        feature_manifest = self._run_stage5a(
            mfu_dir,
            srs_content,
            mfu_id,
            module_id,
            module_name,
            source_path,
            technology,
            module_context=module_context,
            focal_anchor=focal_anchor,
            focal_anchor_functions=focal_anchor_functions,
            language_hints=language_hints,
        )
        # S.3 correction: remove any hallucinated controls using linker's verbatim index
        feature_manifest = _correct_bounding_box_controls(feature_manifest, mfu_dir, mfu_id)

        # Review-fix C2 (residual): reconcile l1_source to the ACTUAL focal SRS document
        # type on ALL paths. The deterministic/fallback paths already build it correctly,
        # but the LLM-success path trusts the model's l1_source, which can mislabel a
        # batch/API MFU as UIBlueprint (with a phantom screen_id). Deterministic + idempotent.
        try:
            if isinstance(feature_manifest, dict):
                feature_manifest["l1_source"] = _build_l1_source(
                    mfu_id, source_path, module_id, _extract_mfu_seq(mfu_id)
                )
        except Exception as _l1_err:
            _Log.warn(
                f"[L1-RECONCILE] {mfu_id}: could not reconcile l1_source ({_l1_err}); non-fatal."
            )

        # ── Defensive field normalisation: function_label → label ─────────────
        # The LLM occasionally outputs "function_label" despite the prompt using "label".
        # Silently rename before schema validation so the validator sees the correct key.
        # Also strip non-dict entries (LLM sometimes emits plain string IDs instead of
        # {id, label, l2_source_ref} objects), which crash _build_srs_anchor_index.
        for _feat in feature_manifest.get("features", []):
            _raw_fns = _feat.get("functions", [])
            _dict_fns = [fn for fn in _raw_fns if isinstance(fn, dict)]
            if len(_dict_fns) < len(_raw_fns):
                _Log.warn(
                    f"Stage 5a [{_feat.get('id', '?')}]: stripped "
                    f"{len(_raw_fns) - len(_dict_fns)} non-dict function "
                    f"entry/entries (LLM emitted string IDs instead of objects) — "
                    f"functions ceiling reduced accordingly."
                )
                _feat["functions"] = _dict_fns
            for _fn in _feat.get("functions", []):
                if "function_label" in _fn and "label" not in _fn:
                    _fn["label"] = _fn.pop("function_label")

        # ── #27 (M1+P1): deterministic functions[] = stable story ceiling ────────
        # functions[] sets the Stage-5b ceiling. Leaving it LLM-derived made the count
        # swing run-to-run (e.g. 6 vs 18 for the same menu SRS) once the generator began
        # filling the ceiling (#26). Here we bind it deterministically to the focal-
        # functions list (already computed above as _focal_fns) — exactly as controls[]
        # is auto-injected — so the ceiling is repeatable. Skipped for infrastructure
        # features (their functions[] must stay []). The LLM still authors story CONTENT
        # in Stage 5b; only the operation COUNT/anchors are made deterministic.
        if _focal_fns:
            for _feat in feature_manifest.get("features", []):
                if _feat.get("is_infrastructure"):
                    continue
                _det_fns = _build_deterministic_functions(
                    _focal_fns, _feat.get("id", ""), nav_collapse_min=self._nav_collapse_min
                )
                if _det_fns:
                    _prev_n = len(_feat.get("functions", []) or [])
                    _feat["functions"] = _det_fns
                    _Log.ok(
                        f"[FN-DETERMINISTIC] {_feat.get('id', '?')}: functions[] set to "
                        f"{len(_det_fns)} (deterministic from {len(_focal_fns)} S.5 events; "
                        f"was {_prev_n} LLM-derived) — stable story ceiling."
                    )

        # ── Review-fix P3: guarantee >=1 function for every NON-INFRA feature ─────
        # A non-infrastructure feature with functions == [] is an UNWINNABLE critic state:
        # Gate 6a fails if it has any stories (stories > 0 functions) and Gate 6a-ZERO fails
        # if it has none — the ECBDRVR batch-driver case (no indexed S.5 events / batch steps
        # → _build_deterministic_functions returns []). The Stage-5b runtime uses a fallback
        # ceiling of 2, but the manifest functions[] stayed empty and the critic reads it
        # directly → permanent false FAIL. Synthesize ONE primary function (grounded in the
        # feature's first real l2_source, else a GAP marker) so the manifest is self-consistent
        # and the critic's len(functions) matches the runtime ceiling. Deterministic; infra
        # features are untouched (their functions[] MUST stay []).
        for _feat in feature_manifest.get("features", []):
            if _feat.get("is_infrastructure"):
                continue
            if not (_feat.get("functions") or []):
                _fid = _feat.get("id", "")
                _l2s = [r for r in (_feat.get("l2_sources") or []) if isinstance(r, str) and r]
                _primary_ref = _l2s[0] if _l2s else f"GAP::{mfu_id}::NO_PRIMARY_OPERATION"
                _title = (_feat.get("title") or "Primary operation").strip()
                _feat["functions"] = [
                    {
                        "id": f"{_fid}-FN1",
                        "label": f"{_title} (primary operation)"[:240],
                        "l2_source_ref": _primary_ref,
                    }
                ]
                _Log.warn(
                    f"[FN-GUARANTEE] {_fid or mfu_id}: non-infra feature had functions=[] "
                    f"(no S.5 events/batch steps) — synthesised 1 primary function anchored to "
                    f"'{_primary_ref}' so Gate 6a/6a-ZERO are satisfiable (P3 fix)."
                )

        # ── Fix5(item2): polish function labels into business sub-feature titles ──
        # The deterministic step above fixes the COUNT and l2_source_ref (the ceiling
        # and anchors) but leaves labels as verbatim event-handler names, which read as
        # an implementation dump rather than end-user sub-features. This grounded LLM
        # pass rewrites ONLY the label wording and adds a 2-3 sentence business
        # description (ids/anchors/count untouched), keeping the verbatim token in
        # parentheses for traceability. Always on (business descriptions are always
        # wanted); fully non-blocking — any failure keeps the deterministic labels.
        _feats = feature_manifest.get("features", [])
        for _idx in range(len(_feats)):
            if _feats[_idx].get("is_infrastructure"):
                continue
            if _feats[_idx].get("functions"):
                _feats[_idx] = self._polish_function_labels(_feats[_idx], _focal_fns, mfu_id)

        # ── #42 hardening: drop story-bearing keys before validation ─────────
        # When the edit-mode guidance (which renders the current stories) is prepended to the
        # Stage 5a prompt, the manifest LLM may echo a 'stories'/'user_stories' array into each
        # feature. The manifest schema is CLOSED (additionalProperties=False) and has no such
        # key, so it would fail validation. Stories never belong in the manifest (Stage 5b
        # generates them independently), so stripping them is safe and deterministic. This is a
        # belt-and-braces guard; Fix 2 (per-stage guidance) also stops the echo at the source.
        _stripped = 0
        for _f in feature_manifest.get("features") or []:
            for _k in ("stories", "user_stories"):
                if _k in _f:
                    _f.pop(_k, None)
                    _stripped += 1
        if _stripped:
            _Log.ok(
                f"[MANIFEST-CLEAN] {mfu_id}: dropped {_stripped} story-bearing key(s) "
                f"not allowed by the closed manifest schema."
            )

        # ── Schema validation: feature_manifest.json ─────────────────────────
        # Both Stage 5 schemas use only static hardcoded enums — registry=None is correct.
        # Non-blocking: log on failure, write anyway so downstream has something to work with.
        try:
            JSONSchemaValidator(FEATURE_MANIFEST_SCHEMA_PATH, registry=None).validate_data(
                feature_manifest
            )
            _Log.ok(f"[SCHEMA] feature_manifest.json valid ({mfu_id})")
        except SchemaValidationError as _sv_err:
            _Log.warn(f"[SCHEMA] feature_manifest.json INVALID ({mfu_id}): {_sv_err}")
        except Exception as _sv_err:
            _Log.warn(f"[SCHEMA] feature_manifest.json validation error ({mfu_id}): {_sv_err}")

        self._write_manifest(feature_manifest, mfu_dir)

        feature_count = len(feature_manifest.get("features", []))
        infra_count = sum(
            1 for f in feature_manifest.get("features", []) if f.get("is_infrastructure")
        )
        _Log.ok(
            f"Stage 5a complete — {feature_count} features "
            f"({infra_count} infrastructure, {feature_count - infra_count} user-facing)."
        )

        # ── STAGE 5b: Story Derivation ────────────────────────────────────
        _Log.reason("Stage 5b: Deriving User Stories per feature...")
        final_output = self._run_stage5b(
            mfu_dir,
            feature_manifest,
            srs_content,
            mfu_id,
            module_id,
            module_name,
            technology,
            language_hints=language_hints,
            prior_output=prior_output,
            edit_targets=edit_targets,
        )

        # ── ACT: Inject resolved module identity fields (always present) ────
        # module_name              → business-quality name from Stage 2.7 if available,
        #                           else raw folder-derived name (never empty).
        # module_business_description → business purpose text; empty string when
        #                           Stage 2.7 has not run yet for this module.
        # Both fields are injected here so every output path (happy, empty
        # manifest, skeleton) carries them — consumers need only this file.
        # NOTE: schema uses module_business_description (not module_description).
        _biz_name = module_context.get("module_name") or module_name
        _biz_desc = module_context.get("description", "")
        final_output["module_name"] = _biz_name
        final_output["module_business_description"] = _biz_desc

        # ── Option 1 (MVP): reconstruct emergency-scaffold features from stories ──
        # Deterministic, NO LLM. Runs ONLY for features on the Stage-5a emergency
        # scaffold that have successful Stage-5b stories — rebuilds their display
        # fields (description, business_context, functions, success_criteria) from
        # the story content so the deliverable is readable instead of a bare
        # "requires manual review" placeholder. Grounding preserved (anchors are
        # harvested from stories, never fabricated); provenance recorded in
        # generation_metadata. Must run BEFORE hydration/schema so the rebuilt
        # shape is hydrated and validated. Normal-path features are untouched.
        try:
            _recon_n = _reconstruct_scaffold_features_from_stories(
                final_output, llm=getattr(self, "_stage5_llm", None)
            )
            if _recon_n:
                _Log.ok(
                    f"[RECONSTRUCT] {mfu_id}: rebuilt {_recon_n} scaffold feature(s) "
                    f"from generated stories (deterministic, provenance recorded)."
                )
        except Exception as _rc_err:
            _Log.warn(
                f"[RECONSTRUCT] {mfu_id}: scaffold reconstruction skipped ({_rc_err}); non-fatal."
            )

        # ── SRS Evidence hydration ────────────────────────────────────────────
        # Populate srs_evidence[] on every feature and user story by resolving
        # each l2_sources entry to its physical SRS file location + anchor via
        # the SRSPhysicalLinker index. Must run before schema validation and
        # before the final write so the evidence is present in the output file.
        _hydrate_all_srs_evidence(final_output.get("features", []), mfu_dir, mfu_id)

        # ── #43 — screen traceability (relate features & stories to legacy screen file(s)) ──
        # Deterministic: each UIBlueprint SRS = a screen (enriched from PRE-FLIGHT inventory);
        # per-story screens derived from its hydrated srs_evidence file_names. Runs AFTER
        # hydration (needs file_names) and BEFORE schema validation (screens[] is declared).
        try:
            _screen_registry = _build_screen_registry(mfu_dir, srs_files, source_path)
            if _screen_registry:
                for _feat in final_output.get("features", []):
                    _feat["screens"] = _screen_registry
                    for _st in _feat.get("user_stories", []):
                        _st["screens"] = _screens_for_story(_st, _screen_registry)
                _Log.observe(
                    f"[SCREENS] {mfu_id}: {len(_screen_registry)} screen(s) linked "
                    f"({', '.join(s['artifact_id'] for s in _screen_registry)})."
                )
        except Exception as _scr_err:
            _Log.warn(f"[SCREENS] {mfu_id}: screen traceability skipped ({_scr_err}); non-fatal.")

        # ── Layer-1 guarantee: condensation_note on every infrastructure feature ──
        # Deterministic (NO LLM): any feature with is_infrastructure=true MUST carry a
        # human-readable note explaining why it has no user stories, so the UI can show
        # the reason. Fills ONLY when missing/blank (never overwrites the model's note).
        # Runs here — on the final output, before validation/write — so it covers EVERY
        # path: normal generation, P10-guard revert, and deterministic/emergency fallback
        # (the path where the note was previously being dropped).
        self._ensure_condensation_notes(final_output.get("features", []), source_path)

        # ── Schema validation: features_stories.json ─────────────────────────
        # Both Stage 5 schemas use only static hardcoded enums — registry=None is correct.
        # Non-blocking: log on failure, write anyway so downstream has something to work with.
        try:
            JSONSchemaValidator(FEATURES_STORIES_SCHEMA_PATH, registry=None).validate_data(
                final_output
            )
            _Log.ok(f"[SCHEMA] features_stories.json valid ({mfu_id})")
        except SchemaValidationError as _sv_err:
            # Output-integrity guard (#19): a schema-invalid file must NEVER be
            # silently presented as a valid deliverable. We still write the file
            # (downstream relies on it always existing — see _write_output), but we
            # (a) log loudly at ERROR level, (b) stamp final_status so the file
            # self-identifies as invalid, and (c) drop a quarantine copy for triage.
            _Log.err(f"[SCHEMA] features_stories.json INVALID ({mfu_id}): {_sv_err}")
            _gm = final_output.setdefault("generation_metadata", {})
            _gm["final_status"] = "FAIL_SCHEMA_INVALID"
            _gm["schema_error"] = str(_sv_err)[:500]
            try:
                self._write_json_safe(Path(mfu_dir) / "features_stories.INVALID.json", final_output)
                _Log.warn(
                    f"[SCHEMA] Quarantine copy written: features_stories.INVALID.json ({mfu_id})"
                )
            except Exception:
                pass
        except Exception as _sv_err:
            _Log.warn(f"[SCHEMA] features_stories.json validation error ({mfu_id}): {_sv_err}")

        # ── Cancellation check: the Stage 5a/5b LLM calls above are
        # synchronous/non-streaming and can't be aborted mid-flight — if the
        # pipeline was cancelled while they were in flight, discard this
        # MFU's now-stale output instead of persisting it.
        if request_id:
            from app.core import task_control  # noqa: PLC0415

            if task_control.is_request_cancelled(request_id):
                _Log.warn(
                    f"[CANCELLED] {mfu_id}: request cancelled — discarding generated output, not writing."
                )
                return None

        # ── ACT: Write final output (always) ─────────────────────────────
        self._write_output(final_output, mfu_dir)
        total_stories = sum(
            len(f.get("user_stories", [])) for f in final_output.get("features", [])
        )
        _Log.ok(
            f"Stage 5 complete for {mfu_id} — "
            f"{feature_count} features, {total_stories} stories. "
            f"Status: {final_output.get('generation_metadata', {}).get('final_status', 'UNKNOWN')}"
        )
        return final_output

    def run_for_module(self, module_dir: Path, request_id: str | None = None) -> list[dict]:
        """
        Runs the two-stage pipeline for all MFUs within a module's stage4_specs directory.

        *request_id* is the cooperative-cancellation request id (see
        app/core/task_control.py) — checked before each MFU so a cancel
        requested mid-module stops dispatching further MFUs.
        """
        module_dir = Path(module_dir).resolve()
        _nm = module_dir.name.upper()

        # ── Shared/infrastructure skip — enforced HERE (the per-module entry point) ──
        # Both cmd_run's Phase-3 per-module loop (run_stage5_for_module -> run_for_module)
        # and run_for_project funnel through this method, so honoring the skip here makes
        # it caller-independent. (The run_for_project loop also pre-skips, which is fine.)
        # Skip 1 — legacy name-based list (folder/BFS MOD-SHARED, explicit overrides).
        if self._skip_module_ids and self._module_matches_skip(
            module_dir.name, self._skip_module_ids
        ):
            _Log.ok(
                f"[SKIP] {module_dir.name}: in stage5.skip_module_ids — no feature/story "
                f"derivation (shared module, API Contracts retained)."
            )
            return []
        # Skip 2 — name-independent is_shared flag (AI-clustered shared/infra module).
        if _nm in self._shared_module_ids:
            _info = self._shared_module_info.get(_nm, {})
            _arts = _info.get("artifacts", [])
            _sample = ", ".join(_arts[:8]) + (" …" if len(_arts) > 8 else "")
            _Log.ok(
                f"[SKIP] {module_dir.name}: flagged is_shared=true ({_info.get('track', '?')}) — "
                f"no feature/story generation. Contains {len(_arts)} artifact(s) [{_sample}]. "
                f"If any are genuine business screens (e.g. a login/authentication feature), "
                f"unset is_shared in module_manifest.json (or re-cluster) and re-run so they "
                f"get stories."
            )
            return []

        specs_dir = module_dir / "stage4_specs"
        if not specs_dir.exists():
            _Log.warn(f"No stage4_specs directory found in {module_dir}. Skipping module.")
            return []

        results = []
        mfu_dirs = sorted([d for d in specs_dir.iterdir() if d.is_dir()])
        _Log.observe(f"Module {module_dir.name}: Found {len(mfu_dirs)} MFU spec director(y/ies).")

        # Per-module feature-sequence registry (MVP Fix 1): guarantees unique feature
        # ids across the MFUs of this module. Reset per module; consulted in _run_stage5a.
        self._module_feature_seqs = {}

        for mfu_dir in mfu_dirs:
            if request_id:
                from app.core import task_control  # noqa: PLC0415

                if task_control.is_request_cancelled(request_id):
                    _Log.warn(
                        f"[CANCELLED] Module {module_dir.name}: request cancelled — "
                        f"stopping after {len(results)}/{len(mfu_dirs)} MFUs."
                    )
                    break
            result = self.run_for_mfu(mfu_dir, request_id=request_id)
            if result:
                results.append(result)

        _Log.ok(f"Module {module_dir.name}: {len(results)}/{len(mfu_dirs)} MFUs processed.")
        self._summarize_stage5(results, f"module {module_dir.name}")
        return results

    def run_for_project(self, modules_root: Path) -> dict[str, list[dict]]:
        """
        Runs the full Stage 5 pipeline across all modules under modules_root.
        """
        modules_root = Path(modules_root)
        if not modules_root.exists():
            _Log.err(f"Modules root not found: {modules_root}")
            return {}

        all_results: dict[str, list[dict]] = {}
        module_dirs = sorted([d for d in modules_root.iterdir() if d.is_dir()])
        _Log.observe(f"Project scan: Found {len(module_dirs)} module(s) under {modules_root}.")

        for mod_dir in module_dirs:
            _nm = mod_dir.name.upper()
            # Skip 1 — legacy name-based list (folder/BFS MOD-SHARED, etc.).
            if self._skip_module_ids and self._module_matches_skip(
                mod_dir.name, self._skip_module_ids
            ):
                _Log.ok(
                    f"[SKIP] {mod_dir.name}: in stage5.skip_module_ids — "
                    f"no feature/story derivation (shared module, API Contracts retained)."
                )
                continue
            # Skip 2 — name-independent is_shared flag (AI-clustered shared/infra module).
            # Log LOUDLY and list the artifacts being skipped: a real shared module often
            # bundles shared UI (login/splash/common dialogs), so surface exactly what is
            # dropped so a human can intervene if any is a genuine business screen.
            if _nm in self._shared_module_ids:
                _info = self._shared_module_info.get(_nm, {})
                _arts = _info.get("artifacts", [])
                _sample = ", ".join(_arts[:8]) + (" …" if len(_arts) > 8 else "")
                _Log.ok(
                    f"[SKIP] {mod_dir.name}: flagged is_shared=true ({_info.get('track', '?')}) — "
                    f"no feature/story generation. Contains {len(_arts)} artifact(s) [{_sample}]. "
                    f"If any are genuine business screens (e.g. a login/authentication feature), "
                    f"unset is_shared in module_manifest.json (or re-cluster) and re-run so they "
                    f"get stories."
                )
                continue
            results = self.run_for_module(mod_dir)
            if results:
                all_results[mod_dir.name] = results

        if self.trigger_neo4j:
            self._trigger_neo4j_export()

        _Log.ok(
            f"Stage 5 complete. "
            f"{sum(len(v) for v in all_results.values())} MFU outputs written across "
            f"{len(all_results)} module(s)."
        )
        self._summarize_stage5([r for v in all_results.values() for r in v], "project")
        return all_results

    @staticmethod
    def _summarize_stage5(results: list, scope_label: str) -> None:
        """
        #23 — End-of-run Stage 5 summary. PURE reporting: reads the results already
        produced and prints a PASS/FAIL roll-up plus one line per non-PASS MFU.
        Fully wrapped so a summary glitch can never affect the pipeline.
        """
        try:
            counts: dict = {}
            fails: list = []
            for r in results or []:
                gm = (r or {}).get("generation_metadata", {}) or {}
                status = gm.get("final_status", "UNKNOWN")
                counts[status] = counts.get(status, 0) + 1
                if not str(status).startswith("PASS"):
                    detail = gm.get("schema_error") or ""
                    fails.append(
                        (
                            f"{(r or {}).get('module_id', '?')}/{(r or {}).get('mfu_id', '?')}",
                            status,
                            str(detail)[:100],
                        )
                    )
            total = sum(counts.values())
            roll = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "none"
            _Log.ok(f"[STAGE5 SUMMARY] {scope_label}: {total} MFU(s) — {roll}")
            for name, status, detail in fails:
                _Log.warn(
                    f"[STAGE5 SUMMARY]   {status}  {name}" + (f"  — {detail}" if detail else "")
                )
        except Exception as _e:
            _Log.warn(f"[STAGE5 SUMMARY] render skipped ({_e}).")

    # -----------------------------------------------------------------------
    # Stage 5a: Feature Manifest
    # -----------------------------------------------------------------------

    def _run_stage5a_deterministic(
        self,
        mfu_dir: Path,
        srs_files: list,
        mfu_id: str,
        module_id: str,
        module_name: str,
        source_path: str,
        technology_tag: str,
        module_context: dict | None = None,
    ) -> dict:
        """
        Semi-deterministic Stage 5a for large MFUs (files > focal_only_threshold).

        Architecture:
          Step 1 — Python extraction (deterministic, zero hallucination):
            SRSPhysicalLinker indexes the MFU directory and extracts:
              • functions[]           from S.5 EVENT-NNN table rows
              • bounding_box.controls from S.3 verbatim physical names
              • feature_id            {MODULE_PREFIX}-{MFU_SEQ}-F1
              • is_infrastructure     False if any S.5 events exist
            These fields NEVER hallucinate because they come from the linker index,
            not from an LLM response.

          Step 2 — Minimal LLM enrichment (~3K chars input, not 114K):
            Sends only the UIBlueprint executive summary (TIER 1, ~2-3K chars)
            to the LLM to obtain: title, description, business_context.
            If the LLM call fails, sensible defaults are applied.

        Benefits:
          • Eliminates LLM fatigue (3K chars vs 114K for full approach)
          • Gate 7c cannot fail (controls[] is Python-extracted from S.3 verbatim)
          • Feature ID is always correct (deterministic construction)
          • Stage 5b still uses full SRS content for rich story generation
        """
        mfu_seq = _extract_mfu_seq(mfu_id)
        _Log.act(f"Stage 5a deterministic [{mfu_id}]: indexing SRS via SRSPhysicalLinker...")

        try:
            from .srs_linker import SRSPhysicalLinker  # type: ignore

            linker = SRSPhysicalLinker(mfu_dir)
        except Exception as e:
            _Log.err(
                f"Stage 5a deterministic [{mfu_id}]: linker failed ({e}). Falling back to LLM path."
            )
            focal_srs = _build_focal_only_srs_content(srs_files)
            return self._run_stage5a(
                mfu_dir,
                focal_srs,
                mfu_id,
                module_id,
                module_name,
                source_path,
                technology_tag,
                module_context=module_context,
                focal_only_srs=focal_srs,
            )

        # Step 1: Python extraction
        scaffold = _build_manifest_scaffold(
            linker,
            mfu_id,
            module_id,
            module_name,
            source_path,
            technology_tag,
            mfu_seq,
            srs_files,
        )
        _Log.ok(
            f"Stage 5a deterministic [{mfu_id}]: scaffold — "
            f"{len(scaffold['functions'])} functions, "
            f"{len(scaffold['bounding_box']['controls'])} S.3 controls (verbatim)."
        )

        # Step 2: Minimal LLM enrichment for title / description / business_context
        exec_summary = _extract_uiblueprint_executive_summary(srs_files, max_chars=3000)
        ctx_name = (module_context or {}).get("module_name", module_name)
        ctx_desc = (module_context or {}).get("description", "")

        enrich_user = (
            f"Module: {ctx_name}\n"
            f"Business Purpose: {ctx_desc}\n"
            f"Technology: {technology_tag}\n\n"
            f"Screen executive summary:\n{exec_summary}\n\n"
            f"Output ONLY a JSON object with these four fields:\n"
            f'{{"title":"...","description":"...","business_context":{{"current_state":"...","target_state":"..."}},"source":"..."}}'
        )
        enrich_sys = (
            "You are an Enterprise Business Analyst. "
            "Read the screen description and output a JSON object with title, description, "
            "business_context (current_state and target_state), and source. "
            "Use domain business vocabulary. Do NOT mention artifact filenames in the title. "
            "Output ONLY the JSON object."
        )

        try:
            enrich_resp = self._stage5_llm.complete(
                system_prompt=enrich_sys,
                user_prompt=enrich_user,
            )
            # Try <OUTPUT> tags first, then bare JSON
            enriched = _extract_output_json(enrich_resp)
            if enriched is None:
                try:
                    enriched = json.loads(enrich_resp.strip())
                except Exception:
                    enriched = None

            if enriched and isinstance(enriched, dict):
                scaffold["title"] = enriched.get("title", scaffold["title"]) or scaffold["title"]
                scaffold["description"] = enriched.get("description", "") or ""
                scaffold["source"] = (
                    enriched.get("source", scaffold["source"]) or scaffold["source"]
                )
                bc = enriched.get("business_context", {}) or {}
                scaffold["business_context"]["current_state"] = bc.get("current_state", "") or ""
                scaffold["business_context"]["target_state"] = bc.get("target_state", "") or ""
                _Log.ok(f"Stage 5a deterministic [{mfu_id}]: LLM enrichment succeeded.")
            else:
                _Log.warn(
                    f"Stage 5a deterministic [{mfu_id}]: enrichment parse failed — using defaults."
                )
        except Exception as e:
            _Log.warn(
                f"Stage 5a deterministic [{mfu_id}]: enrichment call failed ({e}) — using defaults."
            )

        # Ensure required non-empty fields
        if not scaffold["title"] or scaffold["title"] == module_name:
            scaffold["title"] = f"{ctx_name} Management"
        if not scaffold["description"]:
            scaffold["description"] = (
                f"Enables authorised users to manage {ctx_name.lower()} data "
                f"via a {technology_tag} screen with {len(scaffold['functions'])} key operations."
            )
        if not scaffold["business_context"]["current_state"]:
            scaffold["business_context"]["current_state"] = (
                f"Legacy {technology_tag} screen: {module_name}."
            )
        if not scaffold["business_context"]["target_state"]:
            scaffold["business_context"]["target_state"] = (
                "Modern React/TypeScript frontend backed by Java Spring Boot REST microservices."
            )

        manifest = {
            "schema_version": "5a.2",
            "mfu_id": mfu_id,
            "mfu_seq": mfu_seq,
            "module_id": module_id,
            "module_name": module_name,
            "source_path": source_path,
            "technology_tag": technology_tag,
            "l1_source": _build_l1_source(mfu_id, source_path, module_id, mfu_seq),
            "features": [scaffold],
            "generation_metadata": {
                "generator_model": (
                    f"{self._stage5_llm.effective_model} "
                    f"(deterministic-extraction + minimal-enrichment)"
                ),
                "iteration_count": 1,
                "final_status": "PASS",
                "generated_at": datetime.now(UTC).isoformat(),
            },
        }

        if module_context:
            if module_context.get("module_name"):
                manifest["module_business_name"] = module_context["module_name"]
            if module_context.get("description"):
                manifest["module_business_description"] = module_context["description"]

        manifest.setdefault("mfu_seq", mfu_seq)
        _Log.ok(
            f"Stage 5a deterministic [{mfu_id}]: manifest complete — "
            f"feature={scaffold['id']}, infra={scaffold['is_infrastructure']}."
        )
        return manifest

    def _run_stage5a(
        self,
        mfu_dir: Path,
        srs_content: str,
        mfu_id: str,
        module_id: str,
        module_name: str,
        source_path: str,
        technology_tag: str,
        module_context: dict | None = None,
        focal_anchor: str = "",
        focal_anchor_functions: str = "",
        language_hints: str = "",
    ) -> dict:
        """
        Calls the Stage 5a LLM generator to derive the Feature Manifest.
        Returns a parsed feature_manifest dict, or an empty manifest on failure.

        module_context (Layer 2): when provided, injects [MODULE CONTEXT] block
        into the prompt and writes module_business_name / module_business_description
        into the parsed manifest for downstream consumers.

        focal_anchor_functions (Layer 4): pre-built {FOCAL_ANCHOR_FUNCTIONS} block
        containing verbatim S.5 event labels (UIBlueprint) or BATCH::STEP labels
        (BatchBlueprint/COBOL).  Eliminates hallucinated CRUD function labels.
        Built by _build_focal_anchor_functions_block() in run_for_mfu().

        language_hints (Layer 5): pre-rendered {LANGUAGE_HINTS} block from
        Stage5ProfileRegistry. Contains language-specific vocabulary constraints
        and Gherkin guidance for the feature manifest generator. "" when registry
        not loaded or technology_tag has no profile.
        """

        _Log.act(f"Stage 5a: Calling Feature Manifest generator for {mfu_id}...")

        prompt = _build_stage5a_prompt(
            template=self._5a_template,
            srs_content=srs_content,
            mfu_id=mfu_id,
            module_id=module_id,
            module_name=module_name,
            source_path=source_path,
            technology_tag=technology_tag,
            mfu_seq=_extract_mfu_seq(mfu_id),
            module_context=module_context,
            focal_anchor=focal_anchor,
            focal_anchor_functions=focal_anchor_functions,
            language_hints=language_hints,
        )
        prompt = _prepend_human_guidance(
            prompt, getattr(self, "_manifest_guidance", None)
        )  # #42 (Stage 5a manifest guidance)

        # ── Stage 5a: simplified 2-call approach ──────────────────────────────────
        #
        # Call 1: primary content (UIBlueprint-only for > threshold, all specs for ≤ threshold)
        #   Validate: len >= 1000 AND <OUTPUT> tag AND features[] not empty AND not placeholder
        #   → if pass: done ✓
        #   → if any failure mode: fall back to Call 2
        #
        # Call 2: UIBlueprint-only + reinforced prompt (always the fallback)
        #   Works for both > threshold (retry with same content, different prompt)
        #   and ≤ threshold (retry with reduced content + reinforced prompt)
        #   → if pass: done ✓
        #   → if still fail: write empty manifest

        _MIN_CHARS = 1000
        _OUTPUT_MARKER = "<OUTPUT>"

        # Focal anchor clause injected into both system prompts.
        # Guides LLM attention to the primary entry point without truncating SRS.
        # Full SRS sent to both Stage 5a and 5b => KV cache reuse between stages.
        _anchor = (
            (
                f"FOCAL ANCHOR: '{focal_anchor}' is the primary business entry point. "
                f"Derive the Feature Manifest anchored exclusively on the [FOCAL ENTRYPOINT] "
                f"document. All other SRS documents are supporting context only. "
            )
            if focal_anchor
            else ""
        )

        _SYS_STANDARD = (
            "You are an expert Enterprise Business Analyst specialising in legacy system migration. "
            + _anchor
            + "Derive a Feature Manifest (features only, NO User Stories) from the provided SRS. "
            "Follow all directives strictly. Output a single valid JSON object inside <OUTPUT> tags."
        )
        _SYS_REINFORCED = (
            "You are an expert Enterprise Business Analyst specialising in legacy system migration. "
            + _anchor
            + "READ the [FOCAL ENTRYPOINT] document — it contains S.3 controls, S.5 events. "
            "Output a single valid JSON object inside <OUTPUT>...</OUTPUT> tags. "
            "RULES: (1) Do NOT output placeholders — the SRS IS provided. "
            "(2) is_infrastructure=false if S.5 has user-visible events. "
            "(3) controls[] must use verbatim physical names from S.3."
        )

        _PLACEHOLDER_SIGNALS = (
            "no srs content",
            "no source document",
            "placeholder feature",
            "placeholder—no srs",
            "no data",
            "not provided",
            "real run",
            "please provide the stage",
            # LLM evasion suffix patterns (model-agnostic catch-all for deflection titles):
            # e.g. "Batch Run Operations Bridge", "System Infrastructure Bridge",
            # "Data Processing Connector", "Module Integration Stub"
            " bridge",
            " connector",
            " stub",
            " shell",
            " integration bridge",
        )

        def _validate_5a(resp: str) -> tuple[bool, str]:
            """5-condition validation: length + tag + features + not-placeholder + not-structurally-empty.

            Dual-layer Mode4 defence (language-agnostic):
              Layer A (string)     — keyword signals in title/description/condensation_note.
                                     Catches known LLM evasion patterns immediately.
              Layer B (structural) — zero l2_sources AND zero functions AND short description.
                                     Catches novel evasion patterns regardless of title wording.
            """
            if not resp or len(resp) < _MIN_CHARS:
                return False, f"Mode1-refusal (len={len(resp or '')} < {_MIN_CHARS})"
            if _OUTPUT_MARKER not in resp:
                return False, f"Mode1-no-output-tag (len={len(resp)})"
            p = _extract_output_json(resp)
            if p is None:
                return False, "Mode2-malformed-json"
            if not p.get("features"):
                return False, "Mode3-empty-features"
            feat = p["features"][0]
            combined = (
                feat.get("description", "")
                + " "
                + feat.get("condensation_note", "")
                + " "
                + feat.get("title", "")
            ).lower()
            # Layer A: string-signal check
            if any(sig in combined for sig in _PLACEHOLDER_SIGNALS):
                return False, f"Mode4-placeholder (title={feat.get('title', '?')!r})"
            # Layer B: structural emptiness — a genuine feature for any non-infra MFU
            # must have l2_sources (traceability) OR functions (operations).
            # A feature with neither and a very short description is a deflection
            # output regardless of title.
            if (
                not feat.get("l2_sources")
                and not feat.get("functions")
                and len(feat.get("description", "")) < 200
            ):
                return False, (
                    f"Mode4-structural-empty (title={feat.get('title', '?')!r}, "
                    f"l2_sources=[], functions=[], "
                    f"desc_len={len(feat.get('description', ''))})"
                )
            return True, "ok"

        # ── Call 1: primary ────────────────────────────────────────────────────
        _Log.act(f"Stage 5a [call 1/2]: calling LLM for {mfu_id}...")
        response = None
        try:
            response = self._stage5_llm.complete(
                system_prompt=_SYS_STANDARD,
                user_prompt=prompt,
            )
        except Exception as e:
            _Log.err(f"Stage 5a call 1 failed for {mfu_id}: {e}")

        ok1, mode1 = _validate_5a(response or "")
        if ok1:
            _Log.ok(f"Stage 5a [call 1/2]: success for {mfu_id} (len={len(response)}).")
            parsed = _extract_output_json(response)
        else:
            # ── Call 2: UIBlueprint-only + reinforced prompt ───────────────────
            _Log.warn(
                f"Stage 5a [call 1/2] failed ({mode1}). "
                f"Retrying with UIBlueprint-only + reinforced prompt..."
            )
            # Call 2 fallback: same full SRS + reinforced system prompt
            # (never truncate — cache reuse with Stage 5b is critical)
            fallback_prompt = _build_stage5a_prompt(
                template=self._5a_template,
                srs_content=srs_content,
                mfu_id=mfu_id,
                module_id=module_id,
                module_name=module_name,
                source_path=source_path,
                technology_tag=technology_tag,
                mfu_seq=_extract_mfu_seq(mfu_id),
                module_context=module_context,
                focal_anchor=focal_anchor,
                focal_anchor_functions=focal_anchor_functions,
            )
            fallback_prompt = _prepend_human_guidance(
                fallback_prompt, getattr(self, "_manifest_guidance", None)
            )  # #42 (Stage 5a fallback: manifest guidance)
            response2 = None
            try:
                response2 = self._stage5_llm.complete(
                    system_prompt=_SYS_REINFORCED,
                    user_prompt=fallback_prompt,
                )
            except Exception as e:
                _Log.err(f"Stage 5a call 2 failed for {mfu_id}: {e}")

            ok2, mode2 = _validate_5a(response2 or "")
            if ok2:
                _Log.ok(
                    f"Stage 5a [call 2/2]: UIBlueprint-only fallback succeeded "
                    f"for {mfu_id} (len={len(response2)})."
                )
                parsed = _extract_output_json(response2)
            else:
                # ── Emergency deterministic fallback ──────────────────────────
                # Both LLM calls failed (Mode4-placeholder or Mode1-refusal on
                # every attempt). Attempt a linker-only scaffold as last resort:
                #   - Zero additional API cost (no further LLM calls)
                #   - Zero hallucination (all fields from SRSPhysicalLinker index)
                #   - is_infrastructure derived from S.5 event presence:
                #       no events → True  (dead code, startup stubs, utility mods)
                #       has events → False (linker provides complete function scaffold)
                # Language-agnostic: works for VB6, COBOL, PowerBuilder, RPG, etc.
                # The PASS_DETERMINISTIC_FALLBACK status lets downstream guards
                # (e.g. the P2 UIBlueprint override) correctly skip LLM-only fixes.
                _Log.warn(
                    f"Stage 5a [call 2/2] also failed ({mode2}). "
                    f"Attempting deterministic linker scaffold as emergency fallback "
                    f"for {mfu_id}."
                )
                try:
                    from .srs_linker import SRSPhysicalLinker  # type: ignore

                    _fb_linker = SRSPhysicalLinker(mfu_dir)
                    _fb_srs_files = _find_srs_files(mfu_dir)
                    _fb_scaffold = _build_manifest_scaffold(
                        _fb_linker,
                        mfu_id,
                        module_id,
                        module_name,
                        source_path,
                        technology_tag,
                        _extract_mfu_seq(mfu_id),
                        _fb_srs_files,
                    )
                    _infra = _fb_scaffold.get("is_infrastructure", True)
                    if _infra:
                        # Infrastructure modules (dead code, startup stubs, etc.):
                        # override bounding_box zone to API_LAYER — the correct zone
                        # for non-screen technical modules in the target architecture.
                        _fb_scaffold["bounding_box"]["zone_id"] = "API_LAYER"
                        _fb_scaffold["bounding_box"]["zone_label_en"] = (
                            "API Layer — Infrastructure Module"
                        )
                        _fb_scaffold["bounding_box"]["zone_label_jp"] = (
                            "API Layer — Infrastructure Module"
                        )
                    # ── Gate 1b/1e defaults: non-empty description + business_context ──
                    # The Critic fails Gate 1b if description is empty and Gate 1e
                    # if business_context strings are empty.  These placeholders are
                    # honest (clearly labelled as emergency fallback) and neutral
                    # w.r.t. infrastructure status — safe even when P2-guard later
                    # flips is_infrastructure True→False on a UIBlueprint focal.
                    if not _fb_scaffold.get("description"):
                        _fb_scaffold["description"] = (
                            f"Emergency fallback scaffold for {mfu_id} "
                            f"({module_name}). LLM analysis was unavailable — "
                            f"description requires manual review."
                        )
                    _bc = _fb_scaffold.setdefault("business_context", {})
                    if not _bc.get("current_state"):
                        _tech_label = (technology_tag or "Legacy").upper()
                        _bc["current_state"] = (
                            f"Legacy {_tech_label} module ({mfu_id}): "
                            f"{module_name}. Emergency fallback — LLM analysis "
                            f"was unavailable. Business context requires manual "
                            f"review."
                        )
                        _bc["target_state"] = (
                            "Modern microservice-based implementation. "
                            "Migration scope and architecture require manual "
                            "assessment."
                        )
                    _Log.ok(
                        f"Stage 5a [fallback]: deterministic scaffold built for "
                        f"{mfu_id} — is_infrastructure={_infra}, "
                        f"functions={len(_fb_scaffold.get('functions', []))}, "
                        f"controls={len(_fb_scaffold.get('bounding_box', {}).get('controls', []))}."
                    )
                    # ── Gate 1a: root envelope fields ─────────────────────────
                    # Critic Gate 1a requires schema_version, mfu_id, module_id,
                    # module_name, source_path, and l1_source at the top level.
                    # Without them the Critic fails immediately and the Corrector
                    # has no way to add structural envelope fields — resulting in
                    # FAIL_CORRECTOR_NO_PROGRESS on every det-fallback MFU.
                    _fb_mfu_seq = _extract_mfu_seq(mfu_id)
                    parsed = {
                        "schema_version": "5a.2",
                        "mfu_id": mfu_id,
                        "mfu_seq": _fb_mfu_seq,
                        "module_id": module_id,
                        "module_name": module_name,
                        "source_path": source_path,
                        "technology_tag": technology_tag,
                        "l1_source": _build_l1_source(mfu_id, source_path, module_id, _fb_mfu_seq),
                        "features": [_fb_scaffold],
                    }
                    # Stamp module business identity if Stage 2.7 context available.
                    if module_context:
                        if module_context.get("module_name"):
                            parsed["module_business_name"] = module_context["module_name"]
                        if module_context.get("description"):
                            parsed["module_business_description"] = module_context["description"]
                    parsed["generation_metadata"] = {
                        "generator_model": "deterministic-fallback",
                        "iteration_count": 2,
                        "final_status": "PASS_DETERMINISTIC_FALLBACK",
                        "generated_at": datetime.now(UTC).isoformat(),
                        "fallback_reason": f"call1={mode1}; call2={mode2}",
                    }
                except Exception as _fb_exc:
                    _Log.err(
                        f"Stage 5a [fallback]: linker scaffold also failed "
                        f"({_fb_exc}). Writing empty manifest for {mfu_id}."
                    )
                    return _build_empty_manifest(
                        {
                            "mfu_id": mfu_id,
                            "module_id": module_id,
                            "module_name": module_name,
                        },
                        source_path,
                        technology_tag,
                    )

        parsed.setdefault("generation_metadata", {})
        # Guard: do not overwrite deterministic-fallback metadata.
        # PASS_DETERMINISTIC_FALLBACK is set by the emergency scaffold path
        # and must survive to (a) keep generator_model="deterministic-fallback"
        # accurate in the output JSON and (b) make _is_det_fallback correctly
        # True so P10-guard skips the authoritative linker-based classification.
        if parsed["generation_metadata"].get("final_status") != "PASS_DETERMINISTIC_FALLBACK":
            parsed["generation_metadata"].update(
                {
                    "generator_model": self._stage5_llm.effective_model,
                    "iteration_count": 1,
                    "final_status": "PASS",
                    "generated_at": datetime.now(UTC).isoformat(),
                }
            )

        # Layer 2: stamp refined module business identity into the manifest.
        # These fields surface the Stage 2.7 DDD-quality names in every downstream
        # consumer (Neo4j, Jira, reporting) without re-querying the manifest file.
        if module_context:
            if module_context.get("module_name"):
                parsed.setdefault("module_business_name", module_context["module_name"])
            if module_context.get("description"):
                parsed.setdefault("module_business_description", module_context["description"])

        # ── P2 guard: UIBlueprint focal → never infrastructure ────────────────
        # Architectural contract: a focal SRS file named *_UIBlueprint_* represents
        # a user-facing screen by definition. is_infrastructure=True on such a file
        # is always an LLM classification error and must be overridden deterministically.
        # Language-agnostic: the *_UIBlueprint_* filename convention is consistent
        # across VB6, PowerBuilder, and any future UI-based language.
        _focal_fname = ""
        _focal_list = []
        try:
            _focal_list = _find_srs_files(mfu_dir)
            _focal_fname = _focal_list[0].name if _focal_list else ""
        except Exception:
            pass
        # P2 applies unconditionally — including det-fallback manifests.
        # Rationale: a focal file named *_UIBlueprint_* is always a user-facing
        # screen by definition. The det-fallback scaffold may classify a UIBlueprint
        # MFU as is_infrastructure=True (when S.5 events are absent from the linker
        # index) — this is still an incorrect classification that P2 must correct.
        # Not skipping P2 for det-fallback is the intended behaviour: the UIBlueprint
        # filename is more authoritative than an empty S.5 event count.
        _is_det_fallback = (
            parsed.get("generation_metadata", {}).get("final_status", "")
            == "PASS_DETERMINISTIC_FALLBACK"
        )
        if _detect_srs_document_type(_focal_fname) in self._ui_bearing_srs_types:
            for _feat in parsed.get("features", []):
                if _feat.get("is_infrastructure") and _feat.get("functions"):
                    # Only override when user-interaction functions are present.
                    # ABEND / error display screens legitimately have functions=[]
                    # and should retain their infrastructure classification.
                    _feat["is_infrastructure"] = False
                    _feat["category"] = "Functional"
                    _Log.warn(
                        f"[P2-guard] {_feat.get('id', '?')}: overriding is_infrastructure=True→False "
                        f"— focal file '{_focal_fname}' is a UI-bearing SRS "
                        f"({_detect_srs_document_type(_focal_fname)})."
                    )
                elif _feat.get("is_infrastructure") and not _feat.get("functions"):
                    _Log.observe(
                        f"[P2-guard] {_feat.get('id', '?')}: skipping override "
                        f"— functions=[] indicates non-interactive display screen "
                        f"(infrastructure classification preserved)."
                    )

        # ── P10 guard: no functional SRS in MFU → always infrastructure ──────────
        # BUG-2 fix: P10-guard now checks TWO configurable sets:
        #   1. ui_bearing_srs_types  — MFU has a user-facing screen
        #                              (UIBlueprint, BatchBlueprint, JSPBlueprint, …)
        #   2. functional_api_types  — MFU has headless business logic
        #                              (APIContracts, COBOLService, RPGServiceProgram, …)
        # If ANY SRS file in the MFU resolves to either set, the MFU is functional
        # and P10-guard must NOT set is_infrastructure=True.
        # Only MFUs with NO match in either set (dead code, startup stubs, utility
        # bridges, pure dispatch modules) are classified as infrastructure.
        #
        # Architectural distinction preserved:
        #   ui_bearing_srs_types  → stories reference UI controls + API endpoints
        #   functional_api_types  → stories reference API endpoints only (no screen)
        # This distinction is visible in the output JSON (l2_sources will contain
        # S3 refs for UI-bearing, API refs only for headless-functional).
        #
        # Prior behaviour (ui_bearing_srs_types only) is preserved when
        # functional_api_types is empty (the default) — fully backward-compatible.
        # Configurable via project_config.json → stage5.functional_api_types.
        # Skipped for deterministic-fallback manifests: they derive is_infrastructure
        # from S.5 event presence (linker-based, authoritative).
        _has_any_ui_bearing = any(
            _detect_srs_document_type(f.name) in self._ui_bearing_srs_types for f in _focal_list
        )
        _has_any_functional_api = bool(self._functional_api_types) and any(
            _detect_srs_document_type(f.name) in self._functional_api_types for f in _focal_list
        )
        _is_functional = _has_any_ui_bearing or _has_any_functional_api
        if not _is_functional and not _is_det_fallback:
            for _feat in parsed.get("features", []):
                if not _feat.get("is_infrastructure", True):
                    _feat["is_infrastructure"] = True
                    _feat["category"] = "Infrastructure"
                    _Log.warn(
                        f"[P10-guard] {_feat.get('id', '?')}: overriding "
                        f"is_infrastructure=False→True — no UI-bearing or functional-API "
                        f"SRS in MFU (ui_bearing={sorted(self._ui_bearing_srs_types)}, "
                        f"functional_api={sorted(self._functional_api_types)}; "
                        f"focal='{_focal_fname}')."
                    )
                # Infrastructure lock: always clear functions[], controls[], s3_row_refs[]
                # so the critic never sees a non-empty functions[] on an infra feature.
                if _feat.get("is_infrastructure"):
                    if _feat.get("functions"):
                        _Log.warn(
                            f"[P10-guard] {_feat.get('id', '?')}: clearing "
                            f"{len(_feat['functions'])} functions[] entry/entries "
                            f"(infrastructure features must have functions=[])."
                        )
                        _feat["functions"] = []
                    _bb = _feat.get("bounding_box", {})
                    if _bb.get("controls"):
                        _bb["controls"] = []
                        _bb["s3_row_refs"] = []
        elif _has_any_functional_api and not _has_any_ui_bearing:
            _Log.ok(
                f"[P10-guard] {mfu_id}: headless-functional MFU detected "
                f"— functional_api_types match, is_infrastructure not overridden."
            )

        # ── Option C: manifest normalizer — RC-3 fix (det-fallback APIContracts) ──
        # _normalize_manifest checks if this is a det-fallback manifest AND if any
        # focal SRS file resolves to a functional_api_types type. If so, it overrides
        # is_infrastructure=True → False for all features.
        # This fills the gap where P10-guard explicitly skips det-fallback manifests
        # (comment: "Skipped for deterministic-fallback manifests: they derive
        # is_infrastructure from S.5 event presence (linker-based, authoritative)")
        # but APIContracts modules have no S.5 events by design — making the scaffold's
        # is_infrastructure=True classification structurally incorrect for this type.
        parsed, _manifest_normalised = _normalize_manifest(
            manifest=parsed,
            mfu_id=mfu_id,
            focal_srs_files=_focal_list,
            functional_api_types=self._functional_api_types,
        )
        if _manifest_normalised:
            _Log.ok(
                f"[NORM-MANIFEST] {mfu_id}: manifest normalizer corrected "
                f"is_infrastructure for det-fallback functional API module."
            )

        # ── MVP Fix 2: orphan / data-only MFU → infrastructure (deterministic, honest) ──
        # Orphan / dead-code buckets (SYS-DATA-*, SYS-DEAD-*, or orphan/ungrouped/dead-code
        # module names) are unclustered data-contract fragments with no consuming program.
        # Asking the LLM to derive functional stories from them invites hallucination — it
        # fabricates operations from the (mis-fitting) module identity, producing an
        # unwinnable critic/corrector loop that ends in FAIL_HALLUCINATION. Route them to
        # infrastructure with a deterministic, honest data-contract description so NO
        # fabricated stories are produced and the loop cannot occur. Runs AFTER P10-guard
        # and NORM-MANIFEST so it is authoritative for this narrow class.
        if _is_orphan_data_mfu(mfu_id, module_name):
            for _feat in parsed.get("features", []):
                _feat["is_infrastructure"] = True
                _feat["category"] = "Infrastructure"
                _feat["functions"] = []
                _feat["l2_sources"] = []
                _feat["success_criteria"] = []
                _bb = _feat.get("bounding_box") or {}
                _bb["controls"] = []
                _bb["s3_row_refs"] = []
                _feat["description"] = (
                    f"Orphan / unclustered data artifact ({mfu_id}) with no consuming program "
                    f"identified in the analysed source. Documented as a data contract for "
                    f"migration; it exposes no user-facing operations, so no user stories are "
                    f"derived. Human triage required to confirm ownership and scope."
                )
                _bc = _feat.setdefault("business_context", {})
                _bc["current_state"] = (
                    f"Orphan {technology_tag} data structure ({mfu_id}) with no traceable caller "
                    f"in the analysed source (unclustered / potential dead code)."
                )
                _bc["target_state"] = (
                    "Catalogue the data contract and decide during migration whether a consuming "
                    "service should own it; there is no standalone runtime behaviour to migrate."
                )
                _feat["condensation_note"] = (
                    "Classified as infrastructure — orphan/dead-code data artifact with no "
                    "operations. User stories intentionally omitted; human triage required."
                )
            # Provenance goes in status_detail (additionalProperties:true in BOTH
            # schemas); the manifest's generation_metadata is additionalProperties:false,
            # so a top-level key here would fail feature_manifest.json validation.
            _gm_orphan = parsed.setdefault("generation_metadata", {})
            _gm_orphan.setdefault("status_detail", {})["orphan_routed_to_infrastructure"] = True
            _Log.warn(
                f"[ORPHAN-GUARD] {mfu_id}: orphan/data-only MFU routed to infrastructure "
                f"(no stories) — deterministic data-contract description applied to prevent "
                f"hallucinated functional stories."
            )

        # ── MVP Fix 1: authoritative, deterministic, per-module-unique feature ids ──
        # The LLM-success path previously trusted the model's feature id verbatim, which was
        # non-deterministic across runs AND could collide across MFUs (two MFUs both '-901-').
        # Recompute canonically from mfu_id (mirrors the deterministic scaffold path) and
        # enforce per-module uniqueness for the hashed non-standard (9xx) band.
        _module_prefix = module_id[4:] if module_id.startswith("MOD-") else module_id
        _seq = _extract_mfu_seq(mfu_id)
        _seen = getattr(self, "_module_feature_seqs", None)
        if isinstance(_seen, dict) and len(_seq) == 3 and _seq.startswith("9"):
            if _seen.get(_seq, mfu_id) != mfu_id:
                for _cand in range(900, 999):
                    _cs = str(_cand)
                    if _cs not in _seen:
                        _Log.warn(
                            f"[ID-UNIQUE] {mfu_id}: hashed seq {_seq} already used by "
                            f"'{_seen[_seq]}' in this module — remapped to {_cs}."
                        )
                        _seq = _cs
                        break
            _seen[_seq] = mfu_id
        for _i, _feat in enumerate(parsed.get("features", [])):
            _fid = f"{_module_prefix}-{_seq}-F{_i + 1}"
            _feat["id"] = _fid
            for _j, _fn in enumerate(_feat.get("functions") or [], start=1):
                _fn["id"] = f"{_fid}-FN{_j}"
        if isinstance(parsed.get("l1_source"), dict):
            parsed["l1_source"]["screen_id"] = f"{_module_prefix}_SCREEN_{_seq}"
        parsed["mfu_seq"] = _seq

        # Sanitise non-ASCII characters in L2 IDs (Gate 1 compliance).
        # bounding_box.controls[] is preserved as-is for Gate 7 S.3 cross-check.
        parsed = _sanitise_l2_ids(parsed)
        _Log.ok(f"Stage 5a parsed: {len(parsed.get('features', []))} features for {mfu_id}.")
        return parsed

    # -----------------------------------------------------------------------
    # Stage 5b+ Targeted Correction Pass
    # -----------------------------------------------------------------------

    def _run_stage5b_correction(
        self,
        feature: dict,
        all_stories: list,
        failed_story_ids: list,
        suggested_fix: str,
        coverage_gap_l2ids: list,
        srs_content: str,
        mfu_id: str,
        module_id: str,
        technology_tag: str,
        language_hints: str = "",
    ) -> tuple[list, list]:
        """
        Stage 5b+ Targeted Correction Pass.

        Fires only when the initial critic returns FAIL. Sends ONLY the failing
        stories and coverage-gap L2 IDs to the LLM — passing stories are untouched.
        The corrector returns a JSON object with:
          "new_function_entries": entries to append to feature.functions[]
          "stories":              corrected replacements + new stories for gaps

        Returns (merged_stories, corrected_ids):
          merged_stories  — full story list with corrections merged in
          corrected_ids   — IDs of all stories that were added or replaced
        """
        if not self._5b_corrector_template:
            _Log.warn("Stage 5b+: corrector template not loaded — correction pass skipped.")
            return all_stories, []

        feature_id = feature.get("id", "UNKNOWN-F1")
        failing_stories = [s for s in all_stories if s.get("id", "") in set(failed_story_ids)]

        # Compute next available story sequence number for new stories
        seq_numbers = []
        for s in all_stories:
            m = re.search(r"-S(\d+)$", s.get("id", ""))
            if m:
                seq_numbers.append(int(m.group(1)))
        next_seq = max(seq_numbers, default=0) + 1

        prompt = _build_corrector_prompt(
            template=self._5b_corrector_template,
            feature=feature,
            failing_stories=failing_stories,
            coverage_gap_l2ids=coverage_gap_l2ids,
            suggested_fix=suggested_fix,
            srs_content=srs_content,
            mfu_id=mfu_id,
            module_id=module_id,
            technology_tag=technology_tag,
            next_story_seq=next_seq,
            language_hints=language_hints,
        )

        _Log.act(
            f"Stage 5b+: Correction pass for {feature_id} — "
            f"{len(failing_stories)} failing story/stories, "
            f"{len(coverage_gap_l2ids)} coverage gap(s)..."
        )

        try:
            response = self._stage5_llm.complete(
                system_prompt=(
                    "You are an expert Agile Product Owner correcting user stories that failed QA review. "
                    "Apply ONLY the specified fixes. Return ONLY the corrected and new stories. "
                    "Do NOT modify or return passing stories. "
                    "Output a JSON object inside <CORRECTIONS> tags."
                ),
                user_prompt=prompt,
            )
        except Exception as e:
            _Log.err(f"Stage 5b+ correction LLM call failed for {feature_id}: {e}")
            return all_stories, []

        corrections_data = _extract_corrections_json(response)
        if not corrections_data:
            _Log.warn(
                f"Stage 5b+: Could not parse <CORRECTIONS> JSON for {feature_id}. "
                "Keeping original stories."
            )
            return all_stories, []

        corrected_stories: list = corrections_data.get("stories", [])
        new_fn_entries: list = corrections_data.get("new_function_entries", [])
        remove_ids: list = corrections_data.get("remove_story_ids", [])

        # Early-exit only when the corrector made NO changes at all — no replacements,
        # no additions, and no deletions.  A removal-only response (stories=[], but
        # remove_story_ids non-empty) is a valid correction and must proceed.
        if not corrected_stories and not remove_ids:
            _Log.warn(
                f"Stage 5b+: Correction pass returned 0 stories and no removals for {feature_id}."
            )
            return all_stories, []

        if remove_ids:
            _Log.ok(
                f"Stage 5b+: Removing {len(remove_ids)} story/stories flagged by critic "
                f"for deletion: {remove_ids} for {feature_id}."
            )

        # Apply the same post-processing as the initial generator
        if corrected_stories:
            feature_l2s = feature.get("l2_sources", [])
            corrected_stories = _validate_l2_refs(corrected_stories, feature_l2s, mfu_id)
            _check_ac_gherkin_quality(corrected_stories, mfu_id)

        # Review-fix A1: DO NOT append corrector-proposed function entries. functions[]
        # is the DETERMINISTIC story ceiling (set by _build_deterministic_functions from
        # the SRS S.5 events / batch steps). Letting the corrector add entries inflates
        # the ceiling from non-deterministic LLM output, so two runs on the identical SRS
        # could produce different story counts and non-reproducible Gate-6 sizing — a
        # direct violation of the #27 stable-ceiling invariant. The deterministic count is
        # authoritative; corrector-proposed additions are rejected (logged for audit).
        # Traceability is unaffected: ACs anchor to l2_source_ref SRS ids, not function ids.
        if new_fn_entries:
            _existing_fn_ids = {fn.get("id", "") for fn in feature.get("functions", [])}
            _rejected = [
                fn.get("id", "?")
                for fn in new_fn_entries
                if fn.get("id") and fn.get("id") not in _existing_fn_ids
            ]
            if _rejected:
                _Log.warn(
                    f"Stage 5b+: Rejected {len(_rejected)} corrector-proposed function "
                    f"entry/entries {_rejected} for {feature_id} — the deterministic "
                    f"functions[] ceiling is authoritative and must remain stable run-to-run."
                )

        merged = _merge_story_corrections(all_stories, corrected_stories, remove_ids=remove_ids)
        corrected_ids = [s.get("id", "") for s in corrected_stories] + list(remove_ids)
        _Log.ok(
            f"Stage 5b+: Applied — {len(corrected_stories)} story/stories "
            f"corrected/added, {len(remove_ids)} removed for {feature_id}."
        )
        return merged, corrected_ids

    # -----------------------------------------------------------------------
    # Stage 5b: Story Derivation per Feature
    # -----------------------------------------------------------------------

    def _run_stage5b(
        self,
        mfu_dir: Path,
        feature_manifest: dict,
        srs_content: str,
        mfu_id: str,
        module_id: str,
        module_name: str,
        technology_tag: str,
        language_hints: str = "",
        prior_output: dict | None = None,
        edit_targets=None,
    ) -> dict:
        """
        For each non-infrastructure feature in the manifest, calls the Stage 5b
        LLM generator to derive user stories. Assembles the final features_stories.json.
        Runs a one-shot critic review (non-gating, quality logging only).
        Hydrates stories with physical SRS UI rendering evidence via the SRSPhysicalLinker.

        language_hints (Layer 5): pre-rendered {LANGUAGE_HINTS} block propagated
        from run_for_mfu() into story generator, critic, and corrector prompts.
        """
        features = feature_manifest.get("features", [])
        populated_features = []
        total_story_calls = 0
        critic_log: list[str] = []

        # Derive focal SRS type once — used by Gate 7b zone_id post-pass (_fix_zone_id).
        # Same derivation logic as _run_stage5a (P2/P10 guard block).
        try:
            _stage5b_focal_files = _find_srs_files(mfu_dir)
            _stage5b_focal_fname = _stage5b_focal_files[0].name if _stage5b_focal_files else ""
        except Exception:
            _stage5b_focal_fname = ""
        _stage5b_srs_type = (
            _detect_srs_document_type(_stage5b_focal_fname) if _stage5b_focal_fname else ""
        )

        for feature in features:
            feature_id = feature.get("id", "UNKNOWN-F0")
            is_infra = feature.get("is_infrastructure", False)
            # Evidence-based ceiling: one story per function entry (collapsing encouraged).
            # story_budget is deprecated — functions[] is the authoritative limit.
            functions_ceiling = len(feature.get("functions", []))

            if is_infra:
                _Log.observe(
                    f"Feature {feature_id}: is_infrastructure=True -- skipping story generation."
                )
                populated_features.append({**feature, "user_stories": []})
                continue

            if functions_ceiling == 0:
                # P9 — P2-guard / Stage 5a fallback: LLM returned is_infrastructure=True
                # with functions=[], then P2-guard overrode is_infrastructure→False but
                # left functions[] empty. Without a ceiling the story loop would be
                # skipped, causing Gate 6a-ZERO to fail.
                # Fallback: allow LLM to derive up to 2 stories freely from full SRS.
                # Language-agnostic: this can occur for any single-event UIBlueprint form
                # (VB6, PB, etc.) whose S.5 section uses key-value format rather than an
                # event table, so the linker finds no S5::EVENT entries to inject.
                _FALLBACK_CEILING = 2
                _Log.warn(
                    f"Feature {feature_id}: is_infrastructure=False but functions_ceiling=0 "
                    f"(P9 fallback — LLM derived no functions, possibly P2-guard override). "
                    f"Using fallback ceiling={_FALLBACK_CEILING}; LLM will derive freely."
                )
                functions_ceiling = _FALLBACK_CEILING

            srs_zone_content = _extract_srs_zone_content(srs_content, feature)

            # ── Option B: build SRS anchor index + map block for this feature ─
            # Anchor index derived from feature manifest data (bounding_box.controls
            # + functions[].l2_source_ref). No raw SRS parsing needed — manifest
            # already has all valid refs for this feature.
            # The anchor map block is injected into the generator prompt via
            # {SRS_ANCHOR_MAP}, constraining the LLM to reference only valid anchors
            # and providing explicit guidance for SRS-gap modules (no error events).
            _anchor_index = _build_srs_anchor_index(feature, mfu_id)
            _anchor_map_block = _build_srs_anchor_map_block(_anchor_index, mfu_id)
            if _anchor_index:
                _Log.observe(
                    f"[ANCHOR-MAP] {feature_id}: {len(_anchor_index)} valid anchors indexed "
                    f"({sum(1 for t in _anchor_index.values() if t == 'control')} controls, "
                    f"{sum(1 for t in _anchor_index.values() if 'event' in t)} events)."
                )

            _Log.act(
                f"Stage 5b: Generating stories for feature {feature_id} "
                f"(functions ceiling={functions_ceiling})..."
            )

            prompt = _build_stage5b_prompt(
                template=self._5b_template,
                feature_entry=feature,
                srs_zone_content=srs_zone_content,
                mfu_id=mfu_id,
                module_id=module_id,
                module_name=module_name,
                technology_tag=technology_tag,
                functions_ceiling=functions_ceiling,
                language_hints=language_hints,
                anchor_map=_anchor_map_block,
            )
            prompt = _prepend_human_guidance(prompt, getattr(self, "_human_guidance", None))  # #42

            try:
                response = self._stage5_llm.complete(
                    system_prompt=(
                        "You are an expert Agile Product Owner specialising in legacy system migration. "
                        "Derive User Stories with Gherkin Acceptance Criteria for the given Feature. "
                        "Output ONLY a JSON array of UserStory objects inside <STORIES> tags. "
                        "Each story must pass the four-condition validity test. "
                        f"Hard limit: {functions_ceiling} stories maximum (one per function entry)."
                    ),
                    user_prompt=prompt,
                )
                total_story_calls += 1
            except Exception as e:
                _Log.err(f"Stage 5b LLM call failed for feature {feature_id}: {e}")
                populated_features.append({**feature, "user_stories": []})
                critic_log.append(f"[{feature_id}] LLM error: {e}")
                continue

            # -- Parse stories from <STORIES> tags (with parse-failure retry)
            raw_stories = _extract_stories_json(response)
            if raw_stories is None:
                # Large responses (>10K chars) sometimes produce malformed JSON because
                # the LLM's reasoning output bleeds into the JSON block, or the JSON is
                # truncated due to output length limits.  Retry once with a compact prompt
                # that instructs the LLM to output ONLY the JSON array, no reasoning text.
                _Log.warn(
                    f"Feature {feature_id}: Could not parse <STORIES> JSON "
                    f"(response len={len(response)}). Retrying with compact-output prompt..."
                )
                _compact_prompt = (
                    f"The following feature requires User Stories in JSON.\n"
                    f"Output ONLY a valid JSON array inside <STORIES></STORIES> tags.\n"
                    f"No reasoning text, no markdown, no explanation — ONLY the JSON array.\n\n"
                    f"Feature:\n{json.dumps(feature, ensure_ascii=False)}\n\n"
                    f"Hard limit: {functions_ceiling} stories maximum.\n"
                    f"Each story must have: id, title, as_a, i_want_to, so_that, "
                    f"story_points, acceptance_criteria (min 3), l2_sources, "
                    f"technical_notes, migration_hint, non_functional_requirements.\n"
                    f"Each AC must have: id, path_type, given, when, then, l2_source_ref."
                )
                try:
                    _retry_resp = self._stage5_llm.complete(
                        system_prompt=(
                            "You are an expert Agile Product Owner. "
                            "Output ONLY a JSON array inside <STORIES> tags. "
                            "No other text."
                        ),
                        user_prompt=_compact_prompt,
                    )
                    raw_stories = _extract_stories_json(_retry_resp)
                except Exception as _re:
                    _Log.warn(f"Story parse retry call failed for {feature_id}: {_re}")
                    raw_stories = None

                if raw_stories is None:
                    _Log.warn(
                        f"Feature {feature_id}: parse retry also failed. "
                        "Attaching empty story list."
                    )
                    populated_features.append({**feature, "user_stories": []})
                    critic_log.append(f"[{feature_id}] WARN: No parseable story JSON.")
                    continue
                _Log.ok(
                    f"Feature {feature_id}: parse retry succeeded — "
                    f"{len(raw_stories)} stories recovered."
                )

            # Enforce the functions ceiling -- guard against LLM over-generation.
            if len(raw_stories) > functions_ceiling:
                _Log.warn(
                    f"Feature {feature_id}: LLM generated {len(raw_stories)} stories "
                    f"but ceiling is {functions_ceiling}. Truncating."
                )
                raw_stories = raw_stories[:functions_ceiling]

            # Post-processing: repair synthetic/invalid l2_refs (language-agnostic)
            feature_l2s = feature.get("l2_sources", [])
            raw_stories = _validate_l2_refs(raw_stories, feature_l2s, mfu_id)

            # Post-processing: audit AC 'when' clauses for implementation contamination
            _check_ac_gherkin_quality(raw_stories, mfu_id)

            _Log.ok(
                f"Feature {feature_id}: {len(raw_stories)} story/stories parsed "
                f"(ceiling={functions_ceiling})."
            )

            # ── Option C: normalizer pass after generator output ─────────────
            # Runs before the critic sees stories for the first time.
            # Enforces P1 mechanical rules deterministically:
            #   Rule 1 — l2_source_ref must exist in anchor_index
            #   Rule 2 — Negative Path / Edge Case ACs must not use success_event anchors
            _gen_feature = {**feature, "user_stories": raw_stories}
            _gen_feature, _norm_modified = _normalize_stories(
                _gen_feature, mfu_id, _anchor_index, srs_type=_stage5b_srs_type
            )
            if _norm_modified:
                raw_stories = _gen_feature.get("user_stories", raw_stories)

            # ── Gate 7b post-pass after generator output (Fix 2) ─────────────
            # Correct invalid zone_id before the critic sees the feature.
            # bounding_box is on the feature, not the stories — apply to
            # the assembled feature dict before appending to populated_features.
            _gen_feature_final = {**feature, "user_stories": raw_stories}
            _gen_feature_final, _zone_fixed_gen = _fix_zone_id(
                _gen_feature_final, mfu_id, _stage5b_srs_type
            )
            if _zone_fixed_gen:
                feature = _gen_feature_final
                raw_stories = feature.get("user_stories", raw_stories)

            # -- Gate 7e post-pass after generator output ----------------
            # Normalise NFR field names before first critic pass so the critic
            # never sees quality_attribute / criterion alias fields.
            _gen_feature_nfr = {**feature, "user_stories": raw_stories}
            _gen_feature_nfr, _nfr_fixed_gen = _normalize_nfr_fields(_gen_feature_nfr, mfu_id)
            if _nfr_fixed_gen:
                feature = _gen_feature_nfr
                raw_stories = feature.get("user_stories", raw_stories)

            populated_features.append({**feature, "user_stories": raw_stories})

        # -- Assemble full features_stories document
        total_stories = sum(len(f.get("user_stories", [])) for f in populated_features)
        _Log.ok(
            f"Stage 5b assembled: {len(populated_features)} feature(s), "
            f"{total_stories} total story/stories across {total_story_calls} LLM call(s)."
        )

        # ── Fix 3: dead-code det-fallback recovery ────────────────────────────
        # If Stage 5a produced a PASS_DETERMINISTIC_FALLBACK manifest AND the LLM
        # story generator could not find any user-facing behavior in a feature
        # (user_stories=[]), revert is_infrastructure to True before the critic runs.
        #
        # Rationale: NORM-MANIFEST flips is_infrastructure=False for all APIContracts
        # det-fallback features (to fix RC-3: genuine headless modules misclassified as
        # infrastructure). For dead code (e.g., a VB6 module with only global constants),
        # the LLM correctly generates 0 stories — use that as the reclassification signal.
        #
        # Gate: det-fallback manifests only (Stage 5a status = PASS_DETERMINISTIC_FALLBACK).
        # Non-det-fallback manifests are not affected.
        _is_det_fallback_manifest = (
            feature_manifest.get("generation_metadata", {}).get("final_status")
            == "PASS_DETERMINISTIC_FALLBACK"
        )
        if _is_det_fallback_manifest:
            _recovered_features = []
            for _pf in populated_features:
                _pf_infra = _pf.get("is_infrastructure", False)
                _pf_stories = _pf.get("user_stories") or []
                if not _pf_infra and not _pf_stories:
                    # LLM found no behavior → dead code → revert to infrastructure
                    _pf = {**_pf, "is_infrastructure": True, "category": "Infrastructure"}
                    _Log.ok(
                        f"[DEAD-CODE-RECOVERY] {_pf.get('id', '?')}: "
                        f"det-fallback + 0 stories → reverted is_infrastructure=True "
                        f"(reason: dead_code_no_stories)"
                    )
                _recovered_features.append(_pf)
            populated_features = _recovered_features

        # ── #42 EDIT-MODE MERGE (before the critic) ───────────────────────────
        # In edit mode the caller passes the pre-edit output (prior_output = the .bak).
        # We deterministically merge the just-generated stories onto that known-good base
        # — only the reviewer-targeted story(ies) change; every other story and all
        # derived/structural fields are restored from the backup. Crucially this happens
        # BEFORE critic call #1, so the critic + correction loop validate exactly the
        # document that will ship (no validate-one-ship-another gap), and a reviewer edit
        # that contradicts a restored/pinned anchor is caught here as a grounding failure.
        # Normal (non-edit) runs pass prior_output=None and are completely unaffected.
        _edit_not_applied_note = ""
        if prior_output:
            _gen_doc = {**feature_manifest, "features": populated_features}
            _merged, _merge_notes = _reconcile_edit(_gen_doc, prior_output, target_ids=edit_targets)
            populated_features = _merged.get("features", populated_features)
            for _n in _merge_notes:
                _Log.ok(f"[EDIT-MERGE] {mfu_id}: {_n}")
            # #42-transparency: a reviewer change to a preserved derived/structural field is not
            # applied by design — surface it via generation_metadata (a log line is not reviewer-visible).
            _edit_not_applied_note = next((n for n in _merge_notes if n.startswith("NOT APPLIED")), "")

        final_manifest = {**feature_manifest, "features": populated_features}
        final_manifest.setdefault("generation_metadata", {}).update(
            {
                "generator_model": self._stage5_llm.effective_model,
                "iteration_count": 1,
                "final_status": "IN_PROGRESS",  # ← lifecycle placeholder; resolved at function end
                "generated_at": datetime.now(UTC).isoformat(),
            }
        )
        if _edit_not_applied_note:
            final_manifest["generation_metadata"]["edit_fields_not_applied"] = _edit_not_applied_note

        # Lifecycle variables — track state through critic / corrector chain.
        # Written to generation_metadata exactly once, at function end.
        # Status codes:
        #   PASS                      — critic satisfied (initial or after correction)
        #   FAIL_PENDING_CORRECTION   — critic returned FAIL, correction loop not yet exhausted
        #   FAIL_CORRECTOR_NO_PROGRESS — correction loop exited early (LLM returned nothing new)
        #   FAIL_CORRECTION_EXHAUSTED — correction loop ran max_iterations without PASS
        #   INCOMPLETE_LLM_ERROR      — LLM call threw exception; verdict unknown
        _run_status = "IN_PROGRESS"
        _failure_reason = ""
        _initial_critic_verdict = "IN_PROGRESS"

        # -- Critic call #1
        _Log.reason("Stage 5b: Running Critic review (iteration 1)...")
        _critic_system = (
            "You are a rigorous QA critic for an Agile legacy modernisation project. "
            "Review the Feature Manifest and User Stories against the SRS source. "
            "Output your verdict inside <STATUS>, <FEEDBACK>, <SUGGESTED_FIX>, "
            "and <FAILED_STORY_IDS> tags."
        )
        try:
            critic_response = self._run_critic_call(
                srs_content, final_manifest, language_hints, mfu_id, _critic_system
            )
            verdict, feedback, suggested_fix, failed_story_ids = _extract_critic_result(
                critic_response
            )
        except Exception as e:
            _Log.err(f"Critic LLM call failed for {mfu_id}: {e}")
            verdict = "FAIL_PARSE_ERROR"
            feedback = str(e)
            suggested_fix = ""
            failed_story_ids = []

        _Log.ok(f"Critic verdict for {mfu_id}: {verdict}")

        # ── #42 surfacing — did the reviewer's edit conflict with the SRS? ──
        # If this was a targeted edit (edit_targets) and critic call #1 raised a GROUNDING
        # failure (Gate 9a/8a/5f) on a target story, the correction loop will revert the edit
        # toward the SRS-grounded value. We remember that here so we can honestly tell the
        # reviewer their feedback was overridden — even when the final status becomes PASS.
        _edit_grounding_conflict = []
        _edit_conflict_reason = ""
        try:
            if edit_targets and verdict != "PASS" and _has_grounding_failure(feedback):
                _tset = set(edit_targets)
                # Only flag when a TARGET story is explicitly implicated. A grounding failure on a
                # NON-target story is a latent defect the corrector fixed (a good thing) — not the
                # reviewer's edit being reverted — so we must not falsely claim their edit failed.
                _edit_grounding_conflict = [s for s in (failed_story_ids or []) if s in _tset]
                _edit_conflict_reason = (feedback or "").strip()[:600]
        except Exception:
            _edit_grounding_conflict = []

        if critic_log:
            _Log.warn(f"Critic quality log: {len(critic_log)} note(s).")
            for entry in critic_log:
                _Log.warn(f"  {entry}")
        if feedback and verdict != "PASS":
            _Log.warn(f"Critic feedback: {feedback}")
        if suggested_fix:
            _Log.observe(f"Critic suggested fix: {suggested_fix}")

        # ── Resolve _run_status after critic call #1 ────────────────────────
        _initial_critic_verdict = verdict
        if verdict == "PASS":
            _run_status = "PASS"
        elif verdict == "FAIL_PARSE_ERROR":
            _run_status = "INCOMPLETE_LLM_ERROR"
            _failure_reason = feedback
        else:  # verdict == "FAIL"
            _run_status = "FAIL_PENDING_CORRECTION"

        # -- Stage 5b+ Correction Loop (fires on FAIL, up to max_iter-1 passes) ----
        # Architecture:
        #   iteration 1 = initial generation + critic call #1  (done above)
        #   iteration 2 = correction pass #1  + critic call #2
        #   iteration 3 = correction pass #2  + critic call #3  (if max_iter >= 3)
        #   ...up to self.max_iterations total iterations
        #
        # Loop exits early when:
        #   (a) verdict == "PASS"        — success; no further correction needed
        #   (b) no correction was applied — LLM returned nothing new; further
        #       iterations would produce identical output (infinite-loop guard)
        #   (c) corrector template absent — 05b_story_corrector.txt not loaded
        #
        # Language-agnostic: correction pass operates on the story JSON structure
        # regardless of source technology (PowerBuilder, VB6, COBOL, etc.).
        correction_applied = False
        current_iteration = 1
        current_features = populated_features

        # #3 — oscillation guard. Some Critic⇄corrector conflicts can never converge:
        # e.g. the critic demands a boundary/guard S5 event as the anchor for a negative
        # AC, while the deterministic anchor normalizer keeps reverting it to a GAP marker
        # (success_anchor_on_neg_path). Each pass "corrects" then reverts, producing the
        # SAME output every cycle — the existing "no correction applied" guard never fires
        # and all iterations burn. We track the FULL post-correction content; if a pass
        # reproduces a previously-seen state, further iterations cannot help, so stop.
        #
        # Review-fix M1/M2: the signature MUST hash the full corrected content, not just
        # the (story_id, ac_id, l2_source_ref) anchors. A correction to a non-anchor field
        # (a Gate-5g "when" clause, an NFR, bounding_box controls, a zone) leaves anchors
        # unchanged; an anchor-only signature therefore falsely detected "oscillation" on
        # the FIRST corrected pass and broke before the re-critic — labelling a genuinely-
        # correctable MFU FAIL_CORRECTION_EXHAUSTED. Hashing full content fires ONLY on a
        # true byte-identical stall. This also resolves M2: every state we break on has
        # already been critiqued (the seed by critic #1, each added pass by its re-critic),
        # so the standing `verdict` is accurate and no closing re-critic is needed.
        import hashlib as _hashlib

        def _content_signature(_feats) -> str:
            try:
                _blob = json.dumps(_feats or [], sort_keys=True, ensure_ascii=False, default=str)
            except Exception:
                _blob = repr(_feats)
            return _hashlib.md5(_blob.encode("utf-8")).hexdigest()

        _seen_content_sigs = {_content_signature(current_features)}
        _oscillation_detected = False

        while verdict == "FAIL" and current_iteration < self.max_iterations:
            any_corrected_this_pass = False
            corrected_features_pass = []

            for feature in current_features:
                f_id = feature.get("id", "")
                stories = feature.get("user_stories", [])
                feature_failed_ids = [s for s in failed_story_ids if s.startswith(f_id)]
                coverage_gaps = _detect_coverage_gaps(feature, stories)

                # ── Corrector: only when there are story-level failures or coverage gaps ──
                # Feature-level failures (Gate 7d / 7b / 5f) populate failed_story_ids
                # at the story level only when the critic references a specific story.
                # When the critic fails a feature-level gate with no story IDs, the
                # corrector has nothing to do — but the deterministic post-passes below
                # MUST still run. Separating the corrector guard from the post-pass
                # guard is the correct enterprise-grade fix (language-neutral).
                corrected_ids = []
                if feature_failed_ids or coverage_gaps:
                    corrected_stories, corrected_ids = self._run_stage5b_correction(
                        feature=feature,
                        all_stories=stories,
                        failed_story_ids=feature_failed_ids,
                        suggested_fix=suggested_fix,
                        coverage_gap_l2ids=coverage_gaps,
                        srs_content=srs_content,
                        mfu_id=mfu_id,
                        module_id=module_id,
                        technology_tag=technology_tag,
                        language_hints=language_hints,
                    )
                    feature = {**feature, "user_stories": corrected_stories}
                    if corrected_ids:
                        any_corrected_this_pass = True
                        correction_applied = True
                        critic_log.append(
                            f"[{f_id}] Correction applied (iter {current_iteration + 1}): "
                            f"{corrected_ids}"
                        )

                # ── Feature-level post-passes: run for EVERY feature ──────────
                # These operate on bounding_box / l2_source_ref fields that the
                # corrector LLM cannot modify (it only emits story objects). They
                # must execute regardless of whether the corrector was called, so
                # that purely feature-level critic failures (Gate 7d, 7b, 5f) get
                # resolved and the critic re-run can verify the fix.

                # ── Gate 7d post-pass (BUG-FIX-A) ────────────────────────────
                # Inject control names from technical_notes into bounding_box.controls[]
                # The corrector output schema has no bounding_box key; this deterministic
                # pass bridges that gap so the critic sees populated controls[] next iter.
                # Fix D: pass mfu_dir so the S.3 empty guard (Fix B) can fire and
                # prevent the Gate 7d three-way contradiction loop on S.3-absent MFUs.
                feature, _bb_injected = _inject_bounding_box_controls_from_notes(
                    feature, mfu_id, mfu_dir=mfu_dir
                )
                if _bb_injected:
                    any_corrected_this_pass = True
                    correction_applied = True

                # ── Gate 5f post-pass (BUG-FIX-B) ────────────────────────────
                # Fill empty l2_source_ref with the best available fallback so
                # Gate 5f does not hard-fail on SRS-gap ACs (no error event in SRS).
                feature, _ref_fixed = _fix_empty_l2_source_refs(feature, mfu_id)
                if _ref_fixed:
                    any_corrected_this_pass = True
                    correction_applied = True

                # ── Gate 7b post-pass (Fix 2) ─────────────────────────────────
                # Correct invalid zone_id values in bounding_box (e.g. "FULL_MODULE").
                # The corrector output schema has no bounding_box key; all feature-level
                # bounding_box fixes must be deterministic. This mirrors Gate 7d.
                feature, _zone_fixed = _fix_zone_id(feature, mfu_id, _stage5b_srs_type)
                if _zone_fixed:
                    any_corrected_this_pass = True
                    correction_applied = True

                # -- Gate 7e post-pass -- NFR field normalisation -------
                # Renames aliased NFR keys (quality_attribute->category,
                # criterion->requirement) and clamps category to the schema enum.
                # Must run in the correction loop so re-critic sees clean NFRs.
                feature, _nfr_normalised = _normalize_nfr_fields(feature, mfu_id)
                if _nfr_normalised:
                    any_corrected_this_pass = True
                    correction_applied = True

                # ── Option C: normalizer pass after corrector output ──────────
                # Rebuilds anchor index from the (potentially updated) feature so
                # any controls injected by BUG-FIX-A are included in the index.
                # Runs AFTER BUG-FIX-A and BUG-FIX-B so the index reflects the
                # most up-to-date bounding_box.controls state.
                _loop_anchor_index = _build_srs_anchor_index(feature, mfu_id)
                feature, _norm_loop_modified = _normalize_stories(
                    feature, mfu_id, _loop_anchor_index, srs_type=_stage5b_srs_type
                )
                if _norm_loop_modified:
                    any_corrected_this_pass = True
                    correction_applied = True

                corrected_features_pass.append(feature)

            if not any_corrected_this_pass:
                _Log.warn(
                    f"Stage 5b+: No stories corrected in iteration "
                    f"{current_iteration + 1} for {mfu_id}. Exiting correction loop early."
                )
                break

            # #3 — oscillation guard. A correction WAS applied this pass, but if the
            # FULL corrected content is byte-identical to a state we have already seen,
            # the corrector and the deterministic post-passes are flip-flopping (e.g. the
            # critic keeps steering a negative AC to a success/boundary event that the
            # normalizer keeps reverting) and re-running the critic will only reproduce the
            # same FAIL. Stop now and accept the current output (flagged for human review)
            # instead of burning the remaining iterations. Because the repeated state was
            # already critiqued, the standing `verdict` is accurate (M2).
            _pass_sig = _content_signature(corrected_features_pass)
            if _pass_sig in _seen_content_sigs:
                _oscillation_detected = True
                current_features = corrected_features_pass
                final_manifest = {**final_manifest, "features": current_features}
                _Log.warn(
                    f"Stage 5b+: Correction is stalled for {mfu_id} — this pass reproduced a "
                    f"prior corrected state (corrector⇄Critic could not converge). Stopping the "
                    f"loop early to avoid burning iterations; accepting the current output for "
                    f"human review."
                )
                break
            _seen_content_sigs.add(_pass_sig)

            current_iteration += 1
            current_features = corrected_features_pass
            final_manifest = {**final_manifest, "features": current_features}
            final_manifest["generation_metadata"]["iteration_count"] = current_iteration
            final_manifest["generation_metadata"]["correction_applied"] = True

            _Log.reason(
                f"Stage 5b+: Re-running Critic after correction (iteration {current_iteration})..."
            )
            try:
                critic_response_n = self._run_critic_call(
                    srs_content,
                    final_manifest,
                    language_hints,
                    mfu_id,
                    system_prompt=(
                        "You are a rigorous QA critic for an Agile legacy modernisation project. "
                        f"Review the corrected Feature Manifest and User Stories (iteration "
                        f"{current_iteration}) against the SRS source. "
                        "Output your verdict inside <STATUS>, <FEEDBACK>, "
                        "<SUGGESTED_FIX>, and <FAILED_STORY_IDS> tags."
                    ),
                )
                verdict, feedback, suggested_fix, failed_story_ids = _extract_critic_result(
                    critic_response_n
                )
            except Exception as e:
                _Log.err(f"Critic call (iteration {current_iteration}) failed for {mfu_id}: {e}")
                verdict = "FAIL_EXHAUSTED"
                # Explicitly sync _run_status so _STATUS_MAP resolves
                # to FAIL_CORRECTION_EXHAUSTED (not FAIL_PENDING_CORRECTION)
                # when the re-critic throws an exception inside the loop.
                _run_status = "FAIL_CORRECTION_EXHAUSTED"
                suggested_fix = ""
                failed_story_ids = []
                break

            _Log.ok(f"Critic verdict (iteration {current_iteration}) for {mfu_id}: {verdict}")
            if feedback and verdict != "PASS":
                _Log.warn(f"Critic feedback (iteration {current_iteration}): {feedback}")
            if suggested_fix and verdict != "PASS":
                _Log.observe(
                    f"Critic suggested fix (iteration {current_iteration}): {suggested_fix}"
                )
            # Sync _run_status with the re-critic verdict so the _STATUS_MAP
            # below resolves to PASS when the correction loop succeeds.
            if verdict == "PASS" and _run_status == "FAIL_PENDING_CORRECTION":
                _run_status = "PASS"

        # Review-fix C3: persist loop telemetry regardless of how the loop exited (normal
        # completion, no-correction break, oscillation break, or re-critic exception). The
        # in-loop writes were only reached on a full pass, leaving iteration_count/
        # correction_applied stale on an early break.
        _gm_sync = final_manifest.setdefault("generation_metadata", {})
        _gm_sync["iteration_count"] = current_iteration
        _gm_sync["correction_applied"] = bool(correction_applied)

        _STATUS_MAP = {
            "PASS": "PASS",
            "INCOMPLETE_LLM_ERROR": "FAIL_PARSE_ERROR",
            "FAIL_PENDING_CORRECTION": "FAIL_CORRECTION_EXHAUSTED",
            "FAIL_MAX_RETRIES": "FAIL_CORRECTION_EXHAUSTED",
            # Review-fix A3: statuses that were previously ABSENT and fell through to the
            # worst-case default, mislabelling a feature that HAS a full manifest+stories
            # as "no parseable output" (which drives a misleading REGEN recommendation).
            # The re-critic-exception path sets _run_status = "FAIL_CORRECTION_EXHAUSTED"
            # directly, and FAIL_CORRECTOR_NO_PROGRESS is a documented terminal status.
            "FAIL_CORRECTION_EXHAUSTED": "FAIL_CORRECTION_EXHAUSTED",
            "FAIL_CORRECTOR_NO_PROGRESS": "FAIL_CORRECTION_EXHAUSTED",
        }
        if _run_status not in _STATUS_MAP:
            _Log.warn(
                f"[STATUS-MAP] {mfu_id}: unmapped _run_status '{_run_status}' — defaulting to "
                f"FAIL_CORRECTION_EXHAUSTED (a manifest exists; not 'no parseable output'). "
                f"Add an explicit mapping if this recurs."
            )
        # Safer default: a populated manifest reaching here is an exhausted correction, not
        # absent output. Only a genuinely empty/parse-failed run should be NO_PARSEABLE.
        final_status_val = _STATUS_MAP.get(_run_status, "FAIL_CORRECTION_EXHAUSTED")

        # ── #20: Relaxed gates for framework / menu / ancestor features ──────────
        # A residual FAIL_CORRECTION_EXHAUSTED on a reusable-framework unit (menu,
        # ancestor, NVO, shared utility, MDI shell) reflects business-value and
        # controls gates that DO NOT APPLY to framework code — not a real defect.
        # Such features still receive a critiqued, correction-improved backlog; we
        # accept the last output and relabel it PASS_RELAXED_GATES (visible but
        # non-blocking). Structural validity remains enforced by schema validation
        # (#19), so this only relaxes the subjective quality gates.
        #
        # Feature-level + config-driven: classification comes from the active language
        # profile's framework_patterns (language_profiles.yaml), so sibling BUSINESS
        # MFUs in the same module are unaffected, and new languages extend via YAML.
        if (
            final_status_val == "FAIL_CORRECTION_EXHAUSTED"
            and _has_grounding_failure(feedback)
            and not _oscillation_detected
        ):
            # ── #31: Anti-hallucination guard — never relax a GROUNDING failure ──────
            # The last critic feedback implicates a grounding gate (9a SRS-contradiction,
            # 5f invalid l2_source_ref, 8a absent-artifact) or explicit fabrication. This
            # is content NOT grounded in the SRS (e.g. a login feature invented for a
            # passive input component). Relaxing this would ship a hallucination as PASS,
            # so it is marked FAIL_HALLUCINATION and excluded from the framework carve-out.
            #
            # #3 exclusion: when the loop stopped due to Critic⇄normalizer OSCILLATION,
            # the "grounding contradiction" the critic reports is an ARTIFACT of the
            # normalizer reverting the model's (often correct) anchor — not a model
            # hallucination. (Observed: an Edge-Case AC for a PF3/ENTER key press whose
            # correct anchor is a navigation success_event; the normalizer reverts it to
            # the unmapped-key event, which the critic then flags as contradicting the
            # AC's premise.) Mislabeling this as FAIL_HALLUCINATION erodes trust in that
            # signal, so oscillation is routed to FAIL_CORRECTION_EXHAUSTED with an honest
            # disclosure (below) instead. It remains a blocking FAIL for human review.
            final_status_val = "FAIL_HALLUCINATION"
            _gm = final_manifest.setdefault("generation_metadata", {})
            _gm["gate_profile"] = "grounding_failed"
            _gm["failure_reason"] = (feedback or "")[:1000]
            _Log.err(
                f"[HALLUCINATION-GUARD] {mfu_id}: critic reported a GROUNDING-gate failure "
                f"(9a/5f/8a / SRS-contradiction) — NOT eligible for relaxed gates. "
                f"Marked FAIL_HALLUCINATION (content not grounded in SRS; needs human review)."
            )
        elif final_status_val == "FAIL_CORRECTION_EXHAUSTED":
            _fw_patterns: tuple = ()
            try:
                if getattr(self, "_lang_registry", None):
                    _prof = self._lang_registry.get_profile(technology_tag)
                    _fw_patterns = tuple(getattr(_prof, "framework_patterns", ()) or ())
            except Exception:
                _fw_patterns = ()
            # Project-level override / additions (optional), merged on top of profile.
            try:
                _proj_fw = (
                    (getattr(self, "config", {}) or {})
                    .get("stage5", {})
                    .get("framework_artifact_patterns", [])
                )
                if _proj_fw:
                    _fw_patterns = (*_fw_patterns, *(str(p) for p in _proj_fw))
            except Exception:
                pass

            _focal = ""
            try:
                _focal = _resolve_focal_artifact(mfu_dir) or ""
            except Exception:
                _focal = ""

            # Review-fix A2/A4: the framework carve-out relaxes Gate7c_controls. Do NOT
            # relax when controls cannot be trusted — either S.3 verification errored
            # (marker set by _correct_bounding_box_controls) or the feature still carries
            # non-empty controls while the critic implicates Gate 7 (i.e. controls that
            # failed verification and may be hallucinated). In that case keep the FAIL so a
            # human reviews it, rather than shipping unverified controls as an approvable
            # PASS. (Fail-closed; narrow — only affects framework units with suspect controls.)
            _s3_err = bool(
                (final_manifest.get("generation_metadata") or {}).get("s3_correction_error")
            )
            _has_nonempty_controls = any(
                (not _f.get("is_infrastructure"))
                and ((_f.get("bounding_box") or {}).get("controls"))
                for _f in (final_manifest.get("features") or [])
            )
            _gate7_implicated = bool(
                re.search(r"gate\s*7|\bcontrol", feedback or "", re.IGNORECASE)
            )
            _controls_unsafe = _s3_err or (_has_nonempty_controls and _gate7_implicated)

            if (
                _focal
                and _fw_patterns
                and _is_framework_artifact(_focal, _fw_patterns)
                and not _controls_unsafe
            ):
                final_status_val = "PASS_RELAXED_GATES"
                _gm = final_manifest.setdefault("generation_metadata", {})
                _gm["gate_profile"] = "framework"
                _gm["framework_focal"] = _focal
                _gm["relaxed_gates"] = ["Gate5/6_business_value", "Gate7c_controls"]
                _Log.ok(
                    f"[RELAXED-GATES] {mfu_id}: focal '{_focal}' is a framework/menu/ancestor "
                    f"unit ({technology_tag}) — relabeled FAIL_CORRECTION_EXHAUSTED → "
                    f"PASS_RELAXED_GATES. Business-value gates relaxed; schema still enforced."
                )
            elif (
                _focal
                and _fw_patterns
                and _is_framework_artifact(_focal, _fw_patterns)
                and _controls_unsafe
            ):
                _Log.warn(
                    f"[RELAXED-GATES] {mfu_id}: framework unit '{_focal}' NOT relaxed — controls "
                    f"could not be trusted (s3_error={_s3_err}, unverified_controls="
                    f"{_has_nonempty_controls and _gate7_implicated}); kept FAIL for human review."
                )
            elif _is_5g_only_failure(feedback):
                # MVP carve-out: the SOLE residual is Gate 5g (a "when" clause phrased as a
                # system event rather than a user action). This is a cosmetic Gherkin nit with
                # no impact on story correctness, coverage, or grounding — so we relax it to a
                # visible, spot-check PASS_RELAXED_GATES rather than hard-failing the MFU. All
                # other gates (incl. grounding 9a/8a/5f, handled above) remain blocking.
                final_status_val = "PASS_RELAXED_GATES"
                _gm = final_manifest.setdefault("generation_metadata", {})
                _gm["gate_profile"] = "gherkin_5g_only"
                _gm["relaxed_gates"] = ["Gate5g_when_clause_phrasing"]
                _Log.ok(
                    f"[RELAXED-GATES] {mfu_id}: only Gate 5g (when-clause phrasing) failed — "
                    f"relabeled FAIL_CORRECTION_EXHAUSTED → PASS_RELAXED_GATES (MVP: "
                    f"implementation-detail when-clauses are non-blocking; flagged for spot-check)."
                )
            elif final_manifest.get("features") and all(
                f.get("is_infrastructure") for f in final_manifest["features"]
            ):
                # Infrastructure-only feature (orphan data object / dead-code stub / shared
                # utility) that produces NO user stories. A residual FAIL here can only be a
                # non-grounding gate (grounding is diverted to FAIL_HALLUCINATION above) — e.g.
                # Gate 1c l2_source ID formatting (the MOD-MAIL/SYS-DATA-01 case). There is no
                # shippable backlog to gate, and the corrector has no stories to act on, so the
                # failure is cosmetic. Relax to a visible, spot-check PASS_RELAXED_GATES —
                # parallel to the framework carve-out. Schema validity is still enforced (#19).
                final_status_val = "PASS_RELAXED_GATES"
                _gm = final_manifest.setdefault("generation_metadata", {})
                _gm["gate_profile"] = "infrastructure_no_stories"
                _gm["relaxed_gates"] = ["Gate1c_l2_format", "story_traceability_gates"]
                _Log.ok(
                    f"[RELAXED-GATES] {mfu_id}: infrastructure-only feature (no user stories) — "
                    f"relabeled FAIL_CORRECTION_EXHAUSTED → PASS_RELAXED_GATES (non-grounding, "
                    f"cosmetic metadata gate; no shippable backlog to block)."
                )

        # #3 — if the loop stopped because the corrector and Critic could not converge and
        # no carve-out reclassified it, record an honest, human-actionable failure_reason
        # so the PM sees WHY it exhausted rather than a bare FAIL_CORRECTION_EXHAUSTED.
        # NOTE: keep this CAUSE-AGNOSTIC. Oscillation can arise from an anchor disagreement
        # (a boundary/guard event the normalizer keeps reverting to a GAP marker) OR from a
        # structural gate the corrector cannot satisfy (e.g. a Gate-6 functions-ceiling
        # deadlock, or Gate-5 vocabulary it keeps re-introducing). Asserting one specific
        # cause here previously contradicted the accurate `failure_summary`/`gate_reference`
        # sitting right beside it — so we point the reviewer to those instead of guessing.
        if _oscillation_detected and final_status_val == "FAIL_CORRECTION_EXHAUSTED":
            _gm_osc = final_manifest.setdefault("generation_metadata", {})
            if not _gm_osc.get("failure_reason"):
                _gm_osc["failure_reason"] = (
                    "Correction loop stopped early: the corrector and the Critic could not "
                    "converge (the same gate failure repeated across passes, so further "
                    "iterations would not help). The story content is generally sound — see "
                    "`review_guidance.failure_summary` and `gate_reference` for the exact "
                    "gate(s) that failed, then `revise` the flagged story/AC or edit it "
                    "directly and re-validate."
                )

        final_manifest["generation_metadata"]["final_status"] = final_status_val
        if _failure_reason:
            final_manifest["generation_metadata"]["failure_reason"] = _failure_reason

        # ── #38 (MVP) — emit deterministic, PM-facing review guidance at FEATURE level ──
        # Self-describing failure: status -> {severity, recommended_action,
        # approve_as_is_allowed, guideline, failure_summary, flagged_story_ids}. The app
        # renders this when a PM opens the feature in the tree; approve_as_is_allowed is
        # the boolean the UI keys the one-click-approve button on (hard-False for
        # hallucination / schema-invalid). No LLM call — pure mapping.
        try:
            final_manifest["generation_metadata"]["review_guidance"] = _build_review_guidance(
                final_status_val, feedback, failed_story_ids
            )
        except Exception as _rg_err:
            _Log.warn(
                f"[REVIEW-GUIDANCE] {mfu_id}: could not build review_guidance ({_rg_err}); non-fatal."
            )

        # ── #42 surfacing — tell the reviewer when their edit was overridden by grounding ──
        # A targeted edit whose critic-1 grounding failure was then auto-corrected means the
        # reviewer's requested change was NOT applied (the SRS-grounded value was kept instead).
        # Without this the run just reports PASS/No-action, hiding that the feedback was reverted.
        if _edit_grounding_conflict and final_status_val in ("PASS", "PASS_RELAXED_GATES"):
            _note = (
                "Your edit was NOT applied to "
                f"{', '.join(_edit_grounding_conflict)} — it contradicted the SRS (Gate 9a "
                "grounding), so the SRS-grounded value was kept. If your change is correct, the "
                "upstream SRS must be updated first."
            )
            _gm = final_manifest.setdefault("generation_metadata", {})
            _gm["feedback_override"] = {
                "overridden": True,
                "stories": _edit_grounding_conflict,
                "gate": "9a_grounding",
                "reason": _edit_conflict_reason,
                "note": _note,
            }
            _rg = _gm.get("review_guidance")
            if isinstance(_rg, dict):
                _rg["feedback_override_note"] = _note
                # a silently-reverted edit is something the reviewer SHOULD look at
                _rg["recommended_action"] = _rg.get("recommended_action") or "REVIEW"
            _Log.warn(f"[FEEDBACK-OVERRIDE] {mfu_id}: {_note}")

        # ── #42 — audit: record the PM human-feedback that drove this regeneration ──
        _hg = getattr(self, "_human_guidance", None)
        if _hg:
            _gm = final_manifest.setdefault("generation_metadata", {})
            _log = _gm.get("human_feedback_log") or []
            _log.append(
                {
                    "feedback": _hg,
                    "mode": "regenerate",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "result_status": final_status_val,
                }
            )
            _gm["human_feedback_log"] = _log

        _Log.ok(
            f"Stage 5b complete for {mfu_id}: final_status={final_status_val} "
            f"(_run_status={_run_status})"
        )
        return final_manifest
