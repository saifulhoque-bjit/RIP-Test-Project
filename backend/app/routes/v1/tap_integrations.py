"""Routes for TAP (Test Automation Platform) per-project integration config and sync."""

from __future__ import annotations

import hmac
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, status

from app.core.config import settings
from app.core.exceptions import ForbiddenError, ServiceUnavailableError
from app.core.messages import (
    MSG_TAP_ACK_RECEIVED,
    MSG_TAP_ACK_SECRET_MISSING,
    MSG_TAP_ACK_UNAUTHORIZED,
    MSG_TAP_INTEGRATION_CREATED,
    MSG_TAP_INTEGRATION_UPDATED,
    SUMMARY_TAP_ACK_RECEIVE,
    SUMMARY_TAP_INTEGRATION_CREATE,
    SUMMARY_TAP_INTEGRATION_GET,
    SUMMARY_TAP_INTEGRATION_UPDATE,
    SUMMARY_TAP_SYNC_DATA,
    SUMMARY_TAP_SYNC_EXECUTE,
)
from app.core.rate_limiter import limiter, tap_config_limit, tap_sync_limit
from app.db.unit_of_work import UnitOfWork
from app.deps import get_current_db_user, get_tap_integration_service, get_tap_sync_service, get_uow
from app.models.postgres.user_model import User
from app.schemas.tap_integration_schema import (
    TapAckRequest,
    TapAckResponse,
    TapIntegrationCreate,
    TapIntegrationResponse,
    TapIntegrationUpdate,
    TapSyncDataResponse,
    TapSyncExecuteRequest,
    TapSyncExecuteResponse,
)
from app.services.tap_integration_service import TapIntegrationService
from app.services.tap_sync_service import TapSyncService
from app.utils.logger import get_logger
from app.utils.response import ApiResponse

logger = get_logger(__name__)


def verify_tap_ack_signature(
    x_tap_signature: Annotated[str | None, Header()] = None,
) -> None:
    """Guard the inbound TAP-facing endpoints with a shared secret.

    These two endpoints are not user-authenticated: one serves a project's
    entire staged requirements hierarchy, the other flips entities to synced.
    A missing ``TAP_ACK_SECRET`` therefore fails **closed**, not open — an
    unset secret is a deployment mistake, and treating it as "no auth
    required" would silently expose both to anyone who can reach the API.

    Note this compares a static shared secret, so it authenticates the caller
    but does not bind the request body; a captured header can be replayed.
    Signing the payload requires TAP-side support.
    """
    expected = settings.TAP_ACK_SECRET
    if not expected:
        logger.error(
            "TAP_ACK_SECRET is not set — refusing inbound TAP callbacks. Set it on this "
            "deployment (and on TAP's side) before TAP can pull sync data or send acks."
        )
        raise ServiceUnavailableError(MSG_TAP_ACK_SECRET_MISSING)
    if not x_tap_signature or not hmac.compare_digest(x_tap_signature, expected):
        raise ForbiddenError(MSG_TAP_ACK_UNAUTHORIZED)


router = APIRouter(
    prefix="/projects/{project_id}/integrations/tap",
    tags=["TAP Integration"],
)

# ── Dependency aliases ─────────────────────────────────────────────────────

CurrentUser = Annotated[User, Depends(get_current_db_user)]
CurrentUow = Annotated[UnitOfWork, Depends(get_uow)]
SyncService = Annotated[TapSyncService, Depends(get_tap_sync_service)]
IntegrationService = Annotated[TapIntegrationService, Depends(get_tap_integration_service)]


# ── Config CRUD ────────────────────────────────────────────────────────────


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary=SUMMARY_TAP_INTEGRATION_CREATE,
)
@limiter.limit(tap_config_limit)
async def create_integration(
    request: Request,
    project_id: UUID,
    payload: TapIntegrationCreate,
    current_user: CurrentUser,
    uow: CurrentUow,
    service: IntegrationService,
) -> ApiResponse[TapIntegrationResponse]:
    result = await service.create_integration(
        project_id=project_id,
        payload=payload,
        uow=uow,
        user_id=current_user.id,
    )
    return ApiResponse.ok(data=result, message=MSG_TAP_INTEGRATION_CREATED)


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_TAP_INTEGRATION_GET,
)
def get_config(
    project_id: UUID,
    current_user: CurrentUser,
    uow: CurrentUow,
    service: IntegrationService,
) -> ApiResponse[TapIntegrationResponse]:
    result = service.get_integration(project_id=project_id, uow=uow)
    return ApiResponse.ok(data=result)


