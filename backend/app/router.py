"""Central router — registers all route modules under /api/v1."""

from __future__ import annotations

from fastapi import APIRouter

from app.core.error_response import ErrorResponse
from app.routes.v1.activity_logs import router as activity_logs_router
from app.routes.v1.auth import router as auth_router
from app.routes.v1.export import router as export_router
from app.routes.v1.feedback_updates import router as feedback_updates_router
from app.routes.v1.fragments import router as fragments_router
from app.routes.v1.incremental_updates import router as incremental_updates_router
from app.routes.v1.invitations import router as invitations_router
from app.routes.v1.jira_integrations import router as jira_integrations_router
from app.routes.v1.module_features import router as module_features_router
from app.routes.v1.notifications import router as notifications_router
from app.routes.v1.observability import router as observability_router
from app.routes.v1.project_members import router as project_members_router
from app.routes.v1.project_tasks import router as project_tasks_router, tasks_router as tasks_router
from app.routes.v1.projects import router as projects_router
from app.routes.v1.roles import permissions_router, roles_router
from app.routes.v1.settings import router as settings_router
from app.routes.v1.source_ingestion_pipelines import router as source_ingestion_pipelines_router
from app.routes.v1.sources import router as sources_router
from app.routes.v1.tap_integrations import router as tap_integrations_router
from app.routes.v1.tenant_invitations import router as tenant_invitations_router
from app.routes.v1.tenants import router as tenants_router
from app.routes.v1.updates import router as updates_router
from app.routes.v1.user_stories import router as user_stories_router
from app.routes.v1.users import router as users_router

# Shared error-response schemas applied to every route in the API.
# Swagger / ReDoc will render these models for each listed status code.
_ERROR_RESPONSES: dict = {
    400: {"model": ErrorResponse, "description": "Validation / bad-request error"},
    401: {"model": ErrorResponse, "description": "Authentication required"},
    403: {"model": ErrorResponse, "description": "Insufficient permissions"},
    404: {"model": ErrorResponse, "description": "Resource not found"},
    409: {"model": ErrorResponse, "description": "Conflict with current state"},
    422: {"model": ErrorResponse, "description": "Request body / query-parameter validation error"},
    500: {"model": ErrorResponse, "description": "Unexpected server error"},
    502: {"model": ErrorResponse, "description": "Upstream service error (e.g. AWS Cognito)"},
}

v1_router = APIRouter(prefix="/api/v1", responses=_ERROR_RESPONSES)

v1_router.include_router(auth_router)
v1_router.include_router(users_router)
v1_router.include_router(tenants_router)
v1_router.include_router(tenant_invitations_router)
v1_router.include_router(invitations_router)
v1_router.include_router(projects_router)
v1_router.include_router(project_members_router)
v1_router.include_router(project_tasks_router)
v1_router.include_router(roles_router)
v1_router.include_router(permissions_router)
v1_router.include_router(tasks_router)
v1_router.include_router(source_ingestion_pipelines_router)
v1_router.include_router(settings_router)
v1_router.include_router(sources_router)
v1_router.include_router(fragments_router)
v1_router.include_router(module_features_router)
v1_router.include_router(observability_router)
v1_router.include_router(user_stories_router)
v1_router.include_router(incremental_updates_router)
v1_router.include_router(feedback_updates_router)
v1_router.include_router(updates_router)
v1_router.include_router(activity_logs_router)
v1_router.include_router(notifications_router)
v1_router.include_router(jira_integrations_router)  # Project-scoped integration endpoints
v1_router.include_router(tap_integrations_router)  # Project-scoped TAP integration endpoints
v1_router.include_router(export_router)  # Project-scoped export endpoint
