"""Route handlers for the /notifications resource — v1.

Endpoint summary
────────────────
Any authenticated user:
    GET   /notifications                           — list own notifications (paginated)
    GET   /notifications/unread-count              — bell-icon badge counter
    PATCH /notifications/read-all                  — mark all as read
    PATCH /notifications/{notification_id}/read    — mark one as read

Design rules
────────────
- Zero business logic in this file — all decisions live in NotificationService.
- Annotated dependency aliases declared once at module level; reused across handlers.
- Exception handling is centralised in app/core/exception_handlers.py.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.core.enums.notification_type import NotificationType
from app.core.messages import (
    DESC_NOTIFICATION_FILTER_IS_READ,
    DESC_NOTIFICATION_FILTER_TYPE,
    MSG_NOTIFICATION_ALL_MARKED_READ,
    MSG_NOTIFICATION_MARKED_READ,
    SUMMARY_NOTIFICATION_LIST,
    SUMMARY_NOTIFICATION_MARK_ALL_READ,
    SUMMARY_NOTIFICATION_MARK_READ,
    SUMMARY_NOTIFICATION_UNREAD_COUNT,
)
from app.db.unit_of_work import UnitOfWork
from app.deps import get_current_db_user, get_uow
from app.models.postgres.user_model import User
from app.schemas.notification_schema import (
    NotificationListResponse,
    NotificationResponse,
    NotificationUnreadCountResponse,
)
from app.services.notification_service import NotificationService
from app.utils.pagination import PaginationParams
from app.utils.response import ApiResponse

router = APIRouter(prefix="/notifications", tags=["Notifications"])


# ── Annotated dependency aliases ───────────────────────────────────────────

CurrentUser = Annotated[User, Depends(get_current_db_user)]
CurrentUow = Annotated[UnitOfWork, Depends(get_uow)]
CurrentPagination = Annotated[PaginationParams, Depends(PaginationParams)]
CurrentService = Annotated[NotificationService, Depends(NotificationService)]

IsReadFilter = Annotated[bool | None, Query(description=DESC_NOTIFICATION_FILTER_IS_READ)]
TypeFilter = Annotated[NotificationType | None, Query(description=DESC_NOTIFICATION_FILTER_TYPE)]


# ── Endpoints ──────────────────────────────────────────────────────────────


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_NOTIFICATION_LIST,
)
def list_notifications(
    current_user: CurrentUser,
    uow: CurrentUow,
    pagination: CurrentPagination,
    service: CurrentService,
    is_read: IsReadFilter = None,
    notification_type: TypeFilter = None,
) -> ApiResponse[NotificationListResponse]:
    """GET /notifications — return the authenticated user's notification feed."""
    result = service.list_notifications(
        current_user.id,
        skip=pagination.skip,
        limit=pagination.limit,
        is_read=is_read,
        notification_type=notification_type,
        uow=uow,
    )
    return ApiResponse.ok(data=result)


@router.get(
    "/unread-count",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_NOTIFICATION_UNREAD_COUNT,
)
def get_unread_count(
    current_user: CurrentUser,
    uow: CurrentUow,
    service: CurrentService,
) -> ApiResponse[NotificationUnreadCountResponse]:
    """GET /notifications/unread-count — return the bell-icon badge counter."""
    result = service.get_unread_count(current_user.id, uow=uow)
    return ApiResponse.ok(data=result)


@router.patch(
    "/read-all",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_NOTIFICATION_MARK_ALL_READ,
)
def mark_all_read(
    current_user: CurrentUser,
    uow: CurrentUow,
    service: CurrentService,
) -> ApiResponse[NotificationUnreadCountResponse]:
    """PATCH /notifications/read-all — mark every unread notification as read."""
    result = service.mark_all_read(current_user.id, uow=uow)
    return ApiResponse.ok(data=result, message=MSG_NOTIFICATION_ALL_MARKED_READ)


@router.patch(
    "/{notification_id}/read",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_NOTIFICATION_MARK_READ,
)
def mark_as_read(
    notification_id: UUID,
    current_user: CurrentUser,
    uow: CurrentUow,
    service: CurrentService,
) -> ApiResponse[NotificationResponse]:
    """PATCH /notifications/{notification_id}/read — mark a single notification as read."""
    result = service.mark_as_read(current_user.id, notification_id, uow=uow)
    return ApiResponse.ok(data=result, message=MSG_NOTIFICATION_MARKED_READ)
