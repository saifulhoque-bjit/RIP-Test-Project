"""Common utility helpers shared across services."""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any
import unicodedata
import uuid

from app.utils.logger import get_logger

logger = get_logger(__name__)


def enforce_dev_only_skip_processing(requested: bool) -> bool:
    """Force ``skip_processing`` off outside development.

    ``skip_processing=True`` bypasses the LLM entirely and returns a
    hand-placed sample result — it exists purely for local iteration without
    burning tokens, and every sample_result/ file is dev-curated fixture
    data, not something that should ever reach a real project in staging or
    production. It's a public request field with no other gating (upload
    form, approve payload, regeneration payload), so this is the single
    shared chokepoint every skip_processing-capable pipeline entry point
    (the 5 rfp_pipeline_v2_graph_service runners) calls before honoring the
    flag — regardless of which route/service call chain reached it.
    """
    from app.core.config import settings  # noqa: PLC0415 — avoid import cycle at module load

    if requested and settings.APP_ENV != "development":
        logger.warning(
            "skip_processing requested but APP_ENV=%s — forcing real processing.",
            settings.APP_ENV,
        )
        return False
    return requested


def generate_short_uuid(length: int = 12) -> str:
    """Return a compact UUID string for human-friendly identifiers."""
    if length <= 0:
        raise ValueError("length must be greater than 0")
    return uuid.uuid4().hex[:length]


def normalize_filename(filename: str, max_stem_length: int = 50) -> str:
    """Normalize a client-supplied filename to a safe, storage-friendly string.

    Steps applied:
    1. Decompose Unicode characters (NFKD) and strip non-ASCII bytes.
    2. Replace any character that is not alphanumeric or ``_`` with ``_``.
    3. Collapse consecutive underscores into one.
    4. Strip leading / trailing underscores.
    5. Truncate the stem to *max_stem_length* characters.
    6. Rejoin with the sanitized, lower-cased extension.

    Args:
        filename: Raw client-supplied filename (basename only, no path).
        max_stem_length: Maximum characters allowed in the stem (default 50).

    Returns:
        Normalized filename safe for S3 keys and file systems.

    Examples:
        >>> normalize_filename("Hospital Management System Final Version (Updated).pdf")
        'Hospital_Management_System_Final_Version_Updated.pdf'
        >>> normalize_filename("  My__File  (v2)!.DOCX")
        'My_File_v2.docx'
    """
    filename = filename.strip()
    if not filename:
        return "file"

    # Separate stem and extension on the last dot.
    dot_index = filename.rfind(".")
    if dot_index > 0:
        stem = filename[:dot_index]
        ext = filename[dot_index + 1 :]
    else:
        stem = filename
        ext = ""

    # 1. Decompose Unicode → ASCII (e.g. é → e, ü → u, Chinese → dropped).
    stem = unicodedata.normalize("NFKD", stem)
    stem = stem.encode("ascii", "ignore").decode("ascii")

    # 2. Replace every non-alphanumeric / non-underscore character with '_'.
    stem = re.sub(r"[^\w]", "_", stem)

    # 3. Collapse consecutive underscores.
    stem = re.sub(r"_+", "_", stem)

    # 4. Strip leading / trailing underscores.
    stem = stem.strip("_")

    # 5. Fallback if nothing remains after normalization.
    if not stem:
        stem = "file"

    # 6. Truncate to the allowed stem length.
    stem = stem[:max_stem_length]

    # Sanitize extension: allow only alphanumeric chars, lower-case.
    ext = re.sub(r"[^a-zA-Z0-9]", "", ext).lower()

    return f"{stem}.{ext}" if ext else stem


def dump_json_debug(filename: str, data: Any, *, base_dir: str = "temp/debug") -> None:
    """Write *data* as pretty-printed JSON to *base_dir/filename* when DEBUG is enabled.

    No-ops silently when ``settings.DEBUG`` is ``False``, so call sites need
    no ``if DEBUG`` guard.  Handles dataclass / UUID / datetime objects via
    ``default=str``.

    Args:
        filename: Target file name, e.g. ``"backlog.json"``.
        data:     Any JSON-serialisable value (dict, list, str, …).
                  A raw JSON string is decoded first so the output is
                  always pretty-printed.
        base_dir: Directory relative to the working directory (created if absent).
    """
    from app.core.config import settings  # noqa: PLC0415

    if not settings.DEBUG:
        return

    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            pass

    target_dir = pathlib.Path(base_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / filename).write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
