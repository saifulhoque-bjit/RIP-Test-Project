import json
import os
from pathlib import Path

from ..scanner.plugin_registry import PluginRegistry  # TASK 4.4: Dynamic Registry Integration
from ..utils.json_schema_validator import JSONSchemaValidator, SchemaValidationError
from ..utils.json_utils import extract_llm_json
from ..utils.vocabulary_sanitizer import SYSTEM_RISK_FLAGS, sanitize_mfu_vocabulary

try:
    from ..ai.llm_client import LLMClient
except ImportError:
    LLMClient = None


class ReviewAgent:
    """
    Post-processing LLM agent that audits MFU architectural requirements quality.
    Input: mfus_proposed.json + artifacts_enriched.json (for evidence)
    Output: mfus_final.json (corrected + reviewed)

    TASK 11: Headless Guardrails & Day 4 Multi-Track Integration
    - Removed legacy paradigm hardcodes.
    - Added Track-aware orchestrator validation (UI-Track, Batch-Track, Mixed-Track).
    - Recognition of 'batch_anchor' as a valid core component for Headless workflows.

    CRITICAL FIXES (RIP Update):
    - Replaced basic track mapping with robust dynamic schema enum normalization.
    - Injected strict JSON schema directly into LLM prompt to prevent structural hallucination.
    - Defensive normalization dynamically hydrated from PluginRegistry.
    - Provider-agnostic LLM integration with forced reasoning tier for maximum fidelity.
    """

    def __init__(
        self,
        schema_path: Path,
        system_prompt_path: Path,
        api_key: str | None = None,
    ):
        """
        Parameters
        ----------
        schema_path : Path
            JSON schema for the final MFU output.
        system_prompt_path : Path
            Path to the review agent system prompt.

        Model selection is fully config-driven via project_config.json.
        Do NOT pass a model name here — change ``llm.active_provider`` and
        ``llm.providers[active].reasoning_model`` in project_config.json instead.
        ReviewAgent always uses the provider's reasoning model (use_reasoning_model=True).
        """
        self.schema_path = schema_path
        self.system_prompt_path = system_prompt_path

        # TASK 4.4: Initialize Validator with the active Plugin Registry
        self.registry = PluginRegistry()
        self.validator = JSONSchemaValidator(schema_path, registry=self.registry)

        # SYSTEM_RISK_FLAGS: single source of truth from vocabulary_sanitizer.py.
        # Replaces the previously divergent local definition — adding a new
        # system flag in vocabulary_sanitizer.SYSTEM_RISK_FLAGS is sufficient;
        # this assignment picks it up automatically.
        self.system_flags = SYSTEM_RISK_FLAGS

        if LLMClient is None:
            raise RuntimeError("LLMClient module not found. Ensure src.ai.llm_client exists.")

        # Enterprise Check: If no keys exist for any supported provider, fallback to pass-through mode
        has_any_key = any(
            os.getenv(k)
            for k in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY"]
        )
        if not has_any_key and not api_key:
            print(
                "[WARN] No AI Provider API Keys detected in environment. Review Agent will run in PASS-THROUGH (Mock) mode."
            )
            self.llm_client = None
        else:
            # Target the heavy-path (reasoning) client for strict architectural auditing
            self.llm_client = LLMClient(use_reasoning_model=True, api_key=api_key)

    def _prepare_evidence_context(self, mfus_data: dict, artifacts_path: Path) -> dict:
        artifacts = json.loads(artifacts_path.read_text(encoding="utf-8"))
        evidence = {}

        for art in artifacts.get("artifacts", []):
            evidence[art["id"]] = {
                "type": art.get("type"),
                "signals": art.get("signals", {}),
                "graph_metrics": art.get("graph_metrics", {}),
                "edges": art.get("edges", []),
                "landmine_details": art.get("landmine_details", {}),
            }
        return evidence

    def _enforce_root_structure(self, result) -> dict:
        """
        Safety layer: If the LLM returns a naked list [{}, {}] instead of
        the schema-mandated {"mfus": [{}, {}]}, this wraps it safely to prevent
        AttributeErrors downstream.
        """
        if isinstance(result, list):
            print("[Review Agent] Auto-corrected root array hallucination.")
            return {"mfus": result}
        if not isinstance(result, dict):
            return {"mfus": []}
        return result

    def _enforce_schema_enums(self, reviewed_data: dict) -> dict:
        """
        Forces AI output tracks, risk flags, and complexity values into the strict enums
        required by the dynamically hydrated schema.
        """
        if hasattr(self.registry, "get_all_execution_tracks"):
            valid_tracks = set(self.registry.get_all_execution_tracks())
        else:
            valid_tracks = {"UI-Track", "Batch-Track", "Mixed-Track"}

        # Combine Plugin technical flags + Sweeper operational flags
        valid_risks = set(self.registry.get_all_risk_flags()).union(self.system_flags)

        track_map = {
            "ui-track": "UI-Track",
            "batch-track": "Batch-Track",
            "mixed-track": "Mixed-Track",
            "ui": "UI-Track",
            "batch": "Batch-Track",
            "mixed": "Mixed-Track",
            "frontend": "UI-Track",
            "backend": "Batch-Track",
        }

        valid_complexities = {"Simple", "Medium", "Complex", "Critical"}
        complexity_map = {
            "low": "Simple",
            "moderate": "Medium",
            "high": "Complex",
            "extreme": "Critical",
            "severe": "Critical",
        }

        for mfu in reviewed_data.get("mfus", []):
            # Normalize Track
            raw_track = mfu.get("execution_track", "")
            if raw_track not in valid_tracks:
                normalized_track = track_map.get(str(raw_track).lower().strip(), "Mixed-Track")
                mfu["execution_track"] = (
                    normalized_track if normalized_track in valid_tracks else list(valid_tracks)[0]
                )
                print(
                    f"[REVIEW_AGENT] Auto-corrected invalid track '{raw_track}' to '{mfu['execution_track']}' for {mfu.get('id')}"
                )

            # Normalize Complexity
            raw_comp = mfu.get("complexity", "")
            if raw_comp not in valid_complexities:
                normalized_comp = complexity_map.get(str(raw_comp).lower().strip(), "Medium")
                mfu["complexity"] = normalized_comp
                print(
                    f"[REVIEW_AGENT] Auto-corrected invalid complexity '{raw_comp}' to '{normalized_comp}' for {mfu.get('id')}"
                )

            # Heal Risk Flags Array
            raw_risks = mfu.get("risk_flags", [])
            if isinstance(raw_risks, list):
                clean_risks = []
                for r in raw_risks:
                    r_clean = str(r).lower().replace(" ", "_").replace("-", "_")
                    if r_clean in valid_risks:
                        clean_risks.append(r_clean)
                    # Heuristics mapped only if target exists in valid active registry
                    elif (
                        "db" in r_clean or "database" in r_clean or "sql" in r_clean
                    ) and "heavy_db_logic" in valid_risks:
                        clean_risks.append("heavy_db_logic")
                    elif (
                        "integration" in r_clean or "api" in r_clean
                    ) and "integration_risk" in valid_risks:
                        clean_risks.append("integration_risk")

                mfu["risk_flags"] = list(set(clean_risks))

        return reviewed_data

    def _enforce_signal_integrity(self, reviewed_data: dict, evidence_context: dict):
        """
        The Hard Override:
        If the scanner saw an external dependency or transaction, the reviewer is
        NOT allowed to remove the risk flag. (Assuming they are registered).
        """
        valid_risks = set(self.registry.get_all_risk_flags())

        for mfu in reviewed_data.get("mfus", []):
            # 1. TYPE-SAFE RISK EXTRACTION (Prevents 'unhashable dict' crash)
            raw_risks = mfu.get("risk_flags", [])
            mfu_risks = set()
            if isinstance(raw_risks, list):
                for r in raw_risks:
                    if isinstance(r, str):
                        mfu_risks.add(r)
                    elif isinstance(r, dict) and "flag" in r:
                        mfu_risks.add(r["flag"])

            # 2. TYPE-SAFE ARTIFACT EXTRACTION (Prevents evidence lookup crash)
            raw_artifacts = mfu.get("artifacts", [])
            if isinstance(raw_artifacts, list):
                for art in raw_artifacts:
                    # Flatten LLM object hallucination (e.g. [{"id": "foo"}] to "foo")
                    art_id = (
                        art
                        if isinstance(art, str)
                        else art.get("id")
                        if isinstance(art, dict)
                        else str(art)
                    )

                    if not isinstance(art_id, str):
                        continue

                    art_evidence = evidence_context.get(art_id, {})
                    signals = art_evidence.get("signals", {})

                    # Rule 1: Transaction Integrity
                    if (
                        signals.get("transaction_control")
                        and "transaction_control" in valid_risks
                        and "transaction_control" not in mfu_risks
                    ):
                        mfu_risks.add("transaction_control")

                    # Rule 2: External Boundary Integrity
                    if (
                        signals.get("external_dependency")
                        and "external_dependency" in valid_risks
                        and "external_dependency" not in mfu_risks
                    ):
                        mfu_risks.add("external_dependency")

            mfu["risk_flags"] = list(mfu_risks)

    def _call_llm(self, mfus_data: dict, evidence_context: dict) -> dict:
        if not self.llm_client:
            # Mock Pass-Through: Just auto-approve everything
            print("[Review Agent] Operating in PASS-THROUGH mode...")
            for mfu in mfus_data.get("mfus", []):
                mfu["review"] = {
                    "reviewed_by": "Pass-Through Mock",
                    "review_notes": "Skipped LLM review due to missing API key.",
                    "status": "approved",
                }
            return mfus_data

        sys_prompt = self.system_prompt_path.read_text(encoding="utf-8")

        # Inject the actual JSON schema into the system prompt to force structural compliance
        schema_text = self.schema_path.read_text(encoding="utf-8")
        sys_prompt += f"\n\n### MANDATORY JSON SCHEMA ###\nYour output MUST validate against this JSON Schema:\n{schema_text}"

        payload = {"proposal_to_review": mfus_data, "raw_scanner_evidence": evidence_context}

        print(
            f"[Review Agent] Auditing MFU boundaries and requirements using {self.llm_client.active_reasoning_model}..."
        )

        # Model selection, token limits, and temperature are resolved entirely by
        # LLMClient from project_config.json.  ReviewAgent was constructed with
        # use_reasoning_model=True so LLMClient automatically uses the provider's
        # reasoning model and its max_completion_tokens ceiling.
        # No model names or token counts are hardcoded here.
        raw_content = self.llm_client.complete(
            system_prompt=sys_prompt,
            user_prompt=f"AUDIT PAYLOAD:\n{json.dumps(payload, indent=2)}",
            response_format={"type": "json_object"},
        )
        return self._extract_json(raw_content)

    def _extract_json(self, text: str) -> dict:
        """
        Delegates to the shared 4-stage extraction pipeline in json_utils.
        Handles reasoning-model trailing prose, <think> blocks, code fences,
        and balanced brace extraction — see json_utils.extract_llm_json.
        """
        return extract_llm_json(
            text,
            caller_label="Review Agent",
            empty_error_checklist=(
                "  1. System prompt contains the word 'JSON' (required for json_object mode).\n"
                "  2. Audit payload size is within the model context window.\n"
                "  3. API key is valid and has remaining quota.\n"
                "  4. Check [LLMClient] finish_reason in the log above for the exact cause."
            ),
        )

    def _strip_unknown_mfu_properties(self, reviewed: dict) -> dict:
        """
        Deterministic heal (no LLM): remove keys the model added to MFU items that the
        schema forbids (``additionalProperties: false``), so a stray annotation — e.g.
        Sonnet 5 emitting ``"ai_confidence_note": null`` — does NOT hard-fail schema
        validation and abort the whole run.

        Mirrors AIRunner._sanitize_extra_properties. Zero data loss: a non-empty string
        extra is appended onto ``rationale`` (preserving the model's intent); null/empty
        or non-string extras are dropped. Allowed keys are derived from the loaded schema
        (self-maintaining), with a safe hardcoded fallback.
        """
        mfus = reviewed.get("mfus")
        if not isinstance(mfus, list):
            return reviewed
        try:
            allowed = set(self.validator.schema["properties"]["mfus"]["items"]["properties"].keys())
        except Exception:
            allowed = {
                "id",
                "name",
                "description",
                "execution_track",
                "artifacts",
                "traceability_chain",
                "complexity",
                "risk_flags",
                "ai_confidence",
                "rationale",
                "review",
            }
        for idx, mfu in enumerate(mfus):
            if not isinstance(mfu, dict):
                continue
            extras = [k for k in list(mfu.keys()) if k not in allowed]
            if not extras:
                continue
            print(
                f"[Review Agent] [SANITIZE] {mfu.get('id', f'idx-{idx + 1}')}: removing "
                f"{len(extras)} non-schema key(s): {extras}"
            )
            rationale = mfu.get("rationale", "")
            if not isinstance(rationale, str):
                rationale = str(rationale)
            for ek in extras:
                val = mfu.pop(ek)
                if isinstance(val, str) and val.strip():
                    rationale = (rationale + " " + val).strip()
            if rationale:
                mfu["rationale"] = rationale
        return reviewed

    def run(
        self,
        mfus_input_path: Path,
        artifacts_enriched_path: Path,
        output_path: Path,
        log_path: Path,
    ):
        """
        Executes the review agent, reading from and writing to the filesystem.
        """
        mfus_data = json.loads(mfus_input_path.read_text(encoding="utf-8"))
        evidence_context = self._prepare_evidence_context(mfus_data, artifacts_enriched_path)

        raw_reviewed = self._call_llm(mfus_data, evidence_context)

        # Apply Structural Healing
        reviewed = self._enforce_root_structure(raw_reviewed)

        # Apply the Active Guardrail
        self._enforce_signal_integrity(reviewed, evidence_context)

        # Apply Sanitization
        reviewed = sanitize_mfu_vocabulary(reviewed)

        # Normalize Track, Complexity, and Risk Flags Enums to Defend Against Hallucination
        reviewed = self._enforce_schema_enums(reviewed)

        # Strip any non-schema keys the model added (additionalProperties:false guard) —
        # deterministic, zero-loss. Prevents a stray key (e.g. 'ai_confidence_note') from
        # hard-failing validation and aborting the run.
        reviewed = self._strip_unknown_mfu_properties(reviewed)

        try:
            self.validator.validate_data(reviewed)
        except SchemaValidationError as e:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(
                f"--- REVIEW SCHEMA VALIDATION FAILED ---\n\n{e}\n\n", encoding="utf-8"
            )
            raise RuntimeError(f"Review Agent Output failed schema validation:\n{e}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(reviewed, indent=2, ensure_ascii=False), encoding="utf-8")

        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            f"--- REVIEW SUCCESS ---\n\n{json.dumps(reviewed, indent=2)}", encoding="utf-8"
        )
