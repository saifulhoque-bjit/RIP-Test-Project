"""
Abstract Base Class for RIP Language Plugins.
Enforces the standardized Forensic Interface for all polyglot adapters.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
import re
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------
# 1. The Strict Enterprise Taxonomy
# ---------------------------------------------------------
EnterpriseArchetype = Literal[
    "ui_anchor", "batch_anchor", "data_provider", "shared_logic", "interface", "unresolved"
]


# ---------------------------------------------------------
# 2. The Plugin Manifest Contract (DTO)
# ---------------------------------------------------------
class PluginManifest(BaseModel):
    """
    The strict contract that all legacy language plugins must fulfill to register
    their vocabulary and reasoning profile with the RIP Core Engine.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    plugin_name: str = Field(
        ..., description="Unique identifier for the plugin (e.g., 'pb_plugin', 'delphi_plugin')"
    )

    supported_extensions: list[str] = Field(
        ..., description="File extensions claimed by this plugin (e.g., ['.srw', '.frm'])"
    )

    # Task 1.1: Extension-to-Archetype Mapping for Orchestrator Scalability
    extension_archetype_map: dict[str, EnterpriseArchetype] = Field(
        ...,
        description="Maps file extensions explicitly to their strict Enterprise Archetype definition.",
    )

    archetypes: list[EnterpriseArchetype] = Field(
        ..., description="The subset of enterprise archetypes this plugin is capable of extracting."
    )

    risk_flags: list[str] = Field(
        ..., description="Plugin-specific landmines (e.g., 'legacy_ui_coupling', 'bde_coupling')."
    )

    paradigm_hints: dict[EnterpriseArchetype, str] = Field(
        ..., description="AI prompt injection hints, strictly keyed by the Enterprise Archetype."
    )

    # ---------------------------------------------------------
    # NEW (Multi-Language Decoupling): Encoding Preference List
    # ---------------------------------------------------------
    preferred_encodings: list[str] = Field(
        default_factory=lambda: ["utf-8"],
        description=(
            "Ordered list of character encodings to try when reading source files for this language. "
            "The orchestrator will attempt each encoding in sequence, stopping at the first success. "
            "Override in plugin manifests for legacy character sets "
            "(e.g., UTF-16 LE for PowerBuilder, cp037/EBCDIC for mainframe COBOL). "
            "Default: ['utf-8'] — safe fallback for modern source repositories."
        ),
    )

    # ---------------------------------------------------------
    # Contract Validation Rules
    # ---------------------------------------------------------

    @field_validator("risk_flags")
    @classmethod
    def validate_risk_flags_format(cls, v: list[str]) -> list[str]:
        """
        Enforces the ^[a-z_]+$ pattern required by the dynamic JSON schemas.
        Traps invalid flags (e.g., spaces, uppercase) before they reach the engine.
        """
        pattern = re.compile(r"^[a-z_]+$")
        for flag in v:
            if not pattern.match(flag):
                raise ValueError(f"Invalid risk flag '{flag}'. Must be snake_case (^[a-z_]+$).")
        # Deduplicate while preserving order
        return list(dict.fromkeys(v))

    @field_validator("supported_extensions")
    @classmethod
    def normalize_extensions(cls, v: list[str]) -> list[str]:
        """
        Normalizes extensions to lowercase and ensures they begin with a dot.
        """
        normalized = []
        for ext in v:
            ext = ext.strip().lower()
            if not ext.startswith("."):
                ext = f".{ext}"
            normalized.append(ext)
        return list(dict.fromkeys(normalized))

    @field_validator("extension_archetype_map", mode="before")
    @classmethod
    def normalize_extension_map_keys(cls, v: Any) -> Any:
        """
        Task 1.1 Key Guardrail: Normalizes map extensions to lowercase dot-prefixed
        format to ensure clean lookups during cross-field verification.
        """
        if isinstance(v, dict):
            normalized = {}
            for ext, archetype in v.items():
                clean_ext = ext.strip().lower()
                if not clean_ext.startswith("."):
                    clean_ext = f".{clean_ext}"
                normalized[clean_ext] = archetype
            return normalized
        return v

    @field_validator("preferred_encodings")
    @classmethod
    def validate_preferred_encodings(cls, v: list[str]) -> list[str]:
        """
        Ensures the encoding list is non-empty and that each entry is a non-blank string.
        Does not validate that the encoding is a known Python codec — that is deferred
        to runtime to preserve flexibility for custom codec registrations.
        """
        if not v:
            raise ValueError(
                "preferred_encodings must contain at least one encoding entry (e.g., ['utf-8'])."
            )
        for enc in v:
            if not enc or not enc.strip():
                raise ValueError(f"preferred_encodings contains an empty or blank entry: {v!r}")
        return v

    @model_validator(mode="after")
    def validate_hints_and_mappings_match(self) -> "PluginManifest":
        """
        Enterprise Integrity Check:
        Ensures that every archetype claimed in 'archetypes' has a corresponding
        extraction rule in 'paradigm_hints', and vice versa.
        Enforces complete 1:1 coverage parity between supported_extensions and extension_archetype_map.
        """
        claimed_archetypes = set(self.archetypes)
        hint_keys = set(self.paradigm_hints.keys())

        # Check 1: Did the plugin claim an archetype but fail to provide the AI extraction rule?
        missing_hints = claimed_archetypes - hint_keys
        if missing_hints:
            raise ValueError(
                f"Contract Violation in {self.plugin_name}: Missing 'paradigm_hints' for claimed archetypes: {missing_hints}"
            )

        # Check 2: Did the plugin provide rules for an archetype it doesn't officially support?
        extra_hints = hint_keys - claimed_archetypes
        if extra_hints:
            raise ValueError(
                f"Contract Violation in {self.plugin_name}: Provided 'paradigm_hints' for unclaimed archetypes: {extra_hints}"
            )

        # Check 3: Are any of the hint strings empty?
        for arch, hint in self.paradigm_hints.items():
            if not hint or not hint.strip():
                raise ValueError(
                    f"Contract Violation in {self.plugin_name}: Empty hint string provided for archetype '{arch}'"
                )

        # Task 1.1 Guardrail Checks: Ensure exact mapping coherence
        supported_exts_set = set(self.supported_extensions)
        mapped_exts_set = set(self.extension_archetype_map.keys())

        # Check 4: Mapped extensions must be explicitly listed in supported_extensions
        unsupported_mapped_exts = mapped_exts_set - supported_exts_set
        if unsupported_mapped_exts:
            raise ValueError(
                f"Contract Violation in {self.plugin_name}: 'extension_archetype_map' contains extensions "
                f"not listed in 'supported_extensions': {unsupported_mapped_exts}"
            )

        # Check 5: Total coverage validation (every supported extension must be accounted for in the map)
        unmapped_supported_exts = supported_exts_set - mapped_exts_set
        if unmapped_supported_exts:
            raise ValueError(
                f"Contract Violation in {self.plugin_name}: 'supported_extensions' contains extensions "
                f"missing from the 'extension_archetype_map': {unmapped_supported_exts}"
            )

        # Check 6: Mapped archetypes must match officially claimed capabilities
        mapped_archetypes = set(self.extension_archetype_map.values())
        undeclared_archetypes = mapped_archetypes - claimed_archetypes
        if undeclared_archetypes:
            raise ValueError(
                f"Contract Violation in {self.plugin_name}: 'extension_archetype_map' references archetypes "
                f"not declared in 'archetypes': {undeclared_archetypes}"
            )

        return self


