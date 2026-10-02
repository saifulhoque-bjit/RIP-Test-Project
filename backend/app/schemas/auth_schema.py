"""Pydantic v2 schemas for AWS Cognito authentication endpoints.

Request/response models follow the project convention of being strict,
fully-typed, and never leaking internal detail.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.messages import DESC_AUTH_CONFIRM_CODE
from app.core.openapi_examples import (
    AUTH_FORGOT_PASSWORD_REQUEST_EXAMPLE,
    AUTH_LOGIN_REQUEST_EXAMPLE,
    AUTH_RESET_PASSWORD_REQUEST_EXAMPLE,
)

# ── Registration ───────────────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    """Payload for POST /auth/register."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    name: str = Field(min_length=1, max_length=200)

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        """Enforce Cognito's default password policy early to give a clear error."""
        errors: list[str] = []
        if not any(c.isupper() for c in v):
            errors.append("at least one uppercase letter")
        if not any(c.islower() for c in v):
            errors.append("at least one lowercase letter")
        if not any(c.isdigit() for c in v):
            errors.append("at least one digit")
        specials = set(r"""!@#$%^&*()_+-=[]{}|;:'",.<>?/\\""")
        if not any(c in specials for c in v):
            errors.append("at least one special character")
        if errors:
            raise ValueError(f"Password must contain: {', '.join(errors)}")
        return v


class RegisterResponse(BaseModel):
    """Returned after a successful Cognito sign-up."""

    email: str
    sub: str
    message: str


# ── Confirm sign-up ────────────────────────────────────────────────────────


class ConfirmSignUpRequest(BaseModel):
    """Payload for POST /auth/confirm."""

    email: EmailStr
    code: str = Field(min_length=6, max_length=10, description=DESC_AUTH_CONFIRM_CODE)


class ConfirmSignUpResponse(BaseModel):
    """Returned after a successful email confirmation."""

    email: str
    message: str
    email_verified: bool = True


# ── Login ──────────────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    """Payload for POST /auth/login."""

    model_config = ConfigDict(json_schema_extra={"example": AUTH_LOGIN_REQUEST_EXAMPLE})

    email: EmailStr
    password: str


# ── Forgot / reset password ─────────────────────────────────────────────────


class ForgotPasswordRequest(BaseModel):
    """Payload for POST /auth/forgot-password."""

    model_config = ConfigDict(json_schema_extra={"example": AUTH_FORGOT_PASSWORD_REQUEST_EXAMPLE})

    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    """Returned after requesting a password reset code.

    Always returned regardless of whether an account exists for the given
    email — see ``CognitoAuthService.forgot_password``.
    """

    email: str
    message: str


class ConfirmForgotPasswordRequest(BaseModel):
    """Payload for POST /auth/reset-password."""

    model_config = ConfigDict(json_schema_extra={"example": AUTH_RESET_PASSWORD_REQUEST_EXAMPLE})

    email: EmailStr
    code: str = Field(min_length=6, max_length=10, description=DESC_AUTH_CONFIRM_CODE)
    new_password: str = Field(min_length=8, max_length=256)

    @field_validator("new_password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        """Enforce Cognito's default password policy early to give a clear error.

        Duplicated from ``RegisterRequest.validate_password_strength`` rather
        than shared — matches this schema module's existing convention of
        one inline validator per field, not a shared mixin.
        """
        errors: list[str] = []
        if not any(c.isupper() for c in v):
            errors.append("at least one uppercase letter")
        if not any(c.islower() for c in v):
            errors.append("at least one lowercase letter")
        if not any(c.isdigit() for c in v):
            errors.append("at least one digit")
        specials = set(r"""!@#$%^&*()_+-=[]{}|;:'",.<>?/\\""")
        if not any(c in specials for c in v):
            errors.append("at least one special character")
        if errors:
            raise ValueError(f"Password must contain: {', '.join(errors)}")
        return v


class ConfirmForgotPasswordResponse(BaseModel):
    """Returned after a successful password reset."""

    email: str
    message: str


# ── Tokens ─────────────────────────────────────────────────────────────────


class TokenData(BaseModel):
    """Cognito token set returned on successful authentication or refresh."""

    access_token: str
    id_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int  # seconds until access_token expires (Cognito default: 3600)
    cognito_username: str | None = None
    email: str | None = None
    name: str | None = None
    roles: list[str] = []
    permissions: list[str] = []


# ── Refresh ────────────────────────────────────────────────────────────────


class RefreshRequest(BaseModel):
    """Payload for POST /auth/refresh.

    Either field can be None when the value is supplied via an HttpOnly cookie.
    ``username`` is the Cognito username (email when the user pool uses email
    as the username attribute) and is required to compute the SECRET_HASH for
    Cognito's REFRESH_TOKEN_AUTH flow when the app client has a client secret.
    """

    refresh_token: str | None = None
    username: str | None = None  # email / Cognito username


# ── User profile ───────────────────────────────────────────────────────────


class UserInfo(BaseModel):
    """Decoded Cognito token claims exposed via GET /auth/me."""

    sub: str
    email: str
    name: str | None = None
    email_verified: bool = False
