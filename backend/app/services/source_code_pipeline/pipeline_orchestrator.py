from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
from typing import Any

from dotenv import load_dotenv

from .feedback_grouping import FeedbackGroupingService
from .source_code_pipeline_service import SourceCodePipelineService
from .spec_extractor import SpecExtractor
from .src.ai.llm_client import LLMClient, PipelineRunCancelled
from .src.cli.main import PluginRegistry, load_and_register_plugins, set_project_paths
from .src.utils.json_schema_validator import JSONSchemaValidator

try:
    from .flatten_source import discover_language_extensions
except Exception:
    discover_language_extensions = None

try:
    from app.utils.logger import get_logger

    logger = get_logger(__name__)
except ImportError:
    import logging

    logger = logging.getLogger(__name__)


@dataclass
class PipelineContext:
    """Configuration context for pipeline execution."""

    project_dir: str
    source_dir: str
    config_path: str
    module_filter: list[str] | None = None
    skip_processing: bool = False
    request_id: str | None = None
    llm_api_key: str | None = None

    def __post_init__(self):
        """Ensure paths are Path objects."""
        self.project_dir = Path(self.project_dir)
        self.source_dir = Path(self.source_dir)
        self.config_path = Path(self.config_path)

        # Derived paths - calculated from project_dir
        self.global_dir = self.project_dir / "_global"

        # Pipeline resource paths - calculated from module location
        pipeline_root = Path(__file__).parent
        self.prompt_dir = pipeline_root / "prompts"
        self.schema_dir = pipeline_root / "schemas"
        self.archetype_dir = pipeline_root / "config"
        logger.info("[DEBUG] prompt dir: %s", self.prompt_dir)
        logger.info("[DEBUG] schema dir: %s", self.schema_dir)
        logger.info("[DEBUG] archetype dir: %s", self.archetype_dir)


