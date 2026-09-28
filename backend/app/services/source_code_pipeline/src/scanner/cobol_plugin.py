"""
COBOL Language Plugin (Enterprise Hybrid Parser)
Implements the LanguagePluginBase contract.
Utilizes AST for structural fidelity with precision Regex Masking
to prevent AST cascading on EXEC SQL, EXEC CICS, and COPY statements.
"""

import logging
from pathlib import Path
import re
from typing import Any

from .plugin_base import (
    ArtifactAnalysisResult,
    EdgeDict,
    LanguagePluginBase,
    MaskedRegionDict,
    PluginManifest,
)

# Use tree-sitter-language-pack for pre-compiled parsers
try:
    from tree_sitter_language_pack import get_language, get_parser

    PACK_AVAILABLE = True
except ImportError:
    PACK_AVAILABLE = False
    logging.error("[COBOL_PLUGIN] tree-sitter-language-pack not found. AST extraction disabled.")

logger = logging.getLogger(__name__)

# ---------------- Shallow Header Regex (Discovery Phase) ----------------
# ENTERPRISE FIX: Support quoted PROGRAM-IDs found in some legacy dialects
COBOL_PROG_ID_REGEX = re.compile(
    r"PROGRAM-ID\.\s+['\"]?(?P<id>[A-Za-z0-9_\-]+)['\"]?[.\s]", re.IGNORECASE
)

# ---------------- Masking Regex (Stage 1) ----------------
# These proprietary blocks cause AST parsers to cascade/panic.
# We extract them, record their byte-spans, and replace them with whitespace.
MASK_PATTERNS = {
    "embedded_sql": re.compile(r"\bEXEC\s+SQL\b.*?\bEND-EXEC\.?", re.IGNORECASE | re.DOTALL),
    "cics_transaction": re.compile(r"\bEXEC\s+CICS\b.*?\bEND-EXEC\.?", re.IGNORECASE | re.DOTALL),
    # ENTERPRISE FIX: Support quoted targets and extensions (e.g., COPY "REC.CPY".)
    "precompiler_copy": re.compile(
        r"\b(?:COPY|INCLUDE)\s+['\"]?([A-Za-z0-9_\-\.]+)['\"]?.*?\.", re.IGNORECASE | re.DOTALL
    ),
}

# ---------------- Fallback Regex (Stage 4) ----------------
# Used to extract edges and landmines from Masked Regions and AST Errors
# ENTERPRISE FIX: Support quoted targets
COBOL_CALL_REGEX = re.compile(r"\bCALL\s+['\"]?([A-Za-z0-9_\-\.]+)['\"]?", re.IGNORECASE)
COBOL_DB2_CALL_REGEX = re.compile(r"\bCALL\s+['\"]?(DSNTIAR|DSNTIAC)['\"]?", re.IGNORECASE)

# ---------------- COBOL Program Subtype Detection ----------------
# Used by _refine_cbl_archetype() to distinguish .cbl/.cob/.pco/.sqb subtypes.
#
# The extension_archetype_map cannot distinguish these three structurally
# distinct COBOL program types — they all share the same file extensions:
#
#   CICS online programs   → interactive transaction processing (ui_anchor)
#   Called subprograms     → reusable logic invoked via CALL (shared_logic)
#   Standalone batch drivers → top-level job executed by JCL (batch_anchor)
#
# Three deterministic, content-based signals are applied in priority order.
# No LLM involvement — detection is pure regex on normalized source text.

# Signal 1 — CICS online program (highest confidence, checked first).
# EXEC CICS blocks are unambiguous: only CICS online transaction programs
# contain them. They are NOT batch jobs — they respond to terminal/web
# requests via IBM CICS middleware. Correct archetype: ui_anchor.
_COBOL_CICS_REGEX = re.compile(r"\bEXEC\s+CICS\b", re.IGNORECASE)

# Signal 1b — BMS 3270 screen usage. `EXEC CICS` alone does NOT imply a user
# interface: MQ-triggered / linked CICS services are screenless. A genuine online
# SCREEN program drives a BMS map — it issues SEND MAP / RECEIVE MAP, which always
# carry a MAPSET(...) reference. `MAPSET` is a BMS-only keyword and is robust to
# COBOL fixed-format line continuation (the SEND/RECEIVE and MAP tokens may wrap).
_COBOL_BMS_MAP_REGEX = re.compile(r"\bMAPSET\b|\bSEND\s+MAP\b|\bRECEIVE\s+MAP\b", re.IGNORECASE)

# Extracts the BMS mapset name from a SEND/RECEIVE MAP ... MAPSET('NAME') option
# so a screen program can be linked to its physical .bms mapset artifact (which is
# indexed with a "_bms" id suffix — see discover / COBOL_BMS_MAPSET note).
_COBOL_MAPSET_REF_REGEX = re.compile(r"\bMAPSET\s*\(\s*'?([A-Z0-9#@\$]+)'?\s*\)", re.IGNORECASE)

# Signal 2a — LINKAGE SECTION presence.
# Called subprograms declare a LINKAGE SECTION to receive parameters from
# the calling program's CALL ... USING clause. Standalone batch drivers
# typically have no LINKAGE SECTION (or a skeleton one for JCL PARM only).
_COBOL_LINKAGE_SECTION_REGEX = re.compile(r"^\s*LINKAGE\s+SECTION\b", re.IGNORECASE | re.MULTILINE)

# Signal 2b — At least one 01-level or 77-level data item in LINKAGE SECTION.
# Confirms this is a genuine called subprogram with parameter contracts,
# not a batch driver that has an empty/skeleton LINKAGE SECTION for PARM.
_COBOL_LINKAGE_DATA_REGEX = re.compile(
    r"^\s{0,8}(?:0[1-9]|77)\s+[A-Z0-9][A-Z0-9-]*\b", re.IGNORECASE | re.MULTILINE
)

