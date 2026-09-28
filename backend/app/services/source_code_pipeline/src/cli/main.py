"""
CLI Orchestrator for Requirement Intelligence Platform (RIP)
Refactored for Option B:
  Global scan → Global graph → Per-module MFU generation (sliced)

Properties:
- Deterministic
- Explainable
- High-Fidelity: Uses Global Index for identity and fuzzy linking
- INTEGRATED: Orphan Sweeper & Review Agent (Requirements Auditor)
- POLYGLOT: Dynamic Microkernel Architecture
- END-TO-END: Native execution of Specs, Neo4j, and RIP Reports (Phase 4)
- INTELLIGENT DISCOVERY: Topological/Semantic manifest generation
"""

import argparse
import importlib
import json
import os
from pathlib import Path
import sys

# --- Force litellm's LOCAL model-cost/context map (set BEFORE any litellm import) ---
# Defense-in-depth for the pipeline hang: litellm otherwise fetches its price/context
# map over the network on first use (raw.githubusercontent.com), which is NOT bounded
# by the per-call timeout and blocks the whole run before any LLM request is sent when
# that host is slow/firewalled. LLMClient already sets this, but setting it here too
# guarantees it is in place no matter which module pulls in litellm first.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

# --- UTF-8 safe console/file output (enterprise: never crash on Unicode logs) -------------
# Log lines and CJK source identifiers contain non-ASCII characters (e.g. '→' arrows,
# em-dashes, Japanese control names). On Windows the default stdout/stderr encoding is
# cp1252 ("charmap"), which raises UnicodeEncodeError and aborts the whole pipeline when
# such a character is written. Force UTF-8 with errors="replace" so output is always
# encodable regardless of the active console code page or whether output is redirected.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # Python 3.7+
    except Exception:
        pass  # very old Python or non-reconfigurable stream — degrade gracefully


# --- Dynamic Import for Downstream Phase 3/4 Tools ---
def dynamic_import(class_name, paths):
    """Tries to import a class or function from a list of possible module paths safely."""
    for path in paths:
        try:
            mod = __import__(path, fromlist=[class_name])
            return getattr(mod, class_name)
        except (ImportError, AttributeError):
            continue
    return None


SpecExtractor = dynamic_import(
    "SpecExtractor",
    [
        "app.services.source_code_pipeline.spec_extractor",
        "spec_extractor",
    ],
)
ReportGenerator = dynamic_import(
    "ReportGenerator",
    [
        "app.services.source_code_pipeline.src.tools.report_generator",
        "src.utils.report_generator",
        "src.reporting.report_generator",
        "report_generator",
    ],
)
Neo4jExporter = dynamic_import(
    "Neo4jExporter",
    [
        "app.services.source_code_pipeline.src.tools.neo4j_exporter",
        "neo4j_exporter",
        "src.utils.neo4j_exporter",
        "src.extraction.neo4j_exporter",
        "src.tools.neo4j_exporter",
    ],
)

# Stage 2.5 Intelligent Discovery Import
build_hybrid_manifest = dynamic_import(
    "build_hybrid_manifest",
    [
        "app.services.source_code_pipeline.src.tools.hybrid_manifest_generator",
        "src.tools.hybrid_manifest_generator",
        "hybrid_manifest_generator",
    ],
)

# Stage 5 Feature & User Story Agent
FeatureStoryAgent = dynamic_import(
    "FeatureStoryAgent",
    [
        "app.services.source_code_pipeline.src.tools.feature_story_agent",
        "src.tools.feature_story_agent",
        "tools.feature_story_agent",
    ],
)


# ---------------- Imports ----------------

# Global Index Builder establishes the identity universe (Task 2)
from ..ai.ai_runner import AIRunner
from ..ai.guardrails import apply_guardrails
from ..enrichment.llm_enricher import LLMEnricher

# Post-processing
from ..post_process.orphan_sweeper import sweep_orphans

# Review Agent (Task 11)
from ..review.review_agent import ReviewAgent
from ..scanner.call_graph_builder import (
    enrich_with_call_graph,
    slice_enriched_for_module,
)
from ..scanner.global_index_builder import build_global_index
from ..scanner.plugin_base import LanguagePluginBase
from ..scanner.plugin_registry import PluginRegistry  # TASK 4.1: Registry Singleton
from ..tools.mfu_comparator import compare
from ..tools.mfu_exporter import export_comparison
from ..utils.json_schema_validator import JSONSchemaValidator

# ---------------- Paths ----------------
# Initialized to defaults, but can be dynamically overridden by CLI args via set_project_paths()

BASE_PROJECT = Path("projects/sample_project")
MODULES_ROOT = BASE_PROJECT / "modules"

# Config - Check project dir first, fallback to root workspace dir
if (BASE_PROJECT / "project_config.json").exists():
    PROJECT_CONFIG = BASE_PROJECT / "project_config.json"
else:
    PROJECT_CONFIG = Path("project_config.json")

GLOBAL_DIR = BASE_PROJECT / "_global"
# Path for high-fidelity manifest
GLOBAL_INDEX = GLOBAL_DIR / "global_index.json"
GLOBAL_DETECTED = GLOBAL_DIR / "artifacts_detected.json"
GLOBAL_ENRICHED = GLOBAL_DIR / "artifacts_enriched.json"
MODULE_MANIFEST = GLOBAL_DIR / "module_manifest.json"  # Stage 2.5 AI Module Output


def set_project_paths(project_dir: str):
    """
    ENTERPRISE FIX: Dynamically updates all global path dependencies
    based on the --project argument passed via CLI.
    """
    global BASE_PROJECT, MODULES_ROOT, PROJECT_CONFIG, GLOBAL_DIR
    global GLOBAL_INDEX, GLOBAL_DETECTED, GLOBAL_ENRICHED, MODULE_MANIFEST

    BASE_PROJECT = Path(project_dir)
    MODULES_ROOT = BASE_PROJECT / "modules"

    if (BASE_PROJECT / "project_config.json").exists():
        PROJECT_CONFIG = BASE_PROJECT / "project_config.json"
    else:
        PROJECT_CONFIG = Path("project_config.json")

    GLOBAL_DIR = BASE_PROJECT / "_global"
    GLOBAL_INDEX = GLOBAL_DIR / "global_index.json"
    GLOBAL_DETECTED = GLOBAL_DIR / "artifacts_detected.json"
    GLOBAL_ENRICHED = GLOBAL_DIR / "artifacts_enriched.json"
    MODULE_MANIFEST = GLOBAL_DIR / "module_manifest.json"

    # Enterprise Fix: Inject the exact project config path into LLMClient via env var
    # so it always finds the correct project_config.json regardless of the working
    # directory from which the CLI is invoked (e.g., D:\poc\rip-polyglot-workspace).
    # LLMClient checks RIP_LLM_CONFIG_PATH as candidate[0] — highest priority.
    os.environ["RIP_LLM_CONFIG_PATH"] = str(PROJECT_CONFIG)


# Schemas
SCHEMA_MFUS_PROPOSED = Path("schemas/mfus_proposed.schema.json")
SCHEMA_MFUS_FINAL = Path("schemas/mfus_final.schema.json")
SCHEMA_GUARDRAIL = Path("schemas/guardrail_report.schema.json")
SCHEMA_SEMANTIC = Path("schemas/artifacts_semantic.schema.json")

# Prompts
PROMPT_MFU_SYS = Path("prompts/mfu_system_prompt.txt")
PROMPT_MFU_GEN = Path("prompts/mfu_generation_prompt.txt")
PROMPT_REVIEW_SYS = Path("prompts/review_agent_system_prompt.txt")
PROMPT_ENRICHER_SYS = Path("prompts/llm_enricher_system_prompt.txt")
PROMPT_ENRICHER_GEN = Path("prompts/llm_enricher_generation_prompt.txt")


# ---------------- Helper Logic ----------------

ACTIVE_PLUGINS = []


# ---------------------------------------------------------------------------
# Enterprise Module Class Filter
# ---------------------------------------------------------------------------


def _load_project_config() -> dict:
    """
    Load the active project_config.json safely.
    Returns an empty dict on any failure so callers never need to guard.
    """
    try:
        if PROJECT_CONFIG.exists():
            with open(PROJECT_CONFIG, encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"[WARN] Could not load project_config.json for pipeline filter: {e}")
    return {}


def _apply_module_class_filter(modules: list, project_cfg: dict) -> list:
    """
    Language-agnostic module class filter.

    Filters the module list using the module_class policy defined in
    project_config.json → module_pipeline.

    Classification priority (highest → lowest):
      1. class_overrides   — explicit per-module-ID decision in project_config
      2. module_class      — field stamped by hybrid_manifest_generator at Stage 2.5
      3. Default           — "business" (safe fallback for legacy manifests)

    Modules whose resolved class appears in skip_classes are excluded.
    Every exclusion is logged with module_id, class, and policy source so the
    operator has a full audit trail.

    Parameters
    ----------
    modules      : list  — module list from discover_modules() or --module filter
    project_cfg  : dict  — loaded project_config.json (may be empty)

    Returns
    -------
    Filtered list; order is preserved.
    """
    pipeline_cfg = project_cfg.get("module_pipeline", {})
    skip_classes = set(pipeline_cfg.get("skip_classes", ["infrastructure"]))
    overrides = pipeline_cfg.get("class_overrides", {})

    def _mid(m):
        if isinstance(m, dict):
            return m.get("module_id") or m.get("name") or ""
        return m.name  # Path object

    filtered, skipped = [], []
    for m in modules:
        mid = _mid(m)

        # Priority 1: explicit config override
        if mid in overrides and overrides[mid] != "_auto":
            m_class = overrides[mid]
            source = "config_override"
        # Priority 2: module_class field stamped by manifest generator
        elif isinstance(m, dict) and "module_class" in m:
            m_class = m["module_class"]
            source = "manifest"
        # Priority 3: default
        else:
            m_class = "business"
            source = "default"

        if m_class in skip_classes:
            skipped.append((mid, m_class, source))
        else:
            filtered.append(m)

    if skipped:
        print(
            f"\n[PIPELINE FILTER] Excluding {len(skipped)} module(s) — "
            f"class policy: skip_classes={sorted(skip_classes)}"
        )
        for mid, cls, src in skipped:
            print(f"  [SKIP][{cls.upper():14s}] {mid}  (policy: {src})")
    print(f"[PIPELINE FILTER] {len(filtered)} module(s) queued for processing.")

    return filtered


def get_project_paradigm():
    """Helper to retrieve source_paradigm from config (Task 5)."""
    if not PROJECT_CONFIG.exists():
        return "legacy_desktop"  # Default to PB
    try:
        config = json.loads(PROJECT_CONFIG.read_text(encoding="utf-8"))
        return config.get("project", {}).get("source_paradigm", "legacy_desktop")
    except Exception:
        return "legacy_desktop"


def _framework_matcher():
    """Build the vendor-library/framework exclusion matcher from project_config + the per-language
    library_excludes profile (prompts/language_profiles.yaml). Returns (matcher, filters); filters['active']
    is False when nothing is excluded (e.g. VB6/COBOL projects), so behaviour is unchanged for them."""
    try:
        from ..tools.framework_filter import load_project_config, make_matcher

        cfg = load_project_config(BASE_PROJECT) if PROJECT_CONFIG.exists() else {}
        return make_matcher(cfg)
    except Exception as e:
        print(f"[WARN] framework exclusion unavailable ({e}); indexing all files.")
        return (lambda p: False), {"active": False, "exclude_dirs": set(), "exclude_globs": []}


