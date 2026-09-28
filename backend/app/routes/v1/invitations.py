"""Route handlers for /invitations — v1.

Public (no authentication — the invite token itself is the credential):
    GET  /invitations/{token}          — validate a token, return prefill details
    POST /invitations/{token}/accept   — set a password and finalize the account

Design rules
────────────
- Zero business logic here — all decisions live in InvitationService /
  CognitoAuthService.
- Both endpoints return the same generic "invalid or expired" error for any
  missing/expired/already-used token, to avoid leaking which case applies to
  an unauthenticated caller.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.core.messages import (
    MSG_INVITATION_ACCEPTED,
    SUMMARY_INVITATION_ACCEPT,
    SUMMARY_INVITATION_VALIDATE,
)
from app.db.unit_of_work import UnitOfWork
from app.deps import get_uow
from app.schemas.invitation_schema import InvitationAcceptRequest, InvitationPublicOut
from app.services.auth_service import CognitoAuthService
from app.services.invitation_service import InvitationService
from app.utils.response import ApiResponse

router = APIRouter(prefix="/invitations", tags=["Invitations"])

CurrentUow = Annotated[UnitOfWork, Depends(get_uow)]


@router.get(
    "/{token}",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_INVITATION_VALIDATE,
)
def validate_invitation(token: str, uow: CurrentUow) -> ApiResponse[InvitationPublicOut]:
    """GET /invitations/{token} — validate a token and return prefill details."""
    invitation = InvitationService(uow).get_valid_by_token(token)
    data = InvitationPublicOut(
        email=invitation.email,
        tenant_name=invitation.tenant.name,
        expires_at=invitation.expires_at,
    )
    return ApiResponse.ok(data=data)


@router.post(
    "/{token}/accept",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_INVITATION_ACCEPT,
)
async def accept_invitation(
    token: str, body: InvitationAcceptRequest, uow: CurrentUow
) -> ApiResponse[None]:
    """POST /invitations/{token}/accept — set a password and finalize the account.

    Does not return session tokens — the invitee logs in separately via the
    existing ``POST /auth/login`` once their account is active.
    """
    invitation_service = InvitationService(uow)
    invitation = invitation_service.get_valid_by_token(token)
    await CognitoAuthService().admin_set_user_password(
        email=invitation.email, password=body.password, permanent=True
    )
    invitation_service.accept(invitation)

    return ApiResponse.ok(message=MSG_INVITATION_ACCEPTED)
