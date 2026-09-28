"""Unit tests for TenantLLMProviderUpdateRequest validation."""

from __future__ import annotations

from pydantic import ValidationError
import pytest

from app.schemas.tenant_schema import TenantLLMProviderUpdateRequest


def test_strips_whitespace_from_api_key() -> None:
    req = TenantLLMProviderUpdateRequest(api_key="  sk-secret  \n")
    assert req.api_key == "sk-secret"


def test_rejects_blank_after_strip() -> None:
    with pytest.raises(ValidationError):
        TenantLLMProviderUpdateRequest(api_key="    ")


def test_requires_at_least_one_field() -> None:
    with pytest.raises(ValidationError):
        TenantLLMProviderUpdateRequest()


def test_is_active_only_is_valid() -> None:
    req = TenantLLMProviderUpdateRequest(is_active=False)
    assert req.api_key is None
    assert req.is_active is False


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        TenantLLMProviderUpdateRequest(api_key="sk-secret", unexpected="x")
