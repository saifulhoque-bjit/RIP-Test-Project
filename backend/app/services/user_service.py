"""User lifecycle service.

Responsibilities
────────────────
- **Cognito sync**: upsert a ``User`` record the first time a Cognito identity
  is seen, and fill missing mutable fields on subsequent requests.
- **Default role assignment**: every newly created user automatically receives
  the ``member`` role.
- **Profile management**: name update, soft-delete (deactivate).
- **RBAC management**: assign/revoke named roles (admin-only callers expected
  by the route layer).

All methods are synchronous and operate through an open
:class:`~app.db.unit_of_work.UnitOfWork` passed at construction time.
"""

from __future__ import annotations

from datetime import UTC, datetime
import uuid

from sqlalchemy.exc import IntegrityError

from app.core.constants import ROLE_ADMIN, ROLE_MEMBER, ROLE_SUPER_ADMIN
from app.core.enums.notification_type import NotificationType
from app.core.enums.tenant_status import TenantStatus
from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.messages import (
    MSG_AUTH_INVALID_CREDENTIALS,
    MSG_RBAC_ADMIN_ROLE_FORBIDDEN,
    MSG_RBAC_SUPER_ADMIN_ROLE_FORBIDDEN,
    MSG_TENANT_ACCOUNT_DEACTIVATED,
    MSG_USER_ACCOUNT_DEACTIVATED,
    MSG_USER_CANNOT_CHANGE_OWN_STATUS,
    MSG_USER_CANNOT_REMOVE_SELF,
    MSG_USER_NOT_FOUND,
    MSG_USER_ROLE_NOT_FOUND,
    MSG_USER_STATUS_CHANGE_FORBIDDEN_REMOVED,
)
from app.db.unit_of_work import UnitOfWork
from app.models.postgres.role_model import Role
from app.models.postgres.user_model import User, UserRole
from app.schemas.user_schema import ProjectSummaryOut, RoleBasicOut, UserListItemOut
from app.services.email_service import EmailService
from app.services.notification_service import publish_notification
from app.utils.logger import get_logger

logger = get_logger(__name__)


