# src/scanner/pb_plugin.py

"""
PowerBuilder Plugin (Production-Grade)
Refactored to comply with the unified LanguagePluginBase contract.
Implements the 'AST Facade' pattern to map legacy Regex logic into
the Enterprise Hybrid Parser's Masking and Fallback stages.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from .plugin_base import (
    ArtifactAnalysisResult,
    EdgeDict,
    LandmineDict,
    LanguagePluginBase,
    MaskedRegionDict,
    MetricsDict,
    PluginManifest,
)

# ---------------- Tier 4 — DataWindow block classification ----------------
# SRS-relevant top-level blocks (keep in full):
#   datawindow  — header properties, update settings, SQL retrieval args
#   table       — embedded SELECT statement, table/column bindings
#   column      — column definitions, edit masks, validation rules
#   compute     — computed field expressions
#   group       — grouping expressions and break logic
#
# Layout-only blocks (structural summary — skip body, just count):
#   band        — visual band definitions (header, detail, footer)
#   text        — static text labels
#   line        — decorative lines
#   rectangle   — decorative rectangles
#   bitmap      — embedded images
#   button      — push buttons (non-functional in migration context)
#   htmltable   — HTML rendering hints
#   report      — nested report references
#   ole         — OLE control placeholders
#   graph       — graph/chart visual widgets
_PB_DW_KEEP_BLOCKS: frozenset = frozenset({"datawindow", "table", "column", "compute", "group"})
_PB_DW_SKIP_BLOCKS: frozenset = frozenset(
    {"band", "text", "line", "rectangle", "bitmap", "button", "htmltable", "report", "ole", "graph"}
)
_PB_DW_ALL_KEYWORDS: frozenset = _PB_DW_KEEP_BLOCKS | _PB_DW_SKIP_BLOCKS

# Regex: matches any recognised block keyword at the start of a line
# (after optional whitespace), followed by '('. Sorted longest-first so
# alternation is unambiguous (e.g. 'datawindow' before 'data').
_PB_DW_BLOCK_RE = re.compile(
    r"^\s*(" + "|".join(sorted(_PB_DW_ALL_KEYWORDS, key=len, reverse=True)) + r")\s*\(",
    re.IGNORECASE,
)

# Regex: matches PowerBuilder script block openers / closers
_PB_SCRIPT_HIGH_OPEN_RE = re.compile(
    r"^\s*(?:forward|global\s+type|type\s+variables|global\s+variables|shared\s+variables)\b",
    re.IGNORECASE,
)
_PB_SCRIPT_HIGH_CLOSE_RE = re.compile(
    r"^\s*(?:end\s+type|end\s+forward|end\s+variables)\b",
    re.IGNORECASE,
)
_PB_SCRIPT_MED_OPEN_RE = re.compile(
    r"^\s*(?:on\s+\S+|event\s+\S+|function\s+|subroutine\s+)",
    re.IGNORECASE,
)
_PB_SCRIPT_MED_CLOSE_RE = re.compile(
    r"^\s*(?:end\s+on|end\s+event|end\s+function|end\s+subroutine)\b",
    re.IGNORECASE,
)

# ---------------- Regex patterns ----------------
EXEC_SQL_REGEX = re.compile(r"\bexecute\s+immediate\b", re.IGNORECASE)
SELECT_INTO_REGEX = re.compile(r"\bselect\b.+?\binto\b", re.IGNORECASE | re.DOTALL)
USING_SQLCA_REGEX = re.compile(r"\busing\s+sqlca\b", re.IGNORECASE)
SQLCA_REGEX = re.compile(r"\bsqlca\b", re.IGNORECASE)

DYNAMIC_SQL_REGEX = re.compile(
    r"\b(execute\s+immediate|setsqlselect|modify\s*\(\s*['\"].*?select)\b",
    re.IGNORECASE | re.DOTALL,
)

TRANSACTION_REGEX = re.compile(
    r"\b(commit|rollback|settransobject|disconnect|f_commit|f_rollback)\b", re.IGNORECASE
)

SQL_COMPLEX_KEYWORDS = re.compile(
    r"\b(UNION|JOIN|HAVING|CURSOR|PROCEDURE|GROUP\s+BY|EXEC|EXECUTE)\b", re.IGNORECASE
)

FUNCTION_REGEX = re.compile(r"\bfunction\b", re.IGNORECASE)
EVENT_REGEX = re.compile(r"\bevent\b", re.IGNORECASE)

# ---------------- DataWindow User Object Detection ----------------
# Used by _refine_sru_archetype() to distinguish .sru subtypes:
#   u_  prefix → Non-Visual Object (NVO): pure business logic → shared_logic
#   uo_ prefix → DataWindow User Object: visual UI widget   → ui_anchor

# Known PowerBuilder DataWindow base classes. An .sru that inherits from
# any of these is definitively a visual DataWindow wrapper, not an NVO.
_DW_BASE_CLASSES: frozenset = frozenset(
    [
        "datawindow",
        "datastore",
        "datastoreobject",
        "u_dw",
        "uo_dw",
        "u_datawindow",
        "u_dw_base",
        "userobject",  # generic visual user object base
    ]
)

# DataWindow-specific UI event / method signatures that NVOs never contain.
# ≥ 2 matches is treated as conclusive evidence of a visual DataWindow object.
_DW_UI_EVENT_REGEX = re.compile(
    r"\b(rbuttondown|u_sort|setsort\s*\(|selectrow\s*\(|setredraw\s*\(|"
    r"lookupdisplay|getitemstring\s*\(|getitemdecimal\s*\(|getitemdate\s*\(|"
    r"getitemdatetime\s*\(|getitemnumber\s*\(|getitemtime\s*\(|"
    r"rowfocuschanged|itemchanged|itemerror|accepttext\s*\(|"
    r"doubleclicked|clicked)\b",
    re.IGNORECASE,
)

OBJECT_DECL_REGEX = re.compile(
    r"""
    (?:global\s+type|type)      # 'global type' or 'type'
    \s+
    (?P<id>[a-zA-Z_][a-zA-Z0-9_]*)   # artifact id
    \s+from\b
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)

