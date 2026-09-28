"""
VB6 Language Plugin (Production-Grade)
Refactored to comply with the unified LanguagePluginBase contract.
Enforces self-reporting manifests and full 4-stage forensic masking analysis.
"""

from __future__ import annotations

import logging
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

# Use tree-sitter-language-pack for pre-compiled parsers safely
try:
    from tree_sitter_language_pack import get_language, get_parser

    PACK_AVAILABLE = True
except ImportError:
    PACK_AVAILABLE = False
    logging.error("[VB6_PLUGIN] tree-sitter-language-pack not found. AST extraction disabled.")

try:
    from ..utils.legacy_sanitizer import sanitize_source
except ImportError:
    # Fallback if sanitizer is missing during local cross-compilation testing.
    # Mirrors legacy_sanitizer's waterfall so Japanese (cp932) source isn't silently
    # dropped by a hard utf-8/errors=ignore read. (#24)
    def sanitize_source(path: Path, language_hint: str = None) -> str:
        raw = path.read_bytes()
        for enc in ("utf-8-sig", "utf-8", "cp932", "cp1252", "latin-1"):
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return raw.decode("utf-8", errors="ignore")


logger = logging.getLogger(__name__)

# Preserved High-Fidelity AST Queries (Focused on VB6/COM Graph Boundaries)
AST_QUERIES = {
    "vb": """
        (invocation_expression (identifier) @vb.target)
        (object_creation_expression (identifier) @vb.target)
    """
}

# Targeted High-Fidelity VB6 Regex Patterns
VB6_NEW_DECL_REGEX = re.compile(r"\bAs\s+New\s+([a-zA-Z_][a-zA-Z0-9_]*)\b", re.IGNORECASE)
VB6_SET_NEW_REGEX = re.compile(r"\bSet\s+\w+\s*=\s*New\s+([a-zA-Z_][a-zA-Z0-9_]*)\b", re.IGNORECASE)
VB6_CREATEOBJECT_REGEX = re.compile(
    r"\bCreateObject\s*\(\s*['\"]([a-zA-Z0-9_\.]+)['\"]\s*\)", re.IGNORECASE
)
VB6_CALL_REGEX = re.compile(r"\bCall\s+([a-zA-Z_][a-zA-Z0-9_]*)\b", re.IGNORECASE)
VB6_DECLARE_REGEX = re.compile(
    r"\bDeclare\s+(?:PtrSafe\s+)?(?:Function|Sub)\s+\w+\s+Lib\s+['\"]([^'\"]+)['\"]", re.IGNORECASE
)
VB6_SQL_REGEX = re.compile(r"\"\s*(?:SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM)\b", re.IGNORECASE)
VB6_TRANS_REGEX = re.compile(r"\b(BeginTrans|CommitTrans|RollbackTrans)\b", re.IGNORECASE)

