import json
from pathlib import Path

from ..utils.json_utils import balanced_extract, strip_reasoning_wrapper

try:
    from ..ai.llm_client import LLMClient
except ImportError:
    LLMClient = None

from ..scanner.plugin_registry import PluginRegistry  # TASK 4.4: Dynamic Registry Integration
from ..utils.json_schema_validator import JSONSchemaValidator


class LLMEnricher:
    def __init__(
        self,
        system_prompt_path="prompts/llm_enricher_system_prompt.txt",
        generation_prompt_path="prompts/llm_enricher_generation_prompt.txt",
        schema_path="schemas/artifacts_semantic.schema.json",
        mapping_path="config/archetype_mapping.json",
        api_key: str | None = None,
    ):
        """
        Model selection is fully config-driven via project_config.json.
        Do NOT pass a model name here — change ``llm.active_provider`` and
        ``llm.providers[active].model`` in project_config.json instead.
        """

        # Load Prompts
        self.system_prompt = Path(system_prompt_path).read_text(encoding="utf-8")
        self.generation_prompt_template = Path(generation_prompt_path).read_text(encoding="utf-8")

        # TASK 4.4: Initialize Validator with the active Plugin Registry
        self.registry = PluginRegistry()
        self.validator = JSONSchemaValidator(schema_path, registry=self.registry)

        try:
            with open(Path(mapping_path), encoding="utf-8") as f:
                self.type_mapping = json.load(f)
        except Exception as e:
            print(f"[WARN] Failed to load type mapping from {mapping_path}: {e}")
            self.type_mapping = {}

        if LLMClient is None:
            raise RuntimeError("LLMClient module not found. Ensure src.ai.llm_client exists.")

        self.llm_client = LLMClient(use_reasoning_model=True, api_key=api_key)

    # -----------------------------
    # Public entry point
    # -----------------------------
    def run(self, artifacts_enriched_path, output_path, log_path=None):
        artifacts_path = Path(artifacts_enriched_path)
        output_path = Path(output_path)
        log_path = Path(log_path) if log_path else None

        if not artifacts_path.exists():
            print(f"[LLM-ENRICH] Error: Input path {artifacts_path} does not exist.")
            return

        artifacts_data = json.loads(artifacts_path.read_text(encoding="utf-8"))

        # Normalize types to satisfy strict schema ENUMS before any LLM or validation work
        artifacts_data = self._normalize_types_for_schema(artifacts_data)

        try:
            result = self._call_llm(artifacts_data)
            self._reprocess_hallucinations(result, artifacts_data)
            self.validator.validate_data(result)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(result, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"[LLM-ENRICH] Semantic artifacts written to {output_path}")

        except Exception as e:
            print(f"[LLM-ENRICH] WARNING: LLM enrichment failed, falling back to graph output: {e}")
            fallback = self._merge_semantic(artifacts_data, [])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(fallback, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    def enrich(self, artifacts_enriched_path, output_path, log_path=None):
        return self.run(artifacts_enriched_path, output_path, log_path)

    # -----------------------------
    # Normalization (Schema fix)
    # -----------------------------
    def _normalize_types_for_schema(self, data: dict) -> dict:
        """Maps specific legacy types to schema-compliant generic archetypes."""
        for art in data.get("artifacts", []):
            orig_type = art.get("type", "unknown")
            art["type"] = self.type_mapping.get(orig_type, "shared_logic")
            art["original_legacy_type"] = orig_type
        return data

    # -----------------------------
    # Payload Slimming & Chunking
    # -----------------------------
    # Root cause: large modules (267 artifacts) produce ~347 K-token payloads
    # that exceed the 250 K TPM ceiling on gpt-5-mini.  The enricher already
    # has an exception fallback, but without chunking that fallback fires
    # silently for EVERY large module, meaning all semantic signals are lost.
    # With slimming + chunking, enrichment now succeeds for large modules.
    #
    # Fields stripped (enricher prompt never references these):
    #   path, identity_heuristic, confidence, original_legacy_type,
    #   graph (full graph object -- graph_metrics has the summary), reuse.
    # Fields retained (enricher prompt explicitly uses):
    #   id, file_name, extension, type, signals, graph_metrics,
    #   calls (<=20), called_by (<=20), edges, landmine_details.

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Conservative rough estimate: len/3.5 chars-per-token."""
        return int(len(text) / 3.5)

    def _slim_artifact(self, art: dict) -> dict:
        """
        Returns a projection of an artifact that strips heavy metadata fields
        not needed for semantic enrichment, while retaining all signal fields
        the enricher prompt uses (signals, graph_metrics, edges, calls,
        called_by, landmine_details).
        Truncates calls/called_by to 20 entries to cut noise tokens.
        """
        _strip = {
            "path",
            "identity_heuristic",
            "confidence",
            "original_legacy_type",
            "graph",
            "reuse",
        }
        slim = {k: v for k, v in art.items() if k not in _strip}
        for list_field in ("calls", "called_by"):
            if isinstance(slim.get(list_field), list) and len(slim[list_field]) > 20:
                slim[list_field] = slim[list_field][:20]
        return slim

    def _chunk_artifact_list(self, artifact_list: list, token_budget: int) -> list:
        """
        Splits pre-slimmed artifact dicts into token-bounded chunks.
        Guarantees at least one artifact per chunk (degenerate guard).
        """
        chunks, current, current_tokens = [], [], 0
        for art in artifact_list:
            art_tokens = self._estimate_tokens(json.dumps(art, ensure_ascii=False))
            if current and (current_tokens + art_tokens) > token_budget:
                chunks.append(current)
                current, current_tokens = [art], art_tokens
            else:
                current.append(art)
                current_tokens += art_tokens
        if current:
            chunks.append(current)
        return chunks

    # -----------------------------
    # LLM call & Processing
    # -----------------------------
    def _call_llm(self, artifacts_json):
        # Slim & Chunk
        artifact_list = artifacts_json.get("artifacts", [])
        slimmed_list = [self._slim_artifact(a) for a in artifact_list]

        # 250K TPM ceiling - 5K prompt - 16,384 output = 228K -> use 180K (30% safety margin)
        SAFE_CHUNK_BUDGET = 180_000
        chunks = self._chunk_artifact_list(slimmed_list, SAFE_CHUNK_BUDGET)
        n_chunks = len(chunks)

        total_estimated = self._estimate_tokens(
            json.dumps({"artifacts": slimmed_list}, ensure_ascii=False)
        )
        print(
            f"[LLM-ENRICH] Calling model {self.llm_client.effective_model} on {len(artifact_list)} artifacts "
            f"(~{total_estimated:,} est. tokens, {n_chunks} batch(es))"
        )

        # Execute per batch.
        # Each call returns {id, semantic} dicts via _safe_parse_json.
        # We accumulate all of them, then merge back into the ORIGINAL
        # (unslimmed) artifact list -- only the LLM INPUT is slimmed.
        all_semantic_results = []
        for idx, chunk in enumerate(chunks, start=1):
            if n_chunks > 1:
                print(f"[LLM-ENRICH] Enrichment batch {idx}/{n_chunks} ({len(chunk)} artifacts)...")

            chunk_payload = {"artifacts": chunk}

            # "model" is passed as extra_kwarg so LLMClient.complete() routes it
            # through kwargs.update(extra_kwargs), overriding config-driven model.
            prompt = self.generation_prompt_template.replace(
                "{{ARTIFACTS_JSON}}",
                json.dumps(chunk_payload, indent=2, ensure_ascii=False),
            )
            # Model selection and token limits are resolved by LLMClient from config.
            # temperature=0.2 is passed as a named parameter; LLMClient suppresses it
            # internally when the resolved model is a reasoning model, so no provider-
            # name check is needed here.  Switching providers requires only a config change.
            raw_text = self.llm_client.complete(
                system_prompt=self.system_prompt,
                user_prompt=prompt,
                response_format={"type": "json_object"},
                temperature=0.2,
            )
            parsed_chunk = self._safe_parse_json(raw_text)
            if parsed_chunk:
                all_semantic_results.extend(parsed_chunk)

        if not all_semantic_results:
            raise RuntimeError("No semantic enrichments returned from LLM across all batches")

        # Merge all batch inferences back into the ORIGINAL (unslimmed) artifact list
        return self._merge_semantic(artifacts_json, all_semantic_results)

    def _reprocess_hallucinations(self, data: dict, original_artifacts: dict):
        """
        Safety layer to fix common LLM key hallucinations in the semantic block.
        Ensures consistency for high-risk signals like external_dependency.
        """
        original_by_id = {a["id"]: a for a in original_artifacts.get("artifacts", [])}

        for artifact in data.get("artifacts", []):
            semantic = artifact.get("semantic")

            orig_art = original_by_id.get(artifact["id"], {})
            signals = orig_art.get("signals", {})
            metrics = orig_art.get("graph_metrics", {})
            art_type = orig_art.get("type", "")

            if not semantic or not isinstance(semantic, dict):
                continue

            # Fix 1: Remap generic "reason" to schema-mandated "role_reason"
            if "reason" in semantic and "role_reason" not in semantic:
                semantic["role_reason"] = semantic.pop("reason")

            # Fix 2: Ensure "role" adheres to the Enum vocabulary
            if "role" in semantic:
                valid_roles = ["feature_anchor", "supporting", "utility"]
                val = str(semantic["role"]).lower().replace(" ", "_")
                semantic["role"] = val if val in valid_roles else "supporting"

            # Fix 3: Polyglot Headless Guardrail
            # Prevent LLM from demoting a batch entry point to "supporting".
            if art_type == "batch_anchor" and metrics.get("is_entry_point") is True:
                if semantic["role"] != "feature_anchor":
                    semantic["role"] = "feature_anchor"
                    semantic["role_reason"] = (
                        "[OVERRIDE] Deterministic graph metrics flag this batch_anchor as an entry point."
                    )

            # Fix 4: External Dependency Architectural Guardrail
            if signals.get("external_dependency") and not semantic.get("reasoning"):
                semantic["reasoning"] = (
                    "Critical: System-level bridge (OLE/DLL/Registry) detected via external_dependency signal."
                )

    # -----------------------------
    # Robust JSON parsing
    # -----------------------------
    def _safe_parse_json(self, text: str):
        text = text.strip()

        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and "artifacts" in parsed:
                return parsed["artifacts"]
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass

        # Stage 3: Strip reasoning wrappers then balanced brace extraction.
        # Replaces rfind('}') which overshoots when reasoning models append
        # explanatory prose containing {} characters after the JSON payload.
        stripped = strip_reasoning_wrapper(text).strip()
        for source in (stripped, text):
            obj_slice = balanced_extract(source, "{", "}")
            if obj_slice:
                try:
                    parsed = json.loads(obj_slice)
                    if isinstance(parsed, dict) and "artifacts" in parsed:
                        return parsed["artifacts"]
                    if isinstance(parsed, list):
                        return parsed
                except json.JSONDecodeError:
                    pass

        print("[LLM-ENRICH] WARNING: Could not parse model output as JSON.")
        return []

    def _merge_semantic(self, original_artifacts, semantic_list):
        """Combines the LLM semantic inferences with the original artifact metadata."""
        semantic_by_id = {a["id"]: a.get("semantic") for a in semantic_list if "id" in a}

        merged = []
        for art in original_artifacts["artifacts"]:
            art_copy = dict(art)
            art_id = art["id"]
            signals = art.get("signals", {})

            if art_id in semantic_by_id:
                art_copy["semantic"] = semantic_by_id[art_id]
            else:
                default_reasoning = "Default assigned due to lack of specific LLM inference."
                if signals.get("external_dependency"):
                    default_reasoning = "Default assigned; external_dependency detected but not explicitly enriched."
                art_copy["semantic"] = {
                    "role": "supporting",
                    "role_confidence": 0.5,
                    "role_reason": "Default assigned due to lack of specific LLM inference.",
                    "likely_related": None,
                    "likely_owner": None,
                    "reasoning": default_reasoning,
                }

            merged.append(art_copy)

        return {"artifacts": merged}
