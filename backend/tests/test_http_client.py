"""Unit tests for app/utils/http_client.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.utils.http_client import (
    build_async_client,
    get_with_retry,
    post_with_retry,
)

# ── build_async_client ─────────────────────────────────────────────────────


class TestBuildAsyncClient:
    def test_returns_async_client(self):
        client = build_async_client()
        assert isinstance(client, httpx.AsyncClient)

    def test_with_base_url(self):
        client = build_async_client(base_url="https://api.example.com")
        assert str(client.base_url) == "https://api.example.com"

    def test_with_custom_headers(self):
        client = build_async_client(headers={"Authorization": "Bearer token"})
        assert "Authorization" in client.headers

    def test_follow_redirects_disabled(self):
        client = build_async_client()
        assert client.follow_redirects is False


# ── get_with_retry ─────────────────────────────────────────────────────────


class TestGetWithRetry:
    @pytest.mark.asyncio
    async def test_success(self):
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = lambda: None

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_response)

        result = await get_with_retry(client, "/test")

        assert result.status_code == 200
        client.get.assert_awaited_once_with("/test")

    @pytest.mark.asyncio
    async def test_retries_on_timeout(self):
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = lambda: None

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[httpx.TimeoutException("timeout"), mock_response])

        # Patch tenacity wait to avoid real delays
        with patch("app.utils.http_client.wait_exponential", return_value=0):
            result = await get_with_retry(client, "/test")

        assert result.status_code == 200
        assert client.get.await_count == 2

    @pytest.mark.asyncio
    async def test_retries_on_network_error(self):
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = lambda: None

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[httpx.NetworkError("conn reset"), mock_response])

        with patch("app.utils.http_client.wait_exponential", return_value=0):
            result = await get_with_retry(client, "/test")

        assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.TimeoutException("persistent timeout"))

        with patch("app.utils.http_client.wait_exponential", return_value=0):
            with pytest.raises(httpx.TimeoutException):
                await get_with_retry(client, "/test")

        assert client.get.await_count == 3  # stop_after_attempt(3)

    @pytest.mark.asyncio
    async def test_http_status_error_not_retried(self):
        """Non-retryable errors (like 400) should propagate immediately."""
        request = httpx.Request("GET", "https://example.com/test")
        response = httpx.Response(400, request=request)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=response)

        # raise_for_status will raise HTTPStatusError for 400
        with pytest.raises(httpx.HTTPStatusError):
            await get_with_retry(client, "/test")


# ── post_with_retry ────────────────────────────────────────────────────────


class TestPostWithRetry:
    @pytest.mark.asyncio
    async def test_success(self):
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 201
        mock_response.raise_for_status = lambda: None

        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=mock_response)

        result = await post_with_retry(client, "/create", json={"key": "value"})

        assert result.status_code == 201
        client.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_retries_on_timeout(self):
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = lambda: None

        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(side_effect=[httpx.TimeoutException("timeout"), mock_response])

        with patch("app.utils.http_client.wait_exponential", return_value=0):
            result = await post_with_retry(client, "/create")

        assert result.status_code == 200
        assert client.post.await_count == 2

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(side_effect=httpx.NetworkError("connection refused"))

        with patch("app.utils.http_client.wait_exponential", return_value=0):
            with pytest.raises(httpx.NetworkError):
                await post_with_retry(client, "/create")

        assert client.post.await_count == 3
