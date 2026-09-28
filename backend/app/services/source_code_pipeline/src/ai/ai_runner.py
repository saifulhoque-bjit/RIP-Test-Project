"""
AI Runner for MFU proposal generation.

Supports:
- Mock mode (default)
- Real LLM mode via provider-agnostic LLMClient (litellm backend)
- Schema validation enforcement
- Vocabulary sanitization via src.utils.vocabulary_sanitizer
- Full logging
- Robust JSON parsing and self-healing for large contexts
- Dynamic Paradigm Hydration via PluginRegistry
"""

import json
import os
from pathlib import Path
import re

from ..scanner.plugin_registry import PluginRegistry  # TASK 4.2: Decoupled Registry
from ..utils.json_schema_validator import JSONSchemaValidator, SchemaValidationError
from ..utils.json_utils import (
    extract_llm_json,
)
from ..utils.vocabulary_sanitizer import sanitize_mfu_vocabulary

try:
    from .llm_client import LLMClient
except ImportError:
    LLMClient = None


class AIRunner:
    def __init__(
        self,
        schema_path: Path,
        system_prompt_path: Path,
        generation_prompt_path: Path,
        use_mock: bool = True,
        api_key: str | None = None,
    ):
        """
        Parameters
        ----------
        schema_path, system_prompt_path, generation_prompt_path : Path
            Paths to the JSON schema and prompt files.
        use_mock : bool
            When True, skips LLM calls and returns synthetic data.

        Model selection is fully config-driven via project_config.json.
        Do NOT pass a model name here — change ``llm.active_provider`` and
        ``llm.providers[active].model`` in project_config.json instead.
        """
        self.schema_path = schema_path
        self.system_prompt_path = system_prompt_path
        self.generation_prompt_path = generation_prompt_path
        self.use_mock = use_mock

        # TASK 4.2: Bind the global registry to the runner and schema validator
        self.registry = PluginRegistry()
        self.validator = JSONSchemaValidator(schema_path, registry=self.registry)

        if not use_mock:
            if LLMClient is None:
                raise RuntimeError("LLMClient module not found. Ensure src.ai.llm_client exists.")

            # Enterprise Check: If no keys exist for any supported provider, fallback to mock mode
            has_any_key = any(
                os.getenv(k)
                for k in [
                    "OPENAI_API_KEY",
                    "ANTHROPIC_API_KEY",
                    "GEMINI_API_KEY",
                    "DEEPSEEK_API_KEY",
                ]
            )
            if not has_any_key and not api_key:
                print(
                    "[WARN] No AI Provider API Keys detected in environment. Falling back to Mock Mode."
                )
                self.use_mock = True
            else:
                # Use reasoning model (deepseek-v4-pro) for all MFU generation calls
                self.llm_client = LLMClient(use_reasoning_model=True, api_key=api_key)

    def _load_prompts(self) -> tuple[str, str]:
        sys_prompt = self.system_prompt_path.read_text(encoding="utf-8")
        gen_prompt = self.generation_prompt_path.read_text(encoding="utf-8")

        # TASK 4.2: Dynamically inject active vocabulary into prompts
        risk_flags = self.registry.get_all_risk_flags() or ["none"]
        archetypes = self.registry.get_all_archetypes() or ["unresolved"]

        if hasattr(self.registry, "get_all_execution_tracks"):
            tracks = self.registry.get_all_execution_tracks()
        else:
            tracks = ["UI-Track", "Batch-Track", "Mixed-Track"]

        # Safe injection (prevents crashing on raw JSON `{}` in prompts)
        sys_prompt = sys_prompt.replace("{risk_flags}", ", ".join(risk_flags))
        sys_prompt = sys_prompt.replace("{execution_tracks}", ", ".join(tracks))
        sys_prompt = sys_prompt.replace("{archetypes}", ", ".join(archetypes))

        gen_prompt = gen_prompt.replace("{risk_flags}", ", ".join(risk_flags))
        gen_prompt = gen_prompt.replace("{execution_tracks}", ", ".join(tracks))
        gen_prompt = gen_prompt.replace("{archetypes}", ", ".join(archetypes))

        return sys_prompt, gen_prompt

    # ------------------------------------------------------------------
    # JSON extraction helpers — delegate to src.ai.json_utils
    # (canonical implementations live there; all callers share one fix)
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_reasoning_wrapper(text: str) -> str:
        """
        Strips reasoning/thinking wrapper blocks emitted by reasoning models
        (DeepSeek-v4-pro, o1, claude-extended-thinking, etc.) before the
        actual JSON payload.

        Patterns removed:
          * <think>...</think>   — DeepSeek chain-of-thought wrapper
          * <thinking>...</thinking>  — Anthropic extended thinking
          * Any leading prose lines before the first { or [
        """
        # Remove explicit reasoning tags (greedy — handles multi-line blocks)
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL | re.IGNORECASE)
        return text

    @staticmethod
    def _balanced_extract(text: str, open_ch: str, close_ch: str) -> str | None:
        """
        Extracts the FIRST complete balanced {…} or […] block from text.

        Uses character-level traversal with proper handling of:
          * String literals — skips { } [ ] inside "quoted strings"
          * Escape sequences — correctly handles \" inside strings
          * Stops at the exact matching close character, ignoring any
            trailing content (prose, reasoning, second JSON objects, etc.)

        This is the enterprise replacement for rfind('}') which breaks when
        reasoning models append explanatory text containing {} after the JSON.

        Returns the extracted string slice, or None if no balanced block found.
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

    def _extract_json(self, text: str) -> dict:
        """
        Delegates to the shared 4-stage extraction pipeline in json_utils.
        Handles reasoning-model trailing prose, <think> blocks, code fences,
        and balanced brace extraction. See json_utils.extract_llm_json for
        the full algorithm description.
        """
        return extract_llm_json(
            text,
            caller_label="AI Runner",
            empty_error_checklist=(
                "  1. System prompt contains the word JSON (required for json_object mode).\n"
                "  2. Input payload size is within the model context window.\n"
                "  3. API key is valid and has remaining quota.\n"
                "  4. Check [LLMClient] finish_reason in the log above for the exact cause."
            ),
        )

    def _enforce_root_structure(self, result) -> dict:
        """Safety layer: wraps naked list output in {mfus: [...]} to prevent AttributeErrors."""
        if isinstance(result, list):
            print("[AI Runner] Auto-corrected root array hallucination.")
            return {"mfus": result}
        if not isinstance(result, dict):
            return {"mfus": []}
        return result

    def _enforce_id_format(self, result: dict) -> dict:
        """Safety layer: ensures MFU IDs comply with pattern ^MFU-[0-9]+$."""
        mfus = result.get("mfus", [])
        if not isinstance(mfus, list):
            return result
        for i, mfu in enumerate(mfus):
            if not isinstance(mfu, dict):
                continue
            current_id = str(mfu.get("id", ""))
            if not re.match(r"^MFU-[0-9]+$", current_id):
                mfu["id"] = f"MFU-{i + 1:03d}"
        return result

    # ------------------------------------------------------------------
    # Known MFU item properties (union of proposed + final schemas).
    # Any key returned by the LLM that is NOT in this set is considered a
    # JSON-generation artefact and will be merged back into 'rationale'.
    # ------------------------------------------------------------------
    _MFU_KNOWN_PROPERTIES: frozenset = frozenset(
        {
            "id",
            "name",
            "description",
            "execution_track",
            "artifacts",
            "traceability_chain",
            "complexity",
            "risk_flags",
            "ai_confidence",
            "rationale",
            "review",  # mfus_final.schema.json only
        }
    )

    def _repair_missing_fields(self, result: dict) -> dict:
        """
        Post-processing repair layer: recovers required MFU fields that the LLM
        accidentally embedded as prose inside other fields rather than emitting
        them as standalone JSON properties.

        Problem this addresses
        ──────────────────────
        The model is instructed to output ``ai_confidence`` as a separate numeric
        field (0.0–1.0).  However, on long, complex rationales it sometimes writes
        the value inline:

            "rationale": "...estimated confidence for this grouping is 0.86. ai_confidence=0.86"

        leaving ``ai_confidence`` absent from the MFU dict and triggering a hard
        ``SchemaValidationError: 'ai_confidence' is a required property``.

        Why ``_sanitize_extra_properties`` doesn't catch this
        ──────────────────────────────────────────────────────
        That method handles the *inverse* problem (extra top-level keys).  Here the
        key is *missing*, not extra — the value was never promoted to the JSON object
        level in the first place.

        Repair strategy — zero data loss, safe to run multiple times
        ─────────────────────────────────────────────────────────────
        For each MFU that is missing ``ai_confidence`` (or has it set to a
        non-numeric value):

          1. Scan the ``rationale`` string for embedded confidence patterns using
             a priority-ordered regex list.
          2. Extract the first matching float value.
          3. Clamp it to [0.0, 1.0] — occasionally the model writes "0.86" inside
             a percentage sentence yielding "86" which would fail the schema max.
          4. Remove the matched text fragment from ``rationale`` and set
             ``ai_confidence`` as a proper float on the MFU dict.
          5. Fall back to 0.75 if no pattern matches (conservative default —
             signals partial evidence rather than falsely high confidence).
        """
        # Ordered by specificity — most explicit pattern first.
        _CONFIDENCE_PATTERNS = [
            # "ai_confidence=0.86" or "ai_confidence: 0.86" (exact field name)
            re.compile(
                r"[.,;]?\s*ai_confidence\s*[=:]\s*(1\.0{1,3}|0\.\d{1,4}|\d{1,3}(?:\.\d{1,4})?)\s*\.?$",
                re.IGNORECASE,
            ),
            # "ai_confidence=0.86" anywhere mid-string
            re.compile(
                r"[.,;]?\s*ai_confidence\s*[=:]\s*(1\.0{1,3}|0\.\d{1,4}|\d{1,3}(?:\.\d{1,4})?)",
                re.IGNORECASE,
            ),
            # "estimated confidence … is 0.86" / "confidence for this grouping is 0.86"
            re.compile(
                r"(?:estimated\s+)?confidence\s+(?:for\s+\w+\s+grouping\s+)?is\s+(1\.0{1,3}|0\.\d{1,4})",
                re.IGNORECASE,
            ),
            # "confidence: 0.86" / "confidence = 0.86"
            re.compile(
                r"\bconfidence\s*[=:]\s*(1\.0{1,3}|0\.\d{1,4})",
                re.IGNORECASE,
            ),
        ]

        mfus = result.get("mfus", [])
        if not isinstance(mfus, list):
            return result

        for idx, mfu in enumerate(mfus):
            if not isinstance(mfu, dict):
                continue

            # Only repair if ai_confidence is missing or not a valid float
            existing = mfu.get("ai_confidence")
            if isinstance(existing, (int, float)) and 0.0 <= float(existing) <= 1.0:
                continue  # Already valid — nothing to do

            mfu_id = mfu.get("id", f"idx-{idx + 1}")
            rationale = mfu.get("rationale", "")
            if not isinstance(rationale, str):
                rationale = str(rationale)

            extracted_value: float | None = None
            cleaned_rationale = rationale

            for pattern in _CONFIDENCE_PATTERNS:
                m = pattern.search(rationale)
                if m:
                    raw_val = m.group(1)
                    try:
                        parsed = float(raw_val)
                        # Normalise percentages (e.g. "86" → 0.86) — values > 1 are %
                        if parsed > 1.0:
                            parsed = parsed / 100.0
                        extracted_value = max(0.0, min(1.0, round(parsed, 4)))
                        # Remove the matched fragment from rationale (clean output)
                        cleaned_rationale = pattern.sub("", rationale).rstrip(". ,;")
                        # Guarantee minLength: 10 after stripping
                        if len(cleaned_rationale) < 10:
                            cleaned_rationale = (
                                rationale  # Keep original if strip is too aggressive
                            )
                        print(
                            f"[AI Runner] [REPAIR] {mfu_id}: extracted ai_confidence={extracted_value} "
                            f"from rationale text (pattern: {pattern.pattern[:40]}...)"
                        )
                        break
                    except (ValueError, IndexError):
                        continue

            if extracted_value is None:
                extracted_value = 0.75
                print(
                    f"[AI Runner] [REPAIR] {mfu_id}: ai_confidence missing and not recoverable "
                    f"from rationale — defaulting to {extracted_value}."
                )

            mfu["ai_confidence"] = extracted_value
            mfu["rationale"] = cleaned_rationale

        return result

    def _sanitize_extra_properties(self, result: dict) -> dict:
        """
        Defensive sanitization layer: merges any unexpected top-level keys on
        MFU items back into the 'rationale' field before schema validation.

        Root cause this addresses
        ─────────────────────────
        The LLM writes SQL text containing identifiers like 'M_DEVZAIKOIO'
        inside a JSON string.  If the model accidentally terminates the string
        early (e.g. after 'SELECT MAX(' the opening parenthesis confuses the
        token stream), the SQL table name becomes a spurious JSON key:

            "rationale": "... SELECT MAX(",
            "M_DEVZAIKOIO": " raw SQL selecting MAX(...)"

        Because both schemas declare ``additionalProperties: false``, this
        triggers a hard SchemaValidationError.

        Repair strategy — zero data loss
        ─────────────────────────────────
        For each extra key found on an MFU item:
          1. Concatenate its value directly onto the existing 'rationale'
             string (values that started mid-word glue seamlessly).
          2. Remove the extra key from the MFU dict.

        The repaired rationale is identical to what the LLM *intended* to
        write — it's purely a JSON escaping artefact being undone.
        """
        mfus = result.get("mfus", [])
        if not isinstance(mfus, list):
            return result

        known = self._MFU_KNOWN_PROPERTIES
        for idx, mfu in enumerate(mfus):
            if not isinstance(mfu, dict):
                continue

            extra_keys = [k for k in list(mfu.keys()) if k not in known]
            if not extra_keys:
                continue

            mfu_id = mfu.get("id", f"idx-{idx + 1}")
            print(
                f"[AI Runner] [SANITIZE] {mfu_id}: merging {len(extra_keys)} "
                f"spurious key(s) back into rationale: {extra_keys}"
            )

            rationale = mfu.get("rationale", "")
            if not isinstance(rationale, str):
                rationale = str(rationale)

            for ek in extra_keys:
                extra_val = mfu.pop(ek)
                if isinstance(extra_val, str):
                    # Direct concatenation — restores the intended sentence.
                    rationale = rationale + extra_val
                else:
                    # Non-string value (unlikely but safe to serialise).
                    rationale = (
                        rationale + f" [RECOVERED:{ek}={json.dumps(extra_val, ensure_ascii=False)}]"
                    )

            # Guarantee minLength: 10 in case rationale was empty.
            if len(rationale) < 10:
                rationale = rationale + " [auto-recovered from JSON artefact]"

            mfu["rationale"] = rationale

        return result

    def _enforce_schema_enums(self, result: dict) -> dict:
        """
        Defensive Normalization Layer:
        Auto-corrects LLM hallucinations for strict enum fields (complexity,
        execution_track, risk_flags) before schema validation.
        Dynamically hydrated from PluginRegistry.
        """
        mfus = result.get("mfus", [])
        if not isinstance(mfus, list):
            return result

        complexity_map = {
            "low": "Simple",
            "moderate": "Medium",
            "high": "Complex",
            "extreme": "Critical",
            "severe": "Critical",
        }

        if hasattr(self.registry, "get_all_execution_tracks"):
            valid_tracks = set(self.registry.get_all_execution_tracks())
        else:
            valid_tracks = {"UI-Track", "Batch-Track", "Mixed-Track"}

        valid_risks = set(self.registry.get_all_risk_flags())
        default_track = (
            "Mixed-Track"
            if "Mixed-Track" in valid_tracks
            else (list(valid_tracks)[0] if valid_tracks else "Unknown")
        )

        track_map = {
            "ui": "UI-Track",
            "ui-track": "UI-Track",
            "frontend": "UI-Track",
            "batch": "Batch-Track",
            "batch-track": "Batch-Track",
            "backend": "Batch-Track",
            "mixed": "Mixed-Track",
            "mixed-track": "Mixed-Track",
            "hybrid": "Mixed-Track",
        }

        for mfu in mfus:
            if not isinstance(mfu, dict):
                continue

            comp = mfu.get("complexity", "")
            if isinstance(comp, str) and comp not in ["Simple", "Medium", "Complex", "Critical"]:
                mfu["complexity"] = complexity_map.get(comp.lower().strip(), "Medium")

            track = mfu.get("execution_track", "")
            if isinstance(track, str) and track not in valid_tracks:
                normalized = track_map.get(track.lower().strip(), default_track)
                mfu["execution_track"] = normalized if normalized in valid_tracks else default_track

            raw_risks = mfu.get("risk_flags", [])
            if isinstance(raw_risks, list):
                clean_risks = []
                for r in raw_risks:
                    r_clean = str(r).lower().replace(" ", "_").replace("-", "_")
                    if r_clean in valid_risks:
                        clean_risks.append(r_clean)
                    elif (
                        "db" in r_clean or "database" in r_clean or "sql" in r_clean
                    ) and "heavy_db_logic" in valid_risks:
                        clean_risks.append("heavy_db_logic")
                    elif (
                        "integration" in r_clean or "api" in r_clean
                    ) and "integration_risk" in valid_risks:
                        clean_risks.append("integration_risk")
                mfu["risk_flags"] = list(set(clean_risks))

        return result

    def _mock_generate(self, artifacts: dict) -> dict:
        """Mock generator for testing -- complies with RIP strict schemas."""
        return {
            "mfus": [
                {
                    "id": "MFU-001",
                    "name": "Legacy System Integration",
                    "description": "Orchestrates communication with local OS resources.",
                    "execution_track": "UI-Track",
                    "artifacts": ["n_os_bridge", "w_system_status"],
                    "traceability_chain": ["w_system_status", "n_os_bridge"],
                    "complexity": "Critical",
                    "risk_flags": ["external_dependency", "integration_risk"],
                    "ai_confidence": 0.95,
                    "rationale": "High-fidelity signals indicate direct Win32 API interactions anchoring off a system status UI.",
                }
            ]
        }

    # ------------------------------------------------------------------
    # Payload Slimming & Token-Budget Chunking
    # ------------------------------------------------------------------
    # Root cause of the 429 RateLimitError:
    #   gpt-5-mini on this OpenAI project tier is capped at 250 K tokens
    #   per request.  A single module with 267 artifacts at ~1,300 tokens/
    #   artifact = 347 K tokens -- 40% over the ceiling.
    #
    # Two-layer mitigation:
    #   1. Slim each artifact: strip fields the MFU prompt never reads.
    #      Typically cuts ~1,300 -> ~350-500 tokens/artifact.
    #   2. Auto-chunk: if slimmed payload still exceeds the safe per-request
    #      budget, split into batches, call the LLM once per batch, then
    #      merge all MFU arrays and renumber IDs globally.
    #
    # Fields retained (MFU prompt explicitly references):
    #   id, file_name, extension, type, signals, graph_metrics,
    #   calls, called_by, edges, landmine_details (incl. masked_regions),
    #   semantic.

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Conservative rough estimate: len/3.5 chars-per-token (OpenAI average is ~4)."""
        return int(len(text) / 3.5)

    def _slim_artifact(self, art: dict) -> dict:
        """
        Returns a projection of an artifact retaining only fields the MFU
        generation prompt needs:
          Retained  -- id, file_name, extension, type, signals, graph_metrics,
                       calls (<=20), called_by (<=20), edges, landmine_details
                       (metadata only — see masked_regions redaction below),
                       semantic
          Stripped  -- path, identity_heuristic, confidence,
                       original_legacy_type, graph, reuse

        masked_regions redaction (Solution C — defence layer 1)
        ─────────────────────────────────────────────────────────
        Raw SQL / code content inside landmine_details.masked_regions is
        replaced with structured metadata stubs.  The LLM receives the
        position, type, and size of each region but NEVER the raw SQL text.

        Why this matters:
          SQL identifiers like 'M_DEVZAIKOIO' (Japanese schema table names)
          inside a JSON-encoded string can confuse the model's output token
          stream, causing the string to close early and the identifier to
          appear as a spurious top-level JSON key.  By stripping the raw
          content at the input stage, we eliminate the root cause entirely
          rather than patching the symptom in post-processing.

        Information preserved for MFU reasoning:
          - masked_region_count  → LLM knows how many logic blocks exist
          - type / category      → LLM knows what kind of SQL/logic is present
          - char_count           → LLM knows the relative size / complexity
          - start / end offsets  → LLM can cite spatial coordinates in rationale
        """
        _strip = {
            "path",
            "identity_heuristic",
            "confidence",
            "original_legacy_type",
            "graph",
            "reuse",
        }
        slim = {k: v for k, v in art.items() if k not in _strip}

        # ── Trim oversized list fields ────────────────────────────────────
        for list_field in ("calls", "called_by"):
            if isinstance(slim.get(list_field), list) and len(slim[list_field]) > 20:
                slim[list_field] = slim[list_field][:20]

        # ── Redact raw SQL/code from masked_regions ───────────────────────
        landmine = slim.get("landmine_details")
        if isinstance(landmine, dict):
            raw_regions = landmine.get("masked_regions")
            if isinstance(raw_regions, list) and raw_regions:
                redacted_regions = []
                total_chars_removed = 0
                for region in raw_regions:
                    if not isinstance(region, dict):
                        redacted_regions.append(region)
                        continue
                    start = region.get("start", 0)
                    end = region.get("end", 0)
                    char_count = (
                        (end - start)
                        if (end and start)
                        else len(str(region.get("content", region.get("raw", ""))))
                    )
                    total_chars_removed += char_count
                    stub = {
                        "start": start,
                        "end": end,
                        "char_count": char_count,
                    }
                    # Preserve any non-content classification keys
                    for meta_key in ("type", "category", "kind", "label", "region_type"):
                        if meta_key in region:
                            stub[meta_key] = region[meta_key]
                    redacted_regions.append(stub)

                # Replace in-place; keep all other landmine_details fields
                slim["landmine_details"] = {
                    **{k: v for k, v in landmine.items() if k != "masked_regions"},
                    "masked_regions": redacted_regions,
                    "masked_region_count": len(redacted_regions),
                }
                if total_chars_removed > 0:
                    print(
                        f"    [SLIM] {art.get('id', '?')}: redacted "
                        f"{len(redacted_regions)} masked_region(s), "
                        f"removed {total_chars_removed:,} SQL/code chars from payload."
                    )

        return slim

    def _chunk_artifacts(self, slimmed_arts: list, token_budget: int) -> list:
        """
        Splits pre-slimmed artifact dicts into chunks whose serialised JSON
        size stays within token_budget.  Guarantees at least one artifact per
        chunk (degenerate guard).
        """
        chunks, current, current_tokens = [], [], 0
        for art in slimmed_arts:
            art_tokens = self._estimate_tokens(json.dumps(art, ensure_ascii=False))
            if current and (current_tokens + art_tokens) > token_budget:
                chunks.append(current)
                current, current_tokens = [art], art_tokens
            else:
                current.append(art)
                current_tokens += art_tokens
        if current:
            chunks.append(current)
        return chunks

    def _merge_mfu_batches(self, batch_results: list) -> dict:
        """
        Merges MFU arrays from multiple batch calls into a single manifest.
        IDs are renumbered globally and sequentially (MFU-001, MFU-002, ...)
        so downstream stages see a clean, collision-free namespace.
        """
        merged = []
        for batch in batch_results:
            merged.extend(batch.get("mfus", []))
        for i, mfu in enumerate(merged):
            mfu["id"] = f"MFU-{i + 1:03d}"
        return {"mfus": merged}

    def _call_llm_for_batch(self, artifact_batch: list, sys_prompt: str, gen_prompt: str) -> dict:
        """
        Executes one LLM call for a single batch of pre-slimmed artifacts and
        returns the parsed, structurally-healed MFU dict.  All structural
        healing layers are applied so each batch is independently valid.

        Retry policy for empty-content responses:
          Reasoning models (DeepSeek-v4-pro, o1, etc.) occasionally return an empty
          body with finish_reason='stop' due to transient API hiccups (load balancing,
          brief rate-limit flush, tool-call routing). This is not a logic error — a
          single retry resolves it in > 95% of cases.  We retry up to 2 times with
          exponential back-off (2 s, 4 s) before propagating the error.
        """
        import time as _time

        payload = {"artifacts_enriched": {"artifacts": artifact_batch}}
        user_msg = f"{gen_prompt}\n\nPAYLOAD:\n{json.dumps(payload, indent=2)}"

        _MAX_EMPTY_RETRIES = 2
        _BACKOFF_BASE = 2  # seconds

        for attempt in range(_MAX_EMPTY_RETRIES + 1):
            try:
                # Model selection, token limits, and temperature are resolved entirely
                # by LLMClient from project_config.json — no hardcoded values here.
                raw_content = self.llm_client.complete(
                    system_prompt=sys_prompt,
                    user_prompt=user_msg,
                    response_format={"type": "json_object"},
                )
                break  # successful response — exit retry loop

            except RuntimeError as e:
                err_msg = str(e)
                is_empty = "Empty/null content" in err_msg or "content_len=0" in err_msg
                if is_empty and attempt < _MAX_EMPTY_RETRIES:
                    wait = _BACKOFF_BASE * (2**attempt)  # 2 s, then 4 s
                    print(
                        f"[AI Runner] WARN: Empty response from LLM "
                        f"(attempt {attempt + 1}/{_MAX_EMPTY_RETRIES + 1}). "
                        f"Transient API issue — retrying in {wait}s..."
                    )
                    _time.sleep(wait)
                    continue
                # Non-empty error or retries exhausted — propagate
                raise

        print(f"[AI Runner] Raw LLM output preview (first 300 chars): {repr(raw_content[:300])}")

        try:
            raw_json = self._extract_json(raw_content)
        except Exception:
            # Persist the FULL raw output so the exact malformation is inspectable
            # (the console only shows a truncated preview). Best-effort — never mask
            # the original parse error.
            try:
                _dump_dir = getattr(self, "_raw_dump_dir", None)
                if _dump_dir is not None:
                    _dump_dir.mkdir(parents=True, exist_ok=True)
                    _dump = _dump_dir / "_raw_llm_failure.txt"
                    _dump.write_text(raw_content or "", encoding="utf-8")
                    print(f"[AI Runner] Full raw LLM output dumped → {_dump}")
            except Exception as _dump_err:
                print(f"[AI Runner] (could not write raw dump: {_dump_err})")
            raise
        result = self._enforce_root_structure(raw_json)
        result = sanitize_mfu_vocabulary(result)
        result = self._enforce_id_format(result)
        result = self._enforce_schema_enums(result)
        result = self._sanitize_extra_properties(result)  # <- heal JSON artefacts (extra keys)
        result = self._repair_missing_fields(result)  # <- heal missing required fields
        return result

    def run(self, artifacts_path: Path, output_path: Path, log_path: Path) -> dict:
        artifacts = json.loads(artifacts_path.read_text(encoding="utf-8"))

        # Where _call_llm_for_batch dumps the full raw output if JSON parsing fails.
        self._raw_dump_dir = output_path.parent

        if self.use_mock:
            print("[AI Runner] Operating in MOCK mode...")
            result = self._mock_generate(artifacts)
            result = self._enforce_root_structure(result)
            result = sanitize_mfu_vocabulary(result)
            result = self._enforce_id_format(result)
            result = self._enforce_schema_enums(result)
            result = self._sanitize_extra_properties(result)  # <- heal JSON artefacts
            result = self._repair_missing_fields(result)  # <- heal missing required fields
            self.validator.validate_data(result)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            return result

        sys_prompt, gen_prompt = self._load_prompts()

        # Step 1: Payload Slimming
        artifact_list = artifacts.get("artifacts", [])
        slimmed = [self._slim_artifact(a) for a in artifact_list]

        total_estimated_tokens = self._estimate_tokens(
            json.dumps({"artifacts": slimmed}, ensure_ascii=False)
        )
        print(
            f"[AI Runner] Payload: {len(slimmed)} artifacts | "
            f"~{total_estimated_tokens:,} estimated tokens after slimming."
        )

        # Step 2: Token Budget & Chunk Routing
        # Derive from provider config — no hardcoded constant.
        # DeepSeek v4-pro: 384,000  |  OpenAI gpt-4o: 128,000  |  etc.
        SAFE_CHUNK_BUDGET = self.llm_client.max_completion_tokens
        chunks = self._chunk_artifacts(slimmed, SAFE_CHUNK_BUDGET)
        n_chunks = len(chunks)

        if n_chunks == 1:
            print(
                f"[AI Runner] Single-pass mode -- {len(slimmed)} artifacts fit "
                f"within {SAFE_CHUNK_BUDGET:,}-token budget."
            )
        else:
            print(
                f"[AI Runner] Chunked mode -- {len(slimmed)} artifacts split into "
                f"{n_chunks} batches (budget={SAFE_CHUNK_BUDGET:,} tokens/batch)."
            )

        # Step 3: Execute
        batch_results = []
        for idx, chunk in enumerate(chunks, start=1):
            if n_chunks > 1:
                print(f"[AI Runner] Batch {idx}/{n_chunks}: {len(chunk)} artifacts...")
            batch_results.append(self._call_llm_for_batch(chunk, sys_prompt, gen_prompt))

        # Step 4: Merge & Renumber
        result = self._merge_mfu_batches(batch_results)
        if n_chunks > 1:
            print(
                f"[AI Runner] Merged {n_chunks} batches -> {len(result.get('mfus', []))} total MFUs."
            )

        # Step 4b: Final heal passes (safety net — per-batch already applied
        # these; catches any edge-cases introduced by merge/renumber).
        result = self._sanitize_extra_properties(result)
        result = self._repair_missing_fields(result)

        # ── Enterprise empty-MFU guard (Layer 2 of Option C resilience fix) ──────
        # An empty mfus[] list is a VALID response for infrastructure-only modules
        # (Win32 API wrappers, global variable holders, utility-only bas files, etc.).
        # The LLM correctly recognises these have no business MFUs.
        # The orphan_sweeper in main.py will create SYS-* coverage buckets downstream.
        # Crashing here would abandon all subsequent modules in the pipeline run.
        if not result.get("mfus"):
            print(
                "[AI Runner] WARN: LLM returned zero MFUs for this module.\n"
                "           This is expected for infrastructure-only modules "
                "(Win32 wrappers, utility bas files, global var holders).\n"
                "           Skipping schema validation — orphan sweeper will "
                "create SYS-* coverage buckets downstream."
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(result, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(
                "--- EMPTY MFU LIST (infrastructure-only module) ---\n\n"
                f"{json.dumps(result, indent=2)}",
                encoding="utf-8",
            )
            return result

        try:
            self.validator.validate_data(result)
        except SchemaValidationError as e:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(
                f"--- SCHEMA VALIDATION FAILED ---\n\n{e}\n\n"
                f"--- MERGED OUTPUT ---\n{json.dumps(result, indent=2)}",
                encoding="utf-8",
            )
            raise RuntimeError(
                f"AI Output failed schema validation:\n{e}\n\nSee {log_path} for full output."
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(f"--- SUCCESS ---\n\n{json.dumps(result, indent=2)}", encoding="utf-8")

        return result
