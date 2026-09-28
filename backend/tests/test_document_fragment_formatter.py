"""Unit tests for app.utils.document_fragment_formatter."""

from __future__ import annotations

from app.utils.document_fragment_formatter import format_parsed


class TestFormatParsed:
    def test_single_page_single_item(self):
        raw = {
            "items": {
                "pages": [
                    {
                        "page_number": 1,
                        "items": [
                            {
                                "type": "text",
                                "md": "hello",
                                "bbox": [{"x": 1, "y": 2, "w": 3, "h": 4, "confidence": 0.9}],
                            }
                        ],
                    }
                ]
            }
        }

        result = format_parsed(raw)

        assert len(result) == 1
        chunk = result[0]
        assert chunk["frag_type"] == "text"
        assert chunk["content"] == "hello"
        assert chunk["bbox"] == [
            {"page": 1, "bbox": {"x": 1, "y": 2, "w": 3, "h": 4}, "confidence": 0.9}
        ]

    def test_multiple_pages_and_items(self):
        raw = {
            "items": {
                "pages": [
                    {"page_number": 1, "items": [{"type": "text", "value": "a"}]},
                    {"page_number": 2, "items": [{"type": "table", "value": "b"}]},
                ]
            }
        }

        result = format_parsed(raw)

        assert len(result) == 2
        assert result[0]["bbox"][0]["page"] == 1
        assert result[1]["bbox"][0]["page"] == 2

    def test_missing_type_defaults_to_unknown(self):
        raw = {"items": {"pages": [{"page_number": 1, "items": [{"value": "x"}]}]}}

        result = format_parsed(raw)

        assert result[0]["frag_type"] == "unknown"

    def test_no_pages_returns_empty_list(self):
        raw = {"items": {"pages": []}}

        assert format_parsed(raw) == []


class TestExtractBbox:
    def test_list_bbox_uses_first_entry(self):
        from app.utils.document_fragment_formatter import _extract_bbox

        item = {"bbox": [{"x": 1, "y": 2, "w": 3, "h": 4}]}
        assert _extract_bbox(item) == {"x": 1, "y": 2, "w": 3, "h": 4}

    def test_dict_bbox(self):
        from app.utils.document_fragment_formatter import _extract_bbox

        item = {"bbox": {"x": 5, "y": 6, "w": 7, "h": 8}}
        assert _extract_bbox(item) == {"x": 5, "y": 6, "w": 7, "h": 8}

    def test_missing_bbox_falls_back_to_nested_item(self):
        from app.utils.document_fragment_formatter import _extract_bbox

        item = {"items": [{"bbox": {"x": 9, "y": 10, "w": 11, "h": 12}}]}
        assert _extract_bbox(item) == {"x": 9, "y": 10, "w": 11, "h": 12}

    def test_no_bbox_or_nested_returns_zeros(self):
        from app.utils.document_fragment_formatter import _extract_bbox

        assert _extract_bbox({}) == {"x": 0, "y": 0, "w": 0, "h": 0}

    def test_empty_bbox_list_falls_through(self):
        from app.utils.document_fragment_formatter import _extract_bbox

        assert _extract_bbox({"bbox": []}) == {"x": 0, "y": 0, "w": 0, "h": 0}


class TestExtractConfidence:
    def test_list_bbox_confidence(self):
        from app.utils.document_fragment_formatter import _extract_confidence

        item = {"bbox": [{"confidence": 0.75}]}
        assert _extract_confidence(item) == 0.75

    def test_dict_bbox_confidence(self):
        from app.utils.document_fragment_formatter import _extract_confidence

        item = {"bbox": {"confidence": 0.5}}
        assert _extract_confidence(item) == 0.5

    def test_missing_confidence_defaults_to_zero(self):
        from app.utils.document_fragment_formatter import _extract_confidence

        assert _extract_confidence({"bbox": {}}) == 0.0

    def test_no_bbox_returns_zero(self):
        from app.utils.document_fragment_formatter import _extract_confidence

        assert _extract_confidence({}) == 0.0


class TestExtractText:
    def test_md_field_preferred(self):
        from app.utils.document_fragment_formatter import _extract_text

        assert _extract_text({"md": "markdown text", "value": "plain"}) == "markdown text"

    def test_value_field_used_when_no_md(self):
        from app.utils.document_fragment_formatter import _extract_text

        assert _extract_text({"value": "plain text"}) == "plain text"

    def test_nested_items_joined(self):
        from app.utils.document_fragment_formatter import _extract_text

        item = {"items": [{"value": "a"}, {"value": "b"}]}
        assert _extract_text(item) == "a\nb"

    def test_no_content_returns_empty_string(self):
        from app.utils.document_fragment_formatter import _extract_text

        assert _extract_text({}) == ""