# ---------------------------------------------------------
# 3. Artifact Analysis Type Definitions (Task 1.4 AST Upgrade)
# ---------------------------------------------------------
# Using TypedDict to provide strict IDE contract enforcement without
# runtime Pydantic overhead inside high-speed AST parsing loops.


class EdgeDict(TypedDict, total=False):
    target: str  # Required
    type: str  # Required
    line: int  # Optional (Legacy regex coordinate)
    line_start: int  # Optional (AST precise start)
    line_end: int  # Optional (AST precise end)
    column: int  # Optional (AST precise column)
    resolved: bool  # Optional (Indicates if dependency was mapped)


class MaskedRegionDict(TypedDict):
    category: str
    start: int
    end: int
    raw_text: str


class LandmineDict(TypedDict):
    flags: list[str]
    severity: str
    normalized_score: int
    masked_regions: list[MaskedRegionDict] | None


class MetricsDict(TypedDict, total=False):
    lines_of_code: int  # Required
    script_lines: int  # Required
    function_count: int
    event_count: int
    ast_health_score: float  # AST specific
    total_nodes: int  # AST specific
    error_nodes: int  # AST specific


class ArtifactAnalysisResult(TypedDict):
    metrics: MetricsDict
    edges: list[EdgeDict]
    signals: dict[str, bool]
    landmines: LandmineDict


