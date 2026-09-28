import logging
import threading
from typing import Any

from .plugin_base import EnterpriseArchetype, LanguagePluginBase, PluginManifest

logger = logging.getLogger(__name__)


class PluginRegistry:
    """
    Thread-safe Singleton Registry for managing Polyglot Language Plugins.
    Acts as the centralized "Source of Truth" for dynamic AI vocabularies,
    risk flags, and JSON Schema hydration.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(PluginRegistry, cls).__new__(cls)
                cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        """Internal initializer to separate state creation from instantiation."""
        self._manifests: dict[str, PluginManifest] = {}
        self._plugins: dict[str, LanguagePluginBase] = {}  # Track live plugin instances internally

        # NEW: Secondary lookup keyed by lowercase plugin_name for paradigm-language routing.
        # Artifact metadata stores paradigm_language as a lowercase string (e.g., "powerbuilder",
        # "vb6", "cobol"). This map allows O(1) lookup without iterating all manifests.
        self._plugins_by_paradigm: dict[str, LanguagePluginBase] = {}

        # Caches to prevent re-aggregation overhead during Stage 4 extraction
        self._cached_risk_flags: list[str] | None = None
        self._cached_archetypes: list[EnterpriseArchetype] | None = None
        self._cached_extensions: list[str] | None = None  # Extension cache

    def register(self, plugin: LanguagePluginBase) -> None:
        """
        Registers an active plugin instance and extracts its configuration manifest.
        Invalidates caches to ensure subsequent requests include the new vocabulary.
        The plugin is indexed under both its canonical plugin_name and its lowercase
        paradigm key to support dual-path lookups by the orchestrator.
        """
        manifest = plugin.get_manifest()

        with self._lock:
            if manifest.plugin_name in self._manifests:
                logger.warning(
                    f"Overwriting existing plugin registration for: {manifest.plugin_name}"
                )

            self._manifests[manifest.plugin_name] = manifest
            self._plugins[manifest.plugin_name] = plugin

            # NEW: Index by lowercase plugin_name so artifact metadata lookups
            # (which use paradigm_language = "powerbuilder", "vb6", "cobol") work directly.
            self._plugins_by_paradigm[manifest.plugin_name.lower()] = plugin

            # Invalidate caches upon new registration
            self._cached_risk_flags = None
            self._cached_archetypes = None
            self._cached_extensions = None  # Invalidate extension cache
            logger.info(f"Successfully registered plugin: {manifest.plugin_name}")

    def get_all_risk_flags(self) -> list[str]:
        """
        Aggregates and deduplicates all risk flags from active plugins.
        Returns a deterministically sorted list.
        """
        with self._lock:
            if self._cached_risk_flags is not None:
                return self._cached_risk_flags

            flags = set()
            for manifest in self._manifests.values():
                flags.update(manifest.risk_flags)

            self._cached_risk_flags = sorted(list(flags))
            return self._cached_risk_flags

    def get_all_archetypes(self) -> list[EnterpriseArchetype]:
        """
        Aggregates and deduplicates all supported archetypes from active plugins.
        Returns a deterministically sorted list.
        """
        with self._lock:
            if self._cached_archetypes is not None:
                return self._cached_archetypes

            archetypes = set()
            for manifest in self._manifests.values():
                archetypes.update(manifest.archetypes)

            self._cached_archetypes = sorted(list(archetypes))
            return self._cached_archetypes

    def get_all_supported_extensions(self) -> list[str]:
        """
        Aggregates and deduplicates all supported file extensions from active plugins.
        Returns a deterministically sorted list.
        """
        with self._lock:
            if self._cached_extensions is not None:
                return self._cached_extensions

            extensions = set()
            for manifest in self._manifests.values():
                extensions.update(manifest.supported_extensions)

            self._cached_extensions = sorted(list(extensions))
            return self._cached_extensions

    def get_plugin_for_extension(self, extension: str) -> LanguagePluginBase | None:
        """
        DYNAMIC DISCOVERY LOOKUP: Inspects self-reported extension maps across all registered
        microkernels to return the live plugin instance handling the target file type.
        Eliminates hardcoded routing dictionaries in core orchestrators.
        """
        normalized_ext = extension.strip().lower()
        if not normalized_ext.startswith("."):
            normalized_ext = f".{normalized_ext}"

        with self._lock:
            for name, manifest in self._manifests.items():
                # Scan Task 1.1 self-reported manifest archetype map
                if normalized_ext in manifest.extension_archetype_map:
                    return self._plugins.get(name)
        return None

    def get_hints_for_paradigm(self, paradigm_name: str, archetype: EnterpriseArchetype) -> str:
        """
        Retrieves specific prompt injection hints for a given language paradigm and archetype.
        Returns an empty string if the paradigm or archetype hint does not exist.
        """
        manifest = self._manifests.get(paradigm_name)
        if not manifest:
            logger.debug(f"Paradigm '{paradigm_name}' not found in registry.")
            return ""

        return manifest.paradigm_hints.get(archetype, "")

    def is_extension_supported(self, extension: str) -> bool:
        """
        Helper method for the Orchestrator to route files to the correct plugin.
        """
        return self.get_plugin_for_extension(extension) is not None

    # ---------------------------------------------------------
    # NEW (Multi-Language Decoupling): Broker Dispatcher Methods
    # ---------------------------------------------------------
    # These methods decouple the core orchestrator from language-specific
    # knowledge by delegating to the registered plugin instance for the
    # given paradigm. All methods return safe defaults when no plugin is found,
    # so the orchestrator degrades gracefully for unregistered languages.

    def get_filename_candidates(self, paradigm_language: str, art_id: str, ext: str) -> list[str]:
        """
        Dispatches to the correct plugin's get_filename_candidates() method.

        The orchestrator calls this when it needs to resolve an abstract artifact ID
        (e.g., "mainwindow") into a list of candidate physical filenames to search for
        on disk (e.g., ["mainwindow.srw", "w_mainwindow.srw"]).

        Args:
            paradigm_language: The lowercase language key from artifact metadata
                               (e.g., "powerbuilder", "vb6", "cobol").
            art_id: The canonical artifact identifier (lowercase, no extension).
            ext:    The file extension including the leading dot (e.g., ".srw").

        Returns:
            List[str]: Ordered candidate filenames from the plugin. Falls back to
                       ["{art_id}{ext}"] if no plugin is registered for the paradigm.
        """
        plugin = self._plugins_by_paradigm.get(paradigm_language.lower())
        if plugin:
            return plugin.get_filename_candidates(art_id, ext)
        logger.debug(
            f"[PluginRegistry] No plugin for paradigm '{paradigm_language}'. "
            f"Using default filename candidate: {art_id}{ext}"
        )
        return [f"{art_id}{ext}".lower()]

    def get_preferred_encodings(self, paradigm_language: str) -> list[str]:
        """
        Returns the ordered encoding fallback list for the given language paradigm.

        The orchestrator calls this inside _read_smart() to determine which character
        encodings to attempt when opening a source file, instead of using a hardcoded
        global list that privileges PowerBuilder's UTF-16 over all other languages.

        Args:
            paradigm_language: The lowercase language key from artifact metadata.

        Returns:
            List[str]: Ordered encoding labels (e.g., ["utf-16", "utf-8", "cp1252"]).
                       Falls back to ["utf-8", "cp1252", "latin-1"] for unknown paradigms.
        """
        plugin = self._plugins_by_paradigm.get(paradigm_language.lower())
        if plugin:
            return plugin.get_preferred_encodings()
        logger.debug(
            f"[PluginRegistry] No plugin for paradigm '{paradigm_language}'. "
            "Using generic encoding fallback: ['utf-8', 'cp1252', 'latin-1']"
        )
        return ["utf-8", "cp1252", "latin-1"]

    def get_dep_resolvers(self, paradigm_language: str) -> list[Any]:
        """
        Returns all dependency-resolver callables registered by the plugin for the
        given language paradigm.

        The orchestrator calls this inside _gather_mfu_code() to extract additional
        dependency IDs from raw source content, replacing the hardcoded whitelist of
        PB/VB6 regex patterns that were previously embedded in the extractor.

        Each returned callable has the signature:
            resolver(raw_source: str) -> List[str]

        Args:
            paradigm_language: The lowercase language key from artifact metadata.

        Returns:
            List[Callable]: Zero or more resolver callables. Returns an empty list
                            if no plugin is found or the plugin exposes no resolvers.
        """
        plugin = self._plugins_by_paradigm.get(paradigm_language.lower())
        if plugin:
            return plugin.get_dep_resolvers()
        logger.debug(
            f"[PluginRegistry] No plugin for paradigm '{paradigm_language}'. "
            "Returning empty dep resolver list."
        )
        return []

    def strip_noise(self, paradigm_language: str, content: str, file_ext: str) -> str:
        """
        Tier 2 context budget broker: delegates noise stripping to the language plugin.

        Called by _gather_mfu_code() only when a dependency file exceeds its
        remaining character budget (Tier 2 path).  Returns the stripped content
        so the caller can re-evaluate whether it now fits within budget.

        If no plugin is registered for the given paradigm, the original content
        is returned unchanged (safe no-op fallback).

        Args:
            paradigm_language : Lowercase language key from artifact metadata
                                 (e.g., 'powerbuilder', 'vb6', 'cobol').
            content           : Raw source file text as decoded string.
            file_ext          : Lowercase file extension including dot (e.g. ".srw").

        Returns:
            str: Noise-stripped content. Always non-empty if input was non-empty.
        """
        plugin = self._plugins_by_paradigm.get(paradigm_language.lower())
        if plugin:
            return plugin.strip_noise(content, file_ext)
        logger.debug(
            f"[PluginRegistry] No plugin for paradigm '{paradigm_language}'. "
            "Returning original content (no-op strip)."
        )
        return content

    def extract_structural_summary(
        self,
        paradigm_language: str,
        content: str,
        file_ext: str,
        char_budget: int,
    ) -> str:
        """
        Tier 4 context budget broker: delegates structural summary extraction
        to the language plugin registered for the given paradigm.

        Called by _gather_mfu_code() when a file would otherwise be fully
        omitted (former Tier 3).  The plugin is responsible for returning
        the highest-value structural sections within char_budget, without
        truncating mid-block.

        Universal fallback for unregistered paradigms:
          When no plugin is registered (e.g. a future language added before
          its plugin is written), a safe head-70% + tail-30% split is applied
          so the file is never completely invisible to the spec LLM.  The
          omission marker names the unregistered paradigm, prompting the
          developer to implement a dedicated plugin override.

        Args:
            paradigm_language : Lowercase language key from artifact metadata
                                 (e.g., 'powerbuilder', 'vb6', 'cobol').
            content           : Raw (or Tier-2-stripped) source file text.
            file_ext          : Lowercase file extension including dot (e.g. ".srd").
            char_budget       : Maximum character count for the returned string.

        Returns:
            str: Structural summary guaranteed to fit within char_budget.
                 Always non-empty when content is non-empty and char_budget > 0.
        """
        # Fast path — already fits, nothing to summarise
        if len(content) <= char_budget:
            return content

        # Dispatch to the registered plugin (PB / COBOL / VB6 / any future plugin)
        plugin = self._plugins_by_paradigm.get(paradigm_language.lower())
        if plugin:
            return plugin.extract_structural_summary(content, file_ext, char_budget)

        # ── Universal fallback: head-70% + tail-30% ───────────────────────
        # No plugin registered yet for this language.  Apply the base-class
        # split so at minimum the start (declarations) and end (final logic)
        # are visible to the spec LLM.  The omission marker names the
        # unregistered paradigm to guide future plugin development.
        logger.warning(
            f"[PluginRegistry] No plugin registered for paradigm "
            f"'{paradigm_language}' — applying generic head/tail Tier 4 split. "
            f"Consider adding a LanguagePluginBase subclass with an "
            f"extract_structural_summary() override for this language."
        )
        # Pre-compute marker to measure its overhead; build content split to fit.
        _marker_template = (
            f"\n\n... [TIER-4 STRUCTURAL SUMMARY: {len(content):,} chars omitted — "
            f"no plugin registered for paradigm '{paradigm_language}'. "
            f"Implement extract_structural_summary() in a LanguagePluginBase "
            f"subclass for targeted {file_ext} extraction.] ...\n\n"
        )
        _content_budget = max(0, char_budget - len(_marker_template))
        head_budget = int(_content_budget * 0.70)
        tail_budget = _content_budget - head_budget
        omitted_chars = len(content) - head_budget - tail_budget
        head = content[:head_budget]
        tail = content[len(content) - tail_budget :]
        marker = (
            f"\n\n... [TIER-4 STRUCTURAL SUMMARY: {omitted_chars:,} chars omitted — "
            f"no plugin registered for paradigm '{paradigm_language}'. "
            f"Implement extract_structural_summary() in a LanguagePluginBase "
            f"subclass for targeted {file_ext} extraction.] ...\n\n"
        )
        return head + marker + tail

    def get_archetype_for_extension(self, extension: str) -> str:
        """
        Returns the EnterpriseArchetype string for a given file extension.

        The orchestrator calls this to replace hardcoded arch_map dictionaries
        (e.g., {"pb_window": "ui_anchor", "cobol_program_id": "batch_anchor"})
        with a
        single registry lookup driven by the plugins' self-reported
        extension_archetype_map contracts.

        Args:
            extension: The file extension including the leading dot (e.g., ".srw", ".frm").
                       Case-insensitive; normalized to lowercase internally.

        Returns:
            str: The matched EnterpriseArchetype (e.g., "ui_anchor", "batch_anchor").
                 Returns "unresolved" if no registered plugin claims the extension.
        """
        normalized_ext = extension.strip().lower()
        if not normalized_ext.startswith("."):
            normalized_ext = f".{normalized_ext}"

        with self._lock:
            for name, manifest in self._manifests.items():
                if normalized_ext in manifest.extension_archetype_map:
                    return manifest.extension_archetype_map[normalized_ext]

        logger.debug(
            f"[PluginRegistry] Extension '{normalized_ext}' not found in any registered manifest. "
            "Returning 'unresolved'."
        )
        return "unresolved"
