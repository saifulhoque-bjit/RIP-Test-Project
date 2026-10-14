"""Feedback revise must reach the LLM with the project's runtime API key.

The source-code feedback flow (POST /user-stories/regenerate-for-source-code ->
regenerate_feature_mfu_task -> PipelineOrchestrator.process_mfu_revise_with_reconstruction)
passes the project's ``llm_api_key`` into ``SourceCodePipelineService``. The key is
runtime-only (never written to project_config.json), so if the revise path drops
it, the Stage 5 FeatureStoryAgent falls back to an env var and every feedback
regeneration fails when the key is configured per project.

Boundary stub: ``FeatureStoryAgent`` in ``src.cli.main`` (the object that would
construct the LLM clients) is replaced with a recorder; everything above it —
the service, workspace reconstruction and ``cmd_revise`` — runs for real.
"""

import shutil
from pathlib import Path

import pytest

from app.services.source_code_pipeline import source_code_pipeline_service as svc_mod
from app.services.source_code_pipeline.src.cli import main as cli_main
from app.services.source_code_pipeline.src.tools import hybrid_manifest_generator

PIPELINE_DIR = Path(svc_mod.__file__).parent
RUNTIME_KEY = "sk-project-runtime-key"


class _RecordingAgent:
    """Labelled stub for FeatureStoryAgent: records constructor kwargs, no LLM calls."""

    instances: list[dict] = []

    def __init__(self, **kwargs):
        _RecordingAgent.instances.append(kwargs)
        self.kwargs = kwargs

    def run_for_mfu(self, mfu_dir, **kwargs):
        # Record the hybrid manifest generator's key as seen while the agent runs.
        self.kwargs["_hybrid_key_during_run"] = hybrid_manifest_generator._runtime_api_key
        return {"generation_metadata": {"final_status": "PASS"}, "features": []}


@pytest.fixture
def workspace(tmp_path):
    shutil.copy(PIPELINE_DIR / "pb-project_config.json", tmp_path / "project_config.json")
    (tmp_path / "input").mkdir()
    return tmp_path


def _revise_input():
    return {
        "module_id": "MOD-ANCES",
        "mfu_id": "MFU-001",
        "mode": "edit",
        "feedback_spec": {
            "module": "MOD-ANCES",
            "mfu": "MFU-001",
            "entries": [
                {
                    "story_id": "ANCES-001-F1-S1",
                    "items": [{"quoted_text": None, "feedback_text": "Clarify the trigger."}],
                }
            ],
        },
        "srs_files": [{"filename": "srs.md", "content": "# SRS\nThe screen loads ancestors."}],
        "features_stories": {
            "features": [
                {
                    "feature_id": "ANCES-001-F1",
                    "user_stories": [{"story_id": "ANCES-001-F1-S1", "title": "Load"}],
                }
            ]
        },
        "source_paradigm": "pb",
    }


def test_revise_with_reconstruction_passes_runtime_api_key_to_feature_story_agent(
    workspace, monkeypatch
):
    _RecordingAgent.instances = []
    monkeypatch.setattr(cli_main, "FeatureStoryAgent", _RecordingAgent)

    service = svc_mod.SourceCodePipelineService(
        project_dir=str(workspace),
        source_dir=str(workspace / "input"),
        config_path=str(workspace / "project_config.json"),
        api_key=RUNTIME_KEY,
    )
    result = service.revise_mfu_with_reconstruction(_revise_input())

    assert _RecordingAgent.instances, f"FeatureStoryAgent was never built: {result}"
    agent_kwargs = _RecordingAgent.instances[-1]
    assert agent_kwargs.get("api_key") == RUNTIME_KEY
    assert agent_kwargs.get("_hybrid_key_during_run") == RUNTIME_KEY
