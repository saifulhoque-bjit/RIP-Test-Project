"""Unit tests for app.core.security — decode_cognito_token.

Strategy:
- Generate a real RSA key pair at test-module load time.
- Patch ``_get_jwks`` so the function uses our test JWKS without hitting AWS.
- Patch ``settings`` values (issuer, client_id) to match the tokens we sign.
- Every validation branch (expiry, issuer, audience, token_use, kid) is
  exercised in isolation.
"""

from __future__ import annotations

import time
from unittest.mock import patch

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from jose import ExpiredSignatureError, JWTError, jwt
from jose.backends import RSAKey
import pytest

# ── Test RSA key pair (generated once per module) ──────────────────────────

_PRIVATE_KEY = rsa.generate_private_key(
    public_exponent=65537, key_size=2048, backend=default_backend()
)
_KID = "test-kid-1"
_ALG = "RS256"

# PEM bytes used for signing
_PRIVATE_PEM: bytes = _PRIVATE_KEY.private_bytes(
    Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()
)

# JWKS entry used for verification
_PUB_JWK: dict = {
    **RSAKey(_PRIVATE_PEM, algorithm=_ALG).public_key().to_dict(),
    "kid": _KID,
    "use": "sig",
}

_TEST_REGION = "us-east-1"
_TEST_POOL_ID = "us-east-1_TestPool"
_TEST_CLIENT_ID = "test-client-id"
_TEST_ISSUER = f"https://cognito-idp.{_TEST_REGION}.amazonaws.com/{_TEST_POOL_ID}"


def _make_token(
    claims: dict,
    kid: str = _KID,
    algorithm: str = _ALG,
) -> str:
    """Sign *claims* with the test private key."""
    return jwt.encode(claims, _PRIVATE_PEM, algorithm=algorithm, headers={"kid": kid})


def _base_access_claims(
    *,
    exp_offset: int = 3600,
    issuer: str = _TEST_ISSUER,
    client_id: str = _TEST_CLIENT_ID,
) -> dict:
    return {
        "sub": "sub-access-123",
        "iss": issuer,
        "token_use": "access",
        "client_id": client_id,
        "exp": int(time.time()) + exp_offset,
    }


def _base_id_claims(
    *,
    exp_offset: int = 3600,
    issuer: str = _TEST_ISSUER,
    aud: str = _TEST_CLIENT_ID,
) -> dict:
    return {
        "sub": "sub-id-456",
        "iss": issuer,
        "token_use": "id",
        "aud": aud,
        "email": "alice@example.com",
        "exp": int(time.time()) + exp_offset,
    }


# ── Shared patch context ───────────────────────────────────────────────────

_JWKS_PATCH = patch(
    "app.core.security._get_jwks",
    return_value={"keys": [_PUB_JWK]},
)
_SETTINGS_PATCH = patch(
    "app.core.security.settings",
    **{
        "AWS_REGION": _TEST_REGION,
        "AWS_COGNITO_USER_POOL_ID": _TEST_POOL_ID,
        "AWS_COGNITO_CLIENT_ID": _TEST_CLIENT_ID,
    },
)
# The issuer is computed as a module-level constant at import time; patch it directly.
_ISSUER_PATCH = patch("app.core.security._COGNITO_ISSUER", _TEST_ISSUER)


# ── Access token tests ─────────────────────────────────────────────────────


