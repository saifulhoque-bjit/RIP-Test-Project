"""Route handlers for /projects/{project_id}/activity-logs — v1.

Endpoint summary
────────────────
Any authenticated project member:
    GET /projects/{project_id}/activity-logs — paginated project activity feed

Design rules
────────────
- Zero business logic here — all decisions live in ActivityLogService.
- Annotated aliases declared once at module level; reused across handlers.
- Exception handling is centralised in app/core/exception_handlers.py.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.core.enums.activity_type import ActivityType
from app.core.messages import (
    DESC_ACTIVITY_LOG_FILTER_TYPE,
    MSG_ACTIVITY_LOG_LIST_FETCHED,
    SUMMARY_ACTIVITY_LOG_LIST,
)
from app.db.unit_of_work import UnitOfWork
from app.deps import get_current_db_user, get_uow, require_project_access
from app.models.postgres.project_model import Project
from app.models.postgres.user_model import User
from app.schemas.activity_log_schema import ActivityLogListResponse
from app.services.activity_log_service import ActivityLogService
from app.utils.pagination import PaginationParams
from app.utils.response import ApiResponse

router = APIRouter(tags=["Activity Logs"])

# ── Annotated dependency aliases ────────────────────────────────────────────

CurrentUser = Annotated[User, Depends(get_current_db_user)]
CurrentUow = Annotated[UnitOfWork, Depends(get_uow)]
CurrentPagination = Annotated[PaginationParams, Depends(PaginationParams)]
CurrentService = Annotated[ActivityLogService, Depends(ActivityLogService)]
ReadAccess = Annotated[Project, Depends(require_project_access("read"))]

TypeFilter = Annotated[ActivityType | None, Query(description=DESC_ACTIVITY_LOG_FILTER_TYPE)]


@router.get(
    "/projects/{project_id}/activity-logs",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_ACTIVITY_LOG_LIST,
)
def list_activity_logs(
    project_id: UUID,
    uow: CurrentUow,
    pagination: CurrentPagination,
    service: CurrentService,
    _current_user: CurrentUser,
    _project: ReadAccess,
    activity_type: TypeFilter = None,
) -> ApiResponse[ActivityLogListResponse]:
    """GET /projects/{project_id}/activity-logs — paginated project activity feed."""
    result = service.list_activities(
        project_id,
        skip=pagination.skip,
        limit=pagination.limit,
        activity_type=activity_type,
        uow=uow,
    )
    return ApiResponse.ok(data=result, message=MSG_ACTIVITY_LOG_LIST_FETCHED)
