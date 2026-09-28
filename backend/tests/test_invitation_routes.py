"""Unit tests for invitation routes.

Covers ``POST /tenants/{tenant_id}/invitations`` (app/routes/v1/tenant_invitations.py)
and ``GET /invitations/{token}`` + ``POST /invitations/{token}/accept``
(app/routes/v1/invitations.py).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest
from starlette.requests import Request

from app.core.constants import ROLE_ADMIN, ROLE_MEMBER, ROLE_SUPER_ADMIN
from app.core.enums.invitation_status import InvitationStatus
from app.core.enums.project_member_role import ProjectMemberRole
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.models.postgres.invitation_model import Invitation
from app.models.postgres.role_model import Role
from app.models.postgres.tenant_model import Tenant
from app.routes.v1.invitations import accept_invitation, validate_invitation
from app.routes.v1.tenant_invitations import (
    invite_user,
    list_invitations,
    resend_invitation,
    revoke_invitation,
)
from app.schemas.invitation_schema import InvitationAcceptRequest, InvitationCreateRequest
from tests.conftest import make_user


def _make_request() -> Request:
    return Request(
        scope={
            "type": "http",
            "method": "POST",
            "path": "/",
            "query_string": b"",
            "headers": [],
        }
    )


def _make_tenant() -> Tenant:
    t = Tenant()
    t.id = uuid.uuid4()
    t.name = "Acme"
    t.status = "active"
    return t


def _make_invitation(
    *,
    tenant: Tenant | None = None,
    role: Role | None = None,
    status: str = InvitationStatus.PENDING.value,
) -> Invitation:
    role = role or Role(name=ROLE_MEMBER)
    inv = Invitation()
    inv.id = uuid.uuid4()
    inv.tenant_id = (tenant or _make_tenant()).id
    inv.tenant = tenant or _make_tenant()
    inv.email = "invitee@example.com"
    inv.name = "Invitee Name"
    inv.role_id = uuid.uuid4()
    inv.role = role
    inv.cognito_sub = "sub-invitee-1"
    inv.status = status
    inv.expires_at = datetime.now(UTC) + timedelta(days=7)
    inv.created_at = datetime.now(UTC)
    return inv


def _make_super_admin() -> object:
    return make_user(roles=[Role(name=ROLE_SUPER_ADMIN)])


def _make_client_admin(*, tenant_id: uuid.UUID) -> object:
    return make_user(roles=[Role(name=ROLE_ADMIN)], tenant_id=tenant_id)


class TestInviteBySuperAdmin:
    """A super_admin invites into any tenant — invitee always becomes admin."""

    @pytest.mark.asyncio
    async def test_creates_new_cognito_user_when_none_exists(self) -> None:
        tenant = _make_tenant()
        invitation = _make_invitation(tenant=tenant)
        admin_user = _make_super_admin()

        mock_cognito = MagicMock()
        mock_cognito.admin_get_user_sub = AsyncMock(return_value=None)
        mock_cognito.admin_invite_user = AsyncMock(return_value="sub-new-1")

        with (
            patch(
                "app.routes.v1.tenant_invitations.InvitationService.validate_invite_target",
                return_value=tenant,
            ),
            patch(
                "app.routes.v1.tenant_invitations.InvitationService.create",
                return_value=(invitation, "raw-token-123"),
            ) as mock_create,
            patch("app.routes.v1.tenant_invitations.CognitoAuthService", return_value=mock_cognito),
            patch("app.routes.v1.tenant_invitations.EmailService.send_invitation") as mock_delay,
        ):
            result = await invite_user(
                tenant_id=tenant.id,
                body=InvitationCreateRequest(
                    email="invitee@example.com", name="Invitee Name", role=ROLE_ADMIN
                ),
                uow=MagicMock(),
                current_user=admin_user,
            )

        assert result.success is True
        assert result.data.email == "invitee@example.com"
        mock_cognito.admin_invite_user.assert_called_once_with(
            email="invitee@example.com", name="Invitee Name"
        )
        mock_create.assert_called_once_with(
            tenant_id=tenant.id,
            email="invitee@example.com",
            name="Invitee Name",
            cognito_sub="sub-new-1",
            invited_by_id=admin_user.id,
            role_name=ROLE_ADMIN,
        )
        mock_delay.assert_called_once()
        call_kwargs = mock_delay.call_args.kwargs
        assert "raw-token-123" in call_kwargs["invite_link"]

    @pytest.mark.asyncio
    async def test_reuses_existing_cognito_identity(self) -> None:
        tenant = _make_tenant()
        invitation = _make_invitation(tenant=tenant)
        admin_user = _make_super_admin()

        mock_cognito = MagicMock()
        mock_cognito.admin_get_user_sub = AsyncMock(return_value="sub-existing")
        mock_cognito.admin_invite_user = AsyncMock()

        with (
            patch(
                "app.routes.v1.tenant_invitations.InvitationService.validate_invite_target",
                return_value=tenant,
            ),
            patch(
                "app.routes.v1.tenant_invitations.InvitationService.create",
                return_value=(invitation, "raw-token-123"),
            ),
            patch("app.routes.v1.tenant_invitations.CognitoAuthService", return_value=mock_cognito),
            patch("app.routes.v1.tenant_invitations.EmailService.send_invitation"),
        ):
            await invite_user(
                tenant_id=tenant.id,
                body=InvitationCreateRequest(
                    email="invitee@example.com", name="Invitee Name", role=ROLE_ADMIN
                ),
                uow=MagicMock(),
                current_user=admin_user,
            )

        mock_cognito.admin_invite_user.assert_not_called()

    @pytest.mark.asyncio
    async def test_forbidden_to_invite_as_member(self) -> None:
        """A super_admin may never invite as 'member' — that's a Client
        Admin's responsibility within their own tenant."""
        tenant = _make_tenant()
        admin_user = _make_super_admin()

        mock_cognito = MagicMock()
        mock_cognito.admin_get_user_sub = AsyncMock(return_value="sub-existing")

        with patch(
            "app.routes.v1.tenant_invitations.CognitoAuthService", return_value=mock_cognito
        ):
            with pytest.raises(ForbiddenError):
                await invite_user(
                    tenant_id=tenant.id,
                    body=InvitationCreateRequest(
                        email="invitee@example.com",
                        name="Invitee Name",
                        role=ProjectMemberRole.MEMBER,
                    ),
                    uow=MagicMock(),
                    current_user=admin_user,
                )

        mock_cognito.admin_get_user_sub.assert_not_called()

    @pytest.mark.asyncio
    async def test_forbidden_to_invite_as_dynamic_role(self) -> None:
        """A super_admin is restricted to 'admin' — not even a custom dynamic role."""
        tenant = _make_tenant()
        admin_user = _make_super_admin()

        mock_cognito = MagicMock()
        mock_cognito.admin_get_user_sub = AsyncMock(return_value="sub-existing")

        with patch(
            "app.routes.v1.tenant_invitations.CognitoAuthService", return_value=mock_cognito
        ):
            with pytest.raises(ForbiddenError):
                await invite_user(
                    tenant_id=tenant.id,
                    body=InvitationCreateRequest(
                        email="invitee@example.com", name="Invitee Name", role="qa_lead"
                    ),
                    uow=MagicMock(),
                    current_user=admin_user,
                )

        mock_cognito.admin_get_user_sub.assert_not_called()

    @pytest.mark.asyncio
    async def test_defaults_to_admin_when_role_omitted(self) -> None:
        """'admin' is the only role a super_admin may ever invite as, so an
        omitted role defaults to it instead of requiring the caller to spell
        out the one value they're allowed to pass."""
        tenant = _make_tenant()
        invitation = _make_invitation(tenant=tenant)
        admin_user = _make_super_admin()

        mock_cognito = MagicMock()
        mock_cognito.admin_get_user_sub = AsyncMock(return_value="sub-existing")
        mock_cognito.admin_invite_user = AsyncMock()

        with (
            patch(
                "app.routes.v1.tenant_invitations.InvitationService.validate_invite_target",
                return_value=tenant,
            ),
            patch(
                "app.routes.v1.tenant_invitations.InvitationService.create",
                return_value=(invitation, "raw-token-123"),
            ) as mock_create,
            patch("app.routes.v1.tenant_invitations.CognitoAuthService", return_value=mock_cognito),
            patch("app.routes.v1.tenant_invitations.EmailService.send_invitation"),
        ):
            result = await invite_user(
                tenant_id=tenant.id,
                body=InvitationCreateRequest(email="invitee@example.com", name="Invitee Name"),
                uow=MagicMock(),
                current_user=admin_user,
            )

        assert result.success is True
        mock_create.assert_called_once_with(
            tenant_id=tenant.id,
            email="invitee@example.com",
            name="Invitee Name",
            cognito_sub="sub-existing",
            invited_by_id=admin_user.id,
            role_name=ROLE_ADMIN,
        )

    @pytest.mark.asyncio
    async def test_missing_role_row_rejected_before_any_cognito_call(self) -> None:
        """Even for an allow-listed role name ('admin'), a missing roles-table
        row must fail fast — before Cognito is ever touched — not deep
        inside InvitationService.create. (Defense in depth: 'admin' is
        always seeded in practice, but this guards a broken/partial deploy.)
        """
        tenant = _make_tenant()
        admin_user = _make_super_admin()

        mock_cognito = MagicMock()
        mock_cognito.admin_get_user_sub = AsyncMock(return_value=None)
        mock_cognito.admin_invite_user = AsyncMock(return_value="sub-new-1")

        with (
            patch(
                "app.routes.v1.tenant_invitations.InvitationService.assert_role_exists",
                side_effect=NotFoundError("Role 'admin' does not exist."),
            ),
            patch("app.routes.v1.tenant_invitations.CognitoAuthService", return_value=mock_cognito),
        ):
            with pytest.raises(NotFoundError):
                await invite_user(
                    tenant_id=tenant.id,
                    body=InvitationCreateRequest(
                        email="invitee@example.com", name="Invitee Name", role=ROLE_ADMIN
                    ),
                    uow=MagicMock(),
                    current_user=admin_user,
                )

        mock_cognito.admin_get_user_sub.assert_not_called()
        mock_cognito.admin_invite_user.assert_not_called()


