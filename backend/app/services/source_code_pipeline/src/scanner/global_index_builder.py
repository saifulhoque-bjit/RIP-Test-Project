# src/scanner/global_index_builder.py
from collections.abc import Callable
import json
from pathlib import Path
import re
from typing import Any

# ---------------- Configuration & Constants ----------------
# Directories to deprioritize during collision resolution
DEPRIORITIZE_DIRS = {"backup", "old", "temp", "test", "work", "migration_backup"}

# Common UI control prefixes to ignore when identifying top-level containers (PB/VB6)
CONTROL_PREFIXES = (
    "cb_",
    "dw_",
    "sle_",
    "m_ext_",
    "st_",
    "gb_",
    "uo_",
    "hpb_",
    "vpb_",
    "em_",
    "cmd",
    "txt",
    "lbl",
)


# ---------------- Identity Regex (Polyglot) ----------------
# PowerBuilder
OBJECT_DECL_REGEX = re.compile(
    r"(?:global\s+type|type)\s+(?P<id>[a-zA-Z_][a-zA-Z0-9_]*)\s+from\b", re.IGNORECASE
)
GLOBAL_FUNC_REGEX = re.compile(
    r"global\s+function\s+[a-zA-Z0-9_]+\s+(?P<id>[a-zA-Z_][a-zA-Z0-9_]*)\s*\(", re.IGNORECASE
)

# COBOL (Accounts for optional clauses before the period)
COBOL_PROG_ID_REGEX = re.compile(r"PROGRAM-ID\.\s+(?P<id>[A-Za-z0-9_\-]+)[.\s]", re.IGNORECASE)

# JCL (Must be MULTILINE to catch start of line)
JCL_JOB_REGEX = re.compile(
    r"^//(?P<id>[A-Za-z0-9_@#\$]{1,8})\s+JOB\b", re.IGNORECASE | re.MULTILINE
)

# VB6
VB6_ATTR_REGEX = re.compile(r'Attribute\s+VB_Name\s*=\s*"(?P<id>[A-Za-z0-9_]+)"', re.IGNORECASE)

# Java / Web Bridge
JAVA_CLASS_REGEX = re.compile(r"(?:public\s+)?(?:class|interface|enum)\s+(?P<id>[A-Za-z0-9_]+)\b")


# ---------------- Language Handlers ----------------


def handle_cobol(content: str, file_path: Path) -> tuple[str, str] | None:
    match = COBOL_PROG_ID_REGEX.search(content)
    if match:
        return match.group("id").lower(), "cobol_program_id"
    return None


def handle_jcl(content: str, file_path: Path) -> tuple[str, str] | None:
    match = JCL_JOB_REGEX.search(content)
    if match:
        return match.group("id").lower(), "jcl_job_name"
    return None


def handle_vb6(content: str, file_path: Path) -> tuple[str, str] | None:
    match = VB6_ATTR_REGEX.search(content)
    if match:
        return match.group("id").lower(), "vb6_vb_name"
    return None


def handle_java(content: str, file_path: Path) -> tuple[str, str] | None:
    match = JAVA_CLASS_REGEX.search(content)
    if match:
        return match.group("id").lower(), "java_class_name"
    return None


def handle_powerbuilder(content: str, file_path: Path) -> tuple[str, str] | None:
    func_match = GLOBAL_FUNC_REGEX.search(content)
    if func_match:
        return func_match.group("id").lower(), "pb_global_function"

    obj_match = OBJECT_DECL_REGEX.search(content)
    if obj_match:
        return obj_match.group("id").lower(), "pb_object_declaration"
    return None


# ---------------- The Identity Registry ----------------


class IdentityRegistry:
    """
    Dynamically maps file extensions to their specific identity extraction logic.
    Follows the Open-Closed Principle for adding new languages.
    """

    def __init__(self):
        # Native Python 3.10 type hinting for dicts and callables
        self._handlers: dict[str, Callable[[str, Path], tuple[str, str] | None]] = {}
        self.supported_extensions: set[str] = set()

    def register(self, extensions: set[str], handler_func: Callable):
        for ext in extensions:
            ext_lower = ext.lower()
            self._handlers[ext_lower] = handler_func
            self.supported_extensions.add(ext_lower)

    def extract(self, content: str, file_path: Path) -> tuple[str, str]:
        ext = file_path.suffix.lower()
        handler = self._handlers.get(ext)

        if handler:
            result = handler(content, file_path)
            if result:
                return result

        # Universal Fallback if no handler matches or is registered
        return file_path.stem.lower(), "filename_stem_fallback"


