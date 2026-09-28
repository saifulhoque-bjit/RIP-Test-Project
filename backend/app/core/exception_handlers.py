"""Centralized global exception handlers.

Register all handlers with a single call at application startup:

    from app.core.exception_handlers import register_exception_handlers
    register_exception_handlers(app)

Handler hierarchy
─────────────────
1. RIPBaseException subclasses  → HTTP status/error_code carried on the class
2. RequestValidationError       → 422 — Pydantic / FastAPI input validation
3. HTTPException                → pass-through, wrapped in ErrorResponse
4. Exception (catch-all)        → 500 Internal Server Error

All handlers produce the same ``ErrorResponse`` envelope so consumers never
have to branch on error shape.

Logging strategy
────────────────
- 4xx client errors  → WARNING  (expected, no stack trace needed)
- 5xx server errors  → ERROR    (unexpected, always log exc_info)
- Every log line carries ``correlation_id`` via the logger filter.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.requests import Request

from app.core.config import get_settings
from app.core.error_response import ErrorResponse
from app.core.exceptions import RIPBaseException, UnauthorizedError
from app.core.messages import MSG_GENERIC_ERROR, MSG_GENERIC_UNEXPECTED_ERROR
from app.utils.correlation import get_correlation_id
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Individual handlers ────────────────────────────────────────────────────


async def _handle_rip_exception(request: Request, exc: RIPBaseException) -> JSONResponse:
    """Convert any ``RIPBaseException`` subclass to its declared HTTP status.

    - 4xx → logged at WARNING level (client error, no stack trace)
    - 5xx → logged at ERROR level with full stack trace
    """
    correlation_id = get_correlation_id()
    log_level = logging.WARNING if exc.status_code < 500 else logging.ERROR
    logger.log(
        log_level,
        "[%s] %s | path=%s | cid=%s",
        exc.error_code,
        exc.message,
        request.url.path,
        correlation_id,
        exc_info=(exc.status_code >= 500),
    )

    headers: dict[str, str] = {}
    if isinstance(exc, UnauthorizedError):
        headers["WWW-Authenticate"] = "Bearer"

    return JSONResponse(
        status_code=exc.status_code,
        headers=headers,
        content=ErrorResponse.build(
            error_code=exc.error_code,
            message=exc.message or MSG_GENERIC_ERROR,
            correlation_id=correlation_id,
            path=str(request.url.path),
        ).model_dump(),
    )


def _sanitize_validation_errors(errors: list[dict]) -> list[dict]:
    """Make Pydantic v2 error dicts JSON-safe.

    Pydantic v2 populates ``error["ctx"]["error"]`` with the original
    ``ValueError`` instance from a ``@field_validator``.  That object is
    not JSON-serializable and causes ``json.dumps`` to raise a
    ``TypeError``, turning a clean 422 into a 500 Internal Server Error.

    This function walks every error dict and converts any exception
    object found in ``ctx`` to its string representation.
    """
    safe: list[dict] = []
    for err in errors:
        entry = dict(err)
        if "ctx" in entry:
            ctx = dict(entry["ctx"])
            for key, val in ctx.items():
                if isinstance(val, Exception):
                    ctx[key] = str(val)
            entry["ctx"] = ctx
        safe.append(entry)
    return safe


async def _handle_request_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Convert Pydantic / FastAPI input validation failures to 422.

    The raw Pydantic error list is surfaced in ``detail`` to help API
    consumers fix their payloads without needing to read server logs.
    """
    correlation_id = get_correlation_id()
    errors = _sanitize_validation_errors(exc.errors())
    # Build a concise human-readable summary for the top-level message.
    first = errors[0] if errors else {}
    field = " → ".join(str(loc) for loc in first.get("loc", []))
    summary = f"{field}: {first.get('msg', 'invalid input')}" if field else "Invalid request input."
    logger.warning(
        "[VALIDATION_ERROR] %s | path=%s | cid=%s | errors=%s",
        summary,
        request.url.path,
        correlation_id,
        errors,
    )
    return JSONResponse(
        status_code=422,
        content=ErrorResponse.build(
            error_code="VALIDATION_ERROR",
            message=summary,
            correlation_id=correlation_id,
            path=str(request.url.path),
            detail=errors,
        ).model_dump(),
    )


async def _handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
    """Wrap FastAPI / Starlette ``HTTPException`` in the standard envelope.

    This handler preserves the original status code.  Any existing
    ``WWW-Authenticate`` header is forwarded.
    """
    correlation_id = get_correlation_id()
    log_level = logging.WARNING if exc.status_code < 500 else logging.ERROR
    logger.log(
        log_level,
        "[HTTP_%d] %s | path=%s | cid=%s",
        exc.status_code,
        exc.detail,
        request.url.path,
        correlation_id,
    )

    headers: dict[str, str] = dict(exc.headers or {})
    # Map common HTTP status codes to a machine-readable error code.
    _code_map = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        409: "CONFLICT",
        422: "VALIDATION_ERROR",
        429: "TOO_MANY_REQUESTS",
        502: "BAD_GATEWAY",
        503: "SERVICE_UNAVAILABLE",
    }
    error_code = _code_map.get(exc.status_code, f"HTTP_{exc.status_code}")

    return JSONResponse(
        status_code=exc.status_code,
        headers=headers,
        content=ErrorResponse.build(
            error_code=error_code,
            message=str(exc.detail),
            correlation_id=correlation_id,
            path=str(request.url.path),
        ).model_dump(),
    )


async def _handle_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler for any exception that was not explicitly handled.

    Always logged at ERROR with a full stack trace.
    In debug/non-production environments the real exception type and message
    are surfaced in the response so developers can diagnose failures quickly.
    In production the message is intentionally vague to avoid leaking internals.
    """
    correlation_id = get_correlation_id()
    logger.error(
        "[UNHANDLED] %s: %s | path=%s | cid=%s",
        type(exc).__name__,
        exc,
        request.url.path,
        correlation_id,
        exc_info=True,
    )

    settings = get_settings()
    expose_detail = settings.DEBUG or settings.APP_ENV in ("development", "testing")

    if expose_detail:
        message = f"{type(exc).__name__}: {exc}"
    else:
        message = MSG_GENERIC_UNEXPECTED_ERROR

    return JSONResponse(
        status_code=500,
        content=ErrorResponse.build(
            error_code="INTERNAL_ERROR",
            message=message,
            correlation_id=correlation_id,
            path=str(request.url.path),
        ).model_dump(),
    )


# ── Registration ───────────────────────────────────────────────────────────


def register_exception_handlers(app: FastAPI) -> None:
    """Attach all global exception handlers to *app*.

    Call once in ``create_app`` — after the router is included so route-level
    handlers (if any) take precedence.

    Handler registration order matters: most-specific first.
    Starlette resolves handlers by walking the exception class MRO, so
    ``RIPBaseException`` catches all its subclasses automatically.
    """
    app.add_exception_handler(RIPBaseException, _handle_rip_exception)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _handle_request_validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(HTTPException, _handle_http_exception)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _handle_unhandled_exception)  # type: ignore[arg-type]
