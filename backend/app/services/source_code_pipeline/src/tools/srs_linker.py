"""SRSPhysicalLinker v3.1 - Pass 1c: Verb+Resource-Hint Search

Fixes faulty LLM-generated API L2 ID slugs where the endpoint path is
incorrectly encoded (wrong separators, missing path segments). Adds:

Pass 1c: verb + resource-hint search — extracts the last meaningful segment
         from the slug (e.g. 'login') and searches all API files for any line
         containing {verb} + /...{resource}... This finds the actual endpoint
         even when the slug-reconstructed path does not exist in the file.
Pass 2 enhancement: after finding the contract section heading, scans section
         content for an actual verb+path line to populate target_string and
         exact_quote instead of the phantom reconstructed path.
"""

import html
import json
import os
from pathlib import Path
import re


def _detect_srs_document_type(filename):
    """Classify an SRS filename into a canonical document-type string.

    Returns one of: ``'UIBlueprint'``, ``'BatchBlueprint'``,
    ``'APIContracts'``, or ``'SRS'``.

    Design notes
    ------------
    - All comparisons are upper-cased; the caller passes any-case filename.
    - ``'API'`` and ``'CONTRACT'`` are matched only when they appear as whole
      tokens (split on ``[\\W_]+``) to prevent false positives on words like
      RAPID, RAPIER, CAPITAL, CONTRACTOR, EXTRACT that contain these substrings.
    - Explicit compound strings (``APISPEC``, ``APICONTRACTS``) are checked
      first and bypass the token split for backwards compatibility.
    - Priority: UIBlueprint > BatchBlueprint > APIContracts > SRS.
    """
    fn = filename.upper()
    # UIBlueprint
    if "UIBLUEPRINT" in fn or "UI_BLUEPRINT" in fn:
        return "UIBlueprint"
    # BatchBlueprint
    if "BATCHSPEC" in fn or ("BATCH" in fn and "BLUEPRINT" in fn):
        return "BatchBlueprint"
    # APIContracts — token-boundary: split on non-word chars to avoid false
    # positives (e.g. 'RAPID', 'RAPIER', 'CAPITAL', 'CONTRACTOR').
    # Compound strings checked first for speed and backwards compatibility.
    _fn_tokens = set(re.split(r"[\W_]+", fn))
    if "APISPEC" in fn or "APICONTRACTS" in fn or "API" in _fn_tokens or "CONTRACT" in _fn_tokens:
        return "APIContracts"
    # Generic fallbacks (less-specific)
    if "BLUEPRINT" in fn:
        return "UIBlueprint"
    if "BATCH" in fn:
        return "BatchBlueprint"
    return "SRS"


def _derive_evidence_role(l2_id):
    u = l2_id.upper()
    if "::S3::" in u:
        return "ui_control"
    if "::S5::EVENT-" in u:
        return "event_trigger"
    if "::API::GET_" in u:
        return "data_contract"
    if any(f"::API::{v}_" in u for v in ("POST", "PUT", "PATCH")):
        return "write_contract"
    if "::API::DELETE_" in u:
        return "write_contract"
    if "::BATCH::STEP-" in u:
        return "batch_step"
    if "::BATCH::PARAM::" in u:
        return "parameter"
    if "::S4::" in u:
        return "validation_rule"
    if "TRCE-" in u:
        return "trace_reference"
    return "business_rule"


def _upd_stack(stack, level, text):
    while stack and stack[-1][0] >= level:
        stack.pop()
    stack.append((level, text))
    return stack


def _stack_path(stack):
    return [t for _, t in stack]


_HR = re.compile(r"^(#{1,6})\s+(.+)$")
_S3_SKIP = frozenset(
    {
        "control",
        "name",
        "id",
        "type",
        "flag",
        "description",
        "i/o",
        "#",
        "note",
        "required",
        "max length",
        "item name (jp)",
        "item name (en)",
        "physical name",
        "control type",
        "visual type",
        "behavioral intent",
        "validation/requirement",
        "input format / mask",
        "min value",
        "max value",
        "regular expression",
        "error message (literal)",
        "trace id",
        "modern field name",
        "legacy control id",
        "event name (jp / en)",
        "event name",
        "event overview",
        "processing type",
        "sync / async",
        "operation type",
        "remarks",
        "item name (jp / en)",
        "attribute",
        "min length",  # Common S.3 cell VALUES (not control names) that leak from value columns
        # such as "Overrides Ancestor? -> Yes/No", "Enabled Condition -> Enabled",
        # "Source Event -> clicked". These are never physical control identifiers in
        # any supported language, so they are denied globally for every consumer.
        "yes",
        "no",
        "true",
        "false",
        "enabled",
        "disabled",
        "clicked",
        "none",
        "n/a",
        "null",
    }
)
_CONTRACT_PAT = {
    "GET": [r"data\s+retrieval", r"\bget\b.*contract"],
    "POST": [r"transactional.*contract", r"post.*put.*contract", r"\bpost\b.*contract"],
    "PUT": [r"transactional.*contract", r"post.*put.*contract", r"\bput\b.*contract"],
    "DELETE": [r"delete.*contract"],
    "PATCH": [r"patch.*contract"],
}
_COUNT_HEADING_RE = re.compile(r"\b[Tt]otal\s+[Cc]ontrols?:?\s*\d+", re.IGNORECASE)
_EVENT_BOLD_RE = re.compile(r"^\*+\s*[Ee]vent\s+#(\d+)\s*[—–\-]", re.UNICODE)
_MODERN_EP_RE = re.compile(r"[Mm]odern\s+[Ee]ndpoint\s+[Rr]oute", re.IGNORECASE)
# Column header synonyms for 'Physical Name' across supported technologies.
# Extend this set when integrating new source languages or SRS templates.
_S3_PHYS_NAME_HEADERS = frozenset(
    {
        # English column header variants
        "physical name",
        "physical_name",
        "physicalname",
        "control name",
        "control_name",
        "controlname",
        "field name",
        "field_name",
        "fieldname",
        "variable name",
        "variable_name",
        "object name",
        "component name",
        "identifier",
        "item name (physical)",
        # COBOL SRS template variants
        "legacy control id",
        "legacy_control_id",
        "legacycontrolid",
        # Japanese column header variants (normalised to lowercase)
        "コントロール名",  # コントロール名 (control name)
        "物理名",  # 物理名 (physical name)
        "フィールド名",  # フィールド名 (field name)
        "変数名",  # 変数名 (variable name)
    }
)

