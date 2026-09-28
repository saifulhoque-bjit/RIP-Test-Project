"""Route handlers for module-feature resources — v1.

Endpoint summary
────────────────
Any authenticated user:
    GET   /projects/{project_id}/modules                                        — list modules (paginated)
    GET   /projects/{project_id}/modules/list                                   — list modules as tree
    GET   /projects/{project_id}/modules/{module_id}                            — get a single module
    GET   /projects/{project_id}/modules/{module_id}/features/{feature_id}      — get a single feature
    PATCH /projects/{project_id}/modules/{module_id}/sync-status                — update module Jira/TAP sync flags
    PATCH /projects/{project_id}/modules/{module_id}/features/{feature_id}/sync-status — update feature Jira/TAP sync flags
    PATCH /projects/{project_id}/modules/status                                 — change module/feature status
    POST  /projects/{project_id}/modules/regenerate                             — enqueue regeneration
    DELETE /projects/{project_id}/modules/{module_id}                           — delete a module and its features
    DELETE /projects/{project_id}/modules/{module_id}/features/{feature_id}     — delete a single feature

Design rules
────────────
- Zero business logic here — all decisions live in ModuleFeatureService.
- Dependencies resolved via Depends() in deps.py.
- Annotated aliases declared once at module level; reused across handlers.
- Exception handling is centralised in app/core/exception_handlers.py.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, Request, status

from app.core.messages import (
    DESC_MODULE_FILTER_SOURCE_INGESTION_ID,
    DESC_MODULE_REGENERATE,
    MSG_FEATURE_DELETED,
    MSG_FEATURE_FETCHED,
    MSG_FEATURE_SYNC_FLAGS_UPDATED,
    MSG_MODULE_DELETED,
    MSG_MODULE_FETCHED,
    MSG_MODULE_LISTED,
    MSG_MODULE_REGENERATION_QUEUED,
    MSG_MODULE_STATUS_CHANGED,
    MSG_MODULE_SYNC_FLAGS_UPDATED,
    MSG_MODULE_TREE_LISTED,
    SUMMARY_FEATURE_DELETE,
    SUMMARY_FEATURE_GET,
    SUMMARY_FEATURE_UPDATE_SYNC_FLAGS,
    SUMMARY_MODULE_CHANGE_STATUS,
    SUMMARY_MODULE_DELETE,
    SUMMARY_MODULE_GET,
    SUMMARY_MODULE_LIST,
    SUMMARY_MODULE_REGENERATE,
    SUMMARY_MODULE_TREE_LIST,
    SUMMARY_MODULE_UPDATE_SYNC_FLAGS,
)
from app.core.rate_limiter import ai_regenerate_limit, limiter
from app.db.unit_of_work import UnitOfWork
from app.deps import get_current_db_user, get_uow, require_project_access
from app.models.postgres.project_model import Project
from app.models.postgres.user_model import User
from app.schemas.module_feature_schema import (
    FeatureDeleteRequest,
    FeatureDeleteResponse,
    ModuleDeleteRequest,
    ModuleDeleteResponse,
    ModuleFeatureFeatureSingleResponse,
    ModuleFeatureListResponse,
    ModuleFeatureRegenerationQueuedResponse,
    ModuleFeatureRegenerationRequest,
    ModuleFeatureSingleResponse,
    ModuleFeatureStatusChangeRequest,
    ModuleFeatureStatusChangeResponse,
    ModuleSingleResponse,
    ModuleTreeListResponse,
    SyncFlagsUpdateRequest,
)
from app.services.module_feature_service import ModuleFeatureService
from app.utils.log_context import bind_log_context
from app.utils.pagination import PaginationParams
from app.utils.response import ApiResponse

router = APIRouter(tags=["ModuleFeature"])

# ── Annotated dependency aliases ────────────────────────────────────────────

CurrentUser = Annotated[User, Depends(get_current_db_user)]
CurrentUow = Annotated[UnitOfWork, Depends(get_uow)]
CurrentPaging = Annotated[PaginationParams, Depends(PaginationParams)]
ReadAccess = Annotated[Project, Depends(require_project_access("read"))]
WriteAccess = Annotated[Project, Depends(require_project_access("write"))]
# Query filter aliases — default=None kept at parameter site (FastAPI rule)
SourceIngestionIdFilter = Annotated[
    str | None, Query(description=DESC_MODULE_FILTER_SOURCE_INGESTION_ID)
]


# ── Handlers ────────────────────────────────────────────────────────────────


@router.get(
    "/projects/{project_id}/modules",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_MODULE_LIST,
)
async def list_modules(
    project_id: UUID,
    uow: CurrentUow,
    _current_user: CurrentUser,
    _project: ReadAccess,
    pagination: CurrentPaging,
) -> ApiResponse[ModuleFeatureListResponse]:
    """GET /projects/{project_id}/modules — list modules for a project."""
    result = await ModuleFeatureService().list_modules_for_project(
        project_id=project_id,
        uow=uow,
        skip=pagination.skip,
        limit=pagination.limit,
    )
    return ApiResponse.ok(data=result, message=MSG_MODULE_LISTED)


@router.get(
    "/projects/{project_id}/modules/list",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_MODULE_TREE_LIST,
)
async def list_modules_tree(
    project_id: UUID,
    uow: CurrentUow,
    _current_user: CurrentUser,
    _project: ReadAccess,
    source_ingestion_id: SourceIngestionIdFilter = None,
) -> ApiResponse[ModuleTreeListResponse]:
    """GET /projects/{project_id}/modules/list — list modules as a tree."""
    result = await ModuleFeatureService().list_modules_tree_for_project(
        project_id=project_id,
        uow=uow,
        source_ingestion_id=source_ingestion_id,
    )
    return ApiResponse.ok(data=result, message=MSG_MODULE_TREE_LISTED)


@router.get(
    "/projects/{project_id}/modules/{module_id}",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_MODULE_GET,
)
async def get_module(
    project_id: UUID,
    module_id: str,
    uow: CurrentUow,
    _current_user: CurrentUser,
    _project: ReadAccess,
) -> ApiResponse[ModuleSingleResponse]:
    """GET /projects/{project_id}/modules/{module_id} — fetch a single module."""
    result = await ModuleFeatureService().get_module_by_project(
        project_id=project_id,
        module_id=module_id,
        uow=uow,
    )
    return ApiResponse.ok(data=result, message=MSG_MODULE_FETCHED)


@router.get(
    "/projects/{project_id}/modules/{module_id}/features/{feature_id}",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_FEATURE_GET,
)
async def get_feature(
    project_id: UUID,
    module_id: str,
    feature_id: str,
    uow: CurrentUow,
    _current_user: CurrentUser,
    _project: ReadAccess,
) -> ApiResponse[ModuleFeatureFeatureSingleResponse]:
    """GET /projects/{project_id}/modules/{module_id}/features/{feature_id} — fetch a single feature."""
    result = await ModuleFeatureService().get_feature_by_module(
        project_id=project_id,
        module_id=module_id,
        feature_id=feature_id,
        uow=uow,
    )
    return ApiResponse.ok(data=result, message=MSG_FEATURE_FETCHED)


@router.patch(
    "/projects/{project_id}/modules/{module_id}/sync-status",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_MODULE_UPDATE_SYNC_FLAGS,
)
async def update_module_sync_flags(
    project_id: UUID,
    module_id: str,
    payload: SyncFlagsUpdateRequest,
    uow: CurrentUow,
    _current_user: CurrentUser,
    _project: WriteAccess,
) -> ApiResponse[ModuleFeatureSingleResponse]:
    """PATCH /projects/{project_id}/modules/{module_id}/sync-status — update Jira/TAP sync flags for a module."""
    result = await ModuleFeatureService().update_module_sync_flags(
        project_id=project_id,
        module_id=module_id,
        payload=payload,
        uow=uow,
    )
    return ApiResponse.ok(data=result, message=MSG_MODULE_SYNC_FLAGS_UPDATED)


@router.patch(
    "/projects/{project_id}/modules/{module_id}/features/{feature_id}/sync-status",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_FEATURE_UPDATE_SYNC_FLAGS,
)
async def update_feature_sync_flags(
    project_id: UUID,
    module_id: str,
    feature_id: str,
    payload: SyncFlagsUpdateRequest,
    uow: CurrentUow,
    _current_user: CurrentUser,
    _project: WriteAccess,
) -> ApiResponse[ModuleFeatureFeatureSingleResponse]:
    """PATCH /projects/{project_id}/modules/{module_id}/features/{feature_id}/sync-status — update Jira/TAP sync flags for a feature."""
    result = await ModuleFeatureService().update_feature_sync_flags(
        project_id=project_id,
        module_id=module_id,
        feature_id=feature_id,
        payload=payload,
        uow=uow,
    )
    return ApiResponse.ok(data=result, message=MSG_FEATURE_SYNC_FLAGS_UPDATED)


@router.patch(
    "/projects/{project_id}/modules/status",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_MODULE_CHANGE_STATUS,
)
async def change_module_feature_status(
    project_id: UUID,
    payload: ModuleFeatureStatusChangeRequest,
    uow: CurrentUow,
    current_user: CurrentUser,
    _project: WriteAccess,
) -> ApiResponse[ModuleFeatureStatusChangeResponse]:
    """PATCH /projects/{project_id}/modules/status — change module/feature status."""
    with bind_log_context(project_id=str(project_id)):
        result = await ModuleFeatureService().change_module_feature_status_for_project(
            project_id=project_id,
            payload=payload,
            uow=uow,
            user_id=current_user.id,
        )
        return ApiResponse.ok(data=result, message=MSG_MODULE_STATUS_CHANGED)


@router.post(
    "/projects/{project_id}/modules/regenerate",
    status_code=status.HTTP_202_ACCEPTED,
    summary=SUMMARY_MODULE_REGENERATE,
    description=DESC_MODULE_REGENERATE,
)
@limiter.limit(ai_regenerate_limit)
async def regenerate_modules(
    request: Request,
    project_id: UUID,
    payload: ModuleFeatureRegenerationRequest,
    uow: CurrentUow,
    current_user: CurrentUser,
    _project: WriteAccess,
) -> ApiResponse[ModuleFeatureRegenerationQueuedResponse]:
    """POST /projects/{project_id}/modules/regenerate — enqueue regeneration."""
    with bind_log_context(project_id=str(project_id)):
        result = await ModuleFeatureService().enqueue_module_feature_regeneration(
            project_id=project_id,
            feedback=payload.feedback,
            module_ids=payload.module_ids,
            feature_ids=payload.feature_ids,
            uow=uow,
            user_id=current_user.id,
        )
        return ApiResponse.ok(data=result, message=MSG_MODULE_REGENERATION_QUEUED)


@router.delete(
    "/projects/{project_id}/modules/{module_id}",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_MODULE_DELETE,
)
async def delete_module(
    project_id: UUID,
    module_id: str,
    uow: CurrentUow,
    _current_user: CurrentUser,
    _project: WriteAccess,
    payload: Annotated[ModuleDeleteRequest | None, Body()] = None,
) -> ApiResponse[ModuleDeleteResponse]:
    """DELETE /projects/{project_id}/modules/{module_id} — delete a module and its features.

    ``reason`` is required in the request body when the module status is
    ``approved``. Approved modules (and their features and user stories) are
    soft-deleted; all others are hard-deleted along with their features, user
    stories, and version snapshots.
    """
    outcome = await ModuleFeatureService().delete_module(
        project_id=project_id,
        module_id=module_id,
        uow=uow,
        reason=payload.reason if payload else None,
    )
    return ApiResponse.ok(
        data=ModuleDeleteResponse(
            module_id=module_id,
            project_id=project_id,
            is_deleted=outcome["is_deleted"],
            deletion_reason=outcome["deletion_reason"],
            deleted_at=outcome["deleted_at"],
        ),
        message=MSG_MODULE_DELETED,
    )


@router.delete(
    "/projects/{project_id}/modules/{module_id}/features/{feature_id}",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_FEATURE_DELETE,
)
async def delete_feature(
    project_id: UUID,
    module_id: str,
    feature_id: str,
    uow: CurrentUow,
    _current_user: CurrentUser,
    _project: WriteAccess,
    payload: Annotated[FeatureDeleteRequest | None, Body()] = None,
) -> ApiResponse[FeatureDeleteResponse]:
    """DELETE /projects/{project_id}/modules/{module_id}/features/{feature_id} — delete a feature.

    ``reason`` is required in the request body when the feature status is
    ``approved``. Approved features (and their user stories) are soft-deleted;
    all others are hard-deleted along with their user stories and version
    snapshots.
    """
    outcome = await ModuleFeatureService().delete_feature(
        project_id=project_id,
        module_id=module_id,
        feature_id=feature_id,
        uow=uow,
        reason=payload.reason if payload else None,
    )
    return ApiResponse.ok(
        data=FeatureDeleteResponse(
            feature_id=feature_id,
            module_id=module_id,
            project_id=project_id,
            is_deleted=outcome["is_deleted"],
            deletion_reason=outcome["deletion_reason"],
            deleted_at=outcome["deleted_at"],
        ),
        message=MSG_FEATURE_DELETED,
    )
