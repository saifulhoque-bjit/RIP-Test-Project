import copy
import json
from pathlib import Path
import threading

import jsonschema

# Single source of truth for system-level risk flags.
# Imported here so the schema hydration step uses the SAME complete allowlist
# as vocabulary_sanitizer.sanitize_mfu_vocabulary().  Both must stay in sync —
# adding a new flag in vocabulary_sanitizer.SYSTEM_RISK_FLAGS is sufficient;
# this import picks it up automatically.
from .vocabulary_sanitizer import SYSTEM_RISK_FLAGS


class SchemaValidationError(Exception):
    pass


class JSONSchemaValidator:
    # ---------------------------------------------------------
    # Class-Level Compile-Once Cache
    # Prevents File I/O and traversal overhead across instances
    # ---------------------------------------------------------
    _schema_cache = {}
    _cache_lock = threading.Lock()

    def __init__(self, schema_path, registry=None):
        self.schema_path = Path(schema_path)

        # Cache key encodes both the schema file path AND whether it was
        # hydrated.  This prevents a raw (registry=None) instantiation from
        # poisoning the cache for a later hydrated (registry=<obj>) one.
        # SYSTEM_RISK_FLAGS is a module-level constant so it never changes
        # within a process; no need to include it in the key.
        cache_key = f"{self.schema_path}::{'hydrated' if registry else 'raw'}"

        # 1. Check Cache First (Thread-Safe)
        with self._cache_lock:
            if cache_key in self._schema_cache:
                self.schema = self._schema_cache[cache_key]
                self._is_hydrated = registry is not None
                return

        # 2. Cache Miss: Perform File I/O
        if not self.schema_path.exists():
            raise FileNotFoundError(f"Schema file not found: {self.schema_path}")

        with self.schema_path.open(encoding="utf-8") as f:
            base_schema = json.load(f)

        # 3. Perform Hydration
        self._is_hydrated = False
        if registry:
            compiled_schema = self.hydrate_schema(base_schema, registry)
            self._is_hydrated = True
        else:
            compiled_schema = base_schema

        # 4. Store in Cache (Thread-Safe)
        with self._cache_lock:
            self._schema_cache[cache_key] = compiled_schema
            self.schema = compiled_schema

    def hydrate_schema(self, base_schema: dict, registry) -> dict:
        """
        Traverses the base JSON schema looking for $DYNAMIC_ENUM markers.
        Replaces regex patterns with strictly compiled enum arrays from the PluginRegistry.
        """
        hydrated = copy.deepcopy(base_schema)

        def traverse(node):
            if isinstance(node, dict):
                desc = node.get("description", "")
                if isinstance(desc, str) and "$DYNAMIC_ENUM:" in desc:
                    # Extract the exact marker, stripping out any piped comments
                    marker = desc.split("$DYNAMIC_ENUM:")[1].split("|")[0].strip()

                    enums = []

                    # Dynamically resolve the requested vocabulary
                    if marker == "registry.risk_flags" and hasattr(registry, "get_all_risk_flags"):
                        # Plugin flags from the active PluginRegistry microkernel.
                        plugin_enums = list(registry.get_all_risk_flags() or [])
                        # System flags emitted by pipeline machinery (orphan_sweeper,
                        # review_agent) — these are never registered via plugins but
                        # are always valid.  SYSTEM_RISK_FLAGS is the single source
                        # of truth shared with vocabulary_sanitizer.
                        enums = plugin_enums + [
                            f for f in sorted(SYSTEM_RISK_FLAGS) if f not in plugin_enums
                        ]
                    elif marker == "registry.archetypes" and hasattr(
                        registry, "get_all_archetypes"
                    ):
                        enums = registry.get_all_archetypes()
                    elif marker == "registry.paradigm_language":
                        if hasattr(registry, "get_all_paradigms"):
                            enums = registry.get_all_paradigms()
                        elif hasattr(registry, "_manifests"):
                            enums = list(registry._manifests.keys())
                    elif marker == "registry.execution_tracks":
                        if hasattr(registry, "get_all_execution_tracks"):
                            enums = registry.get_all_execution_tracks()
                        else:
                            enums = ["UI-Track", "Batch-Track", "Mixed-Track"]

                    # TASK 3.3: Fallback Logic
                    if not enums:
                        enums = ["none", "unresolved"]

                    # Inject the dynamic vocabulary
                    node["enum"] = enums

                    # Remove the regex pattern constraint
                    if "pattern" in node:
                        del node["pattern"]

                # Continue traversal
                for key, value in node.items():
                    traverse(value)

            elif isinstance(node, list):
                for item in node:
                    traverse(item)

        traverse(hydrated)
        return hydrated

    def validate_data(self, data):
        try:
            jsonschema.validate(instance=data, schema=self.schema)
        except jsonschema.ValidationError as e:
            raise SchemaValidationError(f"JSON schema validation failed:\n{e}")

    @classmethod
    def _clear_cache(cls):
        """
        TESTING ONLY: Wipes the class-level cache.
        Critical for test isolation when the PluginRegistry changes.
        """
        with cls._cache_lock:
            cls._schema_cache.clear()
