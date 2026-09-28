"""Unit tests for InvitationService.

Strategy:
- All repository interactions are replaced by MagicMock so no DB is needed.
- The UnitOfWork is constructed manually with mock repositories injected.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
from unittest.mock import MagicMock, patch
import uuid

import pytest

from app.core.enums.invitation_status import InvitationStatus
from app.core.enums.notification_type import NotificationType
from app.core.enums.tenant_status import TenantStatus
from app.core.exceptions import ConflictError, NotFoundError
from app.db.unit_of_work import UnitOfWork
from app.models.postgres.invitation_model import Invitation
from app.models.postgres.role_model import Role
from app.models.postgres.tenant_model import Tenant
from app.services.invitation_service import InvitationService, _hash_token


def _make_uow() -> MagicMock:
    uow = MagicMock(spec=UnitOfWork)
    uow.tenants = MagicMock()
    uow.users = MagicMock()
    uow.roles = MagicMock()
    uow.invitations = MagicMock()
    return uow


def _make_tenant(*, status: str = TenantStatus.ACTIVE.value) -> Tenant:
    t = Tenant()
    t.id = uuid.uuid4()
    t.name = "Acme"
    t.status = status
    return t


def _make_role(*, name: str = "admin") -> Role:
    r = Role()
    r.id = uuid.uuid4()
    r.name = name
    return r


def _make_invitation(
    *,
    tenant_id: uuid.UUID | None = None,
    role_id: uuid.UUID | None = None,
    status: str = InvitationStatus.PENDING.value,
    expires_at: datetime | None = None,
    token: str | None = None,
) -> Invitation:
    inv = Invitation()
    inv.id = uuid.uuid4()
    inv.tenant_id = tenant_id or uuid.uuid4()
    inv.email = "invitee@example.com"
    inv.name = "Invitee Name"
    inv.role_id = role_id or uuid.uuid4()
    inv.cognito_sub = "sub-invitee-1"
    inv.invited_by_id = None
    inv.token_hash = _hash_token(token or "raw-token")
    inv.status = status
    inv.expires_at = expires_at or (datetime.now(UTC) + timedelta(days=7))
    inv.accepted_at = None
    return inv


class TestValidateInviteTarget:
    def test_raises_not_found_when_tenant_missing(self) -> None:
        uow = _make_uow()
        uow.tenants.get.return_value = None

        with pytest.raises(NotFoundError):
            InvitationService(uow).validate_invite_target(uuid.uuid4(), "a@b.com")

    def test_raises_conflict_when_user_already_exists(self) -> None:
        uow = _make_uow()
        uow.tenants.get.return_value = _make_tenant()
        uow.users.get_by_email.return_value = MagicMock()

        with pytest.raises(ConflictError):
            InvitationService(uow).validate_invite_target(uuid.uuid4(), "a@b.com")

    def test_raises_conflict_when_pending_invitation_exists(self) -> None:
        uow = _make_uow()
        uow.tenants.get.return_value = _make_tenant()
        uow.users.get_by_email.return_value = None
        uow.invitations.get_pending_by_tenant_and_email.return_value = _make_invitation()

        with pytest.raises(ConflictError):
            InvitationService(uow).validate_invite_target(uuid.uuid4(), "a@b.com")

    def test_returns_tenant_when_target_is_valid(self) -> None:
        uow = _make_uow()
        tenant = _make_tenant()
        uow.tenants.get.return_value = tenant
        uow.users.get_by_email.return_value = None
        uow.invitations.get_pending_by_tenant_and_email.return_value = None

        result = InvitationService(uow).validate_invite_target(tenant.id, "a@b.com")

        assert result is tenant


class TestAssertRoleExists:
    def test_raises_not_found_when_role_missing(self) -> None:
        uow = _make_uow()
        uow.roles.get_by_name.return_value = None

        with pytest.raises(NotFoundError, match="member"):
            InvitationService(uow).assert_role_exists("member")

    def test_does_not_raise_when_role_exists(self) -> None:
        uow = _make_uow()
        uow.roles.get_by_name.return_value = _make_role(name="member")

        InvitationService(uow).assert_role_exists("member")


class TestCreate:
    def test_raises_not_found_when_admin_role_missing(self) -> None:
        uow = _make_uow()
        uow.roles.get_by_name.return_value = None

        with pytest.raises(NotFoundError):
            InvitationService(uow).create(
                tenant_id=uuid.uuid4(),
                email="a@b.com",
                name="A B",
                cognito_sub="sub-1",
                invited_by_id=None,
                role_name="admin",
            )

    def test_creates_invitation_and_returns_raw_token(self) -> None:
        uow = _make_uow()
        role = _make_role()
        uow.roles.get_by_name.return_value = role

        invitation, token = InvitationService(uow).create(
            tenant_id=uuid.uuid4(),
            email="a@b.com",
            name="A B",
            cognito_sub="sub-1",
            invited_by_id=None,
            role_name="admin",
        )

        assert invitation.role_id == role.id
        assert invitation.status == InvitationStatus.PENDING.value
        # Raw token is never persisted directly — only its hash is.
        assert invitation.token_hash == hashlib.sha256(token.encode("utf-8")).hexdigest()
        assert invitation.token_hash != token
        uow.commit.assert_called_once()


class TestGetValidByToken:
    def test_raises_not_found_when_token_unknown(self) -> None:
        uow = _make_uow()
        uow.invitations.get_by_token_hash.return_value = None

        with pytest.raises(NotFoundError):
            InvitationService(uow).get_valid_by_token("bogus-token")

    def test_raises_not_found_when_already_accepted(self) -> None:
        uow = _make_uow()
        uow.invitations.get_by_token_hash.return_value = _make_invitation(
            status=InvitationStatus.ACCEPTED.value
        )

        with pytest.raises(NotFoundError):
            InvitationService(uow).get_valid_by_token("raw-token")

    def test_raises_not_found_when_expired(self) -> None:
        uow = _make_uow()
        uow.invitations.get_by_token_hash.return_value = _make_invitation(
            expires_at=datetime.now(UTC) - timedelta(days=1)
        )

        with pytest.raises(NotFoundError):
            InvitationService(uow).get_valid_by_token("raw-token")

    def test_returns_invitation_when_pending_and_unexpired(self) -> None:
        uow = _make_uow()
        invitation = _make_invitation()
        uow.invitations.get_by_token_hash.return_value = invitation

        result = InvitationService(uow).get_valid_by_token("raw-token")

        assert result is invitation


class TestGetForTenant:
    def test_raises_not_found_when_missing(self) -> None:
        uow = _make_uow()
        uow.invitations.get_by_id_and_tenant.return_value = None

        with pytest.raises(NotFoundError):
            InvitationService(uow).get_for_tenant(uuid.uuid4(), uuid.uuid4())

    def test_returns_invitation_when_found(self) -> None:
        uow = _make_uow()
        invitation = _make_invitation()
        uow.invitations.get_by_id_and_tenant.return_value = invitation

        result = InvitationService(uow).get_for_tenant(invitation.id, invitation.tenant_id)

        assert result is invitation


class TestListForTenant:
    def test_delegates_to_repository(self) -> None:
        uow = _make_uow()
        invitations = [_make_invitation()]
        uow.invitations.list_by_tenant.return_value = (invitations, 1)
        tenant_id = uuid.uuid4()

        result, total = InvitationService(uow).list_for_tenant(
            tenant_id, status="pending", skip=0, limit=20
        )

        assert result == invitations
        assert total == 1
        uow.invitations.list_by_tenant.assert_called_once_with(
            tenant_id, status="pending", skip=0, limit=20
        )


class TestResend:
    def test_raises_conflict_when_already_accepted(self) -> None:
        uow = _make_uow()
        invitation = _make_invitation(status=InvitationStatus.ACCEPTED.value)

        with pytest.raises(ConflictError):
            InvitationService(uow).resend(invitation)

    def test_raises_conflict_when_already_revoked(self) -> None:
        uow = _make_uow()
        invitation = _make_invitation(status=InvitationStatus.REVOKED.value)

        with pytest.raises(ConflictError):
            InvitationService(uow).resend(invitation)

    def test_issues_new_token_and_resets_expiry(self) -> None:
        uow = _make_uow()
        original_hash = _hash_token("raw-token")
        original_expiry = datetime.now(UTC) - timedelta(days=1)
        invitation = _make_invitation(
            status=InvitationStatus.PENDING.value,
            expires_at=original_expiry,
            token="raw-token",
        )

        _, token = InvitationService(uow).resend(invitation)

        assert invitation.token_hash == hashlib.sha256(token.encode("utf-8")).hexdigest()
        assert invitation.token_hash != original_hash
        assert invitation.status == InvitationStatus.PENDING.value
        assert invitation.expires_at > datetime.now(UTC)
        uow.commit.assert_called_once()

    def test_allows_resend_of_expired_status(self) -> None:
        uow = _make_uow()
        invitation = _make_invitation(status=InvitationStatus.EXPIRED.value)

        InvitationService(uow).resend(invitation)

        assert invitation.status == InvitationStatus.PENDING.value


class TestRevoke:
    def test_raises_conflict_when_already_accepted(self) -> None:
        uow = _make_uow()
        invitation = _make_invitation(status=InvitationStatus.ACCEPTED.value)

        with pytest.raises(ConflictError):
            InvitationService(uow).revoke(invitation)

    def test_raises_conflict_when_already_revoked(self) -> None:
        uow = _make_uow()
        invitation = _make_invitation(status=InvitationStatus.REVOKED.value)

        with pytest.raises(ConflictError):
            InvitationService(uow).revoke(invitation)

    def test_marks_pending_invitation_revoked(self) -> None:
        uow = _make_uow()
        invitation = _make_invitation(status=InvitationStatus.PENDING.value)

        result = InvitationService(uow).revoke(invitation)

        assert result.status == InvitationStatus.REVOKED.value
        uow.commit.assert_called_once()


class TestAccept:
    @pytest.fixture(autouse=True)
    def _mock_publish_notification(self):
        """Prevent accept's welcome-notification call from opening a real
        UnitOfWork/Redis publish — patched at ``app.services.invitation_service``
        since it's imported at module level there.
        """
        with patch("app.services.invitation_service.publish_notification") as mock:
            yield mock

    def test_creates_user_assigns_role_and_marks_accepted(
        self, _mock_publish_notification
    ) -> None:
        uow = _make_uow()
        role = _make_role()
        tenant = _make_tenant(status=TenantStatus.ACTIVE.value)
        invitation = _make_invitation(tenant_id=tenant.id, role_id=role.id)
        uow.roles.get.return_value = role
        uow.tenants.get.return_value = tenant

        user = InvitationService(uow).accept(invitation)

        assert user.email == invitation.email
        assert user.tenant_id == invitation.tenant_id
        assert user.is_active is True
        assert user.is_verified is True
        assert role in user.roles
        assert invitation.status == InvitationStatus.ACCEPTED.value
        assert invitation.accepted_at is not None
        uow.commit.assert_called_once()
        _mock_publish_notification.assert_called_once_with(
            user_id=user.id,
            title="Welcome to RIP",
            message='Your invitation to join "Acme" has been accepted.',
            notification_type=NotificationType.SUCCESS,
            data={"tenant_id": str(tenant.id)},
        )

    def test_activates_tenant_still_pending_invitation(self) -> None:
        uow = _make_uow()
        role = _make_role()
        tenant = _make_tenant(status=TenantStatus.PENDING_INVITATION.value)
        invitation = _make_invitation(tenant_id=tenant.id, role_id=role.id)
        uow.roles.get.return_value = role
        uow.tenants.get.return_value = tenant

        InvitationService(uow).accept(invitation)

        assert tenant.status == TenantStatus.ACTIVE.value

    def test_leaves_already_active_tenant_unchanged(self) -> None:
        uow = _make_uow()
        role = _make_role()
        tenant = _make_tenant(status=TenantStatus.ACTIVE.value)
        invitation = _make_invitation(tenant_id=tenant.id, role_id=role.id)
        uow.roles.get.return_value = role
        uow.tenants.get.return_value = tenant

        InvitationService(uow).accept(invitation)

        assert tenant.status == TenantStatus.ACTIVE.value

    def test_succeeds_when_notification_publish_fails(
        self, _mock_publish_notification
    ) -> None:
        uow = _make_uow()
        role = _make_role()
        tenant = _make_tenant(status=TenantStatus.ACTIVE.value)
        invitation = _make_invitation(tenant_id=tenant.id, role_id=role.id)
        uow.roles.get.return_value = role
        uow.tenants.get.return_value = tenant
        _mock_publish_notification.side_effect = Exception("redis down")

        user = InvitationService(uow).accept(invitation)

        assert user.email == invitation.email
        _mock_publish_notification.assert_called_once()