# ── Archetype-Refinement Constants (Task #24 — VB6 .bas classification) ──────
#
# Signal 1 — Sub Main presence: a .bas file that declares "Sub Main" is an
# application entry point, not a passive library.  Naming is case-insensitive
# because VB6 source may be saved in any casing by the IDE.
_VB6_SUB_MAIN_REGEX = re.compile(
    r"^\s*(?:Public\s+|Private\s+)?Sub\s+Main\s*(?:\(\s*\))?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Signal 2 — Form / GUI-driver interactions: presence of at least one of these
# patterns inside a .bas file indicates the module orchestrates a windowed UI
# rather than running headlessly.  Threshold ≥ 1 match is intentional — even a
# single "Load frmXxx" or "frmXxx.Show" is enough to prove GUI involvement.
_VB6_FORM_INTERACTION_REGEX = re.compile(
    r"\b(?:"
    r"Load\s+[Ff]rm"  # Load frmXxx  — explicit form load
    r"|[Ff]rm\w+\.(?:Show|Hide)"  # frmXxx.Show / frmXxx.Hide
    r"|Unload\s+[Ff]rm"  # Unload frmXxx
    r"|Unload\s+Me"  # Unload Me (inside a form, referenced from .bas)
    r"|DoEvents\s*(?:\(\s*\))?"  # DoEvents — yields to Windows message pump
    r"|Screen\.(?:ActiveForm|ActiveControl|MousePointer)"
    r"|App\.(?:TaskVisible|hInstance)"
    r"|Forms\s*\("  # Forms(index) collection access
    r"|VBA\.UserForms"  # VBA variant
    r")",
    re.IGNORECASE,
)


def strip_vb6_comments(source_text: str) -> str:
    """
    State-aware comment stripping with line parity.
    Safely removes VB6 single-line comments starting with an apostrophe (') or REM.
    Preserves exact line counts and newlines to prevent byte-offset drift.
    """
    clean_lines = []
    for line in source_text.splitlines(keepends=True):
        stripped = line.lstrip()

        if stripped.startswith("'") or stripped.upper().startswith("REM "):
            ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            clean_lines.append(ending)
            continue

        in_string = False
        comment_idx = -1

        for i, char in enumerate(line):
            if char == '"':
                in_string = not in_string
            elif char == "'" and not in_string:
                comment_idx = i
                break

        if comment_idx != -1:
            ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            clean_lines.append(line[:comment_idx].rstrip() + ending)
        else:
            clean_lines.append(line)

    return "".join(clean_lines)


class VB6Plugin(LanguagePluginBase):
    """
    Dedicated plugin for Visual Basic 6 / VBA.
    Decoupled Architecture: Capabilities are self-contained within the Manifest object.
    """

    def __init__(self, source_dir: Path):
        super().__init__(source_dir)

    @property
    def supported_extensions(self) -> set[str]:
        """Dynamically exposes extensions managed under the strict manifest contract maps."""
        return set(self.get_manifest().extension_archetype_map.keys())

    def get_manifest(self) -> PluginManifest:
        """Declares capabilities and self-reports extension archetype mapping structures."""
        return PluginManifest(
            plugin_name="VB6",
            supported_extensions=[".frm", ".bas", ".cls", ".ctl", ".dsr", ".dob", ".pag"],
            extension_archetype_map={
                ".frm": "ui_anchor",
                ".ctl": "ui_anchor",
                # UserDocument (.dob): ActiveX document — a full user-facing surface
                # (hosted in IE/container) with its own code-behind and controls.
                ".dob": "ui_anchor",
                # PropertyPage (.pag): the UI dialog backing a control's custom
                # property sheet — event-driven code + controls, so ui_anchor.
                ".pag": "ui_anchor",
                ".cls": "shared_logic",
                ".bas": "shared_logic",
                ".dsr": "data_provider",
            },
            archetypes=["ui_anchor", "shared_logic", "data_provider"],
            risk_flags=[
                "embedded_sql",
                "dynamic_sql",
                "transaction_control",
                "external_dependency",
                "embedded_sql_detected",
                "transaction_control_detected",
                "external_dll_call",
                "windows_api_call",
            ],
            paradigm_hints={
                "ui_anchor": "Focus on Form lifecycle events (Form_Load, QueryUnload), control arrays, and hidden state (.Tag). Explicitly trace ADODB.Recordset flows, mapped UI data-bindings (DataSource/DataField), and watch for Win32 API declarations (kernel32).",
                "shared_logic": "Analyze standard modules (.bas) for global state pollution and class modules (.cls) for COM component instantiation. Explicitly trace ADODB flows, Win32 API declarations, and map 'On Error GoTo' blocks to modern exception handling.",
                "data_provider": "Inspect Data Environment (.dsr) files and Adodc controls for centralized data connections. Extract raw SQL commands, parameter mappings, and explicitly document ADO CursorTypes (e.g., ForwardOnly/Static) and LockTypes (Optimistic/Pessimistic) for concurrency rules.",
            },
            preferred_encodings=["utf-8", "cp1252", "latin-1"],
        )

    def discover(self) -> list[dict[str, Any]]:
        """
        Scans the source directory and maps each file to its archetype.

        For all extensions except ``.bas`` the archetype is read directly from
        the manifest (e.g., ``.frm`` → ``ui_anchor``).  For ``.bas`` files the
        manifest default of ``shared_logic`` is used only as a fallback; the
        content-aware ``_refine_bas_archetype()`` method is called instead to
        distinguish GUI entry points (``ui_anchor``), headless runners
        (``batch_anchor``), and passive utility modules (``shared_logic``).
        """
        artifacts = []
        manifest = self.get_manifest()

        for file_path in self.source_dir.rglob("*"):
            ext = file_path.suffix.lower()
            if ext not in self.supported_extensions:
                continue

            artifact_id = file_path.stem.lower()
            archetype = manifest.extension_archetype_map[ext]  # manifest default

            if ext == ".bas":
                # Content-aware override: .bas files can be ui_anchor,
                # batch_anchor, or shared_logic depending on their content.
                try:
                    raw_text = sanitize_source(file_path, language_hint="vb")
                    stripped_text = strip_vb6_comments(raw_text)
                    archetype = self._refine_bas_archetype(artifact_id, stripped_text)
                except Exception as exc:
                    logger.warning(
                        "[VB6_PLUGIN] Could not read %s for archetype refinement (%s); "
                        "falling back to manifest default '%s'.",
                        file_path.name,
                        exc,
                        archetype,
                    )

            artifacts.append(
                {
                    "id": artifact_id,
                    "path": str(file_path.resolve()),
                    "file_name": file_path.name,
                    "type": archetype,
                    "paradigm_language": "vb6",
                }
            )

        return artifacts

    def _extract_from_ast(self, tree, ts_lang) -> list[EdgeDict]:
        """Executes compiled Tree-sitter queries against the generated AST."""
        deps: list[EdgeDict] = []
        query_string = AST_QUERIES.get("vb")

        if not query_string:
            return deps

        try:
            query = ts_lang.query(query_string)
            captures = query.captures(tree.root_node)

            for node, _ in captures:
                target_name = node.text.decode("utf-8").strip("'\" ")
                deps.append(
                    {
                        "target": target_name.lower(),
                        "type": "vb6_ast_call",
                        "line_start": node.start_point[0] + 1,
                        "line_end": node.end_point[0] + 1,
                        "column": node.start_point[1],
                        "resolved": False,
                    }
                )
        except Exception as e:
            logger.debug(f"[VB6_PLUGIN] AST query failed: {e}")

        return deps

    def _extract_dependencies_fallback(self, clean_source_text: str) -> list[EdgeDict]:
        """Robust regex fallback for extracting outbound calls using high-fidelity VB6 rules."""
        deps: list[EdgeDict] = []
        lines = clean_source_text.splitlines()

        for i, line in enumerate(lines):
            line_num = i + 1

            def add_edge(match_obj, edge_type):
                deps.append(
                    {
                        "target": match_obj.group(1).lower(),
                        "type": edge_type,
                        "line_start": line_num,
                        "line_end": line_num,
                        "column": match_obj.start(1),
                        "resolved": False,
                    }
                )

            for m in VB6_NEW_DECL_REGEX.finditer(line):
                add_edge(m, "vb6_instantiation")
            for m in VB6_SET_NEW_REGEX.finditer(line):
                add_edge(m, "vb6_instantiation")
            for m in VB6_CREATEOBJECT_REGEX.finditer(line):
                add_edge(m, "vb6_com_creation")
            for m in VB6_CALL_REGEX.finditer(line):
                add_edge(m, "vb6_call")
            for m in VB6_DECLARE_REGEX.finditer(line):
                add_edge(m, "windows_api_declare")

        return deps

    def analyze_artifact(self, file_path: Path) -> ArtifactAnalysisResult:
        """Performs single-pass deep forensic analysis matching the hybrid parsing specification."""
        try:
            raw_source = sanitize_source(file_path, language_hint="vb")
        except Exception as e:
            logger.error(f"[VB6_PLUGIN] Failed to read {file_path}: {e}")
            return {
                "metrics": {
                    "lines_of_code": 0,
                    "script_lines": 0,
                    "ast_health_score": 0.0,
                    "total_nodes": 0,
                    "error_nodes": 0,
                },
                "edges": [],
                "signals": {},
                "landmines": {
                    "flags": [],
                    "severity": "Low",
                    "normalized_score": 0,
                    "masked_regions": [],
                },
            }

        clean_source = strip_vb6_comments(raw_source)
        raw_lines = raw_source.splitlines(keepends=True)
        clean_lines = clean_source.splitlines(keepends=True)
        script_lines = [l for l in clean_lines if l.strip()]

        outbound_calls: list[EdgeDict] = []
        ast_health = 0.0
        total_nodes = 0
        error_nodes = 0

        if PACK_AVAILABLE:
            try:
                ts_lang_obj = get_language("vb")
                parser = get_parser("vb")

                if parser and ts_lang_obj:
                    tree = parser.parse(bytes(clean_source, "utf8"))
                    total_nodes = tree.root_node.child_count
                    ast_health = 100.0 if not tree.root_node.has_error else 50.0
                    outbound_calls = self._extract_from_ast(tree, ts_lang_obj)
            except Exception as e:
                logger.debug(f"[VB6_PLUGIN] AST unavailable for {file_path.name}: {e}")

        regex_calls = self._extract_dependencies_fallback(clean_source)

        seen_edges = set()
        final_edges: list[EdgeDict] = []
        for edge in outbound_calls + regex_calls:
            sig = (edge["target"], edge["line_start"])
            if sig not in seen_edges:
                seen_edges.add(sig)
                final_edges.append(edge)

        metrics: MetricsDict = {
            "lines_of_code": len(raw_lines),
            "script_lines": len(script_lines),
            "ast_health_score": ast_health,
            "total_nodes": total_nodes,
            "error_nodes": error_nodes,
        }

        masked_regions: list[MaskedRegionDict] = []
        has_sql = False
        has_trans = False
        has_external = False

        in_sql_continuation = False
        in_api_continuation = False
        block_buffer = []
        block_start_byte = 0
        current_byte_offset = 0

        for raw_line, clean_line in zip(raw_lines, clean_lines, strict=False):
            line_len = len(raw_line.encode("utf-8"))
            clean_stripped = clean_line.strip()
            is_continuation = clean_stripped.endswith("_")
            is_string_builder = bool(re.search(r"&\s*\"|\"\s*&", clean_stripped))

            if VB6_TRANS_REGEX.search(clean_line):
                has_trans = True
            if VB6_CREATEOBJECT_REGEX.search(clean_line):
                has_external = True

            if in_api_continuation:
                block_buffer.append(raw_line.rstrip("\r\n"))
                if not is_continuation:
                    in_api_continuation = False
                    masked_regions.append(
                        {
                            "category": "windows_api_call",
                            "start": block_start_byte,
                            "end": current_byte_offset + line_len,
                            "raw_text": "\n".join(block_buffer),
                        }
                    )
                    block_buffer = []

            elif in_sql_continuation:
                block_buffer.append(raw_line.rstrip("\r\n"))
                if not (is_continuation or is_string_builder):
                    in_sql_continuation = False
                    masked_regions.append(
                        {
                            "category": "embedded_sql",
                            "start": block_start_byte,
                            "end": current_byte_offset + line_len,
                            "raw_text": "\n".join(block_buffer),
                        }
                    )
                    block_buffer = []

            elif VB6_DECLARE_REGEX.search(clean_line):
                has_external = True
                block_start_byte = current_byte_offset
                block_buffer.append(raw_line.rstrip("\r\n"))
                if is_continuation:
                    in_api_continuation = True
                else:
                    masked_regions.append(
                        {
                            "category": "windows_api_call",
                            "start": block_start_byte,
                            "end": current_byte_offset + line_len,
                            "raw_text": "\n".join(block_buffer),
                        }
                    )
                    block_buffer = []

            elif VB6_SQL_REGEX.search(clean_line):
                has_sql = True
                block_start_byte = current_byte_offset
                block_buffer.append(raw_line.rstrip("\r\n"))
                if is_continuation or is_string_builder:
                    in_sql_continuation = True
                else:
                    masked_regions.append(
                        {
                            "category": "embedded_sql",
                            "start": block_start_byte,
                            "end": current_byte_offset + line_len,
                            "raw_text": "\n".join(block_buffer),
                        }
                    )
                    block_buffer = []

            current_byte_offset += line_len

        if in_api_continuation and block_buffer:
            masked_regions.append(
                {
                    "category": "windows_api_call",
                    "start": block_start_byte,
                    "end": current_byte_offset,
                    "raw_text": "\n".join(block_buffer),
                }
            )
        elif in_sql_continuation and block_buffer:
            masked_regions.append(
                {
                    "category": "embedded_sql",
                    "start": block_start_byte,
                    "end": current_byte_offset,
                    "raw_text": "\n".join(block_buffer),
                }
            )

        signals = {
            "embedded_sql": has_sql,
            "dynamic_sql": has_sql,
            "transaction_control": has_trans,
            "external_dependency": has_external,
        }

        landmine_score = 0
        flags = []

        if has_sql:
            flags.append("embedded_sql_detected")
            landmine_score += 1
        if has_trans:
            flags.append("transaction_control_detected")
            landmine_score += 1
        if has_external:
            flags.append("external_dll_call")
            landmine_score += 3

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

        return {
            "metrics": metrics,
            "edges": final_edges,
            "signals": signals,
            "landmines": landmines,
        }

    # ---------------------------------------------------------
    # NEW (Multi-Language Decoupling): Language Hook Overrides
    # ---------------------------------------------------------

    # ---------------------------------------------------------
    # Archetype Refinement — .bas files (Task #24)
    # ---------------------------------------------------------

    def _refine_bas_archetype(self, aid: str, text: str) -> str:
        """
        Determines the precise archetype for a Visual Basic 6 standard module (.bas).

        The manifest default of ``shared_logic`` is correct for passive utility
        modules, but .bas files can also serve as application entry points.
        VB6 applications start execution at ``Sub Main`` (when "Sub Main" is
        selected in Project → Properties → Startup Object), so we need two
        additional signals to distinguish three cases:

        ┌─────────────────────────────┬──────────────────────────────────────────────┐
        │ Signal combination          │ Archetype                                    │
        ├─────────────────────────────┼──────────────────────────────────────────────┤
        │ Sub Main + form interactions│ ui_anchor  — GUI application entry point     │
        │ Sub Main, no form signals   │ batch_anchor — headless / console runner     │
        │ No Sub Main                 │ shared_logic — passive library / helper      │
        └─────────────────────────────┴──────────────────────────────────────────────┘

        Args:
            aid:  Artifact identifier (lowercased stem), used for logging only.
            text: Comment-stripped source text of the .bas file.

        Returns:
            str: One of ``"ui_anchor"``, ``"batch_anchor"``, or ``"shared_logic"``.
        """
        has_sub_main = bool(_VB6_SUB_MAIN_REGEX.search(text))

        if not has_sub_main:
            # No entry point — conventional shared module / utility library.
            logger.debug("[VB6_PLUGIN] %s → shared_logic (no Sub Main detected)", aid)
            return "shared_logic"

        has_form_interaction = bool(_VB6_FORM_INTERACTION_REGEX.search(text))

        if has_form_interaction:
            logger.debug("[VB6_PLUGIN] %s → ui_anchor (Sub Main + form-interaction signals)", aid)
            return "ui_anchor"

        logger.debug("[VB6_PLUGIN] %s → batch_anchor (Sub Main present, no form interactions)", aid)
        return "batch_anchor"

    def get_filename_candidates(self, art_id: str, ext: str) -> list[str]:
        """
        Returns the candidate filenames for VB6 artifacts.

        Visual Basic 6 uses no IDE-enforced prefix naming convention for source files.
        A Form named "frmBatchRun" is saved verbatim as "frmBatchRun.frm". Module and
        class files follow the same convention. Therefore, only the single unprefixed
        candidate is returned.

        Args:
            art_id: The canonical artifact identifier (e.g., "frmbatchrun").
            ext:    The file extension including the leading dot (e.g., ".frm", ".cls").

        Returns:
            List[str]: A single-element list ["{art_id}{ext}"] in lowercase.
        """
        return [f"{art_id}{ext}".lower()]

    def get_preferred_encodings(self) -> list[str]:
        """
        Returns the encoding fallback order for VB6 source files.

        Visual Basic 6 IDE saves source files in the Windows ANSI code page, which
        is typically Windows-1252 (cp1252) on Western European systems. Modern
        source-controlled repositories may re-save files as UTF-8 without BOM.
        Latin-1 is included as a final broad fallback for any legacy encodings
        not covered by the above.

        Japanese VB6 source is saved in Shift-JIS (cp932); it is placed before the
        Western fallbacks so genuine Shift-JIS files decode correctly (cp932 raises on
        non-JP byte sequences, so it never wins for true cp1252/latin-1 files).

        Returns:
            List[str]: ["utf-8", "cp932", "cp1252", "latin-1"]
        """
        return ["utf-8", "cp932", "cp1252", "latin-1"]

    def get_dep_resolvers(self) -> list:
        """
        Exposes VB6-specific dependency extraction patterns as callable resolvers.

        These patterns extract class and module references from VB6 source that may
        not appear as explicit runtime calls but establish compile-time dependencies
        (e.g., "Dim x As New ClassName" is a static class reference, not an invocation).
        The orchestrator adds the returned IDs to the BFS traversal queue in
        _gather_mfu_code() to ensure full dependency graph coverage.

        Resolvers:
            1. resolve_instantiations — Extracts class names from:
               - "Dim x As New ClassName" (VB6_NEW_DECL_REGEX)
               - "Set x = New ClassName" (VB6_SET_NEW_REGEX)
            2. resolve_com_creations  — Extracts ProgIDs from:
               - CreateObject("ProgID") calls (VB6_CREATEOBJECT_REGEX)
            3. resolve_call_targets   — Extracts procedure names from:
               - "Call ProcedureName" statements (VB6_CALL_REGEX)

        Returns:
            List[Callable[[str], List[str]]]: Three stateless resolver callables.
                Each callable operates only on its str argument (stateless).
        """

        def resolve_instantiations(content: str) -> list[str]:
            """Extracts class IDs from Dim/As New and Set/New declarations."""
            results = []
            for m in VB6_NEW_DECL_REGEX.finditer(content):
                if m.group(1):
                    results.append(m.group(1).lower())
            for m in VB6_SET_NEW_REGEX.finditer(content):
                if m.group(1):
                    results.append(m.group(1).lower())
            # Deduplicate while preserving order
            return list(dict.fromkeys(results))

        def resolve_com_creations(content: str) -> list[str]:
            """Extracts COM ProgIDs from CreateObject() calls."""
            return [
                m.group(1).lower() for m in VB6_CREATEOBJECT_REGEX.finditer(content) if m.group(1)
            ]

        def resolve_call_targets(content: str) -> list[str]:
            """Extracts procedure/subroutine names from Call statements."""
            return [m.group(1).lower() for m in VB6_CALL_REGEX.finditer(content) if m.group(1)]

        return [resolve_instantiations, resolve_com_creations, resolve_call_targets]

    def strip_noise(self, content: str, file_ext: str) -> str:
        """Tier 2 VB6 noise stripper: removes designer coords and Attribute VB_* lines."""
        import re as _re

        visual_prop_names = frozenset(
            [
                "left",
                "top",
                "width",
                "height",
                "backcolor",
                "forecolor",
                "fillcolor",
                "bordercolor",
                "tabindex",
                "tabstop",
                "visible",
                "enabled",
                "fontbold",
                "fontitalic",
                "fontname",
                "fontsize",
                "fontstrikethru",
                "fontunderline",
                "fontcharset",
                "borderstyle",
                "appearance",
                "mousepointer",
                "mouseicon",
                "startupposition",
                "windowstate",
                "clipcontrols",
                "hasdc",
                "scalemode",
                "scalewidth",
                "scaleheight",
                "drawmode",
                "drawstyle",
                "drawwidth",
                "fillstyle",
                "autoredraw",
                "imemode",
                "righttoleft",
            ]
        )
        attr_pat = _re.compile(r"^\s*Attribute\s+VB_\w+\s*=", _re.IGNORECASE)
        begin_pat = _re.compile(r"^\s*Begin\s+VB\.", _re.IGNORECASE)
        end_pat = _re.compile(r"^\s*End\s*$", _re.IGNORECASE)
        vprop_pat = _re.compile(
            r"^\s+(" + "|".join(_re.escape(p) for p in sorted(visual_prop_names)) + r")\s*=\s*",
            _re.IGNORECASE,
        )
        lines = content.splitlines(keepends=True)
        result = []
        blank_run = 0
        depth = 0
        for line in lines:
            s = line.rstrip("\r\n")
            if not s.strip():
                blank_run += 1
                if blank_run <= 1:
                    result.append(line)
                continue
            blank_run = 0
            if begin_pat.match(s):
                depth += 1
                result.append(line)
                continue
            if end_pat.match(s):
                if depth > 0:
                    depth -= 1
                    result.append(line)
                    continue
            if depth > 0 and vprop_pat.match(s):
                continue
            if attr_pat.match(s):
                continue
            result.append(line)
        return "".join(result)

    # ---------------------------------------------------------
    # Tier 4 — Structural Summary (universal hook override)
    # ---------------------------------------------------------

    def extract_structural_summary(self, content: str, file_ext: str, char_budget: int) -> str:
        """
        Tier 4 VB6 structural extractor — declaration + signature extraction.

        VB6 module files (.bas, .cls, .frm) contain six structural layers,
        classified here in priority order for SRS relevance:

          1. attr_option   — Attribute VB_* and Option Explicit/Base/Compare lines.
                             Always small; defines module identity and behaviour.
          2. type_blocks   — Type … End Type: UDT struct declarations.
          3. enum_blocks   — Enum … End Enum: named constant enumerations.
          4. declare_lines — Declare Function/Sub … Lib "…": external API bindings.
          5. module_vars   — Module-level Public/Private/Friend/Global variables
                             and Const declarations.
          6. proc_sigs     — Function/Sub/Property Get|Let|Set signature lines
                             (first line only; bodies are discarded).

        Strategy:
          Phase 1 — Parse: single pass over lines using state-machine.
                    Type/Enum blocks captured in full (they are small and
                    structurally complete).  Procedure bodies skipped; only
                    the opening signature line is retained.
          Phase 2 — Fill: emit items greedily in priority order within
                    char_budget.  Items are either included or excluded —
                    never truncated mid-item.
          Phase 3 — Marker: append structured omission summary.

        Contract (inherited from LanguagePluginBase):
          - Returns content as-is when len(content) <= char_budget.
          - Output length is always <= char_budget.
          - Deterministic and idempotent.

        Args:
            content    : Raw (or Tier-2-stripped) VB6 source text.
            file_ext   : Lowercase extension (e.g. ".bas", ".cls", ".frm").
            char_budget: Maximum character count for the returned string.

        Returns:
            str: Structural summary guaranteed to fit within char_budget.
        """
        import re as _re

        if len(content) <= char_budget:
            return content

        # ── Bucket detection patterns ─────────────────────────────────────
        _ATTR_OPT_RE = _re.compile(r"^\s*(?:Attribute\s+|Option\s+)", _re.IGNORECASE)
        _TYPE_OPEN_RE = _re.compile(r"^\s*(?:Public\s+|Private\s+)?Type\s+\w", _re.IGNORECASE)
        _TYPE_CLOSE_RE = _re.compile(r"^\s*End\s+Type\b", _re.IGNORECASE)
        _ENUM_OPEN_RE = _re.compile(r"^\s*(?:Public\s+|Private\s+)?Enum\s+\w", _re.IGNORECASE)
        _ENUM_CLOSE_RE = _re.compile(r"^\s*End\s+Enum\b", _re.IGNORECASE)
        _DECLARE_RE = _re.compile(r"^\s*(?:Public\s+|Private\s+)?Declare\s+", _re.IGNORECASE)
        _MOD_VAR_RE = _re.compile(
            r"^\s*(?:Public\s+|Private\s+|Friend\s+|Global\s+)(?:Const\s+|\w)",
            _re.IGNORECASE,
        )
        _PROC_SIG_RE = _re.compile(
            r"^\s*(?:Public\s+|Private\s+|Friend\s+)?"
            r"(?:Function|Sub|Property\s+(?:Get|Let|Set))\s+\w",
            _re.IGNORECASE,
        )
        _PROC_END_RE = _re.compile(r"^\s*End\s+(?:Function|Sub|Property)\b", _re.IGNORECASE)

        # ── Phase 1: Single-pass state-machine parse ──────────────────────
        attr_opt_lines: list[str] = []
        type_blocks: list[str] = []
        enum_blocks: list[str] = []
        declare_lines: list[str] = []
        mod_var_lines: list[str] = []
        proc_sigs: list[str] = []

        in_type = False
        in_enum = False
        in_proc = False
        cur_block: list[str] = []

        for line in content.splitlines(keepends=True):
            if in_type:
                cur_block.append(line)
                if _TYPE_CLOSE_RE.match(line):
                    type_blocks.append("".join(cur_block))
                    cur_block = []
                    in_type = False
            elif in_enum:
                cur_block.append(line)
                if _ENUM_CLOSE_RE.match(line):
                    enum_blocks.append("".join(cur_block))
                    cur_block = []
                    in_enum = False
            elif in_proc:
                # Body discarded — only watch for End keyword
                if _PROC_END_RE.match(line):
                    in_proc = False
            else:
                if _TYPE_OPEN_RE.match(line):
                    in_type = True
                    cur_block = [line]
                elif _ENUM_OPEN_RE.match(line):
                    in_enum = True
                    cur_block = [line]
                elif _PROC_SIG_RE.match(line):
                    proc_sigs.append(line)  # Signature line only
                    in_proc = True
                elif _DECLARE_RE.match(line):
                    declare_lines.append(line)
                elif _ATTR_OPT_RE.match(line):
                    attr_opt_lines.append(line)
                elif _MOD_VAR_RE.match(line):
                    mod_var_lines.append(line)

        # Flush unclosed block (malformed file guard)
        if cur_block:
            if in_type:
                type_blocks.append("".join(cur_block))
            elif in_enum:
                enum_blocks.append("".join(cur_block))

        # ── Phase 2: Fill budget in priority order ────────────────────────
        _BUCKETS: list[tuple] = [
            ("attr_option", attr_opt_lines),
            ("type_blocks", type_blocks),
            ("enum_blocks", enum_blocks),
            ("declare", declare_lines),
            ("module_vars", mod_var_lines),
            ("proc_sigs", proc_sigs),
        ]

        parts: list[str] = []
        used: int = 0
        omitted_counts: dict[str, int] = {}

        for bucket_name, items in _BUCKETS:
            for item in items:
                if used + len(item) <= char_budget:
                    parts.append(item)
                    used += len(item)
                else:
                    omitted_counts[bucket_name] = omitted_counts.get(bucket_name, 0) + 1

        # ── Phase 3: Omission marker ──────────────────────────────────────
        if omitted_counts:
            skipped_str = ", ".join(f"{k}×{v}" for k, v in omitted_counts.items())
            marker = f"\n--- [TIER-4 OMITTED: {skipped_str}] ---\n"
            # Ensure marker fits without breaching char_budget.
            while parts and len("".join(parts)) + len(marker) > char_budget:
                parts.pop()
            parts.append(marker)

        result = "".join(parts)
        return result if result else content[:char_budget]