# ---------------- JCL Orchestration Regex (Stage 4, .jcl only) ----------------
# JCL is not COBOL and is skipped by the AST stage (see COBOL_AST_EXTENSIONS),
# but it carries the batch orchestration graph: which COBOL program each job
# step runs. We recover those JCL -> program edges with two patterns:
#   (a) Standard step:      //STEP EXEC PGM=PROGNAME
#   (b) IMS region control:  //STEP EXEC PGM=DFSRRC00,PARM='TYPE,PGMNAME,PSBNAME'
#       (BMP/DLI/DBB region controller wraps the real application program in the
#        PARM; the PGMNAME is the middle token.)
JCL_EXEC_PGM_REGEX = re.compile(r"\bEXEC\s+PGM=([A-Z0-9#@\$]+)", re.IGNORECASE)
JCL_IMS_PARM_REGEX = re.compile(
    r"PARM=[('\"]?\s*(BMP|DLI|DBB|ULU|BMR)\s*,\s*([A-Z0-9#@\$]+)\s*,\s*([A-Z0-9#@\$]+)",
    re.IGNORECASE,
)
# IMS region controllers + common z/OS system utilities that are NOT application
# programs (so JCL steps invoking them must not create a spurious program edge).
JCL_NON_APP_PGMS = {
    "DFSRRC00",  # IMS region controller (app pgm is in PARM)
    "IEFBR14",  # null / dataset-scaffolding utility
    "IDCAMS",  # VSAM Access Method Services
    "SORT",
    "ICEMAN",
    "ICETOOL",
    "SYNCSORT",  # sort/merge utilities
    "IEBGENER",
    "IEBCOPY",
    "IEBUPDTE",
    "IEHPROGM",  # dataset utilities
    "DFSURGU0",
    "DFSUDMP0",
    "DFSDDLT0",
    "DFSUCUM0",  # IMS system utilities
}

# ---------------- AST-eligible extensions (Stage 2/3 gate) ----------------
# The tree-sitter COBOL grammar must ONLY be run on genuine COBOL program
# source. JCL (job control) is orchestration syntax, NOT COBOL: feeding it to
# the COBOL grammar produces only ERROR nodes and — on certain constructs
# (e.g. nested SPACE=(...) / inline `DD *` data cards) — sends tree-sitter's
# error-recovery into a non-terminating (GIL-holding) loop that hangs the whole
# scan. JCL dependencies (COPY/PROC/PGM) are recovered by the Stage-4 regex
# fallback instead, so skipping the AST stage for JCL loses no information.
#
# Copybooks (.cpy / .inc) and DB2 DCLGEN (.dcl) ARE parsed: their DATA DIVISION /
# host-variable structures are valid COBOL. (.inc = Micro Focus / distributed-COBOL
# copybook alias, functionally identical to .cpy.)
COBOL_AST_EXTENSIONS = {".cbl", ".cob", ".pco", ".sqb", ".cpy", ".dcl", ".inc"}

# ---------------- JCL orchestration extensions (Stage-4 edge recovery) ----------------
# JCL job streams (.jcl) AND cataloged JCL procedures (.prc/.proc) carry batch
# orchestration (EXEC PGM / IMS PARM). PROCs are invoked by jobs via `EXEC PROCNAME`
# and contain their own EXEC PGM steps, so they MUST be scanned for program edges or
# the batch call-graph is incomplete. All three are non-COBOL and are skipped by the
# AST stage (they are NOT in COBOL_AST_EXTENSIONS); their edges come from the regex
# fallback in analyze_artifact (see the JCL_ORCHESTRATION_EXTENSIONS gate there).
JCL_ORCHESTRATION_EXTENSIONS = {".jcl", ".prc", ".proc"}

# ---------------- DB2 Access Contract Regex (Stage 4, from EXEC SQL) ----------------
# The application-tier DB access contract is recovered from the EXEC SQL blocks we
# already isolate as masked regions (see MASK_PATTERNS["embedded_sql"]). We do NOT
# parse DDL/stored procs/triggers here (that is the deferred DB-tier epic) — only
# what the program itself does: which tables, which CRUD/cursor operations, and
# which DCLGEN members it includes.
#
# DCLGEN include:  EXEC SQL INCLUDE <member> END-EXEC.  (SQLCA/SQLDA are system, skipped)
DB2_INCLUDE_REGEX = re.compile(r"\bINCLUDE\s+([A-Z0-9_#@\$]+)", re.IGNORECASE)
DB2_SYSTEM_INCLUDES = {"SQLCA", "SQLDA"}
# CRUD + cursor verbs paired with their target table (schema-qualified allowed).
DB2_ACCESS_REGEXES = [
    ("INSERT", re.compile(r"\bINSERT\s+INTO\s+([A-Z0-9_#@\$\.]+)", re.IGNORECASE)),
    ("UPDATE", re.compile(r"\bUPDATE\s+([A-Z0-9_#@\$\.]+)", re.IGNORECASE)),
    ("DELETE", re.compile(r"\bDELETE\s+FROM\s+([A-Z0-9_#@\$\.]+)", re.IGNORECASE)),
    ("SELECT", re.compile(r"\bFROM\s+([A-Z0-9_#@\$\.]+)", re.IGNORECASE)),
]
DB2_CURSOR_REGEX = re.compile(r"\bDECLARE\s+([A-Z0-9_#@\$]+)\s+CURSOR", re.IGNORECASE)
# Tokens that follow a CRUD verb but are SQL keywords, not table names
# (e.g. "FOR UPDATE OF col" -> "OF"; "UPDATE SET ..." -> "SET").
DB2_TABLE_STOPWORDS = {"OF", "SET", "WHERE", "VALUES", "TABLE", "ONLY", "CURRENT", "FROM"}

