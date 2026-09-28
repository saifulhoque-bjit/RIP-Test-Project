"""Unit tests for CognitoAuthService.

Strategy:
- Patch ``app.services.auth_service.boto3.client`` so that asyncio.to_thread
  runs the inner sync function (in a real thread) against a MagicMock Cognito
  client.  This exercises the full service code path without hitting AWS.
- ClientError cases exercise ``_map_cognito_error`` mapping to application
  exceptions.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from botocore.exceptions import BotoCoreError, ClientError
import pytest

from app.core.exceptions import (
    CognitoError,
    ConflictError,
    UnauthorizedError,
    ValidationError as AppValidationError,
)
from app.core.messages import MSG_AUTH_FORGOT_PASSWORD_SUCCESS, MSG_AUTH_RESET_PASSWORD_SUCCESS
from app.services.auth_service import CognitoAuthService, _compute_secret_hash

# ── Helpers ────────────────────────────────────────────────────────────────


def _client_error(code: str, message: str = "Cognito test error") -> ClientError:
    """Build a ``botocore.exceptions.ClientError`` for *code*."""
    return ClientError(
        error_response={"Error": {"Code": code, "Message": message}},
        operation_name="MockOperation",
    )


def _mock_cognito(
    method_name: str, return_value: object = None, side_effect: object = None
) -> MagicMock:
    """Return a mock boto3 Cognito client with *method_name* configured."""
    client = MagicMock()
    method = getattr(client, method_name)
    if side_effect is not None:
        method.side_effect = side_effect
    else:
        method.return_value = return_value
    return client


# ── _compute_secret_hash ───────────────────────────────────────────────────


class TestComputeSecretHash:
    def test_returns_empty_string_when_no_secret(self) -> None:
        with patch("app.services.auth_service.settings") as mock_settings:
            mock_settings.AWS_COGNITO_CLIENT_SECRET = ""
            mock_settings.AWS_COGNITO_CLIENT_ID = "client123"
            result = _compute_secret_hash("user@example.com")
        assert result == ""

    def test_returns_base64_string_when_secret_configured(self) -> None:
        with patch("app.services.auth_service.settings") as mock_settings:
            mock_settings.AWS_COGNITO_CLIENT_SECRET = "super_secret"
            mock_settings.AWS_COGNITO_CLIENT_ID = "client123"
            result = _compute_secret_hash("user@example.com")
        # Must be non-empty base64
        assert isinstance(result, str)
        assert len(result) > 0
        import base64

        base64.b64decode(result)  # must not raise

    def test_deterministic_for_same_inputs(self) -> None:
        with patch("app.services.auth_service.settings") as mock_settings:
            mock_settings.AWS_COGNITO_CLIENT_SECRET = "s3cr3t"
            mock_settings.AWS_COGNITO_CLIENT_ID = "clientXYZ"
            h1 = _compute_secret_hash("alice@example.com")
            h2 = _compute_secret_hash("alice@example.com")
        assert h1 == h2

    def test_different_for_different_users(self) -> None:
        with patch("app.services.auth_service.settings") as mock_settings:
            mock_settings.AWS_COGNITO_CLIENT_SECRET = "s3cr3t"
            mock_settings.AWS_COGNITO_CLIENT_ID = "clientXYZ"
            h_alice = _compute_secret_hash("alice@example.com")
            h_bob = _compute_secret_hash("bob@example.com")
        assert h_alice != h_bob


# ── CognitoAuthService.register ───────────────────────────────────────────


class TestRegister:
    @pytest.fixture
    def service(self) -> CognitoAuthService:
        return CognitoAuthService()

    @pytest.mark.asyncio
    async def test_register_success(self, service: CognitoAuthService) -> None:
        cognito = _mock_cognito(
            "sign_up",
            return_value={"UserSub": "sub-abc-123", "UserConfirmed": False},
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            result = await service.register(
                email="new@example.com",
                password="Secure1!Pass",
                name="Alice Smith",
            )

        assert result.sub == "sub-abc-123"
        assert result.email == "new@example.com"
        assert "Registration successful" in result.message
        cognito.sign_up.assert_called_once()

    @pytest.mark.asyncio
    async def test_register_duplicate_email_raises_conflict(
        self, service: CognitoAuthService
    ) -> None:
        cognito = _mock_cognito(
            "sign_up",
            side_effect=_client_error("UsernameExistsException"),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(ConflictError):
                await service.register(
                    email="existing@example.com",
                    password="Secure1!Pass",
                    name="Bob Jones",
                )

    @pytest.mark.asyncio
    async def test_register_weak_password_raises_validation_error(
        self, service: CognitoAuthService
    ) -> None:
        cognito = _mock_cognito(
            "sign_up",
            side_effect=_client_error("InvalidPasswordException", "Password too weak"),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(AppValidationError, match="Password does not meet"):
                await service.register(
                    email="test@example.com",
                    password="Secure1!Pass",
                    name="Test User",
                )

    @pytest.mark.asyncio
    async def test_register_cognito_rate_limit_raises_cognito_error(
        self, service: CognitoAuthService
    ) -> None:
        cognito = _mock_cognito(
            "sign_up",
            side_effect=_client_error("TooManyRequestsException"),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(CognitoError, match="Too many requests"):
                await service.register(
                    email="test@example.com",
                    password="Secure1!Pass",
                    name="Test User",
                )

    @pytest.mark.asyncio
    async def test_register_boto_infrastructure_error_raises_cognito_error(
        self, service: CognitoAuthService
    ) -> None:
        """Non-ClientError botocore exceptions (e.g. endpoint unreachable) must
        surface as CognitoError, not as an unhandled INTERNAL_ERROR."""
        cognito = _mock_cognito(
            "sign_up",
            side_effect=BotoCoreError(),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(CognitoError, match="AWS service unavailable"):
                await service.register(
                    email="test@example.com",
                    password="Secure1!Pass",
                    name="Test User",
                )


# ── CognitoAuthService.login ───────────────────────────────────────────────


class TestLogin:
    @pytest.fixture
    def service(self) -> CognitoAuthService:
        return CognitoAuthService()

    @pytest.mark.asyncio
    async def test_login_success(self, service: CognitoAuthService) -> None:
        cognito = _mock_cognito(
            "initiate_auth",
            return_value={
                "AuthenticationResult": {
                    "AccessToken": "access-tok",
                    "IdToken": "id-tok",
                    "RefreshToken": "refresh-tok",
                    "ExpiresIn": 3600,
                }
            },
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            result = await service.login(email="user@example.com", password="Secure1!Pass")

        assert result.access_token == "access-tok"
        assert result.id_token == "id-tok"
        assert result.refresh_token == "refresh-tok"
        assert result.expires_in == 3600
        assert result.token_type == "Bearer"

    @pytest.mark.asyncio
    async def test_login_wrong_credentials_raises_unauthorized(
        self, service: CognitoAuthService
    ) -> None:
        cognito = _mock_cognito(
            "initiate_auth",
            side_effect=_client_error("NotAuthorizedException"),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(UnauthorizedError, match="Invalid credentials"):
                await service.login(email="user@example.com", password="WrongPass1!")

    @pytest.mark.asyncio
    async def test_login_unknown_user_raises_unauthorized(
        self, service: CognitoAuthService
    ) -> None:
        """UserNotFoundException must surface as UnauthorizedError (no enumeration)."""
        cognito = _mock_cognito(
            "initiate_auth",
            side_effect=_client_error("UserNotFoundException"),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(UnauthorizedError):
                await service.login(email="ghost@example.com", password="Secure1!Pass")

    @pytest.mark.asyncio
    async def test_login_unconfirmed_user_raises_unauthorized(
        self, service: CognitoAuthService
    ) -> None:
        cognito = _mock_cognito(
            "initiate_auth",
            side_effect=_client_error("UserNotConfirmedException"),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(UnauthorizedError):
                await service.login(email="unverified@example.com", password="Secure1!Pass")

    @pytest.mark.asyncio
    async def test_login_boto_infrastructure_error_raises_cognito_error(
        self, service: CognitoAuthService
    ) -> None:
        cognito = _mock_cognito(
            "initiate_auth",
            side_effect=BotoCoreError(),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(CognitoError, match="AWS service unavailable"):
                await service.login(email="test@example.com", password="Secure1!Pass")


# ── CognitoAuthService.refresh_tokens ─────────────────────────────────────


class TestRefreshTokens:
    @pytest.fixture
    def service(self) -> CognitoAuthService:
        return CognitoAuthService()

    @pytest.mark.asyncio
    async def test_refresh_success(self, service: CognitoAuthService) -> None:
        cognito = _mock_cognito(
            "initiate_auth",
            return_value={
                "AuthenticationResult": {
                    "AccessToken": "new-access-tok",
                    "IdToken": "new-id-tok",
                    "ExpiresIn": 3600,
                }
            },
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            result = await service.refresh_tokens(
                refresh_token="original-refresh-tok",
                username="user@example.com",
            )

        # original refresh token must be preserved
        assert result.refresh_token == "original-refresh-tok"
        assert result.access_token == "new-access-tok"
        assert result.id_token == "new-id-tok"

    @pytest.mark.asyncio
    async def test_refresh_expired_token_raises_unauthorized(
        self, service: CognitoAuthService
    ) -> None:
        cognito = _mock_cognito(
            "initiate_auth",
            side_effect=_client_error("NotAuthorizedException", "Refresh token expired"),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(UnauthorizedError):
                await service.refresh_tokens(
                    refresh_token="expired-tok",
                    username="user@example.com",
                )

    @pytest.mark.asyncio
    async def test_refresh_boto_infrastructure_error_raises_cognito_error(
        self, service: CognitoAuthService
    ) -> None:
        cognito = _mock_cognito(
            "initiate_auth",
            side_effect=BotoCoreError(),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(CognitoError, match="AWS service unavailable"):
                await service.refresh_tokens(
                    refresh_token="tok",
                    username="user@example.com",
                )


# ── CognitoAuthService.logout ──────────────────────────────────────────────


class TestLogout:
    @pytest.fixture
    def service(self) -> CognitoAuthService:
        return CognitoAuthService()

    @pytest.mark.asyncio
    async def test_logout_success(self, service: CognitoAuthService) -> None:
        cognito = _mock_cognito("global_sign_out", return_value={})
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            await service.logout(access_token="valid-access-token")
        cognito.global_sign_out.assert_called_once_with(AccessToken="valid-access-token")

    @pytest.mark.asyncio
    async def test_logout_invalid_token_raises_unauthorized(
        self, service: CognitoAuthService
    ) -> None:
        cognito = _mock_cognito(
            "global_sign_out",
            side_effect=_client_error("NotAuthorizedException"),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(UnauthorizedError):
                await service.logout(access_token="bad-token")

    @pytest.mark.asyncio
    async def test_logout_unknown_cognito_error_raises_cognito_error(
        self, service: CognitoAuthService
    ) -> None:
        cognito = _mock_cognito(
            "global_sign_out",
            side_effect=_client_error("InternalErrorException", "AWS error"),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(CognitoError):
                await service.logout(access_token="some-token")

    @pytest.mark.asyncio
    async def test_logout_boto_infrastructure_error_raises_cognito_error(
        self, service: CognitoAuthService
    ) -> None:
        cognito = _mock_cognito(
            "global_sign_out",
            side_effect=BotoCoreError(),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(CognitoError, match="AWS service unavailable"):
                await service.logout(access_token="tok")


# ── CognitoAuthService.confirm_sign_up ────────────────────────────────────


class TestConfirmSignUp:
    @pytest.fixture
    def service(self) -> CognitoAuthService:
        return CognitoAuthService()

    @pytest.mark.asyncio
    async def test_confirm_success_returns_response(self, service: CognitoAuthService) -> None:
        cognito = _mock_cognito("confirm_sign_up", return_value={})
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            result = await service.confirm_sign_up(email="alice@example.com", code="123456")
        assert result.email == "alice@example.com"
        assert "confirmed" in result.message.lower()
        cognito.confirm_sign_up.assert_called_once()

    @pytest.mark.asyncio
    async def test_confirm_wrong_code_raises_validation_error(
        self, service: CognitoAuthService
    ) -> None:
        cognito = _mock_cognito(
            "confirm_sign_up",
            side_effect=_client_error("CodeMismatchException", "Invalid code"),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(AppValidationError):
                await service.confirm_sign_up(email="alice@example.com", code="000000")

    @pytest.mark.asyncio
    async def test_confirm_expired_code_raises_validation_error(
        self, service: CognitoAuthService
    ) -> None:
        cognito = _mock_cognito(
            "confirm_sign_up",
            side_effect=_client_error("ExpiredCodeException", "Code expired"),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(AppValidationError):
                await service.confirm_sign_up(email="alice@example.com", code="987654")

    @pytest.mark.asyncio
    async def test_confirm_alias_exists_raises_conflict(self, service: CognitoAuthService) -> None:
        cognito = _mock_cognito(
            "confirm_sign_up",
            side_effect=_client_error("AliasExistsException"),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(ConflictError):
                await service.confirm_sign_up(email="alice@example.com", code="111111")

    @pytest.mark.asyncio
    async def test_confirm_email_normalised_to_username(self, service: CognitoAuthService) -> None:
        """Email is transformed to Cognito username (@ and . → -)."""
        cognito = _mock_cognito("confirm_sign_up", return_value={})
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            await service.confirm_sign_up(email="bob.jones@domain.com", code="555555")
        call_kwargs = cognito.confirm_sign_up.call_args.kwargs
        assert call_kwargs["Username"] == "bob-jones@domain-com".replace("@", "-")

    @pytest.mark.asyncio
    async def test_confirm_boto_infrastructure_error_raises_cognito_error(
        self, service: CognitoAuthService
    ) -> None:
        cognito = _mock_cognito(
            "confirm_sign_up",
            side_effect=BotoCoreError(),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(CognitoError, match="AWS service unavailable"):
                await service.confirm_sign_up(email="alice@example.com", code="123456")


# ── CognitoAuthService.admin_get_user_sub / admin_create_user ─────────────


class TestAdminGetUserSub:
    @pytest.fixture
    def service(self) -> CognitoAuthService:
        return CognitoAuthService()

    @pytest.mark.asyncio
    async def test_returns_sub_when_user_exists(self, service: CognitoAuthService) -> None:
        cognito = _mock_cognito(
            "admin_get_user",
            return_value={
                "UserAttributes": [
                    {"Name": "email", "Value": "admin@example.com"},
                    {"Name": "sub", "Value": "sub-existing-1"},
                ]
            },
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            result = await service.admin_get_user_sub(email="admin@example.com")

        assert result == "sub-existing-1"

    @pytest.mark.asyncio
    async def test_returns_none_when_user_not_found(self, service: CognitoAuthService) -> None:
        cognito = _mock_cognito(
            "admin_get_user",
            side_effect=_client_error("UserNotFoundException"),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            result = await service.admin_get_user_sub(email="missing@example.com")

        assert result is None

    @pytest.mark.asyncio
    async def test_unknown_cognito_error_raises_cognito_error(
        self, service: CognitoAuthService
    ) -> None:
        cognito = _mock_cognito(
            "admin_get_user",
            side_effect=_client_error("InternalErrorException", "AWS error"),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(CognitoError):
                await service.admin_get_user_sub(email="user@example.com")


class TestAdminCreateUser:
    @pytest.fixture
    def service(self) -> CognitoAuthService:
        return CognitoAuthService()

    @pytest.mark.asyncio
    async def test_creates_user_and_sets_permanent_password(
        self, service: CognitoAuthService
    ) -> None:
        cognito = _mock_cognito(
            "admin_create_user",
            return_value={
                "User": {
                    "Attributes": [
                        {"Name": "email", "Value": "boss@example.com"},
                        {"Name": "sub", "Value": "sub-new-1"},
                    ]
                }
            },
        )
        cognito.admin_set_user_password.return_value = {}
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            result = await service.admin_create_user(
                email="boss@example.com",
                password="Sup3rStrong!Pass",
                name="Boss Admin",
            )

        assert result == "sub-new-1"
        create_kwargs = cognito.admin_create_user.call_args.kwargs
        assert create_kwargs["MessageAction"] == "SUPPRESS"
        set_password_kwargs = cognito.admin_set_user_password.call_args.kwargs
        assert set_password_kwargs["Permanent"] is True

    @pytest.mark.asyncio
    async def test_cognito_error_is_mapped(self, service: CognitoAuthService) -> None:
        cognito = _mock_cognito(
            "admin_create_user",
            side_effect=_client_error("InternalErrorException", "AWS error"),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(CognitoError):
                await service.admin_create_user(
                    email="boss@example.com",
                    password="Sup3rStrong!Pass",
                    name="Boss Admin",
                )


# ── CognitoAuthService.admin_invite_user / admin_set_user_password ────────


class TestAdminInviteUser:
    @pytest.fixture
    def service(self) -> CognitoAuthService:
        return CognitoAuthService()

    @pytest.mark.asyncio
    async def test_creates_user_without_setting_password(self, service: CognitoAuthService) -> None:
        cognito = _mock_cognito(
            "admin_create_user",
            return_value={
                "User": {
                    "Attributes": [
                        {"Name": "email", "Value": "invitee@example.com"},
                        {"Name": "sub", "Value": "sub-invitee-1"},
                    ]
                }
            },
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            result = await service.admin_invite_user(
                email="invitee@example.com", name="Invitee Name"
            )

        assert result == "sub-invitee-1"
        create_kwargs = cognito.admin_create_user.call_args.kwargs
        assert create_kwargs["MessageAction"] == "SUPPRESS"
        # No password is set at invite time — only AdminCreateUser is called.
        cognito.admin_set_user_password.assert_not_called()

    @pytest.mark.asyncio
    async def test_username_exists_raises_conflict(self, service: CognitoAuthService) -> None:
        cognito = _mock_cognito(
            "admin_create_user",
            side_effect=_client_error("UsernameExistsException"),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(ConflictError):
                await service.admin_invite_user(email="dup@example.com", name="Dup")

    @pytest.mark.asyncio
    async def test_boto_infrastructure_error_raises_cognito_error(
        self, service: CognitoAuthService
    ) -> None:
        cognito = _mock_cognito(
            "admin_create_user",
            side_effect=BotoCoreError(),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(CognitoError, match="AWS service unavailable"):
                await service.admin_invite_user(email="a@example.com", name="A")


class TestAdminSetUserPassword:
    @pytest.fixture
    def service(self) -> CognitoAuthService:
        return CognitoAuthService()

    @pytest.mark.asyncio
    async def test_sets_permanent_password_by_default(self, service: CognitoAuthService) -> None:
        cognito = _mock_cognito("admin_set_user_password", return_value={})
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            await service.admin_set_user_password(
                email="invitee@example.com", password="Chosen1!Pass"
            )

        kwargs = cognito.admin_set_user_password.call_args.kwargs
        assert kwargs["Permanent"] is True
        assert kwargs["Password"] == "Chosen1!Pass"

    @pytest.mark.asyncio
    async def test_weak_password_raises_validation_error(self, service: CognitoAuthService) -> None:
        cognito = _mock_cognito(
            "admin_set_user_password",
            side_effect=_client_error("InvalidPasswordException", "Too weak"),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(AppValidationError):
                await service.admin_set_user_password(email="invitee@example.com", password="weak")

    @pytest.mark.asyncio
    async def test_boto_infrastructure_error_raises_cognito_error(
        self, service: CognitoAuthService
    ) -> None:
        cognito = _mock_cognito(
            "admin_set_user_password",
            side_effect=BotoCoreError(),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(CognitoError, match="AWS service unavailable"):
                await service.admin_set_user_password(
                    email="invitee@example.com", password="Chosen1!Pass"
                )


class TestAdminDeleteUser:
    @pytest.fixture
    def service(self) -> CognitoAuthService:
        return CognitoAuthService()

    @pytest.mark.asyncio
    async def test_deletes_user(self, service: CognitoAuthService) -> None:
        cognito = _mock_cognito("admin_delete_user", return_value={})
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            await service.admin_delete_user(email="removed@example.com")

        kwargs = cognito.admin_delete_user.call_args.kwargs
        assert kwargs["Username"] == "removed-example-com"

    @pytest.mark.asyncio
    async def test_user_not_found_is_a_no_op(self, service: CognitoAuthService) -> None:
        cognito = _mock_cognito(
            "admin_delete_user",
            side_effect=_client_error("UserNotFoundException"),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            await service.admin_delete_user(email="already-gone@example.com")

    @pytest.mark.asyncio
    async def test_unknown_cognito_error_raises_cognito_error(
        self, service: CognitoAuthService
    ) -> None:
        cognito = _mock_cognito(
            "admin_delete_user",
            side_effect=_client_error("InternalErrorException", "AWS error"),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(CognitoError):
                await service.admin_delete_user(email="user@example.com")

    @pytest.mark.asyncio
    async def test_boto_infrastructure_error_raises_cognito_error(
        self, service: CognitoAuthService
    ) -> None:
        cognito = _mock_cognito("admin_delete_user", side_effect=BotoCoreError())
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(CognitoError, match="AWS service unavailable"):
                await service.admin_delete_user(email="user@example.com")


# ── CognitoAuthService.forgot_password / confirm_forgot_password ──────────


class TestForgotPassword:
    @pytest.fixture
    def service(self) -> CognitoAuthService:
        return CognitoAuthService()

    @pytest.mark.asyncio
    async def test_success_returns_generic_response(self, service: CognitoAuthService) -> None:
        cognito = _mock_cognito("forgot_password", return_value={})
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            result = await service.forgot_password(email="alice@example.com")

        assert result.email == "alice@example.com"
        assert result.message == MSG_AUTH_FORGOT_PASSWORD_SUCCESS
        cognito.forgot_password.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_not_found_returns_same_generic_response(
        self, service: CognitoAuthService
    ) -> None:
        """The whole point: a nonexistent account must not raise or differ
        from the success response — otherwise this endpoint becomes a
        user-enumeration oracle."""
        cognito = _mock_cognito(
            "forgot_password",
            side_effect=_client_error("UserNotFoundException"),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            result = await service.forgot_password(email="nobody@example.com")

        assert result.email == "nobody@example.com"
        assert result.message == MSG_AUTH_FORGOT_PASSWORD_SUCCESS

    @pytest.mark.asyncio
    async def test_invalid_parameter_returns_same_generic_response(
        self, service: CognitoAuthService
    ) -> None:
        """Cognito's 'no verified contact method' error must also be
        swallowed — it would otherwise leak that the email belongs to a
        real, unconfirmed account."""
        cognito = _mock_cognito(
            "forgot_password",
            side_effect=_client_error(
                "InvalidParameterException",
                "Cannot reset password for the user as there is no registered/verified email or phone_number",
            ),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            result = await service.forgot_password(email="unconfirmed@example.com")

        assert result.message == MSG_AUTH_FORGOT_PASSWORD_SUCCESS

    @pytest.mark.asyncio
    async def test_too_many_requests_still_raises(self, service: CognitoAuthService) -> None:
        """Rate-limit signals from Cognito itself are not existence-related
        and should still surface normally."""
        cognito = _mock_cognito(
            "forgot_password",
            side_effect=_client_error("LimitExceededException"),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(CognitoError):
                await service.forgot_password(email="alice@example.com")

    @pytest.mark.asyncio
    async def test_email_normalised_to_username(self, service: CognitoAuthService) -> None:
        cognito = _mock_cognito("forgot_password", return_value={})
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            await service.forgot_password(email="bob.jones@domain.com")
        call_kwargs = cognito.forgot_password.call_args.kwargs
        assert call_kwargs["Username"] == "bob-jones-domain-com"

    @pytest.mark.asyncio
    async def test_boto_infrastructure_error_raises_cognito_error(
        self, service: CognitoAuthService
    ) -> None:
        cognito = _mock_cognito("forgot_password", side_effect=BotoCoreError())
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(CognitoError, match="AWS service unavailable"):
                await service.forgot_password(email="alice@example.com")


class TestConfirmForgotPassword:
    @pytest.fixture
    def service(self) -> CognitoAuthService:
        return CognitoAuthService()

    @pytest.mark.asyncio
    async def test_success_returns_response(self, service: CognitoAuthService) -> None:
        cognito = _mock_cognito("confirm_forgot_password", return_value={})
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            result = await service.confirm_forgot_password(
                email="alice@example.com", code="123456", new_password="Sup3rStrong!Pass"
            )

        assert result.email == "alice@example.com"
        assert result.message == MSG_AUTH_RESET_PASSWORD_SUCCESS
        call_kwargs = cognito.confirm_forgot_password.call_args.kwargs
        assert call_kwargs["ConfirmationCode"] == "123456"
        assert call_kwargs["Password"] == "Sup3rStrong!Pass"

    @pytest.mark.asyncio
    async def test_wrong_code_raises_validation_error(self, service: CognitoAuthService) -> None:
        cognito = _mock_cognito(
            "confirm_forgot_password",
            side_effect=_client_error("CodeMismatchException", "Invalid code"),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(AppValidationError):
                await service.confirm_forgot_password(
                    email="alice@example.com", code="000000", new_password="Sup3rStrong!Pass"
                )

    @pytest.mark.asyncio
    async def test_expired_code_raises_validation_error(self, service: CognitoAuthService) -> None:
        cognito = _mock_cognito(
            "confirm_forgot_password",
            side_effect=_client_error("ExpiredCodeException", "Code expired"),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(AppValidationError):
                await service.confirm_forgot_password(
                    email="alice@example.com", code="987654", new_password="Sup3rStrong!Pass"
                )

    @pytest.mark.asyncio
    async def test_weak_password_raises_validation_error(self, service: CognitoAuthService) -> None:
        cognito = _mock_cognito(
            "confirm_forgot_password",
            side_effect=_client_error("InvalidPasswordException", "Too weak"),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(AppValidationError):
                await service.confirm_forgot_password(
                    email="alice@example.com", code="123456", new_password="weak"
                )

    @pytest.mark.asyncio
    async def test_user_not_found_raises_unauthorized(self, service: CognitoAuthService) -> None:
        """Unlike forgot_password, this method does NOT swallow
        UserNotFoundException — by this point the caller already needs a
        valid code, so the generic UnauthorizedError leaks nothing new."""
        cognito = _mock_cognito(
            "confirm_forgot_password",
            side_effect=_client_error("UserNotFoundException"),
        )
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(UnauthorizedError):
                await service.confirm_forgot_password(
                    email="nobody@example.com", code="123456", new_password="Sup3rStrong!Pass"
                )

    @pytest.mark.asyncio
    async def test_email_normalised_to_username(self, service: CognitoAuthService) -> None:
        cognito = _mock_cognito("confirm_forgot_password", return_value={})
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            await service.confirm_forgot_password(
                email="bob.jones@domain.com", code="123456", new_password="Sup3rStrong!Pass"
            )
        call_kwargs = cognito.confirm_forgot_password.call_args.kwargs
        assert call_kwargs["Username"] == "bob-jones-domain-com"

    @pytest.mark.asyncio
    async def test_boto_infrastructure_error_raises_cognito_error(
        self, service: CognitoAuthService
    ) -> None:
        cognito = _mock_cognito("confirm_forgot_password", side_effect=BotoCoreError())
        with patch("app.services.auth_service.boto3.client", return_value=cognito):
            with pytest.raises(CognitoError, match="AWS service unavailable"):
                await service.confirm_forgot_password(
                    email="alice@example.com", code="123456", new_password="Sup3rStrong!Pass"
                )
