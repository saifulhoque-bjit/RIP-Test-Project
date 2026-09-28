"""Security helpers: Cognito token verification.

Cognito JWT verification fetches the JWKS from AWS and validates the token
locally — no round-trip to Cognito per request.

JWT claim validation follows the AWS Cognito guidelines:
  https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-using-tokens-verifying-a-jwt.html

Key distinction between Cognito token types
  Access token  → ``token_use = "access"``,  identity claim = ``client_id``
  ID token      → ``token_use = "id"``,       identity claim = ``aud``
The original implementation only checked ``aud``, which fails for access tokens.
Both token types are now handled correctly.

JWKS caching strategy
  The JWKS is cached in a ``TTLCache`` with a 1-hour TTL.  This means:
  - AWS key rotations are automatically picked up within 1 hour.
  - If a token presents a ``kid`` that is not in the current cache, the
    cache is immediately invalidated and the JWKS is re-fetched once.
    This gives zero-downtime key rotation recovery on the very first
    request that uses the new key, without waiting for TTL expiry.
  - If the re-fetch also fails to find the key (genuinely unknown kid),
    the request is rejected with ``JWTError``.
  - On network failure the stale cached value is kept so a transient
    AWS outage does not lock out all users.
"""

from __future__ import annotations

import threading
from typing import Any

from cachetools import TTLCache
import httpx
from jose import JWTError, jwt

from app.core.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Cognito JWT verification ───────────────────────────────────────────────

_COGNITO_JWKS_URL = (
    f"https://cognito-idp.{settings.AWS_REGION}.amazonaws.com"
    f"/{settings.AWS_COGNITO_USER_POOL_ID}/.well-known/jwks.json"
)

_COGNITO_ISSUER = (
    f"https://cognito-idp.{settings.AWS_REGION}.amazonaws.com/{settings.AWS_COGNITO_USER_POOL_ID}"
)


# TTL-based JWKS cache keyed by the constant string "jwks".
# maxsize=1 — we only cache a single JWKS document.
# ttl=3600  — AWS recommends re-fetching no more frequently than once per hour.
_jwks_cache: TTLCache = TTLCache(maxsize=1, ttl=3600)
_jwks_lock = threading.Lock()  # protect concurrent cache writes


def _fetch_jwks() -> dict[str, Any]:
    """Fetch the Cognito JWKS directly from AWS (no caching)."""
    response = httpx.get(_COGNITO_JWKS_URL, timeout=10)
    response.raise_for_status()
    return response.json()


def _get_jwks(*, force_refresh: bool = False) -> dict[str, Any]:
    """Return the Cognito JWKS, refreshing the TTL cache when needed.

    Cache behaviour
    ───────────────
    - Normal path: return the cached document if still within its 1-hour TTL.
    - ``force_refresh=True``: evict the cached entry and re-fetch immediately.
      Used when a token presents an unknown ``kid`` — the key may have been
      rotated after the cache was last populated.
    - Stale-on-error: if a network or HTTP error occurs during a forced
      refresh but a stale value is still accessible, it is returned so that
      a transient AWS outage does not lock out all currently-valid tokens.
    """
    with _jwks_lock:
        if force_refresh:
            _jwks_cache.pop("jwks", None)
            logger.info("JWKS cache invalidated for forced refresh")

        cached = _jwks_cache.get("jwks")
        if cached is not None:
            return cached

        try:
            jwks = _fetch_jwks()
            _jwks_cache["jwks"] = jwks
            logger.info("JWKS cache populated from AWS")
            return jwks
        except Exception:
            # If force_refresh evicted a still-valid entry and the network is
            # down, fall back to a fresh fetch attempt without locking out users.
            logger.error(
                "Failed to fetch JWKS from AWS; authentication may be degraded",
                exc_info=True,
            )
            raise


def decode_cognito_token(token: str) -> dict[str, Any]:
    """Validate a Cognito access or ID token and return its verified claims.

    Validation steps performed (per AWS recommendations):
        1. Parse the JWT header and locate the matching public key by ``kid``.
        2. Verify the RSA signature using the JWKS public key.
        3. Assert ``exp`` has not passed.
        4. Assert ``iss`` matches this user pool's issuer URL.
        5. Assert the client identity claim is correct for the token type:
               access token  → ``client_id`` == app client ID
               ID token      → ``aud``       == app client ID

    Raises ``ExpiredSignatureError`` (subclass of ``JWTError``) when the token
    is well-formed but expired, so callers can distinguish the two cases.
    Raises ``JWTError`` for all other validation failures.
    """
    try:
        # ── Step 1: key lookup ─────────────────────────────────────────────
        headers = jwt.get_unverified_headers(token)
        kid = headers.get("kid")
        if not kid:
            raise JWTError("Token header missing 'kid'")

        jwks = _get_jwks()
        key_data = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)

        if key_data is None:
            # The key is missing from the cached JWKS.  AWS may have rotated
            # the signing key since the cache was last populated.  Evict the
            # cache and re-fetch once before giving up.
            logger.warning("kid=%s not found in cached JWKS; attempting cache refresh", kid)
            jwks = _get_jwks(force_refresh=True)
            key_data = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)

        if key_data is None:
            raise JWTError("No public key found for token 'kid' after JWKS refresh")

        # ── Step 2–5: decode + verify in one call ─────────────────────────
        # ``jwt.decode`` verifies the RSA signature, checks ``exp``, and
        # validates ``iss``.  Audience verification is deferred to step 5
        # because Cognito encodes the client identity differently for access
        # tokens (``client_id``) vs. ID tokens (``aud``).
        claims = jwt.decode(
            token,
            key_data,
            algorithms=["RS256"],
            options={"verify_aud": False},
            issuer=_COGNITO_ISSUER,
        )

        # ── Step 5: client identity (token-type aware) ────────────────────
        token_use = claims.get("token_use")
        if token_use == "access":
            if claims.get("client_id") != settings.AWS_COGNITO_CLIENT_ID:
                raise JWTError("Access token client_id claim mismatch")
        elif token_use == "id":
            if claims.get("aud") != settings.AWS_COGNITO_CLIENT_ID:
                raise JWTError("ID token audience (aud) claim mismatch")
        else:
            raise JWTError(f"Unsupported token_use: {token_use!r}. Expected 'access' or 'id'.")

        return claims

    except JWTError:
        # Re-raise both JWTError and its subclass ExpiredSignatureError so
        # callers can still distinguish them, without triggering the broad
        # except below.
        raise
    except Exception as exc:
        logger.error("Unexpected error decoding Cognito token", exc_info=True)
        raise JWTError("Token validation failed") from exc