# Semantic section heading aliases for S.3 (Screen/Form Controls).
# Supports SRS templates that use decimal numbering (e.g. '2.3 Screen Specifications')
# instead of the canonical 'S.3' prefix.  Add one string per new template variant.
_S3_SECTION_ALIASES = frozenset(
    {
        "screen specifications",
        "screen controls",
        "form elements",
        "ui elements",
        "display elements",
        "physical controls",
        "screen fields",
    }
)


def _is_s3_heading(text: str) -> bool:
    """Return True if *text* identifies an S.3 (physical controls) section.

    Handles the canonical VB6/PB format ('S.3 Screen Specifications') and the
    COBOL decimal-numbering format ('2.3 Screen Specifications & Form Elements').
    Extend _S3_SECTION_ALIASES for additional SRS template variants.
    """
    if re.search(r"S\.3", text, re.IGNORECASE):
        return True
    t = text.lower()
    if re.search(r"\b2\.3\b", t):
        return True
    return any(alias in t for alias in _S3_SECTION_ALIASES)


# Punctuation that signals prose, assignments, or code statements — never present in a
# bare physical control identifier (e.g. 'This.Title = "x"', 'super::create, sets text').
_PROSE_PUNCT = set("\"'=(),;:?!")

# #32 — identifier-shaped NON-control tokens that leak from S.3 tables (data-type cells,
# method/property calls, function/window/NVO names, event-name cells). These pass a pure
# identifier-shape test but are not physical UI controls. The lists below are the
# PowerBuilder/general DEFAULT; they are externalized here so they can be moved to / merged
# with per-language profiles (language_profiles.yaml) under task #11 — keeping the engine
# language-agnostic. The "contains '.'" rule is universal (no control name contains a dot).
_TYPE_KEYWORDS = {
    "char",
    "character",
    "string",
    "int",
    "integer",
    "uint",
    "long",
    "longlong",
    "ulong",
    "dec",
    "decimal",
    "double",
    "real",
    "number",
    "boolean",
    "bool",
    "date",
    "datetime",
    "time",
    "blob",
    "byte",
    "any",
    "window",
    "structure",
    "powerobject",
    "graphicobject",
}
# Non-control PREFIXES (functions / windows / NVOs / structures). Deliberately excludes
# control prefixes: dw_ st_ cb_ sle_ mle_ em_ rb_ cbx_ ddlb_ ddw_ lb_ gb_ tab_ uo_ p_ pb_
# gr_ hsb_ vsb_ m_ mdi_ menu_  (note "u_" does NOT match "uo_": 2nd char differs).
_NONCTRL_PREFIXES = (
    "w_",
    "wf_",
    "fw_",
    "f_",
    "gf_",
    "uf_",
    "of_",
    "u_",
    "n_",
    "nvo_",
    "gn_",
    "gnv_",
)
# Common PowerBuilder event names that leak from S.5/event columns into the S.3 index.
_EVENT_NAMES = {
    "open",
    "close",
    "clicked",
    "doubleclicked",
    "rbuttondown",
    "rbuttonup",
    "itemchanged",
    "itemfocuschanged",
    "itemerror",
    "modified",
    "getfocus",
    "setfocus",
    "losefocus",
    "lostfocus",
    "gainfocus",
    "activate",
    "deactivate",
    "resize",
    "show",
    "hide",
    "timer",
    "idle",
    "key",
    "constructor",
    "destructor",
    "retrieveend",
    "retrievestart",
    "buttonclicked",
    "selectionchanged",
}


