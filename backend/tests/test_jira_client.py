"""Unit tests for the Jira Cloud HTTP client.

``httpx.AsyncClient`` is patched at the module import site so no real network
call is made.  ``asyncio.sleep`` is patched too so retry-backoff paths run
instantly.  ``asyncio_mode = auto`` (see pytest.ini) collects the ``async def``
tests without an explicit marker.
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.clients.jira_client import JiraCloudClient
from app.core.exceptions import JiraClientError


class _FakeResponse:
    """Minimal stand-in for an ``httpx.Response``."""

    def __init__(self, status_code: int, json_data: object = None, text: str = "") -> None:
        self.status_code = status_code
        self._json = json_data
        self.text = text

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> object:
        return self._json


def _patch_httpx(responses: list[_FakeResponse]):
    """Return a patch object for ``httpx.AsyncClient`` that yields *responses* in order.

    Each ``async with httpx.AsyncClient(...) as client`` produces the same
    mock client whose ``request`` returns the next queued response.
    """
    mock_client = MagicMock()
    mock_client.request = AsyncMock(side_effect=responses)

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_client)
    ctx.__aexit__ = AsyncMock(return_value=False)

    return patch("app.clients.jira_client.httpx.AsyncClient", return_value=ctx), mock_client


def _client() -> JiraCloudClient:
    return JiraCloudClient(
        base_url="https://acme.atlassian.net/",
        email="bot@acme.com",
        api_token="tok-123",
    )


class TestAuthHeader:
    def test_basic_auth_header_is_base64_email_token(self) -> None:
        client = _client()
        expected = base64.b64encode(b"bot@acme.com:tok-123").decode()
        assert client._headers["Authorization"] == f"Basic {expected}"

    def test_trailing_slash_stripped_from_base_url(self) -> None:
        assert _client()._base_url == "https://acme.atlassian.net"


class TestRequestSuccess:
    async def test_test_connection_returns_json(self) -> None:
        patcher, _ = _patch_httpx([_FakeResponse(200, {"displayName": "Bot"})])
        with patcher:
            result = await _client().test_connection()
        assert result == {"displayName": "Bot"}

    async def test_create_issue_posts_fields_and_returns_body(self) -> None:
        patcher, mock_client = _patch_httpx([_FakeResponse(201, {"key": "MER-1", "id": "10001"})])
        with patcher:
            result = await _client().create_issue({"summary": "Hi"})
        assert result == {"key": "MER-1", "id": "10001"}
        # Body is wrapped in {"fields": ...}
        _, kwargs = mock_client.request.call_args
        assert kwargs["json"] == {"fields": {"summary": "Hi"}}

    async def test_204_no_content_returns_empty_dict(self) -> None:
        patcher, _ = _patch_httpx([_FakeResponse(204)])
        with patcher:
            result = await _client().update_issue("MER-1", {"summary": "x"})
        assert result is None  # update_issue returns None explicitly

    async def test_get_issue_types_extracts_issue_types(self) -> None:
        patcher, _ = _patch_httpx(
            [_FakeResponse(200, {"issueTypes": [{"id": "1", "name": "Story"}]})]
        )
        with patcher:
            result = await _client().get_issue_types("MER")
        assert result == [{"id": "1", "name": "Story"}]


class TestRetryBehaviour:
    async def test_transient_500_then_success(self) -> None:
        patcher, mock_client = _patch_httpx(
            [_FakeResponse(503, text="unavailable"), _FakeResponse(200, {"ok": True})]
        )
        with patcher, patch("app.clients.jira_client.asyncio.sleep", new=AsyncMock()) as sleep:
            result = await _client().test_connection()
        assert result == {"ok": True}
        assert mock_client.request.call_count == 2
        sleep.assert_awaited_once()

    async def test_transient_exhausts_retries_then_raises(self) -> None:
        patcher, mock_client = _patch_httpx([_FakeResponse(500, text="boom")] * 3)
        with patcher, patch("app.clients.jira_client.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(JiraClientError):
                await _client().test_connection()
        assert mock_client.request.call_count == 3

    async def test_non_transient_400_raises_immediately(self) -> None:
        patcher, mock_client = _patch_httpx([_FakeResponse(400, text="bad request")])
        with patcher, patch("app.clients.jira_client.asyncio.sleep", new=AsyncMock()) as sleep:
            with pytest.raises(JiraClientError):
                await _client().create_issue({"summary": "x"})
        assert mock_client.request.call_count == 1
        sleep.assert_not_awaited()

    async def test_timeout_retries_then_raises(self) -> None:
        import httpx

        mock_client = MagicMock()
        mock_client.request = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.clients.jira_client.httpx.AsyncClient", return_value=ctx),
            patch("app.clients.jira_client.asyncio.sleep", new=AsyncMock()),
        ):
            with pytest.raises(JiraClientError, match="timeout"):
                await _client().test_connection()

        assert mock_client.request.call_count == 3

    async def test_connect_error_retries_then_raises(self) -> None:
        import httpx

        mock_client = MagicMock()
        mock_client.request = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.clients.jira_client.httpx.AsyncClient", return_value=ctx),
            patch("app.clients.jira_client.asyncio.sleep", new=AsyncMock()),
        ):
            with pytest.raises(JiraClientError, match="connection failed"):
                await _client().test_connection()

        assert mock_client.request.call_count == 3

    async def test_unsupported_protocol_raises_immediately(self) -> None:
        import httpx

        mock_client = MagicMock()
        mock_client.request = AsyncMock(
            side_effect=httpx.UnsupportedProtocol(
                "Request URL is missing an 'http://' or 'https://' protocol."
            )
        )
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.clients.jira_client.httpx.AsyncClient", return_value=ctx),
            patch("app.clients.jira_client.asyncio.sleep", new=AsyncMock()) as sleep,
            pytest.raises(JiraClientError, match="Invalid Jira base URL"),
        ):
            await _client().test_connection()

        assert mock_client.request.call_count == 1
        sleep.assert_not_awaited()


class TestPublicMethods:
    async def test_get_project(self) -> None:
        patcher, mock_client = _patch_httpx([_FakeResponse(200, json_data={"key": "MER"})])
        with patcher:
            result = await _client().get_project("MER")
        assert result == {"key": "MER"}

    async def test_get_issue_types_from_project(self) -> None:
        patcher, _ = _patch_httpx(
            [_FakeResponse(200, json_data={"issueTypes": [{"name": "Epic"}]})]
        )
        with patcher:
            result = await _client().get_issue_types("MER")
        assert result == [{"name": "Epic"}]

    async def test_check_permissions(self) -> None:
        patcher, mock_client = _patch_httpx(
            [
                _FakeResponse(
                    200,
                    json_data={"permissions": {"CREATE_ISSUE": {"havePermission": True}}},
                )
            ]
        )
        with patcher:
            result = await _client().check_permissions("MER", ["CREATE_ISSUE", "EDIT_ISSUE"])
        assert result == {"CREATE_ISSUE": True, "EDIT_ISSUE": False}

    async def test_update_issue(self) -> None:
        patcher, mock_client = _patch_httpx([_FakeResponse(204)])
        with patcher:
            result = await _client().update_issue("MER-1", {"summary": "new"})
        assert result is None
        method, url = mock_client.request.call_args[0]
        assert method == "PUT"
        assert "MER-1" in url

    async def test_get_issue_with_fields(self) -> None:
        patcher, mock_client = _patch_httpx([_FakeResponse(200, json_data={"id": "1"})])
        with patcher:
            await _client().get_issue("MER-1", fields=["summary", "status"])
        _, kwargs = mock_client.request.call_args
        assert kwargs["params"]["fields"] == "summary,status"

    async def test_get_issue_without_fields(self) -> None:
        patcher, mock_client = _patch_httpx([_FakeResponse(200, json_data={"id": "1"})])
        with patcher:
            await _client().get_issue("MER-1")
        _, kwargs = mock_client.request.call_args
        assert kwargs["params"] == {}

    async def test_transition_issue(self) -> None:
        patcher, mock_client = _patch_httpx([_FakeResponse(204)])
        with patcher:
            await _client().transition_issue("MER-1", "31")
        _, kwargs = mock_client.request.call_args
        assert kwargs["json"] == {"transition": {"id": "31"}}

    async def test_get_transitions(self) -> None:
        patcher, _ = _patch_httpx(
            [_FakeResponse(200, json_data={"transitions": [{"id": "31", "name": "Done"}]})]
        )
        with patcher:
            result = await _client().get_transitions("MER-1")
        assert result == [{"id": "31", "name": "Done"}]

    async def test_search_issues(self) -> None:
        patcher, mock_client = _patch_httpx(
            [_FakeResponse(200, json_data={"issues": [{"key": "MER-1"}]})]
        )
        with patcher:
            result = await _client().search_issues("project = MER", ["summary"], max_results=10)
        assert result == [{"key": "MER-1"}]
        _, kwargs = mock_client.request.call_args
        assert kwargs["json"]["maxResults"] == 10

    async def test_create_field(self) -> None:
        patcher, mock_client = _patch_httpx([_FakeResponse(200, json_data={"id": "customfield_1"})])
        with patcher:
            result = await _client().create_field("RIP ID", "textfield", "textsearcher")
        assert result == {"id": "customfield_1"}

    async def test_get_fields(self) -> None:
        patcher, _ = _patch_httpx([_FakeResponse(200, json_data=[{"id": "customfield_1"}])])
        with patcher:
            result = await _client().get_fields()
        assert result == [{"id": "customfield_1"}]

    async def test_create_component_with_description(self) -> None:
        patcher, mock_client = _patch_httpx(
            [_FakeResponse(200, json_data={"id": "10001", "name": "Auth"})]
        )
        with patcher:
            await _client().create_component("MER", "Auth", "Auth module")
        _, kwargs = mock_client.request.call_args
        assert kwargs["json"]["description"] == "Auth module"

    async def test_create_component_without_description(self) -> None:
        patcher, mock_client = _patch_httpx(
            [_FakeResponse(200, json_data={"id": "10001", "name": "Auth"})]
        )
        with patcher:
            await _client().create_component("MER", "Auth")
        _, kwargs = mock_client.request.call_args
        assert "description" not in kwargs["json"]

    async def test_get_components(self) -> None:
        patcher, _ = _patch_httpx([_FakeResponse(200, json_data=[{"id": "10001"}])])
        with patcher:
            result = await _client().get_components("MER")
        assert result == [{"id": "10001"}]

    async def test_get_myself(self) -> None:
        patcher, _ = _patch_httpx([_FakeResponse(200, json_data={"accountId": "acc-1"})])
        with patcher:
            result = await _client().get_myself()
        assert result == {"accountId": "acc-1"}
