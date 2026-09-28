"""Route handlers for /tenants/{tenant_id}/invitations — v1.

Endpoint summary
────────────────
Super-Admin or Client Admin (``super_admin`` or ``admin`` role):
    POST   /tenants/{tenant_id}/invitations                        — invite a
                                               user into the tenant. Each
                                               caller may only choose one
                                               fixed role: super_admin → any
                                               tenant, only as ``admin``
                                               (Client Admin) — cannot invite
                                               as ``member`` or any other
                                               role; ``role`` may be omitted
                                               and defaults to ``admin``,
                                               since it is the only role a
                                               super_admin may ever invite
                                               as. Client Admin → own tenant
                                               only, must explicitly set
                                               ``role`` to ``member`` — never
                                               ``admin`` or any other role.
    GET    /tenants/{tenant_id}/invitations                        — list this
                                               tenant's invitations, optionally
                                               filtered by status.
    POST   /tenants/{tenant_id}/invitations/{invitation_id}/resend — reissue a
                                               fresh token/expiry and re-send the
                                               invite email (e.g. the invitee
                                               never received or lost access to
                                               the original email).
    POST   /tenants/{tenant_id}/invitations/{invitation_id}/revoke — permanently
                                               invalidate a pending invitation
                                               (e.g. wrong email — invite the
                                               correct one afterwards).
                                               An invitation whose role is
                                               anything other than ``member``
                                               (``admin`` or any other role)
                                               may only be resent/revoked by a
                                               super_admin, mirroring who may
                                               create one.

Design rules
────────────
- Zero business logic here — all decisions live in InvitationService.
- Separate module from app/routes/v1/tenants.py (tenant CRUD + LLM provider
  config) so each file stays focused on one sub-resource; both are mounted
  under the same ``/tenants/{tenant_id}`` path space in app/router.py. Public,
  token-based invitation acceptance lives in a third file,
  app/routes/v1/invitations.py (no admin auth — the token is the credential).
- Annotated aliases declared once at module level; reused across handlers.
- Exception handling is centralised in app/core/exception_handlers.py.
"""

from __future__ import annotations

from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, Query, Request, status

from app.core.config import settings
from app.core.constants import ROLE_ADMIN, ROLE_MEMBER, ROLE_SUPER_ADMIN
from app.core.enums.invitation_status import InvitationStatus
from app.core.exceptions import ForbiddenError, ValidationError
from app.core.messages import (
    MSG_INVITATION_FORBIDDEN_ADMIN_ROLE,
    MSG_INVITATION_FORBIDDEN_OTHER_TENANT,
    MSG_INVITATION_RESENT,
    MSG_INVITATION_REVOKED,
    MSG_INVITATION_ROLE_FORBIDDEN_FOR_CLIENT_ADMIN,
    MSG_INVITATION_ROLE_FORBIDDEN_FOR_SUPER_ADMIN,
    MSG_INVITATION_ROLE_REQUIRED,
    MSG_INVITATION_SENT,
    SUMMARY_INVITATION_CREATE,
    SUMMARY_INVITATION_LIST,
    SUMMARY_INVITATION_RESEND,
    SUMMARY_INVITATION_REVOKE,
)
from app.core.rate_limiter import invitation_resend_limit, limiter
from app.db.unit_of_work import UnitOfWork
from app.deps import get_uow, require_roles
from app.models.postgres.invitation_model import Invitation
from app.models.postgres.user_model import User
from app.schemas.invitation_schema import (
    InvitationCreateRequest,
    InvitationListResponse,
    InvitationOut,
)
from app.services.auth_service import CognitoAuthService
from app.services.email_service import EmailService
from app.services.invitation_service import InvitationService
from app.utils.response import ApiResponse

router = APIRouter(prefix="/tenants/{tenant_id}/invitations", tags=["Invitations"])

# ── Annotated dependency aliases ────────────────────────────────────────────

CurrentUow = Annotated[UnitOfWork, Depends(get_uow)]
# A super_admin may invite into any tenant, only as admin (Client Admin); a
# Client Admin may invite into their own tenant only, and only as member —
# the tenant-scoping and granted-role decision happens in invite_user below.
# Each caller's role choice is mutually exclusive with the other's: creating
# a Client Admin is a platform-admin concern, while managing a tenant's own
# Members is that tenant's Client Admin's concern, never the platform
# admin's.
CurrentAdminOrSuperAdmin = Annotated[User, Depends(require_roles(ROLE_SUPER_ADMIN, ROLE_ADMIN))]

SkipQuery = Annotated[int, Query(ge=0)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]
InvitationStatusFilter = Annotated[
    InvitationStatus | None, Query(description="Filter by invitation status")
]