class TestInviteByClientAdmin:
    """A Client Admin invites into their own tenant only, choosing member explicitly."""

    @pytest.mark.asyncio
    async def test_requires_explicit_role(self) -> None:
        tenant = _make_tenant()
        admin_user = _make_client_admin(tenant_id=tenant.id)
        body = InvitationCreateRequest(email="invitee@example.com", name="Invitee Name")

        with pytest.raises(ValidationError):
            await invite_user(
                tenant_id=tenant.id,
                body=body,
                uow=MagicMock(),
                current_user=admin_user,
            )

    @pytest.mark.asyncio
    async def test_grants_chosen_role(self) -> None:
        tenant = _make_tenant()
        invitation = _make_invitation(tenant=tenant)
        admin_user = _make_client_admin(tenant_id=tenant.id)

        mock_cognito = MagicMock()
        mock_cognito.admin_get_user_sub = AsyncMock(return_value="sub-existing")

        with (
            patch(
                "app.routes.v1.tenant_invitations.InvitationService.validate_invite_target",
                return_value=tenant,
            ),
            patch(
                "app.routes.v1.tenant_invitations.InvitationService.create",
                return_value=(invitation, "raw-token-123"),
            ) as mock_create,
            patch("app.routes.v1.tenant_invitations.CognitoAuthService", return_value=mock_cognito),
            patch("app.routes.v1.tenant_invitations.EmailService.send_invitation"),
        ):
            await invite_user(
                tenant_id=tenant.id,
                body=InvitationCreateRequest(
                    email="invitee@example.com", name="Invitee Name", role=ProjectMemberRole.MEMBER
                ),
                uow=MagicMock(),
                current_user=admin_user,
            )

        assert mock_create.call_args.kwargs["role_name"] == ProjectMemberRole.MEMBER.value

    @pytest.mark.asyncio
    async def test_forbidden_for_other_tenant(self) -> None:
        tenant = _make_tenant()
        other_tenant_id = uuid.uuid4()
        admin_user = _make_client_admin(tenant_id=other_tenant_id)
        body = InvitationCreateRequest(
            email="invitee@example.com", name="Invitee Name", role=ProjectMemberRole.MEMBER
        )

        with pytest.raises(ForbiddenError):
            await invite_user(
                tenant_id=tenant.id,
                body=body,
                uow=MagicMock(),
                current_user=admin_user,
            )

    @pytest.mark.asyncio
    async def test_forbidden_to_invite_as_admin(self) -> None:
        """A Client Admin may never invite someone in as another Client Admin."""
        tenant = _make_tenant()
        admin_user = _make_client_admin(tenant_id=tenant.id)
        body = InvitationCreateRequest(
            email="invitee@example.com", name="Invitee Name", role=ROLE_ADMIN
        )

        with pytest.raises(ForbiddenError):
            await invite_user(
                tenant_id=tenant.id,
                body=body,
                uow=MagicMock(),
                current_user=admin_user,
            )

    @pytest.mark.asyncio
    async def test_forbidden_to_invite_as_dynamic_role(self) -> None:
        """A Client Admin is restricted to member — not even a custom dynamic role."""
        tenant = _make_tenant()
        admin_user = _make_client_admin(tenant_id=tenant.id)
        body = InvitationCreateRequest(
            email="invitee@example.com", name="Invitee Name", role="qa_lead"
        )

        with pytest.raises(ForbiddenError):
            await invite_user(
                tenant_id=tenant.id,
                body=body,
                uow=MagicMock(),
                current_user=admin_user,
            )


