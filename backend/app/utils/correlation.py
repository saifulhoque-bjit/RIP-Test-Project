"""Correlation ID propagation.

A ``ContextVar`` carries the correlation ID through the entire async call
stack for each request without threading issues.

``CorrelationIdMiddleware`` is implemented as a **raw ASGI middleware** (not
``BaseHTTPMiddleware``) so that streaming responses (file downloads, SSE) are
never buffered.  ``BaseHTTPMiddleware`` awaits the full response body before
forwarding it to the client, which defeats any ``StreamingResponse``.

The middleware:
  1. Reads ``X-Correlation-ID`` from the incoming request header, or generates
     a new UUID when the header is absent.
  2. Injects the ID into the ContextVar so every log record emitted during
     that request automatically includes it (via the logger filter in
     ``app/utils/logger.py``).
  3. Wraps the ASGI ``send`` callable to inject ``X-Correlation-ID`` into the
     response headers on the ``http.response.start`` event — without buffering
     a single byte of the response body.

Usage (read anywhere in the request lifecycle):
    from app.utils.correlation import get_correlation_id
    cid = get_correlation_id()
"""

from __future__ import annotations

from contextvars import ContextVar, Token
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="none")


def get_correlation_id() -> str:
    """Return the correlation ID for the current async context."""
    return _correlation_id_var.get()


def set_correlation_id(correlation_id: str) -> None:
    """Replace the correlation ID outright (no restore token).

    Used only by the Celery ``task_prerun``/``task_postrun`` signal handlers
    in ``app/core/celery_app.py`` to bind/clear the request's correlation ID
    for a worker task running in its own thread — those are two separate
    signal invocations, not a single enclosing ``with`` block, so a
    token-based restore does not apply here.
    """
    _correlation_id_var.set(correlation_id)


class CorrelationIdMiddleware:
    """Inject and propagate a per-request correlation ID.

    Implemented as a pure ASGI middleware — no response buffering, fully
    compatible with ``StreamingResponse`` and chunked encoding.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self._app(scope, receive, send)
            return

        # Resolve or generate the correlation ID from incoming headers.
        headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
        correlation_id = next(
            (v.decode("latin-1") for k, v in headers if k.lower() == b"x-correlation-id"),
            str(uuid.uuid4()),
        )

        token: Token[str] = _correlation_id_var.set(correlation_id)

        async def send_with_cid(message: Message) -> None:
            """Inject X-Correlation-ID into the response start event."""
            if message["type"] == "http.response.start":
                # ``headers`` in the message is a mutable list of (name, value) byte pairs.
                resp_headers: list[tuple[bytes, bytes]] = list(message.get("headers", []))
                resp_headers.append((b"x-correlation-id", correlation_id.encode("latin-1")))
                message = {**message, "headers": resp_headers}
            await send(message)

        try:
            await self._app(scope, receive, send_with_cid)
        finally:
            _correlation_id_var.reset(token)


# ── Security headers ───────────────────────────────────────────────────────

_SECURITY_HEADERS: list[tuple[bytes, bytes]] = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"x-xss-protection", b"0"),  # modern browsers: disable legacy XSS filter
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"strict-transport-security", b"max-age=63072000; includeSubDomains; preload"),
    (b"permissions-policy", b"geolocation=(), microphone=(), camera=()"),
    (b"cache-control", b"no-store"),
]


class SecurityHeadersMiddleware:
    """Append hardened security headers to every HTTP response.

    Implemented as a **pure ASGI middleware** so it is fully compatible with
    ``StreamingResponse`` and chunked encoding — no response buffering.

    Headers injected
    ────────────────
    ``X-Content-Type-Options``      nosniff — prevents MIME-type sniffing.
    ``X-Frame-Options``             DENY — blocks all framing (clickjacking).
    ``X-XSS-Protection``            0 — disables the legacy IE XSS filter
                                    (modern browsers rely on CSP instead).
    ``Referrer-Policy``             strict-origin-when-cross-origin.
    ``Strict-Transport-Security``   2-year max-age with preload (HTTPS only).
    ``Permissions-Policy``          Deny access to sensitive browser features.
    ``Cache-Control``               no-store — prevents caching of API responses.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(_SECURITY_HEADERS)
                message = {**message, "headers": headers}
            await send(message)

        await self._app(scope, receive, send_with_security_headers)