def get_input_root():
    """Dynamically resolves the input source folder from project_config.json, with paradigm fallbacks."""
    if PROJECT_CONFIG.exists():
        try:
            config = json.loads(PROJECT_CONFIG.read_text(encoding="utf-8"))
            source_path = config.get("project", {}).get("source_path")
            if source_path:
                return BASE_PROJECT / source_path
        except Exception as e:
            print(f"[WARN] Failed to parse {PROJECT_CONFIG.name}: {e}")

    # Legacy fallbacks if source_path is not defined in config
    paradigm = get_project_paradigm()
    if paradigm == "legacy_desktop":
        return BASE_PROJECT / "input" / "powerbuilder_src"
    elif paradigm == "mainframe_batch":
        return BASE_PROJECT / "input" / "mainframe_src"
    else:
        return BASE_PROJECT / "input" / "src"


def load_and_register_plugins(input_root: Path):
    """
    TASK 3.2: Universal Directory Scan Plugin Discovery.
    Scans the 'src/scanner/' folder automatically, dynamically loading and registering
    any class implementation that inherits from 'LanguagePluginBase' to enable zero-code scaling.
    """
    config = {}
    if PROJECT_CONFIG.exists():
        try:
            config = json.loads(PROJECT_CONFIG.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[WARN] Failed to read config for plugins: {e}")

    # Maintain project context filters to restrict active loop pipelines
    allowed_plugin_keys = config.get("project", {}).get("plugins", ["powerbuilder"])
    if isinstance(allowed_plugin_keys, str):
        allowed_plugin_keys = [allowed_plugin_keys]

    # Normalize configuration keys for exact comparison matching
    allowed_keys_normalized = {k.lower().strip() for k in allowed_plugin_keys}
    # Map friendly shorthands to standard paradigm tracking nomenclature
    if "pb" in allowed_keys_normalized:
        allowed_keys_normalized.add("powerbuilder")

    registry = PluginRegistry()
    plugins = []

    # Resolve the physical absolute scanner module directory path
    current_dir = Path(__file__).resolve().parent
    scanner_directory = current_dir.parent / "scanner"

    if not scanner_directory.exists():
        print("[ERROR] Central plugins scanner folder cannot be resolved. Discovery aborted.")
        return plugins

    # Sweep directory for code assets, skipping framework contracts
    for file_path in sorted(scanner_directory.glob("*.py"), key=lambda p: p.name.lower()):
        if file_path.name in ["__init__.py", "plugin_base.py", "plugin_registry.py"]:
            continue

        module_import_path = f"app.services.source_code_pipeline.src.scanner.{file_path.stem}"
        try:
            try:
                module = importlib.import_module(module_import_path)
            except Exception:
                module = importlib.import_module(f"src.scanner.{file_path.stem}")
            for attribute_name in dir(module):
                attribute = getattr(module, attribute_name)

                # Verify structural subclass implementation criteria
                if (
                    isinstance(attribute, type)
                    and issubclass(attribute, LanguagePluginBase)
                    and attribute is not LanguagePluginBase
                ):
                    # Instantiate microkernel via input root injection
                    plugin_instance = attribute(input_root)
                    manifest = plugin_instance.get_manifest()

                    # TASK 1.2: Bind the live plugin instance reference to the central registry lookup table
                    registry.register(plugin_instance)

                    # Core Context Matcher Check to isolate activation tracking bounds
                    p_name_lower = manifest.plugin_name.lower().strip()
                    has_extension_match = any(
                        ext.replace(".", "") in allowed_keys_normalized
                        for ext in manifest.supported_extensions
                    )

                    if p_name_lower in allowed_keys_normalized or has_extension_match:
                        plugins.append(plugin_instance)
                        print(
                            f"[REGISTRY] Registered and Activated vocabulary plugin microkernel: {manifest.plugin_name}"
                        )
                    else:
                        print(
                            f"[REGISTRY] Registered latent background plugin microkernel: {manifest.plugin_name}"
                        )

        except Exception as e:
            print(f"[WARN] Dynamic directory scan module load bypass on {file_path.name}: {e}")

    return plugins


def _active_paradigm_extensions(active_plugins) -> set:
    """Single, paradigm-scoped source of truth for which file extensions Stage 1a
    indexes: the union of supported extensions declared by the ACTIVE (vocabulary)
    plugin manifest(s). This makes the global index (Stage 1a) index exactly the
    extensions the active plugin analyzes in Stage 1b — eliminating both the
    cross-language over-inclusion (a COBOL run must not index .frm/.java) and the
    drift between the plugin manifest and the IdentityRegistry (e.g. .dcl/.bms).
    Returns an empty set only if no active plugin is resolved, in which case
    build_global_index safely falls back to its registry defaults."""
    exts: set = set()
    for p in active_plugins or []:
        try:
            exts |= {str(e).lower() for e in p.supported_extensions}
        except Exception:
            pass
    return exts


def execute_global_scan(input_root: Path, output_path: Path):
    """Dynamic Microkernel Plugin Loader for Stage 1b."""
    if not ACTIVE_PLUGINS:
        print("[ERROR] No valid plugins loaded. Scan cannot proceed.")
        return

    all_artifacts = []
    for plugin in ACTIVE_PLUGINS:
        plugin_name = plugin.__class__.__name__
        print(f"[SCAN] Running discovery for {plugin_name}...")
        try:
            raw_artifacts = plugin.discover()
            print(f"[SCAN] {plugin_name} discovered {len(raw_artifacts)} artifacts. Analyzing...")
            for raw in raw_artifacts:
                art_path = Path(raw["path"])
                analysis = plugin.analyze_artifact(art_path)
                raw.update(analysis)
                all_artifacts.append(raw)
        except Exception as e:
            print(f"[ERROR] Plugin {plugin_name} failed during execution: {e}")

    all_artifacts.sort(key=lambda x: x.get("id", ""))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps({"artifacts": all_artifacts}, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[SCAN] Polyglot scan complete. {len(all_artifacts)} total artifacts → {output_path}")


# ---------------- Single-project legacy commands ----------------


def cmd_scan():
    """
    Step 1: Global Scan (Single Project Mode) - Paradigm Aware
    """
    input_root = get_input_root()
    if not input_root.exists():
        print(f"[ERROR] Source directory not found: {input_root}")
        return

    GLOBAL_DIR.mkdir(parents=True, exist_ok=True)
    out = GLOBAL_DETECTED

    paradigm = get_project_paradigm()
    # Resolve the active plugin(s) first so Stage 1a can index exactly the
    # paradigm's declared extensions (single source of truth = plugin manifest).
    global ACTIVE_PLUGINS
    ACTIVE_PLUGINS = load_and_register_plugins(input_root)

    print(f"[SCAN] Stage 1a: Building global artifact index (Paradigm: {paradigm})...")
    _excl, _filters = _framework_matcher()
    if _filters.get("active"):
        print(
            f"[SCAN] Framework exclusion ({paradigm}): dirs={sorted(_filters['exclude_dirs'])} "
            f"patterns={_filters['exclude_globs'][:6]}"
        )
    build_global_index(
        input_root,
        GLOBAL_INDEX,
        allowed_extensions=_active_paradigm_extensions(ACTIVE_PLUGINS),
        exclude_matcher=_excl if _filters.get("active") else None,
    )

    print("[SCAN] Stage 1b: Executing Polyglot Microkernel Scan...")
    execute_global_scan(input_root, out)


def cmd_graph():
    """
    Step 2: Global Graph (Single Project Mode)
    """
    input_path = GLOBAL_DETECTED
    output_path = GLOBAL_ENRICHED

    if not input_path.exists():
        print("[GRAPH] Global artifacts_detected.json not found. Run 'scan' first.")
        return

    enrich_with_call_graph(input_path, output_path)
    print(f"[GRAPH] Output saved to {output_path}")


def cmd_ai():
    """
    Step 3: AI Processing (Deprecation Guard)
    """
    print("[ERROR] The 'ai' command is obsolete for global runs due to token limits.")
    print("Please use the 'run' command to safely execute the modular, sliced pipeline.")
    return


def cmd_compare(module_name: str):
    """
    Step 4: Compare Generated MFUs with Baselines
    """
    print(f"[COMPARE] Generating comparison report for module: {module_name}...")

    # Establish local module file locations
    module_dir = MODULES_ROOT / module_name
    proposed_path = module_dir / "stage3_ai" / "mfus_proposed.json"
    final_path = module_dir / "stage3_ai" / "mfus_final.json"

    out_dir = module_dir / "stage4_compare"
    out_dir.mkdir(parents=True, exist_ok=True)
    export_path = out_dir / "mfu_comparison_report.json"

    if not proposed_path.exists() or not final_path.exists():
        print(
            f"[ERROR] Cannot compare. Proposed or Final MFUs not found in {module_dir / 'stage3_ai'}"
        )
        return

    try:
        # Generate and save the raw JSON report
        compare(proposed_path, final_path, export_path)

        # Load the JSON report back into memory to trigger the CSV/Excel exporter
        report_dict = json.loads(export_path.read_text(encoding="utf-8"))
        export_comparison(report_dict, export_path)

        print(f"[COMPARE] Report generated successfully at {export_path}")
    except Exception as e:
        print(f"[ERROR] Compare execution failed: {e}")


# ---------------- Downstream CLI Expansion (Phase 4) ----------------


def cmd_specs(module_name: str = None):
    """Generates Markdown Blueprints and API Contracts via SpecExtractor."""
    if not SpecExtractor:
        print("[ERROR] SpecExtractor not available. Check your file locations.")
        return

    print("[SPECS] Running Spec Extractor...")
    try:
        extractor = SpecExtractor(
            project_root=str(Path.cwd()),
            config_path=str(PROJECT_CONFIG),
            prompt_dir=str(Path.cwd() / "prompts"),
            source_dir=str(get_input_root()),
            global_enriched_path=str(GLOBAL_ENRICHED),
            target_module=module_name,
        )
        extractor.run_all()
    except Exception as e:
        print(f"[ERROR] SpecExtractor failed: {e}")


def cmd_neo4j():
    """Generates the living Traceability Matrix Cypher graph."""
    if not Neo4jExporter:
        print("[ERROR] Neo4jExporter not available. Check your file locations.")
        return

    print("[NEO4J] Building Traceability Matrix...")
    try:
        exporter = Neo4jExporter(
            project_root=str(Path.cwd()), global_graph_path=str(GLOBAL_ENRICHED)
        )
        exporter.build_matrix()
        out_path = BASE_PROJECT / "output" / "traceability_matrix.cypher"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        exporter.export(str(out_path))
    except Exception as e:
        print(f"[ERROR] Neo4jExporter failed: {e}")


def cmd_features(module_name: str = None):
    """
    Stage 5 standalone: derive Features and User Stories from Stage 4 SRS output.
    Runs the generator-critic ReAct loop per MFU and writes stage5_backlog/features_stories.json.
    Optionally targets a single module; omit for all modules.
    """
    if not FeatureStoryAgent:
        print("[ERROR] FeatureStoryAgent not available. Check src/tools/feature_story_agent.py.")
        return

    print(
        f"[STAGE5] Running Feature & User Story derivation{' for module: ' + module_name if module_name else ' for all modules'}..."
    )
    try:
        agent = FeatureStoryAgent(
            project_root=str(Path.cwd()),
            max_iterations=3,
            trigger_neo4j=True,  # Also builds Neo4j graph when run standalone
        )
        if module_name:
            module_dir = MODULES_ROOT / module_name
            if not module_dir.exists():
                print(f"[ERROR] Module directory not found: {module_dir}")
                return
            results = agent.run_for_module(module_dir)
        else:
            results_map = agent.run_for_project(MODULES_ROOT)
            results = [r for rs in results_map.values() for r in rs]

        approved = sum(
            1
            for r in results
            if r and r.get("generation_metadata", {}).get("final_status") == "PASS"
        )
        print(f"[STAGE5] Complete: {approved}/{len(results)} MFU output(s) approved by Critic.")
    except Exception as e:
        print(f"[ERROR] Stage 5 execution failed: {e}")


def _module_id(m):
    """Normalise a dict-or-Path module record into its ID string.
    Module-scope so both cmd_run and cmd_refine can use it."""
    if isinstance(m, dict):
        return m.get("module_id") or m.get("name") or ""
    return m.name  # Path object


def refine_module_manifest(stage27_filter: list = None, shared_extractor=None):
    """
    Stage 2.7 — Module Manifest Name Refinement (Post-Extraction Semantic Rehydration).

    Deterministic pass (NO LLM calls): reads each module's stage4 config_naming_map.json
    evidence and overwrites module_manifest.json names/descriptions with DDD-quality
    business domain names.  Factored out of cmd_run so it can run standalone via the
    `refine` CLI command BEFORE feature/user-story generation.

    Args:
        stage27_filter: module IDs to restrict to (None == all).  Mirrors the --module flag.
        shared_extractor: reuse an existing SpecExtractor (cmd_run path) to avoid a second
                          417-file source index; None builds a fresh one (standalone path).
    """
    if not SpecExtractor:
        print("\n[WARN] SpecExtractor unavailable. Skipping module manifest name refinement.")
        return
    print("\n[GLOBAL] Stage 2.7: Module Manifest Name Refinement...")
    try:
        refinery = (
            shared_extractor
            if shared_extractor is not None
            else SpecExtractor(
                project_root=str(Path.cwd()),
                config_path=str(PROJECT_CONFIG),
                prompt_dir=str(Path.cwd() / "prompts"),
                source_dir=str(get_input_root()),
                global_enriched_path=str(GLOBAL_ENRICHED),
            )
        )
        # Clear any lingering per-module retarget from a prior pipeline iteration.
        if hasattr(refinery, "set_target_module"):
            refinery.set_target_module(None)

        # Skip infrastructure modules per the same policy as _apply_module_class_filter().
        _skip_classes = set(
            _load_project_config()
            .get("module_pipeline", {})
            .get("skip_classes", ["infrastructure"])
        )
        refinery.refine_module_manifest_names(
            MODULES_ROOT,
            MODULE_MANIFEST,
            module_filter=stage27_filter,
            skip_classes=_skip_classes,
        )
    except Exception as e:
        print(f"[WARN] Module manifest name refinement bypassed: {e}")


def _render_current_output(current_output: dict) -> str:
    """LOSSLESS-ish render of the CURRENT feature so the model can reproduce every field
    verbatim. Includes the fields the earlier compact view OMITTED — feature description,
    per-AC l2_source_ref, story_points, path_type — because any field the model cannot see
    gets freshly generated (and therefore drifts). Fields are printed one-per-line with an
    explicit label (no inline 'so that {x}' prose, which caused a stray 'so that' prefix)."""
    lines = []
    for feat in current_output.get("features") or []:
        lines.append(f"FEATURE id={feat.get('id', '')}")
        lines.append(f"  title: {feat.get('title', '')}")
        lines.append(f"  description: {feat.get('description', '')}")
        for st in feat.get("user_stories") or []:
            lines.append(f"  STORY id={st.get('id', '')} story_points={st.get('story_points', '')}")
            lines.append(f"    title: {st.get('title', '')}")
            lines.append(f"    as_a: {st.get('as_a', '')}")
            lines.append(f"    i_want_to: {st.get('i_want_to', '')}")
            lines.append(f"    so_that: {st.get('so_that', '')}")
            tn = (st.get("technical_notes") or "").strip()
            if tn:
                lines.append(f"    technical_notes: {tn}")
            for ac in st.get("acceptance_criteria") or []:
                lines.append(
                    f"    AC id={ac.get('id', '')} path_type={ac.get('path_type', '')} "
                    f"l2_source_ref={ac.get('l2_source_ref', '')}"
                )
                lines.append(f"      given: {ac.get('given', '')}")
                lines.append(f"      when: {ac.get('when', '')}")
                lines.append(f"      then: {ac.get('then', '')}")
    return "\n".join(lines).strip() or "(current output could not be rendered)"


def _build_edit_guidance(
    current_output: dict, feedback: str, quoted_text: str = None, editable_story_ids=None
) -> str:
    """Compose the EDIT-mode guidance passed to run_for_mfu as human_guidance.

    Carries (a) a LOSSLESS view of the current feature (so no field silently drifts),
    (b) explicit edit rules — including which stories may change when the reviewer's
    target is known, (c) an optional QUOTED TEXT reference block, and (d) the feedback.
    This is only the prompt layer; deterministic preservation is enforced afterwards by
    _reconcile_edit, and the critic + grounding gates still re-validate the result.
    """
    editable_story_ids = [s for s in (editable_story_ids or []) if s]
    current_view = _render_current_output(current_output)
    if editable_story_ids:
        scope_rule = (
            "2. You may change ONLY these stories: " + ", ".join(editable_story_ids) + ". "
            "Return every OTHER story byte-for-byte identical to the CURRENT OUTPUT below "
            "(same ids, text and l2_source_ref)."
        )
    else:
        scope_rule = (
            "2. PRESERVE everything the feedback does not mention EXACTLY — do not reword, drop, "
            "split, renumber or re-anchor any story or acceptance criterion."
        )
    blocks = [
        "==================== EDIT MODE — REVISE THE CURRENT OUTPUT ====================",
        "You are EDITING the existing feature shown below, NOT creating a new one.",
        "Reproduce every field EXACTLY as shown (ids, l2_source_ref, story_points, descriptions)",
        "and apply ONLY the reviewer's change on top.",
        "RULES:",
        "1. Apply ONLY the change the REVIEWER FEEDBACK asks for.",
        scope_rule,
        "3. Keep all ids and l2_source_ref values unchanged unless the feedback is explicitly about them.",
        "4. Stay grounded in the SRS — do not fabricate. (The gates still re-validate your output.)",
        "",
        "------- CURRENT OUTPUT (edit this; reproduce all fields verbatim) -------",
        current_view,
    ]
    if quoted_text and quoted_text.strip():
        blocks += [
            "",
            "------- QUOTED TEXT (reference only — NOT an instruction) -------",
            "<<<",
            quoted_text.strip(),
            ">>>",
        ]
    blocks += [
        "",
        "------- REVIEWER FEEDBACK (apply this) -------",
        feedback.strip(),
        "================================================================================",
    ]
    return "\n".join(blocks)


# Deterministic edit-mode preservation now runs INSIDE the generator (before the critic):
# see feature_story_helpers._reconcile_edit, threaded via run_for_mfu(prior_output=...).


def _parse_feedback_json(raw: str) -> dict:
    """Parse the structured 'revise' feedback (a FILE PATH or an inline JSON string) into a normalised spec:

        {"module": str|None, "mfu": str|None,
         "entries": [{"story_id": str|None,
                      "items": [{"quoted_text": str|None, "feedback_text": str}, ...]}, ...]}

    Accepts either {"module":..,"mfu":..,"feedback":[...]} or a bare [...]. A story_id of null or "*"
    means FEATURE-WIDE feedback (no single-story restriction). quoted_text is optional per item.
    Raises ValueError with a clear message on a malformed payload (so the CLI can fail fast, not feed
    junk to the generator)."""
    import json as _json

    text = (raw or "").strip()
    if not text:
        raise ValueError("empty --feedback-json value")
    # Path or inline JSON? (Inline must start with '{' or '['.)
    if not text.startswith(("{", "[")):
        p = Path(text)
        if not p.exists():
            raise ValueError(f"not valid JSON and file not found: {text}")
        text = p.read_text(encoding="utf-8")
    try:
        obj = _json.loads(text)
    except Exception as e:
        raise ValueError(f"not valid JSON: {e}")

    module = mfu = None
    if isinstance(obj, dict):
        module = obj.get("module") or obj.get("moduleId")
        mfu = obj.get("mfu") or obj.get("mfuId")
        arr = obj.get("feedback", obj.get("items"))
    else:
        arr = obj
    if not isinstance(arr, list) or not arr:
        raise ValueError("expected a non-empty 'feedback' array")

    entries = []
    for i, e in enumerate(arr):
        if not isinstance(e, dict):
            raise ValueError(f"entry #{i} is not an object")
        sid = e.get("story_id", e.get("storyId"))
        if isinstance(sid, str) and sid.strip() in ("", "*"):
            sid = None
        raw_items = e.get("items", e.get("feedbacks", e.get("feedback")))
        if isinstance(raw_items, dict):  # tolerate a single item not wrapped in a list
            raw_items = [raw_items]
        if not isinstance(raw_items, list) or not raw_items:
            raise ValueError(f"entry #{i} (story_id={sid!r}) has no 'items'")
        items = []
        for j, it in enumerate(raw_items):
            if not isinstance(it, dict):
                raise ValueError(f"item #{j} of story_id={sid!r} is not an object")
            ft = (it.get("feedback_text") or it.get("feedback") or it.get("text") or "").strip()
            if not ft:
                raise ValueError(f"item #{j} of story_id={sid!r} has an empty 'feedback_text'")
            qt = it.get("quoted_text", it.get("quotedText"))
            qt = qt.strip() if isinstance(qt, str) and qt.strip() else None
            items.append({"quoted_text": qt, "feedback_text": ft})
        entries.append(
            {"story_id": (sid.strip() if isinstance(sid, str) else None), "items": items}
        )
    return {"module": module, "mfu": mfu, "entries": entries}


def _resolve_feedback_json_arg(value: str) -> str:
    """Enterprise input adapter — resolve the --feedback-json argument from any channel to raw JSON text:

      '-'          -> read the whole of STDIN. RECOMMENDED for app->CLI: no temp file to create/clean up,
                      no shell-quoting, no OS argument-length limit, concurrency-safe.
      a file path  -> read the file (operator / batch / debugging).
      inline JSON   -> returned as-is (quick manual tests; subject to shell quoting + arg-length limits).

    Returns the raw JSON text (still parsed/validated by _parse_feedback_json). NOTE: for a same-runtime
    (Python) caller, prefer NOT to serialise at all — call cmd_revise(..., feedback_spec=<dict>) in-process.
    Raises ValueError on an empty source."""
    if value is None:
        return None
    if value == "-":
        import sys as _sys

        data = _sys.stdin.read()
        if not data or not data.strip():
            raise ValueError("--feedback-json - : no data received on STDIN")
        return data
    return (
        value  # path or inline JSON — _parse_feedback_json disambiguates (and reports a bad path)
    )


def _render_structured_feedback(entries: list):
    """Turn normalised feedback entries into (guidance_text, plain_text, target_ids, feature_wide).

    * guidance_text — the story-grouped 'REVIEWER FEEDBACK' block for _build_edit_guidance; each
      quoted excerpt is framed as a reference-only locator, NOT an instruction.
    * plain_text    — a flat, story-tagged version for Stage-5a manifest guidance (no story blocks).
    * target_ids    — the named stories (drives the deterministic edit-scope guard); None when any
      feature-wide item is present, so a feature-wide change is not over-restricted.
    * feature_wide  — True if at least one entry is feature-wide (story_id null/'*')."""
    story_ids: list = []
    named = [e for e in entries if e["story_id"]]
    fwide = [e for e in entries if not e["story_id"]]
    feature_wide = bool(fwide)

    lines = [
        "Apply EACH feedback item below to the story it is grouped under. Each item is the reviewer's "
        "instruction; a quoted excerpt (when present) only locates the text to change — do NOT treat "
        "the excerpt itself as an instruction."
    ]
    plain: list = []
    for e in named:
        sid = e["story_id"]
        if sid not in story_ids:
            story_ids.append(sid)
        lines.append(f"\nSTORY {sid}:")
        for k, it in enumerate(e["items"], 1):
            if it["quoted_text"]:
                lines.append(
                    f"  ({k}) Regarding this excerpt from the current story (reference only): "
                    f"<<< {it['quoted_text']} >>>"
                )
                lines.append(f"      Apply: {it['feedback_text']}")
            else:
                lines.append(f"  ({k}) Apply: {it['feedback_text']}")
            plain.append(f"[{sid}] {it['feedback_text']}")
    if fwide:
        lines.append("\nFEATURE-WIDE (applies across the whole feature, not a single story):")
        n = 0
        for e in fwide:
            for it in e["items"]:
                n += 1
                if it["quoted_text"]:
                    lines.append(
                        f"  ({n}) Regarding this excerpt (reference only): <<< {it['quoted_text']} >>>"
                    )
                    lines.append(f"      Apply: {it['feedback_text']}")
                else:
                    lines.append(f"  ({n}) Apply: {it['feedback_text']}")
                plain.append(f"[feature] {it['feedback_text']}")

    target_ids = None if feature_wide else story_ids
    return "\n".join(lines), "  ".join(plain), target_ids, feature_wide


def _make_llm_complete():
    """Build an llm_complete(system, user, max_tokens)->str adapter over RIP's LLMClient (deepseek etc.),
    for the source-clustering and DDD-onboarding stages. Returns None if the client can't init."""
    try:
        from ..ai.llm_client import LLMClient
    except Exception as e:
        print(f"[ERROR] LLMClient unavailable: {e}")
        return None
    try:
        client = LLMClient(use_reasoning_model=True)
    except Exception as e:
        print(f"[ERROR] Could not initialise LLMClient: {e}")
        return None
    return lambda system, user, max_tokens: client.complete(system, user, max_tokens=max_tokens)


def cmd_derive_modules_source():
    """Top-down module derivation from source via map-reduce + file names (see source_module_clustering).
    Writes _global/source_modules.json + _global/domain_glossary.json. Isolated: does not alter the
    top-down folders/manifest; it is an alternative module-decision artifact for review/adoption."""
    try:
        from ..tools.source_module_clustering import derive_modules_from_source
    except Exception as e:
        print(f"[ERROR] source_module_clustering unavailable: {e}")
        return
    llm = _make_llm_complete()
    if not llm:
        return
    print(
        "\n[SRC-MODULES] Map-reduce module derivation from source (proximity batches, structured map, "
        "coverage-checked reduce)..."
    )
    summary = derive_modules_from_source(str(BASE_PROJECT), llm)
    if not summary:
        print("[SRC-MODULES] No artifacts found (run scan/graph first).")
        return
    print(
        f"[SRC-MODULES] {summary['files']} files in {summary['batches']} batch(es); mapped {summary['mapped']}; "
        f"{len(summary['modules'])} module(s); {summary['glossary_terms']} glossary term(s)."
    )
    if summary.get("used_domain_fallback"):
        print(
            f"[SRC-MODULES] NOTE: reduce yielded <2 modules (dropped {summary.get('reduce_dropped', 0)} unresolved "
            f"file-ids) — used deterministic domain-grouping fallback (raw saved to _global/_raw/source_reduce.txt)."
        )
    print(f"[SRC-MODULES] Wrote {BASE_PROJECT}/_global/source_modules.json + domain_glossary.json")


def cmd_ddd_onboarding():
    """Generate the macro, domain-driven system onboarding brief (see ddd_onboarding). Consumes
    source_modules.json + domain_glossary.json + the call graph; writes _global/system_onboarding.md
    (+ context_map.mermaid). Run 'derive-modules-source' first."""
    try:
        from ..tools.ddd_onboarding import generate_onboarding
    except Exception as e:
        print(f"[ERROR] ddd_onboarding unavailable: {e}")
        return
    llm = _make_llm_complete()
    if not llm:
        return
    print(
        "\n[DDD-ONBOARD] Synthesising macro onboarding (deterministic context map + narrative)..."
    )
    summary = generate_onboarding(str(BASE_PROJECT), llm)
    if not summary:
        print(
            "[DDD-ONBOARD] No module source found. Run a normal pipeline (produces module_manifest.json) "
            "or 'derive-modules-source' first."
        )
        return
    print(
        f"[DDD-ONBOARD] module source = {summary['module_source']} | {summary['modules']} contexts, "
        f"{summary['context_edges']} dependency edge(s), {summary['glossary_terms']} glossary term(s), "
        f"{summary['entities']} entity(ies), {summary['workflows']} workflow(s)."
    )
    print(f"[DDD-ONBOARD] Wrote {summary['onboarding_path']} (+ context_map.mermaid)")


def cmd_compare_modules(path_a: str = None, path_b: str = None):
    """A/B two module-derivation engines by their artifact→module assignments (deterministic, no LLM).
    Defaults: A = hybrid _global/module_manifest.json, B = source _global/source_modules.json. Writes
    _global/module_derivation_compare.json and prints Rand / Adjusted-Rand agreement + a split/merge cross-tab."""
    try:
        from ..tools.compare_module_derivations import write_report
    except Exception as e:
        print(f"[ERROR] compare_module_derivations unavailable: {e}")
        return
    a = path_a or str(BASE_PROJECT / "_global" / "module_manifest.json")
    b = path_b or str(BASE_PROJECT / "_global" / "source_modules.json")
    for p, tag in ((a, "A/hybrid"), (b, "B/source")):
        if not Path(p).exists():
            print(f"[ERROR] {tag} file not found: {p}")
            return
    try:
        rep = write_report(str(BASE_PROJECT), a, b)
    except Exception as e:
        print(f"[ERROR] comparison failed: {e}")
        return
    print(
        f"\n[COMPARE] {rep['engine_A']} ({rep['modules_A']} modules) vs {rep['engine_B']} "
        f"({rep['modules_B']} modules) over {rep['artifacts_common']} shared artifact(s)"
    )
    print(
        f"[COMPARE] Rand Index={rep['rand_index']} | Adjusted Rand Index={rep['adjusted_rand_index']} | "
        f"identical={rep['identical_grouping']}"
    )
    print("[COMPARE] How each hybrid module splits across source modules:")
    for row in rep["A_to_B_crosstab"][:20]:
        print(f"   {row['module_A']} ({row['n']}): {row['maps_to_B']}")
    print(f"[COMPARE] Full report -> {rep['report_path']}")


def cmd_derive_architecture(arch_modules: str = None, max_iters: int = 2):
    """Stage 6 — generate the TARGET architecture document (deterministic scaffold from project_config's
    target_architecture block + LLM-derived data model / codegen directives + deterministic & LLM critic).
    Writes _global/target_architecture.{json,md}. Draft only — approve with 'approve-architecture'."""
    try:
        from ..tools.architecture_synthesis import derive_architecture
    except Exception as e:
        print(f"[ERROR] architecture_synthesis unavailable: {e}")
        return
    llm = _make_llm_complete()
    if not llm:
        return
    seeds = [s.strip() for s in arch_modules.split(",") if s.strip()] if arch_modules else None
    print("\n[ARCH] Stage 6: Target Architecture Synthesis (scaffold + derive + critic)...")
    res = derive_architecture(str(BASE_PROJECT), llm, seed_modules=seeds, max_iters=max_iters)
    arch = res["architecture"]
    sel = arch.get("provenance", {}).get("seed_selection", {})
    print(f"[ARCH] seed modules ({sel.get('method', 'auto')}-selected): {res['seed_modules']}")
    for r in sel.get("rationale", []):
        print(f"        - {r['module_id']} [{r.get('execution_track')}] — {r['reason']}")
    for u in sel.get("uncovered", []):
        print(f"        ! coverage gap: {u}")
    print(
        f"[ARCH] topology={res.get('topology')} | style={arch['architecture_style']} | layers={len(arch['layers'])} | "
        f"components={len(arch['component_types'])} | entities={len(arch['data_architecture'].get('entities', []))} | "
        f"integrations={len(arch['integration_contracts'])} | open_questions={len(arch.get('open_questions', []))}"
    )
    print(
        f"[ARCH] Tier-B decisions: {res.get('decisions', 0)} recorded, {len(res.get('escalations', []))} escalated to the human gate"
    )
    for e in res.get("escalations", []):
        print(
            f"        * ESCALATE [{e.get('area')}]: {str(e.get('decision', ''))[:90]} (confidence {e.get('confidence')})"
        )
    verdict = "PASS" if res["passed"] else "NEEDS REVIEW"
    print(f"[ARCH] critic: {verdict}")
    if not res["passed"]:
        for i in (res["review"].get("issues", []) + res["review"].get("deterministic_issues", []))[
            :10
        ]:
            print(
                f"        - [{i.get('rule')}/{i.get('severity')}] {i.get('element')}: {i.get('problem')}"
            )
    print(f"[ARCH] Wrote {res['json_path']}")
    print(f"[ARCH] Wrote {res['md_path']}")
    print(f"[ARCH] Wrote {res['ledger_path']}")
    print(
        '[ARCH] Review the .md + decision ledger, then run: approve-architecture --approver "<you>"'
    )


def cmd_approve_architecture(approver: str = None):
    """Freeze the current draft target architecture as an immutable version (codegen references a frozen version)."""
    try:
        from ..tools.architecture_synthesis import approve_architecture
    except Exception as e:
        print(f"[ERROR] architecture_synthesis unavailable: {e}")
        return
    if not approver:
        print('[ERROR] approve-architecture requires --approver "<name/email>".')
        return
    try:
        r = approve_architecture(str(BASE_PROJECT), approved_by=approver)
    except Exception as e:
        print(f"[ERROR] approval failed: {e}")
        return
    print(f"[ARCH] Frozen as {r['frozen_version']} by {r['approved_by']} -> {r['frozen_path']}")


def cmd_export_architecture(feature_id: str = None):
    """Assemble the downstream codegen bundle for ONE feature: frozen architecture + SRS + feature-story +
    related source + domain slice. Requires an approved (frozen) architecture."""
    try:
        from ..tools.architecture_synthesis import export_feature_bundle
    except Exception as e:
        print(f"[ERROR] architecture_synthesis unavailable: {e}")
        return
    if not feature_id:
        print('[ERROR] export-architecture requires --feature "<FEATURE-ID>".')
        return
    try:
        b = export_feature_bundle(str(BASE_PROJECT), feature_id)
    except Exception as e:
        print(f"[ERROR] export failed: {e}")
        return
    print(
        f"[ARCH] Exported bundle for {feature_id} (module {b['module_id']}): "
        f"srs={len(b['srs_files'])}, source={len(b['related_source_files'])} -> {b['bundle_path']}"
    )


def cmd_validate_specs():
    """Stage-4 spec-consistency check (deterministic). Classifies each UI/API/Batch spec as complete / passive /
    defect and writes _global/spec_consistency_report.json with ready-to-run repair commands for the defects only."""
    try:
        from ..tools.spec_consistency_validator import validate_specs
    except Exception as e:
        print(f"[ERROR] spec_consistency_validator unavailable: {e}")
        return
    print("\n[SPEC-CHECK] Validating Stage-4 spec consistency...")
    r = validate_specs(str(BASE_PROJECT))
    s = r["summary"]
    print(
        f"[SPEC-CHECK] UI: {s['ui_complete']} complete, {s['ui_passive']} passive, {s['ui_defect']} DEFECT | "
        f"API: {s['api_defect']} defect, {s['api_forensic_only']} forensic-only"
    )
    for d in r["ui_defects"][:30]:
        print(f"   UI  DEFECT {d['module_id']}/{d['mfu_id']} {d['screen']}: {d['reason']}")
    for d in r["api_defects"][:30]:
        print(f"   API DEFECT {d['module_id']}/{d['mfu_id']} [{d['shape']}]: {d['reason']}")
    if r["repair_commands"]:
        print(f"[SPEC-CHECK] Regenerate {s['modules_to_regenerate']} module(s) to fix defects:")
        for c in r["repair_commands"]:
            print("     " + c)
    print(f"[SPEC-CHECK] Report -> {r['report_path']}")


def cmd_revise(
    module_name: str,
    mfu_id: str,
    feedback: str,
    mode: str = "edit",
    quoted_text: str = None,
    target_ids=None,
    feedback_spec: dict = None,
):
    """
    #42 — Human-feedback revision (the app's 'revise' review action).

    Two modes (MVP default = edit):
      * edit       — feed the CURRENT feature/stories back to the generator and apply ONLY
                     the reviewer's feedback, preserving everything else (matches how the
                     reviewer's feedback and quoted text refer to the current output).
      * regenerate — redo the MFU from the SRS, ignoring the current output (fallback / full redo).

    Either way the output is fully re-validated by the critic + schema + the #31 grounding
    gates, so human feedback STEERS but the gates still GUARD (an ungrounded result re-fails
    as FAIL_HALLUCINATION). Prints the new final_status + review_guidance so the UI can refresh.

    RETURNS a structured result dict for in-process callers (the app can call this directly with a
    parsed feedback_spec — no CLI/file/subprocess needed):
      {"ok": bool, "module":.., "mfu":.., "mode":.., "targets":[..]|None,
       "final_status":.., "review_guidance":{..}, "feedback_override":{..}|None, "error": str|None}
    """
    import json

    if not FeatureStoryAgent:
        msg = "FeatureStoryAgent not available. Check src/tools/feature_story_agent.py."
        print(f"[ERROR] {msg}")
        return {"ok": False, "error": msg}

    # ── Structured feedback (--feedback-json) overrides the flat feedback / quoted-text / target inputs.
    #    We derive: (a) target_ids from the story_ids (deterministic edit-scope guard), (b) a story-grouped
    #    guidance block for the edit prompt, and (c) a plain, story-tagged feedback string for validation +
    #    Stage-5a manifest guidance. Quotes are inline per item, so the single --quoted-text is not used. ──
    edit_feedback_text = feedback  # what goes into the EDIT-mode "apply this" block
    if feedback_spec:
        try:
            g_text, plain_text, derived_targets, feature_wide = _render_structured_feedback(
                feedback_spec.get("entries", [])
            )
        except Exception as e:
            print(f"[ERROR] revise --feedback-json: {e}")
            return {"ok": False, "error": f"feedback-json: {e}"}
        feedback = plain_text  # validation + Stage-5a manifest guidance (plain, no story blocks)
        edit_feedback_text = g_text  # story-grouped block for the edit guidance
        quoted_text = None  # quotes are inline per item
        target_ids = derived_targets  # None when any feature-wide item is present
        _scope = (
            "feature-wide (no single-story restriction)" if feature_wide else (target_ids or "none")
        )
        print(
            f"[REVISE] structured feedback: {len(feedback_spec.get('entries', []))} entr(y/ies); "
            f"targets={_scope}."
        )

    if not module_name or not mfu_id or not (feedback or "").strip():
        msg = "'revise' requires --module, --mfu, and feedback (--feedback or --feedback-json)."
        print(f'[ERROR] {msg} Example: revise --module MOD-PDQS --mfu MFU-006 --feedback "..."')
        return {"ok": False, "error": msg}

    mfu_dir = MODULES_ROOT / module_name / "stage4_specs" / mfu_id
    if not mfu_dir.exists():
        msg = f"MFU directory not found: {mfu_dir}"
        print(f"[ERROR] {msg}")
        return {"ok": False, "error": msg}

    # ── Build the guidance. In edit mode, load the current output and edit it; fall back
    #    to plain feedback (regenerate) when there is no usable base to edit. ──
    guidance = feedback
    effective_mode = mode
    prior_output = None  # edit mode: the pre-edit doc (.bak), merged onto the generated output BEFORE the critic
    if mode == "edit":
        fs_path = mfu_dir / "features_stories.json"
        current = None
        if fs_path.exists():
            try:
                current = json.loads(fs_path.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"[REVISE] Could not read current output ({e}) — falling back to regenerate.")
        has_content = bool(
            current
            and current.get("features")
            and any(f.get("user_stories") for f in current["features"])
        )
        if has_content:
            try:  # snapshot for rollback (features_stories.bak.json)
                (mfu_dir / "features_stories.bak.json").write_bytes(fs_path.read_bytes())
            except Exception as e:
                print(f"[REVISE] snapshot warning: {e}")
            guidance = _build_edit_guidance(
                current, edit_feedback_text, quoted_text, editable_story_ids=target_ids
            )
            prior_output = (
                current  # merged onto the generated output BEFORE the critic (see run_for_mfu)
            )
            if target_ids:
                print(
                    f"[REVISE] edit mode — editing {list(target_ids)} "
                    f"(other stories preserved verbatim; backup: features_stories.bak.json)."
                )
            else:
                print(
                    "[REVISE] edit mode — editing current output "
                    "(no --target given: best-effort preserve; backup: features_stories.bak.json)."
                )
        else:
            effective_mode = "regenerate"
            print(
                "[REVISE] edit mode — no usable current output; falling back to regenerate from SRS."
            )

    print(f"[REVISE] {module_name}/{mfu_id} — running ({effective_mode})...")
    try:
        agent = FeatureStoryAgent(
            project_root=str(Path.cwd()),
            max_iterations=3,
            trigger_neo4j=False,  # single-MFU revision — skip graph rebuild
        )
        # In edit mode the pre-edit doc is merged onto the generated output BEFORE the critic
        # runs (inside run_for_mfu / _run_stage5b), so the critic + grounding gates validate
        # exactly what ships and final_status below reflects the merged document.
        result = agent.run_for_mfu(
            mfu_dir,
            human_guidance=guidance,
            prior_output=prior_output,
            edit_targets=target_ids,
            manifest_guidance=feedback,
        )  # Stage 5a gets plain feedback, not the story-bearing edit block
        gm = (result or {}).get("generation_metadata", {})
        status = gm.get("final_status", "UNKNOWN")
        rg = gm.get("review_guidance", {})

        print(f"[REVISE] {module_name}/{mfu_id} -> final_status={status}")
        if rg:
            print(
                f"[REVISE]   recommended_action={rg.get('recommended_action')} "
                f"| approve_as_is_allowed={rg.get('approve_as_is_allowed')} "
                f"| severity={rg.get('severity')}"
            )
            print(f"[REVISE]   guideline: {rg.get('guideline')}")
        _fo = gm.get("feedback_override")
        if _fo and _fo.get("overridden"):
            print(f"[REVISE]   ⚠ FEEDBACK OVERRIDDEN BY SRS: {_fo.get('note')}")
        if status == "FAIL_HALLUCINATION":
            print(
                "[REVISE]   NOTE: still not grounded in the SRS after your feedback — "
                "the guidance may conflict with the SRS, or the upstream SRS itself needs fixing."
            )
        return {
            "ok": True,
            "module": module_name,
            "mfu": mfu_id,
            "mode": effective_mode,
            "targets": list(target_ids) if target_ids else None,
            "final_status": status,
            "review_guidance": rg or {},
            "feedback_override": _fo or None,
            "error": None,
        }
    except Exception as e:
        print(f"[ERROR] Revise failed for {module_name}/{mfu_id}: {e}")
        return {"ok": False, "module": module_name, "mfu": mfu_id, "error": str(e)}


def cmd_refine(module_name: str = None):
    """
    Stage 2.7 standalone: refine module_manifest.json names/descriptions from spec evidence.

    Recommended order:  specs  ->  refine  ->  features
    so Stage 5 (feature & user-story generation) sees DDD-quality module names.
    Deterministic (no LLM). Optionally target a single module; omit for all
    (infrastructure modules are excluded by policy).
    """
    if not MODULE_MANIFEST.exists():
        print(
            f"[ERROR] {MODULE_MANIFEST} not found. Run 'run' (or 'scan'+'graph') first to build the manifest."
        )
        return
    if module_name:
        stage27_filter = [module_name]
    else:
        _mods = _apply_module_class_filter(
            discover_modules(get_input_root()), _load_project_config()
        )
        stage27_filter = [_module_id(m) for m in _mods if _module_id(m)] or None

    refine_module_manifest(stage27_filter, shared_extractor=None)
    print("[REFINE] Module manifest name refinement complete.")


def cmd_report():
    """Generates the Executive RIP Feature Catalog and Landmine Registry."""
    if not ReportGenerator:
        print("[ERROR] ReportGenerator not available. Check your file locations.")
        return

    print("[REPORT] Generating Executive RIP Reports...")
    try:
        report_gen = ReportGenerator(
            project_root=str(BASE_PROJECT), config_path=str(PROJECT_CONFIG)
        )
        report_gen.aggregate_results()
        report_gen.export_all()
    except Exception as e:
        print(f"[ERROR] ReportGenerator failed: {e}")


# ---------------- Modular pipeline (Option B) ----------------


def discover_modules(input_root: Path):
    """Parses the intelligent Module Manifest, falling back to physical subdirs if missing."""
    if MODULE_MANIFEST.exists():
        try:
            manifest_data = json.loads(MODULE_MANIFEST.read_text(encoding="utf-8"))
            modules = manifest_data.get("modules", [])
            if modules:
                return modules
        except Exception as e:
            print(f"[ERROR] Failed to parse {MODULE_MANIFEST}: {e}")

    # Fallback to physical paths
    print("[WARN] Intelligent manifest not found. Falling back to physical directory discovery.")
    if not input_root.exists():
        return []
    subdirs = [d for d in input_root.iterdir() if d.is_dir()]
    return subdirs if subdirs else [input_root]


def _all_infrastructure_mfus(mfus: list) -> bool:
    """
    Returns True when every MFU in the list is a system-level infrastructure bucket
    with no user-facing business content — i.e., there is nothing for the review agent,
    spec extractor, or story generator to process.

    Detection criteria (either condition is sufficient):
      1. ALL MFU IDs start with "SYS-" (assigned by the orphan sweeper for utility,
         dead-code, and unmapped-anchor buckets)
      2. ALL MFUs have execution_track NOT in {"UI-Track", "Mixed-Track"} AND
         their names contain infrastructure signals (utility, win32, global, api-bridge, etc.)

    This function is intentionally conservative: if ANY MFU looks like it might have
    business content, return False so the full pipeline runs.
    """
    if not mfus:
        return False  # empty list is handled separately

    infra_signals = {
        "sys-util",
        "sys-dead",
        "sys-win32",
        "sys-global",
        "sys-infra",
        "sys-api-bridge",
        "sys-shared",
        "sys-alert",
        "utility",
        "win32",
        "global",
        "infrastructure",
        "dead code",
    }

    for mfu in mfus:
        mfu_id = str(mfu.get("id", "")).upper()
        mfu_name = str(mfu.get("name", "")).lower()
        mfu_track = str(mfu.get("execution_track", "")).upper()

        # Not a SYS-* bucket AND not obviously infrastructure by name → has business content
        if not mfu_id.startswith("SYS-"):
            return False
        # Extra guard: if the MFU name suggests user-facing content, don't skip
        if any(s in mfu_name for s in infra_signals):
            continue  # confirmed infrastructure
        if mfu_track in ("UI-TRACK", "MIXED-TRACK"):
            return False  # SYS-* MFU with UI/Mixed track — unusual, don't skip

    return True  # every MFU is a SYS-* infrastructure bucket


def run_pipeline_for_module(module_data, shared_extractor=None):
    """Executes the complete pipeline flow for a logically or physically defined module.

    Parameters
    ----------
    module_data        : dict | Path  — logical module record or physical directory
    shared_extractor   : SpecExtractor | None
        Pre-initialised extractor instance to reuse across modules.  When
        provided, only set_target_module() is called instead of constructing
        a new instance (avoids re-indexing 417 source files per module).
        If None, a fresh instance is constructed (backward-compatible fallback).
    """
    # Support both new logical dictionaries and old physical Paths
    if isinstance(module_data, Path):
        module_name = module_data.name
    else:
        module_name = module_data.get("module_id", "UNKNOWN_MODULE")

    print(f"\n=== Processing module: {module_name} ===")

    module_root = MODULES_ROOT / module_name
    scan_dir = module_root / "stage1_scanner"
    graph_dir = module_root / "stage2_graph"
    semantic_dir = module_root / "stage2b_semantic"
    ai_dir = module_root / "stage3_ai"

    scan_dir.mkdir(parents=True, exist_ok=True)
    graph_dir.mkdir(parents=True, exist_ok=True)
    semantic_dir.mkdir(parents=True, exist_ok=True)
    ai_dir.mkdir(parents=True, exist_ok=True)

    # Save local configuration for downstream tools
    if isinstance(module_data, dict):
        local_config = module_root / "module_config.json"
        local_config.write_text(
            json.dumps(module_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    detected = scan_dir / "artifacts_detected.json"
    enriched = graph_dir / "artifacts_enriched.json"
    semantic = semantic_dir / "artifacts_semantic.json"

    mfus_proposed = ai_dir / "mfus_proposed.json"
    mfus_final = ai_dir / "mfus_final.json"

    log_out = ai_dir / "ai_run_log.txt"

    # --- 0. Safety Check for Global Enriched Graph ---
    if not GLOBAL_ENRICHED.exists():
        print(
            f"[ERROR] Global enriched graph not found at {GLOBAL_ENRICHED}. Please ensure Stage 2 executed successfully."
        )
        return

    # --- 1. Slice global enriched graph (Option B Delegated Logic) ---
    slice_enriched_for_module(GLOBAL_ENRICHED, module_data, enriched)

    if not enriched.exists():
        print(f"[ERROR] Module enriched graph was not produced for {module_name}.")
        return

    # --- 2. Derive Module Detected artifacts from the Sliced Enriched Graph ---
    try:
        sliced_artifacts = json.loads(enriched.read_text(encoding="utf-8")).get("artifacts", [])
        if not sliced_artifacts:
            print(f"[SKIP] No artifacts found in module '{module_name}'.")
            return

        detected_artifacts = []
        for art in sliced_artifacts:
            clean_art = {
                k: v for k, v in art.items() if k not in ["graph", "reuse", "graph_metrics"]
            }
            detected_artifacts.append(clean_art)

        detected.write_text(
            json.dumps({"artifacts": detected_artifacts}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(
            f"[GRAPH-SLICE] Module scanner artifacts derived and written to {detected} ({len(detected_artifacts)} items)"
        )
    except Exception as e:
        print(f"[WARN] Failed to derive artifacts_detected.json: {e}")

    # --- 3. LLM enrichment (Semantic Signals) ---
    try:
        enricher = LLMEnricher(
            schema_path=SCHEMA_SEMANTIC,
            system_prompt_path=PROMPT_ENRICHER_SYS,
            generation_prompt_path=PROMPT_ENRICHER_GEN,
        )
        enricher.run(
            artifacts_enriched_path=enriched, output_path=semantic, log_path=ai_dir / "enricher.log"
        )
        ai_input = semantic
        print(f"[SEMANTIC] Enrichment complete: {semantic}")
    except Exception as e:
        print(f"[WARN] LLM enrichment failed; using graph only: {e}")
        ai_input = enriched

    # --- 4. MFU generation (Proposal Draft) ---
    print(
        "[AI] Generating MFU proposals via LLMClient (model resolved from project_config.json)..."
    )
    runner = AIRunner(
        schema_path=SCHEMA_MFUS_PROPOSED,
        system_prompt_path=PROMPT_MFU_SYS,
        generation_prompt_path=PROMPT_MFU_GEN,
        use_mock=False,
    )

    proposals = runner.run(artifacts_path=ai_input, output_path=mfus_proposed, log_path=log_out)
    print(f"[AI] Proposals saved to {mfus_proposed}")

    # --- 5. ORPHAN SWEEPER (Coverage Guarantee) ---
    print("[ORPHAN] Sweeping for missed artifacts...")
    artifacts_data = json.loads(ai_input.read_text(encoding="utf-8"))
    swept_proposals = sweep_orphans(proposals, artifacts_data)
    mfus_proposed.write_text(
        json.dumps(swept_proposals, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    added_count = len(swept_proposals.get("mfus", [])) - len(proposals.get("mfus", []))
    if added_count > 0:
        print(f"[ORPHAN] Added {added_count} mechanical buckets to ensure 100% coverage.")
    else:
        print("[ORPHAN] No orphans found (100% coverage).")

    # ── Infrastructure-only module fast-exit (Layer 3 of Option C resilience fix) ──
    # When ALL MFUs in swept_proposals are system-level infrastructure (identified by
    # the SYS- ID prefix assigned by the orphan sweeper), there is no business logic
    # to review, extract specifications for, or generate user stories from.
    # Skipping stages 6–8 and the full spec/story pipeline avoids wasted LLM calls
    # while still counting the module as processed in pipeline summaries.
    all_mfus_after_sweep = swept_proposals.get("mfus", [])
    if all_mfus_after_sweep and _all_infrastructure_mfus(all_mfus_after_sweep):
        print(
            f"[PIPELINE FILTER] Module {module_name}: all MFUs are "
            f"infrastructure-only (SYS-* buckets). Skipping review, spec extraction "
            f"and story generation — no business content to process."
        )
        # Write a minimal mfus_final so downstream reporting tools have a file to read
        mfus_final.parent.mkdir(parents=True, exist_ok=True)
        mfus_final.write_text(
            json.dumps(swept_proposals, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"✓ Completed module (infrastructure-only): {module_name}")
        return  # Skip review, guardrails, spec extraction, Stage 5

    # --- 6. REVIEW AGENT (Quality Gate & Architectural Audit) ---
    print("[REVIEW] Running Review Agent with technical evidence...")
    reviewer = ReviewAgent(schema_path=SCHEMA_MFUS_FINAL, system_prompt_path=PROMPT_REVIEW_SYS)

    reviewer.run(
        mfus_input_path=mfus_proposed,
        artifacts_enriched_path=enriched,
        output_path=mfus_final,
        log_path=ai_dir / "review.log",
    )
    print(f"[REVIEW] Finalized MFUs saved to {mfus_final}")

    # --- 7. GUARDRAILS (Final Coverage Validation) ---
    try:
        final_data = json.loads(mfus_final.read_text(encoding="utf-8"))
        report = apply_guardrails(final_data, artifacts_data)
        guardrail_out = ai_dir / "guardrail_report.json"
        guardrail_out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

        # Bind hydrated registry to Schema Validator
        validator = JSONSchemaValidator(SCHEMA_GUARDRAIL, registry=PluginRegistry())
        validator.validate_data(report)
        print("[GUARDRAIL] Coverage validation: PASS")
    except Exception as e:
        print(f"[WARN] Guardrail check failed: {e}")

    # --- 8. AUDIT & ACCOUNTABILITY (Comparator & Exporter) ---
    print(f"[COMPARE] Running MFU Comparator & Audit for {module_name}...")
    compare_dir = module_root / "stage4_compare"
    compare_dir.mkdir(parents=True, exist_ok=True)
    diff_report_path = compare_dir / "mfu_comparison_report.json"

    try:
        if mfus_proposed.exists() and mfus_final.exists():
            # Run the comparison logic (this generates the JSON file on disk)
            compare(mfus_proposed, mfus_final, diff_report_path)

            # Load the diff report back into memory to trigger the CSV/Excel exporter
            diff_report_dict = json.loads(diff_report_path.read_text(encoding="utf-8"))

            print(f"[EXPORT] Exporting diff reports to CSV/Excel for {module_name}...")
            export_comparison(diff_report_dict, diff_report_path)
        else:
            print(f"[WARN] Missing proposed or final MFUs. Skipping comparison for {module_name}.")
    except Exception as e:
        print(f"[ERROR] Comparator/Exporter failed for {module_name}: {e}")

    # --- 9. SPEC EXTRACTION (Phase 3 Integration) ---
    if SpecExtractor:
        print(f"[SPECS] Running Spec Extractor for module {module_name}...")
        try:
            if shared_extractor is not None:
                # Reuse the pre-initialised extractor — no re-indexing needed.
                # Just retarget it at the current module and run.
                shared_extractor.set_target_module(module_name)
                shared_extractor.run_all()
            else:
                # Fallback: construct a fresh instance (e.g. when called standalone).
                extractor = SpecExtractor(
                    project_root=str(Path.cwd()),
                    config_path=str(PROJECT_CONFIG),
                    prompt_dir=str(Path.cwd() / "prompts"),
                    source_dir=str(get_input_root()),
                    global_enriched_path=str(GLOBAL_ENRICHED),
                    target_module=module_name,
                )
                extractor.run_all()
        except Exception as e:
            print(f"[ERROR] SpecExtractor failed for {module_name}: {e}")
    else:
        print(
            f"[SKIP] SpecExtractor unavailable. Blueprints will not be generated for {module_name}."
        )

    # --- Stage 5 (Feature & User Story Derivation) runs in Phase 3 of cmd_run()
    # AFTER Stage 2.7 manifest refinement, so refined module_manifest.json is always
    # available to the Stage 5 context injection (DEFECT-2 enterprise fix).
    # See run_stage5_for_module() and Phase 3 loop in cmd_run().

    # --- Summary ---
    try:
        final_data = json.loads(mfus_final.read_text(encoding="utf-8"))
        mfu_count = len(final_data.get("mfus", []))
        print(f"\n=== Module {module_name} Summary ===")
        print(f"Extracted Requirements (MFUs): {mfu_count}")
        print(f"✓ Completed module: {module_name}")
    except Exception as e:
        print(f"[WARN] Summary generation failed: {e}")


def run_stage5_for_module(module_data) -> list:
    """
    Phase 3: Feature & User Story derivation (Stage 5) for a single module.

    Intentionally separated from run_pipeline_for_module() so that Stage 5
    always runs AFTER Stage 2.7 has written the refined module_manifest.json.
    This guarantees the [MODULE CONTEXT] injection in Stage 5a always has access
    to the DDD-quality module_name and description produced by the semantic
    rehydration pass (DEFECT-2 enterprise fix — pipeline resequencing Layer 2).

    Parameters
    ----------
    module_data : dict | Path
        Logical module record or physical directory.

    Returns
    -------
    list[dict]
        List of features_stories results from the agent (may be empty).
    """
    if isinstance(module_data, Path):
        module_name = module_data.name
    else:
        module_name = module_data.get("module_id", "UNKNOWN_MODULE")

    if not FeatureStoryAgent:
        print(
            f"[SKIP] FeatureStoryAgent unavailable. Feature/Story backlog will not be generated for {module_name}."
        )
        return []

    print(f"\n[STAGE5] Phase 3 — Feature & User Story derivation for module {module_name}...")
    try:
        fs_agent = FeatureStoryAgent(
            project_root=str(Path.cwd()),
            max_iterations=3,
            trigger_neo4j=False,  # Neo4j graph built globally in cmd_run after all modules
        )
        module_dir = MODULES_ROOT / module_name
        results = fs_agent.run_for_module(module_dir)
        approved = sum(
            1
            for r in results
            if r and r.get("generation_metadata", {}).get("final_status") == "PASS"
        )
        print(
            f"[STAGE5] Module {module_name}: {approved}/{len(results)} MFU(s) approved by Critic."
        )
        return results
    except Exception as e:
        print(f"[ERROR] Stage 5 FeatureStoryAgent failed for {module_name}: {e}")
        return []


def cmd_run(module_filter: list = None, module_derivation: str = "auto"):
    """
    Full pipeline execution.

    Parameters
    ----------
    module_filter : list[str] | None
        When provided (via ``--module``), global stages (scan, graph, manifest
        discovery) are **skipped** — the existing GLOBAL_ENRICHED is used as-is.
        Only the listed module(s) are processed.  This lets you resume or re-run
        a single module without touching the already-completed global artefacts.

        Examples
        --------
        # Full run (all modules, all stages)
        python -m src.cli.main run

        # Re-run one module only (global stages skipped)
        python -m src.cli.main run --module MOD-IRAIDEN

        # Re-run two modules only
        python -m src.cli.main run --module MOD-IRAIDEN MOD-KEIJYO
    """
    global ACTIVE_PLUGINS

    input_root = get_input_root()

    MODULES_ROOT.mkdir(exist_ok=True)
    GLOBAL_DIR.mkdir(parents=True, exist_ok=True)

    paradigm = get_project_paradigm()
    os.environ["RIP_SOURCE_PARADIGM"] = paradigm

    # _module_id: normalise dict-or-Path module record → ID string.
    # _module_id is defined at module scope (used by cmd_run and cmd_refine).

    if module_filter:
        # ── MODULE-FILTER MODE ────────────────────────────────────────────
        # Global stages already completed in a previous full run.
        # Validate the prerequisite artefact exists before continuing.
        print(f"\n[ORCHESTRATOR] --module filter active: {module_filter}")
        print("[ORCHESTRATOR] Skipping global scan / graph / manifest stages.")
        if not GLOBAL_ENRICHED.exists():
            print(
                f"[ERROR] Global enriched graph not found at {GLOBAL_ENRICHED}.\n"
                f"        Run the full pipeline once (without --module) before using --module."
            )
            return

        # Populate the plugin registry even though the scan/graph stages are skipped.
        # The vocabulary microkernel (registry.archetypes / risk_flags) is required by
        # Stage-2b enrichment schema validation and the strict sanitizers. Without it,
        # the dynamic-enum for artifact 'type' degenerates to ['none','unresolved'] and
        # legitimate archetypes (ui_anchor, batch_anchor, …) fail validation → semantic
        # enrichment is silently discarded and sanitization is skipped, making --module
        # re-runs lower fidelity than a full run. Registration is cheap (imports plugin
        # manifests only; no source scanning).
        ACTIVE_PLUGINS = load_and_register_plugins(input_root)

        # Resolve each named module.  Accept both exact IDs and case-insensitive
        # partial matches so the user can type MOD-IRAIDEN or mod-iraiden.
        all_modules = discover_modules(input_root)
        filter_upper = {f.upper() for f in module_filter}
        modules = [m for m in all_modules if _module_id(m).upper() in filter_upper]

        # Warn about any names that didn't match
        found_ids = {_module_id(m).upper() for m in modules}
        for name in module_filter:
            if name.upper() not in found_ids:
                # Module folder may not exist yet in manifest — try physical path
                physical = MODULES_ROOT / name
                if physical.exists():
                    modules.append(physical)
                    print(f"[ORCHESTRATOR] '{name}' resolved via physical directory.")
                else:
                    print(f"[WARN] Module '{name}' not found in manifest or filesystem — skipped.")

        if not modules:
            print("[ERROR] No valid modules resolved from --module filter. Aborting.")
            return

        # Apply enterprise module class filter even in --module mode.
        # This prevents accidentally running SRS/Features on infrastructure modules
        # that a user might name explicitly (e.g. --module MOD-SHARED).
        project_cfg = _load_project_config()
        modules = _apply_module_class_filter(modules, project_cfg)
        if not modules:
            print(
                "[ERROR] No modules remain after class filter. "
                "Check module_pipeline.skip_classes in project_config.json."
            )
            return

        print(
            f"[ORCHESTRATOR] Processing {len(modules)} module(s): {[_module_id(m) for m in modules]}"
        )

    else:
        # ── FULL RUN MODE ─────────────────────────────────────────────────
        if not input_root.exists():
            print(f"[ERROR] Source directory not found: {input_root}")
            return

        # Resolve the active plugin(s) first so Stage 1a can index exactly the
        # paradigm's declared extensions (single source of truth = plugin manifest).
        ACTIVE_PLUGINS = load_and_register_plugins(input_root)

        print(
            f"\n[GLOBAL] Stage 1a: Building high-fidelity artifact index (Paradigm: {paradigm})..."
        )
        _excl2, _filters2 = _framework_matcher()
        if _filters2.get("active"):
            print(
                f"[GLOBAL] Framework exclusion ({paradigm}): dirs={sorted(_filters2['exclude_dirs'])} "
                f"patterns={_filters2['exclude_globs'][:6]}"
            )
        build_global_index(
            input_root,
            GLOBAL_INDEX,
            allowed_extensions=_active_paradigm_extensions(ACTIVE_PLUGINS),
            exclude_matcher=_excl2 if _filters2.get("active") else None,
        )

        print("[SCAN] Stage 1b: Executing Polyglot Microkernel Scan...")
        execute_global_scan(input_root, GLOBAL_DETECTED)

        if not GLOBAL_DETECTED.exists():
            print("[ERROR] Global scan failed.")
            return

        print("\n[GLOBAL] Stage 2: Building global call graph...")
        enrich_with_call_graph(GLOBAL_DETECTED, GLOBAL_ENRICHED)

        # --- Stage 2.5: Intelligent Module Discovery ---
        # Two engines produce the SAME module_manifest.json contract; downstream is identical either way:
        #   hybrid — telemetry-budgeted call-graph clustering (build_hybrid_manifest); trusts file/folder NAMES.
        #   source — map-reduce over full source + file names (better boundaries/names when names are opaque).
        #   auto (default) — inspect file-naming quality and pick: opaque/numbered/non-latin names -> source
        #                    (names are noise, read the code); domain-worded names -> hybrid (cheaper, trusted).
        if module_derivation == "auto":
            try:
                from ..tools.source_module_clustering import choose_engine

                _dec = choose_engine(str(BASE_PROJECT), enriched_path=str(GLOBAL_ENRICHED))
                module_derivation = _dec["engine"]
                print(
                    f"\n[GLOBAL] Stage 2.5: auto-detect engine -> '{module_derivation}'. {_dec['reason']}"
                )
                if _dec.get("sample_opaque"):
                    print(f"          opaque names e.g.: {_dec['sample_opaque']}")
            except Exception as e:
                module_derivation = "hybrid"
                print(f"\n[GLOBAL] Stage 2.5: auto-detect failed ({e}) — using 'hybrid'.")
        if module_derivation == "source":
            print("\n[GLOBAL] Stage 2.5: Module Derivation from Source (map-reduce)...")
            _llm = _make_llm_complete()
            _done = False
            if _llm:
                try:
                    from ..tools.source_module_clustering import derive_modules_from_source

                    _s = derive_modules_from_source(
                        str(BASE_PROJECT), _llm, manifest_path=str(MODULE_MANIFEST)
                    )
                    if _s and _s.get("manifest_written"):
                        print(
                            f"[SRC-MODULES] {len(_s['modules'])} module(s) written to manifest "
                            f"(infrastructure: {(_s.get('manifest_info') or {}).get('infrastructure', 0)})"
                            + ("  [domain-fallback used]" if _s.get("used_domain_fallback") else "")
                        )
                        _done = True
                except Exception as e:
                    print(f"[ERROR] Source module derivation failed: {e}")
            if not _done:
                print("[WARN] Source derivation unavailable — falling back to hybrid Stage 2.5.")
                if build_hybrid_manifest:
                    try:
                        build_hybrid_manifest(GLOBAL_INDEX, GLOBAL_ENRICHED, MODULE_MANIFEST)
                    except Exception as e:
                        print(f"[ERROR] Hybrid Manifest Generator failed: {e}")
        elif build_hybrid_manifest:
            print("\n[GLOBAL] Stage 2.5: Intelligent Module Discovery (Hybrid Manifest)...")
            try:
                build_hybrid_manifest(GLOBAL_INDEX, GLOBAL_ENRICHED, MODULE_MANIFEST)
            except Exception as e:
                print(f"[ERROR] Hybrid Manifest Generator failed: {e}")
        else:
            print("\n[WARN] build_hybrid_manifest not found. Skipping Stage 2.5.")

        modules = discover_modules(input_root)
        print(f"\n[ORCHESTRATOR] Discovered {len(modules)} logical module(s).")

        # Apply enterprise module class filter — language-agnostic policy from project_config.
        # Infrastructure modules (fan_in >= 2 catch-basins) are excluded here; their
        # artifacts are fully covered as dependencies of the business modules that use them.
        project_cfg = _load_project_config()
        modules = _apply_module_class_filter(modules, project_cfg)
        if not modules:
            print(
                "[ERROR] No modules remain after class filter. "
                "Check module_pipeline.skip_classes in project_config.json."
            )
            return
        print(f"[ORCHESTRATOR] {len(modules)} module(s) queued for modular pipeline...")

    # Build ONE shared SpecExtractor instance for the entire run.
    # Source-file indexing (417 files) and global-metadata loading (417 artifacts)
    # are expensive one-time operations — doing them once per module wastes cycles.
    # set_target_module() retargets the same instance cheaply for each module.
    shared_extractor = None
    if SpecExtractor:
        try:
            shared_extractor = SpecExtractor(
                project_root=str(Path.cwd()),
                config_path=str(PROJECT_CONFIG),
                prompt_dir=str(Path.cwd() / "prompts"),
                source_dir=str(get_input_root()),
                global_enriched_path=str(GLOBAL_ENRICHED),
            )
            print("[SPECS] SpecExtractor initialised once — will be reused across all modules.")
        except Exception as e:
            print(
                f"[WARN] SpecExtractor could not be initialised: {e}. Per-module fallback will be used."
            )
            shared_extractor = None

    for module in modules:
        run_pipeline_for_module(module, shared_extractor=shared_extractor)

    # --- Task 5.4 Integration: Post-Extraction Semantic Dual-Property Rehydration Loop ---
    # Stage 2.7 is factored into refine_module_manifest() so it can ALSO run standalone
    # via the `refine` CLI command (recommended order: specs -> refine -> features).
    #
    # Module-filter resolution (unchanged semantics):
    #   --module mode: pass the raw CLI filter directly (always non-empty, exactly typed).
    #   Full run:      derive from the class-filtered module list (infra already excluded
    #                  by _apply_module_class_filter()).  None == "process all".
    if module_filter:
        stage27_filter = module_filter
    else:
        stage27_filter = [_module_id(m) for m in modules if _module_id(m)] or None

    refine_module_manifest(stage27_filter, shared_extractor=shared_extractor)

    # ── Phase 3: Feature & User Story Derivation (Stage 5) ───────────────────────
    # Runs AFTER Stage 2.7 so refined module_manifest.json is fully written.
    # Each module's Stage 5a call can now inject [MODULE CONTEXT] with DDD-quality
    # module_name and description from the semantic rehydration pass.
    print(
        f"\n[ORCHESTRATOR] Phase 3: Feature & User Story derivation (Stage 5) across {len(modules)} module(s)..."
    )
    stage5_total = 0
    stage5_approved = 0
    for module in modules:
        results = run_stage5_for_module(module)
        stage5_total += len(results)
        stage5_approved += sum(
            1
            for r in results
            if r and r.get("generation_metadata", {}).get("final_status") == "PASS"
        )

    if FeatureStoryAgent:
        print(
            f"[ORCHESTRATOR] Phase 3 complete: {stage5_approved}/{stage5_total} MFU outputs approved by Critic."
        )

    # --- Phase 4 Global Integrations ---
    if Neo4jExporter:
        print("\n[GLOBAL] Stage 5a: Building Legacy Traceability Matrix (Neo4j)...")
        try:
            exporter = Neo4jExporter(
                project_root=str(Path.cwd()),
            )
            exporter.build_matrix()
            out_path = BASE_PROJECT / "output" / "traceability_matrix.cypher"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            exporter.export(str(out_path))
        except Exception as e:
            print(f"[ERROR] Neo4jExporter (build_matrix) failed: {e}")

        print("\n[GLOBAL] Stage 5b: Building Feature & Story Traceability Graph (Neo4j)...")
        try:
            fs_exporter = Neo4jExporter(
                project_root=str(Path.cwd()), global_graph_path=str(GLOBAL_ENRICHED)
            )
            fs_exporter.build_feature_story_graph()
            fs_out_path = BASE_PROJECT / "output" / "feature_story_graph.cypher"
            fs_out_path.parent.mkdir(parents=True, exist_ok=True)
            fs_exporter.export(str(fs_out_path))
        except Exception as e:
            print(f"[ERROR] Neo4jExporter (build_feature_story_graph) failed: {e}")
    else:
        print("\n[SKIP] Neo4jExporter not found. Traceability graph will not be generated.")

    if ReportGenerator:
        print("\n[GLOBAL] Stage 6: Generating Executive RIP Reports...")
        try:
            report_gen = ReportGenerator(
                project_root=str(BASE_PROJECT), config_path=str(PROJECT_CONFIG)
            )
            report_gen.aggregate_results()
            report_gen.export_all()
        except Exception as e:
            print(f"[ERROR] ReportGenerator failed: {e}")
    else:
        print("\n[SKIP] ReportGenerator not found. Executive reports will not be generated.")

    # --- Phase 7: DDD macro onboarding ---
    # Runs for BOTH engines: it reads whatever module source exists (source_modules.json in source mode,
    # else the hybrid module_manifest.json) and synthesises the macro brief. Additive _global/ artifact;
    # non-fatal (the rest of the run already succeeded). In source mode the module derivation itself already
    # happened at Stage 2.5, so we do NOT re-run clustering here.
    print("\n[GLOBAL] Stage 7: DDD macro onboarding...")
    try:
        cmd_ddd_onboarding()
    except Exception as e:
        print(f"[ERROR] ddd-onboarding failed (non-fatal): {e}")

    print("\n[ORCHESTRATOR] Full Pipeline Execution Complete.")


# ---------------- CLI Entry Point ----------------


def main():
    from app.utils.pipeline_output import ensure_stdout_tagging

    ensure_stdout_tagging()
    parser = argparse.ArgumentParser(description="RIP Extraction PoC CLI")
    # Added Phase 4 individual commands: specs, neo4j, report, features
    parser.add_argument(
        "command",
        choices=[
            "scan",
            "graph",
            "ai",
            "run",
            "compare",
            "specs",
            "refine",
            "neo4j",
            "report",
            "features",
            "revise",
            "derive-modules-source",
            "ddd-onboarding",
            "compare-modules",
            "derive-architecture",
            "approve-architecture",
            "export-architecture",
            "validate-specs",
        ],
    )
    parser.add_argument(
        "--compare-a",
        dest="compare_a",
        default=None,
        help="compare-modules: engine-A assignment file (default: _global/module_manifest.json).",
    )
    parser.add_argument(
        "--compare-b",
        dest="compare_b",
        default=None,
        help="compare-modules: engine-B assignment file (default: _global/source_modules.json).",
    )
    parser.add_argument(
        "--arch-modules",
        dest="arch_modules",
        default=None,
        help="derive-architecture: OPTIONAL manual override — comma-separated seed module ids. By default Stage 6 auto-selects 3-5 representative modules (online+batch+shared+core coverage); this flag is only for overriding that automatic choice.",
    )
    parser.add_argument(
        "--max-iters",
        dest="max_iters",
        type=int,
        default=2,
        help="derive-architecture: max generator-critic iterations (default 2).",
    )
    parser.add_argument(
        "--approver",
        dest="approver",
        default=None,
        help="approve-architecture: name/email freezing the architecture.",
    )
    parser.add_argument(
        "--feature",
        dest="feature_id",
        default=None,
        help="export-architecture: feature id to assemble a codegen bundle for.",
    )
    parser.add_argument(
        "--module-derivation",
        dest="module_derivation",
        choices=["auto", "hybrid", "source"],
        default="auto",
        help="Module-discovery engine for 'run'. auto (default) = inspect file-naming quality and "
        "pick automatically: opaque/numbered/non-latin names -> source, domain-worded names -> "
        "hybrid. hybrid = telemetry-budgeted call-graph clustering. source = map-reduce over full "
        "source + file names. All three write the same module_manifest.json contract so every "
        "downstream stage is identical.",
    )
    parser.add_argument(
        "module",
        nargs="?",
        help="Module name (required for compare and optional for specs/features)",
    )

    # #42 — human-feedback regeneration (the app's "revise" review action). Re-runs a single
    # MFU with PM guidance injected into the generators; output is re-validated by the critic
    # + grounding gates. Example:
    #   python -m src.cli.main revise --module MOD-PDQS --mfu MFU-006 --feedback "Passive order-number input component, not a login screen. Regenerate from the SRS."
    parser.add_argument(
        "--mfu", dest="mfu_id", metavar="MFU_ID", help="Target MFU id for 'revise' (e.g. MFU-006)."
    )
    parser.add_argument(
        "--feedback",
        dest="human_feedback",
        metavar="TEXT",
        help="Human reviewer (PM) guidance for 'revise'.",
    )
    parser.add_argument(
        "--quoted-text",
        dest="quoted_text",
        metavar="TEXT",
        default=None,
        help="Optional excerpt of the CURRENT output the feedback refers to (reference only).",
    )
    parser.add_argument(
        "--mode",
        dest="revise_mode",
        choices=["edit", "regenerate"],
        default="edit",
        help="Revision mode for 'revise'. edit (default) = edit the current output, "
        "preserving everything the feedback does not mention; regenerate = redo from the SRS.",
    )
    parser.add_argument(
        "--target",
        dest="revise_targets",
        action="append",
        metavar="STORY_ID",
        help="Story id(s) the feedback targets (repeatable; = the UI node the reviewer "
        "selected). When given, edit mode GUARANTEES only these stories change and "
        "all others are restored verbatim from the backup. Omit for best-effort "
        "preserve (anchors restored, text left to the model).",
    )
    parser.add_argument(
        "--feedback-json",
        dest="feedback_json",
        metavar="SRC",
        default=None,
        help="Structured reviewer feedback for 'revise'. SRC is one of: '-' to read JSON from "
        "STDIN (recommended for app->CLI: no temp file, no arg-length limit), a FILE PATH "
        "(operator/batch), or an INLINE JSON string (quick tests). JSON form: "
        '{"module":..,"mfu":..,"feedback":[{"story_id":..,"items":'
        '[{"quoted_text":..(optional),"feedback_text":..}]}]}. story_id null or "*" = '
        "feature-wide. OVERRIDES --feedback/--quoted-text/--target: derives --target from the "
        "story_ids and composes per-story edit guidance. module/mfu in the JSON are used only "
        "when --module/--mfu are omitted. (Same-runtime Python callers should instead call "
        "cmd_revise(..., feedback_spec=<dict>) in-process — no serialisation.)",
    )

    # ENTERPRISE FIX: Add the optional --project argument to correctly path custom workspaces
    parser.add_argument(
        "--project", default="projects/sample_project", help="Target project directory path"
    )

    # MODULE FILTER: Re-run only specific module(s) — skips global scan/graph/manifest stages.
    # Requires a prior full run (GLOBAL_ENRICHED must already exist).
    # Examples:
    #   python -m src.cli.main run --module MOD-IRAIDEN
    #   python -m src.cli.main run --module MOD-IRAIDEN MOD-KEIJYO
    parser.add_argument(
        "--module",
        nargs="+",
        dest="module_filter",
        metavar="MODULE_NAME",
        help=(
            "Re-run only the named module(s), skipping global scan/graph/manifest stages. "
            "Requires a prior full run. "
            "Example: --module MOD-IRAIDEN  or  --module MOD-IRAIDEN MOD-KEIJYO"
        ),
    )

    args = parser.parse_args()

    # ENTERPRISE FIX: Apply dynamic project paths globally before plugins initialize
    set_project_paths(args.project)

    # --- TASK 3.2: Initialize Registry (singleton — auto-initializes on instantiation) ---
    PluginRegistry()

    # Resolve single-module name from either positional arg or --module flag.
    # Positional: `main.py features MOD-X`
    # Flag:       `main.py features --module MOD-X`
    _single_module = args.module or (args.module_filter[0] if args.module_filter else None)

    # --- Command Dispatch ---
    if args.command == "scan":
        cmd_scan()
    elif args.command == "graph":
        cmd_graph()
    elif args.command == "ai":
        cmd_ai()
    elif args.command == "run":
        cmd_run(module_filter=args.module_filter, module_derivation=args.module_derivation)
    elif args.command == "compare":
        if not _single_module:
            print("[ERROR] 'compare' requires a module name (positional or --module).")
        else:
            cmd_compare(_single_module)
    elif args.command == "specs":
        cmd_specs(_single_module)
    elif args.command == "refine":
        cmd_refine(_single_module)
    elif args.command == "neo4j":
        cmd_neo4j()
    elif args.command == "report":
        cmd_report()
    elif args.command == "features":
        # `features` supports one OR many modules. When --module lists several
        # (e.g. --module MOD-A MOD-B), process EACH in turn — mirroring `run --module`
        # — instead of silently dropping all but the first (module_filter[0]). The
        # positional `features MOD-X` and the no-argument "all modules" forms are
        # unchanged.
        if args.module_filter and len(args.module_filter) > 1:
            print(
                f"[STAGE5] --module filter: {len(args.module_filter)} modules "
                f"queued: {args.module_filter}"
            )
            for _m in args.module_filter:
                cmd_features(_m)
        else:
            cmd_features(_single_module)
    elif args.command == "revise":
        feedback_spec = None
        _fj = getattr(args, "feedback_json", None)
        if _fj:
            try:
                feedback_spec = _parse_feedback_json(_resolve_feedback_json_arg(_fj))
            except Exception as e:
                print(f"[ERROR] revise --feedback-json: {e}")
                return
        # --module/--mfu are authoritative; fall back to the JSON's module/mfu when they're omitted.
        _mod = _single_module or (feedback_spec or {}).get("module")
        _mfu = args.mfu_id or (feedback_spec or {}).get("mfu")
        if feedback_spec:
            if (
                _single_module
                and feedback_spec.get("module")
                and _single_module != feedback_spec["module"]
            ):
                print(
                    f"[REVISE] note: --module {_single_module} overrides JSON module "
                    f"{feedback_spec['module']}."
                )
            if args.mfu_id and feedback_spec.get("mfu") and args.mfu_id != feedback_spec["mfu"]:
                print(
                    f"[REVISE] note: --mfu {args.mfu_id} overrides JSON mfu {feedback_spec['mfu']}."
                )
        cmd_revise(
            _mod,
            _mfu,
            args.human_feedback,
            args.revise_mode,
            quoted_text=getattr(args, "quoted_text", None),
            target_ids=getattr(args, "revise_targets", None),
            feedback_spec=feedback_spec,
        )
    elif args.command == "derive-modules-source":
        cmd_derive_modules_source()
    elif args.command == "ddd-onboarding":
        cmd_ddd_onboarding()
    elif args.command == "compare-modules":
        cmd_compare_modules(getattr(args, "compare_a", None), getattr(args, "compare_b", None))
    elif args.command == "derive-architecture":
        cmd_derive_architecture(getattr(args, "arch_modules", None), getattr(args, "max_iters", 2))
    elif args.command == "approve-architecture":
        cmd_approve_architecture(getattr(args, "approver", None))
    elif args.command == "export-architecture":
        cmd_export_architecture(getattr(args, "feature_id", None))
    elif args.command == "validate-specs":
        cmd_validate_specs()
    else:
        print(f"[ERROR] Unknown command: {args.command}")


if __name__ == "__main__":
    try:
        main()
    except BaseException as _abort:
        # Circuit-breaker abort (BaseException, SystemExit-like): print the
        # reason and exit cleanly with a distinct non-zero code instead of a
        # raw traceback. Anything else (KeyboardInterrupt, SystemExit, real
        # errors) propagates unchanged.
        if _abort.__class__.__name__ == "CircuitBreakerError":
            print(f"\n[RIP] Run aborted by circuit-breaker: {_abort}")
            sys.exit(2)
        raise
