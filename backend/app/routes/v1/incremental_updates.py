"""Route handlers for /incremental-updates and /updates — v1.

Endpoint summary
────────────────
Any authenticated user:
    GET  /projects/{project_id}/incremental-updates/latest — merged review tree
    GET  /projects/{project_id}/updates/list — changed-only tree
    POST /projects/{project_id}/incremental-updates/accept — accept a pending change
    POST /projects/{project_id}/incremental-updates/reject — reject a pending change

Design rules
────────────
- Zero business logic here — all decisions live in IncrementalUpdatesService.
- Annotated aliases declared once at module level; reused across handlers.
- Exception handling is centralised in app/core/exception_handlers.py.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.core.messages import (
    MSG_INCREMENTAL_UPDATES_LIST_FETCHED,
    MSG_INCREMENTAL_UPDATES_TREE_FETCHED,
    MSG_UPDATES_ACCEPTED,
    MSG_UPDATES_REJECTED,
    SUMMARY_INCREMENTAL_UPDATES_LIST,
    SUMMARY_INCREMENTAL_UPDATES_TREE,
    SUMMARY_UPDATES_ACCEPT,
    SUMMARY_UPDATES_REJECT,
)
from app.db.unit_of_work import UnitOfWork
from app.deps import get_current_db_user, get_uow, require_project_access
from app.models.postgres.project_model import Project
from app.models.postgres.user_model import User
from app.schemas.incremental_updates_schema import (
    IncrementalUpdatesListResponse,
    IncrementalUpdatesTreeResponse,
    UpdateAcceptRequest,
    UpdateDecisionResponse,
    UpdateRejectRequest,
)
from app.services.incremental_updates_service import IncrementalUpdatesService
from app.utils.response import ApiResponse

router = APIRouter(tags=["Incremental Updates and Feedback"])

# ── Annotated dependency aliases ────────────────────────────────────────────

CurrentUser = Annotated[User, Depends(get_current_db_user)]
CurrentUow = Annotated[UnitOfWork, Depends(get_uow)]
ReadAccess = Annotated[Project, Depends(require_project_access("read"))]
WriteAccess = Annotated[Project, Depends(require_project_access("write"))]


@router.get(
    "/projects/{project_id}/incremental-updates/latest",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_INCREMENTAL_UPDATES_TREE,
)
async def get_latest_incremental_updates_tree(
    project_id: UUID,
    uow: CurrentUow,
    _current_user: CurrentUser,
    _project: ReadAccess,
) -> ApiResponse[IncrementalUpdatesTreeResponse]:
    """GET /projects/{project_id}/incremental-updates/latest — merged review tree."""
    result = await IncrementalUpdatesService().get_latest_incremental_tree(
        project_id=project_id,
        uow=uow,
    )
    return ApiResponse.ok(data=result, message=MSG_INCREMENTAL_UPDATES_TREE_FETCHED)


@router.get(
    "/projects/{project_id}/updates/list",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_INCREMENTAL_UPDATES_LIST,
)
async def list_updates(
    project_id: UUID,
    uow: CurrentUow,
    _current_user: CurrentUser,
    _project: ReadAccess,
) -> ApiResponse[IncrementalUpdatesListResponse]:
    """GET /projects/{project_id}/updates/list — changed-only tree.

    Returns only modules/features/user stories with a non-null
    ``incremental_change_type``, plus any ancestor needed to keep a changed
    descendant's parent linkage intact.
    """
    result = await IncrementalUpdatesService().get_incremental_updates_list(
        project_id=project_id,
        uow=uow,
    )
    return ApiResponse.ok(data=result, message=MSG_INCREMENTAL_UPDATES_LIST_FETCHED)


@router.post(
    "/projects/{project_id}/incremental-updates/accept",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_UPDATES_ACCEPT,
)
async def accept_update(
    project_id: UUID,
    payload: UpdateAcceptRequest,
    uow: CurrentUow,
    current_user: CurrentUser,
    _project: WriteAccess,
) -> ApiResponse[UpdateDecisionResponse]:
    """POST /projects/{project_id}/incremental-updates/accept — accept a pending incremental change.

    ADDED/UPDATED entities: status is set to ``approved`` and both
    ``text_diffs``/``incremental_change_type`` are cleared.
    DELETE_SUGGESTED user stories: the node is marked deleted and marked as no
    longer synchronized to TAP.
    """
    result = await IncrementalUpdatesService().accept_update(
        project_id=project_id,
        payload=payload,
        actor_user_id=current_user.id,
        uow=uow,
    )
    return ApiResponse.ok(data=result, message=MSG_UPDATES_ACCEPTED)


@router.post(
    "/projects/{project_id}/incremental-updates/reject",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_UPDATES_REJECT,
)
async def reject_update(
    project_id: UUID,
    payload: UpdateRejectRequest,
    uow: CurrentUow,
    current_user: CurrentUser,
    _project: WriteAccess,
) -> ApiResponse[UpdateDecisionResponse]:
    """POST /projects/{project_id}/incremental-updates/reject — reject a pending incremental change.

    UPDATED/DELETE_SUGGESTED entities: content is restored from the most
    recent Module/Feature/UserStory version snapshot (or, for
    DELETE_SUGGESTED, simply un-flagged, since its content was never
    changed), and both ``text_diffs``/``incremental_change_type`` are cleared.
    ADDED entities: the node is hard-deleted, since there is no prior state
    to roll back to.
    """
    result = await IncrementalUpdatesService().reject_update(
        project_id=project_id,
        payload=payload,
        actor_user_id=current_user.id,
        uow=uow,
    )
    return ApiResponse.ok(data=result, message=MSG_UPDATES_REJECTED)
