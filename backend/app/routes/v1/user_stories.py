"""Route handlers for /user-stories — v1.

Endpoint summary
────────────────
Project owner/admin/super_admin, or an assigned project Member (which grants
"read"/"write"/"approve" alike, the last used by the status endpoints — see
ProjectService.assert_project_access):
    GET    /projects/{project_id}/user-stories                          — list (paginated, filtered)
    DELETE /projects/{project_id}/user-stories                          — delete all for project
    GET    /projects/{project_id}/user-stories/list/tree                — full tree view
    GET    /projects/{project_id}/user-stories/summary                  — aggregate counts
    PATCH  /projects/{project_id}/user-stories/{user_story_id}/bboxes  — update bounding boxes
    GET    /projects/{project_id}/user-stories/{user_story_id}         — fetch single user story
    DELETE /projects/{project_id}/user-stories/{user_story_id}         — delete single user story
    PATCH  /user-stories/{user_story_id}/status                        — change status ("approve" level)
    PATCH  /user-stories/{user_story_id}/sync-status                   — update Jira/TAP sync flags
    PATCH  /projects/{project_id}/user-stories/status                   — change status for whole project ("approve" level)
    PATCH  /projects/{project_id}/user-stories/bulk-status              — change status for selected IDs ("approve" level)
    POST   /projects/{project_id}/user-stories/regenerate               — enqueue regeneration
    POST   /projects/{project_id}/user-stories/regenerate-by-feedback   — enqueue targeted patch
    POST   /projects/{project_id}/user-stories/regenerate-for-source-code — enqueue source-code MFU regeneration

Design rules
────────────
- Zero business logic here — all decisions live in UserStoryService.
- Annotated aliases declared once at module level; reused across handlers.
- The two endpoints without ``project_id`` in their path resolve the owning
  project via ``UserStoryRepository.get_project_id_by_user_story_id`` and
  call ``ProjectService.assert_project_access`` manually — see
  ``_authorize_by_user_story_id`` below.
- Exception handling is centralised in app/core/exception_handlers.py.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, Request, status

from app.core.exceptions import NotFoundError
from app.core.messages import (
    DESC_USER_STORY_FILTER_CODE,
    DESC_USER_STORY_FILTER_CONSENSUS_MAX,
    DESC_USER_STORY_FILTER_CONSENSUS_MIN,
    DESC_USER_STORY_FILTER_FEATURE_ID,
    DESC_USER_STORY_FILTER_MODULE_ID,
    DESC_USER_STORY_FILTER_SEARCH_TEXT,
    DESC_USER_STORY_FILTER_SOURCE_ID,
    DESC_USER_STORY_FILTER_SOURCE_INGESTION_ID,
    DESC_USER_STORY_FILTER_STATUS,
    DESC_USER_STORY_FILTER_SYNC_TARGET,
    DESC_USER_STORY_FILTER_VERSION,
    DESC_USER_STORY_REGENERATE,
    DESC_USER_STORY_REGENERATE_BY_FEEDBACK,
    DESC_USER_STORY_REGENERATE_FOR_SOURCE_CODE,
    MSG_PROJECT_NOT_FOUND,
    MSG_PROJECT_USER_STORIES_STATUS_CHANGED,
    MSG_PROJECT_USER_STORY_SUMMARY_FETCHED,
    MSG_USER_STORY_BBOXES_UPDATED,
    MSG_USER_STORY_BULK_STATUS_CHANGED,
    MSG_USER_STORY_DELETED,
    MSG_USER_STORY_DETAIL_FETCHED,
    MSG_USER_STORY_LISTED,
    MSG_USER_STORY_NOT_FOUND_BY_ID,
    MSG_USER_STORY_REGENERATION_BY_FEEDBACK_QUEUED,
    MSG_USER_STORY_REGENERATION_FOR_SOURCE_CODE_QUEUED,
    MSG_USER_STORY_REGENERATION_QUEUED,
    MSG_USER_STORY_SINGLE_DELETED,
    MSG_USER_STORY_STATUS_CHANGED,
    MSG_USER_STORY_SYNC_CANDIDATES_FETCHED,
    MSG_USER_STORY_SYNC_FLAGS_UPDATED,
    MSG_USER_STORY_TREE_FETCHED,
    SUMMARY_PROJECT_USER_STORY_SUMMARY,
    SUMMARY_USER_STORY_BULK_CHANGE_STATUS_BY_IDS,
    SUMMARY_USER_STORY_CHANGE_STATUS,
    SUMMARY_USER_STORY_CHANGE_STATUS_BY_PROJECT,
    SUMMARY_USER_STORY_DELETE_BY_ID,
    SUMMARY_USER_STORY_DELETE_BY_PROJECT,
    SUMMARY_USER_STORY_GET_BY_PROJECT,
    SUMMARY_USER_STORY_LIST_BY_PROJECT,
    SUMMARY_USER_STORY_REGENERATE,
    SUMMARY_USER_STORY_REGENERATE_BY_FEEDBACK,
    SUMMARY_USER_STORY_REGENERATE_FOR_SOURCE_CODE,
    SUMMARY_USER_STORY_SYNC_CANDIDATES,
    SUMMARY_USER_STORY_TREE_BY_PROJECT,
    SUMMARY_USER_STORY_UPDATE_BBOXES,
    SUMMARY_USER_STORY_UPDATE_SYNC_FLAGS,
)
from app.core.rate_limiter import ai_regenerate_limit, limiter
from app.db.unit_of_work import UnitOfWork
from app.deps import get_current_db_user, get_uow, require_project_access
from app.models.postgres.project_model import Project
from app.models.postgres.user_model import User
from app.repositories.neo4j.user_story_repository import UserStoryRepository
from app.schemas.module_feature_schema import (
    FeatureRegenerationQueuedResponse,
    FeatureRegenerationRequest,
)
from app.schemas.user_story_schema import (
    BulkStatusChangeRequest,
    BulkStatusChangeResponse,
    ChangeStatusRequest,
    ProjectUserStoriesStatusChangedResponse,
    ProjectUserStorySummaryResponse,
    StoryFeedbackInput,
    SyncCandidateTreeResponse,
    UpdateBboxesRequest,
    UpdateBboxesResponse,
    UserStoryDeleteByProjectResponse,
    UserStoryDeleteRequest,
    UserStoryDeleteResponse,
    UserStoryDetailResponse,
    UserStoryListResponse,
    UserStoryRegenerationQueuedResponse,
    UserStoryRegenerationRequest,
    UserStoryStatusChangedResponse,
    UserStorySyncFlagsUpdatedResponse,
    UserStorySyncFlagsUpdateRequest,
    UserStoryTreeResponse,
)
from app.services.module_feature_service import ModuleFeatureService
from app.services.project_service import ProjectService
from app.services.user_story_service import UserStoryService
from app.utils.log_context import bind_log_context
from app.utils.openapi import USER_STORY_REGENERATE_BY_FEEDBACK_OPENAPI_EXTRA
from app.utils.pagination import PaginationParams
from app.utils.response import ApiResponse

router = APIRouter(tags=["User Story"])

# ── Annotated dependency aliases ────────────────────────────────────────────

CurrentUser = Annotated[User, Depends(get_current_db_user)]
CurrentUow = Annotated[UnitOfWork, Depends(get_uow)]
CurrentPaging = Annotated[PaginationParams, Depends(PaginationParams)]
ReadAccess = Annotated[Project, Depends(require_project_access("read"))]
WriteAccess = Annotated[Project, Depends(require_project_access("write"))]
ApproveAccess = Annotated[Project, Depends(require_project_access("approve"))]

# Query filter aliases — default=None kept at parameter site (FastAPI rule)
StatusFilter = Annotated[str | None, Query(description=DESC_USER_STORY_FILTER_STATUS)]
VersionFilter = Annotated[int | None, Query(description=DESC_USER_STORY_FILTER_VERSION)]
ModuleIdFilter = Annotated[str | None, Query(description=DESC_USER_STORY_FILTER_MODULE_ID)]
FeatureIdFilter = Annotated[str | None, Query(description=DESC_USER_STORY_FILTER_FEATURE_ID)]
SourceIdFilter = Annotated[str | None, Query(description=DESC_USER_STORY_FILTER_SOURCE_ID)]
SearchTextFilter = Annotated[str | None, Query(description=DESC_USER_STORY_FILTER_SEARCH_TEXT)]
ReqCodeFilter = Annotated[str | None, Query(description=DESC_USER_STORY_FILTER_CODE)]
ConsensusMinFilter = Annotated[
    float | None, Query(ge=0.0, le=10.0, description=DESC_USER_STORY_FILTER_CONSENSUS_MIN)
]
ConsensusMaxFilter = Annotated[
    float | None, Query(ge=0.0, le=10.0, description=DESC_USER_STORY_FILTER_CONSENSUS_MAX)
]
SourceIngestionIdFilter = Annotated[
    str | None, Query(description=DESC_USER_STORY_FILTER_SOURCE_INGESTION_ID)
]
SyncTargetFilter = Annotated[
    Literal["jira", "tap"], Query(description=DESC_USER_STORY_FILTER_SYNC_TARGET)
]
IncludeDeleted = Annotated[
    bool,
    Query(description="Include soft-deleted user stories for sync payload assembly."),
]


async def _authorize_by_user_story_id(
    user_story_id: str, level: str, current_user: User, uow: UnitOfWork
) -> UUID:
    """Resolve the owning project for a bare user-story ID and enforce access.

    Used by the two endpoints that identify a user story without a
    ``project_id`` path param (``.../status``, ``.../sync-status``) — every
    other route on this router has ``project_id`` in its path and uses the
    ``require_project_access`` dependency directly instead.

    Returns the resolved ``project_id`` so callers that need it (e.g. to run
    further project-scoped validation) don't have to re-resolve it.
    """
    project_id = await UserStoryRepository().get_project_id_by_user_story_id(user_story_id)
    if project_id is None:
        raise NotFoundError(MSG_USER_STORY_NOT_FOUND_BY_ID.format(user_story_id=user_story_id))
    project = uow.projects.get_by_uuid(project_id)
    if project is None:
        raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))
    ProjectService.assert_project_access(
        project=project,
        requester_id=current_user.id,
        requester_roles=current_user.role_names,
        requester_tenant_id=current_user.tenant_id,
        level=level,
        uow=uow,
    )
    return project_id


# ── Handlers ─────────────────────────────────────────────────────────────────


@router.get(
    "/projects/{project_id}/user-stories",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_USER_STORY_LIST_BY_PROJECT,
)
async def list_user_stories_by_project(
    project_id: UUID,
    uow: CurrentUow,
    _current_user: CurrentUser,
    _project: ReadAccess,
    pagination: CurrentPaging,
    req_status: StatusFilter = None,
    version: VersionFilter = None,
    module_id: ModuleIdFilter = None,
    feature_id: FeatureIdFilter = None,
    source_id: SourceIdFilter = None,
    search_text: SearchTextFilter = None,
    user_story_code: ReqCodeFilter = None,
    consensus_min: ConsensusMinFilter = None,
    consensus_max: ConsensusMaxFilter = None,
) -> ApiResponse[UserStoryListResponse]:
    """GET /projects/{project_id}/user-stories — paginated, filtered user story list."""
    result = await UserStoryService().list_user_stories_by_project(
        project_id=project_id,
        skip=pagination.skip,
        limit=pagination.limit,
        status=req_status,
        version=version,
        module_id=module_id,
        feature_id=feature_id,
        source_id=source_id,
        search_text=search_text,
        user_story_code=user_story_code,
        consensus_min=consensus_min,
        consensus_max=consensus_max,
        uow=uow,
    )
    return ApiResponse.ok(data=result, message=MSG_USER_STORY_LISTED)


@router.delete(
    "/projects/{project_id}/user-stories",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_USER_STORY_DELETE_BY_PROJECT,
)
async def delete_user_stories_by_project(
    project_id: UUID,
    uow: CurrentUow,
    _current_user: CurrentUser,
    _project: WriteAccess,
) -> ApiResponse[UserStoryDeleteByProjectResponse]:
    """DELETE /projects/{project_id}/user-stories — delete all user stories for a project."""
    deleted_count = await UserStoryService().delete_user_stories_for_project(
        project_id=project_id,
        uow=uow,
    )
    return ApiResponse.ok(
        data=UserStoryDeleteByProjectResponse(
            project_id=project_id,
            deleted_count=deleted_count,
        ),
        message=MSG_USER_STORY_DELETED,
    )


@router.get(
    "/projects/{project_id}/user-stories/list/tree",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_USER_STORY_TREE_BY_PROJECT,
)
async def get_user_stories_tree(
    project_id: UUID,
    uow: CurrentUow,
    _current_user: CurrentUser,
    _project: ReadAccess,
    source_ingestion_id: SourceIngestionIdFilter = None,
) -> ApiResponse[UserStoryTreeResponse]:
    """GET /projects/{project_id}/user-stories/list/tree — full tree view."""
    result = await UserStoryService().get_user_stories_tree(
        project_id=project_id,
        uow=uow,
        source_ingestion_id=source_ingestion_id,
    )
    return ApiResponse.ok(data=result, message=MSG_USER_STORY_TREE_FETCHED)


@router.get(
    "/projects/{project_id}/user-stories/summary",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_PROJECT_USER_STORY_SUMMARY,
)
async def get_project_user_story_summary(
    project_id: UUID,
    uow: CurrentUow,
    _current_user: CurrentUser,
    _project: ReadAccess,
) -> ApiResponse[ProjectUserStorySummaryResponse]:
    """GET /projects/{project_id}/user-stories/summary — aggregate counts."""
    result = await UserStoryService().get_project_user_story_summary(
        project_id=project_id,
        uow=uow,
    )
    return ApiResponse.ok(data=result, message=MSG_PROJECT_USER_STORY_SUMMARY_FETCHED)


@router.get(
    "/projects/{project_id}/user-stories/sync-candidates",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_USER_STORY_SYNC_CANDIDATES,
)
async def get_user_story_sync_candidates(
    project_id: UUID,
    uow: CurrentUow,
    _current_user: CurrentUser,
    _project: ReadAccess,
    sync_target: SyncTargetFilter,
) -> ApiResponse[SyncCandidateTreeResponse]:
    """GET /projects/{project_id}/user-stories/sync-candidates — approved, not-yet-synced tree."""
    result = await UserStoryService().get_sync_candidate_tree(
        project_id=project_id,
        sync_target=sync_target,
        uow=uow,
    )
    return ApiResponse.ok(data=result, message=MSG_USER_STORY_SYNC_CANDIDATES_FETCHED)


@router.patch(
    "/projects/{project_id}/user-stories/{user_story_id}/bboxes",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_USER_STORY_UPDATE_BBOXES,
)
async def update_user_story_bboxes(
    project_id: UUID,
    user_story_id: str,
    payload: UpdateBboxesRequest,
    uow: CurrentUow,
    _current_user: CurrentUser,
    _project: WriteAccess,
) -> ApiResponse[UpdateBboxesResponse]:
    """PATCH /projects/{project_id}/user-stories/{user_story_id}/bboxes — update bounding boxes."""
    sources = [b.model_dump(mode="json") for b in (payload.sources or [])]
    result = await UserStoryService().update_user_story_bboxes(
        project_id=project_id,
        user_story_id=user_story_id,
        sources=sources,
        uow=uow,
    )
    return ApiResponse.ok(data=result, message=MSG_USER_STORY_BBOXES_UPDATED)


@router.get(
    "/projects/{project_id}/user-stories/{user_story_id}",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_USER_STORY_GET_BY_PROJECT,
)
async def get_user_story_by_project(
    project_id: UUID,
    user_story_id: str,
    uow: CurrentUow,
    _current_user: CurrentUser,
    _project: ReadAccess,
    include_deleted: IncludeDeleted = False,
) -> ApiResponse[UserStoryDetailResponse]:
    """GET /projects/{project_id}/user-stories/{user_story_id} — fetch a single user story."""
    result = await UserStoryService().get_user_story_detail_by_project(
        project_id=project_id,
        user_story_id=user_story_id,
        uow=uow,
        include_deleted=include_deleted,
    )
    return ApiResponse.ok(data=result, message=MSG_USER_STORY_DETAIL_FETCHED)


@router.delete(
    "/projects/{project_id}/user-stories/{user_story_id}",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_USER_STORY_DELETE_BY_ID,
)
async def delete_user_story_by_id(
    project_id: UUID,
    user_story_id: str,
    uow: CurrentUow,
    _current_user: CurrentUser,
    _project: WriteAccess,
    payload: Annotated[UserStoryDeleteRequest | None, Body()] = None,
) -> ApiResponse[UserStoryDeleteResponse]:
    """DELETE /projects/{project_id}/user-stories/{user_story_id} — delete a single user story.

    ``reason`` is required in the request body when the user story status is ``approved``.
    Approved user stories are soft-deleted (``is_current=false``); all others are hard-deleted.
    """
    outcome = await UserStoryService().delete_user_story(
        project_id=project_id,
        user_story_id=user_story_id,
        uow=uow,
        reason=payload.reason if payload else None,
    )
    return ApiResponse.ok(
        data=UserStoryDeleteResponse(
            user_story_id=user_story_id,
            project_id=project_id,
            is_current=outcome["is_current"],
            del_reason=outcome["del_reason"],
            deleted_at=outcome["deleted_at"],
        ),
        message=MSG_USER_STORY_SINGLE_DELETED,
    )


@router.patch(
    "/user-stories/{user_story_id}/status",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_USER_STORY_CHANGE_STATUS,
)
async def change_status(
    user_story_id: str,
    request: ChangeStatusRequest,
    current_user: CurrentUser,
    uow: CurrentUow,
) -> ApiResponse[UserStoryStatusChangedResponse]:
    """PATCH /user-stories/{user_story_id}/status — change status of a single user story.

    Requires ``"approve"`` access — an assigned Member, or the owner/admin/
    super_admin — since this transitions to/from the ``approved`` status.
    """
    project_id = await _authorize_by_user_story_id(user_story_id, "approve", current_user, uow)
    result = await UserStoryService().change_status(
        user_story_id=user_story_id,
        request=request,
        project_id=project_id,
        uow=uow,
        user_id=current_user.id,
    )
    return ApiResponse.ok(data=result, message=MSG_USER_STORY_STATUS_CHANGED)


@router.patch(
    "/user-stories/{user_story_id}/sync-status",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_USER_STORY_UPDATE_SYNC_FLAGS,
)
async def update_sync_flags(
    user_story_id: str,
    request: UserStorySyncFlagsUpdateRequest,
    current_user: CurrentUser,
    uow: CurrentUow,
) -> ApiResponse[UserStorySyncFlagsUpdatedResponse]:
    """PATCH /user-stories/{user_story_id}/sync-status — update Jira/TAP sync flags of a single user story."""
    await _authorize_by_user_story_id(user_story_id, "write", current_user, uow)
    result = await UserStoryService().update_sync_flags(
        user_story_id=user_story_id,
        request=request,
    )
    return ApiResponse.ok(data=result, message=MSG_USER_STORY_SYNC_FLAGS_UPDATED)


@router.patch(
    "/projects/{project_id}/user-stories/status",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_USER_STORY_CHANGE_STATUS_BY_PROJECT,
)
async def change_status_by_project(
    project_id: UUID,
    request: ChangeStatusRequest,
    uow: CurrentUow,
    current_user: CurrentUser,
    _project: ApproveAccess,
) -> ApiResponse[ProjectUserStoriesStatusChangedResponse]:
    """PATCH /projects/{project_id}/user-stories/status — change status for all user stories in a project."""
    pid, req_status, updated_count = await UserStoryService().change_status_by_project(
        project_id=project_id,
        request=request,
        uow=uow,
        user_id=current_user.id,
    )
    return ApiResponse.ok(
        data=ProjectUserStoriesStatusChangedResponse(
            project_id=pid,
            status=req_status,
            updated_count=updated_count,
        ),
        message=MSG_PROJECT_USER_STORIES_STATUS_CHANGED,
    )


@router.patch(
    "/projects/{project_id}/user-stories/bulk-status",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_USER_STORY_BULK_CHANGE_STATUS_BY_IDS,
)
async def bulk_change_status_by_project(
    project_id: UUID,
    request: BulkStatusChangeRequest,
    uow: CurrentUow,
    current_user: CurrentUser,
    _project: ApproveAccess,
) -> ApiResponse[BulkStatusChangeResponse]:
    """PATCH /projects/{project_id}/user-stories/bulk-status — change status for selected user story IDs."""
    result = await UserStoryService().bulk_change_status_by_project(
        project_id=project_id,
        request=request,
        uow=uow,
        user_id=current_user.id,
    )
    return ApiResponse.ok(data=result, message=MSG_USER_STORY_BULK_STATUS_CHANGED)


@router.post(
    "/projects/{project_id}/user-stories/regenerate",
    status_code=status.HTTP_202_ACCEPTED,
    summary=SUMMARY_USER_STORY_REGENERATE,
    description=DESC_USER_STORY_REGENERATE,
)
@limiter.limit(ai_regenerate_limit)
async def regenerate_user_stories(
    request: Request,
    project_id: UUID,
    current_user: CurrentUser,
    uow: CurrentUow,
    _project: WriteAccess,
    payload: Annotated[UserStoryRegenerationRequest | None, Body()] = None,
) -> ApiResponse[UserStoryRegenerationQueuedResponse]:
    """POST /projects/{project_id}/user-stories/regenerate — enqueue user story regeneration."""
    result = await UserStoryService().enqueue_user_story_regeneration(
        project_id=project_id,
        feedback=payload.feedback if payload else None,
        module_ids=payload.module_ids if payload else None,
        feature_ids=payload.feature_ids if payload else None,
        user_story_ids=payload.user_story_ids if payload else None,
        uow=uow,
        user_id=current_user.id,
    )
    return ApiResponse.ok(data=result, message=MSG_USER_STORY_REGENERATION_QUEUED)


@router.post(
    "/projects/{project_id}/user-stories/regenerate-by-feedback",
    status_code=status.HTTP_202_ACCEPTED,
    summary=SUMMARY_USER_STORY_REGENERATE_BY_FEEDBACK,
    description=DESC_USER_STORY_REGENERATE_BY_FEEDBACK,
    openapi_extra=USER_STORY_REGENERATE_BY_FEEDBACK_OPENAPI_EXTRA,
)
@limiter.limit(ai_regenerate_limit)
async def regenerate_user_stories_by_feedback(
    request: Request,
    project_id: UUID,
    current_user: CurrentUser,
    uow: CurrentUow,
    _project: WriteAccess,
    payload: Annotated[list[StoryFeedbackInput], Body()],
    skip_processing: Annotated[
        bool,
        Query(
            description=(
                "Skip the AI pipeline and return cached output when available. Default is False."
            )
        ),
    ] = False,
) -> ApiResponse[UserStoryRegenerationQueuedResponse]:
    """POST /projects/{project_id}/user-stories/regenerate-by-feedback — enqueue targeted patch regeneration.

    Accepts a list of per-story feedback items.  Each item identifies a user
    story by ID and carries optional whole-story feedback and/or inline
    selection-level comments.  The AI patch pipeline revises only the targeted
    stories, preserving unchanged siblings.
    """
    result = await UserStoryService().enqueue_user_story_regeneration_by_feedback(
        project_id=project_id,
        feedback_items=payload,
        uow=uow,
        user_id=current_user.id,
        skip_processing=skip_processing,
    )
    return ApiResponse.ok(data=result, message=MSG_USER_STORY_REGENERATION_BY_FEEDBACK_QUEUED)


@router.post(
    "/projects/{project_id}/user-stories/regenerate-for-source-code",
    status_code=status.HTTP_202_ACCEPTED,
    summary=SUMMARY_USER_STORY_REGENERATE_FOR_SOURCE_CODE,
    description=DESC_USER_STORY_REGENERATE_FOR_SOURCE_CODE,
)
@limiter.limit(ai_regenerate_limit)
async def regenerate_user_stories_for_source_code(
    request: Request,
    project_id: UUID,
    payload: FeatureRegenerationRequest,
    uow: CurrentUow,
    current_user: CurrentUser,
    _project: WriteAccess,
) -> ApiResponse[FeatureRegenerationQueuedResponse]:
    """POST /projects/{project_id}/user-stories/regenerate-for-source-code — enqueue feedback-driven MFU regeneration.

    Distinct from `regenerate-by-feedback` above: this targets source-code-derived
    features (Stage 5 MFU-regeneration pipeline), not the RFP graph-patch pipeline.
    Each feedback item carries its own ``mod_code``/``mfu_id``, so one request can
    target several MFUs; the affected Feature is resolved server-side per module/MFU pair.
    """
    with bind_log_context(project_id=str(project_id)):
        result = await ModuleFeatureService().enqueue_feature_regeneration(
            project_id=project_id,
            feedback_items=payload.feedback_items,
            skip_processing=payload.skip_processing,
            uow=uow,
            user_id=current_user.id,
        )
        return ApiResponse.ok(
            data=result, message=MSG_USER_STORY_REGENERATION_FOR_SOURCE_CODE_QUEUED
        )
