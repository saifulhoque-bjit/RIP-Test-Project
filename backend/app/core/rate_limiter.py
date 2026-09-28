"""Application-wide rate limiter (SlowAPI / limits).

Limits are configured through environment variables so they can be tightened
or relaxed per environment without a code change or redeploy:

    RATE_LIMIT_AUTH_REGISTER=5/minute
    RATE_LIMIT_AUTH_CONFIRM=10/minute
    RATE_LIMIT_AUTH_LOGIN=10/minute
    RATE_LIMIT_AUTH_REFRESH=30/minute
    RATE_LIMIT_AUTH_FORGOT_PASSWORD=5/minute
    RATE_LIMIT_AUTH_RESET_PASSWORD=10/minute

Format: ``"{count}/{period}"`` where period is ``second``, ``minute``,
``hour``, or ``day``.

Key function
────────────
``get_ipaddr`` is used instead of ``get_remote_address`` so that the real
client IP is resolved from ``X-Forwarded-For`` / ``X-Real-IP`` headers when
the service runs behind a reverse proxy or load balancer.

SECURITY NOTE: Only expose this service behind a trusted reverse proxy that
sets ``X-Forwarded-For`` — do not trust these headers from arbitrary clients.

Usage in a handler (import the pre-built callable, not a string):

    from app.core.rate_limiter import limiter, auth_login_limit
    from fastapi import Request

    @limiter.limit(auth_login_limit)
    async def login(self, request: Request, ...):
        ...

Middleware and the 429 exception handler are wired in ``app/core/middleware.py``.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_ipaddr

from app.core.config import settings
from app.core.error_response import ErrorResponse
from app.core.messages import MSG_RATE_LIMIT_EXCEEDED
from app.utils.correlation import get_correlation_id

# ── Limiter ────────────────────────────────────────────────────────────────
# default_limits=[] — no global limit; each endpoint opts in explicitly so
# unrestricted endpoints (e.g. health checks) are never throttled.

limiter = Limiter(key_func=get_ipaddr, default_limits=[])

# ── Retry-After helper ─────────────────────────────────────────────────────

_PERIOD_TO_SECONDS: dict[str, int] = {
    "second": 1,
    "minute": 60,
    "hour": 3_600,
    "day": 86_400,
}


# ── Per-endpoint limit strings ─────────────────────────────────────────────
# SlowAPI accepts either a static string or a callable ``(Request) -> str``.
# Plain strings are used here because class-based view handlers (bound methods)
# are wrapped at __init__ time — before a Request exists — which causes slowapi
# to fail when trying to resolve a callable limit.  Strings read from settings
# at import/startup time still honour environment-variable configuration; a
# process restart is required to pick up changes (which is normal practice).

auth_register_limit: str = settings.RATE_LIMIT_AUTH_REGISTER
auth_confirm_limit: str = settings.RATE_LIMIT_AUTH_CONFIRM
auth_login_limit: str = settings.RATE_LIMIT_AUTH_LOGIN
auth_refresh_limit: str = settings.RATE_LIMIT_AUTH_REFRESH
auth_forgot_password_limit: str = settings.RATE_LIMIT_AUTH_FORGOT_PASSWORD
auth_reset_password_limit: str = settings.RATE_LIMIT_AUTH_RESET_PASSWORD
source_upload_limit: str = settings.RATE_LIMIT_SOURCE_UPLOAD
source_download_limit: str = settings.RATE_LIMIT_SOURCE_DOWNLOAD
source_delete_limit: str = settings.RATE_LIMIT_SOURCE_DELETE
ai_regenerate_limit: str = settings.RATE_LIMIT_AI_REGENERATE
jira_sync_limit: str = settings.RATE_LIMIT_JIRA_SYNC
jira_config_limit: str = settings.RATE_LIMIT_JIRA_CONFIG
tap_sync_limit: str = settings.RATE_LIMIT_TAP_SYNC
tap_config_limit: str = settings.RATE_LIMIT_TAP_CONFIG
export_limit: str = settings.RATE_LIMIT_EXPORT
tenant_llm_provider_test_limit: str = settings.RATE_LIMIT_TENANT_LLM_PROVIDER_TEST
tenant_llm_provider_config_limit: str = settings.RATE_LIMIT_TENANT_LLM_PROVIDER_CONFIG
tenant_llm_provider_balance_limit: str = settings.RATE_LIMIT_TENANT_LLM_PROVIDER_BALANCE
invitation_resend_limit: str = settings.RATE_LIMIT_INVITATION_RESEND


# ── 429 exception handler ──────────────────────────────────────────────────


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Return a structured 429 response matching the application error envelope.

    ``Retry-After`` is set to the length of the limit window (e.g. 60 seconds
    for a per-minute limit) so well-behaved clients know exactly when to retry.
    """
    correlation_id = get_correlation_id()
    # Derive Retry-After from the limit string in the exception detail.
    # SlowAPI formats it as e.g. "20 per 1 minute"; normalise to "minute" for lookup.
    detail = str(exc.detail).lower()
    period = next(
        (p for p in _PERIOD_TO_SECONDS if p in detail),
        "minute",
    )
    retry_after = str(_PERIOD_TO_SECONDS[period])

    return JSONResponse(
        status_code=429,
        content=ErrorResponse.build(
            error_code="RATE_LIMIT_EXCEEDED",
            message=MSG_RATE_LIMIT_EXCEEDED.format(retry_after=retry_after),
            correlation_id=correlation_id,
            path=str(request.url.path),
        ).model_dump(),
        headers={"Retry-After": retry_after},
    )
