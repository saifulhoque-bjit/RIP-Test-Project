"""Middleware registration for the FastAPI application."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.core.rate_limiter import limiter, rate_limit_exceeded_handler
from app.utils.correlation import CorrelationIdMiddleware, SecurityHeadersMiddleware


def register_middleware(app: FastAPI) -> None:
    """Attach all middleware and rate-limiting handlers to *app*.

    FastAPI processes middleware in reverse-registration order
    (last added = outermost at request time):

        Outermost → Innermost at request time:
            SecurityHeadersMiddleware   ← always sets security headers
            CorrelationIdMiddleware     ← correlation ID available in all handlers
            CORSMiddleware              ← CORS preflight / response headers
    """
    # SecurityHeadersMiddleware is outermost so security headers are always
    # present regardless of which inner middleware or handler fires.
    app.add_middleware(SecurityHeadersMiddleware)

    # CorrelationIdMiddleware must be registered before exception handlers so
    # the correlation_id ContextVar is populated when any handler fires.
    app.add_middleware(CorrelationIdMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Accept",
            "Authorization",
            "Cache-Control",
            "Content-Type",
            "X-Correlation-ID",
            "X-Requested-With",
        ],
    )

    # Rate limiting — state must be set before routes are evaluated.
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)  # type: ignore[arg-type]
