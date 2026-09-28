"""Route handlers for ingestion-pipeline tracking — v1.

Endpoint summary
────────────────
Any authenticated user:
    GET /projects/me/pipelines — list source-ingestion pipelines across the current user's own projects

A "pipeline" is a SourceIngestion — one row per uploaded batch, carrying a
human-readable `run_code` (e.g. "RUN-1001"), which of the module_feature /
user_story generation stages have touched it, its `source_type`, and a
lifecycle `status`: running -> ready_for_review -> completed (or failed).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.enums.source_ingestion_status import SourceIngestionStatus
from app.core.enums.source_type import SourceType
from app.db.unit_of_work import UnitOfWork
from app.deps import get_current_db_user, get_uow
from app.models.postgres.user_model import User
from app.schemas.project_pipeline_schema import ProjectPipelineListResponse
from app.services.source_ingestion_service import SourceIngestionService
from app.utils.pagination import PaginationParams
from app.utils.response import ApiResponse

router = APIRouter(prefix="/projects", tags=["Project Pipelines"])


# ── Annotated dependency aliases ───────────────────────────────────────────

CurrentUser = Annotated[User, Depends(get_current_db_user)]
CurrentUow = Annotated[UnitOfWork, Depends(get_uow)]
CurrentPagination = Annotated[PaginationParams, Depends(PaginationParams)]
SearchFilter = Annotated[
    str | None,
    Query(
        description="Optional case-insensitive search on run_code or project_name.",
        min_length=1,
    ),
]
PipelineStatusFilter = Annotated[
    SourceIngestionStatus | None,
    Query(
        description="Optional exact status filter (running / ready_for_review / completed / failed)."
    ),
]
PipelineSourceTypeFilter = Annotated[
    SourceType | None,
    Query(
        description="Optional source_type filter (rfp / additional_rfp / source_code / meeting_notes / requirement_update)."
    ),
]


# ── Endpoints ──────────────────────────────────────────────────────────────


@router.get(
    "/me/pipelines",
    status_code=status.HTTP_200_OK,
    summary="List ingestion pipelines for the current user's projects",
    description=(
        "Returns paginated source-ingestion pipelines across every project "
        "owned by the authenticated user, most recent first. Supports "
        "optional status, source_type, and search filters."
    ),
)
async def list_my_project_pipelines(
    pagination: CurrentPagination,
    uow: CurrentUow,
    current_user: CurrentUser,
    status: PipelineStatusFilter = None,
    source_type: PipelineSourceTypeFilter = None,
    search: SearchFilter = None,
) -> ApiResponse[ProjectPipelineListResponse]:
    """GET /projects/me/pipelines — list ingestion pipelines for the logged-in user's projects."""
    result = SourceIngestionService().list_ingestions_for_user(
        uow=uow,
        owner_id=current_user.id,
        skip=pagination.skip,
        limit=pagination.limit,
        status=status.value if status else None,
        source_type=source_type.value if source_type else None,
        search=search,
    )
    return ApiResponse.ok(data=result)