class TestDecodeAccessToken:
    def test_valid_access_token_returns_claims(self) -> None:
        token = _make_token(_base_access_claims())
        with _JWKS_PATCH, _SETTINGS_PATCH, _ISSUER_PATCH:
            from app.core.security import decode_cognito_token

            claims = decode_cognito_token(token)
        assert claims["sub"] == "sub-access-123"
        assert claims["token_use"] == "access"

    def test_expired_access_token_raises_expired_signature_error(self) -> None:
        token = _make_token(_base_access_claims(exp_offset=-10))
        with _JWKS_PATCH, _SETTINGS_PATCH, _ISSUER_PATCH:
            from app.core.security import decode_cognito_token

            with pytest.raises(ExpiredSignatureError):
                decode_cognito_token(token)

    def test_wrong_issuer_raises_jwt_error(self) -> None:
        token = _make_token(_base_access_claims(issuer="https://wrong.issuer.com"))
        with _JWKS_PATCH, _SETTINGS_PATCH, _ISSUER_PATCH:
            from app.core.security import decode_cognito_token

            with pytest.raises(JWTError, match="[Ii]ssuer"):
                decode_cognito_token(token)

    def test_wrong_client_id_raises_jwt_error(self) -> None:
        token = _make_token(_base_access_claims(client_id="wrong-client"))
        with _JWKS_PATCH, _SETTINGS_PATCH, _ISSUER_PATCH:
            from app.core.security import decode_cognito_token

            with pytest.raises(JWTError, match="client_id"):
                decode_cognito_token(token)

    def test_unknown_kid_raises_jwt_error(self) -> None:
        token = _make_token(_base_access_claims(), kid="unknown-kid")
        with _JWKS_PATCH, _SETTINGS_PATCH, _ISSUER_PATCH:
            from app.core.security import decode_cognito_token

            with pytest.raises(JWTError, match="No public key found"):
                decode_cognito_token(token)

    def test_missing_kid_header_raises_jwt_error(self) -> None:
        # Build token without kid by manipulating headers
        claims = _base_access_claims()
        token = jwt.encode(claims, _PRIVATE_PEM, algorithm=_ALG, headers={})
        # Strip the kid from headers forcibly by encoding without it
        with _JWKS_PATCH, _SETTINGS_PATCH, _ISSUER_PATCH:
            from app.core.security import decode_cognito_token

            with pytest.raises(JWTError):
                decode_cognito_token(token)

    def test_tampered_signature_raises_jwt_error(self) -> None:
        token = _make_token(_base_access_claims())
        # Corrupt the signature (last segment)
        parts = token.split(".")
        parts[2] = parts[2][:-4] + "XXXX"
        tampered = ".".join(parts)
        with _JWKS_PATCH, _SETTINGS_PATCH, _ISSUER_PATCH:
            from app.core.security import decode_cognito_token

            with pytest.raises(JWTError):
                decode_cognito_token(tampered)


# ── ID token tests ─────────────────────────────────────────────────────────


class TestDecodeIdToken:
    def test_valid_id_token_returns_claims(self) -> None:
        token = _make_token(_base_id_claims())
        with _JWKS_PATCH, _SETTINGS_PATCH, _ISSUER_PATCH:
            from app.core.security import decode_cognito_token

            claims = decode_cognito_token(token)
        assert claims["token_use"] == "id"
        assert claims["email"] == "alice@example.com"

    def test_wrong_audience_raises_jwt_error(self) -> None:
        token = _make_token(_base_id_claims(aud="wrong-audience"))
        with _JWKS_PATCH, _SETTINGS_PATCH, _ISSUER_PATCH:
            from app.core.security import decode_cognito_token

            with pytest.raises(JWTError, match="aud"):
                decode_cognito_token(token)

    def test_expired_id_token_raises_expired_signature_error(self) -> None:
        token = _make_token(_base_id_claims(exp_offset=-60))
        with _JWKS_PATCH, _SETTINGS_PATCH, _ISSUER_PATCH:
            from app.core.security import decode_cognito_token

            with pytest.raises(ExpiredSignatureError):
                decode_cognito_token(token)


# ── Unsupported token_use ──────────────────────────────────────────────────


class TestUnsupportedTokenUse:
    def test_refresh_token_use_raises_jwt_error(self) -> None:
        claims = {
            "sub": "sub-x",
            "iss": _TEST_ISSUER,
            "token_use": "refresh",
            "exp": int(time.time()) + 3600,
        }
        token = _make_token(claims)
        with _JWKS_PATCH, _SETTINGS_PATCH, _ISSUER_PATCH:
            from app.core.security import decode_cognito_token

            with pytest.raises(JWTError, match="Unsupported token_use"):
                decode_cognito_token(token)

    def test_missing_token_use_raises_jwt_error(self) -> None:
        claims = {
            "sub": "sub-x",
            "iss": _TEST_ISSUER,
            "exp": int(time.time()) + 3600,
        }
        token = _make_token(claims)
        with _JWKS_PATCH, _SETTINGS_PATCH, _ISSUER_PATCH:
            from app.core.security import decode_cognito_token

            with pytest.raises(JWTError, match="Unsupported token_use"):
                decode_cognito_token(token)


# ── JWKS cache ─────────────────────────────────────────────────────────────


class TestGetJwksCache:
    def test_jwks_is_fetched_via_http(self) -> None:
        """_get_jwks should call httpx.get and return parsed JSON."""
        from unittest.mock import MagicMock

        mock_response = MagicMock()
        mock_response.json.return_value = {"keys": []}
        mock_response.raise_for_status = MagicMock()

        with (
            patch("app.core.security.httpx.get", return_value=mock_response) as mock_get,
            patch("app.core.security._COGNITO_JWKS_URL", "https://test.url/jwks.json"),
        ):
            from app.core.security import _get_jwks, _jwks_cache

            _jwks_cache.clear()  # evict the TTLCache so _fetch_jwks is called
            result = _get_jwks()

        mock_get.assert_called_once()
        assert result == {"keys": []}
