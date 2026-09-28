"""Unit tests for app/schemas/tenant_schema.py request validation."""

from __future__ import annotations

from pydantic import ValidationError
import pytest

from app.schemas.tenant_schema import TenantCreateRequest


class TestTenantCreateRequestRequiredFields:
    def test_valid_payload_succeeds(self) -> None:
        body = TenantCreateRequest(
            name="Acme",
            contact_email="jane@example.com",
            providers=["anthropic"],
        )
        assert body.name == "Acme"
        assert body.providers == ["anthropic"]

    def test_missing_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TenantCreateRequest(contact_email="jane@example.com", providers=["anthropic"])

    def test_missing_contact_email_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TenantCreateRequest(name="Acme", providers=["anthropic"])

    def test_missing_providers_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TenantCreateRequest(name="Acme", contact_email="jane@example.com")

    def test_empty_providers_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TenantCreateRequest(name="Acme", contact_email="jane@example.com", providers=[])
