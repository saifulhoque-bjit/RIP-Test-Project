"""Unit tests for the Neo4j SettingRepository."""

from __future__ import annotations

import json

from app.repositories.neo4j.setting_repository import SettingRepository


class _FakeResult:
    def __init__(self, single_result):
        self._single_result = single_result

    def single(self):
        return self._single_result


class _FakeTx:
    def __init__(self, single_result, captured_runs):
        self._single_result = single_result
        self._captured_runs = captured_runs

    def run(self, cypher, **params):
        self._captured_runs.append((cypher, params))
        return _FakeResult(self._single_result)


class _FakeSession:
    def __init__(self, single_result, captured_runs):
        self._single_result = single_result
        self._captured_runs = captured_runs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return None

    def execute_read(self, callback):
        return callback(_FakeTx(self._single_result, self._captured_runs))

    def execute_write(self, callback):
        return callback(_FakeTx(self._single_result, self._captured_runs))


class _FakeDriver:
    def __init__(self, single_result, captured_runs=None):
        self._single_result = single_result
        self._captured_runs = captured_runs if captured_runs is not None else []

    def session(self):
        return _FakeSession(self._single_result, self._captured_runs)


def _full_record(**overrides) -> dict:
    record = {
        "llm_providers": [json.dumps({"id": "anthropic", "name": "Anthropic", "models": []})],
        "source_languages": [json.dumps({"key": "pb", "value": "Power Builder"})],
        "frontend_stacks": [],
        "backend_stacks": [],
        "database_stacks": [],
        "infrastructure_stacks": [],
        "architecture_stacks": [],
    }
    record.update(overrides)
    return record


class TestGetSetting:
    def test_returns_none_when_no_node(self):
        repo = SettingRepository(_FakeDriver(None))
        assert repo.get_setting() is None

    def test_returns_parsed_schema(self):
        repo = SettingRepository(_FakeDriver(_full_record()))

        result = repo.get_setting()

        assert result is not None
        assert result.project.llm_providers[0].id == "anthropic"
        assert result.source_code_pipeline.source_languages[0].key == "pb"


class TestSeedDefaultSetting:
    def test_seeds_and_returns_schema(self):
        captured_runs = []
        driver = _FakeDriver(_full_record(), captured_runs)
        repo = SettingRepository(driver)

        result = repo.seed_default_setting()

        assert result.project.llm_providers[0].id == "anthropic"
        cypher, params = captured_runs[0]
        assert "MERGE (s:Setting" in cypher
        assert isinstance(params["llm_providers"][0], str)


class TestNormalizeLlmProviders:
    def test_non_list_returns_empty(self):
        assert SettingRepository._normalize_llm_providers(None) == []
        assert SettingRepository._normalize_llm_providers("not a list") == []

    def test_parses_json_string_entries(self):
        raw = [json.dumps({"id": "anthropic", "name": "Anthropic", "models": []})]
        result = SettingRepository._normalize_llm_providers(raw)
        assert len(result) == 1
        assert result[0].id == "anthropic"

    def test_skips_invalid_entries(self):
        raw = [json.dumps({"id": "", "name": "No id"}), "not json", json.dumps([1, 2])]
        assert SettingRepository._normalize_llm_providers(raw) == []


class TestParseLlmProvider:
    def test_missing_id_or_name_returns_none(self):
        assert SettingRepository._parse_llm_provider(json.dumps({"id": "", "name": "x"})) is None
        assert SettingRepository._parse_llm_provider(json.dumps({"id": "x", "name": ""})) is None

    def test_valid_provider_with_models(self):
        payload = json.dumps(
            {
                "id": "anthropic",
                "name": "Anthropic",
                "models": [{"id": "m1", "name": "Model 1"}, {"id": "", "name": "skip"}],
            }
        )
        provider = SettingRepository._parse_llm_provider(payload)
        assert provider.id == "anthropic"
        assert len(provider.models) == 1
        assert provider.models[0].id == "m1"

    def test_non_dict_json_returns_none(self):
        assert SettingRepository._parse_llm_provider(json.dumps([1, 2])) is None


class TestDecodeJsonItem:
    def test_non_string_passthrough(self):
        assert SettingRepository._decode_json_item({"a": 1}) == {"a": 1}

    def test_empty_string_returns_none(self):
        assert SettingRepository._decode_json_item("   ") is None

    def test_invalid_json_returns_none(self):
        assert SettingRepository._decode_json_item("not json") is None

    def test_valid_json_decoded(self):
        assert SettingRepository._decode_json_item('{"a": 1}') == {"a": 1}


class TestParseLlmModels:
    def test_non_list_returns_empty(self):
        assert SettingRepository._parse_llm_models("not a list") == []

    def test_skips_non_dict_items(self):
        assert SettingRepository._parse_llm_models(["not a dict"]) == []


class TestParseOption:
    def test_dict_item(self):
        option = SettingRepository._parse_option({"key": "pb", "value": "Power Builder"})
        assert option.key == "pb"

    def test_string_item(self):
        option = SettingRepository._parse_option("plain-value")
        assert option.key == "plain-value"
        assert option.value == "plain-value"

    def test_other_type_returns_none(self):
        assert SettingRepository._parse_option(123) is None


class TestOptionFromDict:
    def test_missing_key_or_value_returns_none(self):
        assert SettingRepository._option_from_dict({"key": "", "value": "x"}) is None
        assert SettingRepository._option_from_dict({"key": "x", "value": ""}) is None


class TestOptionFromString:
    def test_empty_string_returns_none(self):
        assert SettingRepository._option_from_string("   ") is None

    def test_plain_string_becomes_key_and_value(self):
        option = SettingRepository._option_from_string("cobol")
        assert option.key == "cobol"
        assert option.value == "cobol"

    def test_json_dict_string_parsed(self):
        option = SettingRepository._option_from_string(
            json.dumps({"key": "pb", "value": "Power Builder"})
        )
        assert option.key == "pb"
        assert option.value == "Power Builder"

    def test_json_dict_missing_fields_falls_back_to_raw_string(self):
        raw = json.dumps({"key": "", "value": ""})
        option = SettingRepository._option_from_string(raw)
        assert option.key == raw

    def test_json_non_dict_falls_back_to_raw_string(self):
        raw = json.dumps([1, 2, 3])
        option = SettingRepository._option_from_string(raw)
        assert option.key == raw


class TestSerializePayload:
    def test_serializes_every_field_to_json_strings(self):
        payload = {"source_languages": [{"key": "pb", "value": "Power Builder"}]}

        result = SettingRepository._serialize_payload(payload)

        assert result["source_languages"] == [
            json.dumps({"key": "pb", "value": "Power Builder"}, separators=(",", ":"))
        ]
