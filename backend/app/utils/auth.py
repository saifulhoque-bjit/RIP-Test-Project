"""Authentication utility helpers shared by auth-related modules.

Contains:
- Cookie name / TTL constants
- Optional bearer token extractor
- HttpOnly Secure cookie helpers (set_auth_cookies, clear_auth_cookies)
- Background-task helper (sync_verified_flag)
- Token enrichment helper (attach_user_roles)
"""

from __future__ import annotations

from typing import Any

from fastapi import Response
from fastapi.security import HTTPBearer
from jose import JWTError

from app.core.config import settings
from app.core.security import decode_cognito_token
from app.db.unit_of_work import UnitOfWork
from app.schemas.auth_schema import TokenData
from app.services.user_service import UserService
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ── Cookie configuration ──────────────────────────────────────────────────

COOKIE_ACCESS_TOKEN = "access_token"
COOKIE_ID_TOKEN = "id_token"
COOKIE_REFRESH_TOKEN = "refresh_token"
COOKIE_USERNAME = "cognito_username"

ACCESS_TOKEN_TTL = 4 * 3_600  # 4 hours
REFRESH_TOKEN_TTL = 7 * 86_400  # 7 days — refresh tokens aren't rotated on use (see
# AuthService.refresh_tokens), so keep the absolute
# lifetime short rather than relying on Cognito's 30-day default

# Optional bearer extraction (auto_error=False so logout still works without it)
bearer_optional = HTTPBearer(auto_error=False)


# ── Cookie helpers ────────────────────────────────────────────────────────


def set_auth_cookies(
    response: Response,
    tokens: TokenData,
    username: str,
) -> None:
    """Write all Cognito tokens into HttpOnly Secure cookies."""
    common: dict = {
        "httponly": True,
        "secure": settings.COOKIE_SECURE,
        "samesite": settings.COOKIE_SAMESITE,
    }
    response.set_cookie(
        COOKIE_ACCESS_TOKEN,
        tokens.access_token,
        max_age=ACCESS_TOKEN_TTL,
        **common,
    )
    response.set_cookie(
        COOKIE_ID_TOKEN,
        tokens.id_token,
        max_age=ACCESS_TOKEN_TTL,
        **common,
    )
    response.set_cookie(
        COOKIE_REFRESH_TOKEN,
        tokens.refresh_token,
        max_age=REFRESH_TOKEN_TTL,
        **common,
    )
    # Non-sensitive but kept HttpOnly to prevent JS tampering.
    response.set_cookie(
        COOKIE_USERNAME,
        username,
        max_age=REFRESH_TOKEN_TTL,
        **common,
    )


def clear_auth_cookies(response: Response) -> None:
    """Remove all auth cookies from the browser."""
    for name in (
        COOKIE_ACCESS_TOKEN,
        COOKIE_ID_TOKEN,
        COOKIE_REFRESH_TOKEN,
        COOKIE_USERNAME,
    ):
        response.delete_cookie(
            name,
            httponly=True,
            secure=settings.COOKIE_SECURE,
            samesite=settings.COOKIE_SAMESITE,
        )


# ── Background-task helpers ───────────────────────────────────────────────


def sync_verified_flag(*, email: str) -> None:
    """Post-confirmation: mark the user's email as verified in the local DB.

    Runs as a background task so that a transient DB failure never negates a
    successful Cognito confirmation.  The flag will be corrected on the next
    authenticated request if this task fails.
    """
    try:
        with UnitOfWork() as uow:
            user = uow.users.get_by_email(email)
            if user is not None and not user.is_verified:
                user.is_verified = True
                uow.add(user)
                # commit is handled by UnitOfWork.__exit__ on clean exit
        logger.info("is_verified synced for email=<redacted>")
    except Exception:  # noqa: BLE001
        # Background task — must never propagate.
        logger.error(
            "sync_verified_flag failed for email=<redacted>; flag will sync on next login",
            exc_info=True,
        )


# ── Token enrichment ──────────────────────────────────────────────────────


def attach_user_roles(tokens: TokenData, email: str) -> None:
    """Enrich *tokens* in-place with the user's profile and access data from the DB.

    Cognito authentication alone does not grant access: the caller must also
    have a local ``User`` row already, which only exists once an invitation
    has been accepted (or, for the Super Admin, from startup seeding) — see
    :meth:`UserService.get_authenticated_user`. Raises ``ForbiddenError`` (and
    so aborts the login) if no such row exists, or if the account has been
    deactivated.
    """
    claims: dict[str, Any] = {}
    try:
        claims = decode_cognito_token(tokens.id_token)
    except JWTError:
        logger.warning(
            "Could not decode id_token claims during login sync; falling back to email lookup only"
        )

    claim_sub = claims.get("sub")
    claim_email = claims.get("email")
    claim_username = claims.get("cognito:username") or claims.get("username")
    claim_name = claims.get("name")
    claim_email_verified = claims.get("email_verified")

    sub = str(claim_sub).strip() if claim_sub is not None else ""
    resolved_email = str(claim_email).strip() if claim_email is not None else email
    name = str(claim_name).strip() if claim_name is not None else None
    username = str(claim_username).strip() if claim_username is not None else None
    is_verified = bool(claim_email_verified) if claim_email_verified is not None else True

    with UnitOfWork() as uow:
        db_user = UserService(uow).get_authenticated_user(
            sub=sub,
            email=resolved_email,
            name=name,
            is_verified=is_verified,
            username=username,
        )

        tokens.cognito_username = db_user.username or username or tokens.cognito_username
        tokens.email = db_user.email
        tokens.name = db_user.name
        tokens.roles = [r.name for r in db_user.roles]
        tokens.permissions = [p.name for r in db_user.roles for p in r.permissions]