CREATE_REGEX = re.compile(r"\bcreate(?:\s+using)?\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)
DW_TOKEN_REGEX = re.compile(r"\b(?:ddw_|ddl_|dw_|d_)([a-zA-Z0-9_]+)\b", re.IGNORECASE)
DATAOBJECT_PROPERTY_REGEX = re.compile(
    r"\bDataObject\s*(?:=|:)\s*['\"]?([a-zA-Z0-9_]+)['\"]?", re.IGNORECASE
)
MODIFY_DATAOBJECT_REGEX = re.compile(
    r"Modify\s*\(\s*['\"].*?DataObject\s*=\s*([a-zA-Z0-9_]+).*?['\"]\s*\)",
    re.IGNORECASE | re.DOTALL,
)
QUOTED_TOKEN_REGEX = re.compile(r"['\"](?P<str>[a-zA-Z0-9_]+)['\"]")

OPEN_REGEX = re.compile(
    r"\bOpen\s*\(\s*(?:['\"])?([a-zA-Z_][a-zA-Z0-9_]*)['\"]?\s*(?:,|\))", re.IGNORECASE
)
OPEN_PARM_REGEX = re.compile(
    r"\bOpenWithParm\s*\(\s*(?:['\"])?([a-zA-Z_][a-zA-Z0-9_]*)['\"]?\s*,", re.IGNORECASE
)
OPEN_SHEET_REGEX = re.compile(
    r"\bOpenSheet\s*\(\s*(?:['\"])?([a-zA-Z_][a-zA-Z0-9_]*)['\"]?\s*,", re.IGNORECASE
)

MEMBER_CALL_REGEX = re.compile(
    r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\.\s*"
    r"(OpenWithParm|OpenSheet|Open|TriggerEvent|PostEvent|CloseWithReturn)\s*\(\s*(?:['\"])?([a-zA-Z_][a-zA-Z0-9_]*)?",
    re.IGNORECASE,
)

EXTERNAL_CODE_REGEX = re.compile(
    r"\b("
    r"library|subroutine|alias\s+for|"
    r"run|shellexecute|"
    r"httpclient|httprequest|webserviceproxy|inet|ftp|socket|"
    r"oleobject|connecttonewobject|connecttoobject|connecttoremoteobject|"
    r"create\s+oleobject|create\s+automation|"
    r"fileopen|filewrite|fileread|fileexists|filedelete|saveas|exportfile|"
    r"registryget|registryset|getcontextservice"
    r")\b",
    re.IGNORECASE,
)


# ---------------- Helpers ----------------
def read_pb_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe"):
        return raw.decode("utf-16le", errors="ignore")
    if raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16be", errors="ignore")
    if raw.count(b"\x00") > max(1, len(raw) // 10):
        try:
            return raw.decode("utf-16le", errors="ignore")
        except Exception:
            pass
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("cp932")
    except UnicodeDecodeError:
        pass
    return raw.decode("latin1", errors="ignore")


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//.*", "", text)
    text = re.sub(r"(?m)^\s*'.*$", "", text)
    return text


def normalize(s: str) -> str:
    return s.lower().strip() if s is not None else ""


IGNORE_OBJECTS: set[str] = {"this", "parent", "super", "application"}


def extract_object_id(text: str, fallback: str) -> str:
    m = OBJECT_DECL_REGEX.search(text)
    if m:
        return m.group("id")
    return fallback


def _unique_candidate_from_list(cands: list[str]) -> str | None:
    if not cands:
        return None
    uniq = sorted(set(cands))
    return uniq[0] if len(uniq) == 1 else None


# ---------------- Plugin Implementation ----------------


class PowerBuilderPlugin(LanguagePluginBase):
    def __init__(self, source_dir: Path):
        super().__init__(source_dir)
        self.id_map: dict[str, str] = {}
        self.known_ids: set[str] = set()

    @property
    def supported_extensions(self) -> set[str]:
        """Dynamically exposes extensions managed under the strict manifest contract maps."""
        return set(self.get_manifest().extension_archetype_map.keys())

    def get_manifest(self) -> PluginManifest:
        """Task 2.1: Declares capabilities and self-reports extension archetype mapping structures."""
        return PluginManifest(
            plugin_name="PowerBuilder",
            supported_extensions=[
                ".srw",
                ".sru",
                ".srd",
                ".srm",
                ".sra",
                ".srf",
                ".srs",
                ".srq",
                ".pbl",
                ".srp",
                ".srx",
            ],
            extension_archetype_map={
                ".srw": "ui_anchor",
                ".srm": "ui_anchor",
                ".sra": "ui_anchor",
                ".sru": "shared_logic",
                ".srf": "shared_logic",
                ".srs": "shared_logic",
                # Proxy objects: client-side interface stubs for remote/distributed
                # (EAServer) services. Method signatures = shared business contract.
                ".srx": "shared_logic",
                ".srd": "data_provider",
                ".srq": "data_provider",
                # Data Pipeline objects: DB-to-DB ETL (source SELECT -> destination
                # table with an update strategy). Embedded SQL is extracted by the
                # generic masked-region path, same as DataWindows.
                ".srp": "data_provider",
                ".pbl": "data_provider",
            },
            archetypes=["ui_anchor", "shared_logic", "data_provider"],
            risk_flags=[
                "embedded_sql",
                "dynamic_sql",
                "transaction_control",
                "external_dependency",
                "complex_sql",
                "dynamic_sql_detected",
                "transaction_control_detected",
                "external_dll_call",
                "complex_sql_logic",
            ],
            paradigm_hints={
                "ui_anchor": "Focus on Window (.srw) and Menu (.srm) lifecycles. Explicitly trace DataWindow control events (e.g., ItemChanged, Clicked, RowFocusChanged), map hidden state managed by background DataStores, and extract client-side validation rules/edit masks. Watch for transaction_control via COMMIT/ROLLBACK boundaries on SQLCA.",
                "shared_logic": "Analyze Non-Visual Objects (NVOs, .sru) and global functions for encapsulated business rules. Map explicit function signatures (arguments/returns), trace data contracts, and explicitly monitor transaction boundaries (SetTransObject, COMMIT/ROLLBACK) for global state changes.",
                "data_provider": "Heavily scrutinize DataWindow (.srd) files. Extract the exact embedded SQL (SELECT statements, arguments), map compute fields, and extract embedded validation rules. Explicitly document the Update Properties (UpdateWhere strategy, KeyColumns) for Optimistic Locking constraints, and map composite key deduplication.",
            },
            preferred_encodings=["utf-16", "utf-8", "cp1252", "latin-1"],
        )

    def discover(self) -> list[dict[str, Any]]:
        """Scans the repository and resolves canonical taxonomy types from the self-contained manifest mapping."""
        artifacts = []
        manifest = self.get_manifest()
        files = sorted(
            [
                p
                for p in self.source_dir.rglob("*")
                if p.suffix.lower() in self.supported_extensions
            ],
            key=lambda p: str(p).lower(),
        )

        for p in files:
            ext = p.suffix.lower()
            _stripped_text = ""  # captured for .sru archetype refinement
            try:
                raw = read_pb_text(p)
                text = strip_comments(raw)
                aid = extract_object_id(text, p.stem)
                self.id_map[aid.lower()] = aid
                if ext == ".sru":
                    _stripped_text = text  # save for refinement below
            except Exception:
                aid = p.stem
                self.id_map[aid.lower()] = aid

            self.known_ids.add(aid.lower())

            # Task 2.1 Fix: Query its own manifest mapping at runtime to decouple lookups
            archetype = manifest.extension_archetype_map.get(ext, "unresolved")

            # Option C fix: .sru covers two structurally distinct subtypes.
            # Refine using naming prefix + inheritance + event-density signals
            # so DataWindow User Objects (uo_*) get ui_anchor, not shared_logic.
            if ext == ".sru":
                archetype = self._refine_sru_archetype(aid, _stripped_text)

            artifacts.append(
                {
                    "id": aid,
                    "path": str(p.resolve()),
                    "file_name": p.name,
                    "type": archetype,
                    "paradigm_language": "powerbuilder",
                }
            )

        return artifacts

    def analyze_artifact(self, file_path: Path) -> ArtifactAnalysisResult:
        """
        SINGLE-PASS FORENSIC ANALYSIS (AST Facade)
        Maps legacy Regex extraction into the strict Enterprise Hybrid Parser output.
        """
        try:
            raw_text = read_pb_text(file_path)
            text = strip_comments(raw_text)
        except Exception as e:
            print(f"[WARN] Could not read {file_path}: {e}")
            return {
                "metrics": {"lines_of_code": 0, "script_lines": 0},
                "edges": [],
                "signals": {},
                "landmines": {
                    "flags": [],
                    "severity": "Low",
                    "normalized_score": 0,
                    "masked_regions": [],
                },
            }

        ext = file_path.suffix.lower()
        aid = extract_object_id(text, file_path.stem)
        aid_norm = normalize(aid)

        lines = text.splitlines()
        non_empty = [l for l in lines if l.strip()]

        # STAGE 1 & 2: MASKING FACADE & METRICS
        # -------------------------------------------------------------
        metrics: MetricsDict = {
            "lines_of_code": len(lines),
            "script_lines": len(non_empty),
            "function_count": len(FUNCTION_REGEX.findall(text)),
            "event_count": len(EVENT_REGEX.findall(text)),
            # Explicitly 0.0 to notify the graph this was parsed via Regex Fallback
            "ast_health_score": 0.0,
            "total_nodes": 0,
            "error_nodes": 0,
        }

        masked_regions: list[MaskedRegionDict] = []

        # FIX: Capture Dynamic SQL
        for match in DYNAMIC_SQL_REGEX.finditer(text):
            start, end = match.span()
            masked_regions.append(
                {"category": "dynamic_sql", "start": start, "end": end, "raw_text": text[start:end]}
            )

        # FIX: Capture Standard Embedded SQL (SELECT INTO)
        for match in SELECT_INTO_REGEX.finditer(text):
            start, end = match.span()
            masked_regions.append(
                {
                    "category": "embedded_sql",
                    "start": start,
                    "end": end,
                    "raw_text": text[start:end],
                }
            )

        # FIX: Capture Execute Immediate / Exec SQL
        for match in EXEC_SQL_REGEX.finditer(text):
            start, end = match.span()
            masked_regions.append(
                {
                    "category": "embedded_sql",
                    "start": start,
                    "end": end,
                    "raw_text": text[start:end],
                }
            )

        # STAGE 3: SIGNALS & LANDMINES
        # -------------------------------------------------------------
        is_datawindow = ext == ".srd"
        if is_datawindow:
            embedded_sql = True
        else:
            embedded_sql = bool(
                EXEC_SQL_REGEX.search(text)
                or SELECT_INTO_REGEX.search(text)
                or (USING_SQLCA_REGEX.search(text) and SQLCA_REGEX.search(text))
            )

        has_dynamic = bool(DYNAMIC_SQL_REGEX.search(text))
        has_transaction = bool(TRANSACTION_REGEX.search(text))
        has_external = bool(EXTERNAL_CODE_REGEX.search(text))

        is_complex_sql = False
        if embedded_sql or has_dynamic:
            if (
                SQL_COMPLEX_KEYWORDS.search(text)
                or len(re.findall(r"\bSELECT\b", text, re.IGNORECASE)) > 3
            ):
                is_complex_sql = True

        signals = {
            "embedded_sql": embedded_sql,
            "dynamic_sql": has_dynamic,
            "transaction_control": has_transaction,
            "external_dependency": has_external,
            "complex_sql": is_complex_sql,
        }

        landmine_score = 0
        flags = []
        if has_dynamic:
            flags.append("dynamic_sql_detected")
            landmine_score += 2
        if has_transaction:
            flags.append("transaction_control_detected")
            landmine_score += 1
        if has_external:
            flags.append("external_dll_call")
            landmine_score += 3
        if is_complex_sql:
            flags.append("complex_sql_logic")
            landmine_score += 1

        severity = "Low"
        if landmine_score >= 3:
            severity = "High"
        elif landmine_score >= 1:
            severity = "Medium"

        landmines: LandmineDict = {
            "flags": flags,
            "normalized_score": landmine_score,
            "severity": severity,
            "masked_regions": masked_regions,
        }

        # STAGE 4: TOPOLOGICAL EDGES (Regex Fallback)
        # -------------------------------------------------------------
        ref_map: dict[str, str] = {}
        line_map: dict[str, int] = {}  # normalized_id → first occurrence line (1-based)
        col_map: dict[str, int] = {}  # normalized_id → column of first occurrence (0-based)

        def _record_position(candidate: str, match_start: int) -> None:
            """Records the first occurrence line/column of a reference candidate.

            Called alongside _add_strong / _add_weak_fuzzy so that every edge
            produced by this method carries a real source location instead of the
            previously hardcoded zero that collapsed all Trace IDs to L0.
            Uses the same normalisation key as ref_map to guarantee lookup parity.
            Only the *first* occurrence is stored — subsequent matches for the same
            target are ignored, which is consistent with 'first-seen wins' semantics
            used by the upstream graph builder.
            """
            if not candidate:
                return
            cid = normalize(candidate)
            if not cid or cid in line_map:
                return  # first occurrence already recorded — preserve it
            line_num = text[:match_start].count("\n") + 1
            col_num = match_start - text.rfind("\n", 0, match_start) - 1
            line_map[cid] = line_num
            col_map[cid] = max(0, col_num)

        def _add_strong(candidate: str) -> None:
            if not candidate:
                return
            cid = normalize(candidate)
            if not cid or cid == aid_norm:
                return
            if cid in self.known_ids:
                ref_map[cid] = "strong"
            else:
                ref_map[cid] = "unresolved"

        def _add_weak_fuzzy(candidate: str) -> None:
            if not candidate:
                return
            cid = normalize(candidate)
            if not cid or cid == aid_norm:
                return

            if cid in self.known_ids:
                ref_map[cid] = "strong"
                return

            candidates: list[str] = []
            for prefix in ("d_", "dw_", "ddw_", "ddl_"):
                cand = f"{prefix}{cid}"
                if cand in self.known_ids:
                    candidates.append(cand)

            datawindows_known = [
                k for k in self.known_ids if k.startswith(("d_", "dw_", "ddw_", "ddl_"))
            ]
            fuzzy = [d for d in datawindows_known if cid in d]
            for f in fuzzy:
                if f not in candidates:
                    candidates.append(f)

            unique = _unique_candidate_from_list(sorted(set(candidates)))
            if unique and unique not in ref_map:
                ref_map[unique] = "weak"
            elif not unique and cid not in ref_map:
                ref_map[cid] = "unresolved"

        # --- Strong-reference patterns (CREATE / OPEN family) ---
        for match in CREATE_REGEX.finditer(text):
            m = match.group(1)
            _record_position(m, match.start())
            _add_strong(m)

        for match in OPEN_REGEX.finditer(text):
            m = match.group(1)
            _record_position(m, match.start())
            _add_strong(m)

        for match in OPEN_PARM_REGEX.finditer(text):
            m = match.group(1)
            _record_position(m, match.start())
            _add_strong(m)

        for match in OPEN_SHEET_REGEX.finditer(text):
            m = match.group(1)
            _record_position(m, match.start())
            _add_strong(m)

        # --- Member-call pattern (obj.Method(target)) ---
        for match in MEMBER_CALL_REGEX.finditer(text):
            groups = match.groups()
            obj_name = groups[0]
            target = groups[2] if len(groups) >= 3 and groups[2] else None
            if obj_name and obj_name.lower() not in IGNORE_OBJECTS:
                _record_position(obj_name, match.start())
                _add_strong(obj_name)
            if target:
                _record_position(target, match.start())
                _add_strong(target)

        # --- DataWindow token fuzzy resolution ---
        # Track first match position per raw token so that prefix-expanded
        # keys (e.g. "dw_foo" resolved from tok="foo") inherit the source location.
        tok_pos: dict[str, int] = {}  # normalized token → first match_start
        for match in DW_TOKEN_REGEX.finditer(text):
            tok = normalize(match.group(1))
            if tok and tok not in tok_pos:
                tok_pos[tok] = match.start()

        datawindows_known = [
            k for k in self.known_ids if k.startswith(("d_", "dw_", "ddw_", "ddl_"))
        ]

        for tok in sorted(tok_pos.keys()):
            candidates = [f"d_{tok}", f"dw_{tok}", f"ddw_{tok}", f"ddl_{tok}"]
            matched = [c for c in candidates if c in self.known_ids]
            if len(matched) == 1:
                if matched[0] not in ref_map:
                    ref_map[matched[0]] = "weak"
                _record_position(matched[0], tok_pos[tok])
            else:
                fuzzy_matches = [d for d in datawindows_known if tok in d]
                if len(fuzzy_matches) == 1:
                    ref_map.setdefault(fuzzy_matches[0], "weak")
                    _record_position(fuzzy_matches[0], tok_pos[tok])

        # --- DataObject property patterns ---
        for match in DATAOBJECT_PROPERTY_REGEX.finditer(text):
            m = match.group(1)
            _record_position(m, match.start())
            if m.lower() in self.known_ids:
                _add_strong(m)
            else:
                _add_weak_fuzzy(m)

        for match in MODIFY_DATAOBJECT_REGEX.finditer(text):
            m = match.group(1)
            _record_position(m, match.start())
            if m.lower() in self.known_ids:
                _add_strong(m)
            else:
                _add_weak_fuzzy(m)

        # --- Quoted token passes ---
        # Build a deduplicated map of quoted_str → first match_start so that
        # both passes below operate on deterministic first-occurrence positions
        # rather than iterating a set() whose order varies by Python version.
        quoted_positions: dict[str, int] = {}
        for match in QUOTED_TOKEN_REGEX.finditer(text):
            q = match.group("str")
            if len(q) < 2:
                continue
            q_lower = normalize(q)
            if q_lower not in quoted_positions:
                quoted_positions[q_lower] = match.start()

        # Pass 1: register all known IDs found as quoted literals
        for q_lower, q_start in sorted(quoted_positions.items()):
            if q_lower == aid_norm:
                continue
            if q_lower in self.known_ids and q_lower not in ref_map:
                ref_map[q_lower] = "weak"
                _record_position(q_lower, q_start)

        # Pass 2: context-aware DataObject / Modify() resolution
        for q_lower, q_start in sorted(quoted_positions.items()):
            if len(q_lower) < 2:
                continue
            for qm in re.finditer(r"['\"]" + re.escape(q_lower) + r"['\"]", text):
                ctx_start = max(0, qm.start() - 80)
                ctx_end = qm.end() + 80
                context = text[ctx_start:ctx_end].lower()
                if "dataobject" in context or "modify(" in context:
                    _record_position(q_lower, qm.start())
                    if q_lower in self.known_ids:
                        _add_strong(q_lower)
                    else:
                        _add_weak_fuzzy(q_lower)
                    break

        edges: list[EdgeDict] = []
        for k in sorted(ref_map.keys()):
            orig = self.id_map.get(k, k)
            src_line = line_map.get(k, 0)  # 1-based; 0 = position not resolved by regex
            src_col = col_map.get(k, 0)
            edges.append(
                {
                    "target": orig,
                    "type": f"pb_{ref_map[k]}_call",
                    "line": src_line,  # legacy compat field — mirrors line_start
                    "line_start": src_line,  # used by TRCE-{TYPE}-{artifact_id}-L{line_start}
                    "line_end": src_line,  # single-token reference — start == end
                    "column": src_col,
                }
            )

        return {"metrics": metrics, "edges": edges, "signals": signals, "landmines": landmines}

    # ---------------------------------------------------------
    # DataWindow User Object Archetype Refinement (Option C fix)
    # ---------------------------------------------------------

    def _refine_sru_archetype(self, aid: str, text: str) -> str:
        """
        Determines the correct archetype for a PowerBuilder .sru file by
        distinguishing between its two structurally distinct subtypes:

            u_  prefix  →  Non-Visual Object (NVO): pure business/service
                           logic with no UI event handlers.
                           Correct archetype: shared_logic  ✓

            uo_ prefix  →  DataWindow User Object: a visual UI widget
                           (DataWindow wrapper, custom control) embedded
                           inside .srw windows. Has sort, row-selection,
                           context-menu, and focus event handlers.
                           Correct archetype: ui_anchor  ✓

        The extension_archetype_map cannot make this distinction because
        both subtypes share the .sru extension. This method applies three
        independent deterministic signals in priority order — no LLM call,
        no prompt heuristics.

        Signal 1 — Naming convention (highest confidence, always checked first):
            PowerBuilder enforces 'uo_' as the canonical prefix for visual
            User Objects. If the extracted artifact ID starts with 'uo_',
            it is definitively a DataWindow User Object.

        Signal 2 — Inheritance declaration (high confidence):
            Parses 'type <id> from <parent>' in the source. If <parent> is
            a known DataWindow base class (e.g. u_dw, datawindow, userobject),
            the file is a DataWindow wrapper.

        Signal 3 — UI event density (belt-and-suspenders):
            NVOs never contain DataWindow-specific UI event handlers
            (SetSort, SelectRow, RButtonDown, etc.). If ≥ 2 such signatures
            are found, the file is a visual DataWindow object.

        Falls back to shared_logic when no signal fires — correct for NVOs.

        Args:
            aid  : Canonical artifact ID extracted from the file header.
            text : Comment-stripped source text (may be "" on read failure).

        Returns:
            "ui_anchor"    — DataWindow User Object confirmed.
            "shared_logic" — NVO or type could not be determined.
        """
        aid_lower = aid.lower()

        # ------------------------------------------------------------------
        # Signal 1: naming convention
        # 'uo_' is the PowerBuilder IDE-enforced prefix for visual user
        # objects. This is the most reliable signal and fires instantly.
        # ------------------------------------------------------------------
        if aid_lower.startswith("uo_"):
            return "ui_anchor"

        # ------------------------------------------------------------------
        # Signal 2: inheritance declaration
        # Parse 'type <id> from <parent_class>' in the source.
        # DataWindow User Objects always inherit from a DataWindow base.
        # ------------------------------------------------------------------
        if text:
            parent_match = re.search(
                r"(?i)(?:global\s+type|type)\s+[a-zA-Z_][a-zA-Z0-9_]*"
                r"\s+from\s+([a-zA-Z_][a-zA-Z0-9_]*)",
                text,
            )
            if parent_match:
                parent = parent_match.group(1).lower()
                if parent in _DW_BASE_CLASSES or parent.startswith(("uo_", "u_dw")):
                    return "ui_anchor"

        # ------------------------------------------------------------------
        # Signal 3: UI event density
        # NVOs never contain DataWindow-specific UI event handlers.
        # Two or more matches is treated as conclusive.
        # ------------------------------------------------------------------
        if text and len(_DW_UI_EVENT_REGEX.findall(text)) >= 2:
            return "ui_anchor"

        # No signal fired — this is a Non-Visual Object (NVO).
        return "shared_logic"

    # ---------------------------------------------------------
    # NEW (Multi-Language Decoupling): Language Hook Overrides
    # ---------------------------------------------------------

    def get_filename_candidates(self, art_id: str, ext: str) -> list[str]:
        """
        Returns the ordered list of candidate filenames for PowerBuilder artifacts.

        PowerBuilder IDE enforces a strict prefix naming convention based on the
        object type. A window named "mainwindow" is saved as "w_mainwindow.srw".
        This method produces both the unprefixed and prefixed variants so the
        orchestrator can locate files regardless of whether the artifact ID already
        includes the prefix (e.g., both "mainwindow" and "w_mainwindow" resolve).

        Prefix mapping by extension:
            .srw  →  w_   (Window)
            .srd  →  d_   (DataWindow)
            .sru  →  u_   (Non-Visual Object / NVO)
            .srf  →  f_   (Function)
            .srm  →  m_   (Menu)
            .sra  →  a_   (Application object)
            .srs  →  s_   (Structure)
            .srq  →  q_   (Query)
            .pbl  →  (no prefix — library container, not a typed object)

        Args:
            art_id: The canonical artifact identifier (e.g., "mainwindow", "d_orders").
            ext:    The file extension including the leading dot (e.g., ".srw", ".srd").

        Returns:
            List[str]: Ordered candidates. Unprefixed is always first (handles cases
                       where the caller already passed "w_mainwindow" as art_id).
                       Prefixed variant appended when the extension has a known prefix.
                       All entries are lowercased.
        """
        _PB_PREFIX_MAP = {
            ".srw": "w_",
            ".srd": "d_",
            ".sru": "u_",
            ".srf": "f_",
            ".srm": "m_",
            ".sra": "a_",
            ".srs": "s_",
            ".srq": "q_",
        }
        ext_lower = ext.strip().lower()
        base = f"{art_id}{ext_lower}".lower()
        candidates = [base]  # Unprefixed always first

        prefix = _PB_PREFIX_MAP.get(ext_lower)
        if prefix:
            prefixed = f"{prefix}{art_id}{ext_lower}".lower()
            if prefixed != base:
                candidates.append(prefixed)

        return candidates

    def get_preferred_encodings(self) -> list[str]:
        """
        Returns the encoding fallback order for PowerBuilder source files.

        PowerBuilder IDE (v6-v12) saves .srw / .sru / .srd / .srf files as
        UTF-16 LE with a BOM. Later versions may use UTF-8. The read_pb_text()
        helper in this module performs BOM detection at runtime for byte-precise
        accuracy; this method provides the advisory list used by the generic
        orchestrator _read_smart() path as a fallback when BOM detection is
        not invoked.

        Returns:
            List[str]: ["utf-16", "utf-8", "cp932", "cp1252", "latin-1"]
        """
        # cp932 (Shift-JIS) before cp1252/latin-1: Japanese PowerBuilder source is
        # common; cp932 decodes it correctly and raises on non-JP bytes, so it only
        # wins for genuine Shift-JIS files.
        return ["utf-16", "utf-8", "cp932", "cp1252", "latin-1"]

    def get_dep_resolvers(self) -> list:
        """
        Exposes PowerBuilder-specific dependency extraction patterns as callable resolvers.

        These three patterns extract artifact IDs that are referenced by the source file
        but may not appear as explicit function calls (hence not captured by the main
        STAGE 4 edge extraction). The orchestrator passes these callables into the BFS
        traversal queue in _gather_mfu_code() to ensure full dependency graph coverage.

        Resolvers:
            1. resolve_datawindow  -- Extracts IDs from DataObject = "d_name" property
               assignments. This is how Window scripts reference DataWindow objects.
            2. resolve_ancestor    -- Extracts the parent class from the global type ...
               from <AncestorID> declaration. Critical for inheritance chain traversal.
            3. resolve_menu        -- Extracts the Menu object ID from MenuName = "m_name"
               assignments on Window objects.

        Returns:
            List[Callable[[str], List[str]]]: Three stateless resolver callables.
        """

        def resolve_datawindow(content: str) -> list[str]:
            """Extracts DataWindow IDs from DataObject property assignments."""
            return [
                m.group(1).lower()
                for m in DATAOBJECT_PROPERTY_REGEX.finditer(content)
                if m.group(1)
            ]

        def resolve_ancestor(content: str) -> list[str]:
            """
            Extracts parent class ID from global type ... from <AncestorID>.

            Filters out PowerBuilder built-in base class names that are framework
            types rather than real project artifacts (e.g., Window, NonVisualObject).
            These names must not be queued into the BFS traversal as artifact IDs.
            """
            # PB built-in base types that are not project artifacts
            _PB_BASE_CLASSES = frozenset(
                [
                    "window",
                    "userobject",
                    "datawindow",
                    "menu",
                    "nonvisualobject",
                    "datastoreobject",
                    "transaction",
                    "oleobject",
                    "dragobject",
                    "graphicobject",
                ]
            )
            m = OBJECT_DECL_REGEX.search(content)
            if m:
                # Re-search for the "from <parent>" part directly.
                full_match = re.search(
                    r"(?i)(?:global\s+type|type)\s+[a-zA-Z_][a-zA-Z0-9_]*\s+from\s+([a-zA-Z_][a-zA-Z0-9_]*)",
                    content,
                )
                if full_match:
                    ancestor = full_match.group(1).lower()
                    if ancestor not in _PB_BASE_CLASSES:
                        return [ancestor]
            return []

        def resolve_menu(content: str) -> list[str]:
            """Extracts Menu object ID from MenuName = "m_name" property on Windows."""
            m = re.search(r'(?i)MenuName\s*=\s*"([^"]+)"', content)
            if m:
                return [m.group(1).lower()]
            return []

        return [resolve_datawindow, resolve_ancestor, resolve_menu]

    def strip_noise(self, content: str, file_ext: str) -> str:
        """Tier 2 PB noise stripper: removes visual coords and PBExport headers."""
        import re as _re

        visual_props = (
            "X",
            "Y",
            "Width",
            "Height",
            "BackColor",
            "ForeColor",
            "TextColor",
            "TextSize",
            "Bold",
            "Italic",
            "Underline",
            "StrikeThrough",
            "Border",
            "BorderStyle",
            "BorderColor",
            "TabOrder",
            "TabStop",
            "BringToTop",
            "Visible",
            "Enabled",
            "RMButtonAction",
            "VScrollBar",
            "HScrollBar",
            "UnitsPerColumn",
            "UnitsPerRow",
            "HeaderHeight",
            "SummaryHeight",
            "DetailHeight",
            "FooterHeight",
            "GridLines",
            "CrosshatchColumns",
            "Resizable",
            "PointerType",
            "BackgroundColor",
            "DisplayOnly",
            "Color",
            "FontCharSet",
            "FontFamily",
            "FontPitch",
        )
        pat = _re.compile(
            r"^\s*(?:" + "|".join(_re.escape(p) for p in visual_props) + r")\s*=\s*\S",
            _re.IGNORECASE,
        )
        export_pat = _re.compile(r"^\s*\$PBExport", _re.IGNORECASE)
        lines = content.splitlines(keepends=True)
        result = []
        blank_run = 0
        for line in lines:
            s = line.rstrip("\r\n")
            if not s.strip():
                blank_run += 1
                if blank_run <= 1:
                    result.append(line)
                continue
            blank_run = 0
            if pat.match(s) or export_pat.match(s):
                continue
            result.append(line)
        return "".join(result)

    # ---------------------------------------------------------
    # Tier 4 — Structural Summary (universal hook override)
    # ---------------------------------------------------------

    def extract_structural_summary(self, content: str, file_ext: str, char_budget: int) -> str:
        """
        Tier 4 PowerBuilder structural extractor.

        Dispatches to a language-aware sub-extractor based on file extension:
          .srd  → DataWindow block-segment extractor (_extract_dw_summary):
                   Keeps SRS-critical blocks (datawindow/table/column/compute/group)
                   and discards layout-only blocks (band/text/line/rectangle/bitmap…).
          other → Script declaration extractor (_extract_script_summary):
                   Extracts forward/type/variable declarations in full, then
                   function/event/subroutine signatures (first line only).

        Contract (inherited from LanguagePluginBase):
          - Returns content as-is when len(content) <= char_budget.
          - Output length is always <= char_budget.
          - Never truncates mid-block; skips entire blocks when budget is tight.
          - Appends a human-readable omission marker listing what was dropped.
          - Deterministic and idempotent.

        Args:
            content    : Raw (or Tier-2-stripped) file text.
            file_ext   : Lowercase extension including dot (e.g. ".srd", ".srw").
            char_budget: Maximum character count for the returned string.

        Returns:
            str: Structural summary guaranteed to fit within char_budget.
        """
        if len(content) <= char_budget:
            return content  # Already fits — nothing to summarise.

        if file_ext.lower() == ".srd":
            return self._extract_dw_summary(content, char_budget)
        else:
            return self._extract_script_summary(content, char_budget)

    def _extract_dw_summary(self, content: str, char_budget: int) -> str:
        """
        DataWindow (.srd) structural extractor.

        PowerBuilder DataWindow files are flat sequences of top-level blocks, each
        beginning with a keyword followed by a balanced parenthesised body:

            datawindow(...)   table(...)   column(...)   band(...)   text(...) …

        Strategy:
          Phase 1 — Segment: walk the file and split at recognised block keywords,
                    tracking parenthesis depth so multi-line blocks are treated as
                    a single unit.  Lines before the first recognised block form the
                    preamble and are always kept.
          Phase 2 — Bucket: classify each block as KEEP (SRS-relevant) or SKIP
                    (layout-only).  KEEP blocks are stored by keyword; SKIP blocks
                    are only counted.
          Phase 3 — Fill: emit blocks greedily in priority order
                    (datawindow → table → column → compute → group) until the
                    budget is exhausted.  Entire blocks are either included or
                    excluded — never split.
          Phase 4 — Marker: append a structured omission summary listing all
                    skipped block types with their counts.
        """
        # ── Phase 1: Segment ──────────────────────────────────────────────
        preamble_lines: list[str] = []
        blocks: list[tuple] = []  # (keyword_lower, full_text)
        cur_kw: str | None = None
        cur_lines: list[str] = []
        depth: int = 0

        for line in content.splitlines(keepends=True):
            stripped = line.lstrip()
            if depth == 0:
                m = _PB_DW_BLOCK_RE.match(stripped)
                if m:
                    # Flush current accumulator
                    if cur_kw is not None:
                        blocks.append((cur_kw, "".join(cur_lines)))
                    cur_kw = m.group(1).lower()
                    cur_lines = [line]
                    depth = line.count("(") - line.count(")")
                    if depth < 0:
                        depth = 0
                else:
                    if cur_kw is None:
                        preamble_lines.append(line)
                    else:
                        cur_lines.append(line)
            else:
                cur_lines.append(line)
                depth += line.count("(") - line.count(")")
                if depth < 0:
                    depth = 0

        # Flush final accumulator
        if cur_kw is not None:
            blocks.append((cur_kw, "".join(cur_lines)))

        # ── Phase 2: Bucket ───────────────────────────────────────────────
        _PRIORITY = ["datawindow", "table", "column", "compute", "group"]
        keep_by_kw: dict[str, list[str]] = {k: [] for k in _PRIORITY}
        skip_counts: dict[str, int] = {}

        for kw, text in blocks:
            if kw in _PB_DW_KEEP_BLOCKS:
                keep_by_kw[kw].append(text)
            else:
                skip_counts[kw] = skip_counts.get(kw, 0) + 1

        # ── Phase 3: Fill budget ──────────────────────────────────────────
        parts: list[str] = []
        used: int = 0

        preamble = "".join(preamble_lines)
        if preamble:
            if used + len(preamble) <= char_budget:
                parts.append(preamble)
                used += len(preamble)
            # Preamble too large to fit alone — emit truncated preamble header
            # (this is extremely rare; a real .srd preamble is < 200 chars).
            else:
                snippet = preamble[:char_budget]
                parts.append(snippet)
                used += len(snippet)

        for kw in _PRIORITY:
            for text in keep_by_kw[kw]:
                if used + len(text) <= char_budget:
                    parts.append(text)
                    used += len(text)
                else:
                    # Block doesn't fit — count it as skipped
                    skip_counts[kw] = skip_counts.get(kw, 0) + 1

        # ── Phase 4: Omission marker ──────────────────────────────────────
        all_skipped = {k: v for k, v in skip_counts.items() if v > 0}
        if all_skipped:
            skipped_str = ", ".join(
                f"{k}×{v}" for k, v in sorted(all_skipped.items(), key=lambda x: x[0])
            )
            marker = f"\n--- [TIER-4 OMITTED BLOCKS: {skipped_str}] ---\n"
            # Ensure marker fits within char_budget without exceeding it.
            # Drop complete blocks from the tail (not mid-block truncation) until
            # there is room.  This fires only in extreme edge cases where the
            # budget is so tight that content + marker together exceed it.
            while parts and len("".join(parts)) + len(marker) > char_budget:
                parts.pop()
            parts.append(marker)

        result = "".join(parts)
        # Final safety clamp (guards against empty-parts edge case)
        return result if result else content[:char_budget]

    def _extract_script_summary(self, content: str, char_budget: int) -> str:
        """
        PowerBuilder script (.srw / .sru / .srf / .srm / .srq) structural extractor.

        PowerBuilder script files contain three structural layers:
          1. Declaration blocks (high-priority, always keep in full):
               forward … end forward
               global type … end type
               type variables … end variables
               global variables … end variables
               shared variables … end variables
          2. Procedure signatures (medium-priority, keep first line only):
               on <event>  /  event <name>  /  function …  /  subroutine …
             The body is discarded — only the signature line is retained so the
             spec LLM can see every callable surface without reading the body.
          3. Unclassified preamble lines (e.g. $PBExportHeader$, blank lines):
             kept verbatim before the first recognised block.

        Fill order: preamble → declaration blocks → procedure signatures.
        Entire blocks / signatures are either included or excluded (never split).
        """
        lines = content.splitlines(keepends=True)

        # ── Collect buckets ───────────────────────────────────────────────
        preamble_lines: list[str] = []
        high_blocks: list[str] = []  # Full declaration block text
        med_sigs: list[str] = []  # Procedure signature lines

        i = 0
        in_high = False
        in_med = False
        cur_lines: list[str] = []

        while i < len(lines):
            line = lines[i]

            if in_high:
                cur_lines.append(line)
                if _PB_SCRIPT_HIGH_CLOSE_RE.match(line):
                    high_blocks.append("".join(cur_lines))
                    cur_lines = []
                    in_high = False
            elif in_med:
                # Body lines discarded; only watch for close keyword
                if _PB_SCRIPT_MED_CLOSE_RE.match(line):
                    in_med = False
            else:
                if _PB_SCRIPT_HIGH_OPEN_RE.match(line):
                    in_high = True
                    cur_lines = [line]
                elif _PB_SCRIPT_MED_OPEN_RE.match(line):
                    in_med = True
                    med_sigs.append(line)  # Signature only
                else:
                    preamble_lines.append(line)
            i += 1

        # Flush unclosed high block (malformed file guard)
        if cur_lines:
            high_blocks.append("".join(cur_lines))

        # ── Fill budget ───────────────────────────────────────────────────
        parts: list[str] = []
        used: int = 0

        preamble = "".join(preamble_lines)
        if preamble and used + len(preamble) <= char_budget:
            parts.append(preamble)
            used += len(preamble)

        omitted_high = 0
        for text in high_blocks:
            if used + len(text) <= char_budget:
                parts.append(text)
                used += len(text)
            else:
                omitted_high += 1

        omitted_med = 0
        for sig in med_sigs:
            if used + len(sig) <= char_budget:
                parts.append(sig)
                used += len(sig)
            else:
                omitted_med += 1

        # ── Omission marker ───────────────────────────────────────────────
        omit_parts: list[str] = []
        if omitted_high:
            omit_parts.append(f"declaration_blocks×{omitted_high}")
        if omitted_med:
            omit_parts.append(f"procedure_signatures×{omitted_med}")
        if omit_parts:
            marker = f"\n--- [TIER-4 OMITTED: {', '.join(omit_parts)}] ---\n"
            while parts and len("".join(parts)) + len(marker) > char_budget:
                parts.pop()
            parts.append(marker)

        result = "".join(parts)
        return result if result else content[:char_budget]