# Initialize the Global Registry
registry = IdentityRegistry()
registry.register({".cbl", ".cob", ".pco", ".cpy", ".sqb", ".inc", ".dcl"}, handle_cobol)
# NOTE: .bms is intentionally NOT registered here. The COBOL plugin gives BMS mapsets
# a special "<stem>_bms" id (to avoid colliding with the same-stem symbolic .cpy); a
# plain Stage-1a handler would emit the bare stem and mismatch. Aligning Stage-1a to
# that suffix is a tracked follow-up; .bms is still indexed via the manifest allow-list.
# IMS DL/I definitions (.psb/.dbd) carry no JOB card — handle_jcl returns None and
# the registry resolves them by file stem, matching the COBOL plugin's Stage-1b id
# for these members. Grouped with JCL as non-COBOL mainframe orchestration/data defs.
registry.register({".jcl", ".prc", ".proc", ".psb", ".dbd"}, handle_jcl)
registry.register({".frm", ".bas", ".cls", ".ctl", ".dsr", ".dob", ".pag"}, handle_vb6)
registry.register({".java", ".ts", ".tsx", ".sql"}, handle_java)
registry.register(
    {".srw", ".sru", ".srd", ".srm", ".sra", ".srj", ".srf", ".srs", ".srq", ".srp", ".srx"},
    handle_powerbuilder,
)
# .srf = global functions  (e.g. f_commit, f_connect — shared utility layer)
# .srs = structure objects (e.g. s_zaiko_parm — typed parameter structs)
# .srq/.srp/.srx = query / data-pipeline / proxy objects.
# Registry extensions are kept in sync with the plugin manifests so that (a) the
# Stage-1a identity id matches the Stage-1b plugin id, and (b) the fallback allow-
# list (used when build_global_index is called without allowed_extensions) does not
# drift from what the plugins actually analyze.


# ---------------- Utilities ----------------


def polyglot_read_text(file_path: Path) -> str:
    """
    Safely reads files across different legacy and modern encodings.

    ENTERPRISE FIX — BOM-first detection:
    PowerBuilder exports ALL source files as UTF-16 LE (BOM = 0xFF 0xFE).
    The previous strategy tried cp1252 before utf-16le. cp1252 silently
    "succeeds" on UTF-16 LE content (every byte maps legally) and returns
    null-interspersed garbage — the identity regexes never match and
    analyze_landmines runs on corrupted text for EVERY PB file.

    Fix: sniff the first 4 bytes for known BOMs before attempting character
    decoding.  Only non-BOM files fall through to the encoding probe chain.

    BOM table used:
      0xFF 0xFE        → UTF-16 LE   (PowerBuilder, some legacy Windows tools)
      0xFE 0xFF        → UTF-16 BE
      0xEF 0xBB 0xBF  → UTF-8 with BOM  (some Windows editors)
    """
    # ── Step 1: BOM sniff (read 4 bytes only — no full decode attempt) ────
    raw_head = file_path.read_bytes()[:4]
    if raw_head[:2] == b"\xff\xfe":
        # UTF-16 LE: Python's 'utf-16' codec auto-strips the BOM
        return file_path.read_text(encoding="utf-16")
    if raw_head[:2] == b"\xfe\xff":
        # UTF-16 BE: same codec, BOM-aware
        return file_path.read_text(encoding="utf-16")
    if raw_head[:3] == b"\xef\xbb\xbf":
        # UTF-8 BOM (common from Windows Notepad / some IDEs)
        return file_path.read_text(encoding="utf-8-sig")

    # ── Step 2: No BOM — probe common encodings ───────────────────────────
    # cp1252 deliberately placed AFTER utf-8 so it only handles true Windows
    # ANSI files, not mis-identified UTF-16 content.
    # cp932 (Shift-JIS) before cp1252/latin-1 so Japanese legacy identities decode
    # correctly; cp932 raises on non-JP byte sequences, so it only wins for genuine
    # Shift-JIS files. Without it, Japanese identities were silently mangled here. (#24)
    for enc in ("utf-8", "cp932", "cp1252", "latin-1"):
        try:
            return file_path.read_text(encoding=enc)
        except UnicodeError:
            continue

    raise ValueError(f"Unable to decode file with known encodings: {file_path.name}")


