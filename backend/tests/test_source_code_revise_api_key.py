"""Feedback-driven source-code regeneration (Stage 5 revise) must call the LLM with
the project's tenant API key, the same key the original ingestion used — not fall
back to a provider environment variable the deployment may not set.
"""

from __future__ import annotations

from app.services.source_code_pipeline.pipeline_orchestrator import (
    PipelineContext,
    PipelineOrchestrator,
)
from app.services.source_code_pipeline.src.ai import llm_client

_TENANT_KEY = "tenant-key-for-test"
_PROVIDER_ENV_KEYS = ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY")


def _revise_input() -> dict:
    return {
        "module_id": "MOD-A",
        "mfu_id": "MFU-001",
        "mode": "edit",
        "source_paradigm": "pb",
        "feedback_spec": {
            "module": "MOD-A",
            "mfu": "MFU-001",
            "entries": [
                {
                    "story_id": "MOD-A-F1-S1",
                    "items": [{"quoted_text": None, "feedback_text": "Make the title clearer"}],
                }
            ],
        },
        "srs_files": [
            {
                "module_id": "MOD-A",
                "feature_unit_id": "MFU-001",
                "filename": "w_login_UIBlueprint.md",
                "content": "# UIBlueprint w_login\n## S.5 Events\nclicked: login",
            }
        ],
        "module_manifest": {"modules": [{"module_id": "MOD-A", "module_name": "A"}]},
        "features_stories": {
            "features": [
                {
                    "id": "MOD-A-F1",
                    "title": "Login",
                    "user_stories": [{"id": "MOD-A-F1-S1", "title": "Log in"}],
                }
            ]
        },
    }


def test_revise_calls_llm_with_tenant_api_key(tmp_path, monkeypatch):
    for env_key in _PROVIDER_ENV_KEYS:
        monkeypatch.delenv(env_key, raising=False)
    # set_project_paths() writes this env var directly; register it so it is restored.
    monkeypatch.setenv("RIP_LLM_CONFIG_PATH", "")
    keys_used: list[str | None] = []

    def _record_key(self, *args, **kwargs):
        keys_used.append(self._api_key)
        raise RuntimeError("LLM stubbed in test")

    monkeypatch.setattr(llm_client.LLMClient, "complete", _record_key)

    workspace = tmp_path / "workspace"
    input_dir = workspace / "input"
    input_dir.mkdir(parents=True)
    config_path = PipelineOrchestrator.configure_project(
        project_dir=workspace,
        source_paradigm="pb",
        options={"llm_provider": "deepseek", "llm_model": "deepseek-v4-pro"},
    )
    # Stage 5 resolves SRS paths relative to the working directory, as the worker does.
    monkeypatch.chdir(tmp_path)
    pipeline = PipelineOrchestrator(
        PipelineContext(
            project_dir=str(workspace),
            config_path=str(config_path),
            source_dir=str(input_dir),
            skip_processing=False,
            llm_api_key=_TENANT_KEY,
        )
    )

    pipeline.process_mfu_revise_with_reconstruction(_revise_input())

    assert keys_used, "revise never reached the LLM client"
    assert set(keys_used) == {_TENANT_KEY}
