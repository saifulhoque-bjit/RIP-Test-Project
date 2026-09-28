"""Routes for Jira integration config and sync operations."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status

from app.core.messages import (
    MSG_JIRA_CONNECTION_FAILED,
    MSG_JIRA_CONNECTION_OK,
    MSG_JIRA_INTEGRATION_CREATED,
    MSG_JIRA_INTEGRATION_DELETED,
    MSG_JIRA_INTEGRATION_UPDATED,
    SUMMARY_JIRA_INTEGRATION_CREATE,
    SUMMARY_JIRA_INTEGRATION_DELETE,
    SUMMARY_JIRA_INTEGRATION_GET,
    SUMMARY_JIRA_INTEGRATION_UPDATE,
    SUMMARY_JIRA_ISSUE_TYPES,
    SUMMARY_JIRA_SYNC_EXECUTE,
    SUMMARY_JIRA_SYNC_HISTORY_DETAIL,
    SUMMARY_JIRA_SYNC_HISTORY_LIST,
    SUMMARY_JIRA_SYNC_PREVIEW,
)
from app.core.rate_limiter import jira_config_limit, jira_sync_limit, limiter
from app.db.unit_of_work import UnitOfWork
from app.deps import (
    get_current_db_user,
    get_jira_integration_service,
    get_jira_sync_service,
    get_uow,
)
from app.models.postgres.user_model import User
from app.schemas.jira_integration_schema import (
    JiraConnectionTestResponse,
    JiraIntegrationCreate,
    JiraIntegrationResponse,
    JiraIntegrationUpdate,
    JiraIssueTypeListResponse,
    JiraSyncExecuteRequest,
    JiraSyncExecuteResponse,
    JiraSyncHistoryListResponse,
    JiraSyncHistoryResponse,
    JiraSyncPreviewResponse,
)
from app.services.jira_integration_service import JiraIntegrationService
from app.services.jira_sync_service import JiraSyncService
from app.utils.pagination import PaginationParams
from app.utils.response import ApiResponse

router = APIRouter(
    prefix="/projects/{project_id}/integrations/jira",
    tags=["Jira Integration"],
)

# ── Dependency aliases ─────────────────────────────────────────────────────

CurrentUser = Annotated[User, Depends(get_current_db_user)]
CurrentUow = Annotated[UnitOfWork, Depends(get_uow)]
CurrentPagination = Annotated[PaginationParams, Depends(PaginationParams)]
IntegrationService = Annotated[JiraIntegrationService, Depends(get_jira_integration_service)]
SyncService = Annotated[JiraSyncService, Depends(get_jira_sync_service)]


# ── Config CRUD ────────────────────────────────────────────────────────────


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary=SUMMARY_JIRA_INTEGRATION_CREATE,
)
@limiter.limit(jira_config_limit)
async def create_integration(
    request: Request,
    project_id: UUID,
    payload: JiraIntegrationCreate,
    current_user: CurrentUser,
    uow: CurrentUow,
    service: IntegrationService,
) -> ApiResponse[JiraIntegrationResponse]:
    result = await service.create_integration(
        project_id=project_id,
        payload=payload,
        uow=uow,
        user_id=current_user.id,
        user_roles=current_user.role_names,
    )
    return ApiResponse.ok(data=result, message=MSG_JIRA_INTEGRATION_CREATED)


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_JIRA_INTEGRATION_GET,
)
def get_integration(
    project_id: UUID,
    current_user: CurrentUser,
    uow: CurrentUow,
    service: IntegrationService,
) -> ApiResponse[JiraIntegrationResponse]:
    result = service.get_integration(project_id=project_id, uow=uow)
    return ApiResponse.ok(data=result)


@router.patch(
    "",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_JIRA_INTEGRATION_UPDATE,
)
async def update_integration(
    project_id: UUID,
    payload: JiraIntegrationUpdate,
    current_user: CurrentUser,
    uow: CurrentUow,
    service: IntegrationService,
) -> ApiResponse[JiraIntegrationResponse]:
    result = await service.update_integration(project_id=project_id, payload=payload, uow=uow)
    return ApiResponse.ok(data=result, message=MSG_JIRA_INTEGRATION_UPDATED)


@router.delete(
    "",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_JIRA_INTEGRATION_DELETE,
)
def delete_integration(
    project_id: UUID,
    current_user: CurrentUser,
    uow: CurrentUow,
    service: IntegrationService,
) -> ApiResponse[None]:
    service.delete_integration(project_id=project_id, uow=uow)
    return ApiResponse.ok(data=None, message=MSG_JIRA_INTEGRATION_DELETED)


# ── Connection & discovery ─────────────────────────────────────────────────


@router.post(
    "/test-existing",
    status_code=status.HTTP_200_OK,
    summary="Test existing JIRA integration connection",
)
async def test_connection(
    project_id: UUID,
    current_user: CurrentUser,
    uow: CurrentUow,
    service: IntegrationService,
) -> ApiResponse[JiraConnectionTestResponse]:
    """Test if existing JIRA integration for this project still works."""
    result = await service.test_connection(project_id=project_id, uow=uow)
    msg = (
        MSG_JIRA_CONNECTION_OK
        if result.connected
        else MSG_JIRA_CONNECTION_FAILED.format(detail="Connection refused or credentials invalid")
    )
    return ApiResponse.ok(data=result, message=msg)


@router.get(
    "/issue-types",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_JIRA_ISSUE_TYPES,
)
async def get_issue_types(
    project_id: UUID,
    current_user: CurrentUser,
    uow: CurrentUow,
    service: IntegrationService,
) -> ApiResponse[JiraIssueTypeListResponse]:
    result = await service.get_issue_types(project_id=project_id, uow=uow)
    return ApiResponse.ok(data=result)


# ── Sync ───────────────────────────────────────────────────────────────────


@router.get(
    "/sync/preview",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_JIRA_SYNC_PREVIEW,
)
async def sync_preview(
    project_id: UUID,
    current_user: CurrentUser,
    uow: CurrentUow,
    service: SyncService,
) -> ApiResponse[JiraSyncPreviewResponse]:
    result = await service.compute_sync_preview(project_id=project_id, uow=uow)
    return ApiResponse.ok(data=result)


@router.post(
    "/sync",
    status_code=status.HTTP_201_CREATED,
    summary=SUMMARY_JIRA_SYNC_EXECUTE,
)
@limiter.limit(jira_sync_limit)
async def sync_execute(
    request: Request,
    project_id: UUID,
    payload: JiraSyncExecuteRequest,
    current_user: CurrentUser,
    uow: CurrentUow,
    integration_service: IntegrationService,
    sync_service: SyncService,
) -> ApiResponse[JiraSyncExecuteResponse]:
    """Execute JIRA sync with full module/feature/story hierarchy.

    Synchronously syncs all provided RIP entities to JIRA:
    - Modules → Components
    - Features → Epics
    - User Stories → Stories

    Returns counts and details of created/updated/deprecated items.
    """
    # Validate integration exists
    integration_service.get_integration(project_id=project_id, uow=uow)

    # Execute sync with full hierarchy payload
    result = await sync_service.execute_sync_with_hierarchy(
        project_id=project_id,
        modules=payload.modules,
        triggered_by_id=current_user.id,
        uow=uow,
    )

    return ApiResponse.ok(
        data=result,
        message=f"Synced {result.total_synced} items to JIRA: {result.created} created, {result.updated} updated, {result.deprecated} deprecated",
    )


# ── Sync history ───────────────────────────────────────────────────────────


@router.get(
    "/sync/history",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_JIRA_SYNC_HISTORY_LIST,
)
def list_sync_history(
    project_id: UUID,
    pagination: CurrentPagination,
    current_user: CurrentUser,
    uow: CurrentUow,
    service: SyncService,
) -> ApiResponse[JiraSyncHistoryListResponse]:
    result = service.get_sync_history(
        project_id=project_id,
        skip=pagination.skip,
        limit=pagination.limit,
        uow=uow,
    )
    return ApiResponse.ok(data=result)


@router.get(
    "/sync/history/{sync_id}",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_JIRA_SYNC_HISTORY_DETAIL,
)
def get_sync_history_detail(
    project_id: UUID,
    sync_id: UUID,
    current_user: CurrentUser,
    uow: CurrentUow,
    service: SyncService,
) -> ApiResponse[JiraSyncHistoryResponse]:
    result = service.get_sync_history_detail(sync_id=sync_id, uow=uow)
    return ApiResponse.ok(data=result)