# The only role a Client Admin may invite (or resend/revoke an invitation)
# as — anything else (``admin``, ``super_admin``, or a dynamic role) is
# restricted to a super_admin caller.
_CLIENT_ADMIN_INVITABLE_ROLES = frozenset({ROLE_MEMBER})

# The only role a super_admin may invite as — creating a tenant's Members is
# that tenant's Client Admin's responsibility, not the platform admin's.
# Mirrors _CLIENT_ADMIN_INVITABLE_ROLES above.
_SUPER_ADMIN_INVITABLE_ROLES = frozenset({ROLE_ADMIN})


def _is_super_admin(user: User) -> bool:
    return any(role.name == ROLE_SUPER_ADMIN for role in user.roles)


def _check_tenant_scope(is_super_admin: bool, current_user: User, tenant_id: uuid.UUID) -> None:
    """Raise :class:`ForbiddenError` unless *current_user* may act on *tenant_id*.

    A super_admin may act on any tenant; a Client Admin only on their own.
    """
    if not is_super_admin and current_user.tenant_id != tenant_id:
        raise ForbiddenError(MSG_INVITATION_FORBIDDEN_OTHER_TENANT)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary=SUMMARY_INVITATION_CREATE,
)
async def invite_user(
    tenant_id: uuid.UUID,
    body: InvitationCreateRequest,
    uow: CurrentUow,
    current_user: CurrentAdminOrSuperAdmin,
) -> ApiResponse[InvitationOut]:
    """POST /tenants/{tenant_id}/invitations — invite a user into a tenant.

    Each caller's allowed role is a fixed, mutually exclusive single choice:
    a super_admin may invite into any tenant, only as ``admin`` (Client
    Admin) — never ``member`` or any other role; since ``admin`` is the only
    choice a super_admin ever has, omitting ``role`` in the body defaults to
    it. A Client Admin may only invite into their own tenant, and must
    explicitly choose ``role`` as ``member`` — never ``admin`` or any other
    role, and never inferred. Creating a tenant's Client Admin is a
    platform-admin concern; creating that tenant's own Members is the Client
    Admin's concern — neither caller may do the other's job.

    Validates the target role exists and the tenant/email are invitable
    *before* touching Cognito, so an invalid role name fails fast without
    wasting a real ``AdminCreateUser`` call.

    Creates the Cognito identity up-front (``AdminCreateUser``, message
    suppressed) and emails a custom invite link. No password is set at this
    step — the invitee chooses one when accepting via
    ``POST /invitations/{token}/accept``.
    """
    is_super_admin = _is_super_admin(current_user)
    _check_tenant_scope(is_super_admin, current_user, tenant_id)

    if is_super_admin:
        # admin is the only role a super_admin may ever invite as, so an
        # omitted role is unambiguous — default it instead of demanding the
        # caller spell out the one value they're allowed to pass.
        role_name = body.role or ROLE_ADMIN
        if role_name not in _SUPER_ADMIN_INVITABLE_ROLES:
            raise ForbiddenError(MSG_INVITATION_ROLE_FORBIDDEN_FOR_SUPER_ADMIN)
    else:
        if body.role is None:
            raise ValidationError(MSG_INVITATION_ROLE_REQUIRED)
        if body.role not in _CLIENT_ADMIN_INVITABLE_ROLES:
            raise ForbiddenError(MSG_INVITATION_ROLE_FORBIDDEN_FOR_CLIENT_ADMIN)
        role_name = body.role

    invitation_service = InvitationService(uow)
    invitation_service.assert_role_exists(role_name)
    tenant = invitation_service.validate_invite_target(tenant_id, body.email)

    cognito = CognitoAuthService()
    sub = await cognito.admin_get_user_sub(email=body.email)
    if sub is None:
        sub = await cognito.admin_invite_user(email=body.email, name=body.name)

    invitation, token = invitation_service.create(
        tenant_id=tenant_id,
        email=body.email,
        name=body.name,
        cognito_sub=sub,
        invited_by_id=current_user.id,
        role_name=role_name,
    )

    invite_link = f"{settings.FRONTEND_BASE_URL}/invitations/accept?token={token}"
    EmailService.send_invitation(
        recipient=body.email,
        tenant_name=tenant.name,
        invite_link=invite_link,
        role_name=role_name,
        invitee_name=body.name,
    )

    return ApiResponse.ok(
        data=InvitationOut.model_validate(invitation), message=MSG_INVITATION_SENT
    )


