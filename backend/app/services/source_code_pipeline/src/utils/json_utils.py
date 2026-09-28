"""
json_utils.py — Shared LLM JSON extraction utilities.

Provides a robust 4-stage extraction pipeline used by AIRunner,
ReviewAgent, and LLMEnricher. Centralising the logic ensures all
callers benefit from the same fix without code duplication.

Root cause of the original bug (kept here for audit trail):
  text.rfind('}') overshoots when reasoning models (DeepSeek-v4-pro,
  o1, Claude extended-thinking) append explanatory prose after the JSON
  payload. The prose often contains {} characters, causing rfind to
  land past the actual JSON end and returning a slice that fails to
  parse with "Extra data".

Fix: balanced brace extraction walks character-by-character tracking
depth, correctly ignoring { } inside string literals, stopping at the
exact matching closing brace regardless of trailing content.
"""

from __future__ import annotations

import json
import re

# Optional tolerant JSON repairer (parser-based, safe on string content — unlike
# regex hacks which corrupt valid strings). Used as a recovery stage for LLM output
# with unescaped quotes/newlines, trailing commas, or minor truncation. If not
# installed, extraction degrades gracefully to the deterministic stages below.
#   Install:  pip install json-repair
try:
    import json_repair as _json_repair
except Exception:  # pragma: no cover - optional dependency
    _json_repair = None


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def strip_reasoning_wrapper(text: str) -> str:
    """
    Removes chain-of-thought / extended-thinking wrapper blocks that
    reasoning models emit before the actual JSON payload.

    Patterns stripped (case-insensitive, multi-line):
      * <think>...</think>       — DeepSeek-v4-pro, Qwen-thinking
      * <thinking>...</thinking> — Anthropic extended thinking

    Returns the text with those blocks removed. If no blocks are found
    the original string is returned unchanged (zero overhead).
    """
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL | re.IGNORECASE)
    return text


def balanced_extract(text: str, open_ch: str, close_ch: str) -> str | None:
    """
    Extracts the FIRST complete balanced {…} or […] block from *text*.

    Algorithm: character-level depth counter that correctly handles:
      * String literals — { } [ ] inside "quoted strings" are skipped
      * Escape sequences — \\" inside strings does not end the literal
      * Stops at the EXACT matching close character, not rfind(close_ch)

    This replaces the fragile ``text[start:text.rfind(close_ch)+1]``
    pattern which overshoots when trailing prose contains close_ch.

    Parameters
    ----------
    text     : The full LLM output string to search.
    open_ch  : Opening character: '{' or '['.
    close_ch : Closing character: '}' or ']'.

    Returns
    -------
    The extracted slice (including open_ch and close_ch), or None if no
    complete balanced block exists in *text*.
    """
    start = text.find(open_ch)
    if start == -1:
        return None

    depth = 0
    in_str = False
    esc = False

    for i in range(start, len(text)):
        ch = text[i]

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

        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    return None  # Unbalanced — no complete block found


# ---------------------------------------------------------------------------
# Main extraction entry point
# ---------------------------------------------------------------------------


def extract_llm_json(
    text: str,
    caller_label: str = "LLM",
    empty_error_checklist: str | None = None,
) -> dict:
    """
    Robust 4-stage JSON extractor for raw LLM output.

    Handles the full spectrum of LLM output patterns across all supported
    providers (DeepSeek, OpenAI, Anthropic, Gemini) including reasoning
    models that append prose, emit thinking blocks, or wrap JSON in code
    fences.

    Stage 1 — Direct parse:
        Strips code fences, then attempts json.loads() directly.
        Fast path for well-formed output — zero overhead.

    Stage 2 — Strip reasoning wrappers:
        Removes <think>…</think> / <thinking>…</thinking> blocks that
        reasoning models emit before the JSON payload, then retries.

    Stage 3 — Balanced extraction:
        Uses balanced_extract() to find the exact matching closing brace
        for the first '{', ignoring all trailing content.
        Immune to rfind('}') overshoot caused by prose with {} chars.

    Stage 4 — CRITICAL failure:
        All strategies exhausted. Raises RuntimeError with a preview of
        the raw output and actionable diagnostic hints.

    Parameters
    ----------
    text                  : Raw LLM completion string.
    caller_label          : Short label for log messages (e.g. "AI Runner").
    empty_error_checklist : Optional extra checklist text for empty-response error.

    Returns
    -------
    Parsed dict. Raises RuntimeError on unrecoverable failure.
    """
    if not text or not text.strip():
        checklist = empty_error_checklist or (
            "  1. System prompt contains the word JSON (required for json_object mode).\n"
            "  2. Input payload size is within the model context window.\n"
            "  3. API key is valid and has remaining quota.\n"
            "  4. Check [LLMClient] finish_reason in the log above for the exact cause."
        )
        raise RuntimeError(
            f"CRITICAL: {caller_label} received an empty LLM response — cannot parse JSON.\n"
            f"Checklist:\n{checklist}"
        )

    # ── Stage 1: Direct parse ─────────────────────────────────────────────
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", cleaned, flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # ── Stage 2: Strip reasoning wrapper blocks and retry ─────────────────
    stripped = strip_reasoning_wrapper(cleaned).strip()
    if stripped != cleaned:
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

    # ── Stage 3: Balanced brace / bracket extraction ──────────────────────
    print(f"[WARN] {caller_label} JSON parsing failed. Attempting balanced extraction...")

    for source in (stripped, cleaned):
        # Object extraction ({…}) — most common root structure
        obj_slice = balanced_extract(source, "{", "}")
        if obj_slice:
            try:
                return json.loads(obj_slice)
            except json.JSONDecodeError:
                pass

        # Array extraction ([…]) — handles root-array hallucinations
        arr_slice = balanced_extract(source, "[", "]")
        if arr_slice:
            try:
                return json.loads(arr_slice)
            except json.JSONDecodeError:
                pass

    # ── Stage 3.5: Tolerant parser-based repair (json_repair, if installed) ──
    # Handles the defects the deterministic stages can't: unescaped quotes/newlines
    # inside string values, trailing commas, and best-effort completion of mildly
    # truncated output. Parser-based (not regex), so it never corrupts valid strings.
    if _json_repair is not None:
        for source in (stripped, cleaned):
            try:
                repaired = _json_repair.loads(source)
                if isinstance(repaired, (dict, list)) and repaired:
                    print(
                        f"[WARN] {caller_label} JSON recovered via json_repair "
                        f"(tolerant parse of malformed model output)."
                    )
                    return repaired
            except Exception:
                pass

    # ── Stage 4: CRITICAL ─────────────────────────────────────────────────
    # Show head AND tail: a syntax error shows in the head, a truncation shows in
    # the tail. This is usually enough to diagnose without a separate dump.
    _n = len(text)
    if _n > 1800:
        preview = f"{text[:1300]!r}\n    …[{_n - 1800} chars omitted]…\n    TAIL: {text[-500:]!r}"
    else:
        preview = repr(text)
    _repair_hint = (
        ""
        if _json_repair is not None
        else "\nHint: `pip install json-repair` to enable tolerant recovery of "
        "unescaped-quote / trailing-comma / minor-truncation defects."
    )
    raise RuntimeError(
        f"CRITICAL: {caller_label} output is structurally unrecoverable after recovery.\n"
        f"Output length: {_n} chars.\n"
        f"Output preview (head+tail): {preview}\n"
        f"Likely cause: malformed JSON (unescaped quote/newline in a string, or "
        f"truncation) that extraction could not repair.{_repair_hint}\n"
        f"Action: inspect the dumped raw output, verify the prompt instructs JSON-only output."
    )