class TestListInvitations:
    def test_lists_own_tenant_for_client_admin(self) -> None:
        tenant = _make_tenant()
        admin_user = _make_client_admin(tenant_id=tenant.id)
        invitations = [_make_invitation(tenant=tenant)]

        with patch(
            "app.routes.v1.tenant_invitations.InvitationService.list_for_tenant",
            return_value=(invitations, 1),
        ) as mock_list:
            result = list_invitations(tenant_id=tenant.id, uow=MagicMock(), current_user=admin_user)

        assert result.success is True
        assert result.data.total == 1
        mock_list.assert_called_once_with(tenant.id, status=None, skip=0, limit=20)

    def test_forbidden_for_other_tenant(self) -> None:
        tenant = _make_tenant()
        admin_user = _make_client_admin(tenant_id=uuid.uuid4())

        with pytest.raises(ForbiddenError):
            list_invitations(tenant_id=tenant.id, uow=MagicMock(), current_user=admin_user)

    def test_passes_status_filter(self) -> None:
        tenant = _make_tenant()
        admin_user = _make_super_admin()

        with patch(
            "app.routes.v1.tenant_invitations.InvitationService.list_for_tenant",
            return_value=([], 0),
        ) as mock_list:
            list_invitations(
                tenant_id=tenant.id,
                uow=MagicMock(),
                current_user=admin_user,
                status_filter=InvitationStatus.PENDING,
            )

        mock_list.assert_called_once_with(tenant.id, status="pending", skip=0, limit=20)


