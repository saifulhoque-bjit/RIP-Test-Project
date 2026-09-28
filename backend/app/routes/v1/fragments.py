"""Route handlers for fragment resources — v1.

Endpoint summary
────────────────
Any authenticated user:
    GET   /projects/{project_id}/fragments                         — list fragments for a project
    GET   /projects/{project_id}/fragments/{fragment_id}           — get a single fragment
    PATCH /sources/{source_id}/fragments/{fragment_id}/bbox        — update bounding box

Design rules
────────────
- Zero business logic here — all decisions live in FragmentService.
- Dependencies resolved via Depends() in deps.py.
- Annotated aliases declared once at module level; reused across handlers.
- Exception handling is centralised in app/core/exception_handlers.py.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.core.messages import (
    MSG_FRAGMENT_BBOX_UPDATED,
    MSG_FRAGMENT_FETCHED,
    MSG_FRAGMENTS_LISTED,
    SUMMARY_FRAGMENT_GET,
    SUMMARY_FRAGMENT_LIST_BY_PROJECT,
    SUMMARY_FRAGMENT_UPDATE_BBOX,
)
from app.db.unit_of_work import UnitOfWork
from app.deps import get_current_db_user, get_uow
from app.models.postgres.user_model import User
from app.schemas.fragment_schema import (
    CreateSingleFragmentResponse,
    ListFragmentsByProjectResponse,
    UpdateFragmentBBoxRequest,
    UpdateFragmentBBoxResponse,
)
from app.services.fragment_service import FragmentService
from app.utils.pagination import PaginationParams
from app.utils.response import ApiResponse

router = APIRouter(tags=["Fragments"])

# ── Annotated dependency aliases ────────────────────────────────────────────

CurrentUser = Annotated[User, Depends(get_current_db_user)]
CurrentUow = Annotated[UnitOfWork, Depends(get_uow)]
CurrentPaging = Annotated[PaginationParams, Depends(PaginationParams)]

SourceIdFilter = Annotated[UUID | None, Query(description="Filter by source ID")]
FragTypeFilter = Annotated[str | None, Query(description="Filter by fragment type")]
ContentFilter = Annotated[
    str | None, Query(description="Filter by content (case-insensitive substring match)")
]


# ── Handlers ────────────────────────────────────────────────────────────────


@router.get(
    "/projects/{project_id}/fragments",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_FRAGMENT_LIST_BY_PROJECT,
)
async def list_fragments_by_project(
    project_id: UUID,
    uow: CurrentUow,
    _current_user: CurrentUser,
    pagination: CurrentPaging,
    source_id: SourceIdFilter = None,
    frag_type: FragTypeFilter = None,
    content: ContentFilter = None,
) -> ApiResponse[ListFragmentsByProjectResponse]:
    """GET /projects/{project_id}/fragments — list fragments for a project."""
    result = await FragmentService().list_fragments_by_project(
        project_id=project_id,
        source_id=source_id,
        frag_type=frag_type,
        content=content,
        skip=pagination.skip,
        limit=pagination.limit,
        uow=uow,
    )
    return ApiResponse.ok(data=result, message=MSG_FRAGMENTS_LISTED)


@router.get(
    "/projects/{project_id}/fragments/{fragment_id}",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_FRAGMENT_GET,
)
async def get_fragment(
    project_id: UUID,
    fragment_id: str,
    uow: CurrentUow,
    _current_user: CurrentUser,
) -> ApiResponse[CreateSingleFragmentResponse]:
    """GET /projects/{project_id}/fragments/{fragment_id} — fetch a single fragment."""
    result = await FragmentService().get_fragment_by_project(
        project_id=project_id,
        fragment_id=fragment_id,
        uow=uow,
    )
    return ApiResponse.ok(data=result, message=MSG_FRAGMENT_FETCHED)


@router.patch(
    "/sources/{source_id}/fragments/{fragment_id}/bbox",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_FRAGMENT_UPDATE_BBOX,
)
async def update_fragment_bbox(
    source_id: UUID,
    fragment_id: str,
    request: UpdateFragmentBBoxRequest,
    uow: CurrentUow,
    _current_user: CurrentUser,
) -> ApiResponse[UpdateFragmentBBoxResponse]:
    """PATCH /sources/{source_id}/fragments/{fragment_id}/bbox — update bounding box."""
    result = await FragmentService().update_fragment_bbox(
        source_id=source_id,
        fragment_id=fragment_id,
        request=request,
        uow=uow,
    )
    return ApiResponse.ok(data=result, message=MSG_FRAGMENT_BBOX_UPDATED)
