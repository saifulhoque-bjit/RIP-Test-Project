"""Offline unit tests for Stage 6 (architecture_synthesis) — no LLM provider, no network."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.tools import architecture_synthesis as A  # noqa: E402

PROJECT = ROOT / "projects" / "sample_project"


def fake_llm(system_prompt, user_prompt, max_tokens):
    """Route by prompt role. Returns valid, allowlist-respecting derived JSON / system / review / narrative."""
    if "adversarial architecture reviewer" in system_prompt or "verdict" in system_prompt:
        return '<<<REVIEW>>>{"verdict":"pass","issues":[]}<<<ENDREVIEW>>>'
    if (
        "human-facing TARGET ARCHITECTURE" in system_prompt
        or "architecture review board" in system_prompt
    ):
        return "# Target Architecture\n\nGrounded narrative describing the JSON.\n"
    if (
        "platform architect" in system_prompt
        or "system_architecture" in user_prompt
        and "<<<SYS>>>" in user_prompt
    ):
        return (
            "<<<SYS>>>{"
            '"topology":"modular-monolith",'
            '"system_architecture":{'
            '"data_ownership":"schema-per-module (single PostgreSQL)",'
            '"identity":{"issuer":"in-app Auth module","token":"JWT","refresh":true,"rbac_source":"legacy admin"},'
            '"config_secrets":{"config":"Spring profiles","secrets":"AWS Secrets Manager"},'
            '"deployment":{"packaging":"single container","environments":["dev","stage","prod"]},'
            '"data_migration":{"approach":"one-time ETL","cutover":"freeze-and-migrate"},'
            '"observability":{"logging":"JSON","metrics":"Micrometer","health":true},'
            '"testing":{"unit":"JUnit 5","integration":"Testcontainers","acceptance_gate":"boots green + smoke tests pass"},'
            '"concurrency":"optimistic-locking","audit":true},'
            '"decision_ledger":['
            '{"id":"DL-101","area":"identity","decision":"in-app auth module issues JWT","rationale":"legacy admin module","grounded_in":["manifesto"],"confidence":"medium","reversibility":"hard","escalated":true},'
            '{"id":"DL-102","area":"data_ownership","decision":"schema-per-module","rationale":"monolith","grounded_in":["config"],"confidence":"high","reversibility":"hard","escalated":true},'
            '{"id":"DL-103","area":"data_migration","decision":"one-time ETL + cutover","rationale":"legacy DB","grounded_in":["evidence"],"confidence":"low","reversibility":"irreversible","escalated":true}'
            "]}<<<ENDSYS>>>"
        )
    # derive
    return (
        "<<<ARCH>>>{"
        '"data_architecture":{"entities":[{"id":"ARC-ENT-001","name":"Invoice",'
        '"source_tables":["qry_AR_Invoice"],"owning_module":"MOD-SALES-MANAGEMENT"}],'
        '"persistence":"Spring Data JPA / Hibernate","transaction_boundaries":"per service"},'
        '"nfrs":[],'
        '"codegen_directives":{"element_to_component":[{"story_element":"operation","produces":["Controller","Service"]}],'
        '"dependency_rules":["API->Application only"],"per_feature_output_layout":"one service module"},'
        '"open_questions":[{"id":"ARC-OQ-001","question":"confirm entity ownership","impact":"low"}],'
        '"traceability":[{"arc_id":"ARC-ENT-001","derived_from":["SALES-MANAGEMENT-002-F1","config"]}]'
        "}<<<ENDARCH>>>"
    )


def t_skeleton_is_config_verbatim():
    cfg = A.load_project_config(PROJECT)
    fb = A.assemble_fact_base(PROJECT, ["MOD-SALES-MANAGEMENT"], cfg)
    sk = A.build_skeleton(cfg, fb)
    ta = cfg["target_architecture"]
    assert sk["target_platform"]["tech_allowlist"] == ta["tech_allowlist"], (
        "allowlist must be verbatim from config"
    )
    assert sk["layers"] == ta["layers"], "layers must be verbatim from config"
    assert sk["component_types"] == ta["component_types"], "component_types verbatim"
    assert sk["architecture_style"] == ta["architecture_style"]
    assert len(sk["integration_contracts"]) >= 1, "integration contracts derived from context map"
    assert sk["provenance"]["project_config_sha256"], "provenance stamped"
    print("  ok: skeleton is config-verbatim + integration contracts + provenance")


def t_auto_seed_selection_is_deterministic_and_transparent():
    mods = A.load_modules(PROJECT)
    s1 = A.select_seed_modules(PROJECT, mods)
    s2 = A.select_seed_modules(PROJECT, mods)
    assert s1["seeds"] == s2["seeds"], "auto-selection must be deterministic across runs"
    assert 3 <= len(s1["seeds"]) <= 5 or len(s1["seeds"]) == len(mods), (
        "must pick 3-5 (or all if fewer)"
    )
    assert all(r.get("reason") for r in s1["rationale"]), (
        "every pick must carry a reason (transparency)"
    )
    assert "coverage" in s1 and "available" in s1["coverage"]
    # end-to-end: no seeds passed -> provenance records the auto selection
    res = A.derive_architecture(PROJECT, fake_llm, seed_modules=None, max_iters=1)
    prov = res["architecture"]["provenance"]["seed_selection"]
    assert prov["method"] == "auto" and prov["seeds"] == res["seed_modules"]
    print(
        f"  ok: auto-selected {res['seed_modules']} deterministically, rationale recorded in provenance"
    )


def t_full_run_passes_and_emits():
    res = A.derive_architecture(
        PROJECT, fake_llm, seed_modules=["MOD-SALES-MANAGEMENT"], max_iters=2
    )
    assert res["passed"], f"expected pass, review={res['review']}"
    arch = res["architecture"]
    assert arch["data_architecture"]["entities"], "generator entities merged"
    assert arch["provenance"]["status"] == "draft"
    assert Path(res["json_path"]).exists() and Path(res["md_path"]).exists()
    # nfrs kept from config (skeleton non-empty), so generator's [] did not clobber
    assert arch["nfrs"], "config nfrs preserved"
    print("  ok: full run passes, entities merged, config nfrs preserved, files emitted")


def t_tier_b_system_view_and_ledger():
    res = A.derive_architecture(
        PROJECT, fake_llm, seed_modules=["MOD-SALES-MANAGEMENT"], max_iters=1
    )
    arch = res["architecture"]
    assert arch["topology"] == "modular-monolith", "topology defaulted/decided"
    sa = arch["system_architecture"]
    # config-set identity issuer wins verbatim; data ownership present
    assert sa["identity"]["issuer"] and sa["data_ownership"], "system decisions present (runnable)"
    assert sa["testing"]["acceptance_gate"], "acceptance gate defined"
    # decision ledger recorded + high-risk escalations surfaced
    assert res["decisions"] >= 3 and res["escalations"], "ledger recorded with escalations"
    esc_areas = {e["area"] for e in res["escalations"]}
    assert {"identity", "data_ownership", "data_migration"} & esc_areas, (
        "high-risk areas escalated to human gate"
    )
    assert Path(res["ledger_path"]).exists(), "decision_ledger.json written"
    print(
        f"  ok: Tier-B system view + ledger ({res['decisions']} decisions, {len(res['escalations'])} escalated), "
        f"topology={arch['topology']}"
    )


def t_tier_b_validators_require_runnable_system():
    # identity issuance missing + monolith/per-service contradiction must be blocking
    bad = {
        "schema_version": "x",
        "provenance": {},
        "architecture_style": "layered",
        "topology": "modular-monolith",
        "target_platform": {"tech_allowlist": ["Java 21"]},
        "layers": [{"id": "L1", "name": "A", "allowed_dependencies": []}],
        "component_types": [{"name": "Ctrl", "layer": "L1"}],
        "system_architecture": {
            "identity": {},
            "data_ownership": "database-per-service",
        },  # no issuer + contradiction
        "decision_ledger": [],
    }
    issues = A.deterministic_validate(bad)
    sysblock = [i for i in issues if i["rule"] == "system" and i["severity"] == "blocking"]
    elems = {i["element"] for i in sysblock}
    assert "system_architecture.identity.issuer" in elems, "missing identity issuance not caught"
    assert any("data_ownership" in e for e in elems), (
        "monolith/per-service contradiction not caught"
    )
    print(
        "  ok: Tier-B validators block missing identity issuance + topology/data-ownership contradiction"
    )


def t_deterministic_validators_catch_breakage():
    bad = {
        "schema_version": "x",
        "provenance": {},
        "architecture_style": "layered",
        "target_platform": {"tech_allowlist": []},  # empty allowlist -> blocking
        "layers": [
            {"id": "L1", "name": "A", "allowed_dependencies": ["L2"]},
            {"id": "L2", "name": "B", "allowed_dependencies": ["L1"]},
        ],  # cycle -> blocking
        "component_types": [{"name": "Ctrl", "layer": "L9"}],  # undefined layer -> blocking
        "data_architecture": {"entities": [{"id": "ARC-ENT-9", "name": "X"}]},  # untraced -> minor
        "traceability": [],
    }
    issues = A.deterministic_validate(bad)
    rules = {i["rule"] for i in issues}
    blocking = {i["element"] for i in issues if i["severity"] == "blocking"}
    assert "feasible" in rules and "target_platform.tech_allowlist" in blocking, (
        "empty allowlist not caught"
    )
    assert any(i["problem"].startswith("layer allowed_dependencies") for i in issues), (
        "cycle not caught"
    )
    assert "Ctrl" in blocking, "undefined layer ref not caught"
    assert any(i["rule"] == "grounded" and i["severity"] == "minor" for i in issues), (
        "traceability gap not caught"
    )
    print(
        "  ok: deterministic validators catch empty-allowlist, cycle, bad layer ref, traceability gap"
    )


def t_freeze_and_versioning():
    for old in (PROJECT / "_global").glob("target_architecture.v*.json"):
        old.unlink()  # idempotent: start from a clean version series
    r1 = A.approve_architecture(PROJECT, approved_by="javed.hasan@bjitgroup.com")
    assert r1["frozen_version"] == "v1", r1
    arch = json.loads(
        (PROJECT / "_global" / "target_architecture.json").read_text(encoding="utf-8")
    )
    assert arch["provenance"]["status"] == "frozen" and arch["provenance"]["approved_by"]
    assert Path(r1["frozen_path"]).exists()
    r2 = A.approve_architecture(PROJECT, approved_by="reviewer2")
    assert r2["frozen_version"] == "v2", "re-approval must bump version"
    print("  ok: freeze stamps provenance + immutable v1, re-approval bumps to v2")


def t_export_feature_bundle():
    b = A.export_feature_bundle(PROJECT, "SALES-MANAGEMENT-002-F1")
    assert b["feature_story"]["id"] == "SALES-MANAGEMENT-002-F1"
    assert b["target_architecture"]["provenance"]["status"] == "frozen"
    assert b["srs_files"], "SRS files gathered"
    assert Path(b["bundle_path"]).exists()
    print(
        f"  ok: export bundle -> {Path(b['bundle_path']).name} "
        f"(srs={len(b['srs_files'])}, source={len(b['related_source_files'])})"
    )


if __name__ == "__main__":
    for fn in [
        t_skeleton_is_config_verbatim,
        t_auto_seed_selection_is_deterministic_and_transparent,
        t_tier_b_system_view_and_ledger,
        t_tier_b_validators_require_runnable_system,
        t_full_run_passes_and_emits,
        t_deterministic_validators_catch_breakage,
        t_freeze_and_versioning,
        t_export_feature_bundle,
    ]:
        print(f"[TEST] {fn.__name__}")
        fn()
    print("\nALL STAGE-6 TESTS PASSED")