class TestResendInvitation:
    @pytest.mark.asyncio
    async def test_forbidden_for_other_tenant(self) -> None:
        tenant = _make_tenant()
        admin_user = _make_client_admin(tenant_id=uuid.uuid4())

        with pytest.raises(ForbiddenError):
            await resend_invitation(
                request=_make_request(),
                tenant_id=tenant.id,
                invitation_id=uuid.uuid4(),
                uow=MagicMock(),
                current_user=admin_user,
            )

    @pytest.mark.asyncio
    async def test_client_admin_cannot_manage_admin_role_invitation(self) -> None:
        tenant = _make_tenant()
        admin_user = _make_client_admin(tenant_id=tenant.id)
        invitation = _make_invitation(tenant=tenant, role=Role(name=ROLE_ADMIN))

        with (
            patch(
                "app.routes.v1.tenant_invitations.InvitationService.get_for_tenant",
                return_value=invitation,
            ),
            pytest.raises(ForbiddenError),
        ):
            await resend_invitation(
                request=_make_request(),
                tenant_id=tenant.id,
                invitation_id=invitation.id,
                uow=MagicMock(),
                current_user=admin_user,
            )

    @pytest.mark.asyncio
    async def test_super_admin_resends_and_reemails(self) -> None:
        tenant = _make_tenant()
        admin_user = _make_super_admin()
        invitation = _make_invitation(tenant=tenant, role=Role(name=ROLE_ADMIN))

        with (
            patch(
                "app.routes.v1.tenant_invitations.InvitationService.get_for_tenant",
                return_value=invitation,
            ),
            patch(
                "app.routes.v1.tenant_invitations.InvitationService.resend",
                return_value=(invitation, "new-raw-token"),
            ) as mock_resend,
            patch("app.routes.v1.tenant_invitations.EmailService.send_invitation") as mock_delay,
        ):
            result = await resend_invitation(
                request=_make_request(),
                tenant_id=tenant.id,
                invitation_id=invitation.id,
                uow=MagicMock(),
                current_user=admin_user,
            )

        assert result.success is True
        mock_resend.assert_called_once_with(invitation)
        mock_delay.assert_called_once()
        assert "new-raw-token" in mock_delay.call_args.kwargs["invite_link"]

    @pytest.mark.asyncio
    async def test_propagates_conflict_from_service(self) -> None:
        tenant = _make_tenant()
        admin_user = _make_super_admin()
        invitation = _make_invitation(
            tenant=tenant, role=Role(name=ROLE_ADMIN), status=InvitationStatus.ACCEPTED.value
        )

        with (
            patch(
                "app.routes.v1.tenant_invitations.InvitationService.get_for_tenant",
                return_value=invitation,
            ),
            patch(
                "app.routes.v1.tenant_invitations.InvitationService.resend",
                side_effect=ConflictError("Only a pending or expired invitation can be resent."),
            ),
            pytest.raises(ConflictError),
        ):
            await resend_invitation(
                request=_make_request(),
                tenant_id=tenant.id,
                invitation_id=invitation.id,
                uow=MagicMock(),
                current_user=admin_user,
            )

    @pytest.mark.asyncio
    async def test_not_found_when_invitation_missing(self) -> None:
        tenant = _make_tenant()
        admin_user = _make_super_admin()

        with (
            patch(
                "app.routes.v1.tenant_invitations.InvitationService.get_for_tenant",
                side_effect=NotFoundError("Invitation not found."),
            ),
            pytest.raises(NotFoundError),
        ):
            await resend_invitation(
                request=_make_request(),
                tenant_id=tenant.id,
                invitation_id=uuid.uuid4(),
                uow=MagicMock(),
                current_user=admin_user,
            )


