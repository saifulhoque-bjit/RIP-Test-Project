from collections import Counter
import json
import os
from pathlib import Path
import re
import sys
import time

# --- TASK 4.3: Import PluginRegistry ---
from .src.scanner.plugin_registry import PluginRegistry

# -----------------------------------------------------------------------------
# Imports & Setup
# -----------------------------------------------------------------------------
try:
    from .src.ai.llm_client import LLMClient
except ImportError:
    LLMClient = None


# Forensic Logger to ensure overnight runs are captured to disk
class Logger:
    def __init__(self):
        self.terminal = sys.stdout
        self.log = open("extraction_debug.log", "a", encoding="utf-8")

    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)
        self.log.flush()

    def flush(self):
        # This flush method is needed for python 3 compatibility.
        pass


# ---------------------------------------------------------------------------
# Custom exception: permanent token-overflow (payload too large for the model).
# Raised by call_llm_api and caught by retry loops to trigger payload reduction.
# Distinct from transient rate limits (429 too-many-requests) which resolve by
# waiting — token overflow NEVER resolves with the same payload.
# ---------------------------------------------------------------------------
class TokenOverflowError(Exception):
    """Raised when the LLM refuses the request because the payload exceeds its
    token-per-request limit.  Retry with a smaller payload; waiting does not help."""

    pass


# Regex fragments used to classify LLM errors
_TOKEN_OVERFLOW_PHRASES = re.compile(
    r"too large|token.*(limit|exceed)|request too large|"
    r"max.*token|context.length|context_length_exceeded|"
    r"reduce.*token|input.*token.*must.*reduc",
    re.IGNORECASE,
)


def _is_token_overflow(exc: Exception) -> bool:
    """Returns True when *exc* is a permanent token-overflow error (not a transient rate limit)."""
    msg = str(exc).lower()
    # Must mention overflow/size AND not be a simple requests-per-minute limit
    has_overflow = bool(_TOKEN_OVERFLOW_PHRASES.search(msg))
    is_rpm_only = ("requests per min" in msg or "rpm" in msg) and "token" not in msg
    return has_overflow and not is_rpm_only


