# pipeline_service.py
"""
Sourcecode Pipeline Service Layer
Stateless callable API. Each method accepts dicts, returns dicts.
No JSON persistence except where Stage 4/5 internally require disk paths.
Source codebase is read in Stages 1a, 1b, and 4 only.
"""

import json
from pathlib import Path
import shutil
from typing import Any

try:
    from app.utils.logger import get_logger

    logger = get_logger(__name__)
except ImportError:
    import logging

    logger = logging.getLogger(__name__)
from .src.ai.ai_runner import AIRunner
from .src.ai.guardrails import apply_guardrails as _apply_guardrails
from .src.ai.llm_client import LLMClient
from .src.cli.main import (
    _render_structured_feedback,
    cmd_revise as _cmd_revise,
    execute_global_scan as _execute_global_scan,
    set_project_paths as _set_project_paths,
)
from .src.enrichment.llm_enricher import LLMEnricher
from .src.post_process.orphan_sweeper import sweep_orphans as _sweep_orphans
from .src.review.review_agent import ReviewAgent
from .src.scanner.call_graph_builder import (
    enrich_with_call_graph as _enrich_with_call_graph,
    slice_enriched_for_module as _slice_enriched_for_module,
)
from .src.scanner.global_index_builder import build_global_index as _build_index
from .src.tools.mfu_comparator import compare as _compare
from .src.tools.mfu_exporter import export_comparison as _export_comparison


def _import(cls, paths):
    for p in paths:
        try:
            return getattr(__import__(p, fromlist=[cls]), cls)
        except (ImportError, AttributeError):
            pass
    return None


_SpecExtractor = _import("SpecExtractor", ["app.services.source_code_pipeline.spec_extractor"])
_build_hybrid_manifest = _import(
    "build_hybrid_manifest",
    ["app.services.source_code_pipeline.src.tools.hybrid_manifest_generator"],
)
_set_hybrid_runtime_api_key = _import(
    "set_runtime_api_key", ["app.services.source_code_pipeline.src.tools.hybrid_manifest_generator"]
)
_choose_module_engine = _import(
    "choose_engine", ["app.services.source_code_pipeline.src.tools.source_module_clustering"]
)
_derive_modules_from_source = _import(
    "derive_modules_from_source",
    ["app.services.source_code_pipeline.src.tools.source_module_clustering"],
)
_FeatureStoryAgent = _import(
    "FeatureStoryAgent", ["app.services.source_code_pipeline.src.tools.feature_story_agent"]
)
_Neo4jExporter = _import(
    "Neo4jExporter", ["app.services.source_code_pipeline.src.tools.neo4j_exporter"]
)
_ReportGenerator = _import(
    "ReportGenerator", ["app.services.source_code_pipeline.src.tools.report_generator"]
)
_generate_onboarding = _import(
    "generate_onboarding", ["app.services.source_code_pipeline.src.tools.ddd_onboarding"]
)
_derive_architecture = _import(
    "derive_architecture", ["app.services.source_code_pipeline.src.tools.architecture_synthesis"]
)

# Module-level constants for default paths
DEFAULT_SCHEMA_DIR = Path("schemas")
DEFAULT_PROMPT_DIR = Path("prompts")
DEFAULT_ARCHETYPE_DIR = Path("config")