# ---------------- AST Queries (Stage 3) ----------------
AST_QUERIES = {
    # Extracts explicit CALLs to other programs
    "cobol_ast_call": """
        (call_statement 
            program: (string_literal) @target)
    """,
    # Identifies logical business paragraphs within the PROCEDURE DIVISION
    "cobol_paragraph": """
        (paragraph 
            (paragraph_name) @target)
    """,
}


def read_cobol_text(path: Path) -> str:
    """Safely reads Mainframe and PC COBOL encodings.

    cp932 (Shift-JIS) is placed right after utf-8 and before the Western fallbacks
    (#24): Japanese COBOL source decodes correctly with cp932, which RAISES on
    non-JP byte sequences, so it only wins for genuine Shift-JIS files and never
    mis-claims a true cp1252/latin-1 file. Without it, Japanese COBOL was silently
    decoded as cp1252/latin-1 mojibake. (cp037/EBCDIC stays last: single-byte codecs
    never raise, so it remains a deliberate final fallback.)
    """
    for enc in ["utf-8", "cp932", "cp1252", "latin-1", "cp037"]:  # cp037 is EBCDIC (IBM)
        try:
            return path.read_text(encoding=enc)
        except UnicodeError:
            continue
    return path.read_bytes().decode("utf-8", errors="replace")


def mask_cobol_comments(source_text: str) -> str:
    """
    ENTERPRISE FIX: Spatial-Preservation Comment Stripping.
    Replaces COBOL comments with exact-length spaces to prevent false positive
    regex matches on dead code, without altering byte offsets for downstream tools.
    """
    clean_lines = []
    for line in source_text.splitlines(keepends=True):
        # 1. Fixed Format Comments (Column 7 is 0-indexed 6)
        if len(line) > 6 and line[6] in ("*", "/"):
            ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            clean_lines.append(" " * (len(line) - len(ending)) + ending)
            continue

        # 2. Free Format Inline Comments (*>)
        in_string = False
        comment_idx = -1
        for i, char in enumerate(line):
            if char in ('"', "'"):
                in_string = not in_string
            elif char == "*" and i + 1 < len(line) and line[i + 1] == ">" and not in_string:
                comment_idx = i
                break

        if comment_idx != -1:
            ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            masked_len = len(line) - comment_idx - len(ending)
            clean_lines.append(line[:comment_idx] + (" " * masked_len) + ending)
        else:
            clean_lines.append(line)

    return "".join(clean_lines)


