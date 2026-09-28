import json
from pathlib import Path

import pandas as pd


class ReportGenerator:
    """
    Phase 3: Executive Report Generator (RIP Edition)
    CRITICAL ARCHITECTURAL UPDATE: Removes all financial, hour estimation, and MEU logic.
    Focuses strictly on the core deliverables of the Requirement Intelligence Platform:
    Feature Catalog, Landmine Registry, and Functional Manifests.
    Polyglot v4.2 Update: Dynamic landmine signal extraction.
    """

    def __init__(self, project_root: str, config_path: str):
        # Resolve the absolute path to ensure we find files regardless of execution point
        self.root = Path(project_root).resolve()
        self.config = self._load_json(Path(config_path).resolve())

        self.mfu_data = []
        self.landmine_data = []
        self.manifest_data = []

    def _load_json(self, path: Path):
        if not path.exists():
            print(f"Warning: File not found at {path}")
            return {}
        with open(path, encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}

    def aggregate_results(self):
        """Crawls all module directories to collect architectural data and build the manifest."""
        modules_dir = self.root / "modules"

        if not modules_dir.exists():
            print(f"Error: Modules directory not found at {modules_dir}")
            return

        for mod_path in modules_dir.iterdir():
            if not mod_path.is_dir():
                continue

            # Use mfus_final for audit-approved metadata
            final_path = mod_path / "stage3_ai" / "mfus_final.json"

            # USE SEMANTIC/ENRICHED ARTIFACTS: Merge them safely
            semantic_path = mod_path / "stage2b_semantic" / "artifacts_semantic.json"
            enriched_path = mod_path / "stage2_graph" / "artifacts_enriched.json"

            if final_path.exists() and enriched_path.exists():
                mfus = self._load_json(final_path).get("mfus", [])
                enriched_info = self._load_json(enriched_path).get("artifacts", [])

                art_map = {a["id"]: a for a in enriched_info if "id" in a}

                # Merge semantic data safely to ensure we don't drop graph metadata
                if semantic_path.exists():
                    semantic_info = self._load_json(semantic_path).get("artifacts", [])
                    for a in semantic_info:
                        art_id = a.get("id")
                        if art_id and art_id in art_map:
                            if "semantic" in a:
                                art_map[art_id]["semantic"] = a["semantic"]
                            if "signals" in a:
                                art_map[art_id].setdefault("signals", {}).update(a["signals"])

                for mfu in mfus:
                    # 1. Architectural Feature Data
                    track = mfu.get("execution_track", "Unknown-Track")
                    complexity = mfu.get("complexity", "Unknown")
                    risk_flags = mfu.get("risk_flags", [])
                    trace_chain = mfu.get("traceability_chain", [])

                    # Identify if MFU has Surgical Integration needs
                    has_surgical_risk = (
                        "external_dependency" in risk_flags or "integration_risk" in risk_flags
                    )

                    self.mfu_data.append(
                        {
                            "Module_Name": mod_path.name,
                            "MFU_ID": mfu.get("id", "UNKNOWN"),
                            "MFU_Name": mfu.get("name", "Unnamed MFU"),
                            "Execution_Track": track,
                            "Artifact_Count": len(mfu.get("artifacts", [])),
                            "Complexity_Grade": complexity,
                            "Is_Surgical": has_surgical_risk,
                            "Primary_Risk_Flag": "Surgical Dependency"
                            if has_surgical_risk
                            else (risk_flags[0] if risk_flags else "None"),
                            "Rationale_Snippet": mfu.get("rationale", ""),
                            "Confidence_Rating": mfu.get("ai_confidence", 0),
                        }
                    )

                    # 2. Landmine Registry Data (Dynamic Polyglot extraction)
                    for art_id in mfu.get("artifacts", []):
                        art_meta = art_map.get(art_id, {})
                        signals = art_meta.get("signals", {})

                        is_surgical = signals.get("external_dependency", False) or has_surgical_risk

                        if (
                            complexity in ["Complex", "Critical"]
                            or any(signals.values())
                            or is_surgical
                        ):
                            risk_type = "Standard Technical Debt"
                            impact_level = "Increased Architectural Scrutiny"

                            if is_surgical:
                                risk_type = "Surgical Integration (DLL/API/OS/CICS)"
                                impact_level = "CRITICAL: Manual Architectural Refactor Required"

                            # Dynamically flatten whatever boolean signals the plugin found
                            active_signals = [k for k, v in signals.items() if v is True]

                            self.landmine_data.append(
                                {
                                    "Module": mod_path.name,
                                    "Artifact": art_id,
                                    "Type": art_meta.get("type", "unknown"),
                                    "Complexity": complexity,
                                    "Risk_Type": risk_type,
                                    "Impact_Statement": impact_level,
                                    "Active_Signals": ", ".join(active_signals)
                                    if active_signals
                                    else "None",
                                    "External_Dep": is_surgical,
                                }
                            )

                    # 3. Functional Manifest Data
                    anchor = trace_chain[0] if trace_chain else "Multiple Artifacts"
                    warning = (
                        f"Risk Flags Detected: {', '.join(risk_flags)}"
                        if risk_flags
                        else "Check Landmine Registry"
                    )

                    self.manifest_data.append(
                        {
                            "module": mod_path.name,
                            "mfu_id": mfu.get("id", "UNKNOWN"),
                            "name": mfu.get("name", "Unnamed MFU"),
                            "track": track,
                            "intent": mfu.get("rationale", "N/A"),
                            "steps": trace_chain,
                            "anchor": anchor,
                            "warning": warning,
                            "artifacts": mfu.get("artifacts", []),
                        }
                    )

    def generate_functional_manifest(self, output_dir: Path):
        """Creates the Markdown guide for developer onboarding based on traceability chains."""
        if not self.manifest_data:
            return

        lines = [
            "# Functional Logic Manifest\n",
            "## Onboarding Guide for Modernization Developers\n",
        ]
        df = pd.DataFrame(self.manifest_data)
        for mod_name, group in df.groupby("module"):
            lines.append(f"## Module: {mod_name}")
            lines.append("---")
            for _, row in group.iterrows():
                lines.append(f"### {row['mfu_id']}: {row['name']} [{row['track']}]")
                lines.append(f"**Business Intent:** {row['intent']}\n")
                if row["steps"]:
                    lines.append("**Execution / Traceability Path:**")
                    for step in row["steps"]:
                        lines.append(f"1. `{step}`")
                lines.append(f"\n**Primary Data Anchor:** `{row['anchor']}`")
                func_utils = [a for a in row["artifacts"] if "func" in a.lower()]
                if func_utils:
                    lines.append("**Global Utility Dependencies (`func` module):**")
                    for u in func_utils:
                        lines.append(f"- `{u}`")
                lines.append(f"\n> **Architectural Warning:** {row['warning']}\n")
            lines.append("\n")

        output_path = output_dir / "3_functional_manifest.md"
        output_path.write_text("\n".join(lines), encoding="utf-8")

    def export_all(self):
        if not self.mfu_data:
            print(
                "No data collected. Reports will not be generated. Ensure pipeline ran successfully."
            )
            return

        df_mfu = pd.DataFrame(self.mfu_data)
        output_dir = self.root / "reports"
        output_dir.mkdir(exist_ok=True)

        # Report 1: Feature Catalog
        df_mfu.to_csv(output_dir / "1_feature_requirements_catalog.csv", index=False)

        # Report 2: Landmine Registry
        if self.landmine_data:
            pd.DataFrame(self.landmine_data).drop_duplicates().to_csv(
                output_dir / "2_technical_landmine_registry.csv", index=False
            )

        # Report 3: Functional Manifest
        self.generate_functional_manifest(output_dir)


if __name__ == "__main__":
    gen = ReportGenerator("projects/sample_project", "project_config.json")
    gen.aggregate_results()
    gen.export_all()
    print("Success! Reports generated in projects/sample_project/reports")
