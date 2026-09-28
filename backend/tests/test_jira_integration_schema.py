"""Unit tests for Jira integration schema validators."""

from __future__ import annotations

from pydantic import ValidationError
import pytest

from app.schemas.jira_integration_schema import (
    JiraIntegrationCreate,
    JiraIntegrationUpdate,
)


def _create_payload(**overrides) -> dict:
    payload = {
        "jira_base_url": "https://acme.atlassian.net",
        "jira_project_key": "MER",
        "jira_user_email": "bot@acme.com",
        "api_token": "tok-123",
    }
    payload.update(overrides)
    return payload


class TestJiraIntegrationCreateBaseUrl:
    def test_accepts_https_url(self) -> None:
        model = JiraIntegrationCreate(**_create_payload())
        assert model.jira_base_url == "https://acme.atlassian.net"

    def test_accepts_http_url(self) -> None:
        model = JiraIntegrationCreate(**_create_payload(jira_base_url="http://acme.atlassian.net"))
        assert model.jira_base_url == "http://acme.atlassian.net"

    def test_rejects_url_missing_scheme(self) -> None:
        with pytest.raises(ValidationError, match="http"):
            JiraIntegrationCreate(**_create_payload(jira_base_url="acme.atlassian.net"))

    def test_rejects_unsupported_scheme(self) -> None:
        with pytest.raises(ValidationError, match="http"):
            JiraIntegrationCreate(**_create_payload(jira_base_url="ftp://acme.atlassian.net"))


class TestJiraIntegrationUpdateBaseUrl:
    def test_accepts_https_url(self) -> None:
        model = JiraIntegrationUpdate(jira_base_url="https://new.atlassian.net")
        assert model.jira_base_url == "https://new.atlassian.net"

    def test_rejects_url_missing_scheme(self) -> None:
        with pytest.raises(ValidationError, match="http"):
            JiraIntegrationUpdate(jira_base_url="new.atlassian.net")

    def test_field_omitted_does_not_trigger_validator(self) -> None:
        model = JiraIntegrationUpdate(jira_project_key="MER2")
        assert model.jira_base_url is None
