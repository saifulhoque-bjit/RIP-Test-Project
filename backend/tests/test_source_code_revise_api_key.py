"""P1821-517: source-code feedback (revise) must use the tenant LLM API key.

Real code exercised: SourceCodePipelineService.revise_mfu_with_reconstruction ->
revise_mfu -> cli.main.cmd_revise -> FeatureStoryAgent.__init__ -> LLMClient.__init__.

Dependency stubs (labelled):
  * LITELLM_AVAILABLE is forced True so LLMClient can be constructed without litellm.
  * _reconstruct_revise_workspace is replaced by a minimal MFU directory builder.
  * FeatureStoryAgent.run_for_mfu is replaced by a probe that records the key held by the
    agent's Stage 5 LLM client and fails like a provider auth error when there is none.
No real LLM call is made; end-to-end coverage against a live provider is not provided here.
"""

from __future__ import annotations

import json

import pytest

from app.services.source_code_pipeline import source_code_pipeline_service as svc_mod
from app.services.source_code_pipeline.src.ai import llm_client as llm_mod
from app.services.source_code_pipeline.src.tools import feature_story_agent as fsa_mod

FAKE_TENANT_KEY = "tenant-test-key-not-real"
FAKE_ENV_KEY = "env-test-key-not-real"
MODULE_ID = "MOD-TEST"
MFU_ID = "MFU-001"


@pytest.fixture
def revise_env(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_mod, "LITELLM_AVAILABLE", True)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("RIP_LLM_CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "project_config.json").write_text(json.dumps({}), encoding="utf-8")

    def fake_reconstruct(self, revise_input, project_root):
        mfu_dir = project_root / "modules" / MODULE_ID / "stage4_specs" / MFU_ID
        mfu_dir.mkdir(parents=True, exist_ok=True)
        return {"mfu_dir": mfu_dir, "project_root": project_root}

    monkeypatch.setattr(
        svc_mod.SourceCodePipelineService, "_reconstruct_revise_workspace", fake_reconstruct
    )

    seen: dict = {}

    def probe_run_for_mfu(self, mfu_dir, **kwargs):
        seen["agent_api_key"] = self.api_key
        seen["llm_api_key"] = self._stage5_llm._api_key
        if not self._stage5_llm._api_key:
            raise RuntimeError("AuthenticationError: no API key for provider 'openai'")
        doc = {"features": [], "generation_metadata": {"final_status": "PASS"}}
        (mfu_dir / "features_stories.json").write_text(json.dumps(doc), encoding="utf-8")
        return doc

    monkeypatch.setattr(fsa_mod.FeatureStoryAgent, "run_for_mfu", probe_run_for_mfu)

    orig_init = svc_mod.SourceCodePipelineService.__init__

    def recording_init(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        seen.setdefault("service_api_keys", []).append(self.api_key)

    monkeypatch.setattr(svc_mod.SourceCodePipelineService, "__init__", recording_init)

    def run(api_key):
        service = svc_mod.SourceCodePipelineService(
            project_dir=str(tmp_path), api_key=api_key
        )
        result = service.revise_mfu_with_reconstruction(
            {
                "module_id": MODULE_ID,
                "mfu_id": MFU_ID,
                "mode": "regenerate",
                "feedback": "Clarify the acceptance criteria.",
            }
        )
        return result, seen

    return run


def test_tenant_key_reaches_reconstructed_service_cli_and_llm(revise_env):
    result, seen = revise_env(FAKE_TENANT_KEY)

    assert seen["service_api_keys"] == [FAKE_TENANT_KEY, FAKE_TENANT_KEY]
    assert seen["agent_api_key"] == FAKE_TENANT_KEY
    assert seen["llm_api_key"] == FAKE_TENANT_KEY
    assert result["status"] == "success", result


def test_env_key_used_when_no_tenant_key(revise_env, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_ENV_KEY)

    result, seen = revise_env(None)

    assert seen["agent_api_key"] is None
    assert seen["llm_api_key"] == FAKE_ENV_KEY
    assert result["status"] == "success", result


def test_missing_key_failure_is_visible(revise_env):
    result, seen = revise_env(None)

    assert seen["llm_api_key"] is None
    assert result["status"] == "failed"
    assert "AuthenticationError" in result["error"]
