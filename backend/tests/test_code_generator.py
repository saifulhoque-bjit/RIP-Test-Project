"""Unit tests for app.utils.code_generator."""

from __future__ import annotations

from app.utils.code_generator import bump_code_suffix, generate_tenant_code_candidate


class TestGenerateTenantCodeCandidate:
    def test_multi_word_name_uses_initials(self) -> None:
        assert generate_tenant_code_candidate("Acme Corp") == "AC"

    def test_caps_initials_at_four_words(self) -> None:
        assert generate_tenant_code_candidate("Bjit Group Software Solutions Ltd") == "BGSS"

    def test_single_word_name_uses_leading_characters(self) -> None:
        assert generate_tenant_code_candidate("Acme") == "ACME"

    def test_single_word_longer_than_max_is_truncated(self) -> None:
        assert generate_tenant_code_candidate("Megacorporation") == "MEGACO"

    def test_strips_non_alnum_characters(self) -> None:
        assert generate_tenant_code_candidate("Acme, Corp.") == "AC"

    def test_single_word_pads_short_result(self) -> None:
        assert generate_tenant_code_candidate("A") == "AX"

    def test_degenerate_name_falls_back_to_placeholder(self) -> None:
        assert generate_tenant_code_candidate("!!!") == "TN"

    def test_result_is_always_uppercase(self) -> None:
        assert generate_tenant_code_candidate("acme corp") == "AC"


class TestBumpCodeSuffix:
    def test_appends_attempt_number(self) -> None:
        assert bump_code_suffix("AC", 2) == "AC2"
        assert bump_code_suffix("AC", 3) == "AC3"
