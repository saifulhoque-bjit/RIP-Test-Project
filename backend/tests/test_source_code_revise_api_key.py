"""Regression: source-code feedback (Stage 5 revise) must use the tenant's LLM API key.

``PipelineOrchestrator`` builds ``SourceCodePipelineService`` with the
project's runtime-only ``llm_api_key`` (never written to project_config.json).
The revise path used by ``/user-stories/regenerate-for-source-code`` used to
drop that key twice — the in-place service recreated in
``revise_mfu_with_reconstruction`` had no ``api_key`` (and its constructor
reset the hybrid runtime key to ``None``), and ``cmd_revise`` built
``FeatureStoryAgent`` without one — so every feedback run fell back to an
environment variable and failed wherever only the tenant key is configured.

Boundary stubs (labelled):
- ``_reconstruct_revise_workspace`` — file reconstruction only; replaced by
  creating the MFU directory it would produce.
- ``FeatureStoryAgent`` in ``src/cli/main.py`` — the component that owns the
  LLM client; replaced by a recorder that captures its constructor kwargs.
"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.services.source_code_pipeline import source_code_pipeline_service as svc_mod
from app.services.source_code_pipeline.src.cli import main as cli_main
from app.services.source_code_pipeline.src.tools import hybrid_manifest_generator

TENANT_KEY = "tenant-runtime-key"


class _RecordingAgent:
    """Stub for FeatureStoryAgent: records kwargs, returns a minimal result."""

    instances: list[dict] = []

    def __init__(self, **kwargs):
        _RecordingAgent.instances.append(kwargs)

    def run_for_mfu(self, *args, **kwargs):
        return {"final_status": "PASS", "features_stories": {}}


class TestSourceCodeReviseUsesTenantApiKey(unittest.TestCase):
    def setUp(self):
        _RecordingAgent.instances = []
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "project_config.json").write_text(
            json.dumps({"project": {"source_paradigm": "pb"}}), encoding="utf-8"
        )
        self.mfu_dir = self.root / "modules" / "MOD1" / "stage4_specs" / "MFU1"

    def tearDown(self):
        hybrid_manifest_generator.set_runtime_api_key(None)
        self._tmp.cleanup()

    def _fake_reconstruct(self, revise_input, project_root):
        self.mfu_dir.mkdir(parents=True, exist_ok=True)
        return {"mfu_dir": self.mfu_dir, "project_root": Path(project_root)}

    def test_revise_with_reconstruction_passes_tenant_key_to_feature_story_agent(self):
        service = svc_mod.SourceCodePipelineService(
            project_dir=str(self.root),
            config_path=str(self.root / "project_config.json"),
            api_key=TENANT_KEY,
        )
        revise_input = {
            "module_id": "MOD1",
            "mfu_id": "MFU1",
            "mode": "regenerate",
            "feedback_spec": {
                "module": "MOD1",
                "mfu": "MFU1",
                "entries": [
                    {
                        "story_id": "MOD1-F1-S1",
                        "items": [{"quoted_text": None, "feedback_text": "Add an AC."}],
                    }
                ],
            },
            "srs_files": [],
        }

        with (
            patch.object(
                svc_mod.SourceCodePipelineService,
                "_reconstruct_revise_workspace",
                side_effect=lambda ri, pr: self._fake_reconstruct(ri, pr),
                autospec=False,
            ),
            patch.object(cli_main, "FeatureStoryAgent", _RecordingAgent),
        ):
            service.revise_mfu_with_reconstruction(revise_input)

        self.assertEqual(len(_RecordingAgent.instances), 1, "revise never reached the agent")
        self.assertEqual(_RecordingAgent.instances[0].get("api_key"), TENANT_KEY)
        # Recreating the in-place service must not wipe the process-wide hybrid key.
        self.assertEqual(hybrid_manifest_generator._runtime_api_key, TENANT_KEY)

    def test_cmd_revise_without_key_keeps_env_fallback(self):
        """No tenant key configured: agent gets api_key=None (LLMClient env fallback)."""
        self.mfu_dir.mkdir(parents=True, exist_ok=True)
        cli_main.set_project_paths(str(self.root))
        with patch.object(cli_main, "FeatureStoryAgent", _RecordingAgent):
            cli_main.cmd_revise(
                module_name="MOD1", mfu_id="MFU1", feedback="Add an AC.", mode="regenerate"
            )
        self.assertEqual(len(_RecordingAgent.instances), 1)
        self.assertIsNone(_RecordingAgent.instances[0].get("api_key"))


if __name__ == "__main__":
    unittest.main()
