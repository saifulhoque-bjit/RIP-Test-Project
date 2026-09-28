"""Route handlers for the /projects resource — v1.

Endpoint summary
────────────────
Client Admin only (NOT super_admin — see note below):
    POST   /projects                — create a new project

Project owner/admin, or an assigned Member ("read"):
    GET    /projects                — list own + assigned projects (paginated)
    GET    /projects/{project_id}   — get a single project

Project owner/admin only (no ProjectMember bypass — a Member can view a
project but never rename, retype, or delete it):
    PUT    /projects/{project_id}   — partial update
    DELETE /projects/{project_id}   — delete project

Admin only, super_admin sees every tenant:
    GET    /projects/all            — list every project in the platform

Any authenticated user (scope depends on role):
    GET    /projects/list           — lean id/name/files project list, not
                                       paginated: every tenant project for
                                       admin/super_admin, owned/assigned
                                       projects only for a Member; optionally
                                       filtered by backlog-generation stage

super_admin note
────────────────
super_admin's role is platform-level administration (tenant/user/role/
invitation/observability management) plus a cross-tenant project LIST and
SUMMARY view (``GET /projects/all``, ``GET /projects/dashboard/stats``) —
NOT individual project ownership or content. super_admin cannot create,
view, update, or delete a single project, and has no access to any
project's content (sources, modules, features, user stories, incremental
updates) — see ``ProjectService._assert_owner_or_admin``/
``assert_project_access`` and ``require_exact_roles`` in ``app/deps.py``.

Design rules
────────────
- Zero business logic in this file — all decisions live in ProjectService.
- Dependencies (UoW, current user, service) are resolved via Depends() in deps.py.
- Annotated aliases declared once at module level; reused across all handlers.
- Exception handling is centralised in app/core/exception_handlers.py.
- Every handler is plain ``def`` — ``ProjectService`` has no genuine async I/O
  (see its module docstring); FastAPI runs these in its threadpool
  automatically, matching ``app/routes/v1/notifications.py``/``users.py``.
  The one exception is ``get_project``, which awaits
  ``ProjectService.get_project`` (genuine Neo4j I/O for the
  all-approved response flags) and so is ``async def``.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.core.constants import ROLE_ADMIN
from app.core.enums.project_progress_filter import ProjectProgressFilter
from app.core.messages import (
    DESC_PROJECT_ALL_TENANT_FILTER,
    DESC_PROJECT_ALL_TENANT_FILTER_TITLE,
    DESC_PROJECT_SEARCH,
    DESC_PROJECT_SEARCH_TITLE,
    DESC_PROJECT_STAGE_FILTER,
    DESC_PROJECT_STAGE_FILTER_TITLE,
    MSG_PROJECT_CREATED,
    MSG_PROJECT_DASHBOARD_STATS_FETCHED,
    MSG_PROJECT_UPDATED,
    SUMMARY_PROJECT_CREATE,
    SUMMARY_PROJECT_DASHBOARD_STATS,
    SUMMARY_PROJECT_DELETE,
    SUMMARY_PROJECT_GET,
    SUMMARY_PROJECT_LIST,
    SUMMARY_PROJECT_LIST_ALL,
    SUMMARY_PROJECT_LIST_SUMMARY,
    SUMMARY_PROJECT_UPDATE,
)
from app.db.unit_of_work import UnitOfWork
from app.deps import (
    get_current_db_user,
    get_project_service,
    get_uow,
    require_exact_roles,
    require_roles,
)
from app.models.postgres.user_model import User
from app.schemas.project_schema import (
    DashboardStatsResponse,
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    ProjectSummaryResponse,
    ProjectUpdate,
)
from app.services.project_service import ProjectService
from app.utils.pagination import PaginationParams
from app.utils.response import ApiResponse

router = APIRouter(prefix="/projects", tags=["Projects"])


# ── Annotated dependency aliases ───────────────────────────────────────────
# Declared once and reused across all handlers so each signature stays
# concise and the injection point is defined in a single place.

CurrentUser = Annotated[User, Depends(get_current_db_user)]
CurrentUow = Annotated[UnitOfWork, Depends(get_uow)]
CurrentPagination = Annotated[PaginationParams, Depends(PaginationParams)]
CurrentService = Annotated[ProjectService, Depends(get_project_service)]
SearchQuery = Annotated[
    str | None,
    Query(
        title=DESC_PROJECT_SEARCH_TITLE,
        description=DESC_PROJECT_SEARCH,
        min_length=1,
        examples=["my project"],
    ),
]
AllTenantIdFilter = Annotated[
    UUID | None,
    Query(
        title=DESC_PROJECT_ALL_TENANT_FILTER_TITLE,
        description=DESC_PROJECT_ALL_TENANT_FILTER,
    ),
]
StageFilter = Annotated[
    ProjectProgressFilter | None,
    Query(
        title=DESC_PROJECT_STAGE_FILTER_TITLE,
        description=DESC_PROJECT_STAGE_FILTER,
    ),
]


# ── Endpoints ──────────────────────────────────────────────────────────────


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary=SUMMARY_PROJECT_CREATE,
    dependencies=[Depends(require_exact_roles(ROLE_ADMIN))],
)
def create_project(
    payload: ProjectCreate,
    current_user: CurrentUser,
    uow: CurrentUow,
    service: CurrentService,
) -> ApiResponse[ProjectResponse]:
    """POST /projects — create a new project owned by the authenticated user.

    Client Admin only — NOT super_admin (project creation is a tenant-scoped
    responsibility, outside super_admin's platform-admin role; see the
    module docstring's "super_admin note"). A Member can only view projects
    a Client Admin assigns them to (see ``POST /projects/{id}/members``),
    not create their own.

    ``payload.tenant_id`` is reserved for a future super_admin-driven
    creation flow but currently always rejected — no caller reachable at
    this endpoint may set it (own tenant, ``current_user.tenant_id``, is
    always used automatically).
    """
    result = service.create_project(
        payload=payload,
        uow=uow,
        owner_id=current_user.id,
        requester_roles=current_user.role_names,
        owner_tenant_id=current_user.tenant_id,
    )
    return ApiResponse.ok(data=result, message=MSG_PROJECT_CREATED)


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_PROJECT_LIST,
)
def list_projects(
    pagination: CurrentPagination,
    current_user: CurrentUser,
    uow: CurrentUow,
    service: CurrentService,
    search: SearchQuery = None,
) -> ApiResponse[ProjectListResponse]:
    """GET /projects — list projects belonging to the authenticated user."""
    result = service.list_projects(
        owner_id=current_user.id,
        skip=pagination.skip,
        limit=pagination.limit,
        search=search,
        uow=uow,
    )
    return ApiResponse.ok(data=result)


@router.get(
    "/dashboard/stats",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_PROJECT_DASHBOARD_STATS,
)
async def get_dashboard_stats(
    current_user: CurrentUser,
    uow: CurrentUow,
    service: CurrentService,
    tenant_id: AllTenantIdFilter = None,
) -> ApiResponse[DashboardStatsResponse]:
    """GET /projects/dashboard/stats — aggregate dashboard statistics, scoped
    to what the caller may see: super_admin gets platform-wide stats across
    every tenant (or one tenant, if ``tenant_id`` is given); a Client Admin
    gets their own tenant's stats (``tenant_id`` ignored); a Member gets
    stats for only the projects they own or are assigned to (``tenant_id``
    ignored).
    """
    result = await service.get_dashboard_stats(
        uow=uow,
        requester_id=current_user.id,
        requester_roles=current_user.role_names,
        requester_tenant_id=current_user.tenant_id,
        tenant_id=tenant_id,
    )
    return ApiResponse.ok(data=result, message=MSG_PROJECT_DASHBOARD_STATS_FETCHED)


@router.get(
    "/all",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_PROJECT_LIST_ALL,
    dependencies=[Depends(require_roles("admin"))],
)
def list_all_projects(
    pagination: CurrentPagination,
    current_user: CurrentUser,
    uow: CurrentUow,
    service: CurrentService,
    search: SearchQuery = None,
    tenant_id: AllTenantIdFilter = None,
) -> ApiResponse[ProjectListResponse]:
    """GET /projects/all — list projects (admin only; scoped to the caller's
    own tenant unless the caller is a super_admin).

    ``tenant_id`` lets a super_admin narrow the view to one tenant (omit it
    to see every tenant); a plain admin's ``tenant_id`` is ignored since they
    are always scoped to their own tenant.
    """
    result = service.list_all_projects(
        skip=pagination.skip,
        limit=pagination.limit,
        search=search,
        uow=uow,
        requester_roles=current_user.role_names,
        requester_tenant_id=current_user.tenant_id,
        tenant_id=tenant_id,
    )
    return ApiResponse.ok(data=result)


@router.get(
    "/list",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_PROJECT_LIST_SUMMARY,
)
def list_projects_summary(
    current_user: CurrentUser,
    uow: CurrentUow,
    service: CurrentService,
    search: SearchQuery = None,
    stage: StageFilter = None,
) -> ApiResponse[list[ProjectSummaryResponse]]:
    """GET /projects/list — lean id/name/files project list, not paginated,
    scoped by role: every tenant project for an admin/super_admin,
    owned/assigned projects only for a Member. ``stage`` optionally narrows
    the result to projects with neither Module/Feature nor User Story
    generated yet (``fresh``), only Module/Feature generated
    (``module_feature_only``), or with User Stories generated
    (``user_story_created``).
    """
    result = service.list_projects_summary(
        uow=uow,
        requester_id=current_user.id,
        requester_roles=current_user.role_names,
        requester_tenant_id=current_user.tenant_id,
        search=search,
        stage=stage,
    )
    return ApiResponse.ok(data=result)


@router.get(
    "/{project_id}",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_PROJECT_GET,
)
async def get_project(
    project_id: UUID,
    current_user: CurrentUser,
    uow: CurrentUow,
    service: CurrentService,
) -> ApiResponse[ProjectResponse]:
    """GET /projects/{project_id} — fetch a single project by ID."""
    result = await service.get_project(
        project_id=project_id,
        requester_id=current_user.id,
        requester_roles=current_user.role_names,
        requester_tenant_id=current_user.tenant_id,
        uow=uow,
    )
    return ApiResponse.ok(data=result)


@router.patch(
    "/{project_id}",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_PROJECT_UPDATE,
)
def update_project(
    project_id: UUID,
    payload: ProjectUpdate,
    current_user: CurrentUser,
    uow: CurrentUow,
    service: CurrentService,
) -> ApiResponse[ProjectResponse]:
    """PATCH /projects/{project_id} — partially update name, description, or status.

    ``payload.tenant_id`` is super_admin-only: it reassigns (or, if sent as
    ``null``, clears) the project's tenant. Any other caller must omit it.
    """
    result = service.update_project(
        project_id=project_id,
        payload=payload,
        requester_id=current_user.id,
        requester_roles=current_user.role_names,
        requester_tenant_id=current_user.tenant_id,
        uow=uow,
    )
    return ApiResponse.ok(data=result, message=MSG_PROJECT_UPDATED)


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary=SUMMARY_PROJECT_DELETE,
)
def delete_project(
    project_id: UUID,
    current_user: CurrentUser,
    uow: CurrentUow,
    service: CurrentService,
) -> None:
    """DELETE /projects/{project_id} — remove a project permanently."""
    service.delete_project(
        project_id=project_id,
        requester_id=current_user.id,
        requester_roles=current_user.role_names,
        requester_tenant_id=current_user.tenant_id,
        uow=uow,
    )
