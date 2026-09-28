"""Unit tests for app.utils.common helpers."""

from __future__ import annotations

import pytest

from app.utils.common import generate_short_uuid, normalize_filename


def test_generate_short_uuid_default_and_custom_length() -> None:
    default_value = generate_short_uuid()
    custom_value = generate_short_uuid(8)

    assert len(default_value) == 12
    assert len(custom_value) == 8
    assert default_value != custom_value


def test_generate_short_uuid_invalid_length() -> None:
    with pytest.raises(ValueError, match="length must be greater than 0"):
        generate_short_uuid(0)


def test_normalize_filename_with_extension_and_symbols() -> None:
    value = normalize_filename("  My__File  (v2)!.DOCX")
    assert value == "My_File_v2.docx"


def test_normalize_filename_unicode_and_empty() -> None:
    assert normalize_filename("Crème brûlée.pdf") == "Creme_brulee.pdf"
    assert normalize_filename("   ") == "file"


def test_normalize_filename_stem_only_and_truncation() -> None:
    value = normalize_filename("a" * 80 + ".txt", max_stem_length=10)
    assert value == ("a" * 10 + ".txt")

    no_ext = normalize_filename("No.Ext?*Name")
    assert no_ext == "No.extname"
