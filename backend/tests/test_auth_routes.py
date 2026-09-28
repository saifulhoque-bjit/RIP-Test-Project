"""Unit tests for auth routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks, Response
from fastapi.security import HTTPAuthorizationCredentials
import pytest
from starlette.requests import Request

from app.core.exceptions import UnauthorizedError, ValidationError as AppValidationError
from app.routes.v1.auth import (
    confirm,
    forgot_password,
    login,
    logout,
    me,
    refresh,
    register,
    reset_password,
)
from app.schemas.auth_schema import (
    ConfirmForgotPasswordRequest,
    ConfirmForgotPasswordResponse,
    ConfirmSignUpRequest,
    ConfirmSignUpResponse,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    TokenData,
)
from tests.conftest import make_user


def _make_request() -> Request:
    """Minimal real starlette Request — required by the SlowAPI rate-limiter wrapper."""
    return Request(
        scope={"type": "http", "method": "POST", "path": "/", "query_string": b"", "headers": []}
    )


def _tokens() -> TokenData:
    return TokenData(
        access_token="access",
        id_token="id",
        refresh_token="refresh",
        expires_in=3600,
    )


@pytest.mark.asyncio
async def test_register_returns_cognito_result_without_local_sync() -> None:
    """Registration only provisions Cognito — no local User row is created;
    access still requires an accepted invitation (see get_authenticated_user)."""
    payload = RegisterRequest(email="a@example.com", password="Strong@123", name="Alice")

    with patch(
        "app.routes.v1.auth.CognitoAuthService.register",
        new=AsyncMock(
            return_value=RegisterResponse(email=payload.email, sub="sub-1", message="ok")
        ),
    ):
        result = await register(_make_request(), payload)

    assert result.success is True
    assert result.data is not None
    assert result.data.sub == "sub-1"


@pytest.mark.asyncio
async def test_confirm_adds_verified_sync_task() -> None:
    payload = ConfirmSignUpRequest(email="a@example.com", code="123456")
    tasks = BackgroundTasks()

    with patch(
        "app.routes.v1.auth.CognitoAuthService.confirm_sign_up",
        new=AsyncMock(
            return_value=ConfirmSignUpResponse(
                email=payload.email, message="confirmed", email_verified=True
            )
        ),
    ):
        result = await confirm(_make_request(), payload, tasks)

    assert result.success is True
    assert result.data is not None
    assert result.data.email_verified is True
    assert len(tasks.tasks) == 1


@pytest.mark.asyncio
async def test_login_sets_cookies_and_enriches_roles() -> None:
    response = Response()
    payload = LoginRequest(email="a@example.com", password="x")
    tokens = _tokens()
    tokens.cognito_username = "a-example-com"

    with (
        patch(
            "app.routes.v1.auth.CognitoAuthService.login",
            new=AsyncMock(return_value=tokens),
        ),
        patch("app.routes.v1.auth.attach_user_roles") as mock_attach,
        patch("app.routes.v1.auth.set_auth_cookies") as mock_set_cookies,
    ):
        result = await login(_make_request(), payload, response)

    assert result.success is True
    assert result.data is not None
    mock_attach.assert_called_once()
    mock_set_cookies.assert_called_once_with(
        response,
        tokens,
        username="a-example-com",
    )


@pytest.mark.asyncio
async def test_refresh_validates_required_inputs() -> None:
    with pytest.raises(UnauthorizedError):
        await refresh(
            _make_request(), Response(), body=None, cookie_refresh_token=None, cookie_username=None
        )

    with pytest.raises(AppValidationError):
        await refresh(
            _make_request(),
            Response(),
            body=RefreshRequest(refresh_token="rt", username=None),
            cookie_refresh_token=None,
            cookie_username=None,
        )


@pytest.mark.asyncio
async def test_refresh_success_uses_body_values() -> None:
    body = RefreshRequest(refresh_token="rt", username="a@example.com")
    tokens = _tokens()

    with (
        patch(
            "app.routes.v1.auth.CognitoAuthService.refresh_tokens",
            new=AsyncMock(return_value=tokens),
        ) as mock_refresh,
        patch("app.routes.v1.auth.set_auth_cookies") as mock_set,
    ):
        result = await refresh(_make_request(), Response(), body=body)

    assert result.success is True
    assert result.data is not None
    assert result.data.cognito_username == "a@example.com"
    mock_refresh.assert_awaited_once_with(refresh_token="rt", username="a@example.com")
    mock_set.assert_called_once()


@pytest.mark.asyncio
async def test_logout_requires_access_token_and_clears_cookie_on_success() -> None:
    with pytest.raises(UnauthorizedError):
        await logout(Response(), credentials=None, cookie_access_token=None)

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="access")
    with (
        patch(
            "app.routes.v1.auth.CognitoAuthService.logout",
            new=AsyncMock(return_value=None),
        ) as mock_logout,
        patch("app.routes.v1.auth.clear_auth_cookies") as mock_clear,
    ):
        result = await logout(Response(), credentials=creds, cookie_access_token=None)

    assert result.success is True
    mock_logout.assert_awaited_once_with(access_token="access")
    mock_clear.assert_called_once()


@pytest.mark.asyncio
async def test_forgot_password_returns_generic_response() -> None:
    payload = ForgotPasswordRequest(email="a@example.com")

    with patch(
        "app.routes.v1.auth.CognitoAuthService.forgot_password",
        new=AsyncMock(
            return_value=ForgotPasswordResponse(email=payload.email, message="generic ok")
        ),
    ) as mock_forgot:
        result = await forgot_password(_make_request(), payload)

    assert result.success is True
    assert result.data is not None
    assert result.message == "generic ok"
    mock_forgot.assert_awaited_once_with(email="a@example.com")


@pytest.mark.asyncio
async def test_reset_password_delegates_to_service() -> None:
    payload = ConfirmForgotPasswordRequest(
        email="a@example.com", code="123456", new_password="Sup3rStrong!Pass"
    )

    with patch(
        "app.routes.v1.auth.CognitoAuthService.confirm_forgot_password",
        new=AsyncMock(
            return_value=ConfirmForgotPasswordResponse(email=payload.email, message="reset ok")
        ),
    ) as mock_reset:
        result = await reset_password(_make_request(), payload)

    assert result.success is True
    assert result.data is not None
    mock_reset.assert_awaited_once_with(
        email="a@example.com", code="123456", new_password="Sup3rStrong!Pass"
    )


@pytest.mark.asyncio
async def test_me_returns_current_user_profile() -> None:
    user = make_user(name="Bob", email="bob@example.com", is_verified=True)

    result = await me(current_user=user)

    assert result.success is True
    assert result.data is not None
    assert result.data.email == "bob@example.com"