class PipelineOrchestrator:
    """
    Orchestrates the complete RIP migration pipeline.

    Usage:
        context = PipelineContext(
            project_dir="projects/sample_project",
            source_dir="projects/sample_project/input/pb_src",
            config_path="projects/sample_project/project_config.json",
        )

        pipeline = PipelineOrchestrator(context)

        # Run complete global pipeline
        global_artifacts = pipeline.generate_global_artifacts()

        # Run module processing
        results = pipeline.process_all_modules()
    """

    def __init__(self, context: PipelineContext):
        """
        Initialize pipeline orchestrator.

        Args:
            context: Pipeline configuration context
        """
        from app.utils.pipeline_output import ensure_stdout_tagging

        ensure_stdout_tagging()
        self.context = context

        # Load config early so startup preprocessing can run before Stage 1.
        self.project_config = self._load_project_config()
        effective_source_dir = self._prepare_source_layout()
        self.context.source_dir = effective_source_dir

        # Initialize core services
        self.service = SourceCodePipelineService(
            project_dir=str(context.project_dir),
            source_dir=str(self.context.source_dir),
            config_path=str(context.config_path),
            prompt_dir=str(context.prompt_dir),
            schema_dir=str(context.schema_dir),
            archetype_dir=str(context.archetype_dir),
            api_key=context.llm_api_key,
        )
        self.project_config = self.service._config

        set_project_paths(str(context.project_dir))

        # Load plugins
        self.active_plugins = load_and_register_plugins(str(self.context.source_dir))
        # Set module-level ACTIVE_PLUGINS in main.py so execute_global_scan can use them
        from .src.cli import main as cli_main

        cli_main.ACTIVE_PLUGINS = self.active_plugins

        # Create global directories
        self._setup_directories()

    def _load_project_config(self) -> dict[str, Any]:
        """Load project config safely for orchestration-time decisions."""
        try:
            return json.loads(self.context.config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("[PIPELINE] Failed to load config for startup preprocessing: %s", exc)
            return {}

    def _prepare_source_layout(self) -> Path:
        """
        Startup preprocessing hook.

        Applies config-driven source flattening before scanner/plugin initialization,
        so Stage 1/2.5 operate on the effective source layout.
        """
        source_dir = Path(self.context.source_dir).resolve()
        preprocess = self.project_config.get("preprocess", {})
        if not isinstance(preprocess, dict):
            return source_dir

        flatten_cfg = preprocess.get("flatten", {})
        if not isinstance(flatten_cfg, dict):
            flatten_cfg = {}

        preprocess_enabled = bool(preprocess.get("enabled", False))
        flatten_enabled = bool(flatten_cfg.get("enabled", preprocess_enabled))
        mode = str(flatten_cfg.get("mode", "off")).strip().lower()
        if mode not in {"off", "auto", "always"}:
            mode = "auto"

        if not flatten_enabled or mode == "off":
            logger.info("[PIPELINE] Preprocess flatten disabled (mode=%s).", mode)
            return source_dir

        if mode == "always":
            should_flatten = True
        else:
            should_flatten = self._looks_file_type_layout(source_dir)

        if not should_flatten:
            logger.info("[PIPELINE] Source layout kept as-is (flatten mode=%s).", mode)
            return source_dir

        project_cfg = self.project_config.get("project", {})
        language = (
            str(flatten_cfg.get("language") or project_cfg.get("source_paradigm") or "")
            .strip()
            .lower()
        )
        out_raw = flatten_cfg.get("output_path")
        if out_raw:
            out_dir = Path(out_raw)
            if not out_dir.is_absolute():
                out_dir = (self.context.project_dir / out_dir).resolve()
        else:
            out_dir = (
                self.context.project_dir / "_preprocessed" / f"{language or 'source'}_flat"
            ).resolve()

        if out_dir == source_dir or out_dir in source_dir.parents:
            logger.warning(
                "[PIPELINE] Flatten output_path must not be source_dir or its parent; skipping flatten. out=%s src=%s",
                out_dir,
                source_dir,
            )
            return source_dir

        stats = self._flatten_source_tree(language, source_dir, out_dir)
        logger.info(
            "[PIPELINE] Preprocess flatten complete: lang=%s copied=%d matched=%d scanned=%d collisions=%d out=%s",
            language,
            stats.get("copied", 0),
            stats.get("matched", 0),
            stats.get("scanned", 0),
            stats.get("collisions", 0),
            out_dir,
        )
        return out_dir

    @property
    def _run_id(self) -> str:
        """Per-project run key: '/temp/source_codes/<project_id>' in production."""
        return str(self.context.project_dir)

    def _looks_file_type_layout(self, source_dir: Path) -> bool:
        """Heuristic detector for extension-bucketed source trees."""
        child_dirs = [d for d in source_dir.iterdir() if d.is_dir()]
        if len(child_dirs) < 2:
            return False

        profiles = []
        for d in child_dirs:
            ext_counts: dict[str, int] = {}
            total_files = 0
            for p in d.rglob("*"):
                if not p.is_file():
                    continue
                total_files += 1
                ext = p.suffix.lower()
                if not ext:
                    continue
                ext_counts[ext] = ext_counts.get(ext, 0) + 1
                if total_files >= 200:
                    break

            if total_files == 0 or not ext_counts:
                continue

            dominant_ext = max(ext_counts, key=ext_counts.get)
            dominant_ratio = ext_counts[dominant_ext] / max(total_files, 1)
            profiles.append((dominant_ext, dominant_ratio))

        if len(profiles) < 2:
            return False

        unique_dominant_exts = {p[0] for p in profiles}
        avg_ratio = sum(p[1] for p in profiles) / len(profiles)
        is_bucketed = len(unique_dominant_exts) >= 2 and avg_ratio >= 0.75
        if is_bucketed:
            logger.info(
                "[PIPELINE] Detected file-type-organized source layout (avg dominant ratio=%.2f).",
                avg_ratio,
            )
        return is_bucketed

    def _flatten_source_tree(self, language: str, src: Path, out: Path) -> dict[str, int]:
        """Programmatic flatten operation using plugin-manifest extensions."""
        if discover_language_extensions is None:
            raise RuntimeError("flatten_source discover_language_extensions unavailable")

        langs = discover_language_extensions()
        if language not in langs:
            raise ValueError(
                f"Unsupported flatten language '{language}'. Available: {sorted(langs)}"
            )

        exts = langs[language]
        matched: list[Path] = []
        scanned = 0
        for p in sorted(src.rglob("*")):
            if not p.is_file():
                continue
            scanned += 1
            if p.suffix.lower() in exts:
                matched.append(p)

        seen: dict[str, Path] = {}
        to_copy: list[Path] = []
        collisions = 0
        for p in matched:
            key = p.name.lower()
            if key in seen:
                collisions += 1
                continue
            seen[key] = p
            to_copy.append(p)

        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True, exist_ok=True)
        for p in to_copy:
            shutil.copy2(p, out / p.name)

        return {
            "scanned": scanned,
            "matched": len(matched),
            "copied": len(to_copy),
            "collisions": collisions,
        }

    def _setup_directories(self):
        """Create necessary directory structure."""
        (self.context.project_dir / "_global").mkdir(parents=True, exist_ok=True)
        (self.context.project_dir / "modules").mkdir(parents=True, exist_ok=True)

    @staticmethod
    def configure_project(
        project_dir: Path,
        source_paradigm: str = "pb",
        target_stack: dict | None = None,
        modernization_manifesto: dict | None = None,
        options: dict | None = None,
        source_layout_type: str | None = None,
    ) -> Path:
        """
        Configure project by creating/updating project_config.json.

        Args:
            project_dir: Root directory for the project
            source_paradigm: Source code paradigm (pb, java, python, etc)
            target_stack: Target technology stack configuration
            modernization_manifesto: Modernization strategy and guidelines
            options: Optional LLM/runtime options dict. Supported keys:
                - llm_provider: provider id (openai|anthropic|gemini|deepseek)
                - llm_model: model override for selected provider
                - llm_api_key: API key for selected provider
                The API key is runtime-only and is never written to the config.
            source_layout_type: Single backend field that describes source layout
                (supported: "flat", "modular", "auto")

        Returns:
            Path to the created/updated project_config.json file
        """
        import json

        config_path = project_dir / "project_config.json"

        # Load base config template based on source paradigm
        base_config_filename = f"{source_paradigm}-project_config.json"
        base_config_path = Path("app/services/source_code_pipeline") / base_config_filename

        logger.info("[PIPELINE] Loading base config template: %s", base_config_path)
        with open(base_config_path, encoding="utf-8") as f:
            config_data = json.load(f)

        # Update config with provided parameters
        if target_stack:
            logger.info("[PIPELINE] Updating target_stack in config")
            config_data.setdefault("project", {})["target_stack"] = target_stack

        if modernization_manifesto:
            logger.info("[PIPELINE] Updating modernization_manifesto in config")
            config_data["modernization_manifesto"] = modernization_manifesto

        opts = options if isinstance(options, dict) else {}
        llm_section = config_data.setdefault("llm", {})
        providers_cfg = llm_section.setdefault("providers", {})

        provider_value = str(opts.get("llm_provider") or "").strip().lower()
        if provider_value:
            logger.info("[PIPELINE] Updating active_provider in config: %s", provider_value)
            llm_section["active_provider"] = provider_value

        model_value = str(opts.get("llm_model") or "").strip()
        selected_provider = str(llm_section.get("active_provider") or "").strip().lower()

        if model_value and selected_provider:
            provider_cfg = providers_cfg.setdefault(selected_provider, {})

            if "/" not in model_value:
                prefix_map = {
                    "anthropic": "anthropic/",
                    "gemini": "gemini/",
                    "deepseek": "deepseek/",
                }
                model_value = f"{prefix_map.get(selected_provider, '')}{model_value}"

            provider_cfg["model"] = model_value
            provider_cfg["reasoning_model"] = model_value

            logger.info(
                "[PIPELINE] Updated provider model override: provider=%s model=%s",
                selected_provider,
                model_value,
            )

        # Ensure source_paradigm and plugins are set correctly
        config_data.setdefault("project", {})["source_paradigm"] = source_paradigm
        if "plugins" not in config_data.get("project", {}):
            config_data.setdefault("project", {})["plugins"] = [source_paradigm]

        # Single-field backend input for layout -> preprocess + Stage-2.5 mapping.
        # Guide-aligned semantics:
        # - modular: source already module-per-folder => no flatten, folder clustering.
        # - flat: source is file-type/flat-style => flatten, avoid folder-only clustering.
        # - auto: let flatten heuristics decide, keep Stage-2.5 on auto.
        logger.info("[PIPELINE] Configuring source layout type: %s", source_layout_type)
        if source_layout_type is not None:
            layout = str(source_layout_type).strip().lower()
            if layout not in {"flat", "modular", "auto"}:
                raise ValueError("source_layout_type must be one of: flat, modular, auto")

            preprocess_cfg = config_data.setdefault("preprocess", {})
            flatten_cfg = preprocess_cfg.setdefault("flatten", {})
            stage2_5_cfg = config_data.setdefault("stage2_5", {})

            if layout == "modular":
                preprocess_cfg["enabled"] = False
                flatten_cfg["enabled"] = False
                flatten_cfg["mode"] = "off"
                stage2_5_cfg["clustering_strategy"] = "folder"
            elif layout == "flat":
                preprocess_cfg["enabled"] = True
                flatten_cfg["enabled"] = True
                flatten_cfg["mode"] = "always"
                stage2_5_cfg.setdefault("clustering_strategy", "auto")
            else:  # auto ("Unknown")
                # Do NOT flatten and do NOT run the unreliable file-type-layout
                # heuristic: when the user doesn't know the layout, module grouping
                # is delegated to AI-semantic clustering (Stage 2.5), which is
                # folder-agnostic. Keeping the original tree is lossless (avoids the
                # flatten name-collision drop) and does not affect clustering quality.
                preprocess_cfg["enabled"] = False
                flatten_cfg["enabled"] = False
                flatten_cfg["mode"] = "off"
                stage2_5_cfg.setdefault("clustering_strategy", "auto")

            flatten_cfg.setdefault("language", source_paradigm)
            flatten_cfg.setdefault("output_path", f"_preprocessed/{source_paradigm}_flat")
            flatten_cfg.setdefault("collision_policy", "keep-first")

        # Save customized config to project directory
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(config_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info(
            "[PIPELINE] Saved project config: %s (paradigm=%s)", config_path, source_paradigm
        )

        return config_path

    # -------------------------------------------------------------------------
    # Global Artifact Generation (Stages 1A → 2.5)
    # -------------------------------------------------------------------------

    def generate_global_artifacts(self, module_derivation: str = "auto") -> dict[str, Any]:
        """
        Execute complete global artifact generation pipeline (Stages 1A → 2.5).
        Runs all stages sequentially, passing data between them.

        Returns:
            Dict containing all global artifacts:
                - global_index
                - artifacts_detected
                - artifacts_enriched
                - module_manifest
                - budget_report
                - filtered_modules
        """
        logger.info("[PIPELINE] ===== Starting Global Artifact Generation =====")

        with LLMClient.pipeline_run_guard(self._run_id, request_id=self.context.request_id):
            source_dir = Path(self.context.source_dir)
            if not source_dir.exists():
                raise FileNotFoundError(
                    "[PIPELINE] Source directory does not exist: "
                    f"{source_dir}. Update project_config.project.source_path or pass a valid source_dir."
                )
            if not source_dir.is_dir():
                raise NotADirectoryError(
                    f"[PIPELINE] Source path is not a directory: {source_dir}."
                )

            if self.context.skip_processing:
                logger.info(
                    "[PIPELINE] skip_processing=True — loading global artifacts from sample_results"
                )
                return self._load_sample(
                    "global_artifacts_result.json",
                    subdir="global_artifacts",
                )

            # Stage 1a
            stage_1a = self._run_stage_1a()

            # Stage 1b
            stage_1b = self._run_stage_1b()

            # Stage 2
            stage_2 = self._run_stage_2(stage_1b["artifacts_detected"])

            # Stage 2.5
            stage_2_5 = self._run_stage_2_5(
                stage_1a["global_index"],
                stage_2["artifacts_enriched"],
                module_derivation=module_derivation,
            )

            # Filter modules
            filtered_modules = self._filter_modules(stage_2_5["module_manifest"])

            logger.info("[PIPELINE] ✓ Global artifacts generation complete")

            result = {
                "artifacts_enriched": stage_2["artifacts_enriched"],
                "module_manifest": stage_2_5["module_manifest"],
                "filtered_modules": filtered_modules,
            }

            return result

    def generate_code_dependency_graph(self) -> dict[str, Any]:
        """Execute Stages 1A-2 and return the code dependency graph artifacts."""
        logger.info("[PIPELINE] ===== Starting Code Dependency Graph Generation =====")

        with LLMClient.pipeline_run_guard(self._run_id, request_id=self.context.request_id):
            source_dir = Path(self.context.source_dir)
            if not source_dir.exists():
                raise FileNotFoundError(
                    "[PIPELINE] Source directory does not exist: "
                    f"{source_dir}. Update project_config.project.source_path or pass a valid source_dir."
                )
            if not source_dir.is_dir():
                raise NotADirectoryError(
                    f"[PIPELINE] Source path is not a directory: {source_dir}."
                )

            if self.context.skip_processing:
                logger.info(
                    "[PIPELINE] skip_processing=True — loading dependency graph from sample_results"
                )
                sample = self._load_sample(
                    "dependency_graph.json",
                    subdir="global_artifacts",
                )
                return {
                    key: sample[key]
                    for key in ("global_index", "artifacts_detected", "artifacts_enriched")
                    if key in sample
                }

            # Stage 1a
            stage_1a = self._run_stage_1a()

            # Stage 1b
            stage_1b = self._run_stage_1b()

            # Stage 2
            stage_2 = self._run_stage_2(stage_1b["artifacts_detected"])

            logger.info("[PIPELINE] ✓ Code dependency graph generation complete")
            return {
                "global_index": stage_1a["global_index"],
                "artifacts_detected": stage_1b["artifacts_detected"],
                "artifacts_enriched": stage_2["artifacts_enriched"],
            }

    def discover_modules(
        self,
        dependency_graph: dict[str, Any],
        module_derivation: str = "auto",
    ) -> dict[str, Any]:
        """Execute Stage 2.5 using the output of code dependency graph generation."""
        logger.info("[PIPELINE] ===== Starting Module Discovery =====")

        required_keys = ("global_index", "artifacts_enriched")
        missing_keys = [key for key in required_keys if key not in dependency_graph]
        if missing_keys:
            raise ValueError(
                "[PIPELINE] Dependency graph is missing required key(s): "
                f"{', '.join(missing_keys)}"
            )

        with LLMClient.pipeline_run_guard(self._run_id, request_id=self.context.request_id):
            if self.context.skip_processing:
                logger.info(
                    "[PIPELINE] skip_processing=True — loading module discovery from sample_results"
                )
                sample = self._load_sample(
                    "global_artifacts_result.json",
                    subdir="global_artifacts",
                )
                module_manifest = sample["module_manifest"]
                budget_report = sample.get("budget_report", {})
            else:
                # Stage 2.5
                stage_2_5 = self._run_stage_2_5(
                    dependency_graph["global_index"],
                    dependency_graph["artifacts_enriched"],
                    module_derivation=module_derivation,
                )
                module_manifest = stage_2_5["module_manifest"]
                budget_report = stage_2_5["budget_report"]

            filtered_modules = self._filter_modules(module_manifest)

            logger.info("[PIPELINE] ✓ Module discovery complete")
            return {
                **dependency_graph,
                "module_manifest": module_manifest,
                "budget_report": budget_report,
                "filtered_modules": filtered_modules,
            }

    def generate_domain_knowledge(self, max_tokens: int = 6000) -> dict[str, Any]:
        """Generate the project-level domain knowledge Markdown document."""
        with LLMClient.pipeline_run_guard(self._run_id, request_id=self.context.request_id):
            if self.context.skip_processing:
                logger.info(
                    "[PIPELINE] skip_processing=True — loading domain knowledge from sample_results"
                )
                return self._load_sample(
                    "domain_knowledge_result.json",
                    subdir="system_docs",
                )

            return self.service.generate_domain_knowledge(max_tokens=max_tokens)

    def generate_architecture_document(
        self,
        seed_modules: list[str] | None = None,
        max_iters: int = 2,
    ) -> dict[str, Any]:
        """Generate the target architecture Markdown and structured JSON."""
        with LLMClient.pipeline_run_guard(self._run_id, request_id=self.context.request_id):
            if self.context.skip_processing:
                logger.info(
                    "[PIPELINE] skip_processing=True — loading architecture document from sample_results"
                )
                return self._load_sample(
                    "architecture_document_result.json",
                    subdir="system_docs",
                )

            return self.service.generate_architecture_document(
                seed_modules=seed_modules,
                max_iters=max_iters,
            )

    # -------------------------------------------------------------------------
    # Complete Module-Level Processing for Spec, Feature, User Story Generation (Stages 3 → 4 → 5)
    # -------------------------------------------------------------------------

    def process_complete_module_pipeline(
        self,
        artifacts_enriched: dict[str, Any] | None = None,
        module_manifest: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Execute complete module-level processing in a single loop.
        For each module: spec generation → name refinement → feature derivation

        This assumes global artifacts (Stages 1A-2.5) have already been generated.

        Args:
            artifacts_enriched: Optional pre-loaded enriched artifacts.
                            If None, loads from stage2_artifacts_enriched.json
            module_manifest: Optional pre-loaded module manifest.
                            If None, loads from stage2_5_module_manifest.json

        Returns:
            Dict containing:
                - module_results: Dict mapping module_id to all module processing results
                - all_features: Dict of all features by module
                - summary: High-level statistics

        Example:
            pipeline = PipelineOrchestrator(context)
            results = pipeline.process_complete_module_pipeline()
            logger.info("Processed %d modules", results['summary']['total_modules'])
        """
        logger.info("=" * 70)
        logger.info("[PIPELINE] Starting Complete Module-Level Processing")
        logger.info("=" * 70)

        with LLMClient.pipeline_run_guard(self._run_id, request_id=self.context.request_id):
            # Get filtered modules
            all_modules = self._filter_modules(module_manifest)

            # Create shared extractor once
            shared_extractor = SpecExtractor(
                project_root=str(self.context.project_dir),
                config_path=str(self.context.config_path),
                prompt_dir=str(self.context.prompt_dir),
                source_dir=str(self.context.source_dir),
                global_enriched_path=str(self.context.global_dir / "artifacts_enriched.json"),
                api_key=self.context.llm_api_key,
            )

            # Initialize tracking variables
            module_results = {}
            all_features = {}
            total_mfus = 0
            total_features = 0
            approved_features = 0
            failed_modules = []

            # =========================================================================
            # MAIN LOOP: Process each module through all stages
            # =========================================================================
            for module_data in all_modules:
                result = self.process_single_module(
                    module_data=module_data,
                    artifacts_enriched=artifacts_enriched,
                    module_manifest=module_manifest,
                    shared_extractor=shared_extractor,
                )

                module_id = result["module_id"]
                module_results[module_id] = result

                # Track statistics
                if result["status"] == "success":
                    total_mfus += result.get("mfu_count", 0)

                    feature_data = result["feature_derivation"]
                    all_features[module_id] = feature_data["results"]
                    total_features += feature_data["total"]
                    approved_features += feature_data["approved"]
                elif result["status"] == "cancelled":
                    # process_single_module's own step-boundary checks already
                    # confirmed cancellation — stop iterating instead of paying
                    # for a doomed LLM call on every remaining module (each
                    # would immediately cancel anyway, per LLMClient's own
                    # pre-call check, but not for free).
                    logger.info(
                        "[PIPELINE] Cancellation detected at module_id=%s — stopping "
                        "before %d remaining module(s).",
                        module_id,
                        len(all_modules) - len(module_results),
                    )
                    break
                else:
                    failed_modules.append(module_id)

            # =========================================================================
            # Compile Summary
            # =========================================================================
            successful_count = len(all_modules) - len(failed_modules)

            summary = {
                "total_modules": len(all_modules),
                "successful_modules": successful_count,
                "failed_modules": len(failed_modules),
                "failed_module_ids": failed_modules,
                "module_ids_processed": [m.get("module_id") for m in all_modules],
                "total_mfus_generated": total_mfus,
                "total_features": total_features,
                "approved_features": approved_features,
                "feature_approval_rate": (
                    f"{(approved_features / total_features * 100):.1f}%"
                    if total_features > 0
                    else "0%"
                ),
            }

            logger.info("=" * 70)
            logger.info("[PIPELINE] ✓ Complete Module-Level Processing Finished")
            logger.info("=" * 70)
            logger.info("  Modules:        %d/%d successful", successful_count, len(all_modules))
            if failed_modules:
                logger.info("  Failed:         %s", ", ".join(failed_modules))
            logger.info("  MFUs:           %d", total_mfus)
            logger.info(
                "  Features:       %d/%d (%s)",
                approved_features,
                total_features,
                summary["feature_approval_rate"],
            )
            logger.info("=" * 70)

            # Refresh module_manifest from project _global after all module processing.
            module_manifest_result = self.get_module_manifest()

            return {
                "module_results": module_results,
                "all_features": all_features,
                "summary": summary,
                "module_manifest": module_manifest_result.get("module_manifest", {}),
            }

    def get_module_manifest(self) -> dict[str, Any]:
        """Return module_manifest.json from the active project _global directory."""
        if self.context.skip_processing:
            logger.info(
                "[PIPELINE] skip_processing=True — loading module manifest from the same global_artifacts sample folder"
            )
            # Resolve via global_artifacts_result.json first so module_manifest.json is
            # guaranteed to come from the same provider/paradigm folder.
            global_sample = self._get_sample_path(
                "global_artifacts_result.json",
                subdir="global_artifacts",
            )
            module_manifest_path = global_sample.with_name("module_manifest.json")
            if not module_manifest_path.exists():
                raise FileNotFoundError(
                    "module_manifest.json not found beside global_artifacts_result.json: "
                    f"{module_manifest_path}"
                )
            return {"module_manifest": json.loads(module_manifest_path.read_text(encoding="utf-8"))}

        logger.info("[PIPELINE] Loading module_manifest.json from project _global directory")
        return self.service.get_module_manifest()

    def _is_cancelled(self) -> bool:
        """Cooperative-cancellation check (see app/core/task_control.py).

        No-ops (returns False) when no request_id is set on the context —
        e.g. CLI/local runs outside the Celery worker have nothing to check.
        """
        if not self.context.request_id:
            return False
        from app.core import task_control  # noqa: PLC0415

        return task_control.is_request_cancelled(self.context.request_id)

    def process_single_module(
        self,
        module_data: dict[str, Any],
        artifacts_enriched: dict[str, Any],
        module_manifest: dict[str, Any],
        shared_extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        Process a single module through all three stages:
        1. Spec Generation
        2. Name Refinement
        3. Feature Derivation

        Args:
            module_data: Module metadata
            artifacts_enriched: Global enriched artifacts
            module_manifest: Module manifest
            shared_extractor: Shared SpecExtractor instance

        Returns:
            Dict with module processing results or error info
        """
        with LLMClient.pipeline_run_guard(self._run_id, request_id=self.context.request_id):
            if shared_extractor is None:
                shared_extractor = SpecExtractor(
                    project_root=str(self.context.project_dir),
                    config_path=str(self.context.config_path),
                    prompt_dir=str(self.context.prompt_dir),
                    source_dir=str(self.context.source_dir),
                    global_enriched_path=str(self.context.global_dir / "artifacts_enriched.json"),
                    api_key=self.context.llm_api_key,
                )

            # Get skip configuration for stage 2.7
            skip_classes = set(
                self.project_config.get("module_pipeline", {}).get(
                    "skip_classes", ["infrastructure"]
                )
            )

            module_id = module_data.get("module_id", "UNKNOWN")
            logger.info("=" * 70)
            logger.info("[PIPELINE] Processing Module: %s", module_id)
            logger.info("=" * 70)

            if self.context.skip_processing:
                logger.info(
                    "[%s] skip_processing=True — loading module result from sample_results",
                    module_id,
                )
                return self._load_module_sample(module_id)

            try:
                # -----------------------------------------------------------------
                # Step 1: Spec Generation (slice → semantic → MFU → review → specs)
                # -----------------------------------------------------------------
                logger.info("[%s] Step 1/3: Spec Generation...", module_id)
                spec_result = self._process_module_for_spec_generation(
                    module_data, artifacts_enriched, shared_extractor
                )
                # spec_result = self._load_spec_result_from_stage4(module_id)     # For building sample jsons with pre-generated stage4 results

                mfu_count = len(spec_result.get("mfus_final", {}).get("mfus", []))
                logger.info(
                    "[%s] ✓ Spec Generation Complete: %d MFUs generated", module_id, mfu_count
                )

                if self._is_cancelled():
                    logger.info(
                        "[%s] Cancelled after Step 1/3 — skipping Name Refinement/Feature Derivation.",
                        module_id,
                    )
                    return {"module_id": module_id, "status": "cancelled"}

                # -----------------------------------------------------------------
                # Step 2: Name Refinement (Stage 2.7)
                # -----------------------------------------------------------------
                logger.info("[%s] Step 2/3: Semantic Name Refinement...", module_id)

                # Collect all processed module IDs so far
                modules_dir = self.service.modules_root
                processed_ids = [
                    p.name
                    for p in modules_dir.iterdir()
                    if p.is_dir() and (p / "stage3_ai" / "mfus_final.json").exists()
                ]

                try:
                    refined_result = self.service.refine_module_names(
                        module_manifest,
                        artifacts_enriched,
                        processed_ids,
                        shared_extractor,
                        skip_classes,
                    )

                    refined_manifest = refined_result.get("module_manifest", {})
                    logger.info("[%s] ✓ Name Refinement Complete", module_id)

                except Exception as e:
                    logger.warning("[%s] ⚠ Name Refinement Failed: %s", module_id, e)
                    refined_manifest = module_manifest

                if self._is_cancelled():
                    logger.info(
                        "[%s] Cancelled after Step 2/3 — skipping Feature Derivation.", module_id
                    )
                    return {"module_id": module_id, "status": "cancelled"}

                # -----------------------------------------------------------------
                # Step 3: Feature Derivation (Stage 5)
                # -----------------------------------------------------------------
                logger.info("[%s] Step 3/3: Feature & User Story Derivation...", module_id)

                try:
                    feature_result = self._process_module_for_feature_derivation(module_data)
                    # feature_result = self._load_feature_results_from_stage5(module_id)    # For building sample jsons with pre-generated stage5 results

                    logger.info(
                        "[%s] ✓ Feature Derivation Complete: %d/%d approved",
                        module_id,
                        feature_result["approved"],
                        feature_result["total"],
                    )

                except Exception as e:
                    logger.warning("[%s] ⚠ Feature Derivation Failed: %s", module_id, e)
                    feature_result = {
                        "module_id": module_id,
                        "results": [],
                        "total": 0,
                        "approved": 0,
                        "error": str(e),
                    }

                logger.info("[%s] ✓✓✓ Module Processing Complete ✓✓✓", module_id)

                result = {
                    "status": "success",
                    "module_id": module_id,
                    "spec_generation": {
                        "grouped_specs": spec_result.get("grouped_specs", {}),
                    },
                    "feature_derivation": feature_result,
                }

                return result

            except PipelineRunCancelled as e:
                # Raised by the LLM client before it issues a call (or a
                # retry) on a cancelled run — that check is what bounds
                # cancellation latency at a single in-flight call, since a
                # module's runtime is almost entirely LLM time.
                #
                # It is a BaseException on purpose, so it sails past the
                # per-stage `except Exception` handlers above instead of
                # being degraded into a partial result. This is the only
                # place that stops it: letting it out of here would bypass
                # the worker's cancellation bookkeeping entirely. Converted
                # to the return-value contract the worker already handles
                # (_process_single_module_task turns it into a chain-stopping
                # TaskCancelledError).
                logger.info("[%s] Cancelled mid-module (%s) — discarding output.", module_id, e)
                return {"module_id": module_id, "status": "cancelled"}

            except Exception as e:
                logger.error("[%s] ✗✗✗ Module Processing Failed: %s ✗✗✗", module_id, e)
                return {"module_id": module_id, "error": str(e), "status": "failed"}

    # -------------------------------------------------------------------------
    # Sample Data Loader
    # -------------------------------------------------------------------------

    def _get_active_provider(self) -> str:
        """Resolve configured LLM provider for provider-scoped sample loading."""
        provider = (
            str(self.project_config.get("llm", {}).get("active_provider", "deepseek"))
            .strip()
            .lower()
        )
        return provider or "deepseek"

    def _load_sample(
        self,
        filename: str,
        subdir: str | None = None,
    ) -> dict[str, Any]:
        """Load sample JSON from provider/paradigm folders, with graceful provider fallback."""
        sample_path = self._get_sample_path(filename=filename, subdir=subdir)
        with open(sample_path, encoding="utf-8") as f:
            return json.load(f)

    def _get_sample_path(self, filename: str, subdir: str | None = None) -> Path:
        """Resolve a sample file path with provider/paradigm fallback."""
        sample_root = Path(__file__).parent / "sample_results"
        configured_provider = self._get_active_provider()
        source_paradigm = (
            str(self.project_config.get("project", {}).get("source_paradigm", "pb")).strip().lower()
            or "pb"
        )

        def build_path(provider_name: str) -> Path:
            base = sample_root / provider_name / source_paradigm
            if subdir:
                return base / subdir / filename
            return base / filename

        # First try configured provider.
        provider = configured_provider
        sample_path = build_path(provider)

        # If configured provider doesn't have this sample, gracefully fall back
        # to another available provider sample (prefer deepseek when present).
        if not sample_path.exists():
            provider_dirs = [p.name for p in sample_root.iterdir() if p.is_dir()]
            ordered_providers = sorted(
                provider_dirs,
                key=lambda name: (name != "deepseek", name),
            )

            fallback_provider = next(
                (name for name in ordered_providers if build_path(name).exists()),
                None,
            )

            if not fallback_provider:
                attempted = build_path(configured_provider)
                raise FileNotFoundError(
                    "No provider-scoped sample file found. "
                    f"Configured provider='{configured_provider}', source_paradigm='{source_paradigm}', expected='{attempted}', "
                    f"available_providers={ordered_providers}"
                )

            provider = fallback_provider
            sample_path = build_path(provider)
            logger.warning(
                "[PIPELINE] Sample not found for provider=%s. Falling back to provider=%s",
                configured_provider,
                provider,
            )

        logger.info(
            "[PIPELINE] Loading sample (provider=%s, paradigm=%s): %s",
            provider,
            source_paradigm,
            sample_path,
        )
        return sample_path

    def _load_module_sample(self, module_id: str) -> dict[str, Any]:
        """Load per-module sample result for skip mode, scoped by provider."""
        safe_module_id = (module_id or "UNKNOWN").strip()
        return self._load_sample(
            filename=f"{safe_module_id}_result.json",
            subdir="module_spec_stories",
        )

    def _load_revise_sample(
        self,
        module_id: str | None = None,
        mfu_id: str | None = None,
        mode: str | None = None,
    ) -> dict[str, Any]:
        """Load skip-mode revise result and align request identifiers when provided."""
        sample = self._load_sample(
            filename="revise_result.json",
            subdir="revise",
        )
        if isinstance(sample, dict):
            if module_id:
                sample["module_id"] = module_id
            if mfu_id:
                sample["mfu_id"] = mfu_id
            if mode:
                sample["mode"] = mode
        return sample

    def _load_spec_result_from_stage4(self, module_id: str) -> dict[str, Any]:
        """Load precomputed spec artifacts from stage4_specs for a module."""
        safe_module_id = (module_id or "UNKNOWN").strip()
        module_root = self.service.modules_root / safe_module_id
        stage4_specs_dir = module_root / "stage4_specs"
        stage3_mfus_path = module_root / "stage3_ai" / "mfus_final.json"

        grouped_specs = self.service._load_grouped_specs(
            safe_module_id,
            str(stage4_specs_dir),
        )

        mfus_final: dict[str, Any] = {}
        if stage3_mfus_path.exists():
            mfus_final = json.loads(stage3_mfus_path.read_text(encoding="utf-8"))

        logger.info(
            "[PIPELINE] Loaded grouped specs for module=%s from %s",
            safe_module_id,
            stage4_specs_dir,
        )

        return {
            "module_id": safe_module_id,
            "grouped_specs": grouped_specs,
            "mfus_final": mfus_final,
        }

    def _load_feature_results_from_stage5(self, module_id: str) -> dict[str, Any]:
        """Load module feature results from all Stage 4 feature-unit folders."""
        safe_module_id = (module_id or "UNKNOWN").strip()
        module_root = self.service.modules_root / safe_module_id
        stage4_specs_dir = module_root / "stage4_specs"

        # Support any feature-unit folder name under stage4_specs, not only MFU-*.
        candidate_paths = list(stage4_specs_dir.glob("*/features_stories.json"))
        candidate_paths.extend(stage4_specs_dir.glob("*/stage5_backlog/features_stories.json"))
        feature_files = sorted({p.resolve() for p in candidate_paths})

        if not feature_files:
            raise FileNotFoundError(
                "No feature story files found for module "
                f"'{safe_module_id}' under {stage4_specs_dir}"
            )

        results: list[dict[str, Any]] = []
        for feature_file in feature_files:
            try:
                payload = json.loads(feature_file.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    results.append(payload)
                else:
                    logger.warning(
                        "[PIPELINE] Skipping non-dict feature payload: %s",
                        feature_file,
                    )
            except Exception as exc:
                logger.warning(
                    "[PIPELINE] Failed to read feature file %s: %s",
                    feature_file,
                    exc,
                )

        approved = sum(
            1
            for item in results
            if item.get("generation_metadata", {}).get("final_status") == "PASS"
        )

        logger.info(
            "[PIPELINE] Loaded feature results for module=%s: %d file(s), approved=%d",
            safe_module_id,
            len(results),
            approved,
        )

        return {
            "module_id": safe_module_id,
            "results": results,
            "total": len(results),
            "approved": approved,
        }

    def _load_global_artifacts_from_project(self) -> dict[str, Any]:
        """Load global artifacts from the current project's _global directory."""
        global_dir = self.context.global_dir
        artifacts_enriched_path = global_dir / "artifacts_enriched.json"
        module_manifest_path = global_dir / "module_manifest.json"

        missing_paths = [
            p for p in (artifacts_enriched_path, module_manifest_path) if not p.exists()
        ]
        if missing_paths:
            missing = ", ".join(str(p) for p in missing_paths)
            raise FileNotFoundError(
                "Required global artifact file(s) not found in project _global directory: "
                f"{missing}"
            )

        artifacts_enriched = json.loads(artifacts_enriched_path.read_text(encoding="utf-8"))
        module_manifest = json.loads(module_manifest_path.read_text(encoding="utf-8"))
        filtered_modules = self._filter_modules(module_manifest)

        logger.info("[PIPELINE] Loaded project global artifacts from: %s", global_dir)
        return {
            "artifacts_enriched": artifacts_enriched,
            "module_manifest": module_manifest,
            "filtered_modules": filtered_modules,
        }

    # -------------------------------------------------------------------------
    # Module Filtering
    # -------------------------------------------------------------------------

    def _filter_modules(self, module_manifest: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Filter modules based on configuration and context.

        Args:
            module_manifest: Module manifest containing all modules

        Returns:
            List of filtered module dictionaries
        """
        modules = module_manifest.get("modules", [])
        pipeline = self.project_config.get("module_pipeline", {})
        skip = set(pipeline.get("skip_classes", ["infrastructure"]))
        overrides = pipeline.get("class_overrides", {})

        # Apply module filter if specified
        if self.context.module_filter:
            upper = {m.upper() for m in self.context.module_filter}
            modules = [m for m in modules if m.get("module_id", "").upper() in upper]
            logger.info("[PIPELINE] Module filter applied: %d module(s).", len(modules))

        # Filter by class
        filtered = []
        for m in modules:
            mid = m.get("module_id", "")
            if mid in overrides and overrides[mid] != "_auto":
                cls = overrides[mid]
                source = "config_override"
            elif m.get("module_class"):
                cls = m["module_class"]
                source = "manifest"
            elif m.get("is_shared") is True:
                cls = "infrastructure"
                source = "is_shared"
            else:
                cls = "business"
                source = "default"

            if cls in skip:
                logger.info(
                    "[PIPELINE][SKIP] %s — class=%s (source=%s)",
                    mid,
                    cls,
                    source,
                )
            else:
                filtered.append(m)

        logger.info("[PIPELINE] %d module(s) queued for processing.", len(filtered))
        return filtered

    # -------------------------------------------------------------------------
    # Stage 1: Global Artifacts (1A → 2.5)
    # -------------------------------------------------------------------------

    def _run_stage_1a(self) -> dict[str, Any]:
        """
        Stage 1a: Build global index from source files.

        Returns:
            Dict containing global_index
        """
        logger.info("[PIPELINE] ===== Stage 1a: Global Index =====")
        result = self.service.build_global_index()
        return {"global_index": result["global_index"]}

    def _run_stage_1b(self) -> dict[str, Any]:
        """
        Stage 1b: Execute polyglot scan to detect artifacts.

        Returns:
            Dict containing artifacts_detected
        """
        logger.info("[PIPELINE] ===== Stage 1b: Polyglot Scan =====")
        result = self.service.execute_global_scan()
        return {"artifacts_detected": result["artifacts_detected"]}

    def _run_stage_2(self, artifacts_detected: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        Stage 2: Enrich artifacts with call graph analysis.

        Args:
            artifacts_detected: Optional pre-loaded artifacts. If None, loads from file.

        Returns:
            Dict containing artifacts_enriched
        """
        logger.info("[PIPELINE] ===== Stage 2: Call Graph Enrichment =====")

        result = self.service.enrich_with_call_graph(artifacts_detected)
        return {"artifacts_enriched": result["artifacts_enriched"]}

    def _run_stage_2_5(
        self,
        global_index: dict[str, Any] | None = None,
        artifacts_enriched: dict[str, Any] | None = None,
        module_derivation: str = "auto",
    ) -> dict[str, Any]:
        """
        Stage 2.5: AI-powered module discovery and classification.

        Args:
            global_index: Optional pre-loaded global index
            artifacts_enriched: Optional pre-loaded enriched artifacts

        Returns:
            Dict containing module_manifest and budget_report
        """
        logger.info("[PIPELINE] ===== Stage 2.5: AI Module Discovery =====")

        result = self.service.discover_modules(
            global_index,
            artifacts_enriched,
            module_derivation=module_derivation,
        )

        return {
            "module_manifest": result["module_manifest"],
            "budget_report": result["budget_report"],
        }

    # -------------------------------------------------------------------------
    # Stage 3-4: Per-Module Processing
    # -------------------------------------------------------------------------

    def _process_module_for_spec_generation(
        self,
        module_data: dict[str, Any],
        artifacts_enriched: dict[str, Any],
        shared_extractor: SpecExtractor | None = None,
    ) -> dict[str, Any]:
        """
        Process a single module through the complete pipeline:
        slice → semantic → MFU gen → review → specs

        Args:
            module_data: Module metadata dict from module_manifest
            artifacts_enriched: Global artifacts_enriched dict
            shared_extractor: Optional shared SpecExtractor instance

        Returns:
            Dict with all module results including specs, MFUs, and reports
        """
        module_id = module_data.get("module_id", "UNKNOWN")
        logger.info("[PIPELINE] ===== Processing Module: %s =====", module_id)

        # Setup module directories
        module_root = self.service.modules_root / module_id

        directories = {
            "scan": module_root / "stage1_scanner",
            "graph": module_root / "stage2_graph",
            "semantic": module_root / "stage2b_semantic",
            "ai": module_root / "stage3_ai",
            "compare": module_root / "stage4_compare",
            "spec": module_root / "stage4_specs",
        }

        for dir_path in directories.values():
            dir_path.mkdir(parents=True, exist_ok=True)

        # Slice enriched artifacts for this module
        module_enriched_result = self.service.slice_enriched_for_module(
            artifacts_enriched, module_data, directories["graph"]
        )
        module_enriched = module_enriched_result.get("artifacts_enriched", {})

        # Slice detected artifacts
        module_detected_result = self.service.slice_detected_for_module(
            module_enriched, directories["scan"]
        )
        module_detected = module_detected_result.get("artifacts_detected", {})

        # Semantic enrichment
        semantic_result = self.service.enrich_semantics(
            module_enriched, directories["graph"], directories["semantic"]
        )
        semantic = semantic_result.get("artifacts_semantic", {})

        # Generate MFUs
        semantic_ai_input = semantic_result.get("artifacts_semantic", module_enriched)
        mfus_proposed_result = self.service.generate_mfus(semantic_ai_input, directories["ai"])
        mfus_proposed = mfus_proposed_result.get("mfus_proposed", {})

        # Sweep orphans
        mfus_proposed_swept_result = self.service.sweep_orphans(mfus_proposed, semantic)
        mfus_proposed_swept = mfus_proposed_swept_result.get("mfus_proposed", {})

        added_count = len(mfus_proposed_swept.get("mfus", [])) - len(mfus_proposed.get("mfus", []))
        if added_count > 0:
            logger.info("[ORPHAN] Added %d mechanical buckets for coverage.", added_count)
        else:
            logger.info("[ORPHAN] No orphans found (100%% coverage).")

        # Review MFUs
        mfus_final_result = self.service.review_mfus(
            mfus_proposed, module_enriched, directories["ai"], directories["graph"]
        )
        mfus_final = mfus_final_result.get("mfus_final", {})

        # Validate guardrails
        guardrail_result = self.service.validate_guardrails(mfus_final, semantic)
        guardrail_report = guardrail_result.get("guardrail_report", {})

        # Validate against schema
        try:
            schema_path = self.context.schema_dir / "guardrail_report.schema.json"
            validator = JSONSchemaValidator(schema_path, registry=PluginRegistry())
            validator.validate_data(guardrail_report)
            logger.info("[GUARDRAIL] Coverage validation: PASS")
        except Exception as e:
            logger.warning("[WARN] Guardrail check failed: %s", e)

        # Compare proposed vs final
        compare_result = self.service.compare_mfu_proposed_vs_final(
            mfus_proposed, mfus_final, directories["ai"], directories["compare"]
        )
        mfu_comparison_report = compare_result.get("mfu_comparison_report", {})

        # Extract specifications
        specs_result = self.service.extract_specs(
            module_id,
            directories["spec"],
            artifacts_enriched,
            shared_extractor,
            request_id=self.context.request_id,
        )
        grouped_specs = specs_result.get("grouped_specs", {})

        logger.info("[PIPELINE] ✓ %s — %d MFUs", module_id, len(mfus_final.get("mfus", [])))

        return {
            "module_id": module_id,
            "module_enriched": module_enriched,
            "module_detected": module_detected,
            "semantic": semantic,
            "mfus_proposed": mfus_proposed,
            "mfus_proposed_swept": mfus_proposed_swept,
            "mfus_final": mfus_final,
            "guardrail_report": guardrail_report,
            "mfu_comparison_report": mfu_comparison_report,
            "grouped_specs": grouped_specs,
        }

    # -------------------------------------------------------------------------
    # Stage 2.7: Semantic Name Refinement (deprecated - now in process_single_module)
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # Stage 5: Feature & User Story Derivation
    # -------------------------------------------------------------------------

    def _process_module_for_feature_derivation(self, module_data: dict[str, Any]) -> dict[str, Any]:
        """
        Process a single module for feature & user story derivation.

        Args:
            module_data: Module metadata dict from module_manifest

        Returns:
            Dict with feature results and metadata
        """
        mid = module_data.get("module_id", "UNKNOWN")

        # Load grouped specs from the module's stage4_specs directory
        spec_dir = self.service.modules_root / mid / "stage4_specs"
        grouped_specs = self.service._load_grouped_specs(mid, str(spec_dir))

        results = self.service.derive_features(
            mid, grouped_specs, request_id=self.context.request_id
        )

        approved_count = sum(
            1
            for r in results
            if r and r.get("generation_metadata", {}).get("final_status") == "PASS"
        )

        return {
            "module_id": mid,
            "results": results,
            "total": len(results),
            "approved": approved_count,
        }

    # -------------------------------------------------------------------------
    # Neo4j Graph Generation
    # -------------------------------------------------------------------------

    def run_neo4j_generation(self, artifacts_enriched: dict[str, Any]) -> dict[str, Any]:
        """
        Generate Neo4j graph representations.

        Args:
            artifacts_enriched: Global enriched artifacts

        Returns:
            Dict containing neo4j graph data
        """
        logger.info("[PIPELINE] ===== Neo4j Graph Generation =====")

        neo4j_data = self.service.build_neo4j_graphs(artifacts_enriched)

        return {"neo4j_graphs": neo4j_data}

    # -------------------------------------------------------------------------
    # Stage 6: Executive Report Generation
    # -------------------------------------------------------------------------

    def run_report_generation(self) -> dict[str, Any]:
        """
        Generate executive RIP reports.

        Aggregates results from all pipeline stages and exports comprehensive
        reports in multiple formats (JSON, Excel, HTML, etc.).

        Returns:
            Dict containing report generation status and metadata
        """
        logger.info("[PIPELINE] ===== Stage 6: Executive Report Generation =====")

        report_result = self.service.generate_reports()
        catalog_report = report_result.get("reports", {}).get("catalog_report", {})
        landmine_report = report_result.get("reports", {}).get("landmine_report", {})
        manifest_report = report_result.get("reports", {}).get("manifest_report", {})

        return report_result

    # -------------------------------------------------------------------------
    # Revise: Single MFU Regeneration/Refinement
    # -------------------------------------------------------------------------

    def group_feedbacks_feature_wise(
        self,
        basket_feedback_request: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Validate and group stacked feedback into feature-wise buckets.

        Input schema:
            {
              "feedbacks": [
                {
                  "target": {
                    "project_id": "...",
                    "module_id": "... | null",
                    "mfu_id": "... | null",
                    "user_story_id": "... | null"
                  },
                  "overall_feedback": {"content": "..."},
                  "specific_feedback": [
                    {"selected_text": "...", "content": "..."}
                  ]
                }
              ]
            }

                Example request (aligned to features_stories.json IDs):
                        {
                            "feedbacks": [
                                {
                                    "target": {
                                        "project_id": "d77241ec-8cf2-49b5-86c0-b4ed98397483",
                                        "module_id": "MOD-ANCES",
                                        "mfu_id": "MFU-001",
                                        "user_story_id": null
                                    },
                                    "overall_feedback": {
                                        "content": "Clarify feature wording for user-visible behavior."
                                    },
                                    "specific_feedback": [
                                        {
                                            "selected_text": "Ancestor DataWindow Common Operations",
                                            "content": "Keep wording user-centered and avoid implementation jargon."
                                        }
                                    ]
                                },
                                {
                                    "target": {
                                        "project_id": "d77241ec-8cf2-49b5-86c0-b4ed98397483",
                                        "module_id": "MOD-ANCES",
                                        "mfu_id": "MFU-001",
                                        "user_story_id": "ANCES-931-F1-S2"
                                    },
                                    "overall_feedback": {
                                        "content": "Mention exact user action and shortcut wording where applicable."
                                    },
                                    "specific_feedback": [
                                        {
                                            "selected_text": "Perform Clipboard Operations via Right-click Context Menu",
                                            "content": "State exact copy action and expected user-visible result."
                                        }
                                    ]
                                }
                            ]
                        }

        Output schema:
            {
              "features": [
                {
                  "project_id": "...",
                  "module_id": "...",
                  "mfu_id": "...",
                  "feedbacks": [
                    {
                      "scope": "FEATURE" | "USER_STORY",
                      "user_story_id": "... | null",
                      "overall_feedback": {"content": "..."},
                      "specific_feedback": [
                        {"selected_text": "...", "content": "..."}
                      ]
                    }
                  ]
                }
              ]
            }
        """
        return FeedbackGroupingService.group_feedbacks_feature_wise(basket_feedback_request)

    @staticmethod
    def compose_revise_feedback_from_feature_bucket(
        feature_bucket: dict[str, Any],
    ) -> str:
        """Flatten a grouped feature feedback bucket into a revise-ready feedback string."""
        return FeedbackGroupingService.compose_revise_feedback_from_feature_bucket(feature_bucket)

    @staticmethod
    def compose_revise_feedback_spec_from_feature_bucket(
        feature_bucket: dict[str, Any],
    ) -> dict[str, Any]:
        """Build structured revise feedback_spec payload for cmd_revise path."""
        return FeedbackGroupingService.compose_revise_feedback_spec_from_feature_bucket(
            feature_bucket
        )

    def process_single_mfu_revise(
        self,
        module_id: str,
        mfu_id: str,
        feedback: str,
        mode: str = "edit",
        quoted_text: str | None = None,
        target_ids: list[str] | None = None,
        feedback_spec: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Execute revise flow for a single MFU.

        Exposed as a first-class orchestrator API similar to global/module methods,
        intended for backend route/service integration.
        """
        logger.info("[PIPELINE] ===== Revise Single MFU =====")
        logger.info("[PIPELINE] module_id=%s mfu_id=%s mode=%s", module_id, mfu_id, mode)

        if self.context.skip_processing:
            logger.info(
                "[PIPELINE] skip_processing=True — loading revise result from sample_results"
            )
            return self._load_revise_sample(
                module_id=module_id,
                mfu_id=mfu_id,
                mode=mode,
            )

        if not module_id or not mfu_id or not str(feedback or "").strip():
            return {
                "status": "failed",
                "module_id": module_id,
                "mfu_id": mfu_id,
                "mode": mode,
                "error": "module_id, mfu_id and feedback are required",
            }

        try:
            return self.service.revise_mfu(
                module_id=module_id,
                mfu_id=mfu_id,
                feedback=feedback,
                mode=mode,
                quoted_text=quoted_text,
                target_ids=target_ids,
                feedback_spec=feedback_spec,
            )
        except Exception as exc:
            logger.error(
                "[PIPELINE] Revise failed for module=%s mfu=%s mode=%s: %s",
                module_id,
                mfu_id,
                mode,
                exc,
            )
            return {
                "status": "failed",
                "module_id": module_id,
                "mfu_id": mfu_id,
                "mode": mode,
                "error": str(exc),
            }

    def revise_module_mfu(
        self,
        module_id: str,
        mfu_id: str,
        feedback: str,
        mode: str = "edit",
        quoted_text: str | None = None,
        target_ids: list[str] | None = None,
        feedback_spec: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Backward-compatible alias for single-MFU revise entrypoint.
        """
        return self.process_single_mfu_revise(
            module_id=module_id,
            mfu_id=mfu_id,
            feedback=feedback,
            mode=mode,
            quoted_text=quoted_text,
            target_ids=target_ids,
            feedback_spec=feedback_spec,
        )

    def process_mfu_revise_with_reconstruction(
        self,
        revise_input: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Execute revise for a single MFU with full workspace reconstruction.

        This is the PRIMARY revise entry point for backend API integration.
        Reconstructs the normal project structure in the backend-controlled project
        directory, executes Stage 5 revise, and returns results.

        Args:
            revise_input: Dict with:
                - module_id: str (required)
                - mfu_id: str (required)
                - feedback_spec: optional structured payload for revise
                    {"module": str|None, "mfu": str|None,
                     "entries": [{"story_id": str|None,
                                  "items": [{"quoted_text": str|None, "feedback_text": str}]}]}
                - feedback: str (optional) — direct composed guidance
                - quoted_text: str (optional) — selected text reference context
                - target_ids: list[str] (optional) — targeted user_story ids for edit locking
                - mode: str (optional, default "edit")
                - srs_files: list of {"filename": str, "content": str} (required)
                - config_naming_map: dict (optional) — mfu metadata
                - module_manifest: dict (optional) — full backend module manifest
                - feature_stories: dict (optional) - full feature stories json
                - source_paradigm: str (optional, default "pb")

        Returns:
            Dict with:
                - status: "success" or "failed"
                - module_id, mfu_id, mode
                - final_status: from critic verdict (PASS, PASS_RELAXED_GATES, FAIL_*, etc.)
                - review_guidance: PM-facing recommendation (APPROVE_AS_IS, EDIT, REGENERATE, etc.)
                - result: full features_stories.json output dict
                - features_stories: nested features_stories content
                - error: if status is "failed"
        """
        logger.info("[PIPELINE] ===== Revise MFU with Reconstruction =====")
        logger.info(
            "[PIPELINE] module_id=%s mfu_id=%s mode=%s",
            revise_input.get("module_id"),
            revise_input.get("mfu_id"),
            revise_input.get("mode", "edit"),
        )

        with LLMClient.pipeline_run_guard(self._run_id, request_id=self.context.request_id):
            if self.context.skip_processing:
                logger.info(
                    "[PIPELINE] skip_processing=True — loading revise result from sample_results"
                )
                return self._load_revise_sample(
                    module_id=revise_input.get("module_id")
                    if isinstance(revise_input, dict)
                    else None,
                    mfu_id=revise_input.get("mfu_id") if isinstance(revise_input, dict) else None,
                    mode=(
                        revise_input.get("mode", "edit")
                        if isinstance(revise_input, dict)
                        else "edit"
                    ),
                )

            try:
                # Use service revise_mfu_with_reconstruction for ephemeral workspace handling
                result = self.service.revise_mfu_with_reconstruction(revise_input)

                if result.get("status") == "success":
                    logger.info(
                        "[PIPELINE] ✓ Revise succeeded: %s/%s -> %s",
                        result.get("module_id"),
                        result.get("mfu_id"),
                        result.get("final_status"),
                    )
                else:
                    logger.error(
                        "[PIPELINE] ✗ Revise failed: %s/%s — %s",
                        result.get("module_id"),
                        result.get("mfu_id"),
                        result.get("error"),
                    )

                return result

            except Exception as exc:
                logger.error(
                    "[PIPELINE] Revise with reconstruction failed: %s",
                    exc,
                )
                return {
                    "status": "failed",
                    "module_id": revise_input.get("module_id"),
                    "mfu_id": revise_input.get("mfu_id"),
                    "mode": revise_input.get("mode", "edit"),
                    "error": str(exc),
                }


if __name__ == "__main__":
    load_dotenv(Path(__file__).resolve().parents[3] / ".env")
    project_dir = Path("app/services/source_code_pipeline/projects/sample_project")
    source_dir = project_dir / "input/pb_src"

    # Demo backend field (single input): "flat" | "modular" | "auto".
    source_layout_type = "modular"
    demo_options = {
        "llm_provider": "deepseek",
        "llm_model": "deepseek-v4-pro",
        # Runtime-only credential; never written to project_config.json.
        # "llm_api_key": os.getenv("DEEPSEEK_API_KEY"),
        "llm_api_key": "",


    }

    config_path = PipelineOrchestrator.configure_project(
        project_dir=project_dir,
        source_paradigm="pb",
        options=demo_options,
        source_layout_type=source_layout_type,
    )

    context = PipelineContext(
        project_dir=project_dir,
        config_path=config_path,
        source_dir=source_dir,
        skip_processing=False,
        llm_api_key=demo_options.get("llm_api_key"),
    )

    pipeline = PipelineOrchestrator(context)
    logger.info("[PIPELINE] Requested source_dir: %s", source_dir)
    logger.info(
        "[PIPELINE] Effective source_dir after flatten preprocess: %s", pipeline.context.source_dir
    )

    # global_artifacts = pipeline.generate_global_artifacts()
    dependency_graph = pipeline.generate_code_dependency_graph()
    logger.info("Code dependency graph generated: %s", list(dependency_graph.keys()))

    output_path = "dependency_graph.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(dependency_graph, f, indent=2, ensure_ascii=False)
    logger.info("[PIPELINE] Saved dependency graph to: %s", output_path)

    global_artifacts = pipeline.discover_modules(dependency_graph)
    logger.info("Module discovery completed: %s", list(global_artifacts.keys()))

    # global_artifacts = pipeline._load_global_artifacts_from_project()   # For building sample jsons with pre-generated global artifacts from the project _global directory

    # global_artifacts = json.load(open("global_artifacts_result.json", "r", encoding="utf-8"))

    output_path = "global_artifacts_result.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(global_artifacts, f, indent=2, ensure_ascii=False)
    logger.info("[PIPELINE] Saved global artifacts result to: %s", output_path)

    artifacts_enriched = global_artifacts["artifacts_enriched"]
    module_manifest = global_artifacts["module_manifest"]
    filtered_modules = global_artifacts["filtered_modules"]
    logger.info("Filtered modules for processing: %d", len(filtered_modules))

    for module_data in filtered_modules:
        module_id = module_data.get("module_id", "UNKNOWN")
        if module_id != "MOD-IDO":
            continue
        result = pipeline.process_single_module(
            module_data=module_data,
            artifacts_enriched=artifacts_enriched,
            module_manifest=module_manifest,
        )
        logger.info("Module %s processed:", module_id)
        output_path = f"{module_id}_result.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        logger.info("[PIPELINE] Saved module result to: %s", output_path)

    domain_knowledge = pipeline.generate_domain_knowledge()
    domain_knowledge_path = project_dir / "_global" / "domain_knowledge_result.json"
    domain_knowledge_path.write_text(
        json.dumps(domain_knowledge, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Domain knowledge payload saved: %s", domain_knowledge_path)

    architecture_document = pipeline.generate_architecture_document()
    architecture_document_path = project_dir / "_global" / "architecture_document_result.json"
    architecture_document_path.write_text(
        json.dumps(architecture_document, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Architecture document payload saved: %s", architecture_document_path)

    manifest_result = pipeline.get_module_manifest()
    manifest_output_path = "module_manifest_after_generation.json"
    with open(manifest_output_path, "w", encoding="utf-8") as f:
        json.dump(manifest_result.get("module_manifest", {}), f, indent=2, ensure_ascii=False)
    logger.info("[PIPELINE] Saved post-generation module manifest to: %s", manifest_output_path)