class SpecExtractor:
    def __init__(
        self,
        project_root,
        config_path,
        prompt_dir,
        source_dir,
        global_enriched_path=None,
        target_module=None,
        api_key=None,
    ):
        self.root = Path(project_root)
        self.config = self._load_json(Path(config_path))
        self.target_module = target_module.upper() if target_module else None

        # --- TASK 4.3: Bind the global registry ---
        self.registry = PluginRegistry()

        # --- ENTERPRISE FIX: Dynamic Extension Resolution ---
        # Fetch all supported extensions from the loaded plugins.
        # Fallback provided for standalone/offline testing without loaded plugins.
        exts = self.registry.get_all_supported_extensions()
        if not exts:
            exts = [
                ".srw",
                ".srd",
                ".sru",
                ".srf",
                ".srm",
                ".sra",
                ".srs",
                ".srq",
                ".pbl",
                ".cbl",
                ".cob",
                ".pco",
                ".cpy",
                ".jcl",
                ".sqb",
                ".bas",
                ".frm",
                ".cls",
                ".ctl",
                ".dsr",
            ]
        self.supported_extensions = tuple(exts)

        # --- PHASE 2 ENHANCEMENT: MANIFESTO INGESTION (Original Logic) ---
        # Capture project metadata and the modernization manifesto for strict adherence
        self.project_meta = self.config.get("project", {})
        self.target_stack = self.project_meta.get("target_stack", {})
        self.manifesto = self.config.get("modernization_manifesto", {})

        # --- PROMPT ARCHITECTURE V3: Cascade Loader ---
        # Prompts are now resolved per-MFU via _get_prompt(type, paradigm).
        # self.prompt_dir is the root; variants live in prompt_dir/variants/.
        # Static self.prompts dict is kept as a generic fallback (paradigm="generic").
        self.prompt_dir = Path(prompt_dir)
        self._prompt_cache: dict = {}  # Keyed by "{paradigm}::{prompt_type}"
        self.prompts = {
            "ui": self._load_template("ui_blueprint.txt"),
            "batch": self._load_template("batch_blueprint.txt"),
            "api": self._load_template("api_contracts.txt"),
        }

        # Initialize Source File Index
        self.source_dir = Path(source_dir)
        self.file_map = {}
        self._index_source_files()

        # --- NEW: GLOBAL METADATA INGESTION ---
        self.global_metadata = {}
        if global_enriched_path:
            global_path = Path(global_enriched_path)
            if global_path.exists():
                global_data = self._load_json(global_path).get("artifacts", [])
                self.global_metadata = {a["id"]: a for a in global_data if "id" in a}
                print(f"--- Loaded {len(self.global_metadata)} global artifacts for reference ---")
            else:
                print(f"[WARN] Global enriched file not found at {global_path}")

        # Initialize Provider-Agnostic LLM Client
        if LLMClient is None:
            print("[WARN] 'LLMClient' module not found. Extraction will run in OFFLINE mode.")
            self.llm_client = None
        else:
            # Enterprise Check: If no keys exist for any supported provider, fallback to offline mode
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
                    "[WARN] No AI Provider API Keys detected. Extraction will run in OFFLINE mode."
                )
                self.llm_client = None
            else:
                # Use the reasoning-heavy client for deep architectural specification.
                # SRS generation opts INTO the timeout circuit-breaker: a timeout here
                # counts toward the abort (this is one of the two large-context stages
                # the breaker guards).
                self.llm_client = LLMClient(use_reasoning_model=True, api_key=api_key)
                try:
                    self.llm_client.set_timeout_breaker_scope(True)
                except AttributeError:
                    pass  # older LLMClient without the breaker scope — no-op

    def set_target_module(self, module_name: str) -> None:
        """
        Reset the module filter without reconstructing the extractor.

        All expensive one-time initialisation (source file index, global metadata,
        PluginRegistry, config, LLM client) is already done.  Call this before
        run_all() to retarget the extractor at a different module in the same run.
        """
        self.target_module = module_name.upper() if module_name else None

    def _load_json(self, path):
        if not path.exists():
            print(f"[WARN] File not found: {path}")
            return {}
        with open(path, encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                print(f"[ERR] Failed to decode JSON: {path}")
                return {}

    def _load_template(self, filename):
        path = self.prompt_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Prompt template missing: {path}")
        return path.read_text(encoding="utf-8")

    def _get_prompt(self, prompt_type: str, paradigm: str) -> str:
        """
        PROMPT ARCHITECTURE V4: Option-A Fully-Independent Prompt Resolver.

        Resolution order (first match wins):
          1. Paradigm-specific standalone: prompts/{paradigm}/{prompt_type}.txt
             Each language has a complete, self-contained prompt built around
             its forensic patterns — no injection, no generic backbone wrapper.
          2. Universal core fallback:     prompts/{prompt_type}.txt
             Used for unknown/future paradigms not yet covered by a dedicated
             prompt directory.  The {language_specific_tiers} placeholder is
             silently replaced with an empty string to prevent raw placeholder
             text from leaking into the LLM context.

        Results are cached per (paradigm, prompt_type) pair so the filesystem
        is only hit once per unique combination during a run.

        Args:
            prompt_type: Prompt base name without extension
                         (e.g., "ui_blueprint", "api_contracts", "batch_blueprint").
            paradigm:    Lowercase paradigm/language name from the plugin registry
                         (e.g., "powerbuilder", "vb6", "cobol", or "" for generic).

        Returns:
            str: Complete prompt text ready for _safe_format().
        """
        cache_key = f"{paradigm}::{prompt_type}"
        if cache_key in self._prompt_cache:
            return self._prompt_cache[cache_key]

        composed = None

        # ── Option A: Try paradigm-specific standalone prompt ─────────────────
        if paradigm:
            standalone_path = self.prompt_dir / paradigm / f"{prompt_type}.txt"
            if standalone_path.exists():
                try:
                    composed = standalone_path.read_text(encoding="utf-8")
                    print(f"    [PROMPT] Loaded standalone prompt: {paradigm}/{prompt_type}.txt")
                except Exception as e:
                    print(f"    [WARN] Could not load standalone prompt {standalone_path}: {e}")

        # ── Fallback: Universal core template ─────────────────────────────────
        if composed is None:
            core = self._load_template(f"{prompt_type}.txt")
            # Silently remove the old injection placeholder so no raw {language_specific_tiers}
            # text leaks into the LLM context when falling back to the generic template.
            composed = core.replace("{language_specific_tiers}", "")
            if paradigm:
                print(
                    f"    [PROMPT] No standalone prompt for '{paradigm}/{prompt_type}'. "
                    f"Using universal core fallback: {prompt_type}.txt"
                )

        self._prompt_cache[cache_key] = composed
        return composed

    def _get_modernization_context(self):
        """
        Original logic to flatten the manifesto and target_stack into a flat
        dictionary for dynamic prompt injection. Enhanced with robust defaults.
        """
        ctx = {
            "project_id": self.project_meta.get("id", "N/A"),
            "project_name": self.project_meta.get("name", "N/A"),
            "client": self.project_meta.get("client", "N/A"),
            "frontend": "N/A",
            "backend": "N/A",
            "database": "N/A",
            "architecture": "N/A",
            "api_pattern": "REST",
            "orm_mapping": "N/A",
            "response_wrapper": "N/A",
        }

        # Merge target stack
        ctx.update(self.target_stack)

        # Flatten Manifesto Sections
        for section, rules in self.manifesto.items():
            if isinstance(rules, dict):
                ctx[section] = "\n".join([f"  - {k}: {v}" for k, v in rules.items()])
                ctx.update(rules)
            else:
                ctx[section] = rules
        return ctx

    def _index_source_files(self):
        """
        Walks the source directory ONCE to build a map of {filename: fullpath}.
        Uses dynamic extension resolution from the PluginRegistry.
        """
        print(f"--- Indexing Source Files in {self.source_dir} ---")
        if not self.source_dir.exists():
            print(f"[ERROR] Source directory does not exist: {self.source_dir}")
            return

        count = 0
        for root, _, files in os.walk(self.source_dir):
            for file in files:
                # Enterprise Fix: Uses dynamic self.supported_extensions tuple
                if file.lower().endswith(self.supported_extensions):
                    self.file_map[file.lower()] = Path(root) / file
                    count += 1
        print(f"--- Indexed {count} source files ---")

    def _read_smart(self, file_path):
        """
        Reads a legacy source file by trying multiple character encodings in sequence,
        stopping at the first successful decode that produces non-empty content.

        MULTI-LANGUAGE FIX (Sub-task 6A): Encoding order is now resolved dynamically
        from the PluginRegistry based on the file's extension, so each language plugin
        supplies its own preferred encoding list (e.g., UTF-16 first for PowerBuilder,
        cp037/EBCDIC last for mainframe COBOL) instead of applying a single global order
        that was previously biased towards PowerBuilder's UTF-16 LE format.

        Falls back to a generic ["utf-8", "cp1252", "latin-1"] order when no plugin
        claims the extension, and to lossy UTF-8 decoding as the final safety net.
        """
        # CJK/encoding fix (#24): the previous version looped the plugin's encoding
        # list and returned the FIRST that didn't raise. That mis-decoded Japanese
        # Shift-JIS (cp932) source two ways: (1) 'utf-16' was tried BLINDLY first and
        # an even-length cp932 byte stream decodes as UTF-16 *without raising* → CJK
        # mojibake (e.g. 'm_·0¹0Æ0à0'); (2) 'cp932' was absent, so it fell through to
        # cp1252/latin-1, which decode almost any bytes without raising → more garbage.
        #
        # This mirrors the proven pb_plugin.read_pb_text() strategy:
        #   1. UTF-16 ONLY when clearly indicated (BOM or high NUL-byte density) — never blind.
        #   2. UTF-8 strict (modern files / our own UTF-8 specs).
        #   3. Regional encodings STRICT (cp932, plus the plugin's list) — a correct
        #      encoding decodes cleanly while a wrong one RAISES, so the right one wins
        #      before the lossy Western fallbacks (cp1252/latin-1, which never raise).
        #   4. Lossy UTF-8 as the final never-crash safety net.
        ext = file_path.suffix.lower()
        plugin = self.registry.get_plugin_for_extension(ext)
        plugin_encs = plugin.get_preferred_encodings() if plugin else ["utf-8", "cp1252", "latin-1"]

        try:
            raw = file_path.read_bytes()
        except Exception:
            return ""
        if not raw:
            return ""

        # 1. UTF-16 only on strong evidence (BOM or NUL-byte density), never blindly.
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or raw.count(b"\x00") > max(1, len(raw) // 10):
            try:
                return raw.decode("utf-16")
            except Exception:
                pass
        # 2. UTF-8 BOM, then strict UTF-8.
        if raw[:3] == b"\xef\xbb\xbf":
            try:
                return raw.decode("utf-8-sig")
            except Exception:
                pass
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            pass

        # 3. Regional encodings, STRICT. 'cp932' (Shift-JIS) is appended universally so
        #    Japanese source in ANY language is decoded correctly; utf-16/utf-8 already
        #    handled above are skipped. Order preserves each plugin's own preferences.
        seen = set()
        for enc in [*plugin_encs, "cp932", "cp1252", "latin-1"]:
            el = (enc or "").lower()
            if el in ("utf-16", "utf-8", "utf-8-sig") or el in seen:
                continue
            seen.add(el)
            try:
                content = raw.decode(enc)  # strict: wrong encoding raises
                if content:
                    return content
            except (UnicodeDecodeError, LookupError):
                continue

        # 4. Lossy last resort — never crash a read.
        return raw.decode("utf-8", errors="ignore")

    def _resolve_datawindow_deps(self, content):
        """
        DEPRECATED (Sub-task 6C): This method is superseded by the plugin-dispatched
        dep resolver system. The equivalent logic now lives in PowerBuilderPlugin.get_dep_resolvers()
        and is invoked automatically by _gather_mfu_code() via registry.get_dep_resolvers().

        Retained as a no-op stub to preserve backward compatibility with any external
        callers or subclasses that may reference this method directly.
        """
        return []

    def _build_artifact_block(self, art_id, matched_path, raw_content, meta, status=None):
        """
        Assembles the XML-encapsulated artifact block injected into the LLM payload.
        Extracted from _gather_mfu_code so budget-tier logic can call it for both
        full-fidelity, noise-stripped, and Tier 4 structural-summary variants
        without duplication.

        Args:
            art_id       : Artifact identifier (lowercased in the tag).
            matched_path : Path object for the source file.
            raw_content  : Content string to embed (any tier).
            meta         : Artifact metadata dict (graph, signals, edges, landmines).
            status       : Optional status attribute written into the opening tag.
                           None  → no attribute (Tier 1 / Tier 2 full/stripped)
                           "STRUCTURAL_SUMMARY" → Tier 4 partial extraction
                           Other strings are also accepted for forward-compatibility.
        """
        role = meta.get("graph", {}).get("role", "helper")
        signals = json.dumps(meta.get("signals", {}))
        edges_data = meta.get("edges", [])
        masked_regions = meta.get("landmine_details", {}).get("masked_regions", [])

        parts = []
        if status:
            parts.append(f'\n<artifact id="{art_id.lower()}" status="{status}">')
        else:
            parts.append(f'\n<artifact id="{art_id.lower()}">')
        parts.append(f"--- FILE: {matched_path.name} ---")
        parts.append(f"METADATA: {{'role': '{role}', 'signals': {signals}}}")
        parts.append(f"COORDINATES (Spatial Traceability Map):\n{json.dumps(edges_data, indent=2)}")
        parts.append(f"CONTENT:\n{raw_content}")
        if masked_regions:
            parts.append("\n--- MASKED REGIONS (Proprietary Logic Extracted by Hybrid Parser) ---")
            for mr in masked_regions:
                cat = mr.get("category", "unknown").upper()
                mr_text = mr.get("raw_text", "")
                parts.append(f"[{cat}]:\n{mr_text}\n")
        parts.append("--- END FILE ---")
        parts.append("</artifact>\n")
        return "\n".join(parts)

    def _gather_mfu_code(self, artifact_ids, metadata_lookup, focal_artifact_id=None):
        """
        4-Tier Context Budget Resolution.

        Primary artifacts (artifact_ids) are always included at full fidelity —
        they are the MFU's core entrypoints and carry highest extraction priority.

        BFS-discovered dependency files are subject to the context budget:

          Tier 1 — fits remaining budget       → full fidelity
          Tier 2 — fits after noise stripping  → language-specific strip
          Tier 3 — (RETIRED) replaced by Tier 4 below
          Tier 4 — still too large after strip → plugin-dispatched structural
                   summary (plugin extracts the highest-value sections within
                   summary_budget_chars).  No file is ever fully invisible
                   to the spec LLM.

        summary_budget_chars (project_config.json llm.summary_budget_chars,
        default 20,000) caps the per-file Tier 4 summary.  It is independent
        of source_budget_chars and is always honoured even when the main
        budget is exhausted.

        A pre-flight diagnostic is printed before the BFS loop, summing the
        raw sizes of all primary artifacts and comparing against source_budget.
        This is informational only — Tier 4 fires unconditionally whenever a
        file would otherwise be omitted.

        Budget is read from project_config.json llm.source_budget_chars
        (default: 400,000 chars ≈ 200K tokens at 2 chars/token for Japanese).
        Primary artifacts consume budget freely; the limit only constrains deps.
        """
        # ── Read budgets from project config ──────────────────────────────
        _DEFAULT_BUDGET = 400_000  # ~200K tokens (Japanese DataWindow)
        _DEFAULT_SUMMARY_BUDGET = 20_000  # ~5K tokens per Tier 4 summary
        source_budget = self.config.get("llm", {}).get("source_budget_chars", _DEFAULT_BUDGET)
        summary_budget = self.config.get("llm", {}).get(
            "summary_budget_chars", _DEFAULT_SUMMARY_BUDGET
        )

        # ── Pre-flight diagnostic: sum primary artifact sizes ─────────────
        # Note: BFS deps are discovered lazily so this sum covers primaries
        # only.  It cannot guarantee budget prediction but gives an early
        # signal when a module's primaries alone will exhaust the budget.
        _primary_size_sum = 0
        for _pid in artifact_ids:
            _pmeta = metadata_lookup.get(_pid) or self.global_metadata.get(_pid, {})
            _ppara = _pmeta.get("paradigm_language", "")
            _pcands = {_pid.lower(): True}
            for _ext in self.supported_extensions:
                for _cand in self.registry.get_filename_candidates(_ppara, _pid, _ext):
                    _pcands[_cand.lower()] = True
            _ppath = next((self.file_map[c] for c in _pcands if c in self.file_map), None)
            if _ppath:
                try:
                    _primary_size_sum += _ppath.stat().st_size
                except OSError:
                    pass
        _budget_pressure = _primary_size_sum >= source_budget
        print(
            f"    [PREFLIGHT] Primary artifact size total: ~{_primary_size_sum:,} chars "
            f"({'≥' if _budget_pressure else '<'} budget {source_budget:,}). "
            f"Tier 4 structural summary "
            f"{'likely' if _budget_pressure else 'unlikely'} needed."
        )

        buffer = []
        processed_files = set()
        resolved_deps = set()
        primary_ids = set(a.lower() for a in artifact_ids)
        focal_id_lower = focal_artifact_id.lower() if focal_artifact_id else None
        queue = list(artifact_ids)
        chars_used = 0  # tracks budget consumed by ALL artifacts (focal exempt from tier-down)

        while queue:
            art_id = queue.pop(0)
            if art_id.lower() in processed_files:
                continue

            # Fetch metadata EARLY to inject the Spatial Traceability Map
            meta = metadata_lookup.get(art_id) or self.global_metadata.get(art_id, {})

            # Record AST dependencies for the graph, but DO NOT blindly append their raw code.
            # BUG-03 Fix B: Only add RESOLVED edges to resolved_deps.
            # Unresolved edges (resolved=false) point to external libraries and third-party
            # components (e.g., adodb, word.application, kernel32) that have no project source
            # file and no scanner metadata.  Adding them to resolved_deps caused them to leak
            # into full_artifact_list where _normalize_artifact_archetype's Stage-4 fallback
            # mistakenly classified them as ui_anchors, making them eligible for entrypoint
            # selection and producing specs for Microsoft drivers instead of business logic.
            for edge in meta.get("edges", []):
                tgt = edge.get("target")
                if tgt and edge.get("resolved", False):
                    resolved_deps.add(tgt)

            # --- MULTI-LANGUAGE FIX (Sub-task 6B): Registry-Driven Filename Candidates ---
            # Delegates prefix-naming logic to the plugin via registry broker.
            # PowerBuilder: adds w_/d_/u_/f_/m_ prefixed variants per extension.
            # VB6, COBOL: returns single unprefixed candidate per extension.
            # Future languages: automatically handled by their plugin's override.
            paradigm = meta.get("paradigm_language", "")
            seen_candidates = {}  # ordered-dict pattern: insertion-ordered dedup
            # Always try the bare art_id first (handles pre-prefixed IDs like "w_mainwindow")
            seen_candidates[art_id.lower()] = True
            for ext in self.supported_extensions:
                for cand in self.registry.get_filename_candidates(paradigm, art_id, ext):
                    seen_candidates[cand.lower()] = True
            candidates = list(seen_candidates.keys())

            matched_path = next(
                (self.file_map[c.lower()] for c in candidates if c.lower() in self.file_map), None
            )

            if matched_path:
                try:
                    processed_files.add(art_id.lower())
                    processed_files.add(matched_path.name.lower())

                    raw_content = self._read_smart(matched_path)

                    # --- MULTI-LANGUAGE FIX (Sub-task 6C): Registry-Driven Dep Resolution ---
                    for resolver in self.registry.get_dep_resolvers(paradigm):
                        try:
                            for dep_id in resolver(raw_content):
                                if dep_id and dep_id.lower() not in processed_files:
                                    queue.append(dep_id)
                                    dep_lower = dep_id.lower()
                                    dep_has_source = dep_lower in self.file_map or any(
                                        f"{dep_lower}{ext.lower()}" in self.file_map
                                        for ext in self.supported_extensions
                                    )
                                    if dep_has_source:
                                        resolved_deps.add(dep_lower)
                        except Exception as dep_err:
                            print(
                                f"    [WARN] Dep resolver error for {matched_path.name}: {dep_err}"
                            )

                    is_primary = art_id.lower() in primary_ids
                    is_focal = focal_id_lower is not None and art_id.lower() == focal_id_lower
                    file_ext = matched_path.suffix.lower()
                    content_len = len(raw_content)

                    if is_focal:
                        # FOCAL artifact for this entrypoint call: always full fidelity.
                        # Counts toward chars_used so subsequent primaries/deps are
                        # correctly budget-limited relative to this focal.
                        block = self._build_artifact_block(art_id, matched_path, raw_content, meta)
                        buffer.append(block)
                        chars_used += content_len
                        print(
                            f"    [BUDGET] {matched_path.name}: {content_len:,} chars — Tier 1 (FOCAL, full fidelity)"
                        )
                    elif is_primary:
                        # Non-focal primary: subject to the same 3-tier budget logic as deps.
                        # Prevents MOD-AZUHARA-style overflow when a single MFU has many
                        # large primary .srd/.srw files that together exceed the TPM ceiling.
                        remaining = source_budget - chars_used
                        if content_len <= remaining:
                            block = self._build_artifact_block(
                                art_id, matched_path, raw_content, meta
                            )
                            buffer.append(block)
                            chars_used += content_len
                            print(
                                f"    [BUDGET] {matched_path.name}: {content_len:,} chars — Tier 1 (primary, full fidelity)"
                            )
                        else:
                            stripped_content = self.registry.strip_noise(
                                paradigm, raw_content, file_ext
                            )
                            stripped_len = len(stripped_content)
                            saved = content_len - stripped_len
                            if stripped_len <= remaining:
                                block = self._build_artifact_block(
                                    art_id, matched_path, stripped_content, meta
                                )
                                buffer.append(block)
                                chars_used += stripped_len
                                print(
                                    f"    [BUDGET] {matched_path.name}: {content_len:,}\u2192{stripped_len:,} chars "
                                    f"(saved {saved:,}) \u2014 Tier 2 (primary, noise-stripped)"
                                )
                            else:
                                # \u2500\u2500 Tier 4: plugin-dispatched structural summary \u2500\u2500
                                # Never fully omit \u2014 extract the highest-value
                                # sections within the summary budget cap.
                                effective_summary_budget = (
                                    min(summary_budget, remaining)
                                    if remaining > 0
                                    else summary_budget
                                )
                                summary_content = self.registry.extract_structural_summary(
                                    paradigm,
                                    stripped_content,
                                    file_ext,
                                    effective_summary_budget,
                                )
                                summary_len = len(summary_content)
                                block = self._build_artifact_block(
                                    art_id,
                                    matched_path,
                                    summary_content,
                                    meta,
                                    status="STRUCTURAL_SUMMARY",
                                )
                                buffer.append(block)
                                chars_used += summary_len
                                print(
                                    f"    [BUDGET] {matched_path.name}: "
                                    f"{content_len:,} chars (stripped: {stripped_len:,}) "
                                    f"\u2014 Tier 4 (primary, structural summary: "
                                    f"{summary_len:,} chars, cap: {effective_summary_budget:,})"
                                )
                    else:
                        # Dependency files subject to 3-tier budget logic
                        remaining = source_budget - chars_used

                        if content_len <= remaining:
                            # Tier 1: fits as-is
                            block = self._build_artifact_block(
                                art_id, matched_path, raw_content, meta
                            )
                            buffer.append(block)
                            chars_used += content_len
                            print(
                                f"    [BUDGET] {matched_path.name}: {content_len:,} chars — Tier 1 (dep, full fidelity)"
                            )
                        else:
                            # Attempt Tier 2: noise-strip and re-evaluate
                            stripped_content = self.registry.strip_noise(
                                paradigm, raw_content, file_ext
                            )
                            stripped_len = len(stripped_content)
                            saved = content_len - stripped_len

                            if stripped_len <= remaining:
                                # Tier 2: stripped version fits
                                block = self._build_artifact_block(
                                    art_id, matched_path, stripped_content, meta
                                )
                                buffer.append(block)
                                chars_used += stripped_len
                                print(
                                    f"    [BUDGET] {matched_path.name}: {content_len:,}→{stripped_len:,} chars "
                                    f"(saved {saved:,}) — Tier 2 (noise-stripped)"
                                )
                            else:
                                # ── Tier 4: plugin-dispatched structural summary ──
                                # Never fully omit a dependency — extract the
                                # highest-value sections within the summary budget.
                                effective_summary_budget = (
                                    min(summary_budget, remaining)
                                    if remaining > 0
                                    else summary_budget
                                )
                                summary_content = self.registry.extract_structural_summary(
                                    paradigm,
                                    stripped_content,
                                    file_ext,
                                    effective_summary_budget,
                                )
                                summary_len = len(summary_content)
                                block = self._build_artifact_block(
                                    art_id,
                                    matched_path,
                                    summary_content,
                                    meta,
                                    status="STRUCTURAL_SUMMARY",
                                )
                                buffer.append(block)
                                chars_used += summary_len
                                print(
                                    f"    [BUDGET] {matched_path.name}: "
                                    f"{content_len:,} chars (stripped: {stripped_len:,}) "
                                    f"— Tier 4 (dep, structural summary: "
                                    f"{summary_len:,} chars, cap: {effective_summary_budget:,})"
                                )

                except Exception as e:
                    print(f"    [ERR] Could not read {matched_path.name}: {e}")

        return "".join(buffer), resolved_deps

    def call_llm_api(self, system_prompt, user_content, job_id, output_dir=None):
        """
        Sends a system + user message pair to the configured LLM provider.

        Features
        --------
        - Disk-based response cache (keyed by job_id) so re-runs skip paid
          API calls for already-completed steps.
        - Graceful offline mode when no API client is configured.
        - Model name resolved from project config with sane fallback.

        Returns
        -------
        str | None  -- LLM response text, or None on failure / offline mode.
        """
        # Cache check
        cache_file = None
        if output_dir:
            cache_file = Path(output_dir) / f"_cache_{job_id}.txt"
            if cache_file.exists():
                print(f"    [CACHE HIT] {job_id}")
                return cache_file.read_text(encoding="utf-8")

        # Offline guard
        if self.llm_client is None:
            print(f"    [OFFLINE] Skipping LLM call: {job_id}")
            return None

        try:
            # LLMClient resolves model, token limits, and temperature entirely from
            # project_config.json.  No provider names, model strings, or token counts
            # are referenced here — switching providers requires only a config change.
            active_model = self.llm_client.effective_model
            print(
                f"    [LLM] Calling {active_model} (provider: {self.llm_client.active_provider}) for {job_id}..."
            )

            result = self.llm_client.complete(
                system_prompt=system_prompt,
                user_prompt=user_content,
                # No max_tokens override — LLMClient reads token limits from project_config.json.
                # For reasoning models, LLMClient uses max_completion_tokens automatically.
            )

            # Tag specs recovered at REDUCED reasoning (adaptive timeout ladder)
            # with a non-destructive HTML comment, so a reviewer can optionally
            # regenerate them at full quality later. Only markdown specs are
            # tagged (JSON-bearing callers are unaffected).
            _rec = getattr(self.llm_client, "last_recovery", None)
            if result and _rec and _rec.get("degraded_reasoning"):
                result = result + (
                    f"\n\n<!-- RIP-RECOVERY: generated on retry {_rec.get('attempt')} "
                    f"at reduced reasoning_effort={_rec.get('reasoning_effort')} after "
                    f"timeout(s). Consider regenerating at full reasoning when the "
                    f"provider is healthy. -->\n"
                )

            if cache_file:
                try:
                    cache_file.write_text(result, encoding="utf-8")
                except Exception:
                    pass  # Cache write failure is non-fatal

            return result

        except Exception as e:
            # A circuit-breaker abort is a deliberate, run-level STOP — it must
            # NOT be swallowed into a None return (which would let the run
            # continue to the next item). Re-raise so it propagates out and ends
            # the run. (Checked by class name to avoid an import dependency.)
            if e.__class__.__name__ == "CircuitBreakerError":
                raise
            print(f"    [ERROR] LLM call failed for {job_id}: {e}")
            # Re-raise as TokenOverflowError so retry loops can apply payload
            # reduction instead of wasting retries on an identical oversized payload.
            if _is_token_overflow(e):
                raise TokenOverflowError(str(e)) from e
            return None

    @staticmethod
    def _bust_cache(job_id, output_dir):
        """Delete the on-disk response cache for job_id so the next call_llm_api
        regenerates instead of returning a cached (bad) output via [CACHE HIT]. (Item 1)"""
        if not output_dir or not job_id:
            return
        try:
            (Path(output_dir) / f"_cache_{job_id}.txt").unlink()
        except (OSError, FileNotFoundError):
            pass

    @staticmethod
    def _is_valid_spec(text) -> bool:
        """
        Item 1 — structural validity gate for a generated spec document.

        Distinguishes a real (possibly short) spec from a DEGENERATE LLM response —
        empty, truncated mid-document, or collapsed to only a JSON / naming-map block
        (the observed failure modes). STRUCTURAL, not length-based: a valid short spec
        passes; a long blob of only JSON fails.

        Rules (all must hold):
          1. Non-trivial: stripped length >= 400 chars.
          2. Not "only fenced code/JSON": after removing ```...``` fences, the
             remaining prose body must be substantive (>= 200 chars). Catches the
             JSON-only / naming-map-only collapse.
          3. Contains >= 1 structural spec marker (TIER n / PRE-FLIGHT / S.n /
             Screen Specification / Item Specification / Endpoint / Identity Header) —
             language- and pass-agnostic (covers UI, API and Batch specs).
        """
        if not text or not isinstance(text, str):
            return False
        t = text.strip()
        if len(t) < 400:
            return False
        # #35/#37 — Structured-JSON batch-spec acceptance. The batch blueprint contract may
        # emit a single ```json document (the universal/legacy batch prompt does). That is a
        # VALID structured spec, NOT the degenerate "JSON-only collapse" that rule 2 below
        # guards against. Accept it ONLY when it parses AND carries batch schema keys, so a
        # truncated or naming-map-only collapse still fails the gate.
        try:
            import json as _json

            _jm = re.search(r"```json\s*(\{.*\})\s*```", t, flags=re.DOTALL)
            _cand = _jm.group(1) if _jm else (t if t.startswith("{") else None)
            if _cand:
                _obj = _json.loads(_cand)
                if isinstance(_obj, dict) and any(
                    k in _obj for k in ("job_steps", "jcl_steps", "data_io_mapping")
                ):
                    return True
        except Exception:
            pass
        body = re.sub(r"```.*?```", "", t, flags=re.DOTALL).strip()
        if len(body) < 200:
            return False
        if not re.search(
            r"(?im)(TIER\s*\d|PRE-?FLIGHT|^\s*#{0,4}\s*S\.\d|Screen Specification"
            r"|Item Specification|Endpoint|Identity Header)",
            t,
        ):
            return False
        return True

    @staticmethod
    def _strip_scaffolding(text):
        """
        #22 — Remove known prompt-scaffolding artefacts that occasionally leak into
        generated spec markdown. Conservative & idempotent: each rule is an exact,
        anchored pattern; if nothing matches, the text is returned unchanged. Never
        touches inline code blocks or legitimate populated sections. On ANY error it
        returns the original text — cleanup must never break a write.
        """
        if not text or not isinstance(text, str):
            return text
        original = text
        try:
            import re as _re

            t = text

            # Rule 1 — strip a language-tagged fence (```markdown / ```md) that wraps
            # the ENTIRE document. Requires the tag, so a real ```json/code block at the
            # top is never mistaken for a wrapper.
            _m = _re.match(
                r"^\s*```(?:markdown|md)[ \t]*\r?\n(.*?)\r?\n?```[ \t]*\s*$",
                t,
                flags=_re.DOTALL,
            )
            if _m:
                t = _m.group(1)

            # Rule 2 — remove leaked 'FOCAL SCOPE MANDATE' instruction lines (prompt
            # machinery injected via entrypoint_focus; never legitimate spec content).
            t = _re.sub(r"(?im)^[ \t>*#-]*FOCAL SCOPE MANDATE\b.*(?:\r?\n)?", "", t)

            # Rule 4 — drop schema-template placeholder rows if they survived into an
            # emitted JSON block (degeneracy signal as well as noise).
            t = _re.sub(
                r'(?m)^[ \t]*"(?:LEGACY_FIELD_NAME|INSTRUCTION)"[ \t]*:.*?,?[ \t]*\r?\n',
                "",
                t,
            )

            # Rule 3 — drop a TRAILING 'PART 5' header that has an EMPTY body (header
            # followed only by whitespace / bare '#' until EOF). A populated PART 5
            # (e.g. a real machine-bridge JSON block) is left untouched.
            t = _re.sub(
                r"(?ims)\n#{1,6}[ \t]*PART 5\b[^\n]*\n(?:[ \t#]*\r?\n?)*\Z",
                "\n",
                t,
            )

            return t.rstrip() + "\n"
        except Exception:
            return original

    def _extract_and_remove_naming_map(self, text):
        """
        Extracts [NAMING_MAP] and its JSON block, returning (clean_text, naming_dict).
        GAP-04 FIX: When no [NAMING_MAP] marker is found (e.g. batch blueprint JSON output),
        falls back to _extract_naming_map_from_json_body() which reads the 'naming_map' key
        directly from the top-level JSON object produced by batch_blueprint.txt.
        """
        if not text:
            return text, {}

        marker_idx = text.find("[NAMING_MAP]")
        if marker_idx == -1:
            # GAP-04 FIX: Fallback for batch blueprint output — no [NAMING_MAP] marker present.
            batch_map = self._extract_naming_map_from_json_body(text)
            return text, batch_map

        start_idx = text.find("{", marker_idx)
        end_idx = text.rfind("}")

        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            json_str = text[start_idx : end_idx + 1]
            try:
                parsed = json.loads(json_str)
            except json.JSONDecodeError:
                parsed = {}
            # Return cleaned text without the naming map section
            clean_text = text[:marker_idx].strip()
            return clean_text, parsed

        return text, {}

    def _extract_naming_map_from_json_body(self, text):
        """
        GAP-04 FIX: Extracts the 'naming_map' key from a top-level JSON object produced by
        batch_blueprint.txt.  The batch prompt wraps its entire output in a ```json fence,
        so we locate that fence, parse the JSON, and return the 'naming_map' value.
        Returns an empty dict if extraction fails or the key is absent.
        """
        try:
            # Locate the opening ```json fence (or bare ```)
            fence_start = text.find("```json")
            if fence_start == -1:
                fence_start = text.find("```")
            if fence_start == -1:
                return {}

            # #21: use the string-aware balanced extractor so that '{' / '}' INSIDE
            # quoted strings do not prematurely open/close the object. The old naive
            # depth walker below ignored string literals and mis-parsed multi-line or
            # brace-containing values → "Unterminated string" and a silently-dropped
            # naming map. We fall back to the original walker if the helper is
            # unavailable, so behaviour can only improve, never regress.
            json_str = None
            try:
                from .src.utils.json_utils import balanced_extract

                json_str = balanced_extract(text[fence_start:], "{", "}")
            except Exception:
                json_str = None

            if json_str is None:
                brace_start = text.find("{", fence_start)
                if brace_start == -1:
                    return {}
                depth = 0
                brace_end = -1
                for idx in range(brace_start, len(text)):
                    if text[idx] == "{":
                        depth += 1
                    elif text[idx] == "}":
                        depth -= 1
                        if depth == 0:
                            brace_end = idx
                            break
                if brace_end == -1:
                    return {}
                json_str = text[brace_start : brace_end + 1]

            parsed = json.loads(json_str)

            raw_map = parsed.get("naming_map", {})
            if not isinstance(raw_map, dict):
                return {}

            # Strip the instructional placeholder key that we embed in the schema template
            return {
                k: v for k, v in raw_map.items() if k not in ("LEGACY_FIELD_NAME", "INSTRUCTION")
            }

        except Exception as e:
            print(f"    [WARN] _extract_naming_map_from_json_body failed: {e}")
            return {}

    def _safe_format(self, template, mapping):
        """
        Replaces {key} in template with mapping[key].
        Ignores braces that do not match keys in mapping, preventing 'Invalid format specifier'
        errors caused by literal JSON or complex Markdown tables.
        """

        def replace(match):
            key = match.group(1)
            if key in mapping:
                val = mapping[key]
                # TASK 2.2: Ensure fallback for a missing hint (or None) is an empty string
                if val is None:
                    return ""
                return str(val)
            # Unmatched keys are ignored and left intact (avoids JSON parsing crashes)
            return match.group(0)

        return re.sub(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", replace, template)

    def segregate_master_blueprint(self, master_path, output_dir):
        """
        Fan-Out function: Splits the Master file and prevents 'ghost' files.
        GAP-01 FIX: Parses the enriched [FILE_BREAK:ep|pfx] tag format and uses
        type-aware filename prefixes:
          UI    -> 1_UIBlueprint_<ep>.md
          Logic -> 1_LogicSpec_<ep>.md
          Batch -> 1_BatchSpec_<ep>.md
          (unknown) -> 1_Blueprint_<ep>.md  (backward-compatible fallback)
        """
        if not master_path.exists():
            return

        print(f"  > Executing Fan-Out Segregation in {output_dir.name}...")

        # GAP-01 FIX: Clean up all known spec prefixes, not just the old generic one.
        # PROMPT V3: Added 1_APISpec_* for shared_logic/data_provider artifacts.
        for prefix_glob in (
            "1_Blueprint_*.md",
            "1_UIBlueprint_*.md",
            "1_APISpec_*.md",
            "1_LogicSpec_*.md",
            "1_BatchSpec_*.md",
        ):
            for f in output_dir.glob(prefix_glob):
                if f.name != "1_Master_Blueprint.md":
                    try:
                        f.unlink()
                    except Exception as e:
                        print(
                            f"    [WARN] Could not delete old file {f.name}. It may be locked in an editor: {e}"
                        )

        # GAP-01 FIX: Map prompt-type token -> file prefix
        # PROMPT V3: Added "API" prefix for shared_logic/data_provider artifacts
        # routed to api_contracts in Pass 1 (replaces the retired "Logic" pfx).
        PREFIX_MAP = {
            "UI": "1_UIBlueprint_",
            "API": "1_APISpec_",
            "Batch": "1_BatchSpec_",
            "Logic": "1_LogicSpec_",  # Kept for backward compat with old output dirs
        }

        try:
            content = master_path.read_text(encoding="utf-8")
            pattern = r"\[FILE_BREAK:(.*?)\]"
            parts = re.split(pattern, content)

            if len(parts) < 3:
                print("    [WARN] No FILE_BREAK tags found in master file. Skipping segregation.")
                return

            split_count = 0
            for i in range(1, len(parts), 2):
                tag_value = parts[i].strip()  # e.g. "frmbatchrun|UI" or legacy "frmbatchrun"
                screen_content = parts[i + 1].strip()

                # GAP-01 FIX: Parse the optional "|pfx" suffix from the tag value.
                if "|" in tag_value:
                    screen_name, prompt_pfx = tag_value.split("|", 1)
                    screen_name = screen_name.strip()
                    prompt_pfx = prompt_pfx.strip()
                    file_prefix = PREFIX_MAP.get(prompt_pfx, "1_Blueprint_")
                else:
                    # Backward-compatible: old tags without a type suffix
                    screen_name = tag_value
                    file_prefix = "1_Blueprint_"

                # MULTI-LANGUAGE FIX (Sub-task 6F): Strip any registered extension from
                # the screen_name dynamically instead of hardcoding a fixed set of 6 extensions.
                # The registry supplies all extensions across all registered plugins, so new
                # languages are handled automatically without changes here.
                # Longest extensions are tried first to handle any overlapping suffix cases.
                safe_name = screen_name
                _all_exts = sorted(
                    self.registry.get_all_supported_extensions(), key=len, reverse=True
                )
                for _ext in _all_exts:
                    if safe_name.lower().endswith(_ext.lower()):
                        safe_name = safe_name[: -len(_ext)]
                        break  # Only strip one extension layer per name
                safe_name = re.sub(r'[\\/*?:"<>|]', "", safe_name)

                out_path = output_dir / f"{file_prefix}{safe_name}.md"
                try:
                    out_path.write_text(self._strip_scaffolding(screen_content), encoding="utf-8")
                    split_count += 1
                except Exception as e:
                    print(f"    [WARN] Could not write segregated file {out_path.name}: {e}")

            print(f"    [OK] Successfully segregated into {split_count} individual spec file(s).")
        except Exception as e:
            print(f"    [ERR] Segregation process failed: {e}")

    # --- TASK 4.3 HELPER: Determine Dominant Paradigm ---
    def _get_dominant_paradigm(self, artifact_list, metadata_lookup):
        """
        Determines the dominant language paradigm for a given MFU by reading the
        paradigm_language field emitted by each language plugin during scanning.

        Canonical values match plugin_name.lower() from each plugin's manifest:
          "powerbuilder"  — set by PowerBuilderPlugin  (prompts/powerbuilder/)
          "vb6"           — set by VB6Plugin            (prompts/vb6/)
          "cobol"         — set by COBOLPlugin           (prompts/cobol/)

        NOTE: The prompt resolver (_get_prompt) looks for
        prompts/{paradigm}/{prompt_type}.txt, so the paradigm string returned
        here MUST match the folder name under prompts/.

        Falls back to project_config source_paradigm if no artifact metadata is
        available.
        """
        paradigms = []
        for art_id in artifact_list:
            meta = self.global_metadata.get(art_id) or metadata_lookup.get(art_id, {})
            pl = meta.get("paradigm_language")
            if pl:
                paradigms.append(pl.lower())

        if paradigms:
            return Counter(paradigms).most_common(1)[0][0]

        return self.project_meta.get("source_paradigm", "unknown")

    # --- TASK 2.1 & 2.2: Dynamic Injection of Paradigm Hints ---
    def _normalize_artifact_archetype(self, a_type: str, file_suffix: str) -> str:
        """
        MULTI-LANGUAGE FIX (Sub-task 6E): Converts any artifact type identifier into
        a canonical EnterpriseArchetype string suitable for prompt-routing decisions.

        Resolution order (first match wins):
          1. Pass-through: value is already a valid EnterpriseArchetype — return as-is.
          2. Legacy compat map: translate pre-plugin type names (e.g., "pb_window" →
             "ui_anchor", "cobol_program_id" → "batch_anchor") for backward compatibility
             with project manifests written before the plugin system existed.
          3. Registry extension lookup: if a non-empty file_suffix is provided, ask the
             PluginRegistry for the archetype mapped to that extension. This handles new
             languages automatically without changes to this method.
          4. Fallback: return "ui_anchor" so unrecognised artifacts are routed to the
             UI Blueprint prompt (the safest default — it covers the most ground).

        Args:
            a_type:      The raw type string from artifact metadata (e.g., "ui_anchor",
                         "pb_window", "vb6_form", "unresolved", "").
            file_suffix: The physical file extension including the dot (e.g., ".frm",
                         ".sru"). May be empty string when not available.

        Returns:
            str: A canonical EnterpriseArchetype value from the set:
                 {"ui_anchor", "batch_anchor", "data_provider", "shared_logic",
                  "interface", "unresolved"}.
        """
        _VALID_ARCHETYPES = frozenset(
            [
                "ui_anchor",
                "batch_anchor",
                "data_provider",
                "shared_logic",
                "interface",
                "unresolved",
            ]
        )

        # Stage 1: already a canonical archetype
        if a_type.lower() in _VALID_ARCHETYPES:
            return a_type.lower()

        # Stage 2: legacy pre-plugin type names (backward compat only — do not extend)
        _LEGACY_TYPE_MAP = {
            "pb_window": "ui_anchor",
            "pb_datawindow": "data_provider",
            "pb_userobject": "shared_logic",
            "vb6_form": "ui_anchor",
            "cobol_program_id": "batch_anchor",
            "jcl_job_name": "batch_anchor",
            # Extension stems without dot (stored by older discovery passes)
            "frm": "ui_anchor",
            "ctl": "ui_anchor",
            "dsr": "data_provider",
            "cls": "shared_logic",
            "bas": "shared_logic",
            "sru": "shared_logic",
            "srf": "shared_logic",
            "cpy": "data_provider",
        }
        mapped = _LEGACY_TYPE_MAP.get(a_type.lower())
        if mapped:
            return mapped

        # Stage 3: registry extension lookup (handles all registered future languages)
        if file_suffix:
            ext_archetype = self.registry.get_archetype_for_extension(file_suffix)
            if ext_archetype and ext_archetype != "unresolved":
                return ext_archetype

        # Stage 4: BUG-03 Fix C — return "unresolved" instead of "ui_anchor" for unknown types.
        # The old default silently routed any artifact with an unrecognised type (including
        # external library references like adodb and word.application that leaked into
        # full_artifact_list) into the ui_anchor bucket.  This caused the entrypoint
        # fallback to pick an external library as the UI anchor for a module.
        #
        # "unresolved" is a valid canonical archetype (present in _VALID_ARCHETYPES).  The
        # fallback loop at line ~1152 only appends to windows/user_objects/datawindows/
        # batch_anchors for the four known archetypes — "unresolved" artifacts fall through
        # all four conditions and are silently excluded from entrypoint selection.  This is
        # the correct behaviour: an artifact of truly unknown type must not be auto-promoted
        # to a UI entry point.
        #
        # Real project artifacts with temporarily absent type metadata are protected by Fix D's
        # artifacts safety net, which adds all mfu['artifacts'] to actual_entrypoints directly.
        return "unresolved"

    def _get_paradigm_hints(self, active_paradigm_name, current_archetype=None):
        """
        Fetches the specific AI prompt hints for a paradigm from the PluginRegistry.
        If an archetype is not provided, aggregates all hints for the paradigm.
        """
        # TASK 2.2: Graceful Default string fallback
        if not active_paradigm_name or active_paradigm_name == "unknown":
            return ""

        if current_archetype:
            # MULTI-LANGUAGE FIX (Sub-task 6D): Two-stage archetype normalization.
            #
            # Stage 1 — Registry lookup (primary, forward-compatible):
            #   If current_archetype starts with "." it is a raw file extension.
            #   Ask the registry to resolve it to an EnterpriseArchetype directly.
            #   This handles any language registered in the future automatically.
            #
            # Stage 2 — Legacy compatibility map (fallback, backward-compatible):
            #   Existing project manifests written before the plugin system existed
            #   may still store old type names such as "pb_window" or "vb6_form".
            #   The _LEGACY_ARCH_MAP translates these to standard EnterpriseArchetype
            #   strings so the registry hint lookup always receives a valid key.
            #   New languages should NOT be added here — they belong in their plugin.
            _LEGACY_ARCH_MAP = {
                # PowerBuilder pre-plugin type names
                "pb_window": "ui_anchor",
                "pb_datawindow": "data_provider",
                "pb_userobject": "shared_logic",
                # VB6 pre-plugin type names
                "vb6_form": "ui_anchor",
                # Extension stems (without dot) used by older discovery passes
                "frm": "ui_anchor",
                "ctl": "ui_anchor",
                "dsr": "data_provider",
                "cls": "shared_logic",
                "bas": "shared_logic",
                # COBOL/JCL pre-plugin type names
                "cobol_program_id": "batch_anchor",
                "jcl_job_name": "batch_anchor",
            }

            arch_lower = current_archetype.lower()

            # Stage 1: extension-based registry resolution (e.g., ".frm", ".srw")
            if arch_lower.startswith("."):
                normalized_arch = self.registry.get_archetype_for_extension(arch_lower)
            else:
                # Stage 2: legacy name translation, falling through to pass-through
                normalized_arch = _LEGACY_ARCH_MAP.get(arch_lower, arch_lower)

            # TASK 2.1: Query the active plugin's hints using the registry
            hints = self.registry.get_hints_for_paradigm(active_paradigm_name, normalized_arch)

            # TASK 2.2: Ensure the fallback for a missing hint is an empty string ""
            return hints if hints else ""

        # Combine all available hints if archetype not specified (Pass 2 & 3)
        hints_list = []
        for arch in ["ui_anchor", "batch_anchor", "data_provider", "shared_logic", "interface"]:
            h = self.registry.get_hints_for_paradigm(active_paradigm_name, arch)
            if h:
                hints_list.append(f"- [{arch.upper()} HINTS]: {h}")

        return "\n".join(hints_list) if hints_list else ""

    # -----------------------------------------------------------------------------
    # Task 5.3: Deferred Semantic Hydration Post-Extraction Optimization Loop
    # -----------------------------------------------------------------------------
    def refine_module_manifest_names(
        self,
        modules_dir: Path,
        manifest_path: Path,
        module_filter: list = None,
        skip_classes: set = None,
    ):
        """
        Task 5.3 Enrichment Pass: Post-Extraction Semantic Dual-Property Rehydration.
        Sweeps config mapping JSON evidence tables to overwrite technical draft titles
        AND descriptions inside module_manifest.json using highly descriptive business domains.

        Args:
            modules_dir:    Root directory that holds per-module sub-folders.
            manifest_path:  Path to module_manifest.json.
            module_filter:  Optional list of module IDs to restrict processing to
                            (honours the --module CLI flag).  None = process all.
            skip_classes:   Optional set of module_class values to skip
                            (e.g. {"infrastructure"} to exclude MOD-SHARED).
                            None = no class-level exclusions.
        """
        if not manifest_path or not manifest_path.exists():
            print(f"[WARN] Manifest file not found for name refinement: {manifest_path}")
            return
        if not modules_dir or not modules_dir.exists():
            print(f"[WARN] Modules directory not found for name refinement: {modules_dir}")
            return

        with open(manifest_path, encoding="utf-8") as f:
            try:
                manifest_data = json.load(f)
            except Exception as e:
                print(f"[ERR] Failed to load manifest records for semantic refinement: {e}")
                return

        modules = manifest_data.get("modules", [])
        if not modules:
            print("[INFO] No modules found in manifest to refine.")
            return

        # Normalise caller-supplied filters once (case-insensitive)
        _allowed_ids = {m.upper() for m in module_filter if m} if module_filter else None
        _skip_cls = {c.lower() for c in skip_classes} if skip_classes else None

        scope_count = len(_allowed_ids) if _allowed_ids else len(modules)
        print(
            f"\n--- [REFINEMENT PHASE] Starting Deferred Semantic Hydration Pass on {scope_count} module(s) ---"
        )
        modified = False

        for module in modules:
            module_id = module.get("module_id", "")
            if not module_id:
                continue

            # Honour caller-supplied module_filter (--module CLI flag).
            # When a filter is provided only the listed IDs are rehydrated;
            # all others are silently skipped — their manifests are already correct
            # from previous full-run passes.
            if _allowed_ids is not None and module_id.upper() not in _allowed_ids:
                continue

            # Skip infrastructure / excluded module classes (e.g. MOD-SHARED).
            # These modules are fan-in catch-basins and carry no independent
            # business domain — refining their names adds no value.
            if _skip_cls and module.get("module_class", "").lower() in _skip_cls:
                continue

            # Legacy single-target guard (backwards compatibility with callers that
            # still use set_target_module() instead of passing module_filter).
            if self.target_module and module_id.upper() != self.target_module.upper():
                continue

            # Resolve module directory bindings with case-insensitive protection gates
            mod_folder = modules_dir / module_id
            if not mod_folder.exists():
                mod_folder = modules_dir / module_id.lower()
            if not mod_folder.exists():
                mod_folder = modules_dir / module_id.upper()
            if not mod_folder.exists():
                for p in modules_dir.iterdir():
                    if p.is_dir() and p.name.upper() == module_id.upper():
                        mod_folder = p
                        break

            if not mod_folder.exists():
                print(f"    [SKIP] Module folder not found: {module_id}")
                continue

            specs_dir = mod_folder / "stage4_specs"
            if not specs_dir.exists():
                continue

            # BUG-01 FIX: Build rich composite context from ALL available sources.
            # The old approach collected only behavior/behavioral_intent fields from naming maps.
            # With V3 architecture (logic_security.txt retired), LLM naming maps are simple
            # field→modernName mappings — no behavioral_intent key is present — so the old
            # guard always fired and skipped every module.  Fix: remove the pre-condition gate
            # and build context from entry points, field mappings, MFU names, and execution
            # track — all of which are always available and carry strong domain signals.

            behavior_evidence = []  # Tier-1: explicit behavioral_intent fields (highest signal)
            field_evidence = []  # Tier-2: field name mappings (domain vocabulary)
            mfu_evidence = []  # Tier-3: MFU output directory names (structural signal)
            files_found = 0

            for config_path in specs_dir.glob("**/config_naming_map.json"):
                files_found += 1
                try:
                    with open(config_path, encoding="utf-8") as cf:
                        map_data = json.load(cf)
                    naming_map = map_data.get("naming_map", {})

                    for item_key, item_val in naming_map.items():
                        if isinstance(item_val, dict):
                            behavior = item_val.get("behavior") or item_val.get("behavioral_intent")
                            modern_name = item_val.get("modern_name") or item_val.get("name", "")
                            if behavior and behavior.strip():
                                label = f" → '{modern_name}'" if modern_name else ""
                                behavior_evidence.append(
                                    f"- '{item_key}'{label}: {behavior.strip()}"
                                )
                            elif modern_name:
                                field_evidence.append(f"- Field '{item_key}' → '{modern_name}'")
                            else:
                                field_evidence.append(f"- Field: '{item_key}'")
                        elif isinstance(item_val, str) and item_val.strip():
                            field_evidence.append(f"- Field '{item_key}' → '{item_val.strip()}'")
                except Exception as e:
                    print(f"    [WARN] Could not parse config_naming_map {config_path.name}: {e}")

            # Collect MFU names from spec output directories (structural domain signal)
            for mfu_dir in specs_dir.iterdir():
                if mfu_dir.is_dir():
                    mfu_evidence.append(f"- MFU: {mfu_dir.name}")

            # Always proceed — no pre-condition gate.
            # Entry points from the manifest are the minimum viable context.
            entry_points = module.get("entry_points", [])
            execution_track = module.get("execution_track", "")

            context_parts = []
            if entry_points:
                context_parts.append(f"Entry Point Artifacts: {', '.join(entry_points)}")
            if execution_track:
                context_parts.append(f"Execution Track: {execution_track}")
            if mfu_evidence:
                context_parts.append("Functional Units Identified:\n" + "\n".join(mfu_evidence))
            if behavior_evidence:
                context_parts.append(
                    "Behavioral Evidence (High Confidence):\n" + "\n".join(behavior_evidence)
                )
            if field_evidence:
                context_parts.append(
                    "Data Domain Evidence (Field Mappings):\n" + "\n".join(field_evidence)
                )

            user_evidence = (
                "\n\n".join(context_parts) if context_parts else f"Module ID: {module_id}"
            )

            confidence_tier = (
                "HIGH" if behavior_evidence else ("MEDIUM" if field_evidence else "LOW")
            )
            print(
                f"    [REFINE] {module_id}: Confidence={confidence_tier} | "
                f"{files_found} naming map(s) | {len(entry_points)} entry point(s) | "
                f"{len(mfu_evidence)} MFU(s) | {len(field_evidence)} field(s)"
            )

            # Formulate structural JSON schema system directive constraints
            system_prompt = (
                "You are an expert Lead Modernization Architect and DDD Domain Expert.\n"
                "You will receive multi-tier context about a legacy system module: entry point artifact names,\n"
                "execution track, functional unit names, behavioral evidence, and data field mappings.\n"
                "Use ALL provided context signals — including artifact names and field vocabulary — to infer\n"
                "the true business domain and derive a clean, professional, business-aligned DDD module name and description.\n\n"
                "You MUST return your response as a valid raw JSON object matching this exact schema layout:\n"
                "{\n"
                '  "module_name": "Polished Business Domain Name (max 4 words)",\n'
                '  "description": "A crisp, maximum 2-sentence functional capability summary detailing business operations."\n'
                "}\n\n"
                "CRITICAL ENFORCEMENT RULES:\n"
                "1. Output valid raw JSON only. Do NOT embed introductory remarks, trailing analysis notes, conversational text, or markdown code fence blocks.\n"
                "2. Strip all legacy technical prefixes from artifact names (discard 'frm', 'cls', 'mod', 'bas', 'pb', 'cmd', 'btn', 'dw', 'logger') to reveal the business concept.\n"
                "3. Ensure both name and description capture high-level corporate capabilities "
                "(e.g., name: 'Payroll Bank Export Processing', description: 'Governs the automated extraction "
                "of End-of-Month payroll balances and orchestrates security-compliant ACH text file formatting "
                "for secure direct banking distribution networks.').\n"
                "4. If only entry point names are available, derive the domain from those artifact names alone — "
                "they are sufficient to infer business purpose (e.g., 'frmPayrollExport' → 'Payroll Export Management')."
            )

            if self.llm_client is None:
                print(
                    f"    [OFFLINE] Skipping semantic dual-property rehydration for module {module_id}"
                )
                continue

            try:
                # Enterprise Fix: model and token routing fully delegated to LLMClient.
                # The semantic rehydration task is small (JSON with 2 short fields),
                # so max_tokens=2048 is a generous but cost-effective output ceiling.
                refined_text = self.llm_client.complete(
                    system_prompt=system_prompt,
                    user_prompt=f"Extracted Business Functional Context Evidences:\n\n{user_evidence}",
                    max_tokens=2048,
                )
                refined_text = refined_text.strip()

                # Sanitize loose markdown code blocks cleanly avoiding truncation vectors
                if "```" in refined_text:
                    refined_text = refined_text.replace("```json", "").replace("```", "").strip()

                try:
                    parsed_refinement = json.loads(refined_text)
                    refined_name = parsed_refinement.get("module_name", "").strip().strip("'\"`[]")
                    refined_desc = parsed_refinement.get("description", "").strip()

                    if refined_name and refined_name != module.get("module_name"):
                        print(
                            f"[REFINEMENT] Upgrading module '{module_id}' semantic name from '{module.get('module_name')}' to '{refined_name}'"
                        )
                        module["module_name"] = refined_name
                        modified = True

                    if refined_desc and refined_desc != module.get("description"):
                        print(
                            f"[REFINEMENT] Upgrading module '{module_id}' semantic description to: '{refined_desc}'"
                        )
                        module["description"] = refined_desc
                        modified = True
                except Exception as parse_err:
                    print(
                        f"    [WARN] JSON load parse failure on {module_id}, deploying fallback regex extractors. Error: {parse_err}"
                    )

            except Exception as e:
                print(
                    f"[WARN] Semantic rehydration optimization pass failed for module {module_id}: {e}"
                )

        # Re-serialize clean hydrated updates to single source of truth manifest file
        if modified:
            try:
                manifest_path.write_text(
                    json.dumps(manifest_data, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                print(
                    f"[REFINEMENT] Single source of truth manifest successfully re-hydrated at: {manifest_path.name}"
                )
            except Exception as e:
                print(f"[ERR] Failed to save updated manifest records to disk: {e}")

    def _extract_primary_blueprint_context(self, p1_output, traceability_chain):
        """
        GAP-05 FIX: Extract only the primary anchor's blueprint section from the full
        p1_output (master_content) before injecting it as {master_ui_blueprint} into
        Pass 2 (API Contracts) and Pass 3 (Business Logic).

        Without this, p1_output contains ALL blueprint documents concatenated — for a
        Mixed-Track MFU with three entrypoints that can be 30-50 KB of markdown injected
        into P2/P3 in addition to the raw source code, risking context overflow and
        causing the LLM to summarise rather than extract.

        Strategy:
          1. Find the [FILE_BREAK:{primary_anchor}...] section in p1_output.
          2. Extract only that section.
          3. Strip APPENDIX blocks to further reduce size.
          4. If no FILE_BREAK found, return a hard-capped excerpt of p1_output.
        """
        if not p1_output or p1_output.strip() in ("", "N/A"):
            return "N/A"

        primary_anchor = traceability_chain[0] if traceability_chain else None

        section = None
        if primary_anchor:
            # FILE_BREAK tags now have the form [FILE_BREAK:ep|pfx]; the anchor is the
            # portion before the '|'.  Match either format.
            for candidate in (f"[FILE_BREAK:{primary_anchor}|", f"[FILE_BREAK:{primary_anchor}]"):
                marker_idx = p1_output.find(candidate)
                if marker_idx != -1:
                    # Skip to the end of the FILE_BREAK line
                    content_start = p1_output.find("\n", marker_idx)
                    if content_start == -1:
                        break
                    content_start += 1

                    # Find the next FILE_BREAK (any anchor) to delimit this section
                    next_break = p1_output.find("[FILE_BREAK:", content_start)
                    section = (
                        p1_output[content_start:next_break]
                        if next_break != -1
                        else p1_output[content_start:]
                    )
                    break

        if section is None:
            # No FILE_BREAK structure — cap the full output so P2/P3 aren't overwhelmed
            section = p1_output

        # Strip APPENDIX sections: they contain raw source literals that are already
        # available to P2/P3 via {artifacts_payload}.  Removing them reduces context size
        # significantly without losing any business-rule information.
        appendix_patterns = [
            "## APPENDIX A",
            "## APPENDIX B",
            "## APPENDIX C",
            "# APPENDIX A",
            "# APPENDIX B",
            "# APPENDIX C",
            "APPENDIX A:",
            "APPENDIX B:",
            "APPENDIX C:",
        ]
        for ap in appendix_patterns:
            ap_idx = section.find(ap)
            if ap_idx != -1:
                section = section[:ap_idx].strip()
                break

        # Hard cap: keep at most 8 000 chars (~2 000 tokens) to protect context budgets
        MAX_CHARS = 8000
        if len(section) > MAX_CHARS:
            section = (
                section[:MAX_CHARS]
                + "\n\n... [Primary blueprint truncated for context budget — full detail in Pass 1 spec file]"
            )

        return section.strip() or "N/A"

    @staticmethod
    def _is_generated_modules_dir(path: Path) -> bool:
        """True only if `path` is a pipeline-GENERATED modules root — i.e. it holds
        at least one module sub-folder carrying generated stage output
        (stage3_ai/ or module_config.json). This rejects same-named SOURCE folders
        that would otherwise hijack path resolution on case-insensitive filesystems
        — notably a VB6 'Modules/' source directory (full of .bas files), which
        matches 'modules' on Windows and previously caused Stage-4 specs to be
        written outside the canonical project modules tree (breaking Stage 5)."""
        try:
            if not path.is_dir():
                return False
            for child in path.iterdir():
                if child.is_dir() and (
                    (child / "stage3_ai").exists() or (child / "module_config.json").exists()
                ):
                    return True
        except Exception:
            return False
        return False

    def run_all(self, request_id=None):
        """Extract specs for every MFU of the target module.

        *request_id* is the cooperative-cancellation request id (see
        app/core/task_control.py) — checked before each MFU so a cancel
        requested mid-extraction stops within one MFU instead of running the
        whole module's remaining LLM calls. Optional: CLI/local runs pass
        nothing and no check is performed. Mirrors the same per-item pattern
        in ``feature_story_agent.run_for_module``.
        """
        project_id = self.project_meta.get("id", "sample_project")
        modules_dir = self.root / "projects" / project_id / "modules"

        # Dynamic Path Desync Resolution — handles mismatches between the config
        # ID and the CLI target folder. Validate the candidate is a REAL generated
        # modules dir (not merely a directory literally named 'modules'); otherwise
        # walk up from the source dir for one that IS. The validation is what makes
        # this robust against a source folder named 'Modules/' (e.g. VB6) colliding
        # with the generated 'modules' dir on a case-insensitive filesystem.
        if not self._is_generated_modules_dir(modules_dir):
            current_path = self.source_dir
            while current_path != current_path.parent:
                candidate = current_path / "modules"
                if self._is_generated_modules_dir(candidate):
                    modules_dir = candidate
                    print(
                        f"    [INFO] Config ID differs from CLI path. Resolved modules directory dynamically to: {modules_dir}"
                    )
                    break
                current_path = current_path.parent

        # Graceful degradation if a generated modules dir still can't be located
        if not self._is_generated_modules_dir(modules_dir):
            print(
                f"    [WARN] No generated modules directory found (searched from source: {self.source_dir}). Skipping extraction."
            )
            return

        modernization_ctx = self._get_modernization_context()

        for mod_path in modules_dir.iterdir():
            if not mod_path.is_dir():
                continue
            current_module_name = mod_path.name.upper()

            # --- NEW: Module Filtering Logic ---
            if self.target_module and current_module_name != self.target_module:
                continue

            mfu_file = mod_path / "stage3_ai" / "mfus_final.json"
            enriched_file = mod_path / "stage2_graph" / "artifacts_enriched.json"
            semantic_file = mod_path / "stage2b_semantic" / "artifacts_semantic.json"

            if not mfu_file.exists():
                continue

            mfus = self._load_json(mfu_file).get("mfus", [])
            enriched_data = self._load_json(enriched_file).get("artifacts", [])
            metadata_lookup = {a["id"]: a for a in enriched_data if "id" in a}

            # --- CRITICAL FIX: Merge Semantic Knowledge ---
            if semantic_file.exists():
                semantic_data = self._load_json(semantic_file).get("artifacts", [])
                for a in semantic_data:
                    art_id = a.get("id")
                    if art_id and art_id in metadata_lookup:
                        if "semantic" in a:
                            metadata_lookup[art_id]["semantic"] = a["semantic"]
                        if "signals" in a:
                            metadata_lookup[art_id].setdefault("signals", {}).update(a["signals"])

            output_dir = mod_path / "stage4_specs"
            output_dir.mkdir(exist_ok=True)

            print(f"\nProcessing Module: {current_module_name} ({len(mfus)} MFUs)")

            for mfu in mfus:
                if request_id:
                    from app.core import task_control  # noqa: PLC0415

                    if task_control.is_request_cancelled(request_id):
                        print(
                            f"    [CANCELLED] Module {current_module_name}: request cancelled "
                            f"— stopping spec extraction."
                        )
                        return

                mfu_id = mfu.get("id", "UNKNOWN_ID")
                execution_track = mfu.get("execution_track", "UI-Track")
                prefixed_mfu_name = f"{current_module_name} - {mfu.get('name', 'Unnamed MFU')}"

                # Format specific Phase 1 schema arrays for prompt injection
                traceability_chain_str = ", ".join(mfu.get("traceability_chain", []))
                risk_flags_str = ", ".join(mfu.get("risk_flags", []))

                print(f"\n--- [FORENSIC PIPELINE] {mfu_id} [{execution_track}] ---")

                # --- NEW: Create MFU specific folder for 3-File Architecture ---
                mfu_output_dir = output_dir / mfu_id
                mfu_output_dir.mkdir(exist_ok=True)

                # 1. Gather Raw Code with Forensic Dependency Resolution (Needed for P2/P3)
                combined_code, resolved_files = self._gather_mfu_code(
                    mfu.get("artifacts", []), metadata_lookup
                )
                original_len = len(combined_code)

                if original_len < 50:
                    print(f"    [WARN] No source code found for {mfu_id}")
                    continue

                shared_context = "{}"
                p1_output, p2_output, p3_output = None, None, None

                # Generate MFU-Specific Legacy Source Reference Table
                full_artifact_list = list(set(mfu.get("artifacts", []) + list(resolved_files)))
                references_rows = []
                for art_id in full_artifact_list:
                    meta = self.global_metadata.get(art_id) or metadata_lookup.get(art_id, {})
                    abs_path_str = meta.get("path", "")
                    art_type = meta.get("type", "unknown")
                    role = meta.get("semantic", {}).get("role") or meta.get("graph", {}).get(
                        "role", "helper"
                    )

                    rel_path = "N/A"
                    if abs_path_str:
                        try:
                            rel_path = os.path.relpath(abs_path_str, self.source_dir).replace(
                                "\\", "/"
                            )
                        except:
                            rel_path = os.path.basename(abs_path_str)

                    references_rows.append(f"| {art_id} | {art_type} | {rel_path} | {role} |")

                # --- Determine Dominant Paradigm for the entire MFU ---
                dominant_paradigm = self._get_dominant_paradigm(full_artifact_list, metadata_lookup)

                # --- DAY 5 TASK 14: PASS 1 SOVEREIGN ANCHOR ROUTING ---
                print(
                    f"  > PASS 1: Blueprint Extraction (Track: {execution_track} | Paradigm: {dominant_paradigm})"
                )

                # BUG-03 Fix D: Robust traceability chain parser + artifacts safety net.
                # -----------------------------------------------------------------------
                # PROBLEM (schema mutation by Review Agent):
                #   The MFU generation LLM correctly outputs plain artifact IDs:
                #     traceability_chain: ["frmrunpayroll"]
                #   But the Review Agent rewrites them to narrative edge strings:
                #     "frmrunpayroll (ui_anchor, entry_point)"
                #     "frmrunpayroll -> clspayslipbuilder (vb6_instantiation@34:23, resolved=true)"
                #     "clspayrollengine -> adodb (vb6_instantiation@29:20, resolved=false)"
                #   The old exact-match filter:
                #     [ep for ep in raw_entrypoints if ep.lower() in full_artifact_lower]
                #   fails for ALL narrative formats → actual_entrypoints = [] → fallback fires
                #   → adodb (external library) gets selected as the UI anchor.
                #
                # FIX — two layers:
                #
                # Layer 1 — Regex parser: extract the SOURCE (left-hand side) artifact ID
                #   from each chain entry, handling both plain IDs and narrative strings.
                #   For "A -> B (...)", only A is extracted (caller); B is a dependency
                #   target, not an entrypoint.  External refs (adodb, word.application)
                #   appear only on the RIGHT side of "->", so they are never extracted.
                #   Filter to IDs that actually exist in full_artifact_list (case-insensitive).
                #
                # Layer 2 — Artifacts safety net: any artifact in mfu['artifacts'] that
                #   exists in full_artifact_list but was NOT captured by the chain parse
                #   (e.g., clssecurity omitted from the chain by the Review Agent) is
                #   appended.  This guarantees 100% artifact coverage regardless of chain
                #   format or completeness.
                #
                # The degraded fallback below is retained as a true last resort for the
                # pathological case where BOTH the chain and the artifacts list are empty
                # or contain no matchable IDs.  Fix C ensures that artifacts of unknown
                # type (external libs) are classified as "unresolved" and excluded from
                # the windows/user_objects buckets even if they somehow reach the fallback.

                raw_entrypoints = mfu.get("traceability_chain", [])
                full_artifact_lower = [a.lower() for a in full_artifact_list]

                # Layer 1: regex extraction — source ID only, dedup-with-order preserved
                _chain_re = re.compile(r"^([a-zA-Z0-9_]+)")
                seen_ep_ids = {}  # ordered-dict pattern: insertion-order dedup
                for entry in raw_entrypoints:
                    m = _chain_re.match(entry.strip())
                    if m:
                        extracted = m.group(1).lower()
                        if extracted in full_artifact_lower:
                            seen_ep_ids[extracted] = True

                # Layer 2: artifacts safety net — add uncaptured MFU artifacts
                for _art in mfu.get("artifacts", []):
                    _art_lower = _art.lower()
                    if _art_lower in full_artifact_lower and _art_lower not in seen_ep_ids:
                        seen_ep_ids[_art_lower] = True

                actual_entrypoints = list(seen_ep_ids.keys())

                # Last-resort fallback (rarely fires after Fix D above).
                # Triggered only when both the chain and artifacts list yield nothing
                # matchable — e.g., a completely malformed MFU record with no artifacts
                # present in the module's code index.
                # Fix C guarantees "unresolved" artifacts (external libs) are excluded
                # from all classification buckets, so the fallback cannot pick them.
                if not actual_entrypoints:
                    windows, user_objects, datawindows, batch_anchors = [], [], [], []
                    for art_id in full_artifact_list:
                        meta = self.global_metadata.get(art_id) or metadata_lookup.get(art_id, {})
                        a_type = meta.get("type", "").lower()
                        # MULTI-LANGUAGE FIX (Sub-task 6E): Normalize a_type to a canonical
                        # EnterpriseArchetype so both modern plugin archetypes ("ui_anchor")
                        # and legacy pre-plugin type names ("pb_window", "vb6_form") are
                        # handled uniformly.  Fix C ensures unknown types return "unresolved"
                        # and are not added to any bucket.
                        _norm = self._normalize_artifact_archetype(a_type, "")
                        if _norm == "ui_anchor":
                            windows.append(art_id)
                        elif _norm == "batch_anchor":
                            batch_anchors.append(art_id)
                        elif _norm == "shared_logic":
                            user_objects.append(art_id)
                        elif _norm == "data_provider":
                            datawindows.append(art_id)

                    # Select a single representative anchor — last resort only
                    print(
                        f"    [WARN] Fix-D safety net yielded no entrypoints for {mfu_id}. "
                        f"Activating last-resort type-based fallback."
                    )
                    if execution_track in ["UI-Track", "Mixed-Track"]:
                        eps = (
                            windows if windows else (user_objects if user_objects else datawindows)
                        )
                        if eps:
                            actual_entrypoints = [eps[0]]
                    if execution_track in ["Batch-Track", "Mixed-Track"] and not actual_entrypoints:
                        eps = batch_anchors if batch_anchors else full_artifact_list[:1]
                        if eps:
                            actual_entrypoints = [eps[0]]

                # TASK 4.1: Build tasks using context-driven prompt routing checks
                # PROMPT V3: Use cascade _get_prompt(type, paradigm) for language-specific variants.
                # Routing map:
                #   batch_anchor              → batch_blueprint + cobol/etc variant
                #   shared_logic/data_provider/interface → api_contracts + language variant
                #                               (pfx="API" — triggers Pass 2 dedup guard)
                #   ui_anchor (default)        → ui_blueprint + pb/vb6/etc variant
                ep_tasks = []
                for ep in actual_entrypoints:
                    meta = self.global_metadata.get(ep) or metadata_lookup.get(ep, {})
                    a_type = meta.get("type", "").lower()

                    # Inspect physical file suffix to isolate non-visual components inside UI structures
                    matched_path = self.file_map.get(ep.lower())
                    file_suffix = matched_path.suffix.lower() if matched_path else ""

                    # MULTI-LANGUAGE FIX (Sub-task 6E): Use registry-normalised archetype
                    # for routing. File suffix is resolved via registry extension lookup so
                    # future languages are automatically routed without touching this block.
                    _eff_arch = self._normalize_artifact_archetype(a_type, file_suffix)
                    if execution_track == "Batch-Track" or _eff_arch == "batch_anchor":
                        ep_tasks.append(
                            (ep, self._get_prompt("batch_blueprint", dominant_paradigm), "Batch")
                        )
                    elif _eff_arch in ("shared_logic", "data_provider", "interface"):
                        # Context Redirection: Non-visual components → api_contracts (server-side logic).
                        # pfx="API" triggers the Pass 2 dedup guard to prevent running api_contracts twice.
                        ep_tasks.append(
                            (ep, self._get_prompt("api_contracts", dominant_paradigm), "API")
                        )
                    else:
                        ep_tasks.append(
                            (ep, self._get_prompt("ui_blueprint", dominant_paradigm), "UI")
                        )

                # TASK 4.1: Last resort fallback if absolutely nothing was found
                if not ep_tasks and full_artifact_list:
                    fallback_ep = full_artifact_list[0]
                    meta = self.global_metadata.get(fallback_ep) or metadata_lookup.get(
                        fallback_ep, {}
                    )
                    a_type = meta.get("type", "").lower()
                    matched_path = self.file_map.get(fallback_ep.lower())
                    file_suffix = matched_path.suffix.lower() if matched_path else ""

                    # MULTI-LANGUAGE FIX (Sub-task 6E): Same normaliser used in primary loop.
                    _eff_arch_fb = self._normalize_artifact_archetype(a_type, file_suffix)
                    if execution_track == "Batch-Track" or _eff_arch_fb == "batch_anchor":
                        ep_tasks.append(
                            (
                                fallback_ep,
                                self._get_prompt("batch_blueprint", dominant_paradigm),
                                "Batch",
                            )
                        )
                    elif _eff_arch_fb in ("shared_logic", "data_provider", "interface"):
                        ep_tasks.append(
                            (
                                fallback_ep,
                                self._get_prompt("api_contracts", dominant_paradigm),
                                "API",
                            )
                        )
                    else:
                        ep_tasks.append(
                            (fallback_ep, self._get_prompt("ui_blueprint", dominant_paradigm), "UI")
                        )
                    print(
                        f"    > Level 4 Route: Pure Backend Module Detected (No Anchors) - Routed to {ep_tasks[0][2]}"
                    )

                master_content = f"# Master Blueprint: {prefixed_mfu_name}\n\n"
                master_naming_map = {}
                master_file_path = mfu_output_dir / "1_Master_Blueprint.md"

                def process_entrypoint(art_id, selected_prompt, focal_combined_code):
                    meta = self.global_metadata.get(art_id) or metadata_lookup.get(art_id, {})
                    current_archetype = meta.get("type", "unresolved")
                    active_paradigm_name = dominant_paradigm

                    # TASK 2.1: Query the active plugin's hints using the registry
                    hints = self._get_paradigm_hints(active_paradigm_name, current_archetype)

                    pass_args = modernization_ctx.copy()
                    pass_args.update(
                        {
                            "mfu_id": mfu_id,
                            "mfu_id_lowercase": mfu_id.lower(),
                            "mfu_name": prefixed_mfu_name,
                            "module_name": current_module_name,
                            "module_name_lowercase": current_module_name.lower(),
                            "execution_track": execution_track,
                            "traceability_chain": traceability_chain_str,
                            "risk_flags": risk_flags_str,
                            "mfu_semantic_summary": meta.get("semantic", {}).get("summary", "N/A"),
                            "screen_dependencies": json.dumps(meta.get("edges", []), indent=2),
                            "artifacts_payload": focal_combined_code,
                            "entrypoint_id": art_id,
                            "primary_anchor": art_id,
                            "embedded_components_context": "N/A",
                            "current_screen_code": focal_combined_code,
                            "artifact_list": ", ".join(full_artifact_list),
                            "references_table": "\n".join(references_rows),
                            "naming_map": shared_context,
                            "master_ui_blueprint": "N/A",
                            # TASK 2.1 & 2.2: Inject hints string with graceful empty fallback
                            "paradigm_hints": hints if hints is not None else "",
                            # GAP-02 FIX: Explicit per-call focal scope directive so each
                            # entrypoint document centres its detailed extraction on its own
                            # artifact rather than narrating all artifacts equally.
                            "entrypoint_focus": (
                                f"FOCAL SCOPE MANDATE: Your PRIMARY analysis target for this document "
                                f"is the artifact with id='{art_id}'. All other artifacts in the payload "
                                f"are supporting context only. Your Tier 2 tables, Appendix A proofs, "
                                f"and Visual Zone Map MUST be centred on '{art_id}'. "
                                f"Do NOT perform a full extraction for supporting artifacts — summarise "
                                f"their contribution in one sentence per artifact at most."
                            ),
                        }
                    )

                    # TASK 2.1: Inject into the format mapping call
                    formatted_prompt = self._safe_format(selected_prompt, pass_args)

                    # ── Resilience: 3 retries with error-aware strategy ────────────
                    # TOKEN_OVERFLOW errors (payload too large) are permanent — the
                    # same payload will always be rejected.  Progressive reduction
                    # factors cut focal_combined_code on each overflow retry so the
                    # model eventually receives a payload it can process.
                    #
                    # Transient errors (server timeout, 500, non-size 429) are handled
                    # by a short wait, identical to the previous behaviour.
                    #
                    # Reduction schedule (applied only on TokenOverflowError):
                    #   Attempt 1 (overflow) → keep 70 % of code (remove tail context)
                    #   Attempt 2 (overflow) → keep 45 % of code
                    #   Attempt 3 (overflow) → keep 25 % of code (FOCAL essence only)
                    _OVERFLOW_FACTORS = [0.70, 0.45, 0.25]
                    _current_code = focal_combined_code  # mutable across attempts

                    _job_id = f"{mfu_id}_P1_{art_id}"
                    for attempt in range(3):
                        try:
                            out = self.call_llm_api(
                                formatted_prompt, _current_code, _job_id, mfu_output_dir
                            )
                            if out and self._is_valid_spec(out):
                                return out
                            if out:
                                # Non-empty but structurally degenerate (e.g. JSON-only
                                # collapse or truncated mid-document). Root cause is
                                # LLM-side variance, NOT payload size, so we re-roll the
                                # SAME payload (no reduction). The disk cache must be
                                # busted first, otherwise the next call returns the cached
                                # bad output via [CACHE HIT]. (Item 1)
                                print(
                                    f"    [WARN] Retry {attempt + 1}/3 for {art_id} "
                                    f"[INVALID SPEC STRUCTURE → re-rolling same payload]"
                                )
                                self._bust_cache(_job_id, mfu_output_dir)
                                time.sleep(1.5)  # brief pause before same-payload re-roll
                            else:
                                # call_llm_api returned None → the LLMClient already
                                # OWNS and has EXHAUSTED its transient/timeout retry
                                # budget for this call. Re-invoking here just re-bills
                                # the identical payload (the old app-level x3 that turned
                                # ~6 client attempts into 18). Single-layer retry: stop
                                # and fail-loud immediately.
                                print(
                                    f"    [ERROR] {art_id}: LLM call returned None after "
                                    f"client-side retries — not re-invoking (single-layer "
                                    f"retry)."
                                )
                                return None
                        except TokenOverflowError:
                            factor = _OVERFLOW_FACTORS[attempt]
                            original_len = len(focal_combined_code)
                            target_len = int(original_len * factor)
                            _current_code = (
                                focal_combined_code[:target_len]
                                + f"\n\n... [PAYLOAD REDUCED TO {int(factor * 100)}% FOR TOKEN BUDGET — "
                                f"original {original_len:,} chars → {target_len:,} chars. "
                                f"Core focal artifact preserved; tail context truncated.]"
                            )
                            print(
                                f"    [WARN] Retry {attempt + 1}/3 for {art_id} "
                                f"[TOKEN OVERFLOW → reducing payload to {int(factor * 100)}% "
                                f"({target_len:,}/{original_len:,} chars)]"
                            )
                            # No sleep needed — overflow is not time-based
                    # Fail-loud: 3 attempts exhausted without a structurally valid spec.
                    # Return None so the caller skips writing — never persist a degenerate
                    # spec as if it succeeded. (Item 1)
                    print(
                        f"    [ERROR] {art_id}: spec generation failed validity after 3 "
                        f"attempt(s) — no spec written (fail-loud, Item 1)."
                    )
                    return None

                # BUG-02 FIX: Removed type-level dedup gate (Option A).
                # -----------------------------------------------------------------------
                # PROBLEM: The old GAP-01 guard kept only the FIRST entrypoint per prompt
                # type (UI / API / Batch), silently dropping all subsequent same-type
                # entrypoints.  In the failing case:
                #   modbatchprocessor → pfx=API  (queued first → kept)
                #   clsbankexport     → pfx=API  (second API-type → DROPPED)
                # This caused data contracts for distinct business classes (bank export,
                # payroll engine, security module, etc.) to be silently omitted from the
                # spec output with no warning beyond the [GAP-01] console line.
                #
                # ROOT CAUSE: The dedup conflated "same prompt template used" with "same
                # output content produced" — which is incorrect.  Two artifacts that both
                # route to api_contracts may describe entirely different business domains.
                #
                # FIX: Process EVERY entrypoint in ep_tasks without filtering.
                # The {entrypoint_focus} FOCAL SCOPE MANDATE in each prompt already
                # instructs the LLM to centre extraction on the specific artifact being
                # processed, so each call produces a distinct, focused spec file:
                #   1_UIBlueprint_frmbatchrun.md
                #   1_APISpec_modbatchprocessor.md
                #   1_APISpec_clsbankexport.md       ← was previously silently dropped
                #
                # PASS 2 GUARD (see below): executed_prompt_types replaces seen_prompt_types.
                # It is populated ONLY on successful LLM return so a failed Pass 1 API call
                # does not incorrectly suppress Pass 2 (which would leave the MFU with no
                # API coverage at all).  Semantics: "which prompt types completed successfully
                # in Pass 1?" — used exclusively by the if "API" in executed_prompt_types guard.
                executed_prompt_types = set()

                for ep, prmpt, pfx in ep_tasks:
                    print(f"    > Extracting Entrypoint ({pfx}): {ep}")
                    # Build a per-entrypoint payload: focal ep = full fidelity,
                    # other primaries subject to budget (prevents MOD-AZUHARA overflow).
                    focal_code, _ = self._gather_mfu_code(
                        mfu.get("artifacts", []), metadata_lookup, focal_artifact_id=ep
                    )
                    out = process_entrypoint(ep, prmpt, focal_code)
                    if out:
                        # Mark this prompt type as successfully executed (Pass 2 guard input).
                        executed_prompt_types.add(pfx)
                        clean_txt, p_map = self._extract_and_remove_naming_map(out)
                        # Non-clobber merge: first-occurrence wins.
                        # Later entrypoints in Pass 1 add NEW keys only;
                        # they must not overwrite mapping decisions already
                        # made for the same legacy identifier.
                        for k, v in p_map.items():
                            master_naming_map.setdefault(k, v)

                        # GAP-01 FIX (preserved): Embed the prompt type (pfx) in the FILE_BREAK
                        # tag so segregate_master_blueprint picks the correct filename prefix:
                        #   UI    → 1_UIBlueprint_<ep>.md
                        #   API   → 1_APISpec_<ep>.md
                        #   Batch → 1_BatchSpec_<ep>.md
                        master_content += f"\n\n[FILE_BREAK:{ep}|{pfx}]\n{clean_txt}\n\n"

                # Finalize Master Content
                if master_naming_map:
                    naming_map_json = json.dumps(master_naming_map, indent=2)
                    master_content += "[NAMING_MAP]\n```json\n" + naming_map_json + "\n```\n"

                p1_output = master_content if ep_tasks else None

                if p1_output:
                    shared_context = json.dumps(master_naming_map)
                    master_file_path.write_text(
                        self._strip_scaffolding(p1_output), encoding="utf-8"
                    )

                    # Now segregation will find the tags and create the individual files
                    self.segregate_master_blueprint(master_file_path, mfu_output_dir)

                    # CLEANUP: Remove the redundant Master Blueprint if individual files exist.
                    # GAP-01 FIX: Check for all type-aware prefixes as well as the legacy ones.
                    # PROMPT V3: Added 1_APISpec_* for shared_logic/data_provider Pass-1 outputs.
                    try:
                        spec_files_exist = (
                            any(mfu_output_dir.glob("1_Blueprint_*.md"))
                            or any(mfu_output_dir.glob("1_UIBlueprint_*.md"))
                            or any(mfu_output_dir.glob("1_APISpec_*.md"))
                            or any(mfu_output_dir.glob("1_LogicSpec_*.md"))
                            or any(mfu_output_dir.glob("1_BatchSpec_*.md"))
                        )
                        if spec_files_exist:
                            master_file_path.unlink()
                            print("    [CLEANUP] Removed redundant 1_Master_Blueprint.md")
                    except Exception as e:
                        print(f"    [WARN] Could not remove Master Blueprint: {e}")
                else:
                    continue

                # --- PASS 2: API Contracts & Server-Side Logic ---
                # PROMPT V3: 2-pass architecture — Pass 3 (logic_security.txt) is retired.
                # Server-side business logic and authorization are now in api_contracts.txt.
                active_paradigm_name = dominant_paradigm
                combined_hints = self._get_paradigm_hints(active_paradigm_name)

                # --- TASK 4.2 IMPLEMENTATION: Programmatically extract 'masked_regions' payload ---
                mfu_masked_regions = []
                for art_id in full_artifact_list:
                    meta = self.global_metadata.get(art_id) or metadata_lookup.get(art_id, {})

                    # Interrogate both potential keys to catch all plugin extraction layouts smoothly
                    m_regions = meta.get("landmines", {}).get("masked_regions", [])
                    if not m_regions:
                        m_regions = meta.get("landmine_details", {}).get("masked_regions", [])

                    for mr in m_regions:
                        mfu_masked_regions.append(
                            {
                                "artifact_id": art_id,
                                "category": mr.get("category", "UNKNOWN").upper(),
                                "raw_text": mr.get("raw_text", "").strip(),
                            }
                        )

                # Compile structural schemas into markdown payload context blocks
                if mfu_masked_regions:
                    formatted_mr = []
                    for mr in mfu_masked_regions:
                        formatted_mr.append(
                            f"### Source Component ID: {mr['artifact_id']} | Forensic Category: {mr['category']}\n"
                            f"```sql\n{mr['raw_text']}\n```"
                        )
                    masked_regions_context = "\n\n".join(formatted_mr)
                else:
                    masked_regions_context = "No masked database regions or sequestered queries found for this Functional Unit."

                # GAP-05 FIX: Build a focused blueprint context for P2/P3.
                # Previously p1_output (all concatenated blueprints) was injected raw.
                # Now we extract only the primary anchor's section and strip appendices
                # so P2/P3 context budgets are not overwhelmed.
                primary_blueprint_ctx = self._extract_primary_blueprint_context(
                    p1_output, mfu.get("traceability_chain", [])
                )

                pass_args = modernization_ctx.copy()
                pass_args.update(
                    {
                        "mfu_id": mfu_id,
                        "mfu_id_lowercase": mfu_id.lower(),
                        "mfu_name": prefixed_mfu_name,
                        "execution_track": execution_track,
                        "traceability_chain": traceability_chain_str,
                        "risk_flags": risk_flags_str,
                        "module_name": current_module_name,
                        "module_name_lowercase": current_module_name.lower(),
                        "artifact_list": ", ".join(full_artifact_list),
                        "references_table": "\n".join(references_rows),
                        "naming_map": shared_context,
                        # GAP-05 FIX: Use focused primary-anchor context instead of full master
                        "master_ui_blueprint": primary_blueprint_ctx,
                        # TASK 2.1 & 2.2: Inject combined hints string with graceful fallback
                        "paradigm_hints": combined_hints if combined_hints is not None else "",
                        # Task 4.2 Template Placeholder Hydration Block
                        "masked_regions": masked_regions_context,
                    }
                )

                # Pass 2: API Contracts & Server-Side Logic
                # PROMPT V3: Pass 3 (logic_security.txt) is eliminated — its content is now merged
                # into api_contracts.txt (server-side business logic and authorization rules).
                # Pass 2 only runs when api_contracts was NOT already invoked in Pass 1.
                if "API" not in executed_prompt_types:
                    print("  > PASS 2: API Contracts & Server-Side Logic (Context-Driven)")
                    # Add entrypoint_focus for the primary anchor (required by api_contracts V3).
                    primary_ep = actual_entrypoints[0] if actual_entrypoints else mfu_id
                    pass_args["entrypoint_focus"] = (
                        f"FOCAL SCOPE MANDATE: Your PRIMARY analysis target for this document "
                        f"is the artifact with id='{primary_ep}'. All other artifacts in the payload "
                        f"are supporting context only. Centre your data contracts, authorization rules, "
                        f"and persistence DNA on '{primary_ep}'. Do NOT perform a full extraction for "
                        f"supporting artifacts — summarise their contribution in one sentence at most."
                    )
                    # PROMPT V3: Load api_contracts with language-specific variant injected.
                    p2_prompt_text = self._get_prompt("api_contracts", dominant_paradigm)
                    formatted_p2 = self._safe_format(p2_prompt_text, pass_args)

                    # Build focal payload for Pass 2 centred on the primary entrypoint.
                    p2_focal_code, _ = self._gather_mfu_code(
                        mfu.get("artifacts", []), metadata_lookup, focal_artifact_id=primary_ep
                    )

                    # ── Resilience: 3 retries with error-aware strategy ────────────
                    # TOKEN_OVERFLOW errors (payload too large) are permanent — the same
                    # payload will always be rejected.  Progressive reduction factors cut
                    # p2_focal_code on each overflow retry so the model eventually receives
                    # a processable payload.  Transient errors get a short sleep+retry.
                    p2_output = None
                    _OVERFLOW_FACTORS_P2 = [0.70, 0.45, 0.25]
                    _p2_current_code = p2_focal_code  # mutable across attempts

                    for attempt in range(3):
                        try:
                            p2_output = self.call_llm_api(
                                formatted_p2, _p2_current_code, f"{mfu_id}_P2", mfu_output_dir
                            )
                            if p2_output:
                                break
                            # None → the LLMClient already owns and has exhausted its
                            # transient/timeout retries for this call; re-invoking just
                            # re-bills the identical payload (old app-level x3 -> 18).
                            # Single-layer retry: stop. API spec is skipped below.
                            print(
                                f"    [ERROR] Pass 2 for {mfu_id}: LLM returned None after "
                                f"client-side retries — not re-invoking (single-layer retry)."
                            )
                            break
                        except TokenOverflowError:
                            factor = _OVERFLOW_FACTORS_P2[attempt]
                            original_len = len(p2_focal_code)
                            target_len = int(original_len * factor)
                            _p2_current_code = (
                                p2_focal_code[:target_len]
                                + f"\n\n... [PASS 2 PAYLOAD REDUCED TO {int(factor * 100)}% FOR TOKEN BUDGET — "
                                f"original {original_len:,} chars → {target_len:,} chars.]"
                            )
                            print(
                                f"    [WARN] Pass 2 retry {attempt + 1}/3 for {mfu_id} "
                                f"[TOKEN OVERFLOW → reducing payload to {int(factor * 100)}% "
                                f"({target_len:,}/{original_len:,} chars)]"
                            )

                    if p2_output:
                        clean_p2, p2_map = self._extract_and_remove_naming_map(p2_output)
                        # Non-clobber merge: Pass 2 adds NEW identifiers it discovers;
                        # it MUST NOT overwrite naming decisions already made in Pass 1
                        # for the same legacy control/variable identifier.
                        for k, v in p2_map.items():
                            master_naming_map.setdefault(k, v)
                        p2_path = mfu_output_dir / "2_API_Contracts.md"
                        p2_path.write_text(self._strip_scaffolding(clean_p2), encoding="utf-8")
                        print("    [PASS 2 OK] API spec saved: 2_API_Contracts.md")
                    else:
                        print(
                            f"    [WARN] Pass 2 produced no output for {mfu_id} — API spec skipped."
                        )
                else:
                    print(
                        "    [DEDUP] Skipping Pass 2 — api_contracts already invoked in Pass 1 "
                        "for this MFU.  See 1_APISpec_*.md for API & Server-Side Logic."
                    )

                # --- SAVING CONFIGURATION METADATA ---
                # Guarantee the Naming Map JSON is saved even if P2 produces no output.
                print("  > SAVING CONFIGURATION METADATA...")
                try:
                    parsed_naming_map = (
                        json.loads(shared_context)
                        if shared_context and shared_context != "{}"
                        else {}
                    )
                except json.JSONDecodeError as e:
                    print(f"    [WARN] JSON Parse Error on NAMING_MAP. Detail: {e}")
                    parsed_naming_map = shared_context

                metadata_payload = {
                    "mfu_id": mfu_id,
                    "mfu_name": prefixed_mfu_name,
                    "module": current_module_name,
                    "execution_track": execution_track,
                    "target_stack": self.target_stack,
                    "source_artifacts": mfu.get("artifacts", []),
                    "resolved_dependencies": list(resolved_files),
                    "traceability_chain": mfu.get("traceability_chain", []),
                    "risk_flags": mfu.get("risk_flags", []),
                    "naming_map": parsed_naming_map,
                    "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                config_map_path = mfu_output_dir / "config_naming_map.json"
                config_map_path.write_text(json.dumps(metadata_payload, indent=2), encoding="utf-8")
                print(f"    [OK] config_naming_map.json saved -> {config_map_path.name}")
