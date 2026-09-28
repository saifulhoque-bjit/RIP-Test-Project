"""
RIP Automated Regression Pass & Quality Verification Gate.
Task 5.5: Validates 100% Traceability and Naming Parity between
structural JSON configuration maps and generated Markdown output specifications.
"""

import json
from pathlib import Path
import sys


class ParityValidator:
    def __init__(self, project_dir: str):
        self.project_root = Path(project_dir)
        self.modules_root = self.project_root / "modules"
        self.global_error_count = 0

    def run_regression_pass(self) -> int:
        print("\n================================================================================")
        print("      RIP REGRESSION AUDIT GATE: STARTING 100% PARITY VALIDATION SWEEP")
        print("================================================================================")
        print(f"[AUDIT] Project Directory Target: {self.project_root}")
        print(f"[AUDIT] Modules Directory Target: {self.modules_root}\n")

        if not self.modules_root.exists():
            print(f"[CRITICAL ERROR] Target modules directory does not exist: {self.modules_root}")
            return 1

        module_dirs = sorted(
            [d for d in self.modules_root.iterdir() if d.is_dir()], key=lambda x: x.name.lower()
        )

        if not module_dirs:
            print(
                f"[WARN] No extracted modules found inside {self.modules_root.name}. Parity gate skipped."
            )
            return 0

        for mod_path in module_dirs:
            self._validate_module_specs(mod_path)

        print("\n================================================================================")
        print("                      RIP REGRESSION AUDIT SWEEP SUMMARY")
        print("================================================================================")
        if self.global_error_count == 0:
            print(
                "[PASS] 100% Traceability and Naming Parity verified successfully across all modules."
            )
            print("[PASS] Quality Verification Gate: APPROVED")
            print(
                "================================================================================\n"
            )
            return 0
        else:
            print(
                f"[FAIL] Discovered {self.global_error_count} naming desynchronization desync faults."
            )
            print("[FAIL] Quality Verification Gate: REJECTED")
            print(
                "================================================================================\n"
            )
            return 1

    def _validate_module_specs(self, module_path: Path):
        module_id = module_path.name.upper()
        specs_dir = module_path / "stage4_specs"

        if not specs_dir.exists():
            print(
                f"  [SKIP] Module {module_id}: Spec extraction folder missing (stage4_specs not found)."
            )
            return

        config_map_path = specs_dir / "config_naming_map.json"
        if not config_map_path.exists():
            print(
                f"  [FAIL] Module {module_id}: Critical configuration metadata file 'config_naming_map.json' is missing."
            )
            self.global_error_count += 1
            return

        # 1. Ingest the Single Source of Truth JSON Naming Map
        try:
            with open(config_map_path, encoding="utf-8") as f:
                config_data = json.load(f)
        except Exception as e:
            print(
                f"  [FAIL] Module {module_id}: Failed to deserialize 'config_naming_map.json'. Detail: {e}"
            )
            self.global_error_count += 1
            return

        naming_map = config_data.get("naming_map", {})
        if not naming_map:
            print(
                f"  [INFO] Module {module_id}: 'config_naming_map.json' contains an empty 'naming_map' block. Skipping element scans."
            )
            return

        target_elements: set[str] = set(naming_map.keys())
        print(
            f"  [AUDIT] Module {module_id}: Evaluating {len(target_elements)} elements against generated specs..."
        )

        # 2. Ingest and concatenate all Markdown Specification Text Layers for this module
        markdown_specs: list[Path] = list(specs_dir.glob("**/*.md"))
        if not markdown_specs:
            print(
                f"  [FAIL] Module {module_id}: Spec folder exists but contains 0 generated Markdown blueprint files."
            )
            self.global_error_count += 1
            return

        combined_spec_content_lines = []
        for spec_path in markdown_specs:
            try:
                content = spec_path.read_text(encoding="utf-8")
                combined_spec_content_lines.append(content)
            except Exception as e:
                print(f"    [ERROR] Failed to read spec file asset {spec_path.name}: {e}")

        combined_spec_text = "\n\n".join(combined_spec_content_lines)

        # 3. Perform 1:1 Substring Validation Verification Passes
        missing_elements = []
        for element_key in sorted(list(target_elements)):
            # Normalize comparisons to insulate matching loops against small whitespace anomalies
            clean_key = element_key.strip()

            # Check if the exact structural metadata variable trace token exists inside the spec text
            if clean_key not in combined_spec_text:
                missing_elements.append(element_key)

        # 4. Report Discrepancy Findings
        if not missing_elements:
            print(
                f"    [OK] Parity Match Confirmed: All {len(target_elements)} components match specification proofs."
            )
        else:
            print(f"    [DESYNC FAULT] Traceability Gap Discovered inside Module {module_id}!")
            print(
                "    [DESYNC FAULT] The following keys exist in JSON tracking maps but were dropped from Markdown specifications:"
            )
            for dropped_key in missing_elements:
                print(f"      - Drop Trace: '{dropped_key}'")
                self.global_error_count += 1


def main():
    # Support dynamic execution configuration via directory path arg lookups
    project_target = "projects/sample_project"

    if len(sys.argv) > 1:
        if not sys.argv[1].startswith("-"):
            project_target = sys.argv[1]

    # Allow passing project path via standard CLI arguments
    for i, arg in enumerate(sys.argv):
        if arg == "--project" and i + 1 < len(sys.argv):
            project_target = sys.argv[i + 1]

    validator = ParityValidator(project_target)
    exit_code = validator.run_regression_pass()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
