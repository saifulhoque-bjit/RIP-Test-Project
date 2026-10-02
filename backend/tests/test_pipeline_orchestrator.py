"""Tests for app/services/source_code_pipeline/pipeline_orchestrator.py."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.source_code_pipeline.pipeline_orchestrator import PipelineOrchestrator
from app.services.source_code_pipeline.source_code_pipeline_service import (
    SourceCodePipelineService,
)


def test_reconstructed_revise_service_preserves_runtime_api_key() -> None:
    service = object.__new__(SourceCodePipelineService)
    service.api_key = "runtime-api-key"
    service.project_dir = Path("/tmp/source-feedback")
    service.prompt_dir = Path("/tmp/prompts")
    service.schema_dir = Path("/tmp/schemas")
    service.archetype_dir = Path("/tmp/config")

    project_root = Path("/tmp/source-feedback/task-1")
    service._reconstruct_revise_workspace = MagicMock(
        return_value={
            "mfu_dir": project_root / "modules/MOD-1/stage4_specs/MFU-1",
            "project_root": project_root,
        }
    )
    in_place_service = MagicMock()
    in_place_service.revise_mfu.return_value = {
        "status": "success",
        "result": {"features": []},
        "output_path": None,
    }

    with patch(
        "app.services.source_code_pipeline.source_code_pipeline_service.SourceCodePipelineService",
        return_value=in_place_service,
    ) as service_cls:
        result = service.revise_mfu_with_reconstruction(
            {
                "module_id": "MOD-1",
                "mfu_id": "MFU-1",
                "feedback": "Clarify the story.",
                "srs_files": [],
            }
        )

    assert result["status"] == "success"
    assert service_cls.call_args.kwargs["api_key"] == "runtime-api-key"


def _make_orchestrator(request_id: str | None = "req-cancel-test") -> PipelineOrchestrator:
    """Build an orchestrator without running __init__ (filesystem/config-heavy:
    loads project config, prepares source layout, loads plugins) — the loop
    under test only needs self.context for `_run_id`/`pipeline_run_guard`."""
    orchestrator = object.__new__(PipelineOrchestrator)
    orchestrator.context = SimpleNamespace(
        project_dir="tests/fixtures/pipeline_orchestrator_cancel",
        source_dir="tests/fixtures/pipeline_orchestrator_cancel/src",
        config_path="tests/fixtures/pipeline_orchestrator_cancel/config.json",
        global_dir=Path("tests/fixtures/pipeline_orchestrator_cancel/_global"),
        prompt_dir="tests/fixtures/pipeline_orchestrator_cancel/prompts",
        skip_processing=False,
        request_id=request_id,
    )
    # process_complete_module_pipeline unconditionally refreshes the module
    # manifest via self.service at the end — normally set up in __init__.
    orchestrator.service = MagicMock()
    orchestrator.service.get_module_manifest.return_value = {"module_manifest": {}}
    return orchestrator


class TestProcessCompleteModulePipelineCancellation:
    """process_complete_module_pipeline's module loop must stop as soon as a
    module reports "cancelled" — continuing would pay for a doomed LLM call
    on every remaining module (LLMClient itself would immediately cancel it,
    but not for free), and would contradict cancellation actually stopping
    the run."""

    def test_stops_iterating_once_a_module_is_cancelled(self):
        orchestrator = _make_orchestrator()
        modules = [{"module_id": f"MOD-{i}"} for i in range(3)]
        orchestrator._filter_modules = MagicMock(return_value=modules)
        orchestrator.process_single_module = MagicMock(
            side_effect=[
                {
                    "module_id": "MOD-0",
                    "status": "success",
                    "mfu_count": 1,
                    "feature_derivation": {"results": [], "total": 0, "approved": 0},
                },
                {"module_id": "MOD-1", "status": "cancelled"},
            ]
        )

        with patch("app.services.source_code_pipeline.pipeline_orchestrator.SpecExtractor"):
            result = orchestrator.process_complete_module_pipeline(module_manifest={})

        # Stopped after the cancelled module — MOD-2 was never touched.
        assert orchestrator.process_single_module.call_count == 2
        assert set(result["module_results"]) == {"MOD-0", "MOD-1"}
        assert result["module_results"]["MOD-1"]["status"] == "cancelled"

    def test_cancelled_module_is_not_counted_as_failed(self):
        orchestrator = _make_orchestrator()
        modules = [{"module_id": "MOD-0"}, {"module_id": "MOD-1"}]
        orchestrator._filter_modules = MagicMock(return_value=modules)
        orchestrator.process_single_module = MagicMock(
            return_value={"module_id": "MOD-0", "status": "cancelled"}
        )

        with patch("app.services.source_code_pipeline.pipeline_orchestrator.SpecExtractor"):
            result = orchestrator.process_complete_module_pipeline(module_manifest={})

        assert result["summary"]["failed_module_ids"] == []
        assert result["summary"]["failed_modules"] == 0

    def test_processes_all_modules_when_none_cancelled(self):
        """Unchanged behavior: no cancellation, every module still runs."""
        orchestrator = _make_orchestrator()
        modules = [{"module_id": f"MOD-{i}"} for i in range(2)]
        orchestrator._filter_modules = MagicMock(return_value=modules)
        orchestrator.process_single_module = MagicMock(
            side_effect=[
                {
                    "module_id": f"MOD-{i}",
                    "status": "success",
                    "mfu_count": 1,
                    "feature_derivation": {"results": [], "total": 0, "approved": 0},
                }
                for i in range(2)
            ]
        )

        with patch("app.services.source_code_pipeline.pipeline_orchestrator.SpecExtractor"):
            result = orchestrator.process_complete_module_pipeline(module_manifest={})

        assert orchestrator.process_single_module.call_count == 2
        assert set(result["module_results"]) == {"MOD-0", "MOD-1"}