class UserService:
    """All user-management operations require an open :class:`UnitOfWork`."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    # ── Cognito sync ───────────────────────────────────────────────────────

    @staticmethod
    def _is_missing_text(value: str | None) -> bool:
        return value is None or value.strip() == ""

    @classmethod
    def _backfill_missing_fields(
        cls,
        user: User,
        *,
        sub: str,
        email: str,
        username: str | None,
        name: str | None,
        is_verified: bool,
    ) -> bool:
        """Fill only currently-missing fields on *user* from fresh Cognito claims.

        Never overwrites an existing value. Returns whether anything changed.
        """
        changed = False
        if sub and cls._is_missing_text(user.cognito_sub):
            user.cognito_sub = sub
            changed = True
        if email and cls._is_missing_text(user.email):
            user.email = email
            changed = True
        if username and cls._is_missing_text(user.username):
            user.username = username
            changed = True
        if name and cls._is_missing_text(user.name):
            user.name = name
            changed = True
        # Promote verification only when still false.
        if is_verified and not user.is_verified:
            user.is_verified = True
            changed = True
        return changed

    def get_authenticated_user(
        self,
        *,
        sub: str,
        email: str,
        name: str | None,
        is_verified: bool,
        username: str | None = None,
    ) -> User:
        """Return the local user for an already-Cognito-authenticated identity.

        Deliberately does **not** create a row — unlike
        :meth:`get_or_create_by_cognito_sub`. Reaching the app requires both
        a valid Cognito session (verified by the caller before this runs)
        and an existing local ``User`` row, which only comes into being once
        an invitation has been accepted (``InvitationService.accept``) or,
        for the seeded Super Admin, at startup (``seed_super_admin``).

        A Cognito identity can exist (e.g. an invitation was created, which
        provisions Cognito eagerly) before its invitation is accepted, or
        belong to a self-registration that was never invited at all — in
        both cases Cognito auth succeeds but there is no local row yet, and
        auto-creating one here would race the accept flow's own INSERT (the
        exact bug this method replaces) or leave an orphan row that later
        blocks a legitimate invite to that email.

        Raises :class:`ForbiddenError` if no local user exists yet, or if
        the one found has been deactivated. Both use the same generic
        ``MSG_AUTH_INVALID_CREDENTIALS`` text as a bad email/password would
        (see :class:`~app.services.auth_service.CognitoAuthService`) — this
        never reveals to the caller that their Cognito identity exists but
        has no accepted invitation.

        Also raises :class:`ForbiddenError` if the user's tenant has been
        deactivated — this method backs both the login flow
        (``attach_user_roles``) and every protected request
        (``deps.get_current_db_user``), so a tenant deactivation takes
        effect immediately for already-issued sessions, not just future
        logins.
        """
        user = self._uow.users.get_by_cognito_sub(sub)
        if user is None and email:
            user = self._uow.users.get_by_email(email)
        if user is None:
            raise ForbiddenError(MSG_AUTH_INVALID_CREDENTIALS)

        if not user.is_active:
            raise ForbiddenError(MSG_USER_ACCOUNT_DEACTIVATED)

        if user.tenant_id is not None and (
            user.tenant is None or user.tenant.status != TenantStatus.ACTIVE.value
        ):
            raise ForbiddenError(MSG_TENANT_ACCOUNT_DEACTIVATED)

        if self._backfill_missing_fields(
            user, sub=sub, email=email, username=username, name=name, is_verified=is_verified
        ):
            self._uow.add(user)
            self._uow.flush()
            self._uow.refresh(user)
            self._uow.commit()

        return user

    def _create_user_or_use_race_winner(
        self,
        *,
        sub: str,
        email: str,
        username: str | None,
        name: str | None,
        is_verified: bool,
        assign_default_role: bool,
    ) -> User:
        """Insert a brand-new local user, or return a racing sibling's row.

        Two concurrent first-ever requests for the same identity (e.g. two
        tabs firing right after login) can both find no existing user and
        both reach this method at once — the loser's INSERT then hits the
        partial unique index on ``users.cognito_sub``/``email``. That's
        treated as the winner having already created the row: re-fetch and
        return it instead of raising, since this is an idempotent upsert,
        not a user-facing create request.
        """
        user = User(
            cognito_sub=sub,
            email=email,
            username=username,
            name=name,
            is_active=True,
            is_verified=is_verified,
        )
        self._uow.add(user)
        try:
            self._uow.flush()
        except IntegrityError:
            self._uow.rollback()
            winner = self._uow.users.get_by_cognito_sub(sub) or self._uow.users.get_by_email(email)
            if winner is None:
                raise
            logger.info("Lost create race for sub=%s, using winner's row id=%s", sub, winner.id)
            return winner

        self._uow.refresh(user)
        if assign_default_role:
            self._assign_default_role(user)
        logger.info("Created local user record sub=%s", sub)
        return user

    def get_or_create_by_cognito_sub(
        self,
        *,
        sub: str,
        email: str,
        name: str | None,
        is_verified: bool,
        username: str | None = None,
        assign_default_role: bool = True,
    ) -> User:
        """Upsert a local user record from Cognito claims.

        Behavior:
        1. If no user exists, create it.
        2. If user exists, only fill fields that are currently missing.
        3. If nothing is missing, return the user without updating.
        """
        user = self._uow.users.get_by_cognito_sub(sub)

        if user is None:
            # Fallback by email for edge-cases where sub is not persisted yet.
            user = self._uow.users.get_by_email(email)
            if user is not None and self._is_missing_text(user.cognito_sub):
                user.cognito_sub = sub

        changed = False

        if user is None:
            user = self._create_user_or_use_race_winner(
                sub=sub,
                email=email,
                username=username,
                name=name,
                is_verified=is_verified,
                assign_default_role=assign_default_role,
            )
            changed = True
        else:
            changed = self._backfill_missing_fields(
                user, sub=sub, email=email, username=username, name=name, is_verified=is_verified
            )
            if changed:
                self._uow.add(user)

        if not user.is_active:
            raise ForbiddenError(MSG_USER_ACCOUNT_DEACTIVATED)

        if changed:
            self._uow.flush()
            self._uow.refresh(user)
            self._uow.commit()

        return user

    def _assign_default_role(self, user: User) -> None:
        """Append the default *member* role to *user* if it exists in the DB."""
        role = self._uow.roles.get_by_name(ROLE_MEMBER)
        if role is not None:
            user.roles.append(role)
            self._uow.flush()

    # ── Queries ────────────────────────────────────────────────────────────

    def get_by_id(self, user_id: uuid.UUID) -> User:
        """Return a user by primary key or raise :class:`NotFoundError`."""
        user = self._uow.users.get(user_id)
        if user is None:
            raise NotFoundError(MSG_USER_NOT_FOUND.format(user_id=user_id))
        return user

    def get_by_id_scoped(self, user_id: uuid.UUID, requester_tenant_id: uuid.UUID | None) -> User:
        """Return a user by primary key, scoped to *requester_tenant_id*.

        Raises :class:`NotFoundError` both when the user doesn't exist and
        when it belongs to a different tenant than the requester — a tenant
        admin gets the same 404 either way, so they can't use this endpoint
        to probe for the existence of users in other tenants.
        ``requester_tenant_id=None`` (super_admin, or un-tenanted deployment)
        applies no scoping.
        """
        user = self.get_by_id(user_id)
        if requester_tenant_id is not None and user.tenant_id != requester_tenant_id:
            raise NotFoundError(MSG_USER_NOT_FOUND.format(user_id=user_id))
        return user

    def list_users(
        self,
        skip: int = 0,
        limit: int = 20,
        tenant_id: uuid.UUID | None = None,
    ) -> tuple[list[User], int]:
        """Return a paginated list of users and the total count.

        ``tenant_id=None`` applies no filter (``super_admin`` cross-tenant
        listing, or un-tenanted deployments).
        """
        return self._uow.users.get_paginated(skip=skip, limit=limit, tenant_id=tenant_id)

    @staticmethod
    def _owner_tenant_roles(user: User) -> list[str]:
        """Tenant-wide roles that explain project ownership.

        Project creation is admin/super_admin-only (see ``POST /projects``),
        so an owner always holds at least one of these two roles.
        """
        return [r.name for r in user.roles if r.name in (ROLE_ADMIN, ROLE_SUPER_ADMIN)]

    def list_users_with_projects(
        self,
        skip: int = 0,
        limit: int = 20,
        tenant_id: uuid.UUID | None = None,
    ) -> tuple[list[UserListItemOut], int]:
        """Return a paginated list of users, each with the projects they own
        or are assigned to, and roles without their permission set.

        ``tenant_id=None`` applies no filter (``super_admin`` cross-tenant
        listing, or un-tenanted deployments).
        """
        users, total = self._uow.users.get_paginated(skip=skip, limit=limit, tenant_id=tenant_id)
        projects_by_user = self._uow.projects.get_projects_by_user_ids([u.id for u in users])

        items = [
            UserListItemOut(
                id=user.id,
                cognito_sub=user.cognito_sub,
                email=user.email,
                name=user.name,
                is_active=user.is_active,
                is_verified=user.is_verified,
                tenant_id=user.tenant_id,
                roles=[RoleBasicOut.model_validate(r) for r in user.roles],
                projects=[
                    ProjectSummaryOut(
                        id=p.id,
                        name=p.name,
                        status=p.status,
                        is_owner=is_owner,
                        roles=(self._owner_tenant_roles(user) if is_owner else []) + member_roles,
                    )
                    for p, is_owner, member_roles in projects_by_user.get(user.id, [])
                ],
                created_at=user.created_at,
                updated_at=user.updated_at,
            )
            for user in users
        ]
        return items, total

    # ── Profile management ─────────────────────────────────────────────────

    def update_profile(self, user_id: uuid.UUID, *, name: str) -> User:
        """Update the display name of *user_id* and persist the change."""
        user = self.get_by_id(user_id)
        user.name = name
        self._uow.add(user)
        self._uow.flush()
        self._uow.refresh(user)
        self._uow.commit()
        return user

    def deactivate(self, user_id: uuid.UUID) -> User:
        """Self-service account deactivation: sets ``is_active = False`` only.

        Reversible (no ``deleted_at``, no Cognito change, email/cognito_sub
        stay reserved) — used only by ``DELETE /users/me``. For an
        admin-initiated permanent removal that also frees the user's
        identity for reuse, see :meth:`remove_user`.
        """
        user = self.get_by_id(user_id)
        user.is_active = False
        self._uow.add(user)
        self._uow.flush()
        self._uow.refresh(user)
        self._uow.commit()
        logger.info("Deactivated user id=%s", user_id)
        return user

    def set_active_status(
        self,
        user_id: uuid.UUID,
        is_active: bool,
        requester_id: uuid.UUID,
        requester_tenant_id: uuid.UUID | None = None,
    ) -> User:
        """Activate or deactivate a user (admin-only, tenant-scoped).

        Distinct from :meth:`remove_user`: this only flips ``is_active``,
        is fully reversible either direction, and never touches Cognito or
        ``deleted_at`` — the user's email/cognito_sub stay reserved and they
        can be reactivated at any time. Distinct from :meth:`deactivate`:
        this is the admin-facing counterpart that can target any user in
        scope (not just the caller) and supports reactivating too.

        Raises :class:`ForbiddenError` if *requester_id* matches *user_id*
        (self-deactivation must go through self-service ``DELETE /users/me``
        instead, and self-reactivation makes no sense — a deactivated
        caller cannot authenticate to reach this endpoint in the first
        place) or if the target has been removed (``deleted_at`` set) — a
        removed user's Cognito identity no longer exists, so flipping
        ``is_active`` back to ``True`` here would leave a row that can
        never actually authenticate again.

        The target user is resolved tenant-scoped — see :meth:`assign_role`.
        Sends the user an ``EmailService.send_account_status_changed`` email
        and an in-app bell notification once the status change is durable —
        applies regardless of the target's role (Client Admin or Member).
        """
        if requester_id == user_id:
            raise ForbiddenError(MSG_USER_CANNOT_CHANGE_OWN_STATUS)

        user = self.get_by_id_scoped(user_id, requester_tenant_id)
        if user.deleted_at is not None:
            raise ForbiddenError(MSG_USER_STATUS_CHANGE_FORBIDDEN_REMOVED)

        user.is_active = is_active
        self._uow.add(user)
        self._uow.flush()
        self._uow.refresh(user)
        self._uow.commit()
        logger.info("Set is_active=%s for user id=%s", is_active, user_id)
        EmailService.send_account_status_changed(
            recipient=user.email, name=user.name, is_active=is_active
        )
        self._notify_status_changed(user, is_active)
        return user

    @staticmethod
    def _notify_status_changed(user: User, is_active: bool) -> None:
        """Best-effort: push an in-app bell notification for the status change.

        Mirrors the email sent by :meth:`set_active_status` — never raises,
        since a notification failure must not affect the status-update
        response (same convention as ``ProjectService._notify_project_created``).
        """
        try:
            if is_active:
                title, message, notification_type = (
                    "Account Reactivated",
                    "Your account has been reactivated. You can sign in and resume your work.",
                    NotificationType.SUCCESS,
                )
            else:
                title, message, notification_type = (
                    "Account Deactivated",
                    "Your account has been deactivated by an administrator.",
                    NotificationType.WARNING,
                )
            publish_notification(
                user_id=user.id,
                title=title,
                message=message,
                notification_type=notification_type,
                data={"is_active": is_active},
            )
        except Exception:
            logger.warning(
                "_notify_status_changed: failed for user_id=%s", user.id, exc_info=True
            )

    def assert_can_remove(
        self,
        user_id: uuid.UUID,
        requester_id: uuid.UUID,
        requester_tenant_id: uuid.UUID | None = None,
    ) -> User:
        """Validate a removal request and return the target user, read-only.

        Callers (the route layer) use this to fetch the target's email
        *before* deleting their Cognito identity — the self-removal guard
        and tenant-scope check must run before that external, hard-to-undo
        call is made, not after. See :meth:`remove_user` for the actual
        Postgres mutation, which re-validates both checks independently.
        """
        if requester_id == user_id:
            raise ForbiddenError(MSG_USER_CANNOT_REMOVE_SELF)
        return self.get_by_id_scoped(user_id, requester_tenant_id)

    def remove_user(
        self,
        user_id: uuid.UUID,
        requester_id: uuid.UUID,
        requester_tenant_id: uuid.UUID | None = None,
    ) -> User:
        """Permanently remove a user (admin-only, tenant-scoped).

        Sets ``is_active=False`` and stamps ``deleted_at``, which — via the
        partial unique indexes on ``email``/``cognito_sub`` (migration
        0021) — immediately frees that email/cognito_sub for a future
        invite or registration. The row itself is preserved (not hard
        deleted) so anything referencing it — project ownership, source
        uploads, role-assignment audit trail — stays intact.

        Callers must delete the corresponding Cognito identity themselves
        *before* calling this (see ``CognitoAuthService.admin_delete_user``)
        — this method only touches Postgres, and the DB half should run
        only once the harder-to-retry Cognito deletion has already
        succeeded.

        Raises :class:`ForbiddenError` if *requester_id* matches *user_id* —
        an admin cannot remove their own account through this endpoint
        (use self-service ``DELETE /users/me`` instead), since it could
        otherwise strand a tenant/platform without any admin.

        The target user is resolved tenant-scoped — see :meth:`assign_role`.
        """
        if requester_id == user_id:
            raise ForbiddenError(MSG_USER_CANNOT_REMOVE_SELF)

        user = self.get_by_id_scoped(user_id, requester_tenant_id)
        user.is_active = False
        user.deleted_at = datetime.now(UTC)
        self._uow.add(user)
        self._uow.flush()
        self._uow.refresh(user)
        self._uow.commit()
        logger.info("Removed user id=%s", user_id)
        return user

    # ── Role management ────────────────────────────────────────────────────

    def assign_role(
        self,
        user_id: uuid.UUID,
        role_name: str,
        assigned_by_id: uuid.UUID | None = None,
        requester_roles: list[str] | None = None,
        requester_tenant_id: uuid.UUID | None = None,
    ) -> User:
        """Add ``role_name`` to the user's role set (idempotent).

        If ``assigned_by_id`` is provided it is written to the
        ``user_roles.assigned_by`` audit column.

        The target user is resolved tenant-scoped (404 if the requester
        isn't ``super_admin`` and the target belongs to a different tenant).
        Only a ``super_admin`` requester may grant the ``super_admin`` or
        ``admin`` (Client Admin) role — otherwise raises
        :class:`ForbiddenError` to prevent privilege escalation. The only
        path to becoming a Client Admin is a super_admin invite (see
        ``app/routes/v1/tenant_invitations.py::invite_user``); this generic role
        endpoint must enforce the same rule.
        """
        if (
            role_name == ROLE_SUPER_ADMIN
            and requester_roles is not None
            and ROLE_SUPER_ADMIN not in requester_roles
        ):
            raise ForbiddenError(MSG_RBAC_SUPER_ADMIN_ROLE_FORBIDDEN)
        if (
            role_name == ROLE_ADMIN
            and requester_roles is not None
            and ROLE_SUPER_ADMIN not in requester_roles
        ):
            raise ForbiddenError(MSG_RBAC_ADMIN_ROLE_FORBIDDEN)

        user = self.get_by_id_scoped(user_id, requester_tenant_id)
        role = self._uow.roles.get_by_name(role_name)
        if role is None:
            raise NotFoundError(MSG_USER_ROLE_NOT_FOUND.format(role_name=role_name))

        already_assigned = any(r.name == role_name for r in user.roles)
        if not already_assigned:
            user.roles.append(role)
            self._uow.flush()

            # Update the audit column on the freshly-inserted junction row.
            if assigned_by_id is not None:
                junction = (
                    self._uow.session.query(UserRole)
                    .filter_by(user_id=user.id, role_id=role.id)
                    .first()
                )
                if junction is not None:
                    junction.assigned_by = assigned_by_id

            self._uow.flush()
            self._uow.refresh(user)
            self._uow.commit()
            logger.info(
                "Assigned role '%s' to user id=%s (by %s)",
                role_name,
                user_id,
                assigned_by_id,
            )
        return user

    def revoke_role(
        self,
        user_id: uuid.UUID,
        role_name: str,
        requester_tenant_id: uuid.UUID | None = None,
    ) -> User:
        """Remove ``role_name`` from the user's role set (idempotent).

        The target user is resolved tenant-scoped — see :meth:`assign_role`.
        """
        user = self.get_by_id_scoped(user_id, requester_tenant_id)
        role = self._uow.roles.get_by_name(role_name)
        if role is None:
            raise NotFoundError(MSG_USER_ROLE_NOT_FOUND.format(role_name=role_name))

        user.roles = [r for r in user.roles if r.name != role_name]
        self._uow.add(user)
        self._uow.flush()
        self._uow.refresh(user)
        self._uow.commit()
        logger.info("Revoked role '%s' from user id=%s", role_name, user_id)
        return user

    def update_roles(
        self,
        user_id: uuid.UUID,
        role_names: list[str],
        assigned_by_id: uuid.UUID | None = None,
        requester_roles: list[str] | None = None,
        requester_tenant_id: uuid.UUID | None = None,
    ) -> User:
        """Replace the user's full role set with exactly *role_names* in one call.

        Roles already held are left untouched (junction row's ``assigned_at``/
        ``assigned_by`` preserved); roles no longer listed are revoked; newly
        listed roles are granted and stamped with ``assigned_by_id``. Mirrors
        :meth:`assign_role`'s privilege-escalation guard — only a
        ``super_admin`` requester may grant ``super_admin`` or ``admin``.

        The target user is resolved tenant-scoped — see :meth:`assign_role`.
        """
        user = self.get_by_id_scoped(user_id, requester_tenant_id)

        roles_by_name: dict[str, Role] = {}
        for name in role_names:
            role = self._uow.roles.get_by_name(name)
            if role is None:
                raise NotFoundError(MSG_USER_ROLE_NOT_FOUND.format(role_name=name))
            roles_by_name[name] = role

        current_names = {r.name for r in user.roles}
        newly_added = [name for name in role_names if name not in current_names]

        if (
            ROLE_SUPER_ADMIN in newly_added
            and requester_roles is not None
            and ROLE_SUPER_ADMIN not in requester_roles
        ):
            raise ForbiddenError(MSG_RBAC_SUPER_ADMIN_ROLE_FORBIDDEN)
        if (
            ROLE_ADMIN in newly_added
            and requester_roles is not None
            and ROLE_SUPER_ADMIN not in requester_roles
        ):
            raise ForbiddenError(MSG_RBAC_ADMIN_ROLE_FORBIDDEN)

        user.roles = [roles_by_name[name] for name in role_names]
        self._uow.add(user)
        self._uow.flush()

        if assigned_by_id is not None and newly_added:
            new_role_ids = [roles_by_name[name].id for name in newly_added]
            junctions = (
                self._uow.session.query(UserRole)
                .filter(UserRole.user_id == user.id, UserRole.role_id.in_(new_role_ids))
                .all()
            )
            for junction in junctions:
                junction.assigned_by = assigned_by_id
            self._uow.flush()

        self._uow.refresh(user)
        self._uow.commit()
        logger.info("Updated roles for user id=%s -> %s", user_id, role_names)
        return user