class TestRevokeInvitation:
    def test_forbidden_for_other_tenant(self) -> None:
        tenant = _make_tenant()
        admin_user = _make_client_admin(tenant_id=uuid.uuid4())

        with pytest.raises(ForbiddenError):
            revoke_invitation(
                tenant_id=tenant.id,
                invitation_id=uuid.uuid4(),
                uow=MagicMock(),
                current_user=admin_user,
            )

    def test_client_admin_cannot_manage_admin_role_invitation(self) -> None:
        tenant = _make_tenant()
        admin_user = _make_client_admin(tenant_id=tenant.id)
        invitation = _make_invitation(tenant=tenant, role=Role(name=ROLE_ADMIN))

        with (
            patch(
                "app.routes.v1.tenant_invitations.InvitationService.get_for_tenant",
                return_value=invitation,
            ),
            pytest.raises(ForbiddenError),
        ):
            revoke_invitation(
                tenant_id=tenant.id,
                invitation_id=invitation.id,
                uow=MagicMock(),
                current_user=admin_user,
            )

    def test_client_admin_revokes_own_tenant_member_invitation(self) -> None:
        tenant = _make_tenant()
        admin_user = _make_client_admin(tenant_id=tenant.id)
        invitation = _make_invitation(tenant=tenant, role=Role(name=ROLE_MEMBER))
        revoked = _make_invitation(
            tenant=tenant, role=Role(name=ROLE_MEMBER), status=InvitationStatus.REVOKED.value
        )

        with (
            patch(
                "app.routes.v1.tenant_invitations.InvitationService.get_for_tenant",
                return_value=invitation,
            ),
            patch(
                "app.routes.v1.tenant_invitations.InvitationService.revoke", return_value=revoked
            ) as mock_revoke,
        ):
            result = revoke_invitation(
                tenant_id=tenant.id,
                invitation_id=invitation.id,
                uow=MagicMock(),
                current_user=admin_user,
            )

        assert result.success is True
        assert result.data.status == InvitationStatus.REVOKED
        mock_revoke.assert_called_once_with(invitation)


class TestValidateInvitation:
    def test_returns_public_view(self) -> None:
        invitation = _make_invitation()

        with patch(
            "app.routes.v1.invitations.InvitationService.get_valid_by_token",
            return_value=invitation,
        ):
            result = validate_invitation(token="raw-token-123", uow=MagicMock())

        assert result.success is True
        assert result.data.email == invitation.email
        assert result.data.tenant_name == invitation.tenant.name


class TestAcceptInvitation:
    @pytest.mark.asyncio
    async def test_sets_password_and_accepts(self) -> None:
        invitation = _make_invitation()
        user = make_user(email=invitation.email)

        mock_cognito = MagicMock()
        mock_cognito.admin_set_user_password = AsyncMock(return_value=None)

        with (
            patch(
                "app.routes.v1.invitations.InvitationService.get_valid_by_token",
                return_value=invitation,
            ),
            patch(
                "app.routes.v1.invitations.InvitationService.accept", return_value=user
            ) as mock_accept,
            patch("app.routes.v1.invitations.CognitoAuthService", return_value=mock_cognito),
        ):
            result = await accept_invitation(
                token="raw-token-123",
                body=InvitationAcceptRequest(
                    password="Sup3rSecret1!", confirm_password="Sup3rSecret1!"
                ),
                uow=MagicMock(),
            )

        assert result.success is True
        mock_cognito.admin_set_user_password.assert_called_once_with(
            email=invitation.email, password="Sup3rSecret1!", permanent=True
        )
        mock_accept.assert_called_once_with(invitation)
