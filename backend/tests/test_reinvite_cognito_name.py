"""Reproduction for incident 32: a re-invited email shows the previous
invitee's name in the profile.

The profile name comes from the Cognito ID token's ``name`` claim. Only the
boto3 Cognito client is stubbed (an in-memory user pool); the route and
CognitoAuthService code run for real.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch
import uuid

from botocore.exceptions import ClientError
import pytest

from app.core.constants import ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.models.postgres.role_model import Role
from app.models.postgres.tenant_model import Tenant
from app.routes.v1.invitations import accept_invitation
from app.routes.v1.tenant_invitations import invite_user
from app.routes.v1.users import remove_user
from app.schemas.invitation_schema import InvitationAcceptRequest, InvitationCreateRequest
from tests.conftest import make_user

EMAIL = "client.admin@example.com"


class FakeCognitoPool:
    """In-memory stand-in for the boto3 cognito-idp client."""

    def __init__(self) -> None:
        self.users: dict[str, dict[str, str]] = {}
        self._n = 0

    @staticmethod
    def _not_found(op: str) -> ClientError:
        return ClientError({"Error": {"Code": "UserNotFoundException", "Message": "x"}}, op)

    def admin_get_user(self, *, UserPoolId: str, Username: str) -> dict[str, Any]:
        if Username not in self.users:
            raise self._not_found("AdminGetUser")
        return {"UserAttributes": [{"Name": k, "Value": v} for k, v in self.users[Username].items()]}

    def admin_create_user(self, *, UserPoolId: str, Username: str, UserAttributes, MessageAction):
        if Username in self.users:
            raise ClientError(
                {"Error": {"Code": "UsernameExistsException", "Message": "x"}}, "AdminCreateUser"
            )
        self._n += 1
        attrs = {a["Name"]: a["Value"] for a in UserAttributes}
        attrs["sub"] = f"sub-{self._n}"
        self.users[Username] = attrs
        return {"User": {"Attributes": [{"Name": k, "Value": v} for k, v in attrs.items()]}}

    def admin_update_user_attributes(self, *, UserPoolId: str, Username: str, UserAttributes):
        if Username not in self.users:
            raise self._not_found("AdminUpdateUserAttributes")
        for a in UserAttributes:
            self.users[Username][a["Name"]] = a["Value"]

    def admin_set_user_password(self, *, UserPoolId, Username, Password, Permanent) -> None:
        if Username not in self.users:
            raise self._not_found("AdminSetUserPassword")

    def admin_delete_user(self, *, UserPoolId: str, Username: str) -> None:
        if Username not in self.users:
            raise self._not_found("AdminDeleteUser")
        del self.users[Username]

    def name_for(self, email: str) -> str | None:
        for attrs in self.users.values():
            if attrs.get("email") == email:
                return attrs.get("name")
        return None


def _tenant() -> Tenant:
    t = Tenant()
    t.id = uuid.uuid4()
    t.name = "Acme"
    t.status = "active"
    return t


async def _invite(tenant: Tenant, name: str) -> Any:
    """Run the real invite route; capture the invitation it would persist."""
    captured: dict[str, Any] = {}

    def _create(self, **kwargs):  # noqa: ANN001
        inv = SimpleNamespace(id=uuid.uuid4(), status="pending", **kwargs)
        captured["inv"] = inv
        return inv, "raw-token"

    with (
        patch(
            "app.routes.v1.tenant_invitations.InvitationService.assert_role_exists",
            return_value=None,
        ),
        patch(
            "app.routes.v1.tenant_invitations.InvitationService.validate_invite_target",
            return_value=tenant,
        ),
        patch("app.routes.v1.tenant_invitations.InvitationService.create", _create),
        patch("app.routes.v1.tenant_invitations.EmailService.send_invitation"),
        patch("app.routes.v1.tenant_invitations.InvitationOut.model_validate"),
    ):
        await invite_user(
            tenant_id=tenant.id,
            body=InvitationCreateRequest(email=EMAIL, name=name, role=ROLE_ADMIN),
            uow=MagicMock(),
            current_user=make_user(roles=[Role(name=ROLE_SUPER_ADMIN)]),
        )
    return captured["inv"]


async def _accept(invitation: Any) -> None:
    """Run the real accept route for *invitation*."""
    with (
        patch(
            "app.routes.v1.invitations.InvitationService.get_valid_by_token",
            return_value=invitation,
        ),
        patch("app.routes.v1.invitations.InvitationService.accept"),
    ):
        await accept_invitation(
            token="raw-token",
            body=InvitationAcceptRequest(password="Str0ng!Passw0rd", confirm_password="Str0ng!Passw0rd"),
            uow=MagicMock(),
        )


@pytest.fixture
def pool():
    fake = FakeCognitoPool()
    with patch("app.services.auth_service._cognito_client", return_value=fake):
        yield fake


@pytest.mark.asyncio
async def test_reinvite_after_revoke_uses_new_name(pool: FakeCognitoPool) -> None:
    """Invite "Old Name", revoke (no Cognito call), re-invite "New Name",
    accept: the Cognito name — which the profile shows — must be the new one."""
    tenant = _tenant()
    await _invite(tenant, "Old Name")
    # Revoking only flips the invitation's status; the Cognito identity stays.
    second = await _invite(tenant, "New Name")
    await _accept(second)

    assert pool.name_for(EMAIL) == "New Name"


@pytest.mark.asyncio
async def test_reinvite_when_identity_survived_removal_uses_new_name(
    pool: FakeCognitoPool,
) -> None:
    """A Cognito identity that outlived its RIP user (e.g. created before the
    Cognito delete was added to removal) is reused on re-invite; acceptance
    must still leave the new invitee's name on it."""
    tenant = _tenant()
    first = await _invite(tenant, "Old Name")
    await _accept(first)
    # The RIP row is gone but the Cognito identity is not.
    second = await _invite(tenant, "New Name")
    await _accept(second)

    assert pool.name_for(EMAIL) == "New Name"


@pytest.mark.asyncio
async def test_reinvite_after_remove_user_uses_new_name(pool: FakeCognitoPool) -> None:
    """Differential: the normal Delete path removes the Cognito identity, so
    the re-invite creates a fresh one with the new name."""
    tenant = _tenant()
    first = await _invite(tenant, "Old Name")
    await _accept(first)

    target = make_user(roles=[Role(name=ROLE_ADMIN)])
    target.email = EMAIL
    service = MagicMock(unsafe=True)
    service.assert_can_remove.return_value = target
    with patch("app.routes.v1.users.UserService", return_value=service):
        await remove_user(
            user_id=target.id, current_admin=make_user(roles=[Role(name=ROLE_SUPER_ADMIN)]), uow=MagicMock()
        )

    second = await _invite(tenant, "New Name")
    await _accept(second)

    assert pool.name_for(EMAIL) == "New Name"
