"""Unit tests for app.schemas.image_extraction_schema's custom validators."""

from __future__ import annotations

import json

from app.schemas.image_extraction_schema import ImageExtractionOutput, OcrGroup


class TestOcrGroupTextCoercion:
    def test_accepts_list_directly(self):
        group = OcrGroup(region="header", text=["a", "b"])
        assert group.text == ["a", "b"]

    def test_coerces_json_string_list(self):
        group = OcrGroup(region="header", text=json.dumps(["a", "b"]))
        assert group.text == ["a", "b"]

    def test_non_list_json_string_passthrough_raises(self):
        try:
            OcrGroup(region="header", text=json.dumps({"not": "a list"}))
            assert False, "expected a validation error"
        except Exception:
            pass

    def test_invalid_json_string_passthrough_raises(self):
        try:
            OcrGroup(region="header", text="not json")
            assert False, "expected a validation error"
        except Exception:
            pass


class TestImageExtractionOutputGroupsCoercion:
    def test_accepts_list_of_dicts_directly(self):
        output = ImageExtractionOutput(
            image_type="screenshot",
            description="a screen",
            ocr_groups=[{"region": "header", "text": ["hi"]}],
            confidence="high",
        )
        assert len(output.ocr_groups) == 1
        assert output.ocr_groups[0].region == "header"

    def test_coerces_json_string_list_of_groups(self):
        payload = json.dumps([{"region": "header", "text": ["hi"]}])
        output = ImageExtractionOutput(
            image_type="screenshot",
            description="a screen",
            ocr_groups=payload,
            confidence="high",
        )
        assert output.ocr_groups[0].region == "header"

    def test_legibility_notes_defaults_to_empty_string(self):
        output = ImageExtractionOutput(
            image_type="screenshot",
            description="a screen",
            ocr_groups=[],
            confidence="low",
        )
        assert output.legibility_notes == ""

    def test_non_list_json_string_passthrough_raises(self):
        try:
            ImageExtractionOutput(
                image_type="screenshot",
                description="a screen",
                ocr_groups=json.dumps({"not": "a list"}),
                confidence="high",
            )
            assert False, "expected a validation error"
        except Exception:
            pass

    def test_invalid_json_string_passthrough_raises(self):
        try:
            ImageExtractionOutput(
                image_type="screenshot",
                description="a screen",
                ocr_groups="not json",
                confidence="high",
            )
            assert False, "expected a validation error"
        except Exception:
            pass