def _is_physical_ctrl_name(name: str, authoritative: bool = False) -> bool:
    """Heuristic fallback filter for S.3 physical control names.
    Used only when the Physical Name column cannot be identified in the S.3 header.
    Primary fix: column-aware indexing via _S3_PHYS_NAME_HEADERS.
    Language coverage: PowerBuilder (_), VB6 (camelCase), COBOL (-), future via extension.

    authoritative=True — caller obtained this token from a COLUMN-AUTHORITATIVE S.3
    table ('#'-led with a detected Physical Name column). Such a cell IS a physical
    control name by construction, so the language-BIASED bare-word prose heuristics
    (all-caps-acronym / starts-uppercase-English-word / long-lowercase) are skipped:
    they exist only for the *fallback* case (no column header) and wrongly reject
    legitimate all-caps identifier conventions (COBOL/BMS, RPG, SQL, C macros).
    Structural rejects (whitespace, prose punctuation, dotted property access, bare
    type/event keywords, non-control prefixes) still apply, so real prose/leakage is
    still filtered. Keeps the engine language-neutral, not GUI-convention-biased.

    Identifier-shape test (language-agnostic, no per-language symbol enumeration):
      • Reject ANY whitespace → prose/table cells span words.
      • Reject prose/code-statement punctuation (=,(),;:?! and quotes).
      • Must start with a letter or underscore (rejects pure numbers / values).
      • A token bearing a SEPARATOR ('_' or '-') with no whitespace is a code identifier,
        so embedded non-ASCII symbols are tolerated — this keeps legitimate CJK/legacy
        controls such as 'm_仮lot№訂正' (the '№' numero sign previously failed a strict
        \\w-only regex). Bare words (no separator) still face the English-word / acronym /
        long-lowercase filters so prose like "DataWindow", "Enabled", "SetWindowPos" drop.
    """
    if not name or len(name) < 2 or len(name) > 40:
        return False
    if any(ch.isspace() for ch in name):
        return False
    # #32 — reject identifier-shaped NON-controls:
    #   • any token containing '.'  → method/property access (dw_x.SetTransObject,
    #     This.TriggerEvent, sle_year.SetFocus). Universal: no control name has a dot.
    #   • bare data-type keyword     → char / datetime / decimal / number / string …
    #   • bare event name            → open / clicked / itemchanged …
    #   • non-control prefix         → w_ (window) / wf_,f_,uf_,of_,u_ (functions) / n_,nvo_
    if "." in name:
        return False
    _low = name.lower()
    if _low in _TYPE_KEYWORDS or _low in _EVENT_NAMES:
        return False
    if any(_low.startswith(_p) for _p in _NONCTRL_PREFIXES):
        return False
    if name[0] != "_" and not name[0].isalpha():
        return False
    if any(ch in _PROSE_PUNCT for ch in name):
        return False
    stripped = name.replace("_", "").replace("-", "").replace(".", "")
    if not stripped or stripped.isdigit():
        return False
    has_sep = "_" in name or "-" in name
    # Bare-word prose heuristics — skipped for column-authoritative cells (see
    # docstring): GUI-convention-biased, they wrongly drop all-caps identifier
    # conventions used by COBOL/BMS, RPG, SQL, etc.
    if not has_sep and not authoritative:
        # Filter: starts-uppercase English word (e.g. "Alignment", "Required", "DataWindow")
        if name[0].isupper() and name.isalpha() and len(name) > 4:
            return False
        # Filter: all-caps acronym (e.g. "IME", "FULL") — >= 3 chars
        if name.isupper() and len(name) >= 3:
            return False
        # Filter: long all-lowercase word (event name like "selectionchanged")
        if name.islower() and len(name) > 10:
            return False
    return True


_CTRL_PROP_RE = re.compile(r"\b([a-z_]\w*)\.(Text|Checked|Value|Tag|String)\b", re.IGNORECASE)
_SLUG_SKIP_RE = re.compile(r"^(api|v\d+|mfu-\d+|mod)$", re.IGNORECASE)


def _extract_resource_hint(slug: str, verb: str) -> str:
    """
    Extracts the last meaningful resource segment from an API L2 slug.
    The resource name is usually encoded faithfully even when the module-path
    prefix is garbled by the LLM.
    Examples:
      POST_api_v1_mod_app-labo-stock_auth_login -> 'login'
      GET_api_v1_mod-frmbatchrun_mfu-001_payroll -> 'payroll'
      POST_api_v1_iraiden_excel -> 'excel'
    """
    prefix = verb + "_"
    path_part = slug[len(prefix) :] if slug.upper().startswith(prefix.upper()) else slug
    segments = path_part.split("_")
    meaningful = [s for s in segments if s and not _SLUG_SKIP_RE.match(s)]
    return meaningful[-1] if meaningful else (segments[-1] if segments else "")


