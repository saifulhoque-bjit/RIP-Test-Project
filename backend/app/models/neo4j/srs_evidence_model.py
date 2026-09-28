"""Internal domain models for SRS evidence workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.models.neo4j.group_spec_model import GroupSpecModel


@dataclass(slots=True)
class SRSEvidenceModel:
    """Domain model for SRS evidence linked to a user story."""

    id: str
    module_code: str | None = None
    feature_code: str | None = None
    user_story_code: str | None = None
    user_story_id: str | None = None
    group_spec_id: str | None = None
    l2_id: str | None = None
    file_name: str | None = None
    section_anchor: str | None = None
    section_path: list[str] | None = None
    highlight_type: str | None = None
    target_string: str | None = None
    exact_quote: str | None = None
    line_number: int | None = None
    precision: str | None = None
    evidence_role: str | None = None
    srs_document_type: str | None = None
    ac_ids: list[str] | None = None
    trace_id: str | None = None
    context_snippet: str | None = None
    tier: str | None = None
    group_spec: GroupSpecModel | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