class CobolPlugin(LanguagePluginBase):
    def __init__(self, source_dir: Path):
        # Task 2.3 Fix: Hardcoded reference variable assignment removed from constructor instance setup
        super().__init__(source_dir)

    @property
    def supported_extensions(self) -> set[str]:
        """Dynamically derives extension arrays directly from the manifest routing contract keys."""
        return set(self.get_manifest().extension_archetype_map.keys())

    def get_manifest(self) -> PluginManifest:
        """Task 2.3: Registers capabilities and explicitly maps Mainframe extensions to universal archetypes."""
        return PluginManifest(
            plugin_name="COBOL",
            supported_extensions=[
                ".cbl",
                ".cob",
                ".pco",
                ".cpy",
                ".jcl",
                ".sqb",
                ".dcl",
                ".bms",
                ".prc",
                ".proc",
                ".psb",
                ".dbd",
                ".inc",
            ],
            extension_archetype_map={
                ".cbl": "batch_anchor",
                ".cob": "batch_anchor",
                ".pco": "batch_anchor",
                ".jcl": "batch_anchor",
                ".sqb": "batch_anchor",
                # Cataloged JCL procedures: orchestration members invoked by jobs via
                # `EXEC PROCNAME`. They carry EXEC PGM steps (recovered by the Stage-4
                # regex fallback, same as .jcl) so the batch call-graph stays complete.
                ".prc": "batch_anchor",
                ".proc": "batch_anchor",
                ".cpy": "data_provider",
                # COBOL copybook alias (Micro Focus / distributed COBOL). Same role as
                # .cpy: DATA DIVISION structures included via COPY. AST-parsed.
                ".inc": "data_provider",
                # IMS DL/I definitions: PSB (program specification block — the program's
                # view of IMS databases/segments) and DBD (database description — segment
                # hierarchy). Indexed as data-tier contracts (resolved by file stem, no
                # embedded program-id). They are NOT COBOL, so the AST stage skips them;
                # they feed Stage 4 as the hierarchical-DB analogue of the DB2 slice.
                ".psb": "data_provider",
                ".dbd": "data_provider",
                # DB2 DCLGEN copybooks: host-variable table structures included via
                # `EXEC SQL INCLUDE`. Treated like copybooks (data_provider) so the
                # table column contract is indexed, resolvable, and fed to Stage 4.
                ".dcl": "data_provider",
                # BMS physical mapset source (DFHMSD/DFHMDI/DFHMDF): the 3270 screen
                # layout — field positions and attribute bytes. Treated as a screen
                # definition provider (data_provider). NOTE: indexed with a "_bms"
                # id suffix (see discover) so it does NOT collide with the symbolic
                # map copybook (.cpy) which shares the same file stem.
                ".bms": "data_provider",
            },
            archetypes=["batch_anchor", "shared_logic", "data_provider"],
            risk_flags=[
                "embedded_sql",
                "transaction_control",
                "mainframe_cics_coupling",
                "ibm_database_dependency",
                "external_call",
                "cobol_copy_dependency",
            ],
            paradigm_hints={
                "batch_anchor": "Focus on the PROCEDURE DIVISION for batch orchestrations and JCL streams. Explicitly map file boundaries using ENVIRONMENT DIVISION `SELECT/ASSIGN` clauses. Isolate error recovery and transaction aborts by extracting `EXEC CICS ABEND` and `FILE STATUS` evaluations.",
                "shared_logic": "Analyze standard paragraphs used as `PERFORM` targets for shared logic. Trace data contracts explicitly via the `LINKAGE SECTION` and `CALL ... USING` parameters. Identify subprogram boundaries and legacy `GO TO` branching.",
                "data_provider": "COPY books and DATA DIVISION structures dictate the data model. You MUST meticulously extract IBM Db2 `EXEC SQL` blocks, cursor declarations, and table mappings. For flat files (VSAM/Sequential), explicitly document `REDEFINES` and `OCCURS DEPENDING ON` for accurate modern DTO schema generation.",
            },
            preferred_encodings=["utf-8", "cp1252", "latin-1", "cp037"],
        )

    def discover(self) -> list[dict[str, Any]]:
        """
        STAGE 0: Shallow Header Scan.
        Dynamically extracts architectural roles via the self-reported plugin manifest dictionary map.
        """
        artifacts = []
        manifest = self.get_manifest()

        for file_path in self.source_dir.rglob("*"):
            ext = file_path.suffix.lower()
            if ext in self.supported_extensions:
                # Task 2.3 Fix: Dynamic archetype taxonomy routing from manifest metadata map lookups
                archetype = manifest.extension_archetype_map.get(ext, "unresolved")

                raw_text = read_cobol_text(file_path)
                match = COBOL_PROG_ID_REGEX.search(raw_text)

                # Map true internal ID or fallback to physical stem
                artifact_id = match.group("id").lower() if match else file_path.stem.lower()

                # BMS namespacing: the physical mapset (.bms) and its generated
                # symbolic map copybook (.cpy) share the same file stem (e.g.
                # COPAU00.bms / COPAU00.cpy). Suffix the physical map id with
                # "_bms" so both can be indexed without an identity collision.
                # Programs link to it via a cics_bms_mapset edge (see analyze).
                if ext == ".bms":
                    artifact_id = f"{file_path.stem.lower()}_bms"

                # Option C fix: refine archetype for COBOL program files using
                # content-based signals. .jcl is always batch_anchor (unambiguous
                # batch orchestrator). .cpy is always data_provider (copybook).
                # .cbl/.cob/.pco/.sqb can be CICS online programs, called
                # subprograms, or standalone batch drivers — see _refine_cbl_archetype.
                if ext in (".cbl", ".cob", ".pco", ".sqb"):
                    archetype = self._refine_cbl_archetype(artifact_id, raw_text)

                artifacts.append(
                    {
                        "id": artifact_id,
                        "path": str(file_path.resolve()),
                        "file_name": file_path.name,
                        "type": archetype,
                        "paradigm_language": "cobol",
                    }
                )
        return artifacts

    def analyze_artifact(self, file_path: Path) -> ArtifactAnalysisResult:
        """
        Executes the 4-Stage Enterprise Hybrid Parsing Pipeline.
        Mask -> Parse -> Query -> Fallback.
        """
        try:
            raw_text = read_cobol_text(file_path)
        except Exception as e:
            logger.error(f"[COBOL_PLUGIN] Failed to read {file_path}: {e}")
            return self._empty_result()

        lines = raw_text.splitlines()
        script_lines = [l for l in lines if l.strip() and not (len(l) > 6 and l[6] in ("*", "/"))]

        # ENTERPRISE FIX: Create a clean text layer to prevent false positive regex triggers
        clean_text = mask_cobol_comments(raw_text)

        # STAGE 1: MASKING
        # -------------------------------------------------------------
        masked_regions: list[MaskedRegionDict] = []

        # ENTERPRISE FIX: Create a unified sterile buffer for AST & Fallback Regex
        # that has BOTH comments and proprietary blocks (SQL/CICS/COPY) blanked out.
        masked_clean_text = clean_text

        for category, pattern in MASK_PATTERNS.items():
            # Apply regex to clean text to ignore commented-out proprietary logic
            for match in pattern.finditer(clean_text):
                start, end = match.span()

                # ENTERPRISE FIX: Calculate accurate UTF-8 byte offsets for the schema
                byte_start = len(raw_text[:start].encode("utf-8"))
                byte_end = len(raw_text[:end].encode("utf-8"))

                raw_fragment = raw_text[start:end]

                # Precompute the line_start natively here using character index for speed
                line_start = raw_text.count("\n", 0, start) + 1

                masked_region = {
                    "category": category,
                    "start": byte_start,
                    "end": byte_end,
                    "raw_text": raw_fragment,
                }

                # Internal tracker for Stage 4
                masked_region["_line_start"] = line_start
                masked_regions.append(masked_region)

                # Replace with precise spaces in the sterile buffer to preserve line/column coordinates
                masked_clean_text = (
                    masked_clean_text[:start] + (" " * (end - start)) + masked_clean_text[end:]
                )

        # STAGE 2 & 3: AST PARSE & NATIVE QUERY
        # -------------------------------------------------------------
        edges: list[EdgeDict] = []
        total_nodes = 0
        error_nodes = 0
        ast_health = 0.0

        # AST-eligibility gate: only run the tree-sitter COBOL grammar on genuine
        # COBOL program source. JCL (and any other non-COBOL supported extension)
        # is skipped here — it would only yield ERROR nodes and can hang the
        # parser (see COBOL_AST_EXTENSIONS note). Its edges come from the regex
        # fallback below.
        ext = file_path.suffix.lower()
        ast_eligible = ext in COBOL_AST_EXTENSIONS

        if PACK_AVAILABLE and ast_eligible:
            try:
                ts_lang = get_language("cobol")
                parser = get_parser("cobol")

                if parser and ts_lang:
                    # AST is shielded from comments AND proprietary blocks
                    tree = parser.parse(bytes(masked_clean_text, "utf8"))
                    total_nodes = tree.root_node.child_count
                    ast_health = 100.0 if not tree.root_node.has_error else 75.0

                    call_query = ts_lang.query(AST_QUERIES["cobol_ast_call"])
                    for node, _ in call_query.captures(tree.root_node):
                        target_name = node.text.decode("utf-8").strip("'\"").lower()
                        edges.append(
                            {
                                "target": target_name,
                                "type": "cobol_ast_call",
                                "line_start": node.start_point[0] + 1,
                                "line_end": node.end_point[0] + 1,
                                "column": node.start_point[1],
                                "resolved": False,
                            }
                        )
            except Exception as e:
                logger.debug(f"[COBOL_PLUGIN] AST Parse failed for {file_path.name}: {e}")

        # STAGE 4: REGEX FALLBACK (Landmines & Missed Edges)
        # -------------------------------------------------------------
        flags = []
        landmine_score = 0
        db2_access: list[dict[str, str]] = []  # [{op, table}] application-tier DB2 usage
        db2_cursors: list[str] = []  # declared cursor names
        ims_psb: list[str] = []  # IMS PSB names (from JCL PARM)
        seen_db2 = set()
        seen_mapsets = set()  # BMS mapset ids already linked

        # 4a. Process the Masked Regions we intentionally bypassed
        for region in masked_regions:
            cat = region["category"]
            line_num = region.pop("_line_start", 1)  # Remove the temporary key

            if cat == "embedded_sql":
                if "embedded_sql" not in flags:
                    flags.append("embedded_sql")
                    landmine_score += 2
                sql_text = region["raw_text"]
                # (i) DCLGEN / copybook includes hidden inside EXEC SQL INCLUDE:
                # emit a copy edge so the DCLGEN (.dcl) artifact links to this program
                # and its column contract is gathered into the Stage-4 payload.
                for im in DB2_INCLUDE_REGEX.finditer(sql_text):
                    member = im.group(1).split(".")[0].lower()
                    if not member or member.upper() in DB2_SYSTEM_INCLUDES:
                        continue
                    edges.append(
                        {
                            "target": member,
                            "type": "cobol_copy_dependency",
                            "line_start": line_num,
                            "line_end": line_num,
                            "column": 0,
                            "resolved": False,
                        }
                    )
                # (ii) Application-tier DB2 access contract: CRUD/cursor + table.
                for op, rx in DB2_ACCESS_REGEXES:
                    for am in rx.finditer(sql_text):
                        table = am.group(1).upper().rstrip(".")
                        if not table or table in DB2_TABLE_STOPWORDS:
                            continue
                        key = (op, table)
                        if key not in seen_db2:
                            seen_db2.add(key)
                            db2_access.append({"op": op, "table": table})
                for cm in DB2_CURSOR_REGEX.finditer(sql_text):
                    cur = cm.group(1).upper()
                    if cur not in db2_cursors:
                        db2_cursors.append(cur)
            elif cat == "cics_transaction":
                if "mainframe_cics_coupling" not in flags:
                    flags.append("mainframe_cics_coupling")
                    landmine_score += 4
                # Link a screen program to its physical BMS mapset artifact.
                # SEND/RECEIVE MAP ... MAPSET('COPAU00') -> edge to "copau00_bms"
                # (the .bms is indexed with a _bms suffix; see discover). This pulls
                # the physical map (field positions/attributes) into the screen MFU.
                for mm in _COBOL_MAPSET_REF_REGEX.finditer(region["raw_text"]):
                    mapset_id = f"{mm.group(1).lower()}_bms"
                    if mapset_id in seen_mapsets:
                        continue
                    seen_mapsets.add(mapset_id)
                    edges.append(
                        {
                            "target": mapset_id,
                            "type": "cics_bms_mapset",
                            "line_start": line_num,
                            "line_end": line_num,
                            "column": 0,
                            "resolved": False,
                        }
                    )
            elif cat == "precompiler_copy":
                # Convert COPY statements into structural edges.
                m = re.search(
                    r"(?:COPY|INCLUDE)\s+['\"]?([A-Za-z0-9_\-\.]+)['\"]?",
                    region["raw_text"],
                    re.IGNORECASE,
                )
                if m:
                    # Normalize the target the SAME way get_dep_resolvers does:
                    # strip the COBOL statement-terminator period and any file
                    # extension (e.g. "CCPAURLY." -> "ccpaurly", "REC.CPY" -> "rec")
                    # so the edge target matches the copybook artifact id (its
                    # file stem). Without this, the greedy [.] in the char class
                    # captures the terminator period ("ccpaurly.") and the edge
                    # never resolves against artifact id "ccpaurly".
                    copy_target = m.group(1).split(".")[0].lower()
                    if not copy_target:
                        continue
                    edges.append(
                        {
                            "target": copy_target,
                            "type": "cobol_copy_dependency",
                            "line_start": line_num,
                            "line_end": line_num,
                            "column": 0,
                            "resolved": False,
                        }
                    )

        # 4b. Buffer-Level Extractor for External Calls
        # ENTERPRISE FIX: Run regex on masked_clean_text. It is impossible to extract a
        # phantom call from inside a comment or inside an EXEC SQL / EXEC CICS block.
        seen_edges = {(e["target"], e["line_start"]) for e in edges}

        for m in COBOL_CALL_REGEX.finditer(masked_clean_text):
            target = m.group(1).lower()
            # Calculate line number mathematically based on char index
            line_num = masked_clean_text.count("\n", 0, m.start()) + 1

            if (target, line_num) not in seen_edges:
                seen_edges.add((target, line_num))

                # Optional column offset calc
                last_newline = masked_clean_text.rfind("\n", 0, m.start())
                column = m.start() - last_newline - 1 if last_newline != -1 else m.start()

                edges.append(
                    {
                        "target": target,
                        "type": "cobol_regex_call",
                        "line_start": line_num,
                        "line_end": line_num,
                        "column": column,
                        "resolved": False,
                    }
                )

        if COBOL_DB2_CALL_REGEX.search(masked_clean_text):
            if "ibm_database_dependency" not in flags:
                flags.append("ibm_database_dependency")
                landmine_score += 3

        # 4c. JCL Orchestration Edges (.jcl / .prc / .proc)
        # Recover the JCL -> COBOL program invocation edges the AST stage skips,
        # so batch orchestration is represented in the graph (JCL steps become
        # entrypoints; the programs they run gain callers -> orchestrator role).
        # Cataloged procedures (.prc/.proc) carry the same EXEC PGM / IMS PARM steps.
        if ext in JCL_ORCHESTRATION_EXTENSIONS:
            jcl_pgms: dict[str, int] = {}  # program-id -> first line seen
            # (a) IMS region-controller steps: PARM='TYPE,PGMNAME,PSBNAME'
            for m in JCL_IMS_PARM_REGEX.finditer(masked_clean_text):
                pgm = m.group(2).upper()
                # Capture the PSB name (3rd token) as the IMS DB access handle,
                # regardless of whether the program is an app or system utility.
                psb = m.group(3).upper()
                if psb and psb not in ims_psb:
                    ims_psb.append(psb)
                if pgm in JCL_NON_APP_PGMS:
                    continue
                jcl_pgms.setdefault(pgm.lower(), masked_clean_text.count("\n", 0, m.start()) + 1)
            # (b) Direct step: EXEC PGM=PROGNAME (skip region controllers/utilities)
            for m in JCL_EXEC_PGM_REGEX.finditer(masked_clean_text):
                pgm = m.group(1).upper()
                if pgm in JCL_NON_APP_PGMS:
                    continue
                jcl_pgms.setdefault(pgm.lower(), masked_clean_text.count("\n", 0, m.start()) + 1)
            for pgm, line_num in jcl_pgms.items():
                edges.append(
                    {
                        "target": pgm,
                        "type": "jcl_exec_pgm",
                        "line_start": line_num,
                        "line_end": line_num,
                        "column": 0,
                        "resolved": False,
                    }
                )

        # Assemble Final Output
        severity = "Low"
        if landmine_score >= 4:
            severity = "High"
        elif landmine_score >= 2:
            severity = "Medium"

        return {
            "metrics": {
                "lines_of_code": len(lines),
                "script_lines": len(script_lines),
                "ast_health_score": ast_health,
                "total_nodes": total_nodes,
                "error_nodes": error_nodes,
            },
            "edges": edges,
            "signals": {
                "embedded_sql": "embedded_sql" in flags,
                "transaction_control": "mainframe_cics_coupling" in flags,
                "external_dependency": "ibm_database_dependency" in flags,
            },
            "landmines": {
                "flags": flags,
                "normalized_score": landmine_score,
                "severity": severity,
                "masked_regions": masked_regions,
            },
            # Application-tier DB access contract (the 80/20 DB slice). Populated
            # from EXEC SQL (db2_*) and JCL PARM (ims_psb). This is how the program
            # USES the database, NOT the DB's own logic (stored procs/triggers/DDL/
            # DBD) which is the deferred DB-tier epic.
            "db_access": {"db2_tables": db2_access, "db2_cursors": db2_cursors, "ims_psb": ims_psb},
        }

    # ---------------------------------------------------------
    # COBOL Program Subtype Refinement (Option C fix)
    # ---------------------------------------------------------

    def _refine_cbl_archetype(self, aid: str, text: str) -> str:
        """
        Determines the correct archetype for a COBOL program file
        (.cbl / .cob / .pco / .sqb) by applying three deterministic,
        content-based signals in priority order.

        The extension_archetype_map assigns 'batch_anchor' to all four
        extensions because COBOL programs are typically batch drivers.
        However, two structurally distinct subtypes share the same
        extensions and require a different archetype:

        Signal 1 — EXEC CICS presence (highest confidence):
            CICS online programs are interactive transaction processors
            (IBM CICS middleware). They respond to terminal or web
            requests and are NOT batch jobs. Only CICS online programs
            contain EXEC CICS blocks — the signal is unambiguous.
            Correct archetype: ui_anchor.

        Signal 2 — LINKAGE SECTION with data items (high confidence):
            Called subprograms declare a LINKAGE SECTION to receive
            parameters from a CALL ... USING clause in the invoking
            program. A skeleton LINKAGE SECTION with no data items is
            occasionally present in batch drivers (for JCL PARM), so
            we require at least one 01-level or 77-level item after the
            section header to confirm this is a genuine subprogram.
            Correct archetype: shared_logic.

        Default — No CICS, no LINKAGE data:
            Standalone batch driver executed directly by JCL.
            Correct archetype: batch_anchor (manifest default).

        Args:
            aid  : Canonical artifact ID (PROGRAM-ID value or file stem).
            text : Raw source text as decoded by read_cobol_text().
                   May be empty string on read failure — handled safely.

        Returns:
            "ui_anchor"    — CICS online transaction program.
            "shared_logic" — Called subprogram with parameter contract.
            "batch_anchor" — Standalone batch driver (default).
        """
        if not text:
            return "batch_anchor"

        # ------------------------------------------------------------------
        # Signal 1: CICS online SCREEN program (ui_anchor)
        # `EXEC CICS` alone is NOT sufficient — MQ-triggered or linked CICS
        # services are screenless and must NOT receive a UI blueprint. A genuine
        # online screen program drives a BMS map (SEND MAP / RECEIVE MAP, which
        # always carry MAPSET(...)). Require BOTH signals for ui_anchor.
        #   - CICS + BMS map  -> ui_anchor (3270 screen)
        #   - CICS, no BMS map -> fall through: it is a CICS service, classified
        #     by the LINKAGE check below (shared_logic) or default batch_anchor.
        # ------------------------------------------------------------------
        if _COBOL_CICS_REGEX.search(text) and _COBOL_BMS_MAP_REGEX.search(text):
            return "ui_anchor"

        # ------------------------------------------------------------------
        # Signal 2: Called subprogram
        # Require LINKAGE SECTION header AND at least one data item below it
        # to distinguish genuine subprograms from batch drivers with skeleton
        # LINKAGE SECTIONs used only to access JCL PARM values.
        # ------------------------------------------------------------------
        linkage_match = _COBOL_LINKAGE_SECTION_REGEX.search(text)
        if linkage_match:
            after_linkage = text[linkage_match.end() :]
            if _COBOL_LINKAGE_DATA_REGEX.search(after_linkage):
                return "shared_logic"

        # No signal fired — standalone batch driver.
        return "batch_anchor"

    def _empty_result(self) -> ArtifactAnalysisResult:
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
            "db_access": {"db2_tables": [], "db2_cursors": [], "ims_psb": []},
        }

    # ---------------------------------------------------------
    # NEW (Multi-Language Decoupling): Language Hook Overrides
    # ---------------------------------------------------------

    def get_filename_candidates(self, art_id: str, ext: str) -> list[str]:
        """
        Returns the candidate filenames for COBOL artifacts.

        COBOL programs and copybooks use no IDE-enforced prefix naming convention.
        A program with PROGRAM-ID "PAYROLL" is stored verbatim as "PAYROLL.cbl"
        (or the stem the developer chose). Copybooks are stored as their COPY target
        name with the .cpy extension. Therefore, only the single unprefixed candidate
        is returned, consistent with how COBOL source files appear in repositories.

        Args:
            art_id: The canonical artifact identifier (e.g., "payroll", "custrec").
            ext:    The file extension including the leading dot (e.g., ".cbl", ".cpy").

        Returns:
            List[str]: A single-element list ["{art_id}{ext}"] in lowercase.
        """
        return [f"{art_id}{ext}".lower()]

    def get_preferred_encodings(self) -> list[str]:
        """
        Returns the encoding fallback order for COBOL source files.

        This list mirrors the exact order used by read_cobol_text() in this module
        to ensure consistency between the generic orchestrator path (_read_smart)
        and the plugin's own direct file reader:

            1. utf-8    — Modern PC COBOL and source-controlled mainframe extracts.
            2. cp932    — Shift-JIS (Japanese). Multi-byte and STRICT (raises on non-JP
                          sequences), so it only wins for genuine Japanese source and is
                          safe to try before the single-byte Western codecs. (#24)
            3. cp1252   — Windows ANSI, common in PC COBOL IDEs (Micro Focus, etc.).
            4. latin-1  — ISO-8859-1 broad fallback for Western European legacy files.
            5. cp037    — IBM EBCDIC (Code Page 037). Used for true mainframe extract
                          files that have not been converted to ASCII by the FTP transfer.

        Note: cp037 is intentionally last because EBCDIC files are rare in modern
        repositories and cp037 decoding of a non-EBCDIC file produces garbage output
        rather than a UnicodeError, so it must only be tried after all other options fail.

        Returns:
            List[str]: ["utf-8", "cp932", "cp1252", "latin-1", "cp037"]
        """
        return ["utf-8", "cp932", "cp1252", "latin-1", "cp037"]

    def get_dep_resolvers(self) -> list:
        """
        Exposes COBOL-specific dependency extraction patterns as callable resolvers.

        COBOL uses two primary mechanisms to declare dependencies on other compilation
        units: COPY statements (copybook includes) and CALL statements (program invocations).
        These patterns are extracted here and fed into the BFS traversal queue in
        _gather_mfu_code() to ensure the full dependency graph is traversed.

        Note on CALL vs COPY:
            - CALL creates a runtime dependency on another executable COBOL program.
              It is already captured by the STAGE 4 regex fallback in analyze_artifact().
              It is included here as well to ensure the orchestrator's BFS queue
              (which operates on raw source text, not the analysis result) also picks
              it up without re-running the full analysis pipeline.
            - COPY creates a compile-time dependency on a copybook (.cpy). The target
              name maps directly to a .cpy artifact ID in the repository.

        Resolvers:
            1. resolve_copy_includes — Extracts copybook IDs from COPY / INCLUDE statements.
               Uses the same MASK_PATTERNS["precompiler_copy"] regex as analyze_artifact()
               to guarantee identical extraction logic in both paths.
            2. resolve_call_targets  — Extracts program IDs from CALL statements,
               consistent with COBOL_CALL_REGEX used in analyze_artifact().

        Returns:
            List[Callable[[str], List[str]]]: Two stateless resolver callables.
        """

        def resolve_copy_includes(content: str) -> list[str]:
            """Extracts copybook IDs from COPY and INCLUDE statements."""
            results = []
            for m in MASK_PATTERNS["precompiler_copy"].finditer(content):
                # Group 1 from precompiler_copy regex is the copybook target name
                target = m.group(1)
                if target:
                    # Normalize: strip any file extension (e.g., "REC.CPY" -> "rec")
                    stem = target.split(".")[0].lower()
                    if stem:
                        results.append(stem)
            # Deduplicate while preserving order
            return list(dict.fromkeys(results))

        def resolve_call_targets(content: str) -> list[str]:
            """Extracts program IDs from CALL statements."""
            return [m.group(1).lower() for m in COBOL_CALL_REGEX.finditer(content) if m.group(1)]

        return [resolve_copy_includes, resolve_call_targets]

    def strip_noise(self, content: str, file_ext: str) -> str:
        """Tier 2 COBOL noise stripper: removes ID DIVISION padding and comment lines."""
        import re as _re

        id_noise = _re.compile(
            r"^\s*(?:AUTHOR|DATE-WRITTEN|DATE-COMPILED|REMARKS|SECURITY|INSTALLATION)\b",
            _re.IGNORECASE,
        )
        free_comment = _re.compile(r"^\s*\*>")
        new_section = _re.compile(
            r"^\s*(?:[A-Z][A-Z0-9-]*\s+(?:DIVISION|SECTION)\b|[A-Z][A-Z0-9-]*\.?\s*$)",
            _re.IGNORECASE,
        )
        lines = content.splitlines(keepends=True)
        result = []
        blank_run = 0
        skip = False
        for line in lines:
            raw = line.rstrip("\r\n")
            if not raw.strip():
                blank_run += 1
                skip = False
                if blank_run <= 1:
                    result.append(line)
                continue
            blank_run = 0
            # Fixed-format comment: col 7 (index 6) is * or /
            if len(raw) >= 7 and raw[6] in ("*", "/"):
                continue
            if free_comment.match(raw):
                continue
            if id_noise.match(raw):
                skip = True
                continue
            if skip and new_section.match(raw):
                skip = False
            if skip:
                continue
            result.append(line)
        return "".join(result)

    # ---------------------------------------------------------
    # Tier 4 — Structural Summary (universal hook override)
    # ---------------------------------------------------------

    def extract_structural_summary(self, content: str, file_ext: str, char_budget: int) -> str:
        """
        Tier 4 COBOL structural extractor — DIVISION-aware.

        COBOL programs are partitioned into exactly four canonical divisions:
          IDENTIFICATION DIVISION  — program name, author, metadata
          ENVIRONMENT DIVISION     — file assignments, I/O control
          DATA DIVISION            — WORKING-STORAGE, FILE SECTION, LINKAGE SECTION
          PROCEDURE DIVISION       — executable code

        Strategy:
          Phase 1 — Segment: locate DIVISION headers via regex and slice the
                    content into a preamble (lines before the first DIVISION)
                    plus one segment per division.
          Phase 2 — Fill: emit segments in priority order within char_budget.
                    Priority: preamble → IDENTIFICATION → ENVIRONMENT →
                              DATA → PROCEDURE.
                    Entire divisions are either included or excluded —
                    never truncated mid-line.
          Phase 3 — Marker: append structured omission summary.
          Fallback: if no DIVISION markers are found (free-format or non-standard
                    COBOL), delegates to the base-class head/tail splitter.

        Contract (inherited from LanguagePluginBase):
          - Returns content as-is when len(content) <= char_budget.
          - Output length is always <= char_budget.
          - Deterministic and idempotent.

        Args:
            content    : Raw (or Tier-2-stripped) COBOL source text.
            file_ext   : Lowercase extension (e.g. ".cbl", ".cob", ".pco").
            char_budget: Maximum character count for the returned string.

        Returns:
            str: Structural summary guaranteed to fit within char_budget.
        """
        import re as _re

        if len(content) <= char_budget:
            return content

        # ── Phase 1: Segment at DIVISION boundaries ───────────────────────
        _DIV_RE = _re.compile(
            r"^\s*(IDENTIFICATION|ENVIRONMENT|DATA|PROCEDURE)\s+DIVISION\b",
            _re.IGNORECASE | _re.MULTILINE,
        )

        matches = list(_DIV_RE.finditer(content))
        if not matches:
            # No DIVISION markers — fall back to base-class head/tail splitter
            return super().extract_structural_summary(content, file_ext, char_budget)

        segments: list[tuple] = []  # (name_upper, text)

        # Preamble: everything before the first DIVISION header
        if matches[0].start() > 0:
            segments.append(("__PREAMBLE__", content[: matches[0].start()]))

        for idx, m in enumerate(matches):
            div_name = m.group(1).upper()
            start = m.start()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(content)
            segments.append((div_name, content[start:end]))

        # ── Phase 2: Fill budget in priority order ────────────────────────
        _PRIORITY = [
            "__PREAMBLE__",
            "IDENTIFICATION",
            "ENVIRONMENT",
            "DATA",
            "PROCEDURE",
        ]
        seg_map: dict[str, list[str]] = {}
        for name, text in segments:
            seg_map.setdefault(name, []).append(text)

        parts: list[str] = []
        used: int = 0
        omitted: list[str] = []

        for div in _PRIORITY:
            for text in seg_map.get(div, []):
                if used + len(text) <= char_budget:
                    parts.append(text)
                    used += len(text)
                else:
                    label = div if div != "__PREAMBLE__" else "PREAMBLE"
                    omitted.append(label)

        # Any unrecognised division names (future COBOL dialects)
        for name, texts in seg_map.items():
            if name not in _PRIORITY:
                for text in texts:
                    if used + len(text) <= char_budget:
                        parts.append(text)
                        used += len(text)
                    else:
                        omitted.append(name)

        # ── Phase 3: Omission marker ──────────────────────────────────────
        if omitted:
            marker = f"\n--- [TIER-4 OMITTED DIVISIONS: {', '.join(omitted)}] ---\n"
            # Ensure marker fits without breaching char_budget.
            while parts and len("".join(parts)) + len(marker) > char_budget:
                parts.pop()
            parts.append(marker)

        result = "".join(parts)
        return result if result else content[:char_budget]
