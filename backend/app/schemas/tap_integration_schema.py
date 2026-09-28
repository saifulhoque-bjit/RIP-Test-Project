"""Pydantic v2 schemas for TAP (Test Automation Platform) sync and per-project
integration config.

The push hierarchy (module → feature → user story) is intentionally identical
in shape to the Jira sync payload — the frontend sends the same structure to
either integration. It is duplicated here (rather than imported from the Jira
schema) so the TAP slice stays self-contained: when the TAP contract changes,
changes are confined to this module and ``app/clients/tap_client.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ── Sync push hierarchy (mirrors the Jira sync payload shape) ───────────────


class TapAcceptanceCriteria(BaseModel):
    """Gherkin-style acceptance criteria."""

    type: str = Field(..., description="Happy Path, Negative Path, Edge Case")
    given: str
    when: str
    then: str


class TapUserStory(BaseModel):
    user_story_id: UUID
    user_story_code: str
    title: str
    as_a: str
    i_want_to: str
    so_that: str
    version_id: int | None = Field(default=None)
    is_deleted: bool = Field(default=False)
    test_type: str | None = Field(default=None)
    acceptance_criteria: list[TapAcceptanceCriteria] = Field(default_factory=list)
    story_points: int | None = None


class TapFeature(BaseModel):
    feature_id: UUID
    feature_code: str
    feature_name: str
    feature_description: str
    user_stories: list[TapUserStory] = Field(default_factory=list)


class TapModule(BaseModel):
    module_id: UUID
    module_code: str
    module_name: str
    module_description: str
    features: list[TapFeature] = Field(default_factory=list)


class TapSyncExecuteRequest(BaseModel):
    """Request to push the RIP hierarchy to TAP."""

    model_config = ConfigDict(extra="forbid")

    modules: list[TapModule] = Field(..., min_length=1, description="RIP modules to push")


class TapSyncResultItem(BaseModel):
    rip_entity_id: UUID
    rip_entity_type: str  # "module" | "feature" | "user_story"
    rip_entity_code: str
    title: str
    tap_entity_id: str | None = None
    status: str  # "created" | "updated" | "skipped"


class TapSyncExecuteResponse(BaseModel):
    """Response from staging a TAP sync run. RIP does not push the hierarchy
    inline — it stages the payload and notifies TAP with ``sync_id`` +
    ``pull_url``; TAP fetches the data on its own schedule and later
    acknowledges it via ``POST /sync/{sync_id}/ack``, echoing this same
    ``sync_id`` back in the URL."""

    sync_id: UUID
    pull_url: str | None = Field(
        default=None,
        description="URL TAP calls (guarded by the shared secret) to fetch the staged hierarchy",
    )
    created: int
    updated: int
    skipped: int = 0
    total_synced: int
    message: str
    created_items: list[TapSyncResultItem] = Field(default_factory=list)
    updated_items: list[TapSyncResultItem] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)
    sync_completed_at: datetime | None = None


class TapSyncDataResponse(BaseModel):
    """Payload served to TAP when it pulls a staged sync via its ``pull_url``."""

    sync_id: UUID
    modules: list[dict] = Field(default_factory=list)


# ── Inbound acknowledgement (TAP → RIP) ─────────────────────────────────────


class TapAckRequest(BaseModel):
    """Job-level payload TAP sends to the inbound ``/sync/{sync_id}/ack``
    callback. ``sync_id`` itself travels in the URL path (see
    ``receive_ack``), not in this body."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(..., description="TAP's own job id for this run, for audit/logging only")
    order_index: int = 0
    status: Literal["COMPLETED", "FAILED"]
    total_requirements: int = 0
    processed_requirements: int = 0
    failed_requirements: int = 0
    total_requirement_ids: list[UUID] = Field(
        default_factory=list,
        description="RIP entity ids (module/feature/user_story) staged in this job/batch.",
    )
    processed_requirement_ids: list[UUID] = Field(
        default_factory=list,
        description="RIP entity ids TAP successfully processed — is_tap_synced is flipped true for exactly these.",
    )
    failed_requirement_ids: list[UUID] = Field(
        default_factory=list,
        description="RIP entity ids TAP failed to process — recorded for audit, is_tap_synced is not flipped.",
    )
    error_details: str = ""


class TapAckResponse(BaseModel):
    ack_history_id: UUID
    job_id: str
    status: str
    entities_synced: int = 0
    message: str


# ── Per-project integration config (CRUD) ────────────────────────────────────


# ``app_client_name`` is deliberately absent from both payloads below: RIP
# always identifies to TAP as TAP_APP_CLIENT_NAME, so accepting it from the
# client would let a caller save a value that could never authenticate.
# ``extra="forbid"`` means an older client still sending it gets a clear 422
# rather than having it silently ignored.


class TapIntegrationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(..., min_length=1, max_length=512)
    api_key: str = Field(..., min_length=1)
    client_id: str = Field(..., min_length=1, max_length=256)


class TapIntegrationUpdate(BaseModel):
    """Partial update of an existing integration.

    Every field is optional so the client can edit one setting without
    re-sending the rest. ``api_key`` in particular is omitted whenever the
    user does not retype it — the stored key is reused (and re-verified)
    instead, which is why it is never echoed back by the API.
    """

    model_config = ConfigDict(extra="forbid")

    base_url: str | None = Field(default=None, min_length=1, max_length=512)
    api_key: str | None = Field(default=None, min_length=1)
    client_id: str | None = Field(default=None, min_length=1, max_length=256)


class TapIntegrationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    base_url: str
    # Echoed as the fixed constant so the UI can show it read-only without
    # hardcoding the value client-side. Not stored per project.
    app_client_name: str
    # The raw key is never returned — only a short masked hint.
    api_key_hint: str
    client_id: str
    is_active: bool
    last_verified_at: datetime | None
    created_at: datetime
    updated_at: datetime