class SRSPhysicalLinker:
    def __init__(self, mfu_dir_path):
        self.mfu_dir_path = Path(mfu_dir_path).resolve()
        self.index = {}
        self.file_cache = {}
        # Gate-7c root cause C: per-control set of files whose S.3 lists the control.
        # Kept OUT of self.index entries so it never leaks into serialized srs_evidence.
        self.s3_file_membership = {}
        self._build_index()

    def _cm(self, text):
        if not text:
            return ""
        c = html.unescape(text)
        c = re.sub(r"<[^>]+>", "", c)
        c = re.sub(r"[*`~\\]", "", c)
        return c.strip()

    def _build_index(self):
        if not self.mfu_dir_path.exists():
            return
        mfu_id = self.mfu_dir_path.name
        for fn in sorted(os.listdir(self.mfu_dir_path)):
            if not fn.endswith(".md"):
                continue
            # Case-insensitive filter: on Linux filenames are case-sensitive;
            # lower-cased SRS files (e.g. 'mfu001_uiblueprint.md') were silently
            # dropped by the original case-sensitive 'k in fn' check.
            fn_upper = fn.upper()
            if not any(
                k in fn_upper for k in ["UI", "API", "BATCH", "BLUEPRINT", "CONTRACT", "SPEC"]
            ):
                continue
            try:
                lines = open(self.mfu_dir_path / fn, encoding="utf-8").readlines()
            except OSError:
                continue
            self.file_cache[fn] = lines
            raw = "".join(lines)
            if self._is_batch(raw):
                self._idx_batch(fn, raw, mfu_id)
                self._idx_ui(fn, lines, mfu_id)
            else:
                self._idx_ui(fn, lines, mfu_id)
                self._idx_api(fn, lines, mfu_id)

    @staticmethod
    def _is_batch(raw):
        """Return True if *any* JSON block in *raw* contains a 'job_steps' key.

        The previous implementation only inspected the *first* fenced JSON block.
        BatchBlueprint files with a JSON metadata preamble before the ``job_steps``
        section were misclassified as UIBlueprint.  This implementation scans
        *all* fenced blocks and falls back to the full raw string.
        """
        for m in re.finditer(r"```json\s*(\{.*?})\s*```", raw, re.DOTALL):
            try:
                if "job_steps" in json.loads(m.group(1)):
                    return True
            except Exception:
                continue
        # No fenced block matched — attempt the full raw string as a last resort
        raw_stripped = raw.strip()
        if raw_stripped.startswith("{"):
            try:
                return "job_steps" in json.loads(raw_stripped)
            except Exception:
                pass
        return False

    def _idx_batch(self, fn, raw, mfu_id):
        m = re.search(r"```json\s*(\{.*?})\s*```", raw, re.DOTALL)
        js = m.group(1) if m else raw.strip()
        try:
            data = json.loads(js)
        except:
            return
        steps = data.get("job_steps", [])
        if not isinstance(steps, list):
            return
        for step in steps:
            n = step.get("step_number", 0)
            nm = step.get("step_name", f"Step {n}")
            d = step.get("description", "")
            br = step.get("business_rules", []) or []
            tk = step.get("traceability_keys", []) or []
            seq = str(n).zfill(3)
            anc = f"Step {n}: {nm}"
            rich = f"Step {n}: {nm} - {d[:80].rstrip()}" if d else anc
            sp = ["BatchSpec", anc]
            eq = d[:200] if d else nm
            ft = tk[0] if tk else ""
            for sfx in (f"STEP-{seq}", f"STEP-{n}"):
                for sec in ("BATCH", "S5"):
                    self._put(
                        f"SRS::{mfu_id}::{sec}::{sfx}",
                        fn,
                        rich,
                        "section",
                        nm,
                        sp,
                        eq,
                        ft,
                        "exact",
                        0,
                    )
            self._put(
                f"SRS::{mfu_id}::S5::EVENT-{seq}", fn, rich, "section", nm, sp, eq, ft, "exact", 0
            )
            for t in tk:
                if not t:
                    continue
                self._put(f"SRS::{mfu_id}::S5::{t}", fn, anc, "section", t, sp, eq, t, "exact", 0)
                self._put(t, fn, anc, "section", t, sp, eq, t, "exact", 0)
            for rule in br:
                rt = rule.get("traceability_key", "")
                rn = rule.get("rule_name", nm)
                if rt:
                    ra = f"Step {n}: {nm} - {rn}"
                    rp = sp + [rn]
                    rq = rule.get("description", rn)[:200]
                    self._put(
                        f"SRS::{mfu_id}::S5::{rt}", fn, ra, "table_row", rn, rp, rq, rt, "exact", 0
                    )
                    self._put(rt, fn, ra, "table_row", rt, rp, rq, rt, "exact", 0)
        for pm in re.finditer(r"\bPARM[-_][A-Z0-9_-]+", js):
            pn = pm.group(0)
            l2 = f"SRS::{mfu_id}::BATCH::PARAM::{pn}"
            if l2.lower() not in self.index:
                self._put(
                    l2,
                    fn,
                    f"Parameter: {pn}",
                    "table_row",
                    pn,
                    ["BatchSpec", "Parameters"],
                    pn,
                    "",
                    "exact",
                    0,
                )

    def _idx_ui(self, fn, lines, mfu_id):
        s3 = False
        s3p = []
        s3_stable = "S.3"
        in_s4 = False
        s4_heading = "S.4"
        s4_row = 0
        s4_phys_col = -1
        in_s5 = False
        s5_heading = "S.5"
        s5_op_type_col = -1  # column index of "Operation Type" in current S.5 table
        stk = []
        mfu_seq_m = re.match(r"^MFU-(\d+)", mfu_id.upper())
        mfu_seq = mfu_seq_m.group(1).zfill(3) if mfu_seq_m else "001"
        for i, line in enumerate(lines):
            s = line.rstrip("\n").rstrip()
            ln = i + 1
            h = _HR.match(s)
            if h:
                lv = len(h.group(1))
                ht = self._cm(h.group(2))
                _upd_stack(stk, lv, ht)
                if re.search(r"S\.5", ht, re.IGNORECASE):
                    in_s5 = True
                    s5_heading = ht
                    in_s4 = False
                    s5_op_type_col = -1  # reset column index for each new S.5 section
                elif re.search(r"S\.4", ht, re.IGNORECASE):
                    in_s4 = True
                    s4_heading = ht
                    s4_row = 0
                    s4_phys_col = -1
                    in_s5 = False
                    s3 = False
                elif lv <= 2 and in_s5:
                    in_s5 = False
                elif lv <= 2 and in_s4:
                    in_s4 = False
            # ── Plain-text S.N section markers (no # prefix) ─────────────────
            # All UIBlueprint templates use bare "S.5 Event / Operational
            # Specification" lines (no leading #), so _HR.match() never fires
            # for them.  Detect them explicitly here.  Only active when the
            # line is NOT a markdown heading (h is None) to avoid double-firing.
            if not h:
                if re.match(r"^S\.5\b", s):
                    in_s5 = True
                    s5_heading = s.strip()
                    in_s4 = False
                    s5_op_type_col = -1
                elif re.match(r"^S\.4\b", s):
                    in_s4 = True
                    s4_heading = s.strip()
                    s4_row = 0
                    s4_phys_col = -1
                    in_s5 = False
                    s3 = False
                    s5_op_type_col = -1
                elif re.match(r"^S\.[123]\b", s):
                    # Defensive: earlier sections should never follow S.5,
                    # but reset if encountered (e.g. multi-file concatenation).
                    in_s5 = False
                    in_s4 = False
            ev = re.search(r"S\.5\s*:?\s*(?:Event|Operation)?\s*#?(\d+)", s, re.IGNORECASE)
            if ev:
                num = str(int(ev.group(1))).zfill(3)
                l2 = f"SRS::{mfu_id}::S5::EVENT-{num}"
                anc = self._cm(s.lstrip("#").strip())
                self._put(l2, fn, anc, "section", anc, _stack_path(stk), s, "", "exact", ln)
                continue
            bd = _EVENT_BOLD_RE.match(s)
            if bd:
                num = str(int(bd.group(1))).zfill(3)
                l2 = f"SRS::{mfu_id}::S5::EVENT-{num}"
                anc = self._cm(s.strip("*").strip())
                cur_h = stk[-1][1] if stk else s5_heading
                self._put(l2, fn, cur_h, "section", anc[:80], _stack_path(stk), s, "", "exact", ln)
            if in_s5 and "|" in s and not re.match(r"^[\s|:-]+$", s):
                cols = [c.strip() for c in s.split("|") if c.strip()]
                # Header row detection: first column is '#', scan for "Operation Type" column.
                # This is robust across SRS templates with varying column counts/orders.
                if cols and cols[0] == "#":
                    for _ci, _ch in enumerate(cols):
                        if self._cm(_ch).lower() == "operation type":
                            s5_op_type_col = _ci
                            break
                elif cols and cols[0].isdigit():
                    row_num = str(int(cols[0])).zfill(3)
                    raw_label = cols[1] if len(cols) > 1 else f"Event {row_num}"
                    label = self._cm(raw_label)[:80]
                    # Extract Operation Type from detected column; "" if column absent or not yet found.
                    op_type = (
                        cols[s5_op_type_col].strip()
                        if s5_op_type_col >= 0 and len(cols) > s5_op_type_col
                        else ""
                    )
                    if label and label.lower() not in _S3_SKIP:
                        l2 = f"SRS::{mfu_id}::S5::EVENT-{row_num}"
                        cur_h = stk[-1][1] if stk else s5_heading
                        self._put(
                            l2,
                            fn,
                            cur_h,
                            "table_row",
                            label,
                            _stack_path(stk),
                            s,
                            "",
                            "exact",
                            ln,
                            operation_type=op_type,
                        )
            if in_s4 and "|" in s and not re.match(r"^[\s|:-]+$", s):
                cols = [c.strip() for c in s.split("|") if c.strip()]
                if cols and cols[0] == "#":
                    # Header row: detect Physical Name column (supports VB6 and PowerBuilder layouts)
                    for _ci, _ch in enumerate(cols):
                        if self._cm(_ch).lower() in _S3_PHYS_NAME_HEADERS:
                            s4_phys_col = _ci
                            break
                elif cols and cols[0].isdigit() and len(cols) >= 3:
                    s4_row += 1
                    # Use detected column; fall back to col[2] (PowerBuilder default)
                    _phys_idx = s4_phys_col if s4_phys_col >= 0 and s4_phys_col < len(cols) else 2
                    phys_raw = self._cm(cols[_phys_idx])
                    phys_clean = re.split(r"\s+\(", phys_raw)[0].strip()
                    row_z2 = str(int(cols[0])).zfill(2)
                    val_id = f"VAL-{mfu_seq}-{row_z2}"
                    cur_h = stk[-1][1] if stk else s4_heading
                    sp4 = _stack_path(stk)
                    self._put(
                        f"SRS::{mfu_id}::S4::{val_id}",
                        fn,
                        cur_h,
                        "table_row",
                        phys_clean,
                        sp4,
                        s,
                        "",
                        "exact",
                        ln,
                    )
                    if phys_clean and len(phys_clean) < 50:
                        self._put(
                            f"SRS::{mfu_id}::S4::{phys_clean}",
                            fn,
                            cur_h,
                            "table_row",
                            phys_clean,
                            sp4,
                            s,
                            "",
                            "exact",
                            ln,
                        )
            if _is_s3_heading(s) and not in_s4 and not in_s5:
                s3 = True
                s3p = _stack_path(stk)
                s3_stable = stk[-1][1] if stk else "S.3"
                s3_phys_col = -1  # reset column index for each new S.3 section
            elif re.search(r"S\.[4-9]", s, re.IGNORECASE):
                s3 = False
            if s3 and "|" in s:
                if re.match(r"^[\s|:-]+$", s):
                    continue  # separator row
                cols = [c.strip() for c in s.split("|") if c.strip()]
                cur = stk[-1][1] if stk else "S.3"
                if _COUNT_HEADING_RE.search(cur):
                    cur = s3_stable
                # Column-aware S.3 indexing.
                # Header row: first column is '#' across all supported SRS templates.
                # Detect 'Physical Name' (or synonym) column; data rows use only that column.
                first = self._cm(cols[0]) if cols else ""
                if first == "#":
                    for j, col in enumerate(cols):
                        if self._cm(col).lower() in _S3_PHYS_NAME_HEADERS:
                            s3_phys_col = j
                            break
                    continue  # never index the header row
                # Data row
                if 0 <= s3_phys_col < len(cols):
                    # PRIMARY: index only the verbatim Physical Name cell.
                    # Normalise: strip enclosing parentheses that some SRS authors
                    # wrap around control names — e.g. "(txtUsername)" → "txtUsername".
                    # Parentheses are invalid in the L2 ID regex [\w.-]+ and cause
                    # Gate 1c / Gate 7d critic failures that cannot be corrected by the LLM.
                    cl_raw = self._cm(cols[s3_phys_col])
                    cl = re.sub(r"\s*\([^)]*\)", "", cl_raw).strip() if cl_raw else cl_raw
                    # P4-primary: recover all-parens S3 name.
                    # re.sub strips enclosing parens from e.g. "(txtSalary)" → "".
                    # Fall back to outer-parens-stripped original so the control
                    # name is preserved rather than silently dropped.
                    if cl_raw and not cl:
                        cl = cl_raw.strip("() ")
                    if cl and cl.lower() not in _S3_SKIP and 1 < len(cl) < 50:
                        self._put(
                            f"SRS::{mfu_id}::S3::{cl}",
                            fn,
                            cur,
                            "table_row",
                            cl,
                            s3p or _stack_path(stk),
                            s,
                            "",
                            "exact",
                            ln,
                        )
                else:
                    # FALLBACK SUPPRESSED (Gate 7c root-cause fix).
                    # When the Physical-Name column cannot be detected, the row belongs
                    # to a prose / summary sub-table (e.g. '2.3 Screen Specifications &
                    # Form Elements', Hidden-Controls, event tables). Indexing every
                    # passing cell there over-captured event names, instance variables,
                    # and function names as if they were physical controls — the direct
                    # cause of the bounding_box.controls[] pollution and Gate 7c failures.
                    #
                    # The canonical S.3 'Item Specification Table' (with its '#'-led
                    # Physical Name column) is the authoritative, language-agnostic
                    # source of physical controls and is captured by the column-detected
                    # path above. Prose sub-tables are redundant summaries, so we no
                    # longer index them heuristically.
                    pass

    def _idx_api(self, fn, lines, mfu_id):
        stk = []
        for i, line in enumerate(lines):
            s = line.rstrip("\n").rstrip()
            ln = i + 1
            h = _HR.match(s)
            if h:
                lv = len(h.group(1))
                ht = self._cm(h.group(2))
                _upd_stack(stk, lv, ht)
            for tm in re.finditer(r"TRCE[-][A-Z0-9_-]+", s, re.IGNORECASE):
                key = tm.group(0)
                anc = stk[-1][1] if stk else "API Contracts"
                sp = _stack_path(stk)
                eq = self._cm(s)
                ht2 = "table_row" if "|" in s else "paragraph"
                cm = _CTRL_PROP_RE.search(s)
                sc = cm.group(1).lower() if cm else ""
                if eq and len(eq) < 300:
                    self._put(
                        f"SRS::{mfu_id}::S5::{key}",
                        fn,
                        anc,
                        ht2,
                        key,
                        sp,
                        eq,
                        key,
                        "exact",
                        ln,
                        source_control=sc,
                    )
                    self._put(key, fn, anc, ht2, key, sp, eq, key, "exact", ln, source_control=sc)

    def _put(
        self,
        l2_id,
        filename,
        anchor,
        highlight_type,
        target_string,
        section_path=None,
        exact_quote="",
        trace_id="",
        precision="exact",
        line_number=0,
        source_control="",
        operation_type="",
    ):
        key = l2_id.lower()
        # Gate-7c root cause C: the SAME physical control can appear in the S.3 table of
        # MULTIPLE windows in one MFU (focal window + its child/popup). _put is first-wins
        # and files parse in alphabetical order, so a shared control (e.g. cb_close, dw_data)
        # would otherwise be attributed ONLY to whichever file sorts first (often a child
        # window), and the focal screen's "controls from focal" query would miss it — the
        # direct cause of the unfixable Gate-7c deadlock (bounding_box missing controls the
        # critic sees in the focal S.3). Track EVERY file whose S.3 lists this control in a
        # SEPARATE membership map. IMPORTANT: this must NOT live inside the index entry —
        # index entries are copied verbatim into story srs_evidence, which is JSON-serialised
        # and schema-validated (additionalProperties:false), so an extra set/list field there
        # breaks the write AND silently breaks json.dumps()-based size estimation elsewhere.
        # The canonical first-wins entry (file_name/line_number) is preserved for L2 evidence
        # resolution; screen-membership queries read self.s3_file_membership.
        if "::s3::" in key and filename:
            self.s3_file_membership.setdefault(key, set()).add(filename)
        if key not in self.index:
            self.index[key] = {
                "l2_id": l2_id,
                "file_name": filename,
                "section_anchor": anchor,
                "section_path": section_path or [],
                "highlight_type": highlight_type,
                "target_string": target_string,
                "exact_quote": exact_quote,
                "trace_id": trace_id,
                "precision": precision,
                "line_number": line_number,
                "_source_control": source_control,
                "operation_type": operation_type,
            }
        elif operation_type and not self.index[key].get("operation_type"):
            # Back-fill: section-heading entries are indexed before their table row.
            # When the table row is parsed later (carrying the Operation Type column),
            # update the existing entry rather than silently discarding the value.
            self.index[key]["operation_type"] = operation_type

    def _slug_to_path(self, slug, method):
        pfx = method + "_"
        part = slug[len(pfx) :] if slug.upper().startswith(pfx.upper()) else slug
        return "/" + part.replace("_", "/")

    def _scan_section_for_verb(self, lines, heading_idx, verb, max_lines=40):
        """
        Scans section content (lines after a heading) for an actual HTTP verb+path line.
        Used by Pass 2 as a belt-and-suspenders upgrade when the slug-reconstructed path
        doesn't exist in the file. Returns (target_string, exact_quote, line_number).
        """
        _ep_re = re.compile(rf'\b{re.escape(verb)}\b\s+(/[^\s`\){{,;\'"]+)', re.IGNORECASE)
        for j in range(heading_idx + 1, min(heading_idx + max_lines, len(lines))):
            s = lines[j].rstrip("\n").rstrip()
            if re.match(r"^#{1,3}\s", s):
                break
            em = _ep_re.search(s)
            if em:
                path = em.group(1).rstrip("`").rstrip("'").rstrip()
                return f"{verb} {path}", self._cm(s)[:300], j + 1
        return None, None, 0

    def _resolve_api(self, l2_id):
        seg = l2_id.split("::")
        if len(seg) < 4 or seg[2].upper() != "API":
            return None
        slug = seg[3]
        m = re.match(r"^(POST|GET|PUT|DELETE|PATCH|HEAD|OPTIONS)", slug, re.IGNORECASE)
        verb = m.group(1).upper() if m else None
        if not verb:
            return None
        ep = self._slug_to_path(slug, verb)

        # Pass 1: endpoint path verbatim search
        for fn, lines in self.file_cache.items():
            if not any(k in fn.upper() for k in ("API", "CONTRACT", "SPEC")):
                continue
            stk = []
            for i, line in enumerate(lines):
                s = line.rstrip("\n").rstrip()
                h = _HR.match(s)
                if h:
                    _upd_stack(stk, len(h.group(1)), self._cm(h.group(2)))
                if ep.lower() in s.lower():
                    anc = stk[-1][1] if stk else "API Contracts"
                    sp = _stack_path(stk)
                    eq = self._cm(s)
                    ht = (
                        "bullet_item"
                        if s.lstrip().startswith(("-", "*"))
                        else ("table_row" if "|" in s else "paragraph")
                    )
                    tgt = f"{verb} {ep}"
                    em = re.search(rf"\b{re.escape(verb)}\b\s+(/[^\s,\)`{{]+)", s, re.IGNORECASE)
                    if em:
                        tgt = f"{verb} {em.group(1)}"
                    return {
                        "l2_id": l2_id,
                        "file_name": fn,
                        "section_anchor": anc,
                        "section_path": sp,
                        "highlight_type": ht,
                        "target_string": tgt,
                        "exact_quote": eq[:300],
                        "trace_id": "",
                        "precision": "section",
                        "line_number": i + 1,
                    }

        # Pass 1b: Modern Endpoint Route line
        for fn, lines in self.file_cache.items():
            if not any(k in fn.upper() for k in ("API", "CONTRACT", "SPEC")):
                continue
            stk = []
            for i, line in enumerate(lines):
                s = line.rstrip("\n").rstrip()
                h = _HR.match(s)
                if h:
                    _upd_stack(stk, len(h.group(1)), self._cm(h.group(2)))
                if _MODERN_EP_RE.search(s) and re.search(
                    rf"\b{re.escape(verb)}\b", s, re.IGNORECASE
                ):
                    anc = stk[-1][1] if stk else "API Contracts"
                    sp = _stack_path(stk)
                    eq = self._cm(s)
                    em = re.search(rf"\b{re.escape(verb)}\b\s+(/[^\s`\){{]+)", s, re.IGNORECASE)
                    tgt = f"{verb} {em.group(1)}" if em else f"{verb} {ep}"
                    return {
                        "l2_id": l2_id,
                        "file_name": fn,
                        "section_anchor": anc,
                        "section_path": sp,
                        "highlight_type": "bullet_item",
                        "target_string": tgt,
                        "exact_quote": eq[:300],
                        "trace_id": "",
                        "precision": "section",
                        "line_number": i + 1,
                    }

        # Pass 1c: verb + resource-hint search (handles faulty LLM slug encoding)
        # The resource name (last meaningful slug segment) is almost always encoded
        # faithfully even when the module-path prefix is garbled. Search all API
        # files for any line containing {verb} + /...{resource}...
        resource = _extract_resource_hint(slug, verb)
        if resource:
            _vr = re.compile(
                rf'\b{re.escape(verb)}\b\s+(/[^\s`\){{,;\'"]+{re.escape(resource)}[^\s`\){{,;\'"]*)',
                re.IGNORECASE,
            )
            for fn, lines in self.file_cache.items():
                if not any(k in fn.upper() for k in ("API", "CONTRACT", "SPEC")):
                    continue
                stk = []
                for i, line in enumerate(lines):
                    s = line.rstrip("\n").rstrip()
                    h = _HR.match(s)
                    if h:
                        _upd_stack(stk, len(h.group(1)), self._cm(h.group(2)))
                    em = _vr.search(s)
                    if em:
                        path = em.group(1).rstrip("`").rstrip("'").rstrip()
                        tgt = f"{verb} {path}"
                        eq = self._cm(s)
                        anc = stk[-1][1] if stk else "API Contracts"
                        sp = _stack_path(stk)
                        ht = (
                            "bullet_item"
                            if s.lstrip().startswith(("-", "*"))
                            else ("table_row" if "|" in s else "paragraph")
                        )
                        return {
                            "l2_id": l2_id,
                            "file_name": fn,
                            "section_anchor": anc,
                            "section_path": sp,
                            "highlight_type": ht,
                            "target_string": tgt,
                            "exact_quote": eq[:300],
                            "trace_id": "",
                            "precision": "section",
                            "line_number": i + 1,
                        }

        # Pass 2: contract section heading + section content scan
        pats = _CONTRACT_PAT.get(verb, [])
        if pats:
            for fn, lines in self.file_cache.items():
                if not any(k in fn.upper() for k in ("API", "CONTRACT", "SPEC")):
                    continue
                stk = []
                for i, line in enumerate(lines):
                    s = line.rstrip("\n").rstrip()
                    h = _HR.match(s)
                    if h:
                        lv = len(h.group(1))
                        ht = self._cm(h.group(2))
                        _upd_stack(stk, lv, ht)
                        if any(re.search(p, ht, re.IGNORECASE) for p in pats):
                            tgt, eq, aln = self._scan_section_for_verb(lines, i, verb)
                            return {
                                "l2_id": l2_id,
                                "file_name": fn,
                                "section_anchor": ht,
                                "section_path": _stack_path(stk),
                                "highlight_type": "section",
                                "target_string": tgt or f"{verb} {ep}",
                                "exact_quote": eq or "",
                                "trace_id": "",
                                "precision": "section",
                                "line_number": aln or i + 1,
                            }

        # Pass 3: first API file
        for fn in self.file_cache:
            if any(k in fn.upper() for k in ("API", "CONTRACT", "SPEC")):
                return {
                    "l2_id": l2_id,
                    "file_name": fn,
                    "section_anchor": "API Contracts",
                    "section_path": [],
                    "highlight_type": "section",
                    "target_string": slug,
                    "exact_quote": "",
                    "trace_id": "",
                    "precision": "fuzzy",
                    "line_number": 0,
                }
        return None

    def _best_file(self):
        for p in (
            "UIBlueprint",
            "uiblueprint",
            "BatchSpec",
            "batchspec",
            "Batch",
            "batch",
            "APISpec",
            "apispec",
            "APIContracts",
            "API",
            "Contract",
            "Blueprint",
        ):
            for fn in self.file_cache:
                if p.lower() in fn.lower():
                    return fn
        return sorted(self.file_cache.keys())[0] if self.file_cache else "NOT_FOUND"

    def _computed(self, e):
        e.setdefault("evidence_role", _derive_evidence_role(e.get("l2_id", "")))
        e.setdefault("srs_document_type", _detect_srs_document_type(e.get("file_name", "")))
        e.setdefault("section_path", [])
        e.setdefault("exact_quote", "")
        e.setdefault("trace_id", "")
        e.setdefault("precision", "section")
        e.setdefault("line_number", 0)
        e.setdefault("ac_ids", [])
        e.setdefault("context_snippet", "")
        if "cross_ref" not in e:
            sc = e.pop("_source_control", "")
            parts = e.get("l2_id", "").split("::")
            mfu_seg = parts[1] if len(parts) >= 2 and parts[0] == "SRS" else self.mfu_dir_path.name
            if sc and mfu_seg:
                s3_key = f"srs::{mfu_seg}::s3::{sc}".lower()
                s3 = self.index.get(s3_key)
                e["cross_ref"] = (
                    {
                        "l2_id": s3["l2_id"],
                        "file_name": s3["file_name"],
                        "section_anchor": s3.get("section_anchor", ""),
                        "target_string": s3.get("target_string", ""),
                        "line_number": s3.get("line_number", 0),
                    }
                    if s3
                    else None
                )
        return e

    def _extract_context_snippet(self, l2_id: str, file_name: str) -> str:
        """
        For precision=file fallbacks: scan the SRS file for the control/field name
        embedded in l2_id and return the nearest section heading + first content line
        as a short human-readable snippet.  Returns "" when nothing useful is found.
        """
        lines = self.file_cache.get(file_name)
        if not lines:
            return ""
        # Build search tokens from the last two l2_id segments (e.g. "txtSalary", "VAL-001-03")
        segments = [seg for seg in l2_id.split("::") if seg and seg.upper() not in ("SRS",)]
        target = segments[-1] if segments else l2_id
        # Normalise: collapse separators, lowercase for matching
        target_norm = re.sub(r"[-_\s]+", " ", target).lower().strip()
        # Also try the raw lowercase token for camelCase controls (txtSalary → txtsalary)
        target_raw = target.lower()

        cur_heading = ""
        cur_heading_ln = 0
        best_heading = ""
        best_snippet = ""
        para_after = []
        in_match_section = False

        for i, raw_line in enumerate(lines):
            s = raw_line.rstrip("\n").rstrip()
            hm = _HR.match(s)
            if hm:
                # Flush previous section candidate
                if in_match_section and para_after:
                    best_snippet = " ".join(para_after[:3])
                    break
                cur_heading = self._cm(hm.group(2))
                cur_heading_ln = i + 1
                in_match_section = False
                para_after = []
                continue
            # Check if this line references the target
            s_norm = re.sub(r"[-_\s]+", " ", s).lower()
            if target_raw in s.lower() or (target_norm and target_norm in s_norm):
                if not in_match_section:
                    best_heading = cur_heading
                    in_match_section = True
                if s.strip() and not s.strip().startswith("|") and not re.match(r"^[\s|:-]+$", s):
                    para_after.append(self._cm(s)[:120])
            elif in_match_section and s.strip() and not s.strip().startswith("|"):
                para_after.append(self._cm(s)[:120])
                if len(para_after) >= 3:
                    break

        if in_match_section and para_after:
            best_snippet = " ".join(para_after[:3])

        if not best_heading and not best_snippet:
            return ""
        if best_heading and best_snippet:
            return f"{best_heading} — {best_snippet[:220]}"
        return (best_heading or best_snippet)[:220]

    def resolve_l2_id(self, l2_id: str) -> dict:
        """
        Public API: resolve an L2 reference to an evidence dict.

        Resolution order:
          1. Direct index hit  -- S5::EVENT-*, BATCH::STEP-*, S4::*, etc.
          2. API delegate      -- _resolve_api() for ::API:: refs
          3. File-level fallback -- best matching file, precision='file'

        Always returns a dict (never None) so callers can safely access
        fields like file_name, section_anchor, target_string, etc.
        _computed() is called on every returned entry to populate
        derived fields (evidence_role, srs_document_type, cross_ref, ac_ids).
        """
        key = l2_id.lower()

        # 1. Direct index hit
        entry = self.index.get(key)
        if entry:
            return self._computed(dict(entry))  # copy before mutating

        # 2. API delegate
        if "::api::" in key:
            resolved = self._resolve_api(l2_id)
            if resolved:
                return self._computed(resolved)

        # 3. File-level fallback -- unresolvable ref; return a navigable stub
        best = self._best_file()
        # _best_file() returns a str (file_cache key), not a Path — use directly.
        best_str = best if isinstance(best, str) else (best.name if best else "")
        snippet = self._extract_context_snippet(l2_id, best_str)
        fallback = {
            "l2_id": l2_id,
            "file_name": best_str,
            "section_anchor": "",
            "section_path": [],
            "highlight_type": "file",
            "target_string": l2_id,
            "exact_quote": "",
            "trace_id": "",
            "precision": "file",
            "line_number": 0,
            "operation_type": "",
            "evidence_role": _derive_evidence_role(l2_id),
            "srs_document_type": _detect_srs_document_type(best_str),
            "ac_ids": [],
            "context_snippet": snippet,
        }
        return fallback
