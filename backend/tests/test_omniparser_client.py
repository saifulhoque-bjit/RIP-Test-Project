"""Unit tests for OmniParserClient.

``httpx.AsyncClient`` is patched at the module import site so no real network
call is made. ``asyncio.sleep`` is patched too so retry-backoff runs instantly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.clients.omniparser_client import (
    OmniParserClient,
    OmniParserClientError,
    OmniParserResponse,
)


class _FakeResponse:
    def __init__(
        self, status_code: int, json_data: object = None, text: str = "", json_error: bool = False
    ):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self._json_error = json_error

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> object:
        if self._json_error:
            raise ValueError("not json")
        return self._json


def _patch_httpx(responses: list):
    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=responses)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return patch("app.clients.omniparser_client.httpx.AsyncClient", return_value=ctx), mock_client


class TestParseImageBase64:
    async def test_success_returns_validated_elements(self):
        response = _FakeResponse(
            200,
            json_data={
                "parsed_content_list": [{"type": "text", "bbox": [0, 0, 1, 1], "content": "hi"}]
            },
        )
        patcher, _ = _patch_httpx([response])
        with patcher:
            result = await OmniParserClient().parse_image_base64("base64data", ctx={})

        assert isinstance(result, OmniParserResponse)
        assert len(result.elements) == 1
        assert result.elements[0].type == "text"

    async def test_non_success_status_raises_immediately(self):
        response = _FakeResponse(500, text="server error")
        patcher, mock_client = _patch_httpx([response])
        with patcher:
            with pytest.raises(OmniParserClientError, match="OmniParser returned HTTP 500"):
                await OmniParserClient().parse_image_base64("base64data", ctx={})

        assert mock_client.post.call_count == 1

    async def test_non_json_response_raises(self):
        response = _FakeResponse(200, json_error=True)
        patcher, _ = _patch_httpx([response])
        with patcher:
            with pytest.raises(OmniParserClientError, match="not valid JSON"):
                await OmniParserClient().parse_image_base64("base64data", ctx={})

    async def test_schema_validation_failure_raises(self):
        response = _FakeResponse(
            200,
            json_data={"parsed_content_list": [{"type": "text"}]},  # missing required bbox
        )
        patcher, _ = _patch_httpx([response])
        with patcher:
            with pytest.raises(OmniParserClientError, match="schema validation failed"):
                await OmniParserClient().parse_image_base64("base64data", ctx={})

    async def test_transient_timeout_then_success(self):
        import httpx

        good_response = _FakeResponse(200, json_data={"parsed_content_list": []})
        mock_client = MagicMock()
        mock_client.post = AsyncMock(
            side_effect=[httpx.TimeoutException("timed out"), good_response]
        )
        ctx_mgr = MagicMock()
        ctx_mgr.__aenter__ = AsyncMock(return_value=mock_client)
        ctx_mgr.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.clients.omniparser_client.httpx.AsyncClient", return_value=ctx_mgr),
            patch("app.clients.omniparser_client.asyncio.sleep", new=AsyncMock()),
        ):
            result = await OmniParserClient().parse_image_base64("base64data", ctx={})

        assert result.elements == []
        assert mock_client.post.call_count == 2

    async def test_exhausts_retries_then_raises(self):
        import httpx

        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        ctx_mgr = MagicMock()
        ctx_mgr.__aenter__ = AsyncMock(return_value=mock_client)
        ctx_mgr.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.clients.omniparser_client.httpx.AsyncClient", return_value=ctx_mgr),
            patch("app.clients.omniparser_client.asyncio.sleep", new=AsyncMock()),
        ):
            with pytest.raises(OmniParserClientError, match="failed after 3 attempts"):
                await OmniParserClient().parse_image_base64("base64data", ctx={})

        assert mock_client.post.call_count == 3