def _check_invitation_role_scope(is_super_admin: bool, invitation: Invitation) -> None:
    """Raise :class:`ForbiddenError` unless the caller may manage *invitation*.

    Only a super_admin may resend/revoke an invitation whose role is
    anything other than ``member`` (``admin`` or any other dynamic role) —
    mirroring the rule in :func:`invite_user` that only a super_admin may
    create one in the first place.
    """
    if not is_super_admin and invitation.role.name not in _CLIENT_ADMIN_INVITABLE_ROLES:
        raise ForbiddenError(MSG_INVITATION_FORBIDDEN_ADMIN_ROLE)


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_INVITATION_LIST,
)
def list_invitations(
    tenant_id: uuid.UUID,
    uow: CurrentUow,
    current_user: CurrentAdminOrSuperAdmin,
    status_filter: InvitationStatusFilter = None,
    skip: SkipQuery = 0,
    limit: LimitQuery = 20,
) -> ApiResponse[InvitationListResponse]:
    """GET /tenants/{tenant_id}/invitations — list this tenant's invitations.

    super_admin may list any tenant's invitations; a Client Admin only their
    own tenant's. Optionally filter by ``status`` (pending/accepted/revoked/
    expired) — e.g. ``?status=pending`` to find invitations that still need
    action (resend or revoke).
    """
    _check_tenant_scope(_is_super_admin(current_user), current_user, tenant_id)

    invitations, total = InvitationService(uow).list_for_tenant(
        tenant_id,
        status=status_filter.value if status_filter else None,
        skip=skip,
        limit=limit,
    )
    data = InvitationListResponse(
        items=[InvitationOut.model_validate(i) for i in invitations],
        total=total,
        skip=skip,
        limit=limit,
    )
    return ApiResponse.ok(data=data)


@router.post(
    "/{invitation_id}/resend",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_INVITATION_RESEND,
)
@limiter.limit(invitation_resend_limit)
async def resend_invitation(
    request: Request,
    tenant_id: uuid.UUID,
    invitation_id: uuid.UUID,
    uow: CurrentUow,
    current_user: CurrentAdminOrSuperAdmin,
) -> ApiResponse[InvitationOut]:
    """POST /tenants/{tenant_id}/invitations/{invitation_id}/resend — resend an invitation.

    Use this when the invitee never received or lost access to the original
    invite email (e.g. a Client Admin who can't reach their inbox). Reissues
    a fresh token and expiry on the *same* invitation record and re-sends the
    email — the old link stops working immediately since its token hash is
    overwritten. No new Cognito identity is created; the one from the
    original invite is reused.
    """
    is_super_admin = _is_super_admin(current_user)
    _check_tenant_scope(is_super_admin, current_user, tenant_id)

    invitation_service = InvitationService(uow)
    invitation = invitation_service.get_for_tenant(invitation_id, tenant_id)
    _check_invitation_role_scope(is_super_admin, invitation)

    invitation, token = invitation_service.resend(invitation)

    invite_link = f"{settings.FRONTEND_BASE_URL}/invitations/accept?token={token}"
    EmailService.send_invitation(
        recipient=invitation.email,
        tenant_name=invitation.tenant.name,
        invite_link=invite_link,
        role_name=invitation.role.name,
        invitee_name=invitation.name,
    )

    return ApiResponse.ok(
        data=InvitationOut.model_validate(invitation), message=MSG_INVITATION_RESENT
    )


@router.post(
    "/{invitation_id}/revoke",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_INVITATION_REVOKE,
)
def revoke_invitation(
    tenant_id: uuid.UUID,
    invitation_id: uuid.UUID,
    uow: CurrentUow,
    current_user: CurrentAdminOrSuperAdmin,
) -> ApiResponse[InvitationOut]:
    """POST /tenants/{tenant_id}/invitations/{invitation_id}/revoke — permanently invalidate an invitation.

    Use this when the invite itself was wrong (e.g. a typo'd email) rather
    than merely undelivered — after revoking, the same tenant+email pair is
    free to be re-invited from scratch via ``POST /invitations``. To simply
    re-send the same invite, use ``resend`` instead; it doesn't require a
    revoke first.
    """
    is_super_admin = _is_super_admin(current_user)
    _check_tenant_scope(is_super_admin, current_user, tenant_id)

    invitation_service = InvitationService(uow)
    invitation = invitation_service.get_for_tenant(invitation_id, tenant_id)
    _check_invitation_role_scope(is_super_admin, invitation)

    invitation = invitation_service.revoke(invitation)
    return ApiResponse.ok(
        data=InvitationOut.model_validate(invitation), message=MSG_INVITATION_REVOKED
    )
