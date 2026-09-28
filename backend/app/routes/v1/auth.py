"""Route handlers for /auth — v1.

Endpoint summary
────────────────
Public (no authentication required):
    POST /auth/register        — create a new Cognito user
    POST /auth/confirm         — verify email with Cognito OTP code
    POST /auth/login           — authenticate and issue tokens (sets HttpOnly cookies)
    POST /auth/refresh         — exchange refresh token for a new token pair
    POST /auth/logout          — globally invalidate all tokens for this user
    POST /auth/forgot-password — request a password reset code by email; always
                                  returns the same generic response, whether or
                                  not an account exists for that email
    POST /auth/reset-password  — set a new password using the emailed code

Authenticated:
    GET  /auth/me        — return the caller's profile from the database

Design rules
────────────
- Zero business logic here — all decisions live in CognitoAuthService.
- Rate limits are enforced via @limiter.limit() per handler.
- Exception handling is centralised in app/core/exception_handlers.py.
- ``login`` genuinely needs ``async def`` (it awaits
  ``CognitoAuthService.login``'s real Cognito I/O) — but the sync
  ``attach_user_roles`` call inside it (JWT decode + Postgres) is wrapped in
  ``run_in_threadpool`` so it doesn't block the event loop for other
  concurrent requests while this coroutine runs.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from starlette.concurrency import run_in_threadpool

from app.core.exceptions import UnauthorizedError, ValidationError as AppValidationError
from app.core.messages import (
    MSG_AUTH_ACCESS_TOKEN_REQUIRED,
    MSG_AUTH_LOGIN_SUCCESS,
    MSG_AUTH_LOGOUT_SUCCESS,
    MSG_AUTH_REFRESH_SUCCESS,
    MSG_AUTH_REFRESH_TOKEN_REQUIRED,
    MSG_AUTH_USERNAME_REQUIRED_FOR_REFRESH,
    SUMMARY_AUTH_CONFIRM,
    SUMMARY_AUTH_FORGOT_PASSWORD,
    SUMMARY_AUTH_LOGIN,
    SUMMARY_AUTH_LOGOUT,
    SUMMARY_AUTH_ME,
    SUMMARY_AUTH_REFRESH,
    SUMMARY_AUTH_REGISTER,
    SUMMARY_AUTH_RESET_PASSWORD,
)
from app.core.rate_limiter import (
    auth_confirm_limit,
    auth_forgot_password_limit,
    auth_login_limit,
    auth_refresh_limit,
    auth_register_limit,
    auth_reset_password_limit,
    limiter,
)
from app.deps import get_current_db_user
from app.models.postgres.user_model import User
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
    UserInfo,
)
from app.services.auth_service import CognitoAuthService
from app.utils.auth import (
    attach_user_roles,
    bearer_optional,
    clear_auth_cookies,
    set_auth_cookies,
    sync_verified_flag,
)
from app.utils.logger import get_logger
from app.utils.response import ApiResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])

# ── Annotated dependency aliases ────────────────────────────────────────────

CurrentUser = Annotated[User, Depends(get_current_db_user)]
BearerCreds = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_optional)]
CookieRefreshToken = Annotated[str | None, Cookie(alias="refresh_token")]
CookieUsername = Annotated[str | None, Cookie(alias="cognito_username")]
CookieAccessToken = Annotated[str | None, Cookie(alias="access_token")]


# ── Handlers ────────────────────────────────────────────────────────────────


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    summary=SUMMARY_AUTH_REGISTER,
)
@limiter.limit(auth_register_limit)
async def register(
    request: Request,
    payload: RegisterRequest,
) -> ApiResponse[RegisterResponse]:
    """POST /auth/register — create a new Cognito user.

    Cognito will send a verification email automatically when the User Pool
    has email set as an auto-verified attribute.

    This only provisions the Cognito identity — it does **not** create a
    local user record. Access to the app is invitation-only: the caller
    still needs an invitation accepted (``InvitationService.accept``) before
    they can log in (see ``UserService.get_authenticated_user``); a
    self-registered identity with no invitation can never log in.
    """
    result = await CognitoAuthService().register(
        email=payload.email,
        password=payload.password,
        name=payload.name,
    )
    return ApiResponse.ok(data=result, message=result.message)


@router.post(
    "/confirm",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_AUTH_CONFIRM,
)
@limiter.limit(auth_confirm_limit)
async def confirm(
    request: Request,
    payload: ConfirmSignUpRequest,
    background_tasks: BackgroundTasks,
) -> ApiResponse[ConfirmSignUpResponse]:
    """POST /auth/confirm — verify the email address with the Cognito code.

    After confirmation the local user record's ``is_verified`` flag is updated
    to True.
    """
    result = await CognitoAuthService().confirm_sign_up(
        email=payload.email,
        code=payload.code,
    )
    background_tasks.add_task(sync_verified_flag, email=payload.email)
    return ApiResponse.ok(data=result, message=result.message)


@router.post(
    "/login",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_AUTH_LOGIN,
)
@limiter.limit(auth_login_limit)
async def login(
    request: Request,
    payload: LoginRequest,
    response: Response,
) -> ApiResponse[TokenData]:
    """POST /auth/login — authenticate and issue tokens.

    Tokens are returned both in the JSON body **and** in HttpOnly cookies.
    Browser-based clients should rely on the cookies; API / mobile clients
    should use the JSON body tokens with an ``Authorization: Bearer`` header.
    """
    tokens = await CognitoAuthService().login(
        email=payload.email,
        password=payload.password,
    )
    # attach_user_roles is sync (JWT decode + Postgres UnitOfWork work) — run
    # it off the event loop so it doesn't block other concurrent requests
    # while this coroutine is otherwise fully async (Cognito I/O above).
    await run_in_threadpool(attach_user_roles, tokens, payload.email)
    set_auth_cookies(
        response,
        tokens,
        username=tokens.cognito_username or payload.email,
    )
    return ApiResponse.ok(data=tokens, message=MSG_AUTH_LOGIN_SUCCESS)


@router.post(
    "/refresh",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_AUTH_REFRESH,
)
@limiter.limit(auth_refresh_limit)
async def refresh(
    request: Request,
    response: Response,
    body: RefreshRequest | None = None,
    cookie_refresh_token: CookieRefreshToken = None,
    cookie_username: CookieUsername = None,
) -> ApiResponse[TokenData]:
    """POST /auth/refresh — exchange a refresh token for a new token pair.

    Token and username are resolved from (in priority order):
        1. Request body fields (for API / mobile clients)
        2. HttpOnly cookies (for browser-based clients)
    """
    refresh_token = (body.refresh_token if body else None) or cookie_refresh_token
    username = (body.username if body else None) or cookie_username

    if not refresh_token:
        raise UnauthorizedError(MSG_AUTH_REFRESH_TOKEN_REQUIRED)
    if not username:
        raise AppValidationError(MSG_AUTH_USERNAME_REQUIRED_FOR_REFRESH)

    tokens = await CognitoAuthService().refresh_tokens(
        refresh_token=refresh_token,
        username=username,
    )
    tokens.cognito_username = username
    set_auth_cookies(response, tokens, username=username)
    return ApiResponse.ok(data=tokens, message=MSG_AUTH_REFRESH_SUCCESS)


@router.post(
    "/logout",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_AUTH_LOGOUT,
)
async def logout(
    response: Response,
    credentials: BearerCreds = None,
    cookie_access_token: CookieAccessToken = None,
) -> ApiResponse[None]:
    """POST /auth/logout — globally invalidate all tokens for this user.

    The access token is resolved from (in priority order):
        1. ``Authorization: Bearer <token>`` header
        2. ``access_token`` HttpOnly cookie
    """
    access_token = (credentials.credentials if credentials else None) or cookie_access_token

    if not access_token:
        raise UnauthorizedError(MSG_AUTH_ACCESS_TOKEN_REQUIRED)

    await CognitoAuthService().logout(access_token=access_token)
    clear_auth_cookies(response)
    return ApiResponse.ok(message=MSG_AUTH_LOGOUT_SUCCESS)


@router.post(
    "/forgot-password",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_AUTH_FORGOT_PASSWORD,
)
@limiter.limit(auth_forgot_password_limit)
async def forgot_password(
    request: Request,
    payload: ForgotPasswordRequest,
) -> ApiResponse[ForgotPasswordResponse]:
    """POST /auth/forgot-password — request a password reset code by email.

    Always returns the same generic response — HTTP 200 with an identical
    message — whether or not an account exists for the given email. This is
    deliberate: never branch this endpoint's response, status code, or
    timing on account existence, or it becomes a user-enumeration oracle.
    If an account does exist, Cognito emails it a 6-digit code to be used
    with ``POST /auth/reset-password``.
    """
    result = await CognitoAuthService().forgot_password(email=payload.email)
    return ApiResponse.ok(data=result, message=result.message)


@router.post(
    "/reset-password",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_AUTH_RESET_PASSWORD,
)
@limiter.limit(auth_reset_password_limit)
async def reset_password(
    request: Request,
    payload: ConfirmForgotPasswordRequest,
) -> ApiResponse[ConfirmForgotPasswordResponse]:
    """POST /auth/reset-password — set a new password using the code emailed
    by ``POST /auth/forgot-password``.

    Unlike ``forgot-password``, this endpoint's errors are specific (wrong or
    expired code, weak password) — by this point the caller must already
    have a valid code, so there is nothing left to hide about account
    existence.
    """
    result = await CognitoAuthService().confirm_forgot_password(
        email=payload.email,
        code=payload.code,
        new_password=payload.new_password,
    )
    return ApiResponse.ok(data=result, message=result.message)


@router.get(
    "/me",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_AUTH_ME,
)
async def me(
    current_user: CurrentUser,
) -> ApiResponse[UserInfo]:
    """GET /auth/me — return the caller's profile from the database.

    Uses the PostgreSQL user record (synced from Cognito) so the response
    always contains the real email address regardless of token type.
    """
    user_info = UserInfo(
        sub=current_user.cognito_sub,
        email=current_user.email,
        name=current_user.name,
        email_verified=current_user.is_verified,
    )
    return ApiResponse.ok(data=user_info)
