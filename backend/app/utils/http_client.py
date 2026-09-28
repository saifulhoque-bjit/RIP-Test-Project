"""Shared async HTTP client helpers.

Provides a pre-configured ``httpx.AsyncClient`` with timeout and retry logic
backed by ``tenacity``.  All outbound HTTP calls should go through here.
"""

from __future__ import annotations

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.utils.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)


def build_async_client(
    base_url: str = "",
    timeout: httpx.Timeout = _DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
) -> httpx.AsyncClient:
    """Return a configured ``httpx.AsyncClient``."""
    return httpx.AsyncClient(
        base_url=base_url,
        timeout=timeout,
        headers=headers or {},
        follow_redirects=False,
    )


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    stop=stop_after_attempt(3),
    reraise=True,
)
async def get_with_retry(client: httpx.AsyncClient, url: str, **kwargs: object) -> httpx.Response:
    """GET *url* with automatic exponential-backoff retry."""
    response = await client.get(url, **kwargs)  # type: ignore[arg-type]
    response.raise_for_status()
    return response


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    stop=stop_after_attempt(3),
    reraise=True,
)
async def post_with_retry(client: httpx.AsyncClient, url: str, **kwargs: object) -> httpx.Response:
    """POST *url* with automatic exponential-backoff retry."""
    response = await client.post(url, **kwargs)  # type: ignore[arg-type]
    response.raise_for_status()
    return response
