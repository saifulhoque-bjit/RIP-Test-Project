"""Pydantic v2 schemas for Jira integration and sync."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ── Integration config ─────────────────────────────────────────────────────


def _validate_base_url_scheme(v: str | None) -> str | None:
    if v is None:
        return v
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("jira_base_url must be an absolute URL including http:// or https://")
    return v


class JiraIntegrationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jira_base_url: str = Field(..., min_length=1, max_length=512)
    jira_project_key: str = Field(..., min_length=1, max_length=32)
    jira_board_id: str | None = Field(default=None, max_length=32)
    jira_user_email: str = Field(..., min_length=1, max_length=320)
    api_token: str = Field(
        ..., min_length=1, description="Jira Cloud API token (encrypted at rest)"
    )
    issue_type_name: str = Field(default="Story", max_length=64)
    epic_issue_type_name: str = Field(default="Epic", max_length=64)
    deprecated_transition_id: str | None = Field(default=None, max_length=32)

    _validate_jira_base_url = field_validator("jira_base_url")(_validate_base_url_scheme)


class JiraIntegrationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jira_base_url: str | None = Field(default=None, min_length=1, max_length=512)
    jira_project_key: str | None = Field(default=None, min_length=1, max_length=32)
    jira_board_id: str | None = Field(default=None, max_length=32)
    jira_user_email: str | None = Field(default=None, min_length=1, max_length=320)
    api_token: str | None = Field(
        default=None, min_length=1, description="New Jira API token to rotate (optional)"
    )
    issue_type_name: str | None = Field(default=None, max_length=64)
    epic_issue_type_name: str | None = Field(default=None, max_length=64)
    deprecated_transition_id: str | None = Field(default=None, max_length=32)

    _validate_jira_base_url = field_validator("jira_base_url")(_validate_base_url_scheme)

    @model_validator(mode="after")
    def at_least_one_field(self) -> JiraIntegrationUpdate:
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided.")
        return self


class JiraIntegrationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    jira_base_url: str
    jira_project_key: str
    jira_board_id: str | None = None
    jira_user_email: str
    api_token_hint: str = Field(default="****", description="Token is always masked in responses")
    issue_type_name: str
    epic_issue_type_name: str
    traceability_field_ids: dict | None = None
    deprecated_transition_id: str | None = None
    is_active: bool
    last_synced_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


# ── Connection test ────────────────────────────────────────────────────────


class JiraConnectionTestResponse(BaseModel):
    connected: bool
    display_name: str | None = None
    email: str | None = None
    account_id: str | None = None


# ── Issue types ────────────────────────────────────────────────────────────


class JiraIssueTypeResponse(BaseModel):
    id: str
    name: str
    subtask: bool = False


class JiraIssueTypeListResponse(BaseModel):
    items: list[JiraIssueTypeResponse] = Field(default_factory=list)


# ── Sync preview (Sync Tray) ──────────────────────────────────────────────


class JiraSyncPreviewItem(BaseModel):
    rip_entity_id: UUID
    rip_entity_type: str  # "module" | "feature" | "user_story"
    rip_entity_code: str | None = None
    title: str
    version: int = 1
    change_type: str  # "new" | "changed" | "deprecated"
    flags: list[str] = Field(default_factory=list)


class JiraSyncPreviewResponse(BaseModel):
    new_count: int = 0
    changed_count: int = 0
    deprecated_count: int = 0
    unchanged_count: int = 0
    items: list[JiraSyncPreviewItem] = Field(default_factory=list)


# ── Sync execute ───────────────────────────────────────────────────────────


class JiraAcceptanceCriteria(BaseModel):
    """Gherkin-style acceptance criteria."""

    type: str = Field(..., description="Happy Path, Negative Path, Edge Case")
    given: str
    when: str
    then: str


class JiraNfr(BaseModel):
    """Non-functional requirement attached to a user story."""

    category: str
    requirement: str = ""
    description: str = ""


class JiraUserStory(BaseModel):
    """User story to sync to JIRA."""

    user_story_id: UUID
    user_story_code: str
    title: str
    as_a: str
    i_want_to: str
    so_that: str
    version_id: int | None = None
    is_deleted: bool = False
    test_type: str | None = None
    acceptance_criteria: list[JiraAcceptanceCriteria] = Field(default_factory=list)
    nfrs: list[JiraNfr] = Field(default_factory=list)
    story_points: int | None = None


class JiraFeature(BaseModel):
    """Feature to sync to JIRA as Epic."""

    feature_id: UUID
    feature_code: str
    feature_name: str
    feature_description: str
    user_stories: list[JiraUserStory] = Field(default_factory=list)


class JiraModule(BaseModel):
    """Module to sync to JIRA as Component."""

    module_id: UUID
    module_code: str
    module_name: str
    module_description: str
    features: list[JiraFeature] = Field(default_factory=list)


class JiraSyncExecuteRequest(BaseModel):
    """Request to sync RIP hierarchy to JIRA."""

    model_config = ConfigDict(extra="forbid")

    modules: list[JiraModule] = Field(..., min_length=1, description="RIP modules to sync")


class JiraSyncResultItem(BaseModel):
    """Result of syncing a single RIP entity to JIRA."""

    rip_entity_id: UUID
    rip_entity_type: str  # "module", "feature", "user_story"
    rip_entity_code: str
    title: str
    jira_key: str | None = None  # Component ID, Epic key, or Story key
    jira_id: str | None = None
    status: str  # "created", "updated", "deprecated"


class JiraSyncExecuteResponse(BaseModel):
    """Response from sync execution."""

    created: int
    updated: int
    deprecated: int
    skipped: int = 0
    total_synced: int
    message: str
    created_items: list[JiraSyncResultItem] = Field(default_factory=list)
    updated_items: list[JiraSyncResultItem] = Field(default_factory=list)
    deprecated_items: list[JiraSyncResultItem] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)
    sync_completed_at: datetime | None = None


class JiraSyncTriggerResponse(BaseModel):
    task_id: UUID
    will_release: int
    held: int = 0
    status: str = "queued"


# ── Sync history ───────────────────────────────────────────────────────────


class JiraSyncHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    integration_id: UUID
    project_id: UUID
    status: str
    summary: dict | None = None
    error_details: list | None = None
    started_at: datetime
    completed_at: datetime | None = None
    created_at: datetime


class JiraSyncHistoryListResponse(BaseModel):
    total: int
    skip: int
    limit: int
    items: list[JiraSyncHistoryResponse] = Field(default_factory=list)
