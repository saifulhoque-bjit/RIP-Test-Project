"""Route handlers for /updates — v1.

Endpoint summary
────────────────
Any authenticated user:
    POST /projects/{project_id}/updates/accept — accept a pending change,
         whether it came from the incremental-source-update flow or the
         feedback-driven regeneration flow
    POST /projects/{project_id}/updates/reject — reject one

Design rules
────────────
- Zero business logic here — all decisions (including which underlying flow
  owns the change) live in UpdatesService.
- Annotated aliases declared once at module level; reused across handlers.
- Exception handling is centralised in app/core/exception_handlers.py.
- A single unified entry point over IncrementalUpdatesService
  (incremental_updates.py, /incremental-updates/accept|reject) and
  FeedbackUpdateService (feedback_updates.py, /feedback-updates/accept|reject) —
  those two endpoints remain available for callers that already know which
  flow they're targeting.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.core.messages import (
    MSG_UPDATES_ACCEPTED,
    MSG_UPDATES_REJECTED,
    SUMMARY_UNIFIED_UPDATES_ACCEPT,
    SUMMARY_UNIFIED_UPDATES_REJECT,
)
from app.db.unit_of_work import UnitOfWork
from app.deps import get_current_db_user, get_uow, require_project_access
from app.models.postgres.project_model import Project
from app.models.postgres.user_model import User
from app.schemas.incremental_updates_schema import (
    UpdateAcceptRequest,
    UpdateDecisionResponse,
    UpdateRejectRequest,
)
from app.services.updates_service import UpdatesService
from app.utils.response import ApiResponse

router = APIRouter(tags=["Incremental Updates and Feedback"])

# ── Annotated dependency aliases ────────────────────────────────────────────

CurrentUser = Annotated[User, Depends(get_current_db_user)]
CurrentUow = Annotated[UnitOfWork, Depends(get_uow)]
WriteAccess = Annotated[Project, Depends(require_project_access("write"))]


@router.post(
    "/projects/{project_id}/updates/accept",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_UNIFIED_UPDATES_ACCEPT,
)
async def accept_update(
    project_id: UUID,
    payload: UpdateAcceptRequest,
    uow: CurrentUow,
    current_user: CurrentUser,
    _project: WriteAccess,
) -> ApiResponse[UpdateDecisionResponse]:
    """POST /projects/{project_id}/updates/accept — accept a pending change.

    Dispatches to IncrementalUpdatesService or FeedbackUpdateService
    depending on which of ``incremental_change_type``/``feedback_change_type``
    is set on the target entity.
    """
    result = await UpdatesService().accept_update(
        project_id=project_id,
        payload=payload,
        actor_user_id=current_user.id,
        uow=uow,
    )
    return ApiResponse.ok(data=result, message=MSG_UPDATES_ACCEPTED)


@router.post(
    "/projects/{project_id}/updates/reject",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_UNIFIED_UPDATES_REJECT,
)
async def reject_update(
    project_id: UUID,
    payload: UpdateRejectRequest,
    uow: CurrentUow,
    current_user: CurrentUser,
    _project: WriteAccess,
) -> ApiResponse[UpdateDecisionResponse]:
    """POST /projects/{project_id}/updates/reject — reject a pending change.

    Dispatches to IncrementalUpdatesService or FeedbackUpdateService
    depending on which of ``incremental_change_type``/``feedback_change_type``
    is set on the target entity.
    """
    result = await UpdatesService().reject_update(
        project_id=project_id,
        payload=payload,
        actor_user_id=current_user.id,
        uow=uow,
    )
    return ApiResponse.ok(data=result, message=MSG_UPDATES_REJECTED)