@router.patch(
    "",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_TAP_INTEGRATION_UPDATE,
)
@limiter.limit(tap_config_limit)
async def update_integration(
    request: Request,
    project_id: UUID,
    payload: TapIntegrationUpdate,
    current_user: CurrentUser,
    uow: CurrentUow,
    service: IntegrationService,
) -> ApiResponse[TapIntegrationResponse]:
    """Edit an existing integration.

    Any auth-relevant change is re-verified against TAP before it is stored,
    using the newly supplied key or — when the user did not retype it — the
    stored one, so a config can never be saved into a "connected" state
    without TAP having accepted it.
    """
    result = await service.update_integration(
        project_id=project_id,
        payload=payload,
        uow=uow,
    )
    return ApiResponse.ok(data=result, message=MSG_TAP_INTEGRATION_UPDATED)


# ── Sync (stage RIP hierarchy + notify TAP to pull) ─────────────────────────


@router.post(
    "/sync",
    status_code=status.HTTP_201_CREATED,
    summary=SUMMARY_TAP_SYNC_EXECUTE,
)
@limiter.limit(tap_sync_limit)
async def sync_execute(
    request: Request,
    project_id: UUID,
    payload: TapSyncExecuteRequest,
    current_user: CurrentUser,
    uow: CurrentUow,
    sync_service: SyncService,
) -> ApiResponse[TapSyncExecuteResponse]:
    result = await sync_service.execute_sync_with_hierarchy(
        project_id=project_id,
        modules=payload.modules,
        triggered_by_id=current_user.id,
        uow=uow,
    )
    return ApiResponse.ok(data=result, message=result.message)


# ── Pull (inbound TAP → RIP) ─────────────────────────────────────────────────


@router.get(
    "/sync/{sync_id}/data",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_TAP_SYNC_DATA,
    dependencies=[Depends(verify_tap_ack_signature)],
)
def get_sync_data(
    project_id: UUID,
    sync_id: UUID,
    uow: CurrentUow,
    sync_service: SyncService,
) -> ApiResponse[TapSyncDataResponse]:
    """Inbound callback TAP invokes (via the ``pull_url`` it was given) to
    fetch the staged hierarchy for a ``sync_id`` whenever it is ready.

    Not user-authenticated — guarded by the ``X-TAP-Signature`` shared secret,
    same as ``/ack``.
    """
    result = sync_service.get_sync_payload(project_id=project_id, sync_id=sync_id, uow=uow)
    return ApiResponse.ok(data=result)


# ── Acknowledgement (inbound TAP → RIP) ─────────────────────────────────────


@router.post(
    "/sync/{sync_id}/ack",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_TAP_ACK_RECEIVE,
    dependencies=[Depends(verify_tap_ack_signature)],
)
async def receive_ack(
    project_id: UUID,
    sync_id: UUID,
    payload: TapAckRequest,
    uow: CurrentUow,
    sync_service: SyncService,
) -> ApiResponse[TapAckResponse]:
    """Inbound callback TAP invokes to report a sync job's final status.

    This is **not** a user-authenticated endpoint — TAP is the caller, guarded
    by the ``X-TAP-Signature`` shared secret. ``sync_id`` (path) correlates
    this ack back to the staged sync run. When ``status == "COMPLETED"``,
    every entity staged as part of the acknowledged sync run has
    ``is_tap_synced=true`` flipped in Neo4j (updating the UI's
    ``tap_synced_count``); ``"FAILED"`` flips nothing and is recorded purely
    for audit.
    """
    result = await sync_service.handle_ack(
        project_id=project_id, sync_id=sync_id, request=payload, uow=uow
    )
    return ApiResponse.ok(
        data=result,
        message=MSG_TAP_ACK_RECEIVED.format(job_id=result.job_id, status=result.status),
    )
