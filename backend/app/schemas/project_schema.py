"""Pydantic schemas for the Project domain.

Separate Create, Update, and Response models enforce strict API boundaries.
``extra='forbid'`` on request schemas prevents mass assignment.

Schema hierarchy
────────────────
ProjectCreate       — POST /projects          (all required fields)
ProjectUpdate       — PUT  /projects/{id}     (all fields optional, partial update)
ProjectResponse     — read representation with aggregated counts
ProjectListResponse — paginated collection wrapper
ProjectSummaryResponse — lean read representation (id, name, files) for the
                          role-scoped, unpaginated ``GET /projects/list``

Status values
─────────────
All status fields reference :class:`~app.core.enums.project_status.ProjectStatus`
so the valid set of values is defined in a single place and reflected
automatically in OpenAPI as an enum.
"""

from __future__ import annotations

from datetime import datetime
import logging
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.enums.llm_provider import LLMProvider
from app.core.enums.project_status import ProjectStatus
from app.core.enums.project_type import ProjectType

_log = logging.getLogger(__name__)


_DESC_PROJECT_TENANT_ASSIGN = (
    "Assign the project to a specific tenant. Super_admin only — Client "
    "Admin and Member callers must omit this field; their own tenant is "
    "used automatically."
)


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4_000)
    llm_provider: LLMProvider = Field(...)
    llm_model: str = Field(..., min_length=1, max_length=255)
    project_type: ProjectType = Field(...)
    tenant_id: UUID | None = Field(default=None, description=_DESC_PROJECT_TENANT_ASSIGN)


class ProjectUpdate(BaseModel):
    """Partial-update schema — every field is optional so callers can send only
    the fields they intend to change (PATCH semantics)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4_000)
    llm_provider: LLMProvider | None = Field(default=None)
    llm_model: str | None = Field(default=None, max_length=255)
    project_type: ProjectType | None = Field(default=None)
    tenant_id: UUID | None = Field(default=None, description=_DESC_PROJECT_TENANT_ASSIGN)

    @model_validator(mode="after")
    def validate_at_least_one_updatable_field(self) -> ProjectUpdate:
        if not self.model_fields_set:
            raise ValueError(
                "At least one field must be provided: name, llm_provider, llm_model, "
                "description, project_type, tenant_id"
            )
        return self


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    code: str
    description: str | None
    llm_provider: str | None
    llm_model: str | None
    # Coerced from a plain ORM string to the typed enum on deserialisation.
    status: ProjectStatus
    project_type: ProjectType | None
    owner_id: UUID | None
    tenant_id: UUID | None = None
    version: int
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime | None = None
    # Backlog-approval gates — only populated (real Neo4j lookup) by the
    # single-project GET; other project responses (create/update/list) leave
    # these at the default False.
    all_modules_approved: bool = Field(
        default=False,
        description="True when every module in the project's backlog is approved.",
    )
    all_features_approved: bool = Field(
        default=False,
        description="True when every feature in the project's backlog is approved.",
    )
    all_user_stories_approved: bool = Field(
        default=False,
        description="True when every user story in the project's backlog is approved.",
    )
    # Aggregated counts — populated by the service layer from PostgreSQL and Neo4j.
    files: int = 0
    user_stories: int = 0
    approved_user_stories: int = 0
    jira_synced_count: int = Field(
        default=0,
        description="Count of this project's user stories synced to Jira.",
    )
    tap_synced_count: int = Field(
        default=0,
        description="Count of this project's user stories synced to TAP.",
    )
    pending_jira_sync: int = Field(
        default=0,
        description="Approved or deleted user stories not yet synced to Jira.",
    )
    pending_tap_sync: int = Field(
        default=0,
        description="Approved or deleted user stories not yet synced to TAP.",
    )
    # Placeholder: will be populated once a team-membership table exists.
    # Returning None instead of 0 signals "not yet implemented" to clients
    # so they can distinguish "zero members" from "feature not available".
    team_members: int | None = Field(
        default=None,
        description="Reserved for future use — team membership feature not yet implemented.",
    )

    @field_validator("project_type", mode="before")
    @classmethod
    def coerce_project_type(cls, v: object) -> ProjectType | None:
        if v is None:
            return None
        try:
            return ProjectType(v)
        except ValueError:
            _log.warning("Unknown project_type %r from DB — defaulting to None", v)
            return None

    @field_validator("status", mode="before")
    @classmethod
    def coerce_status(cls, v: object) -> ProjectStatus:
        """Gracefully handle unknown status strings stored in the DB.

        If the DB row carries a status value not yet in the :class:`ProjectStatus`
        enum (e.g. a value written by a newer service version), return
        ``ProjectStatus.ACTIVE`` as a safe default and log a warning rather than
        raising a ``ValidationError`` that would 500 the entire response.
        """
        try:
            return ProjectStatus(v)
        except ValueError:
            _log.warning("Unknown project status %r from DB — defaulting to ACTIVE", v)
            return ProjectStatus.ACTIVE


class ProjectListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[ProjectResponse]
    total: int
    skip: int
    limit: int


class ProjectSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    files: int = 0


class LongestCompletionProject(BaseModel):
    project_id: UUID | None = None
    project_name: str | None = None
    duration_seconds: float | None = None


class DashboardStatsResponse(BaseModel):
    total_projects: int
    active_projects: int
    active_projects_current_month: int
    total_modules: int
    total_features: int
    total_stories: int
    running_pipelines: int
    avg_pipeline_completion_seconds: float | None = None
    longest_completion_project: LongestCompletionProject | None = None
    pending_jira_sync_count: int = Field(
        default=0,
        description="Approved user stories not yet synced to Jira (status=approved AND is_jira_synced=false), within the caller's scope.",
    )
    pending_tap_sync_count: int = Field(
        default=0,
        description="Approved user stories not yet synced to TAP (status=approved AND is_tap_synced=false), within the caller's scope.",
    )
    approved_user_stories: int = Field(
        default=0, description="Approved user stories within the caller's scope."
    )