def analyze_landmines(content: str) -> dict[str, Any]:
    """
    Expanded polyglot analysis to find high-risk technical debt.
    FIXED: Mapped to safe Enterprise Risk Flags to prevent dynamic schema crashes.
    """
    flags = []
    score = 0
    upper_content = content.upper()

    # SQL Context (Cross-Paradigm)
    if "EXECUTE IMMEDIATE" in upper_content or "PREPARE SQLSA" in upper_content:
        flags.append("dynamic_sql")
        score += 3

    if "EXEC SQL" in upper_content:
        flags.append("embedded_sql")
        score += 2

    # Transaction Control (Cross-Paradigm & Mainframe CICS)
    if (
        "COMMIT" in upper_content
        or "ROLLBACK" in upper_content
        or "SYNCPOINT" in upper_content
        or "EXEC CICS" in upper_content
    ):
        flags.append("transaction_control")
        score += 3 if "EXEC CICS" in upper_content else 2

    # PowerBuilder / External Bridges
    if (
        "OLEOBJECT" in upper_content
        or "CREATE OLEOBJECT" in upper_content
        or ("EXTERNAL" in upper_content and "FUNCTION" in upper_content)
    ):
        flags.append("external_dependency")
        score += 4

    severity = "Low"
    if score >= 6:
        severity = "Critical"
    elif score >= 4:
        severity = "High"
    elif score >= 2:
        severity = "Medium"

    return {"flags": flags, "normalized_score": score, "severity": severity}


def calculate_confidence(record: dict[str, Any], path: Path) -> int:
    """
    Heuristic to determine how confident we are that THIS file is the true implementation.
    """
    score = 100
    path_parts = set(p.lower() for p in path.parts)

    # Penalize backup/temp directories
    if path_parts.intersection(DEPRIORITIZE_DIRS):
        score -= 50

    # Penalize fallback heuristics
    if record["identity_heuristic"] == "filename_stem_fallback":
        score -= 20

    # Penalize likely UI child controls if they bleed into the global index
    if record["id"].startswith(CONTROL_PREFIXES):
        score -= 30

    return max(0, score)


# ---------------- Orchestrator ----------------


def build_global_index(
    search_dir: Path,
    output_path: Path,
    allowed_extensions: set | None = None,
    exclude_matcher: Callable[[Path], bool] | None = None,
):
    """
    Scans the entire source directory for polyglot files and builds a single source of truth.
    Handles filename collisions by scoring file paths and heuristics via the Registry.

    allowed_extensions : the PARADIGM-SCOPED allow-list of file extensions to index,
        supplied by the caller as the active plugin manifest's supported_extensions
        (the single source of truth). When provided, Stage 1a indexes exactly the
        extensions the active plugin analyzes in Stage 1b — no cross-language
        over-inclusion, no drift from the local IdentityRegistry. When omitted (or
        empty), falls back to the IdentityRegistry's registered extensions so any
        legacy/standalone caller keeps working unchanged.
    """
    print(f"[INDEX] Starting polyglot global index build across: {search_dir}")

    allow = (
        {e.lower() for e in allowed_extensions}
        if allowed_extensions
        else registry.supported_extensions
    )

    candidate_pool: dict[str, list[dict[str, Any]]] = {}
    stats = {"scanned": 0, "indexed": 0, "collisions": 0, "errors": 0, "excluded_framework": 0}

    for p in search_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in allow:
            if exclude_matcher is not None and exclude_matcher(p):
                stats["excluded_framework"] += 1
                continue
            stats["scanned"] += 1
            try:
                # Use our new robust polyglot reader
                raw_content = polyglot_read_text(p)
            except Exception as e:
                print(f"[INDEX] Warning: Could not read {p.name}: {e}")
                stats["errors"] += 1
                continue

            # Delegate identity extraction to the Registry
            found_id, heuristic = registry.extract(raw_content, p)
            landmines = analyze_landmines(raw_content)

            rec = {
                "id": found_id.lower(),
                "path": str(p.resolve()),
                "file_name": p.name,
                "extension": p.suffix.lower(),
                "identity_heuristic": heuristic,
                "landmine_score": landmines["normalized_score"],
                "landmine_severity": landmines["severity"],
                "landmine_details": landmines,
            }
            rec["confidence"] = calculate_confidence(rec, p)

            candidate_pool.setdefault(found_id.lower(), []).append(rec)

    # Resolution Phase: If multiple files claim the same ID, highest confidence wins.
    final_artifacts = {}
    resolved_collisions = {}

    for key, candidates in candidate_pool.items():
        if len(candidates) > 1:
            candidates.sort(key=lambda x: x["confidence"], reverse=True)
            stats["collisions"] += 1
            resolved_collisions[key] = [c["path"] for c in candidates]

        winner = candidates[0]
        final_artifacts[key] = winner
        stats["indexed"] += 1

    manifest = {"artifacts": final_artifacts, "collisions_log": resolved_collisions, "stats": stats}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[INDEX] Success: {stats['indexed']} polyglot artifacts indexed.")
    if stats.get("excluded_framework"):
        print(
            f"[INDEX] Framework/library exclusion: skipped {stats['excluded_framework']} vendor-library file(s)."
        )
    if stats["collisions"] > 0:
        print(
            f"[INDEX] Warning: {stats['collisions']} identity collisions resolved by heuristic scoring."
        )
