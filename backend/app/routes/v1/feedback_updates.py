"""Route handlers for /feedback-updates — v1.

Endpoint summary
────────────────
Any authenticated user:
    POST /projects/{project_id}/feedback-updates/accept — accept a pending
         feedback-driven regeneration change (from ``/modules/regenerate`` or
         ``/user-stories/regenerate-by-feedback``)
    POST /projects/{project_id}/feedback-updates/reject — reject one

Design rules
────────────
- Zero business logic here — all decisions live in FeedbackUpdateService.
- Annotated aliases declared once at module level; reused across handlers.
- Exception handling is centralised in app/core/exception_handlers.py.
- Distinct from /incremental-updates/accept /incremental-updates/reject
  (incremental_updates.py), which reviews the separate incremental-source-update flow.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.core.messages import (
    MSG_FEEDBACK_UPDATES_ACCEPTED,
    MSG_FEEDBACK_UPDATES_REJECTED,
    SUMMARY_FEEDBACK_UPDATES_ACCEPT,
    SUMMARY_FEEDBACK_UPDATES_REJECT,
)
from app.db.unit_of_work import UnitOfWork
from app.deps import get_current_db_user, get_uow, require_project_access
from app.models.postgres.project_model import Project
from app.models.postgres.user_model import User
from app.schemas.feedback_updates_schema import (
    FeedbackUpdateAcceptRequest,
    FeedbackUpdateDecisionResponse,
    FeedbackUpdateRejectRequest,
)
from app.services.feedback_update_service import FeedbackUpdateService
from app.utils.response import ApiResponse

router = APIRouter(tags=["Incremental Updates and Feedback"])

# ── Annotated dependency aliases ────────────────────────────────────────────

CurrentUser = Annotated[User, Depends(get_current_db_user)]
CurrentUow = Annotated[UnitOfWork, Depends(get_uow)]
WriteAccess = Annotated[Project, Depends(require_project_access("write"))]


@router.post(
    "/projects/{project_id}/feedback-updates/accept",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_FEEDBACK_UPDATES_ACCEPT,
)
async def accept_feedback_update(
    project_id: UUID,
    payload: FeedbackUpdateAcceptRequest,
    uow: CurrentUow,
    current_user: CurrentUser,
    _project: WriteAccess,
) -> ApiResponse[FeedbackUpdateDecisionResponse]:
    """POST /projects/{project_id}/feedback-updates/accept — accept a pending
    feedback-driven regeneration change.

    ADDED/UPDATED entities: status is set to ``approved`` and
    ``feedback_change_type`` is cleared.
    """
    result = await FeedbackUpdateService().accept_feedback_update(
        project_id=project_id,
        payload=payload,
        actor_user_id=current_user.id,
        uow=uow,
    )
    return ApiResponse.ok(data=result, message=MSG_FEEDBACK_UPDATES_ACCEPTED)


@router.post(
    "/projects/{project_id}/feedback-updates/reject",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_FEEDBACK_UPDATES_REJECT,
)
async def reject_feedback_update(
    project_id: UUID,
    payload: FeedbackUpdateRejectRequest,
    uow: CurrentUow,
    current_user: CurrentUser,
    _project: WriteAccess,
) -> ApiResponse[FeedbackUpdateDecisionResponse]:
    """POST /projects/{project_id}/feedback-updates/reject — reject a pending
    feedback-driven regeneration change.

    ADDED entities: the node is hard-deleted, since there is no prior state
    to roll back to. UPDATED entities: content is restored from the most
    recent Module/Feature version snapshot and ``feedback_change_type`` is
    cleared.
    """
    result = await FeedbackUpdateService().reject_feedback_update(
        project_id=project_id,
        payload=payload,
        actor_user_id=current_user.id,
        uow=uow,
    )
    return ApiResponse.ok(data=result, message=MSG_FEEDBACK_UPDATES_REJECTED)
