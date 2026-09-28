"""Invitation lifecycle service.

Two invite paths share this service (see ``app/routes/v1/tenant_invitations.py``):
super_admin invites a Client Admin into any tenant, or a Client Admin
invites a Member into their own tenant. The route layer decides *which*
role is granted (based on the caller's own role) and passes it to
:meth:`InvitationService.create` — this service does not make that call.

All methods are synchronous and operate through an open
:class:`~app.db.unit_of_work.UnitOfWork` passed at construction time, matching
the pattern used by :class:`~app.services.tenant_service.TenantService` and
:class:`~app.services.user_service.UserService`. Cognito I/O (creating the
identity, setting the password) is orchestrated by the route layer via
``CognitoAuthService`` — see ``app/routes/v1/tenant_invitations.py`` and
``app/routes/v1/invitations.py``, matching the existing ``auth.py`` convention.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import secrets
import uuid

from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.enums.invitation_status import InvitationStatus
from app.core.enums.notification_type import NotificationType
from app.core.enums.tenant_status import TenantStatus
from app.core.exceptions import ConflictError, NotFoundError
from app.core.messages import (
    MSG_INVITATION_ALREADY_EXISTS,
    MSG_INVITATION_CANNOT_RESEND,
    MSG_INVITATION_CANNOT_REVOKE,
    MSG_INVITATION_NOT_FOUND,
    MSG_INVITATION_NOT_FOUND_OR_EXPIRED,
    MSG_TENANT_NOT_FOUND,
    MSG_USER_ROLE_NOT_FOUND,
)
from app.db.unit_of_work import UnitOfWork
from app.models.postgres.invitation_model import Invitation
from app.models.postgres.tenant_model import Tenant
from app.models.postgres.user_model import User
from app.services.notification_service import publish_notification
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class InvitationService:
    """Invitation CRUD/lifecycle, scoped through an open :class:`UnitOfWork`."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    # ── Invite creation ────────────────────────────────────────────────────

    def assert_role_exists(self, role_name: str) -> None:
        """Raise :class:`NotFoundError` if *role_name* isn't a real row in the roles table.

        Callers (the route layer) must invoke this before any external side
        effect (e.g. creating a Cognito identity via ``CognitoAuthService``)
        so an invalid/typo'd role name fails fast rather than wasting that
        call — :meth:`create` re-validates this internally too (defense in
        depth), but by then the Cognito identity has already been created.
        """
        if self._uow.roles.get_by_name(role_name) is None:
            raise NotFoundError(MSG_USER_ROLE_NOT_FOUND.format(role_name=role_name))

    def validate_invite_target(self, tenant_id: uuid.UUID, email: str) -> Tenant:
        """Return the target tenant, or raise if inviting *email* is not allowed.

        Raises :class:`NotFoundError` if the tenant doesn't exist, and
        :class:`ConflictError` if an active user or a still-pending
        invitation already exists for this email in this tenant.
        """
        tenant = self._uow.tenants.get(tenant_id)
        if tenant is None:
            raise NotFoundError(MSG_TENANT_NOT_FOUND.format(tenant_id=tenant_id))

        if self._uow.users.get_by_email(email) is not None:
            raise ConflictError(MSG_INVITATION_ALREADY_EXISTS)
        if self._uow.invitations.get_pending_by_tenant_and_email(tenant_id, email) is not None:
            raise ConflictError(MSG_INVITATION_ALREADY_EXISTS)
        return tenant

    def create(
        self,
        *,
        tenant_id: uuid.UUID,
        email: str,
        name: str,
        cognito_sub: str,
        invited_by_id: uuid.UUID | None,
        role_name: str,
    ) -> tuple[Invitation, str]:
        """Create a pending invitation and return it with the raw (unhashed) token.

        *role_name* is granted to the invitee on acceptance — the caller
        (route layer) decides which role that is; this service does not.

        The raw token is never persisted — only its SHA-256 hash is stored —
        so the caller must capture the returned token immediately to build
        the invite-accept email link.
        """
        role = self._uow.roles.get_by_name(role_name)
        if role is None:
            raise NotFoundError(MSG_USER_ROLE_NOT_FOUND.format(role_name=role_name))

        token = _generate_token()
        invitation = Invitation(
            tenant_id=tenant_id,
            email=email,
            name=name,
            role_id=role.id,
            cognito_sub=cognito_sub,
            invited_by_id=invited_by_id,
            token_hash=_hash_token(token),
            status=InvitationStatus.PENDING.value,
            expires_at=datetime.now(UTC) + timedelta(days=settings.INVITATION_EXPIRY_DAYS),
        )
        self._uow.add(invitation)
        self._uow.flush()
        self._uow.refresh(invitation)
        self._uow.commit()
        logger.info("Created invitation id=%s tenant_id=%s", invitation.id, tenant_id)
        return invitation, token

    # ── Management (list / resend / revoke) ─────────────────────────────────

    # Statuses from which an invitation can still be acted on. "expired" is
    # included defensively — nothing currently flips a row's persisted
    # ``status`` to "expired" on its own (expiry is only checked dynamically,
    # in :meth:`get_valid_by_token`), so in practice a lapsed invitation is
    # still stored as "pending" and reachable here.
    _ACTIONABLE_STATUSES = {InvitationStatus.PENDING.value, InvitationStatus.EXPIRED.value}

    def get_for_tenant(self, invitation_id: uuid.UUID, tenant_id: uuid.UUID) -> Invitation:
        """Return the invitation scoped to *tenant_id*, or raise :class:`NotFoundError`.

        Scoping by tenant (not just by ID) prevents a Client Admin of one
        tenant from resending/revoking an invitation that belongs to another.
        """
        invitation = self._uow.invitations.get_by_id_and_tenant(invitation_id, tenant_id)
        if invitation is None:
            raise NotFoundError(MSG_INVITATION_NOT_FOUND)
        return invitation

    def list_for_tenant(
        self,
        tenant_id: uuid.UUID,
        *,
        status: str | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> tuple[list[Invitation], int]:
        """Return a page of *tenant_id*'s invitations, optionally filtered by status."""
        return self._uow.invitations.list_by_tenant(
            tenant_id, status=status, skip=skip, limit=limit
        )

    def resend(self, invitation: Invitation) -> tuple[Invitation, str]:
        """Issue a fresh token and expiry for *invitation*, invalidating the old link.

        Reuses the invitation's existing Cognito identity (``cognito_sub``)
        rather than creating a new one — the invitee still doesn't exist as a
        local :class:`User` until they accept, so nothing else needs to change.
        Returns the invitation with the new raw (unhashed) token, which the
        caller must use to build the new invite-accept link immediately —
        only the hash is persisted.
        """
        if invitation.status not in self._ACTIONABLE_STATUSES:
            raise ConflictError(MSG_INVITATION_CANNOT_RESEND)

        token = _generate_token()
        invitation.token_hash = _hash_token(token)
        invitation.status = InvitationStatus.PENDING.value
        invitation.expires_at = datetime.now(UTC) + timedelta(days=settings.INVITATION_EXPIRY_DAYS)
        self._uow.add(invitation)
        self._uow.flush()
        self._uow.refresh(invitation)
        self._uow.commit()
        logger.info("Resent invitation id=%s tenant_id=%s", invitation.id, invitation.tenant_id)
        return invitation, token

    def revoke(self, invitation: Invitation) -> Invitation:
        """Mark *invitation* as revoked, permanently invalidating its link.

        Once revoked, the tenant+email pair is free to be re-invited from
        scratch via :meth:`create` (``validate_invite_target`` only blocks on
        a still-*pending* invitation).
        """
        if invitation.status not in self._ACTIONABLE_STATUSES:
            raise ConflictError(MSG_INVITATION_CANNOT_REVOKE)

        invitation.status = InvitationStatus.REVOKED.value
        self._uow.add(invitation)
        self._uow.flush()
        self._uow.refresh(invitation)
        self._uow.commit()
        logger.info("Revoked invitation id=%s tenant_id=%s", invitation.id, invitation.tenant_id)
        return invitation

    # ── Accept flow ────────────────────────────────────────────────────────

    def get_valid_by_token(self, token: str) -> Invitation:
        """Return the invitation for *token* if it is still pending and unexpired.

        Raises :class:`NotFoundError` (generic message) for a missing,
        already-accepted/revoked, or expired token alike — the caller is
        unauthenticated, so the response must not reveal which case applies.
        """
        invitation = self._uow.invitations.get_by_token_hash(_hash_token(token))
        if (
            invitation is None
            or invitation.status != InvitationStatus.PENDING.value
            or invitation.expires_at < datetime.now(UTC)
        ):
            raise NotFoundError(MSG_INVITATION_NOT_FOUND_OR_EXPIRED)
        return invitation

    def accept(self, invitation: Invitation) -> User:
        """Finalize acceptance: create the local user, grant the invited role,
        mark the invitation accepted, and activate the tenant if it was
        still pending its first admin.

        Raises :class:`NotFoundError` (the same generic "invalid or expired"
        message as :meth:`get_valid_by_token`) if the INSERT below hits the
        partial unique index on ``users.cognito_sub`` — i.e. a live user for
        this invitation's identity already exists. This is a last-resort
        backstop for a concurrent duplicate accept request racing this one:
        ``get_valid_by_token`` only checked the invitation's own status
        moments ago and can't see an in-flight sibling request, so the
        conflict can only be detected here, at the DB constraint.
        """
        user = User(
            cognito_sub=invitation.cognito_sub,
            email=invitation.email,
            name=invitation.name,
            is_active=True,
            is_verified=True,
            tenant_id=invitation.tenant_id,
        )
        self._uow.add(user)
        try:
            self._uow.flush()
        except IntegrityError as exc:
            self._uow.rollback()
            if "ix_users_cognito_sub_active" in str(exc.orig):
                raise NotFoundError(MSG_INVITATION_NOT_FOUND_OR_EXPIRED) from exc
            raise

        role = self._uow.roles.get(invitation.role_id)
        if role is not None:
            user.roles.append(role)

        invitation.status = InvitationStatus.ACCEPTED.value
        invitation.accepted_at = datetime.now(UTC)
        self._uow.add(invitation)

        tenant = self._uow.tenants.get(invitation.tenant_id)
        if tenant is not None and tenant.status == TenantStatus.PENDING_INVITATION.value:
            tenant.status = TenantStatus.ACTIVE.value
            self._uow.add(tenant)

        self._uow.flush()
        self._uow.refresh(user)
        self._uow.commit()
        logger.info("Invitation id=%s accepted, created user id=%s", invitation.id, user.id)
        self._notify_invitation_accepted(user, tenant)
        return user

    @staticmethod
    def _notify_invitation_accepted(user: User, tenant: Tenant | None) -> None:
        """Best-effort: push a welcome in-app bell notification for the new user.

        The invitee has no ``User`` row (and so no ``publish_notification``
        target) until this point — an invite-send-time notification isn't
        possible, unlike the email sent at :meth:`create`/:meth:`resend`.
        Never raises: a notification failure must not affect the accept
        response, same convention as ``ProjectService._notify_project_created``.
        """
        try:
            tenant_name = tenant.name if tenant is not None else "RIP"
            publish_notification(
                user_id=user.id,
                title="Welcome to RIP",
                message=f'Your invitation to join "{tenant_name}" has been accepted.',
                notification_type=NotificationType.SUCCESS,
                data={"tenant_id": str(user.tenant_id) if user.tenant_id else None},
            )
        except Exception:
            logger.warning(
                "_notify_invitation_accepted: failed for user_id=%s", user.id, exc_info=True
            )
