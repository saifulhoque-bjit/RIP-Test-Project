"""AWS Cognito authentication service.

All boto3 calls are synchronous (boto3 does not support asyncio natively).
They are wrapped with ``asyncio.to_thread`` so they never block the event loop.

Cognito ClientError codes are mapped to application exceptions:
    UsernameExistsException      → ConflictError
    NotAuthorizedException       → UnauthorizedError  (hides user-existence detail)
    UserNotFoundException         → UnauthorizedError  (hides user-existence detail)
    UserNotConfirmedException     → UnauthorizedError
    InvalidPasswordException      → ValidationError
    InvalidParameterException     → ValidationError
    TooManyRequestsException      → CognitoError
    LimitExceededException        → CognitoError
    CodeDeliveryFailureException  → CognitoError
    <anything else>               → CognitoError

``forgot_password`` is the one deliberate exception to this table: it
swallows ``UserNotFoundException``/``InvalidParameterException`` instead of
raising, so it never reveals whether an account exists for a given email —
see its docstring.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
from typing import TYPE_CHECKING, Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings
from app.core.exceptions import (
    CognitoError,
    ConflictError,
    UnauthorizedError,
    ValidationError as AppValidationError,
)
from app.core.messages import (
    MSG_AUTH_AWS_UNAVAILABLE,
    MSG_AUTH_CODE_EXPIRED,
    MSG_AUTH_CONFIRM_SUCCESS,
    MSG_AUTH_EMAIL_ALREADY_CONFIRMED,
    MSG_AUTH_EMAIL_ALREADY_EXISTS,
    MSG_AUTH_EMAIL_DELIVERY_FAILED,
    MSG_AUTH_FORGOT_PASSWORD_SUCCESS,
    MSG_AUTH_INVALID_CONFIRMATION_CODE,
    MSG_AUTH_INVALID_CREDENTIALS,
    MSG_AUTH_INVALID_PARAMETER,
    MSG_AUTH_PASSWORD_REQUIREMENTS,
    MSG_AUTH_REGISTER_NOT_AUTHORIZED,
    MSG_AUTH_REGISTRATION_SUCCESS,
    MSG_AUTH_RESET_PASSWORD_SUCCESS,
    MSG_AUTH_SERVICE_ERROR,
    MSG_AUTH_TOO_MANY_REQUESTS,
)
from app.schemas.auth_schema import (
    ConfirmForgotPasswordResponse,
    ConfirmSignUpResponse,
    ForgotPasswordResponse,
    RegisterResponse,
    TokenData,
)
from app.utils.logger import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


# ── Private helpers ────────────────────────────────────────────────────────


def _compute_secret_hash(username: str) -> str:
    """Compute the HMAC-SHA256 secret hash required by Cognito app clients
    that have a client secret configured.

    SECRET_HASH = Base64( HMAC-SHA256( username + client_id, client_secret ) )

    Returns an empty string when ``AWS_COGNITO_CLIENT_SECRET`` is not set so
    that non-secret app clients remain compatible.
    """
    if not settings.AWS_COGNITO_CLIENT_SECRET:
        return ""
    message = username + settings.AWS_COGNITO_CLIENT_ID
    digest = hmac.new(
        settings.AWS_COGNITO_CLIENT_SECRET.encode("utf-8"),
        msg=message.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode()


def _derive_cognito_username(email: str) -> str:
    """Derive a Cognito-safe username from an email address.

    Cognito usernames must not contain "@" or ".", so both are replaced
    with "-". Must stay deterministic since it is recomputed on every call
    (register, confirm, login-adjacent admin operations) rather than stored.
    """
    return email.replace("@", "-").replace(".", "-")


def _cognito_client() -> Any:
    """Create a new boto3 Cognito IDP client.

    A new client is created per call (inside ``asyncio.to_thread``) to avoid
    sharing a single boto3 client across threads, which is not thread-safe.
    """
    return boto3.client(
        "cognito-idp",
        region_name=settings.AWS_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
    )


def _add_secret_hash(params: dict[str, str], username: str, key: str = "SecretHash") -> None:
    """Inject the Cognito secret hash into *params* when a client secret is set.

    The key name differs by API call:
      ``sign_up``        → top-level kwarg   → key="SecretHash"  (default)
      ``initiate_auth``  → inside AuthParameters dict → key="SECRET_HASH"
    """
    secret_hash = _compute_secret_hash(username)
    if secret_hash:
        params[key] = secret_hash


def _map_cognito_error(exc: ClientError, context: str) -> None:
    """Convert a Cognito ``ClientError`` into an application-level exception.

    The caller must always call ``raise`` after this function; the function
    itself always raises and never returns normally.
    """
    code: str = exc.response["Error"]["Code"]
    message: str = exc.response["Error"]["Message"]
    logger.warning(
        "Cognito error [%s] during %s: %s",
        code,
        context,
        message,
    )

    if code == "UsernameExistsException":
        raise ConflictError(MSG_AUTH_EMAIL_ALREADY_EXISTS)

    if code == "NotAuthorizedException":
        if context == "register":
            # During sign-up this code means the app-client SecretHash is
            # missing/wrong, or the User Pool has admin-only registration.
            # Surface the real Cognito message so it is actionable.
            raise CognitoError(MSG_AUTH_REGISTER_NOT_AUTHORIZED.format(detail=message))
        # For login/token flows keep the response vague to prevent user-enumeration.
        raise UnauthorizedError(MSG_AUTH_INVALID_CREDENTIALS)

    if code in ("UserNotFoundException", "UserNotConfirmedException"):
        raise UnauthorizedError(MSG_AUTH_INVALID_CREDENTIALS)

    if code == "InvalidPasswordException":
        raise AppValidationError(MSG_AUTH_PASSWORD_REQUIREMENTS.format(detail=message))

    if code == "InvalidParameterException":
        raise AppValidationError(MSG_AUTH_INVALID_PARAMETER.format(detail=message))

    if code in ("TooManyRequestsException", "LimitExceededException"):
        raise CognitoError(MSG_AUTH_TOO_MANY_REQUESTS)

    if code == "CodeDeliveryFailureException":
        raise CognitoError(MSG_AUTH_EMAIL_DELIVERY_FAILED)

    if code == "CodeMismatchException":
        raise AppValidationError(MSG_AUTH_INVALID_CONFIRMATION_CODE)

    if code == "ExpiredCodeException":
        raise AppValidationError(MSG_AUTH_CODE_EXPIRED)

    if code == "AliasExistsException":
        raise ConflictError(MSG_AUTH_EMAIL_ALREADY_CONFIRMED)

    # Unexpected Cognito error — wrap generically.
    raise CognitoError(MSG_AUTH_SERVICE_ERROR.format(detail=message))


# ── Service ────────────────────────────────────────────────────────────────


class CognitoAuthService:
    """Async wrapper around all AWS Cognito User Pool operations."""

    def __init__(self) -> None:
        self._client_id = settings.AWS_COGNITO_CLIENT_ID

    # ── Registration ───────────────────────────────────────────────────────

    async def register(
        self,
        *,
        email: str,
        password: str,
        name: str,
    ) -> RegisterResponse:
        """Sign a new user up with the Cognito User Pool.

        Cognito sends a confirmation email automatically when the User Pool
        has auto-verified attributes for email enabled.
        """

        def _sign_up() -> dict[str, Any]:
            username = _derive_cognito_username(email)

            client = _cognito_client()
            params: dict[str, Any] = {
                "ClientId": self._client_id,
                "Username": username,
                "Password": password,
                "UserAttributes": [
                    {"Name": "email", "Value": email},
                    {"Name": "name", "Value": name},
                ],
            }
            # SecretHash must be computed with the Username sent to Cognito.
            _add_secret_hash(params, username)
            return client.sign_up(**params)

        try:
            response = await asyncio.to_thread(_sign_up)
        except ClientError as exc:
            _map_cognito_error(exc, "register")
        except BotoCoreError as exc:
            raise CognitoError(MSG_AUTH_AWS_UNAVAILABLE.format(detail=exc)) from exc

        sub: str = response["UserSub"]
        logger.info("New user registered, sub=%s, email=<redacted>", sub)
        return RegisterResponse(
            email=email,
            sub=sub,
            message=MSG_AUTH_REGISTRATION_SUCCESS,
        )

    # ── Confirm sign-up ────────────────────────────────────────────────────

    async def confirm_sign_up(
        self,
        *,
        email: str,
        code: str,
    ) -> ConfirmSignUpResponse:
        """Confirm a Cognito user's email address with the verification code.

        The username sent to Cognito must match the one used at registration
        (i.e. the email with '@' and '.' replaced by '-').
        """

        def _confirm() -> None:
            username = _derive_cognito_username(email)
            client = _cognito_client()
            params: dict[str, str] = {
                "ClientId": self._client_id,
                "Username": username,
                "ConfirmationCode": code,
            }
            _add_secret_hash(params, username)
            client.confirm_sign_up(**params)

        try:
            await asyncio.to_thread(_confirm)
        except ClientError as exc:
            _map_cognito_error(exc, "confirm_sign_up")
        except BotoCoreError as exc:
            raise CognitoError(MSG_AUTH_AWS_UNAVAILABLE.format(detail=exc)) from exc

        logger.info("User confirmed email, email=<redacted>")
        return ConfirmSignUpResponse(
            email=email,
            message=MSG_AUTH_CONFIRM_SUCCESS,
        )

    # ── Login ──────────────────────────────────────────────────────────────

    async def login(self, *, email: str, password: str) -> TokenData:
        """Authenticate via USER_PASSWORD_AUTH and return the Cognito token set.

        The Cognito App Client must have ``ALLOW_USER_PASSWORD_AUTH`` enabled
        in its 'Auth Flows' settings.
        """

        def _initiate_auth() -> dict[str, Any]:
            client = _cognito_client()
            auth_params: dict[str, str] = {
                "USERNAME": email,
                "PASSWORD": password,
            }
            _add_secret_hash(auth_params, email, key="SECRET_HASH")
            return client.initiate_auth(
                AuthFlow="USER_PASSWORD_AUTH",
                AuthParameters=auth_params,
                ClientId=self._client_id,
            )

        try:
            response = await asyncio.to_thread(_initiate_auth)
        except ClientError as exc:
            _map_cognito_error(exc, "login")
        except BotoCoreError as exc:
            raise CognitoError(MSG_AUTH_AWS_UNAVAILABLE.format(detail=exc)) from exc

        result = response["AuthenticationResult"]
        logger.info("User authenticated successfully, email=<redacted>")
        return TokenData(
            access_token=result["AccessToken"],
            id_token=result["IdToken"],
            refresh_token=result["RefreshToken"],
            expires_in=result["ExpiresIn"],
        )

    # ── Token refresh ──────────────────────────────────────────────────────

    async def refresh_tokens(
        self,
        *,
        refresh_token: str,
        username: str,
    ) -> TokenData:
        """Issue new access and ID tokens using a valid refresh token.

        ``username`` is the Cognito username (email when the User Pool uses
        email as the username attribute) — required to compute SECRET_HASH.

        Cognito does NOT rotate the refresh token on this flow; the original
        refresh token is returned unchanged in the result.
        """

        def _refresh() -> dict[str, Any]:
            client = _cognito_client()
            auth_params: dict[str, str] = {"REFRESH_TOKEN": refresh_token}
            _add_secret_hash(auth_params, username, key="SECRET_HASH")
            return client.initiate_auth(
                AuthFlow="REFRESH_TOKEN_AUTH",
                AuthParameters=auth_params,
                ClientId=self._client_id,
            )

        try:
            response = await asyncio.to_thread(_refresh)
        except ClientError as exc:
            _map_cognito_error(exc, "refresh_tokens")
        except BotoCoreError as exc:
            raise CognitoError(MSG_AUTH_AWS_UNAVAILABLE.format(detail=exc)) from exc

        result = response["AuthenticationResult"]
        logger.info("Tokens refreshed, username=<redacted>")
        return TokenData(
            access_token=result["AccessToken"],
            id_token=result["IdToken"],
            refresh_token=refresh_token,  # Cognito does not re-issue refresh tokens
            expires_in=result["ExpiresIn"],
        )

    # ── Logout ─────────────────────────────────────────────────────────────

    async def logout(self, *, access_token: str) -> None:
        """Globally sign the user out.

        Calls ``GlobalSignOut`` which invalidates ALL tokens (access, ID, and
        refresh) issued for the user, across all devices.
        """

        def _sign_out() -> None:
            client = _cognito_client()
            client.global_sign_out(AccessToken=access_token)

        try:
            await asyncio.to_thread(_sign_out)
        except ClientError as exc:
            _map_cognito_error(exc, "logout")
        except BotoCoreError as exc:
            raise CognitoError(MSG_AUTH_AWS_UNAVAILABLE.format(detail=exc)) from exc

        logger.info("User signed out globally (all tokens invalidated)")

    # ── Forgot / reset password ─────────────────────────────────────────────

    async def forgot_password(self, *, email: str) -> ForgotPasswordResponse:
        """Initiate the Cognito password-reset flow: email a verification code.

        SECURITY — anti user-enumeration: this method returns the SAME
        generic success response whether or not an account exists for
        *email*. Two Cognito errors are deliberately swallowed here instead
        of raised (unlike every other method in this class):

        - ``UserNotFoundException`` — no account exists for this email.
        - ``InvalidParameterException`` — Cognito's "no registered/verified
          email or phone number for this user" error, which would otherwise
          leak that the email belongs to a real (if unconfirmed) account.

        No Postgres lookup happens before the Cognito call either — checking
        our own DB first (e.g. ``is_active``/``deleted_at``) and branching
        the response on it would reintroduce the same timing/response oracle
        this is designed to avoid. A removed user (Cognito identity already
        deleted, see ``admin_delete_user``) naturally falls into the
        ``UserNotFoundException`` case above with no special-casing needed.
        """
        username = _derive_cognito_username(email)

        def _forgot_password() -> None:
            client = _cognito_client()
            params: dict[str, Any] = {
                "ClientId": self._client_id,
                "Username": username,
            }
            _add_secret_hash(params, username)
            client.forgot_password(**params)

        try:
            await asyncio.to_thread(_forgot_password)
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code in ("UserNotFoundException", "InvalidParameterException"):
                logger.info(
                    "forgot_password: Cognito reported '%s' for email=<redacted> — "
                    "returning generic success to avoid user enumeration",
                    code,
                )
            else:
                _map_cognito_error(exc, "forgot_password")
        except BotoCoreError as exc:
            raise CognitoError(MSG_AUTH_AWS_UNAVAILABLE.format(detail=exc)) from exc

        logger.info("Password reset requested, email=<redacted>")
        return ForgotPasswordResponse(email=email, message=MSG_AUTH_FORGOT_PASSWORD_SUCCESS)

    async def confirm_forgot_password(
        self, *, email: str, code: str, new_password: str
    ) -> ConfirmForgotPasswordResponse:
        """Complete the Cognito password-reset flow with the emailed code.

        Unlike :meth:`forgot_password`, errors here are NOT swallowed — by
        this point the caller must already have a valid code, so a specific
        error (wrong/expired code, weak password) leaks nothing beyond what
        ``confirm_sign_up`` already exposes for the equivalent code-based
        flow. A wrong code against a nonexistent/removed user still maps
        through the standard table to a generic ``UnauthorizedError``
        (``UserNotFoundException`` → ``MSG_AUTH_INVALID_CREDENTIALS``), so no
        special-casing is needed here.
        """
        username = _derive_cognito_username(email)

        def _confirm_forgot_password() -> None:
            client = _cognito_client()
            params: dict[str, Any] = {
                "ClientId": self._client_id,
                "Username": username,
                "ConfirmationCode": code,
                "Password": new_password,
            }
            _add_secret_hash(params, username)
            client.confirm_forgot_password(**params)

        try:
            await asyncio.to_thread(_confirm_forgot_password)
        except ClientError as exc:
            _map_cognito_error(exc, "confirm_forgot_password")
        except BotoCoreError as exc:
            raise CognitoError(MSG_AUTH_AWS_UNAVAILABLE.format(detail=exc)) from exc

        logger.info("Password reset completed, email=<redacted>")
        return ConfirmForgotPasswordResponse(email=email, message=MSG_AUTH_RESET_PASSWORD_SUCCESS)

    # ── Admin bootstrap (Super Admin seed only) ─────────────────────────────

    async def admin_get_user_sub(self, *, email: str) -> str | None:
        """Return the Cognito ``sub`` for *email* if the user already exists.

        Uses ``AdminGetUser`` (server-side, no user credentials required).
        Returns ``None`` on ``UserNotFoundException`` instead of raising —
        this is the one place a "not found" Cognito error is an expected,
        non-exceptional outcome (existence check for idempotent seeding),
        not a login attempt where hiding user-existence matters.
        """
        username = _derive_cognito_username(email)

        def _admin_get_user() -> dict[str, Any]:
            client = _cognito_client()
            return client.admin_get_user(
                UserPoolId=settings.AWS_COGNITO_USER_POOL_ID,
                Username=username,
            )

        try:
            response = await asyncio.to_thread(_admin_get_user)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "UserNotFoundException":
                return None
            _map_cognito_error(exc, "admin_get_user")
        except BotoCoreError as exc:
            raise CognitoError(MSG_AUTH_AWS_UNAVAILABLE.format(detail=exc)) from exc

        for attr in response.get("UserAttributes", []):
            if attr["Name"] == "sub":
                return attr["Value"]
        return None

    async def admin_create_user(self, *, email: str, password: str, name: str) -> str:
        """Create a pre-verified, pre-confirmed Cognito user and return its ``sub``.

        Used only for the seeded Super Admin bootstrap account (never for
        self-service registration, which goes through :meth:`register`).
        Follows AWS Cognito admin-provisioning best practice for
        service/bootstrap accounts:
          - ``AdminCreateUser`` with ``MessageAction=SUPPRESS`` (no invite
            email — the account is provisioned out-of-band via settings).
          - Email marked pre-verified since it is operator-supplied, not
            user-submitted.
          - ``AdminSetUserPassword`` with ``Permanent=True`` so the account
            is immediately usable without a forced first-login password
            reset, matching the "available automatically, no manual steps"
            requirement for this bootstrap-only account.
        """
        username = _derive_cognito_username(email)

        def _admin_create_user() -> dict[str, Any]:
            client = _cognito_client()
            return client.admin_create_user(
                UserPoolId=settings.AWS_COGNITO_USER_POOL_ID,
                Username=username,
                UserAttributes=[
                    {"Name": "email", "Value": email},
                    {"Name": "email_verified", "Value": "true"},
                    {"Name": "name", "Value": name},
                ],
                MessageAction="SUPPRESS",
            )

        def _admin_set_password() -> None:
            client = _cognito_client()
            client.admin_set_user_password(
                UserPoolId=settings.AWS_COGNITO_USER_POOL_ID,
                Username=username,
                Password=password,
                Permanent=True,
            )

        try:
            response = await asyncio.to_thread(_admin_create_user)
            await asyncio.to_thread(_admin_set_password)
        except ClientError as exc:
            _map_cognito_error(exc, "admin_create_user")
        except BotoCoreError as exc:
            raise CognitoError(MSG_AUTH_AWS_UNAVAILABLE.format(detail=exc)) from exc

        for attr in response["User"]["Attributes"]:
            if attr["Name"] == "sub":
                logger.info("Admin-created bootstrap user, sub=%s, email=<redacted>", attr["Value"])
                return attr["Value"]
        raise CognitoError(
            MSG_AUTH_SERVICE_ERROR.format(
                detail="sub attribute missing from AdminCreateUser response"
            )
        )

    # ── Admin invitation (Super Admin / Tenant Admin invite flows) ──────────

    async def admin_invite_user(self, *, email: str, name: str) -> str:
        """Create a pre-verified, pre-confirmed Cognito user with no password set.

        Used for admin-issued invitations (see ``InvitationService``). Unlike
        :meth:`admin_create_user`, no password is set here — the invitee
        chooses their own password later, applied via
        :meth:`admin_set_user_password` at invitation-accept time. This
        avoids ever needing to handle Cognito's ``NEW_PASSWORD_REQUIRED``
        challenge on the frontend: whatever temporary password Cognito
        auto-generates internally is irrelevant since it's overwritten with
        ``Permanent=True`` before the invitee ever authenticates.

        ``MessageAction="SUPPRESS"`` — the invite email is sent by the
        application (custom branded email), not by Cognito.
        """
        username = _derive_cognito_username(email)

        def _admin_create_user() -> dict[str, Any]:
            client = _cognito_client()
            return client.admin_create_user(
                UserPoolId=settings.AWS_COGNITO_USER_POOL_ID,
                Username=username,
                UserAttributes=[
                    {"Name": "email", "Value": email},
                    {"Name": "email_verified", "Value": "true"},
                    {"Name": "name", "Value": name},
                ],
                MessageAction="SUPPRESS",
            )

        try:
            response = await asyncio.to_thread(_admin_create_user)
        except ClientError as exc:
            _map_cognito_error(exc, "admin_invite_user")
        except BotoCoreError as exc:
            raise CognitoError(MSG_AUTH_AWS_UNAVAILABLE.format(detail=exc)) from exc

        for attr in response["User"]["Attributes"]:
            if attr["Name"] == "sub":
                logger.info("Admin-invited user created, sub=%s, email=<redacted>", attr["Value"])
                return attr["Value"]
        raise CognitoError(
            MSG_AUTH_SERVICE_ERROR.format(
                detail="sub attribute missing from AdminCreateUser response"
            )
        )

    async def admin_set_user_password(
        self, *, email: str, password: str, permanent: bool = True
    ) -> None:
        """Set a user's password via ``AdminSetUserPassword``.

        Used at invitation-accept time to apply the invitee's chosen
        password. ``Permanent=True`` (the default) makes the account
        immediately usable via normal login, regardless of whatever
        Cognito-internal state (e.g. ``FORCE_CHANGE_PASSWORD``) preceded it.
        """
        username = _derive_cognito_username(email)

        def _admin_set_password() -> None:
            client = _cognito_client()
            client.admin_set_user_password(
                UserPoolId=settings.AWS_COGNITO_USER_POOL_ID,
                Username=username,
                Password=password,
                Permanent=permanent,
            )

        try:
            await asyncio.to_thread(_admin_set_password)
        except ClientError as exc:
            _map_cognito_error(exc, "admin_set_user_password")
        except BotoCoreError as exc:
            raise CognitoError(MSG_AUTH_AWS_UNAVAILABLE.format(detail=exc)) from exc

    # ── Admin removal (Client Admin / Super Admin remove-user flow) ─────────

    async def admin_delete_user(self, *, email: str) -> None:
        """Permanently delete a Cognito user via ``AdminDeleteUser``.

        Used when an admin removes a user (see ``UserService.remove_user``)
        so the freed email/username can immediately be reused for a new
        invite or registration — Cognito enforces its own username/alias
        uniqueness independent of this application's Postgres row, so
        soft-deleting only the Postgres side is not enough to free the
        identity. Treats ``UserNotFoundException`` as a no-op — the desired
        end state ("no Cognito user for this email") already holds, so a
        retried removal (e.g. after a prior partial failure) does not error.
        """
        username = _derive_cognito_username(email)

        def _admin_delete_user() -> None:
            client = _cognito_client()
            client.admin_delete_user(
                UserPoolId=settings.AWS_COGNITO_USER_POOL_ID,
                Username=username,
            )

        try:
            await asyncio.to_thread(_admin_delete_user)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "UserNotFoundException":
                logger.info("Cognito user already absent for removal, email=<redacted>")
                return
            _map_cognito_error(exc, "admin_delete_user")
        except BotoCoreError as exc:
            raise CognitoError(MSG_AUTH_AWS_UNAVAILABLE.format(detail=exc)) from exc

        logger.info("Cognito user deleted, email=<redacted>")

        logger.info("Password set for admin-invited user, email=<redacted>")
