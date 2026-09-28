"""Unit tests for app/utils/correlation.py."""

from __future__ import annotations

from unittest.mock import AsyncMock
import uuid

import pytest

from app.utils.correlation import (
    CorrelationIdMiddleware,
    _correlation_id_var,
    get_correlation_id,
)


class TestGetCorrelationId:
    def test_default_value(self):
        # Reset to default to test the fallback
        token = _correlation_id_var.set("none")
        try:
            assert get_correlation_id() == "none"
        finally:
            _correlation_id_var.reset(token)

    def test_returns_set_value(self):
        token = _correlation_id_var.set("custom-123")
        try:
            assert get_correlation_id() == "custom-123"
        finally:
            _correlation_id_var.reset(token)


def _make_scope(headers: dict[str, str] | None = None) -> dict:
    """Build a minimal ASGI HTTP scope."""
    raw_headers = [
        (k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()
    ]
    return {"type": "http", "headers": raw_headers}


async def _call_middleware(
    middleware: CorrelationIdMiddleware,
    scope: dict,
) -> tuple[str | None, list[dict]]:
    """Drive middleware through its ASGI interface; return (captured_id, sent_messages)."""
    captured_id: str | None = None
    sent_messages: list[dict] = []

    async def inner_app(scope, receive, send):
        nonlocal captured_id
        captured_id = get_correlation_id()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def inner_app_raise(scope, receive, send):
        raise RuntimeError("handler error")

    async def send_capture(message):
        sent_messages.append(message)

    await middleware._app(scope, AsyncMock(), send_capture)  # type: ignore[attr-defined]
    return captured_id, sent_messages


class TestCorrelationIdMiddleware:
    @pytest.mark.asyncio
    async def test_injects_header_from_request(self):
        incoming_id = str(uuid.uuid4())
        captured_id: list[str] = []
        sent_messages: list[dict] = []

        async def inner_app(scope, receive, send):
            captured_id.append(get_correlation_id())
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = CorrelationIdMiddleware(inner_app)
        scope = _make_scope({"X-Correlation-ID": incoming_id})

        async def send_capture(msg):
            sent_messages.append(msg)

        await middleware(scope, AsyncMock(), send_capture)

        assert captured_id[0] == incoming_id
        start = next(m for m in sent_messages if m["type"] == "http.response.start")
        hdrs = dict(start["headers"])
        assert hdrs[b"x-correlation-id"] == incoming_id.encode("latin-1")

    @pytest.mark.asyncio
    async def test_generates_uuid_when_header_missing(self):
        captured_id: list[str] = []
        sent_messages: list[dict] = []

        async def inner_app(scope, receive, send):
            captured_id.append(get_correlation_id())
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = CorrelationIdMiddleware(inner_app)
        scope = _make_scope()  # no correlation header

        async def send_capture(msg):
            sent_messages.append(msg)

        await middleware(scope, AsyncMock(), send_capture)

        assert len(captured_id) == 1
        uuid.UUID(captured_id[0])  # raises if not a valid UUID

        start = next(m for m in sent_messages if m["type"] == "http.response.start")
        hdrs = dict(start["headers"])
        assert hdrs[b"x-correlation-id"] == captured_id[0].encode("latin-1")

    @pytest.mark.asyncio
    async def test_resets_context_var_after_request(self):
        token = _correlation_id_var.set("before-request")

        async def inner_app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = CorrelationIdMiddleware(inner_app)
        scope = _make_scope({"X-Correlation-ID": "during-request"})

        await middleware(scope, AsyncMock(), AsyncMock())

        # After __call__, context var should be reset to the previous token
        assert get_correlation_id() == "before-request"
        _correlation_id_var.reset(token)

    @pytest.mark.asyncio
    async def test_resets_context_var_on_exception(self):
        token = _correlation_id_var.set("initial")

        async def inner_app(scope, receive, send):
            raise RuntimeError("handler error")

        middleware = CorrelationIdMiddleware(inner_app)
        scope = _make_scope({"X-Correlation-ID": "req-id"})

        with pytest.raises(RuntimeError, match="handler error"):
            await middleware(scope, AsyncMock(), AsyncMock())

        # Context var must be restored even when inner app raises
        assert get_correlation_id() == "initial"
        _correlation_id_var.reset(token)