class SourceCodePipelineService:
    """
    Service layer over the source code processing pipeline.
    Stateless, dict-in/dict-out API for pipeline operations.

    Args:
        project_dir: Root directory for the project
        source_dir: Optional source code directory (defaults to project_dir/input/pb_src)
        config_path: Optional config file path (defaults to project_dir/project_config.json)
        prompt_dir: Optional prompt directory (defaults to ./prompts)
        schema_dir: Optional schema directory (defaults to ./schemas)
        archetype_dir: Optional archetype directory (defaults to ./config)
    """

    def __init__(
        self,
        project_dir: str,
        source_dir: str = None,
        config_path: str = None,
        prompt_dir: str = None,
        schema_dir: str = None,
        archetype_dir: str = None,
        api_key: str | None = None,
    ):
        # Core directories
        self.project_dir = Path(project_dir).resolve()
        self.source_dir = (
            Path(source_dir).resolve()
            if source_dir
            else (self.project_dir / "input/pb_src").resolve()
        )
        self.modules_root = (self.project_dir / "modules").resolve()
        self.global_dir = (self.project_dir / "_global").resolve()
        self.output_dir = (self.project_dir / "output").resolve()

        # Configuration paths
        self.config_path = (
            Path(config_path).resolve()
            if config_path
            else (self.project_dir / "project_config.json")
        )
        self.prompt_dir = Path(prompt_dir).resolve() if prompt_dir else DEFAULT_PROMPT_DIR.resolve()
        self.schema_dir = Path(schema_dir).resolve() if schema_dir else DEFAULT_SCHEMA_DIR.resolve()
        self.archetype_dir = (
            Path(archetype_dir).resolve() if archetype_dir else DEFAULT_ARCHETYPE_DIR.resolve()
        )
        self.api_key = api_key
        if _set_hybrid_runtime_api_key:
            _set_hybrid_runtime_api_key(self.api_key)

        # Setup environment
        import os

        os.environ["RIP_LLM_CONFIG_PATH"] = str(self.config_path)

        # Load configuration
        self._config = json.loads(self.config_path.read_text(encoding="utf-8"))
        source_paradigm = (
            str(self._config.get("project", {}).get("source_paradigm", "")).strip().lower()
        )
        if source_paradigm:
            os.environ["RIP_SOURCE_PARADIGM"] = source_paradigm

        # Build path mappings
        self._schema_paths = self._build_schema_paths()
        self._prompt_paths = self._build_prompt_paths()
        self._archetype_paths = self._build_archetype_paths()

    def _build_schema_paths(self) -> dict:
        """Build schema file path mappings."""
        return {
            "proposed": self.schema_dir / "mfus_proposed.schema.json",
            "final": self.schema_dir / "mfus_final.schema.json",
            "semantic": self.schema_dir / "artifacts_semantic.schema.json",
        }

    def _build_prompt_paths(self) -> dict:
        """Build prompt file path mappings."""
        return {
            "mfu_sys": self.prompt_dir / "mfu_system_prompt.txt",
            "mfu_gen": self.prompt_dir / "mfu_generation_prompt.txt",
            "review_sys": self.prompt_dir / "review_agent_system_prompt.txt",
            "enricher_sys": self.prompt_dir / "llm_enricher_system_prompt.txt",
            "enricher_gen": self.prompt_dir / "llm_enricher_generation_prompt.txt",
        }

    def _build_archetype_paths(self) -> dict:
        """Build archetype file path mappings."""
        return {
            "archetype_mapping": self.archetype_dir / "archetype_mapping.json",
        }

    def _write_json(self, directory: Path, filename: str, data: dict) -> Path:
        """Write JSON data to a file in the specified directory."""
        directory.mkdir(parents=True, exist_ok=True)
        file_path = directory / filename
        file_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return file_path

    def _write_file(self, directory: Path, filename: str, content: str) -> Path:
        """Write text content to a file in the specified directory."""
        directory.mkdir(parents=True, exist_ok=True)
        file_path = directory / filename
        file_path.write_text(content, encoding="utf-8")
        return file_path

    def _read_json(self, file_path: Path) -> dict:
        """Read JSON data from a file."""
        return json.loads(file_path.read_text(encoding="utf-8"))

    def _load_grouped_specs(self, module_id: str, directory: str) -> dict:
        """
        Load grouped specs from directory.
        Supports both markdown_specs (.md files) and config_specs (.json files).

        Returns:
            dict with keys:
                - markdown_specs: list of {"module_id", "feature_unit_id", "filename", "content"}
                - config_specs: list of {"module_id", "feature_unit_id", "filename", "content"}
        """
        groups = {"markdown_specs": [], "config_specs": []}

        for file_path in Path(directory).rglob("*"):
            if not file_path.is_file():
                continue

            document = {
                "module_id": module_id,
                "feature_unit_id": file_path.parent.name,
                "filename": file_path.name,
            }

            # Markdown files
            if file_path.suffix == ".md":
                document["content"] = file_path.read_text(encoding="utf-8")
                groups["markdown_specs"].append(document)

            # JSON config files
            elif file_path.name == "config_naming_map.json":
                try:
                    document["content"] = json.loads(file_path.read_text(encoding="utf-8"))
                    groups["config_specs"].append(document)
                except json.JSONDecodeError as e:
                    logger.warning("[PIPELINE] Failed to parse JSON file %s: %s", file_path, e)

        return groups

    def _write_grouped_specs(self, module_id: str, grouped_specs: dict, module_dir: Path) -> None:
        """
        Write grouped specs to directory structure.
        Supports both markdown_specs and config_specs.

        Args:
            module_id: Module identifier
            grouped_specs: dict with "markdown_specs" and/or "config_specs" lists
            module_dir: Base directory to write to
        """
        for spec_type, docs in grouped_specs.items():
            if not isinstance(docs, list):
                continue

            for doc in docs:
                feature_dir = module_dir / doc["feature_unit_id"]
                feature_dir.mkdir(parents=True, exist_ok=True)

                file_path = feature_dir / doc["filename"]

                if isinstance(doc["content"], dict):
                    # JSON content
                    with open(file_path, "w", encoding="utf-8") as f:
                        json.dump(doc["content"], f, indent=4, ensure_ascii=False)
                else:
                    # Text content
                    file_path.write_text(doc["content"], encoding="utf-8")

    def _normalize_revise_grouped_specs(
        self, revise_input: dict, module_id: str, mfu_id: str
    ) -> dict:
        """
        Normalize revise input payload into grouped_specs format.

        Accepted shapes:
        - revise_input.grouped_specs
        - revise_input.spec_generation.grouped_specs
        - revise_input.module_result.spec_generation.grouped_specs
        - revise_input.srs_files as an array of spec documents
        """
        groups = {
            "markdown_specs": [],
            "config_specs": [],
        }

        def append_doc(target_key: str, doc: dict) -> None:
            if not isinstance(doc, dict):
                return
            filename = str(doc.get("filename", "")).strip()
            if not filename:
                return

            feature_unit_id = str(doc.get("feature_unit_id") or mfu_id).strip() or mfu_id
            payload = {
                "module_id": str(doc.get("module_id") or module_id).strip() or module_id,
                "feature_unit_id": feature_unit_id,
                "filename": filename,
                "content": doc.get("content", ""),
            }
            groups[target_key].append(payload)

        def consume_grouped_specs(candidate: dict) -> bool:
            if not isinstance(candidate, dict):
                return False

            has_any = False
            for key in ("markdown_specs", "config_specs"):
                docs = candidate.get(key)
                if not isinstance(docs, list):
                    continue
                has_any = True
                for doc in docs:
                    append_doc(key, doc)
            return has_any

        # Preferred explicit grouped specs payload.
        if consume_grouped_specs(revise_input.get("grouped_specs")):
            return groups

        # Support passing full module result payload directly.
        if consume_grouped_specs(revise_input.get("spec_generation", {}).get("grouped_specs")):
            return groups

        if consume_grouped_specs(
            revise_input.get("module_result", {}).get("spec_generation", {}).get("grouped_specs")
        ):
            return groups

        # Backward-compatible flat array payload.
        srs_files = revise_input.get("srs_files", [])
        if isinstance(srs_files, list):
            for doc in srs_files:
                if not isinstance(doc, dict):
                    continue
                filename = str(doc.get("filename", "")).strip().lower()
                if filename.endswith(".json"):
                    append_doc("config_specs", doc)
                else:
                    append_doc("markdown_specs", doc)

        return groups

    @staticmethod
    def _normalize_target_ids(raw_target_ids: Any) -> list[str]:
        """Normalize incoming target ID payload into a unique, ordered list."""
        if raw_target_ids is None:
            return []

        if isinstance(raw_target_ids, str):
            candidate_ids = [raw_target_ids]
        elif isinstance(raw_target_ids, (list, tuple, set)):
            candidate_ids = list(raw_target_ids)
        else:
            return []

        seen: set[str] = set()
        normalized: list[str] = []
        for story_id in candidate_ids:
            cleaned = str(story_id or "").strip()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            normalized.append(cleaned)
        return normalized

    @staticmethod
    def _feedback_text(value: Any) -> str:
        """Extract plain feedback text from either string or {content: str}."""
        if isinstance(value, dict):
            value = value.get("content", "")
        return str(value or "").strip()

    def _normalize_revise_feedback_payload(
        self,
        revise_input: dict[str, Any],
        module_id: str,
        mfu_id: str,
    ) -> dict[str, Any]:
        """
        Normalize revise feedback payloads into run_for_mfu-compatible fields.

        Supports both legacy flat feedback and new backend feedback arrays.
        """
        requested_mode = str(revise_input.get("mode", "edit")).strip().lower() or "edit"
        mode = requested_mode if requested_mode in {"edit", "regenerate"} else "edit"

        feedback = self._feedback_text(revise_input.get("feedback"))
        quoted_text = self._feedback_text(revise_input.get("quoted_text")) or None
        target_ids = self._normalize_target_ids(
            revise_input.get("target_ids", revise_input.get("targets"))
        )

        # Preferred path: consume structured feedback directly, same shape expected by cmd_revise.
        raw_feedback_spec = revise_input.get("feedback_spec")
        if isinstance(raw_feedback_spec, dict):
            spec_entries = raw_feedback_spec.get("entries")
            if isinstance(spec_entries, list) and spec_entries:
                feedback_spec = {
                    "module": raw_feedback_spec.get("module") or module_id,
                    "mfu": raw_feedback_spec.get("mfu") or mfu_id,
                    "entries": spec_entries,
                }
                try:
                    _render_structured_feedback(feedback_spec.get("entries", []))
                except Exception as exc:
                    return {
                        "error": f"invalid feedback_spec: {exc}",
                        "mode": mode,
                    }
                return {
                    "mode": mode,
                    "feedback": feedback,
                    "quoted_text": quoted_text,
                    "target_ids": target_ids,
                    "feedback_spec": feedback_spec,
                }

        raw_feedbacks = revise_input.get("feedbacks")
        if isinstance(raw_feedbacks, dict):
            raw_feedbacks = raw_feedbacks.get("feedbacks")

        if isinstance(raw_feedbacks, list):
            feedback_lines: list[str] = []
            quoted_segments: list[str] = []
            target_story_ids: list[str] = []
            entries_by_story: dict[str | None, list[dict[str, str | None]]] = {}

            for item in raw_feedbacks:
                if not isinstance(item, dict):
                    continue

                item_module = str(item.get("module_id") or module_id).strip()
                item_mfu = str(item.get("mfu_id") or mfu_id).strip()
                if item_module and module_id and item_module != module_id:
                    continue
                if item_mfu and mfu_id and item_mfu != mfu_id:
                    continue

                user_story_id = str(item.get("user_story_id") or "").strip()
                scope_tag = f"[USER_STORY:{user_story_id}]" if user_story_id else "[FEATURE]"
                if user_story_id:
                    target_story_ids.append(user_story_id)

                story_key: str | None = user_story_id or None
                entries_by_story.setdefault(story_key, [])

                overall_feedback = self._feedback_text(item.get("overall_feedback"))
                if overall_feedback:
                    feedback_lines.append(f"{scope_tag} {overall_feedback}")
                    entries_by_story[story_key].append(
                        {"quoted_text": None, "feedback_text": overall_feedback}
                    )

                specific_feedbacks = item.get("specific_feedback")
                if not isinstance(specific_feedbacks, list):
                    continue

                for specific in specific_feedbacks:
                    if not isinstance(specific, dict):
                        continue

                    selected_text = self._feedback_text(specific.get("selected_text"))
                    selected_feedback = self._feedback_text(
                        specific.get("selected_feedback", specific.get("content"))
                    )

                    if selected_text:
                        quoted_segments.append(selected_text)

                    if selected_feedback:
                        if selected_text:
                            feedback_lines.append(
                                f'{scope_tag} For selected text "{selected_text}": {selected_feedback}'
                            )
                        else:
                            feedback_lines.append(f"{scope_tag} {selected_feedback}")
                        entries_by_story[story_key].append(
                            {
                                "quoted_text": selected_text or None,
                                "feedback_text": selected_feedback,
                            }
                        )

            # Build structured feedback for cmd_revise when possible.
            entries = []
            for story_id, items in entries_by_story.items():
                clean_items = [
                    it
                    for it in items
                    if isinstance(it, dict) and self._feedback_text(it.get("feedback_text"))
                ]
                if not clean_items:
                    continue
                entries.append({"story_id": story_id, "items": clean_items})

            if entries:
                feedback_spec = {
                    "module": module_id,
                    "mfu": mfu_id,
                    "entries": entries,
                }
                try:
                    _render_structured_feedback(entries)
                except Exception as exc:
                    return {
                        "error": f"invalid feedbacks payload: {exc}",
                        "mode": mode,
                    }

                return {
                    "mode": mode,
                    "feedback": feedback,
                    "quoted_text": quoted_text,
                    "target_ids": target_ids,
                    "feedback_spec": feedback_spec,
                }

            if feedback_lines and not feedback:
                feedback = "\n".join(feedback_lines)

            if quoted_segments and not quoted_text:
                deduped_segments = list(dict.fromkeys(quoted_segments))
                quoted_text = "\n".join(deduped_segments)

            if target_story_ids and not target_ids:
                target_ids = self._normalize_target_ids(target_story_ids)

        if not feedback:
            return {
                "error": "feedback or feedbacks with usable content is required",
                "mode": mode,
            }

        return {
            "mode": mode,
            "feedback": feedback,
            "quoted_text": quoted_text,
            "target_ids": target_ids,
            "feedback_spec": None,
        }

    # ── Global stages ────────────────────────────────────────────────────────

    # =========================================================================
    # Stage 1: Global Artifact Generation
    # =========================================================================

    def build_global_index(self) -> dict:
        """
        Stage 1a — Build global index from source files.
        Reads source files and returns global_index dict.

        Returns:
            Dict containing global_index
        """
        global_index_path = self.global_dir / "global_index.json"
        _build_index(self.source_dir, global_index_path)
        global_index = self._read_json(global_index_path)
        return {"global_index": global_index}

    def execute_global_scan(self) -> dict:
        """
        Stage 1b — Execute polyglot scan to detect artifacts.
        Reads source files and returns artifacts_detected dict.

        Returns:
            Dict containing artifacts_detected
        """
        artifacts_detected_path = self.global_dir / "artifacts_detected.json"
        _execute_global_scan(self.source_dir, artifacts_detected_path)
        artifacts_detected = self._read_json(artifacts_detected_path)
        return {"artifacts_detected": artifacts_detected}

    def enrich_with_call_graph(self, artifacts_detected: dict) -> dict:
        """Stage 2 — Returns artifacts_enriched dict."""
        artifacts_detected_path = self._write_json(
            self.global_dir, "artifacts_detected.json", artifacts_detected
        )
        artifacts_enriched_path = self.global_dir / "artifacts_enriched.json"
        _enrich_with_call_graph(artifacts_detected_path, artifacts_enriched_path)
        artifacts_enriched = self._read_json(artifacts_enriched_path)
        return {"artifacts_enriched": artifacts_enriched}

    def discover_modules(
        self,
        global_index: dict,
        artifacts_enriched: dict,
        module_derivation: str = "auto",
    ) -> dict:
        """Stage 2.5 — Derive and return the module manifest."""
        idx_path = self._write_json(self.global_dir, "global_index.json", global_index)
        enr_path = self._write_json(self.global_dir, "artifacts_enriched.json", artifacts_enriched)
        module_manifest_path = self.global_dir / "module_manifest.json"
        budget_report_path = self.global_dir / "budget_report.json"

        if module_derivation not in {"auto", "source", "hybrid"}:
            raise ValueError("module_derivation must be one of: auto, source, hybrid")

        engine = module_derivation
        if engine == "auto":
            try:
                if _choose_module_engine is None:
                    raise ImportError("source module engine selector unavailable")

                decision = _choose_module_engine(str(self.project_dir), enriched_path=str(enr_path))
                engine = decision["engine"]
                logger.info(
                    "[PIPELINE] Stage 2.5 auto-selected '%s': %s",
                    engine,
                    decision.get("reason", ""),
                )
            except Exception as exc:
                engine = "hybrid"
                logger.warning(
                    "[PIPELINE] Stage 2.5 auto-detection failed; using hybrid: %s",
                    exc,
                )

        manifest_written = False
        if engine == "source" and _derive_modules_from_source is not None:
            try:
                llm = LLMClient(use_reasoning_model=True, api_key=self.api_key)
                result = _derive_modules_from_source(
                    str(self.project_dir),
                    lambda system, user, max_tokens: llm.complete(
                        system, user, max_tokens=max_tokens
                    ),
                    enriched_path=str(enr_path),
                    manifest_path=str(module_manifest_path),
                )
                manifest_written = bool(result and result.get("manifest_written"))
            except Exception as exc:
                logger.warning(
                    "[PIPELINE] Source module derivation failed; falling back to hybrid: %s",
                    exc,
                )

        if not manifest_written:
            _build_hybrid_manifest(idx_path, enr_path, module_manifest_path)

        module_manifest = self._read_json(module_manifest_path)
        if budget_report_path.exists():
            budget_report = self._read_json(budget_report_path)
        else:
            logger.warning(
                "[PIPELINE] budget_report.json not generated at %s; using empty budget report",
                budget_report_path,
            )
            budget_report = {}

        return {"module_manifest": module_manifest, "budget_report": budget_report}

    # ── Per-module stages ────────────────────────────────────────────────────

    def slice_enriched_for_module(
        self, artifacts_enriched: dict, module_data: dict, graph_dir: Path
    ) -> dict:
        """Returns module-scoped artifacts_enriched dict."""
        global_enriched_path = self._write_json(
            self.global_dir, "artifacts_enriched.json", artifacts_enriched
        )
        artifacts_enriched_path = graph_dir / "artifacts_enriched.json"
        _slice_enriched_for_module(global_enriched_path, module_data, artifacts_enriched_path)

        artifacts_enriched_module = self._read_json(artifacts_enriched_path)
        return {"artifacts_enriched": artifacts_enriched_module}

    def slice_detected_for_module(self, sliced_enriched: dict, scan_dir: Path) -> dict:
        """Returns module-scoped artifacts_detected dict, derived from sliced_enriched."""
        sliced_artifacts = sliced_enriched.get("artifacts", [])
        detected_artifacts = [
            {k: v for k, v in art.items() if k not in ["graph", "reuse", "graph_metrics"]}
            for art in sliced_artifacts
        ]

        # Write to disk for inspection
        self._write_json(scan_dir, "artifacts_detected.json", {"artifacts": detected_artifacts})
        return {"artifacts_detected": {"artifacts": detected_artifacts}}

    def enrich_semantics(
        self, module_enriched: dict, module_enriched_dir: Path, semantic_dir: Path
    ) -> dict:
        """Stage 3a — Returns artifacts_semantic dict."""
        module_enriched_path = self._write_json(
            module_enriched_dir, "artifacts_enriched.json", module_enriched
        )
        semantic_path = semantic_dir / "artifacts_semantic.json"
        log_path = semantic_dir / "ai_log.txt"

        runner = LLMEnricher(
            schema_path=self._schema_paths["semantic"],
            system_prompt_path=self._prompt_paths["enricher_sys"],
            generation_prompt_path=self._prompt_paths["enricher_gen"],
            mapping_path=self._archetype_paths["archetype_mapping"],
            api_key=self.api_key,
        )

        runner.run(
            artifacts_enriched_path=module_enriched_path,
            output_path=semantic_path,
            log_path=log_path,
        )

        artifacts_semantic = self._read_json(semantic_path)
        return {"artifacts_semantic": artifacts_semantic}

    def generate_mfus(self, artifacts_semantic: dict, ai_dir: Path) -> dict:
        """Stage 3b — Returns mfus_proposed dict."""
        artifacts_semantic_path = self._write_json(
            self.global_dir, "artifacts_semantic.json", artifacts_semantic
        )
        mfus_proposed_path = ai_dir / "mfus_proposed.json"
        log_path = ai_dir / "ai_run_log.txt"

        runner = AIRunner(
            schema_path=self._schema_paths["proposed"],
            system_prompt_path=self._prompt_paths["mfu_sys"],
            generation_prompt_path=self._prompt_paths["mfu_gen"],
            use_mock=False,
            api_key=self.api_key,
        )

        runner.run(
            artifacts_path=artifacts_semantic_path,
            output_path=mfus_proposed_path,
            log_path=log_path,
        )

        mfus_proposed = self._read_json(mfus_proposed_path)
        return {"mfus_proposed": mfus_proposed}

    def sweep_orphans(self, mfus_proposed: dict, artifacts_semantic: dict) -> dict:
        """Stage 3c — Pure in-memory. Returns updated mfus_proposed dict."""
        mfus_proposed_swept = _sweep_orphans(mfus_proposed, artifacts_semantic)
        return {"mfus_proposed": mfus_proposed_swept}

    def review_mfus(
        self, mfus_proposed: dict, module_enriched: dict, ai_dir: Path, module_enriched_dir: Path
    ) -> dict:
        """Stage 3d — Returns mfus_final dict."""
        mfu_proposed_path = self._write_json(ai_dir, "mfus_proposed.json", mfus_proposed)
        artifacts_enriched_path = self._write_json(
            module_enriched_dir, "artifacts_enriched.json", module_enriched
        )

        mfus_final_path = ai_dir / "mfus_final.json"
        log_path = ai_dir / "review_log.txt"

        reviewer = ReviewAgent(
            schema_path=self._schema_paths["final"],
            system_prompt_path=self._prompt_paths["review_sys"],
            api_key=self.api_key,
        )

        reviewer.run(
            mfus_input_path=mfu_proposed_path,
            artifacts_enriched_path=artifacts_enriched_path,
            output_path=mfus_final_path,
            log_path=log_path,
        )

        mfus_final = self._read_json(mfus_final_path)
        return {"mfus_final": mfus_final}

    def validate_guardrails(self, mfus_final: dict, artifacts_semantic: dict) -> dict:
        """Stage 3e — Pure in-memory. Returns guardrail_report dict."""
        guardrail_report = _apply_guardrails(mfus_final, artifacts_semantic)
        # Optional: write to disk for inspection
        self._write_json(self.global_dir, "guardrail_report.json", guardrail_report)
        return {"guardrail_report": guardrail_report}

    def compare_mfu_proposed_vs_final(
        self, mfus_proposed: dict, mfus_final: dict, ai_dir: Path, compare_dir: Path
    ) -> dict:
        """Stage 4a — Compare proposed vs final MFUs. Returns comparison dict."""
        mfus_proposed_path = self._write_json(ai_dir, "mfus_proposed.json", mfus_proposed)
        mfus_final_path = self._write_json(ai_dir, "mfus_final.json", mfus_final)
        mfu_comparison_report_path = compare_dir / "mfu_comparison_report.json"

        _compare(mfus_proposed_path, mfus_final_path, mfu_comparison_report_path)

        mfu_comparison_report = self._read_json(mfu_comparison_report_path)

        _export_comparison(mfu_comparison_report, mfu_comparison_report_path)

        return {"mfu_comparison_report": mfu_comparison_report}

    def extract_specs(
        self,
        module_id: str,
        spec_dir: Path,
        global_enriched: dict,
        shared_extractor=None,
        request_id: str | None = None,
    ) -> dict:
        """
        Stage 4 — Writes .md spec files to modules_root/module_id/stage4_specs/.
        Stage 5 reads those files by path — disk write is intentional.
        Returns {"grouped_specs": dict}.

        *request_id* is the cooperative-cancellation request id (see
        app/core/task_control.py), forwarded to the extractor so its per-MFU
        loop can stop between MFUs. This is the longest sub-stage of Step 1
        and its inner loop is the only place inside it a boundary check in
        the orchestrator cannot reach. Optional — CLI/local runs pass nothing
        and the extractor then never checks (see ``SpecExtractor.run_all``).
        """
        global_artifacts_enriched_path = self._write_json(
            self.global_dir, "artifacts_enriched.json", global_enriched
        )

        if shared_extractor is not None:
            shared_extractor.set_target_module(module_id)
            shared_extractor.run_all(request_id=request_id)
        else:
            extractor = _SpecExtractor(
                project_root=str(Path.cwd()),
                config_path=str(self.config_path),
                prompt_dir=str(self.prompt_dir),
                source_dir=str(self.source_dir),
                global_enriched_path=str(global_artifacts_enriched_path),
                target_module=module_id,
                api_key=self.api_key,
            )
            extractor.run_all(request_id=request_id)

        grouped_specs = self._load_grouped_specs(module_id, spec_dir)
        return {"grouped_specs": grouped_specs}

    def derive_features(
        self, module_id: str, grouped_specs: dict, request_id: str | None = None
    ) -> list:
        """
        Stage 5 — Reads .md files from stage4_specs (written by Stage 4).
        Writes features_stories.json to stage5_backlog.
        Returns list of result dicts.

        *request_id* is the cooperative-cancellation request id (see
        app/core/task_control.py) — forwarded to run_for_module so it can
        stop dispatching further MFUs once the request is cancelled.
        """
        self._write_grouped_specs(
            module_id, grouped_specs, self.modules_root / module_id / "stage4_specs"
        )

        # Use project_dir as project_root (where modules are located)
        # and explicitly pass prompt paths from the pipeline directory
        pipeline_root = self.prompt_dir.parent

        fs_agent = _FeatureStoryAgent(
            project_root=str(self.project_dir),
            stage5a_prompt_path=str(
                pipeline_root / "prompts" / "05a_feature_manifest_generator.txt"
            ),
            stage5b_prompt_path=str(pipeline_root / "prompts" / "05b_story_generator.txt"),
            critic_prompt_path=str(pipeline_root / "prompts" / "05_feature_story_critic.txt"),
            max_iterations=3,
            trigger_neo4j=False,
            api_key=self.api_key,
        )

        results = fs_agent.run_for_module(self.modules_root / module_id, request_id=request_id)

        return results

    def revise_mfu(
        self,
        module_id: str,
        mfu_id: str,
        feedback: str,
        mode: str = "edit",
        quoted_text: str | None = None,
        target_ids: list[str] | None = None,
        feedback_spec: dict[str, Any] | None = None,
    ) -> dict:
        """
        Stage 5 revise flow for one MFU.

        Re-runs feature/story generation for a specific MFU folder with human guidance.
        """
        module_id = str(module_id or "").strip()
        mfu_id = str(mfu_id or "").strip()
        feedback = str(feedback or "").strip()
        requested_mode = str(mode or "edit").strip().lower() or "edit"
        mode = requested_mode if requested_mode in {"edit", "regenerate"} else "edit"
        target_ids = self._normalize_target_ids(target_ids)
        quoted_text = str(quoted_text or "").strip() or None

        if not module_id or not mfu_id or (not feedback and not feedback_spec):
            return {
                "status": "failed",
                "module_id": module_id,
                "mfu_id": mfu_id,
                "mode": mode,
                "error": "module_id, mfu_id and feedback (or feedback_spec) are required",
            }

        mfu_dir = self.modules_root / module_id / "stage4_specs" / mfu_id
        if not mfu_dir.exists():
            return {
                "status": "failed",
                "module_id": module_id,
                "mfu_id": mfu_id,
                "mode": mode,
                "error": f"MFU directory not found: {mfu_dir}",
            }

        # Reuse the canonical revise implementation from src/cli/main.py.
        # This keeps payload handling and guidance behavior in one place.
        try:
            _set_project_paths(str(self.project_dir))
            revise_result = _cmd_revise(
                module_name=module_id,
                mfu_id=mfu_id,
                feedback=feedback,
                mode=mode,
                quoted_text=quoted_text,
                target_ids=target_ids,
                feedback_spec=feedback_spec,
            )
        except Exception as exc:
            return {
                "status": "failed",
                "module_id": module_id,
                "mfu_id": mfu_id,
                "mode": mode,
                "requested_mode": requested_mode,
                "error": str(exc),
            }

        if not isinstance(revise_result, dict):
            return {
                "status": "failed",
                "module_id": module_id,
                "mfu_id": mfu_id,
                "mode": mode,
                "requested_mode": requested_mode,
                "error": "Revise did not return a result payload",
            }

        if not revise_result.get("ok"):
            return {
                "status": "failed",
                "module_id": module_id,
                "mfu_id": mfu_id,
                "mode": revise_result.get("mode", mode),
                "requested_mode": requested_mode,
                "error": revise_result.get("error") or "Revise failed",
            }

        # Canonical revise artifact path.
        output_path = mfu_dir / "features_stories.json"
        output_doc: dict | None = None
        if output_path.exists():
            try:
                output_doc = json.loads(output_path.read_text(encoding="utf-8"))
            except Exception as exc:
                return {
                    "status": "failed",
                    "module_id": module_id,
                    "mfu_id": mfu_id,
                    "mode": revise_result.get("mode", mode),
                    "requested_mode": requested_mode,
                    "error": f"Failed to load revise output from {output_path}: {exc}",
                }

        try:
            # Ensure canonical formatting without mutating business content.
            if isinstance(output_doc, dict):
                output_path.write_text(
                    json.dumps(output_doc, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
        except Exception as exc:
            return {
                "status": "failed",
                "module_id": module_id,
                "mfu_id": mfu_id,
                "mode": revise_result.get("mode", mode),
                "requested_mode": requested_mode,
                "error": f"Failed to write revise output to {output_path}: {exc}",
            }

        return {
            "status": "success",
            "module_id": module_id,
            "mfu_id": mfu_id,
            "mode": revise_result.get("mode", mode),
            "requested_mode": requested_mode,
            "final_status": revise_result.get("final_status", "UNKNOWN"),
            "review_guidance": revise_result.get("review_guidance", {}),
            "feedback_override": revise_result.get("feedback_override"),
            "output_path": str(output_path),
            "result": output_doc,
        }

    # =========================================================================
    # Revise with Project Reconstruction (In-Place Stage 5 Revise)
    # =========================================================================

    def _reconstruct_revise_workspace(self, revise_input: dict, project_workspace: Path) -> dict:
        """
        Reconstruct the project workspace from revise input package.

        Creates folder structure and writes all necessary files:
        - project_config.json (root)
        - _global/module_manifest.json
        - modules/<module_id>/stage4_specs/<mfu_id>/ (SRS + config_naming_map)

        Args:
            revise_input: Dict with keys:
                - module_id, mfu_id, feedback, mode
                - srs_files: list of {"module_id", "feature_unit_id", "filename", "content"}
                - grouped_specs: optional {"markdown_specs": [...], "config_specs": [...]} payload
                - spec_generation.grouped_specs: optional nested grouped specs payload
                - module_result.spec_generation.grouped_specs: optional full module result payload
                - config_naming_map: optional dict with mfu metadata
                - feature_manifest: optional dict to pre-seed feature_manifest.json
                - features_stories: optional dict to pre-seed features_stories.json
                - result: optional dict alias for features_stories payload
                - module_manifest: optional full manifest from backend
                - module_context: optional dict with refined module_name, description
                - source_paradigm: str (e.g. "cobol", "pb")
                - project_config_overrides: optional dict to merge into project_config
            project_workspace: Path to project workspace root

        Returns:
            dict with keys:
                - module_id, mfu_id
                - mfu_dir: Path to reconstructed MFU directory
                - project_root: Path to reconstructed project workspace root
                - error: if reconstruction failed
        """
        try:
            module_id = str(revise_input.get("module_id", "")).strip()
            mfu_id = str(revise_input.get("mfu_id", "")).strip()
            source_paradigm = str(revise_input.get("source_paradigm", "pb")).strip().lower() or "pb"

            if not module_id or not mfu_id:
                return {
                    "error": "module_id and mfu_id required in revise_input",
                    "module_id": module_id,
                    "mfu_id": mfu_id,
                }

            # Create workspace directory structure
            workspace = Path(project_workspace).resolve()
            workspace.mkdir(parents=True, exist_ok=True)

            global_dir = workspace / "_global"
            modules_dir = workspace / "modules"
            mfu_dir = modules_dir / module_id / "stage4_specs" / mfu_id
            mfu_dir.mkdir(parents=True, exist_ok=True)
            global_dir.mkdir(parents=True, exist_ok=True)

            # 1. Create project_config.json
            project_config = self._config.copy() if hasattr(self, "_config") else {}

            # Ensure project section exists
            if "project" not in project_config:
                project_config["project"] = {}
            project_config["project"]["source_paradigm"] = source_paradigm

            # Apply overrides if provided
            if isinstance(revise_input.get("project_config_overrides"), dict):
                for key, value in revise_input["project_config_overrides"].items():
                    if (
                        isinstance(value, dict)
                        and key in project_config
                        and isinstance(project_config[key], dict)
                    ):
                        project_config[key].update(value)
                    else:
                        project_config[key] = value

            self._write_json(workspace, "project_config.json", project_config)
            logger.info("[REVISE] Created project_config.json at %s", workspace)

            # 2. Create/update _global/module_manifest.json
            incoming_manifest = revise_input.get("module_manifest")
            if isinstance(incoming_manifest, dict):
                module_manifest = json.loads(json.dumps(incoming_manifest))
                if not isinstance(module_manifest.get("modules"), list):
                    module_manifest["modules"] = []
                logger.info(
                    "[REVISE] Using backend-provided module_manifest with %d module(s)",
                    len(module_manifest["modules"]),
                )
            else:
                module_manifest = {"modules": []}

            module_context = revise_input.get("module_context")
            target_module = None
            for mod in module_manifest["modules"]:
                if isinstance(mod, dict) and str(mod.get("module_id", "")).strip() == module_id:
                    target_module = mod
                    break

            if target_module is None:
                target_module = {
                    "module_id": module_id,
                    "module_name": module_id,
                    "description": "",
                }
                module_manifest["modules"].append(target_module)

            # Apply explicit module_context as an override for the target module only.
            if isinstance(module_context, dict):
                if "module_name" in module_context:
                    target_module["module_name"] = module_context.get("module_name") or module_id
                if "description" in module_context:
                    target_module["description"] = module_context.get("description") or ""

            self._write_json(global_dir, "module_manifest.json", module_manifest)
            logger.info(
                "[REVISE] Created _global/module_manifest.json (modules=%d, target=%s)",
                len(module_manifest["modules"]),
                module_id,
            )

            # 3. Materialize target MFU specs as individual files under mfu_dir.
            grouped_specs = self._normalize_revise_grouped_specs(revise_input, module_id, mfu_id)

            target_docs = []
            for doc in grouped_specs.get("markdown_specs", []) + grouped_specs.get(
                "config_specs", []
            ):
                if not isinstance(doc, dict):
                    continue
                if str(doc.get("feature_unit_id", "")).strip() != mfu_id:
                    continue
                target_docs.append(doc)

            if not target_docs:
                return {
                    "error": f"No grouped spec documents found for target mfu_id '{mfu_id}'",
                    "module_id": module_id,
                    "mfu_id": mfu_id,
                }

            for doc in target_docs:
                filename = Path(str(doc.get("filename", "")).strip()).name
                if not filename:
                    continue
                content = doc.get("content", "")
                file_path = mfu_dir / filename
                if isinstance(content, dict):
                    file_path.write_text(
                        json.dumps(content, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                else:
                    file_path.write_text(str(content), encoding="utf-8")

            logger.info(
                "[REVISE] Reconstructed %d spec file(s) under %s", len(target_docs), mfu_dir
            )

            # 4. Write config_naming_map.json
            if isinstance(revise_input.get("config_naming_map"), dict):
                config_map = revise_input["config_naming_map"]
                self._write_json(mfu_dir, "config_naming_map.json", config_map)
                logger.info("[REVISE] Created config_naming_map.json")

            # 5. Optionally pre-seed Stage 5 artifacts in mfu_dir.
            feature_manifest = revise_input.get("feature_manifest")
            if isinstance(feature_manifest, dict):
                self._write_json(mfu_dir, "feature_manifest.json", feature_manifest)
                logger.info("[REVISE] Pre-seeded feature_manifest.json")

            features_stories = revise_input.get("features_stories")
            if not isinstance(features_stories, dict):
                features_stories = revise_input.get("result")

            if isinstance(features_stories, dict):
                self._write_json(mfu_dir, "features_stories.json", features_stories)
                logger.info("[REVISE] Pre-seeded features_stories.json")

            logger.info("[REVISE] Workspace reconstruction complete: %s", mfu_dir)
            return {
                "module_id": module_id,
                "mfu_id": mfu_id,
                "mfu_dir": mfu_dir,
                "project_root": workspace,
            }

        except Exception as e:
            logger.error("[REVISE] Workspace reconstruction failed: %s", e)
            return {
                "error": str(e),
                "module_id": revise_input.get("module_id"),
                "mfu_id": revise_input.get("mfu_id"),
            }

    def revise_mfu_with_reconstruction(self, revise_input: dict) -> dict:
        """
        Standalone revise with in-place project reconstruction.

        Complete flow:
        1. Use the configured project workspace root
        2. Reconstruct project structure from revise_input
        3. Invoke Stage 5 revise pipeline
        4. Extract and return results

        Args:
            revise_input: Dict with:
                - module_id, mfu_id
                - feedback: optional composed string guidance
                - quoted_text: optional selected text context
                - target_ids: optional list of user story IDs for edit-scope locking
                - mode: optional, default "edit"
                - srs_files: list of {"module_id", "feature_unit_id", "filename", "content"}
                - config_naming_map: optional
                - features_stories/result: optional dict to pre-seed features_stories.json in mfu_dir
                - module_manifest: optional full manifest from backend
                - source_paradigm: optional (default "pb")
                - workspace_root: optional project workspace root (used directly)

        Returns:
            dict with:
                - status: "success" or "failed"
                - module_id, mfu_id, mode
                - final_status: from Stage 5 generation_metadata
                - result: full features_stories.json output
                - features_stories: extracted features_stories content
                - error: if status is "failed"
        """
        try:
            # Validate input
            if not isinstance(revise_input, dict):
                return {"status": "failed", "error": "revise_input must be a dict"}

            module_id = revise_input.get("module_id")
            mfu_id = revise_input.get("mfu_id")
            mode = str(revise_input.get("mode", "edit")).strip().lower() or "edit"

            if not module_id or not mfu_id:
                return {
                    "status": "failed",
                    "module_id": module_id,
                    "mfu_id": mfu_id,
                    "mode": mode,
                    "error": "module_id and mfu_id are required",
                }

            feedback_payload = self._normalize_revise_feedback_payload(
                revise_input=revise_input,
                module_id=str(module_id).strip(),
                mfu_id=str(mfu_id).strip(),
            )
            if feedback_payload.get("error"):
                return {
                    "status": "failed",
                    "module_id": module_id,
                    "mfu_id": mfu_id,
                    "mode": feedback_payload.get("mode", mode),
                    "error": feedback_payload["error"],
                }

            # Reconstruct directly under backend-controlled project root.
            workspace_root_raw = revise_input.get("workspace_root")
            if workspace_root_raw:
                project_root = Path(str(workspace_root_raw)).resolve()
            else:
                project_root = self.project_dir.resolve()

            project_root.mkdir(parents=True, exist_ok=True)
            logger.info("[REVISE] Using in-place project root for reconstruction: %s", project_root)

            # Reconstruct workspace from input package
            reconstruct_result = self._reconstruct_revise_workspace(revise_input, project_root)
            if "error" in reconstruct_result:
                return {
                    "status": "failed",
                    "module_id": module_id,
                    "mfu_id": mfu_id,
                    "mode": mode,
                    "error": reconstruct_result["error"],
                }

            mfu_dir = reconstruct_result["mfu_dir"]
            project_root = reconstruct_result["project_root"]

            # Create a service instance bound to the reconstructed project root.
            in_place_service = SourceCodePipelineService(
                project_dir=str(project_root),
                source_dir=str(project_root / "input"),  # dummy; not used for revise
                config_path=str(project_root / "project_config.json"),
                prompt_dir=str(self.prompt_dir),
                schema_dir=str(self.schema_dir),
                archetype_dir=str(self.archetype_dir),
            )

            # Execute revise on the reconstructed MFU directory
            logger.info("[REVISE] Invoking revise pipeline for %s/%s", module_id, mfu_id)
            revise_result = in_place_service.revise_mfu(
                module_id=module_id,
                mfu_id=mfu_id,
                feedback=feedback_payload.get("feedback", ""),
                mode=feedback_payload["mode"],
                quoted_text=feedback_payload.get("quoted_text"),
                target_ids=feedback_payload.get("target_ids"),
                feedback_spec=feedback_payload.get("feedback_spec"),
            )

            # Prefer in-memory result payload; fall back to file output paths.
            features_stories_content = (
                revise_result.get("result") if isinstance(revise_result, dict) else None
            )
            output_path = (
                revise_result.get("output_path") if isinstance(revise_result, dict) else None
            )

            if not isinstance(features_stories_content, dict):
                if output_path:
                    features_stories_path = Path(output_path)
                else:
                    features_stories_path = mfu_dir / "features_stories.json"

                if features_stories_path.exists():
                    try:
                        features_stories_content = json.loads(
                            features_stories_path.read_text(encoding="utf-8")
                        )
                    except Exception as e:
                        logger.warning(
                            "[REVISE] Failed to load features_stories.json from %s: %s",
                            features_stories_path,
                            e,
                        )

            module_manifest_content = None
            module_manifest_path = project_root / "_global" / "module_manifest.json"
            if module_manifest_path.exists():
                try:
                    module_manifest_content = json.loads(
                        module_manifest_path.read_text(encoding="utf-8")
                    )
                except Exception as e:
                    logger.warning(
                        "[REVISE] Failed to load module_manifest.json from %s: %s",
                        module_manifest_path,
                        e,
                    )

            # Return success response with full result
            return {
                "status": revise_result.get("status", "unknown"),
                "module_id": module_id,
                "mfu_id": mfu_id,
                "mode": revise_result.get("mode", feedback_payload.get("mode", mode)),
                "requested_mode": revise_result.get("requested_mode", mode),
                "final_status": revise_result.get("final_status", "UNKNOWN"),
                "review_guidance": revise_result.get(
                    "review_guidance", feedback_payload.get("feedback")
                ),
                "features_stories": features_stories_content,
                "module_manifest": module_manifest_content,
                "error": revise_result.get("error"),
            }

        except Exception as e:
            logger.error("[REVISE] Revise with reconstruction failed: %s", e)
            return {
                "status": "failed",
                "module_id": revise_input.get("module_id"),
                "mfu_id": revise_input.get("mfu_id"),
                "mode": revise_input.get("mode", "regenerate"),
                "error": str(e),
            }

    def refine_module_names(
        self,
        module_manifest: dict,
        artifacts_enriched: dict,
        processed_ids: list,
        shared_extractor=None,
        skip_classes: set = None,
    ) -> dict:
        """Stage 2.7 — Returns updated module_manifest dict."""
        skip_classes = skip_classes or {"infrastructure"}
        module_manifest_path = self._write_json(
            self.global_dir, "module_manifest.json", module_manifest
        )
        artifacts_enriched_path = self._write_json(
            self.global_dir, "artifacts_enriched.json", artifacts_enriched
        )

        if shared_extractor is None:
            shared_extractor = _SpecExtractor(
                project_root=str(Path.cwd()),
                config_path=str(self.config_path),
                prompt_dir=str(self.prompt_dir),
                source_dir=str(self.source_dir),
                global_enriched_path=str(artifacts_enriched_path),
                api_key=self.api_key,
            )

        shared_extractor.refine_module_manifest_names(
            modules_dir=self.modules_root,
            manifest_path=module_manifest_path,
            module_filter=processed_ids,
            skip_classes=skip_classes,
        )

        refined_manifest = self._read_json(module_manifest_path)
        return {"module_manifest": refined_manifest}

    def build_neo4j_graphs(self, global_enriched: dict) -> dict:
        """Neo4j Graph Generation — Returns dict with traceability and feature/story cypher strings."""
        artifacts_enriched_path = self._write_json(
            self.global_dir, "artifacts_enriched.json", global_enriched
        )
        results = {}

        graph_types = [
            ("traceability", "build_matrix"),
            ("feature_story", "build_feature_story_graph"),
        ]

        for key, method in graph_types:
            exporter = _Neo4jExporter(
                project_root=str(Path.cwd()), global_graph_path=str(artifacts_enriched_path)
            )
            getattr(exporter, method)()

            # Export to output directory with proper naming
            output_path = self.output_dir / f"{key}.cypher"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            exporter.export(str(output_path))

            results[key] = output_path.read_text(encoding="utf-8")

        return results

    def generate_reports(self) -> dict:
        """
        Stage 6 — Generate executive RIP reports.
        Aggregates results from all modules and exports reports in multiple formats.

        Returns:
            Dict with report generation status, metadata, and report contents
        """
        if not _ReportGenerator:
            return {"status": "skipped", "message": "ReportGenerator not available"}

        report_gen = _ReportGenerator(
            project_root=str(self.project_dir), config_path=str(self.config_path)
        )
        report_gen.aggregate_results()
        report_gen.export_all()

        # Load generated report files
        reports_dir = self.project_dir / "reports"
        reports = {}

        # Report 1: Feature Requirements Catalog (CSV)
        catalog_path = reports_dir / "1_feature_requirements_catalog.csv"
        if catalog_path.exists():
            reports["feature_catalog"] = catalog_path.read_text(encoding="utf-8")

        # Report 2: Technical Landmine Registry (CSV)
        landmine_path = reports_dir / "2_technical_landmine_registry.csv"
        if landmine_path.exists():
            reports["landmine_registry"] = landmine_path.read_text(encoding="utf-8")

        # Report 3: Functional Manifest (Markdown)
        manifest_path = reports_dir / "3_functional_manifest.md"
        if manifest_path.exists():
            reports["functional_manifest"] = manifest_path.read_text(encoding="utf-8")

        return {"reports": reports}

    def get_module_manifest(self) -> dict:
        """Return module_manifest.json from the active project's _global directory."""
        module_manifest_path = self.global_dir / "module_manifest.json"
        if not module_manifest_path.exists():
            raise FileNotFoundError(f"module_manifest.json not found at: {module_manifest_path}")

        module_manifest = self._read_json(module_manifest_path)
        return {
            "module_manifest": module_manifest,
            "path": str(module_manifest_path),
        }

    def generate_domain_knowledge(self, max_tokens: int = 6000) -> dict:
        """Generate and return the project-level domain knowledge document.

        Consumes the module derivation artifacts and call graph already written to
        ``_global``. The generated Markdown is returned for backend responses and
        is also persisted as ``system_onboarding.md`` by the domain tool.
        """
        try:
            if not _generate_onboarding:
                raise RuntimeError("Domain onboarding generator is not available")

            llm = LLMClient(use_reasoning_model=True, api_key=self.api_key)
            result = _generate_onboarding(
                str(self.project_dir),
                lambda system, user, tokens: llm.complete(system, user, max_tokens=tokens),
                max_tokens=max_tokens,
            )
            if not result:
                raise FileNotFoundError(
                    "No module derivation artifacts found; run global artifact generation first."
                )

            document_path = Path(result["onboarding_path"])
            return {
                "status": "success",
                "content": document_path.read_text(encoding="utf-8"),
            }
        except Exception as exc:
            logger.error("[SERVICE] Domain knowledge generation failed: %s", exc)
            return {
                "status": "failed",
                "error": str(exc),
            }

    def generate_architecture_document(
        self,
        seed_modules: list[str] | None = None,
        max_iters: int = 2,
    ) -> dict:
        """Generate and return the target architecture document and source JSON."""
        try:
            if not _derive_architecture:
                raise RuntimeError("Architecture synthesis tool is not available")

            llm = LLMClient(use_reasoning_model=True, api_key=self.api_key)
            result = _derive_architecture(
                str(self.project_dir),
                lambda system, user, tokens: llm.complete(system, user, max_tokens=tokens),
                seed_modules=seed_modules,
                config_path=str(self.config_path),
                max_iters=max_iters,
            )
            markdown_path = Path(result["md_path"])
            review = result.get("review") or {}
            logger.info(
                "[SERVICE] Architecture document verdict=%s summary=%s",
                review.get("verdict", "N/A"),
                review.get("summary", "N/A"),
            )
            return {
                "status": "success",
                "passed": result.get("passed"),
                "review": review,
                "content": markdown_path.read_text(encoding="utf-8"),
            }
        except Exception as exc:
            logger.error("[SERVICE] Architecture document generation failed: %s", exc)
            return {
                "status": "failed",
                "error": str(exc),
            }

    def delete_source_codebase(self):
        """
        Deletes the source codebase from local storage.
        Safe to call after Stage 4 has completed for ALL modules.
        Not called automatically anywhere — invoke explicitly when ready.
        """
        if self.source_dir.exists():
            shutil.rmtree(self.source_dir)
            logger.info("[SERVICE] Source codebase deleted: %s", self.source_dir)
        else:
            logger.info("[SERVICE] Source dir not found (already deleted?): %s", self.source_dir)
