"""
Legacy Code Sanitizer
Transforms raw, noisy legacy files into clean, UTF-8 encoded text for Tree-sitter parsing.
Handles EBCDIC/CP932 conversion, null-byte removal, and strict tab-expansion for mainframe alignment.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def decode_file(file_path: Path) -> str:
    """
    Attempts to decode a legacy file using a waterfall of common enterprise encodings.
    """
    if not file_path.exists() or file_path.stat().st_size == 0:
        return ""

    try:
        raw_bytes = file_path.read_bytes()
    except Exception as e:
        logger.error(f"[SANITIZER] Failed to read file {file_path}: {e}")
        return ""

    # Waterfall of likely encodings in a global enterprise context.
    # - utf-8-sig: Handles BOM without shifting columns.
    # - cp932: Enterprise superset of shift_jis (handles NEC/IBM extensions critical for Japanese legacy code).
    # - cp037: Standard IBM Mainframe EBCDIC.
    # - latin-1: Universal fallback for older European/ASCII legacy code.
    encodings = ["utf-8-sig", "cp932", "cp037", "latin-1"]

    for enc in encodings:
        try:
            return raw_bytes.decode(enc)
        except UnicodeDecodeError:
            continue

    # Ultimate fallback: replace unmappable characters to guarantee a string output
    logger.warning(
        f"[SANITIZER] All strict decodings failed for {file_path}. Using fallback replacement."
    )
    return raw_bytes.decode("utf-8", errors="replace")


def sanitize_fixed_format(source_text: str) -> str:
    """
    Prepares fixed-format code (like COBOL or JCL) for strict column-based Tree-sitter parsing.
    Instead of blanking out columns 1-6, we fix structural anomalies that break C-based external scanners.
    """
    if not source_text:
        return ""

    sanitized_lines = []

    # 1. Strip out null bytes (\x00) which can crash C-bindings in Tree-sitter
    source_text = source_text.replace("\x00", "")

    for line in source_text.splitlines():
        # 2. Expand tabs to 8 spaces.
        # CRITICAL: COBOL Area A is at column 8. Legacy terminal tabs align to 8, not 4.
        # Expanding to 4 would shift Area A logic into the sequence area, breaking the AST.
        line = line.expandtabs(8)

        # 3. Ensure trailing whitespace is clean but DO NOT modify columns 1-6.
        # Tree-sitter relies on them intact for exact diagnostic positioning.
        sanitized_lines.append(line.rstrip())

    # Ensure file ends with a newline, required by some grammar rules
    return "\n".join(sanitized_lines) + "\n"


def sanitize_source(file_path: Path, language_hint: str | None = None) -> str:
    """
    Main entry point for legacy sanitization.
    Reads the file, handles encoding, and applies language-specific noise reduction.
    """
    # 1. Decode safely
    source_text = decode_file(file_path)

    if not source_text:
        return ""

    # 2. Normalize line endings (CRLF/CR -> LF)
    source_text = source_text.replace("\r\n", "\n").replace("\r", "\n")

    # 3. Apply structural sanitization for fixed-format languages
    extension = file_path.suffix.lower()

    if extension in [".cbl", ".cob", ".pco", ".jcl"] or language_hint in ["cobol", "jcl"]:
        source_text = sanitize_fixed_format(source_text)
    else:
        # For free-form (VB6, modern scripts), clean null bytes and trailing spaces
        source_text = source_text.replace("\x00", "")
        # Ensure we don't return an empty string with a newline if the file was just whitespace
        lines = [line.rstrip() for line in source_text.splitlines()]
        source_text = "\n".join(lines) + "\n" if lines else ""

    return source_text
