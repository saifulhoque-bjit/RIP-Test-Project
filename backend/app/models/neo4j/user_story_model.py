"""Internal domain models for user story workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TypedDict
from uuid import UUID

from app.core.constants import INITIAL_ENTITY_VERSION
from app.models.neo4j.module_feature_model import ChangeType, RFPFlaggedItemDict
from app.models.neo4j.srs_evidence_model import SRSEvidenceModel


class UserStorySourceBBoxCoords(TypedDict):
    """Bounding-box pixel/point coordinates for a single region."""

    x: float
    y: float
    w: float
    h: float


class UserStorySourceEntry(TypedDict):
    """One (source_id, page) group of bounding boxes attached to a user story."""

    source_id: str
    fragment_id: str | None
    page: int
    bboxes: list[UserStorySourceBBoxCoords]


class AcceptanceCriterionDict(TypedDict):
    """Structured acceptance criterion in Gherkin-style (Given/When/Then)."""

    type: str  # e.g. "Happy Path", "Negative Path", "Edge Case"
    given: str
    when: str
    then: str
    ac_code: (
        str | None
    )  # RFP: "{user_story_code}.{n}", e.g. "U.S 1.5.1.2"; source-code: copied from `id`
    id: str | None  # source-code pipeline only: its own criterion identifier
    l2_source_ref: str | None  # source-code pipeline only


class UserStoryNFRDict(TypedDict):
    """Normalized non-functional requirement attached to a user story."""

    id: str
    category: str
    description: str
    requirement: str


class TextDiffSpanDict(TypedDict):
    """A single semantic-diff span: the smallest substring whose meaning changed.

    ``before``/``after`` — at least one is populated; both populated means a
    reworded span, ``before`` only means removed, ``after`` only means added.
    """

    before: str | None
    after: str | None


# Nested dict keyed by field name, e.g. {"so_that": [TextDiffSpanDict, ...],
# "acceptance_criteria": {"<ac_code>": {"then": [TextDiffSpanDict, ...]}}} —
# emitted directly by the LLM in this shape (see UserStoryTextDiffs in
# app.schemas.rfp_pipeline_v2_graph_schema), not built by this layer.
TextDiffsDict = dict


@dataclass(slots=True)
class UserStoryModel:
    id: str
    user_story_code: str
    title: str
    description: str | None
    consensus: float
    status: str
    version: int = INITIAL_ENTITY_VERSION
    feature_id: str | None = None
    # Parent Feature's mfu_id and parent Module's mod_code — populated only by
    # the project-scoped detail query, which traverses Module→Feature→UserStory.
    mfu_id: str | None = None
    mod_code: str | None = None
    project_id: UUID | None = None
    # SourceIngestion (Postgres) row that created this user story, or last
    # updated its content via feedback-driven regeneration.
    source_ingestion_id: str | None = None
    as_a: str | None = None
    i_want_to: str | None = None
    so_that: str | None = None
    acceptance_criteria: list[AcceptanceCriterionDict] = field(default_factory=list)
    nfrs: list[UserStoryNFRDict] = field(default_factory=list)
    technical_notes: str | None = None
    story_points: int | None = None
    justification: str | None = None
    incremental_change_type: ChangeType | None = None
    feedback_change_type: ChangeType | None = None
    sources: list[UserStorySourceEntry] | None = field(default_factory=list)
    # Granular L2 source-reference IDs from the source-code pipeline (e.g.
    # "SRS::MFU-001::S5::EVENT-001") — distinct from ``sources`` above, which
    # is the RFP-document {source_id, page, bboxes} evidence shape.
    l2_sources: list[str] = field(default_factory=list)
    screens: list[dict] | None = None
    rfp_flagged_item: RFPFlaggedItemDict | None = None
    text_diffs: TextDiffsDict = field(default_factory=dict)
    source_file_count: int = 0
    srs_evidence: list[SRSEvidenceModel] = field(default_factory=list)
    is_current: bool = True
    is_jira_synced: bool = False
    is_tap_synced: bool = False
    del_reason: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None
