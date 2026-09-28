"""Tests for Celery timeout-related settings normalization."""

from __future__ import annotations

import pytest

from app.core.config import Settings


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (None, None),
        ("", None),
        ("0", None),
        ("none", None),
        ("null", None),
        (0, None),
        (15, 15.0),
        ("15", 15.0),
    ],
)
def test_normalize_optional_broker_socket_timeout(
    raw_value: object, expected: float | None
) -> None:
    result = Settings.normalize_optional_broker_socket_timeout(raw_value)
    assert result == expected


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (None, None),
        ("", None),
        ("0", None),
        ("none", None),
        ("null", None),
        (0, None),
        (30, 30.0),
        ("30", 30.0),
    ],
)
def test_normalize_optional_result_socket_timeout(
    raw_value: object, expected: float | None
) -> None:
    result = Settings.normalize_optional_result_socket_timeout(raw_value)
    assert result == expected
