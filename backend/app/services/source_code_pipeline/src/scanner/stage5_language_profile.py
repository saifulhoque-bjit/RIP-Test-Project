"""
Stage 5 Language Profile Registry
==================================
Loads language-specific vocabulary constraints and Gherkin guidance from
``prompts/language_profiles.yaml`` and makes them available to the Stage 5
FeatureStoryAgent prompt builders via the ``{LANGUAGE_HINTS}`` placeholder.

DESIGN PRINCIPLES
-----------------
* **Zero Stage 4 impact** — this module has no dependency on ``plugin_base.py``
  or ``plugin_registry.py``.  Stage 4 SRS generation (PluginRegistry dispatch,
  paradigm_hints, per-language prompt subdirectories) is completely unaffected.
* **Graceful degradation** — if the YAML file is absent, cannot be parsed, or
  if the requested ``technology_tag`` has no profile, an empty string is returned.
  Existing prompt behaviour is preserved — no exceptions bubble to the caller.
* **Single-file config** — adding a new language requires only a new entry in
  ``language_profiles.yaml``.  No code changes are needed here.
* **Thread-safe singleton** — ``Stage5ProfileRegistry`` uses double-checked
  locking so it is safe for multi-threaded batch runs.
* **Lazy load** — the YAML is parsed once on first access, not at import time,
  so import cost is zero for callers that never use Stage 5.

USAGE (from feature_story_agent.py)
------------------------------------
    from src.scanner.stage5_language_profile import Stage5ProfileRegistry

    registry = Stage5ProfileRegistry.load(project_root=self.root)
    hints = registry.render_hints_block(
        technology_tag="COBOL",
        srs_type="BatchBlueprint",
    )
    # hints is a formatted multi-line string ready to replace {LANGUAGE_HINTS}

PUBLIC API
----------
``Stage5ProfileRegistry.load(project_root)``
    Class method.  Returns the singleton registry, initialising it from the
    YAML file on first call.  Subsequent calls return the cached singleton.

``registry.render_hints_block(technology_tag, srs_type)``
    Returns the ``{LANGUAGE_HINTS}`` block string for the given language and
    SRS document type.  Returns ``""`` on any lookup failure.

``registry.is_loaded``
    True if the YAML was successfully parsed; False on any load error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
import threading

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default YAML search paths — relative to project root
# ---------------------------------------------------------------------------
_YAML_CANDIDATES = [
    Path("prompts/language_profiles.yaml"),
    Path("language_profiles.yaml"),
]

# Fallback root for integrated backend execution where project_root may be
# projects/sample_project while prompts live under source_code_pipeline/prompts.
_CODE_YAML_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Data classes (immutable after construction)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForbiddenPattern:
    """One forbidden AC / Gherkin pattern for a given language."""

    description: str
    examples_bad: tuple[str, ...] = field(default_factory=tuple)
    examples_good: tuple[str, ...] = field(default_factory=tuple)
    note: str = ""


@dataclass(frozen=True)
class Stage5LanguageProfile:
    """
    Immutable profile for one legacy source language.

    All fields are validated at construction time; any missing required field
    is replaced with a safe default so callers never receive None.
    """

    technology_tag: str
    vocab_register: str = "legacy application"
    gherkin_when_rule: str = ""
    forbidden_patterns: tuple[ForbiddenPattern, ...] = field(default_factory=tuple)
    l2ref_formats: dict[str, str] = field(default_factory=dict)
    success_criteria_note: str = ""
    # Glob patterns (case-insensitive) matched against a feature's focal artifact
    # id/name to identify reusable FRAMEWORK / ancestor / navigation-menu units —
    # e.g. PowerBuilder "u_*"/"m_*"/"w_pfc_*", VB6 "modGlobals", COBOL "*UTIL*"/"*.cpy".
    # Such features get relaxed business-value gates in Stage 5b (see #20). Empty
    # tuple = nothing is treated as framework for this language (safe default).
    framework_patterns: tuple[str, ...] = field(default_factory=tuple)

    def get_l2ref_format(self, srs_type: str) -> str:
        """
        Returns the l2_source_ref format template for the given SRS document type.

        Args:
            srs_type: One of "BatchBlueprint", "UIBlueprint", "APIContracts",
                      or any future document type key defined in the YAML.

        Returns:
            Format string (e.g. "SRS::{mfu_id}::BATCH::STEP{nn}") or ""
            if the srs_type is not mapped for this language.
        """
        return self.l2ref_formats.get(srs_type, "")


# ---------------------------------------------------------------------------
# Renderer — converts a profile to a prompt-ready string block
# ---------------------------------------------------------------------------


def _render_profile_block(profile: Stage5LanguageProfile, srs_type: str) -> str:
    """
    Renders a ``Stage5LanguageProfile`` into the formatted multi-line string
    that replaces ``{LANGUAGE_HINTS}`` in Stage 5 prompt templates.

    The rendered block is designed to be injected verbatim into a prompt —
    it uses plain text formatting (no Markdown, no code fences) so it reads
    naturally as part of the surrounding prompt prose.

    Args:
        profile:  The language profile to render.
        srs_type: The SRS document type for this MFU (determines l2ref format).

    Returns:
        A non-empty formatted string, or ``""`` for the Generic fallback with
        no meaningful constraints.
    """
    lines: list[str] = []

    lines.append("════════════════════════════════════════════════════════════")
    lines.append(f"🌐  LANGUAGE-SPECIFIC CONSTRAINTS — {profile.technology_tag}")
    lines.append(f"    Vocabulary register: {profile.vocab_register}")
    lines.append("════════════════════════════════════════════════════════════")
    lines.append("")

    # ── Gherkin "when" clause rule ──────────────────────────────────────────
    if profile.gherkin_when_rule.strip():
        lines.append("── GHERKIN 'when' CLAUSE RULE (MANDATORY) ──────────────────")
        # Wrap long rule text at word boundaries
        for line in profile.gherkin_when_rule.strip().splitlines():
            lines.append(line.rstrip())
        lines.append("")

    # ── Forbidden patterns ──────────────────────────────────────────────────
    if profile.forbidden_patterns:
        lines.append("── FORBIDDEN PATTERNS IN ACCEPTANCE CRITERIA ───────────────")
        for pat in profile.forbidden_patterns:
            lines.append(f"  ✗ {pat.description}")
            if pat.examples_bad:
                lines.append("    Bad examples (DO NOT produce):")
                for ex in pat.examples_bad:
                    lines.append(f'      ✗ "{ex}"')
            if pat.examples_good:
                lines.append("    Good alternatives (USE instead):")
                for ex in pat.examples_good:
                    lines.append(f'      ✓ "{ex}"')
            if pat.note:
                lines.append(f"    Note: {pat.note.strip()}")
            lines.append("")

    # ── l2_source_ref format ────────────────────────────────────────────────
    l2ref = profile.get_l2ref_format(srs_type)
    if l2ref:
        lines.append("── l2_source_ref FORMAT FOR THIS MFU ───────────────────────")
        lines.append(f"  SRS document type: {srs_type}")
        lines.append(f"  Required format:   {l2ref}")
        lines.append(
            "  (Replace {mfu_id} with the actual MFU ID, {nn} / {nnn} with zero-padded step/event numbers.)"
        )
        lines.append("")

    # ── success_criteria guidance ───────────────────────────────────────────
    if profile.success_criteria_note.strip():
        lines.append("── success_criteria[] GUIDANCE ─────────────────────────────")
        for line in profile.success_criteria_note.strip().splitlines():
            lines.append(line.rstrip())
        lines.append("")

    lines.append("════════════════════════════════════════════════════════════")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class Stage5ProfileRegistry:
    """
    Thread-safe singleton registry for Stage 5 language profiles.

    Loaded once from ``prompts/language_profiles.yaml`` on first call to
    ``Stage5ProfileRegistry.load()``.  Subsequent calls return the cached
    instance.  If the YAML cannot be loaded (file missing, parse error, missing
    ``pyyaml`` dependency), the registry operates in fallback mode: all
    ``render_hints_block()`` calls return ``""`` so the agent continues normally.

    The registry is intentionally INDEPENDENT of ``PluginRegistry`` and
    ``LanguagePluginBase``.  Stage 4 paths are not affected.
    """

    _instance: Stage5ProfileRegistry | None = None
    _lock: threading.Lock = threading.Lock()

    def __init__(
        self,
        profiles: dict[str, Stage5LanguageProfile],
        yaml_path: Path | None,
        is_loaded: bool,
    ) -> None:
        self._profiles: dict[str, Stage5LanguageProfile] = profiles
        self._yaml_path: Path | None = yaml_path
        self._is_loaded: bool = is_loaded

    # ------------------------------------------------------------------
    # Class-level constructor (singleton factory)
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, project_root: Path) -> Stage5ProfileRegistry:
        """
        Returns the singleton ``Stage5ProfileRegistry``.

        On first call, locates and parses ``prompts/language_profiles.yaml``
        relative to ``project_root``.  The result is cached; subsequent calls
        with any ``project_root`` value return the same singleton.

        Args:
            project_root: Absolute path to the RIP project root (the directory
                          containing ``prompts/``, ``src/``, ``project_config.json``).

        Returns:
            A ``Stage5ProfileRegistry`` instance (possibly in fallback mode if
            the YAML could not be loaded).
        """
        if cls._instance is not None:
            return cls._instance

        with cls._lock:
            # Double-checked locking — re-check after acquiring the lock
            if cls._instance is not None:
                return cls._instance
            cls._instance = cls._build(project_root)
        return cls._instance

    @classmethod
    def _build(cls, project_root: Path) -> Stage5ProfileRegistry:
        """Parses the YAML and constructs all language profiles."""
        yaml_path = cls._find_yaml(project_root)
        if yaml_path is None:
            logger.warning(
                "[Stage5ProfileRegistry] language_profiles.yaml not found under '%s'. "
                "Language-specific hints disabled — {LANGUAGE_HINTS} will be empty.",
                project_root,
            )
            return cls(profiles={}, yaml_path=None, is_loaded=False)

        try:
            import yaml  # pyyaml — optional dependency
        except ImportError:
            logger.warning(
                "[Stage5ProfileRegistry] pyyaml not installed. "
                "Install via 'pip install pyyaml' to enable language-specific hints. "
                "{LANGUAGE_HINTS} will be empty for all MFUs."
            )
            return cls(profiles={}, yaml_path=yaml_path, is_loaded=False)

        try:
            raw = yaml_path.read_text(encoding="utf-8")
            data = yaml.safe_load(raw)
        except Exception as exc:
            logger.error(
                "[Stage5ProfileRegistry] Failed to parse '%s': %s. "
                "Language-specific hints disabled.",
                yaml_path,
                exc,
            )
            return cls(profiles={}, yaml_path=yaml_path, is_loaded=False)

        profiles_raw = (data or {}).get("profiles", {})
        if not isinstance(profiles_raw, dict):
            logger.error(
                "[Stage5ProfileRegistry] 'profiles' key missing or not a mapping in '%s'. "
                "Language-specific hints disabled.",
                yaml_path,
            )
            return cls(profiles={}, yaml_path=yaml_path, is_loaded=False)

        profiles: dict[str, Stage5LanguageProfile] = {}
        for tag, raw_profile in profiles_raw.items():
            try:
                profile = cls._parse_profile(tag, raw_profile or {})
                profiles[tag] = profile
            except Exception as exc:
                logger.warning(
                    "[Stage5ProfileRegistry] Skipping profile '%s' due to parse error: %s",
                    tag,
                    exc,
                )

        logger.info(
            "[Stage5ProfileRegistry] Loaded %d language profile(s) from '%s': %s",
            len(profiles),
            yaml_path,
            list(profiles.keys()),
        )
        return cls(profiles=profiles, yaml_path=yaml_path, is_loaded=True)

    @staticmethod
    def _find_yaml(project_root: Path) -> Path | None:
        """Searches candidate paths for the YAML file."""
        roots = [project_root, _CODE_YAML_ROOT]
        seen = set()
        for root in roots:
            rp = Path(root).resolve()
            if rp in seen:
                continue
            seen.add(rp)
            for candidate in _YAML_CANDIDATES:
                full = rp / candidate
                if full.exists():
                    return full
        return None

    @staticmethod
    def _parse_profile(tag: str, raw: dict) -> Stage5LanguageProfile:
        """Converts a raw YAML dict to a validated Stage5LanguageProfile."""
        # Parse forbidden_patterns list
        fp_list: list[ForbiddenPattern] = []
        for fp_raw in raw.get("forbidden_patterns") or []:
            if not isinstance(fp_raw, dict):
                continue
            bad_list = fp_raw.get("examples_bad") or []
            good_list = fp_raw.get("examples_good") or []
            fp_list.append(
                ForbiddenPattern(
                    description=str(fp_raw.get("description", "")),
                    examples_bad=tuple(str(e) for e in bad_list),
                    examples_good=tuple(str(e) for e in good_list),
                    note=str(fp_raw.get("note", "")),
                )
            )

        # Parse l2ref_formats dict — normalise values to str
        l2ref_raw = raw.get("l2ref_formats") or {}
        l2ref: dict[str, str] = {str(k): str(v) for k, v in l2ref_raw.items() if k and v}

        # Parse framework_patterns — list of glob patterns (str), normalised.
        fw_raw = raw.get("framework_patterns") or []
        framework_patterns = tuple(str(p).strip() for p in fw_raw if p and str(p).strip())

        return Stage5LanguageProfile(
            technology_tag=tag,
            vocab_register=str(raw.get("vocab_register", "legacy application")),
            gherkin_when_rule=str(raw.get("gherkin_when_rule", "")),
            forbidden_patterns=tuple(fp_list),
            l2ref_formats=l2ref,
            success_criteria_note=str(raw.get("success_criteria_note", "")),
            framework_patterns=framework_patterns,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_loaded(self) -> bool:
        """True if the YAML was successfully parsed."""
        return self._is_loaded

    def get_profile(self, technology_tag: str) -> Stage5LanguageProfile | None:
        """
        Returns the profile for ``technology_tag``, or ``None`` if not found.

        Lookup is exact-match on the canonical tag (e.g. "COBOL", "VB6",
        "PowerBuilder").  Falls back to the "Generic" profile if defined in
        the YAML and the exact tag is not found.
        """
        if technology_tag in self._profiles:
            return self._profiles[technology_tag]
        # Fallback to Generic profile if present
        if "Generic" in self._profiles:
            logger.debug(
                "[Stage5ProfileRegistry] No profile for '%s' — using Generic fallback.",
                technology_tag,
            )
            return self._profiles["Generic"]
        return None

    def render_hints_block(self, technology_tag: str, srs_type: str) -> str:
        """
        Returns the formatted ``{LANGUAGE_HINTS}`` block for injection into
        Stage 5 prompt templates.

        Args:
            technology_tag: Canonical technology tag (e.g. "COBOL", "VB6",
                            "PowerBuilder"). Must match a key in language_profiles.yaml
                            (case-sensitive).
            srs_type:       SRS document type for l2_source_ref format selection.
                            One of "BatchBlueprint", "UIBlueprint", "APIContracts".

        Returns:
            A formatted multi-line string ready to replace ``{LANGUAGE_HINTS}``.
            Returns ``""`` if no profile is found or the registry failed to load,
            so the ``str.replace()`` call in the prompt builder degrades gracefully
            (the placeholder stays empty rather than raising an exception).
        """
        profile = self.get_profile(technology_tag)
        if profile is None:
            logger.debug(
                "[Stage5ProfileRegistry] No profile (including Generic) for '%s'. "
                "{LANGUAGE_HINTS} will be empty.",
                technology_tag,
            )
            return ""
        try:
            return _render_profile_block(profile, srs_type)
        except Exception as exc:
            logger.error(
                "[Stage5ProfileRegistry] Error rendering hints block for '%s': %s",
                technology_tag,
                exc,
            )
            return ""

    def list_registered_tags(self) -> list[str]:
        """Returns all technology tags that have a profile registered."""
        return list(self._profiles.keys())

    @classmethod
    def reset_for_testing(cls) -> None:
        """
        Resets the singleton so it can be re-initialised in tests.
        NOT intended for production use — call only from test teardowns.
        """
        with cls._lock:
            cls._instance = None
