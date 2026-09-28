"""Unit tests for the TAP HTTP client.

``httpx.AsyncClient`` is patched at the module import site so no real network
call is made. Mirrors ``tests/test_jira_client.py``'s pattern.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.clients.tap_client import TapClient
from app.core.exceptions import TapClientError


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
    mock_client = MagicMock()
    mock_client.request = AsyncMock(side_effect=responses)

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_client)
    ctx.__aexit__ = AsyncMock(return_value=False)

    return patch("app.clients.tap_client.httpx.AsyncClient", return_value=ctx), mock_client


def _client(auth_config: dict | None = None) -> TapClient:
    return TapClient(base_url="https://tap.example.com/", auth_config=auth_config)


_NOTIFY_ACK_BODY = {
    "success": True,
    "message": "Requirement sync notification received successfully. Syncing will start shortly.",
    "data": {
        "project_name": "Orange HRM",
        "notifier": "RIP",
        "receiver": "TAP",
        "status": "NOTIFICATION_RECEIVED",
        "message": "Requirement sync notification received successfully. Syncing will start shortly.",
    },
}


#: Both spellings TAP requires — the sync/notify endpoint wants the ``X-``
#: prefixed pair, app-clients/verify wants the bare pair. Each rejects the
#: other with a 422, so every request carries all four.
_API_KEY_HEADERS = ("X-API-Key", "API-Key")
_CLIENT_ID_HEADERS = ("X-App-Client-Id", "Client-Id")


class TestAuthHeaders:
    """TAP's auth headers, pinned in both spellings.

    Getting these wrong is invisible until runtime: TAP answers with a 422
    naming the header fields it wanted, which reads like a payload problem
    rather than an auth one. Both spellings confirmed against the live dev
    deployment, on different endpoints.
    """

    def test_includes_both_spellings_when_configured(self) -> None:
        client = _client({"api_key": "key-123", "app_client_id": "APP-1"})
        for header in _API_KEY_HEADERS:
            assert client._headers[header] == "key-123"
        for header in _CLIENT_ID_HEADERS:
            assert client._headers[header] == "APP-1"

    def test_omits_headers_when_auth_config_missing(self) -> None:
        client = _client(None)
        for header in (*_API_KEY_HEADERS, *_CLIENT_ID_HEADERS):
            assert header not in client._headers

    def test_omits_headers_when_values_empty_strings(self) -> None:
        # An unconfigured project must not send blank header values.
        client = _client({"api_key": "", "app_client_id": ""})
        for header in (*_API_KEY_HEADERS, *_CLIENT_ID_HEADERS):
            assert header not in client._headers

    def test_trailing_slash_stripped_from_base_url(self) -> None:
        assert _client()._base_url == "https://tap.example.com"


class TestNotifySyncReady:
    async def test_success_posts_confirmed_body_and_returns_data(self) -> None:
        patcher, mock_client = _patch_httpx([_FakeResponse(202, _NOTIFY_ACK_BODY)])
        with patcher:
            result = await _client().notify_sync_ready(
                project_name="Orange HRM", project_id="proj-1", sync_id="sync-1"
            )
        assert result == _NOTIFY_ACK_BODY["data"]

        args, kwargs = mock_client.request.call_args
        assert args[0] == "POST"
        assert args[1].endswith("/api/v1/sync/requirements/notify")
        assert kwargs["json"] == {
            "project_name": "Orange HRM",
            "data_type": "requirement",
            "project_id": "proj-1",
            "sync_id": "sync-1",
        }

    async def test_envelope_success_false_raises_even_on_2xx(self) -> None:
        body = {**_NOTIFY_ACK_BODY, "success": False}
        patcher, _ = _patch_httpx([_FakeResponse(200, body)])
        with patcher:
            with pytest.raises(TapClientError):
                await _client().notify_sync_ready(project_name="X", project_id="p", sync_id="s")

    async def test_status_not_notification_received_raises(self) -> None:
        body = {
            "success": True,
            "message": "ok",
            "data": {"status": "REJECTED", "message": "unknown project"},
        }
        patcher, _ = _patch_httpx([_FakeResponse(202, body)])
        with patcher:
            with pytest.raises(TapClientError):
                await _client().notify_sync_ready(project_name="X", project_id="p", sync_id="s")

    async def test_missing_data_field_raises(self) -> None:
        patcher, _ = _patch_httpx([_FakeResponse(202, {"success": True, "message": "ok"})])
        with patcher:
            with pytest.raises(TapClientError):
                await _client().notify_sync_ready(project_name="X", project_id="p", sync_id="s")


class TestRetryBehaviour:
    async def test_transient_503_then_success(self) -> None:
        patcher, mock_client = _patch_httpx(
            [_FakeResponse(503, text="unavailable"), _FakeResponse(202, _NOTIFY_ACK_BODY)]
        )
        with patcher, patch("app.clients.tap_client.asyncio.sleep", new=AsyncMock()) as sleep:
            result = await _client().notify_sync_ready(
                project_name="X", project_id="p", sync_id="s"
            )
        assert result == _NOTIFY_ACK_BODY["data"]
        assert mock_client.request.call_count == 2
        sleep.assert_awaited_once()

    async def test_transient_exhausts_retries_then_raises(self) -> None:
        patcher, mock_client = _patch_httpx([_FakeResponse(500, text="boom")] * 3)
        with patcher, patch("app.clients.tap_client.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(TapClientError):
                await _client().notify_sync_ready(project_name="X", project_id="p", sync_id="s")
        assert mock_client.request.call_count == 3

    async def test_non_transient_400_raises_immediately(self) -> None:
        patcher, mock_client = _patch_httpx([_FakeResponse(400, text="bad request")])
        with patcher, patch("app.clients.tap_client.asyncio.sleep", new=AsyncMock()) as sleep:
            with pytest.raises(TapClientError):
                await _client().notify_sync_ready(project_name="X", project_id="p", sync_id="s")
        assert mock_client.request.call_count == 1
        sleep.assert_not_awaited()