# ---------------------------------------------------------
# 4. The Abstract Base Class
# ---------------------------------------------------------
class LanguagePluginBase(ABC):
    """
    The strict contract every language adapter must fulfill.
    The core orchestrator relies exclusively on these methods to build the global graph.
    """

    def __init__(self, source_dir: Path):
        self.source_dir = Path(source_dir)

    @abstractmethod
    def get_manifest(self) -> PluginManifest:
        """
        MUST return the PluginManifest defining this scanner's capabilities,
        risk flags, and prompt hints.
        """
        pass

    @property
    @abstractmethod
    def supported_extensions(self) -> set[str]:
        """
        Returns a set of file extensions (lowercase) this plugin claims.
        Example: {".frm", ".bas", ".cls"}
        """
        pass

    @abstractmethod
    def discover(self) -> list[dict[str, Any]]:
        """
        Scans the source directory for supported files and establishes baseline identities.

        Returns:
            A list of dictionary objects representing the raw artifacts.
            Required keys per artifact: 'id', 'path', 'file_name', 'type', 'paradigm_language'
        """
        pass

    @abstractmethod
    def analyze_artifact(self, file_path: Path) -> ArtifactAnalysisResult:
        """
        SINGLE-PASS FORENSIC ANALYSIS.
        Routes the file to the EnterpriseHybridParser (AST + Regex Masking).

        Returns:
            ArtifactAnalysisResult: A strictly typed dictionary containing the
            metrics, topological edges, dynamic signals, and landmines.
        """
        pass

    # ---------------------------------------------------------
    # NEW (Multi-Language Decoupling): Optional Override Hooks
    # ---------------------------------------------------------
    # These methods have safe default implementations and are intentionally
    # NOT abstract, so all existing plugin subclasses remain valid without
    # any changes. Plugins override these to inject language-specific behaviour
    # into the core orchestrator without coupling the orchestrator to the language.

    def get_filename_candidates(self, art_id: str, ext: str) -> list[str]:
        """
        Returns an ordered list of candidate filenames to attempt when resolving
        an abstract artifact ID to a physical file path on disk.

        The default implementation returns a single unprefixed candidate:
            ["{art_id}{ext}"]

        Override in plugins that use IDE-mandated naming prefixes. For example:
            - PowerBuilder uses w_ for windows (.srw), d_ for DataWindows (.srd), etc.
            - Other future adapters may use module/ or pkg/ path prefixes.

        Args:
            art_id: The canonical artifact identifier (e.g., "mainwindow", "d_orders").
            ext:    The file extension including the leading dot (e.g., ".srw", ".frm").

        Returns:
            List[str]: Ordered candidate filenames, most-specific first.
                       All entries are lowercased by convention.
        """
        return [f"{art_id}{ext}".lower()]

    def get_preferred_encodings(self) -> list[str]:
        """
        Returns the ordered list of character encodings the orchestrator should
        try when reading source files managed by this plugin.

        The default implementation delegates to the manifest's preferred_encodings
        field, so the authoritative encoding list is declared in one place
        (get_manifest()) and automatically surfaced here.

        Override this method only if encoding selection requires runtime logic
        that cannot be expressed as a static list in the manifest (e.g., BOM
        detection that selects UTF-16 LE vs BE at runtime — see pb_plugin.py
        for the BOM-aware read_pb_text() helper which supersedes this list).

        Returns:
            List[str]: Ordered encoding labels (e.g., ["utf-16", "utf-8", "cp1252"]).
                       The orchestrator stops at the first successful decode.
        """
        return list(self.get_manifest().preferred_encodings)

    def get_dep_resolvers(self) -> list[Callable[[str], list[str]]]:
        """
        Returns a list of dependency-resolver callables specific to this language.

        Each callable has the signature:
            resolver(raw_source: str) -> List[str]

        Where the return value is a list of artifact IDs that the source file
        depends on (i.e., nodes to add to the BFS traversal queue in _gather_mfu_code).

        The default implementation returns an empty list (no extra dep resolution).

        Override in plugins to expose language-specific dependency patterns, for example:
            - PowerBuilder: DataWindow DataObject= property, global type...from ancestry,
              MenuName= assignments.
            - VB6: Dim x As New ClassName, Set x = New ClassName, CreateObject() calls.
            - COBOL: COPY <copybook> statements, CALL <program> statements.

        Returns:
            List[Callable[[str], List[str]]]: Zero or more resolver callables.
                Each callable is stateless and reads only from its str argument.
        """
        return []

    def strip_noise(self, content: str, file_ext: str) -> str:
        """
        Tier 2 Context Budget Hook — removes language-specific noise from source
        file content when the total payload approaches the LLM context limit.

        This method is called ONLY under budget pressure (i.e., after primary
        artifacts already consumed most of the allowed character budget).  It is
        never called speculatively on normal-sized payloads.

        Contract:
            - MUST preserve all business logic, event handlers, SQL, calculations,
              function/method signatures, data structure definitions, and constants.
            - MAY safely remove purely cosmetic or IDE-generated metadata that
              carries zero semantic value for requirements extraction:
                  PowerBuilder : visual coordinate properties (X=, Y=, Width=, etc.)
                  VB6          : designer Begin/End blocks, Attribute VB_* lines
                  COBOL        : IDENTIFICATION DIVISION padding paragraphs, comment lines
            - MUST return syntactically coherent content (no mid-block truncation).
            - MUST be idempotent — calling twice produces the same result as once.

        The default implementation is a no-op (returns content unchanged).
        Override in language plugins to provide language-specific stripping.

        Args:
            content  : Raw source file text as decoded string.
            file_ext : Lowercase file extension including dot (e.g. ".srw", ".frm").
                       Allows a single plugin to apply different rules per file type.

        Returns:
            str: Noise-stripped content, guaranteed non-empty if input was non-empty.
        """
        return content

    def extract_structural_summary(self, content: str, file_ext: str, char_budget: int) -> str:
        """
        Tier 4 Context Budget Hook — extracts the highest-value structural sections
        from a source file when it cannot fit in the context budget even after Tier 2
        noise stripping.

        Called ONLY when a file would otherwise be fully omitted (former Tier 3).
        Never called speculatively on files that fit within budget.

        Contract:
        ──────────────────────────────────────────────────────────────────────────
        • Output MUST be ≤ char_budget characters.
        • MUST preserve exact source syntax — no paraphrasing, no prose rewriting.
        • MUST NOT truncate a logical block mid-way (e.g. mid-function, mid-column
          definition, mid-COBOL paragraph).  Drop entire lower-priority blocks
          before partially including a higher-priority one.
        • MUST prioritise content in this order:
            data definitions > business logic / SQL > structural declarations
            > layout / cosmetic content (drop last).
        • MUST annotate omitted sections with a structured [TIER-4 OMITTED] marker
          so the spec LLM can calibrate its confidence correctly.
        • MUST be deterministic and idempotent.
        ──────────────────────────────────────────────────────────────────────────

        Default implementation (used for unknown / future language paradigms):
            Head-70% + Tail-30% of char_budget, separated by an omission marker.
            This guarantees something semantically useful is always included for
            any language not yet covered by a concrete plugin override, without
            any changes to the orchestrator or registry.

        Override in concrete plugin subclasses to provide language-specific
        extraction targeting known high-value sections:
            PowerBuilder : table()/column()/compute() blocks for .srd DataWindows;
                           event/function signatures for .srw/.sru script files.
            COBOL        : DATA DIVISION in full + PROCEDURE DIVISION paragraph names.
            VB6          : Type/Enum/Declare blocks + Sub/Function/Property signatures.
            Future langs : Override here — zero changes to orchestrator or registry.

        Args:
            content    : Raw (or noise-stripped) source text as a decoded string.
            file_ext   : Lowercase file extension including dot (e.g. ".srd", ".cbl").
            char_budget: Maximum characters the returned string may contain.

        Returns:
            str: Structural extract, guaranteed ≤ char_budget chars and non-empty
                 if content was non-empty.
        """
        if len(content) <= char_budget:
            return content  # Already fits — return as-is, no extraction needed.

        # Build a worst-case marker to measure its overhead before splitting content.
        # The actual marker is identical except for the exact omitted-chars count
        # (a digit string that is always ≤ the placeholder width).
        _class_name = self.__class__.__name__
        _marker_template = (
            f"\n\n... [TIER-4 STRUCTURAL SUMMARY: {len(content):,} chars omitted — "
            f"override extract_structural_summary() in the "
            f"'{_class_name}' plugin for targeted {file_ext} extraction.] ...\n\n"
        )
        _marker_overhead = len(_marker_template)
        _content_budget = max(0, char_budget - _marker_overhead)

        head_budget = int(_content_budget * 0.70)
        tail_budget = _content_budget - head_budget
        omitted_chars = len(content) - head_budget - tail_budget

        head = content[:head_budget]
        tail = content[len(content) - tail_budget :]
        marker = (
            f"\n\n... [TIER-4 STRUCTURAL SUMMARY: {omitted_chars:,} chars omitted — "
            f"override extract_structural_summary() in the "
            f"'{_class_name}' plugin for targeted {file_ext} extraction.] ...\n\n"
        )
        return head + marker + tail
