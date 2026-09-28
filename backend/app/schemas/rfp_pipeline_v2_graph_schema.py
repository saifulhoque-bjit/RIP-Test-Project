import json as _json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ─── Shared ───────────────────────────────────────────────────────────────────


class BBoxSchema(BaseModel):
    x: float
    y: float
    w: float
    h: float


class BBoxEntrySchema(BaseModel):
    """A single bounding box with its associated fragment."""

    fragment_id: str
    bbox: BBoxSchema


class PageSchema(BaseModel):
    """All bounding boxes on a specific page within a source file."""

    page: int
    bboxes: list[BBoxEntrySchema]


class SourceRefSchema(BaseModel):
    source_id: str
    pages: list[PageSchema]

    @field_validator("pages")
    @classmethod
    def _merge_duplicate_pages(cls, v):
        """Silently merge duplicate page entries instead of crashing."""
        seen: dict[int, list] = {}
        order: list[int] = []
        for pg in v:
            if pg.page not in seen:
                seen[pg.page] = list(pg.bboxes)
                order.append(pg.page)
            else:
                seen[pg.page].extend(pg.bboxes)
        return [PageSchema(page=p, bboxes=seen[p]) for p in order]


def _merge_duplicate_sources(v: list) -> list:
    """
    If the LLM emits two SourceRefSchema entries with the same source_id,
    merge their pages lists (which triggers the page-level merge via SourceRefSchema validator).
    Must be defined AFTER SourceRefSchema to avoid forward reference.
    """
    seen: dict[str, list] = {}
    order: list[str] = []
    for src in v:
        sid = src.source_id
        if sid not in seen:
            seen[sid] = list(src.pages)
            order.append(sid)
        else:
            seen[sid].extend(src.pages)
    return [SourceRefSchema(source_id=sid, pages=seen[sid]) for sid in order]


# ─── 2A: Module / Feature Output ─────────────────────────────────────────────


class FunctionSchema(BaseModel):
    fun_code: str
    name: str
    description: str

    @field_validator("fun_code")
    @classmethod
    def _validate_fun_code(cls, v):
        # e.g. 1.1.1, 2.3.4
        if not re.fullmatch(r"[1-9]\d*\.[1-9]\d*\.[1-9]\d*", v):
            raise ValueError(
                f"fun_code must be in format '1.1.1' (no leading zeros, no zero indices): got '{v}'"
            )
        return v


class FeatureSchema(BaseModel):
    fea_code: str
    name: str
    description: str
    functions: list[FunctionSchema]
    sources: list[SourceRefSchema]

    @field_validator("fea_code", mode="before")
    @classmethod
    def _validate_fea_code(cls, v):
        v = str(v)
        if not re.fullmatch(r"[1-9]\d*\.[1-9]\d*", v):
            raise ValueError(
                f"fea_code must be in format '1.1' (no prefix, no zero indices): got '{v}'"
            )
        return v

    @field_validator("sources")
    @classmethod
    def _merge_sources(cls, v):
        return _merge_duplicate_sources(v)


class FeatureModuleSchema(BaseModel):
    mod_code: str
    name: str
    description: str
    features: list[FeatureSchema]

    @field_validator("mod_code", mode="before")
    @classmethod
    def _validate_mod_code(cls, v):
        v = str(v)
        # e.g. 1, 2, 3 — bare integer, no "M" prefix, no zero
        if not re.fullmatch(r"[1-9]\d*", v):
            raise ValueError(f"mod_code must be a bare positive integer like '1', '2': got '{v}'")
        return v


class BusinessRequirementSchema(BaseModel):
    id: str
    title: str
    mapped_features: list[str]
    category: str
    description: str
    rationale: str
    detailed_specification: list[str]
    success_criteria_kpis: list[str]


class BRModuleSchema(BaseModel):
    mod_code: str
    name: str
    requirements: list[BusinessRequirementSchema]

    @field_validator("mod_code", mode="before")
    @classmethod
    def _validate_mod_code(cls, v):
        v = str(v)  # coerce int → str BEFORE type check
        if not re.fullmatch(r"[1-9]\d*", v):
            raise ValueError(f"mod_code must be a bare positive integer like '1', '2': got '{v}'")
        return v


def _coerce_json_string_to_list(v):
    """If the LLM returns a list field as a JSON-encoded string, decode it."""
    if isinstance(v, str):
        try:
            parsed = _json.loads(v)
            if isinstance(parsed, list):
                return parsed
        except (_json.JSONDecodeError, ValueError):
            pass
    return v


class ModuleFeatureOutput(BaseModel):
    feature_inventory: list[FeatureModuleSchema]
    business_requirements: list[BRModuleSchema]
    exclusions: list[str]

    @field_validator("feature_inventory", "business_requirements", "exclusions", mode="before")
    @classmethod
    def _coerce_lists(cls, v):
        return _coerce_json_string_to_list(v)


# ─── 2B: Agile Backlog Output ─────────────────────────────────────────────────


class PersonaSchema(BaseModel):
    persona: str
    description: str


class AcceptanceCriterionSchema(BaseModel):
    type: str  # "Happy Path", "Negative Path", "Edge Case"
    given: str
    when: str
    then: str
    ac_code: str | None = None
    # Stable identifier for this specific criterion, format "{user_story_code}.{n}"
    # (e.g. "U.S 1.5.1.2" — the 2nd AC of story U.S 1.5.1), mirroring fun_code's
    # "{feature_code}.{n}" pattern. Nullable so ACs generated before this field
    # existed still validate — each pipeline (2B, story patch, incremental)
    # backfills it the first time it touches an AC that doesn't have one yet.

    @field_validator("ac_code")
    @classmethod
    def _validate_ac_code_format(cls, v):
        if v is None:
            return v
        if not re.fullmatch(r"U\.S [1-9]\d*\.[1-9]\d*\.[1-9]\d*\.[1-9]\d*", v):
            raise ValueError(
                f"ac_code must be in format 'U.S 1.1.1.1' (no zero indices): got '{v}'"
            )
        return v


class StoryNFRSchema(BaseModel):
    """An NFR embedded directly inside a user story.

    Canonical field is `requirement`. If an older caller supplies
    `description` instead (e.g. incremental pipeline, test fixtures),
    it is promoted to `requirement` before validation. Output only ever
    contains `requirement` — no duplicate field is written back out.
    """

    model_config = {"extra": "ignore"}

    id: str
    category: str
    requirement: str

    @model_validator(mode="before")
    @classmethod
    def _promote_description(cls, data):
        if isinstance(data, dict) and not data.get("requirement") and data.get("description"):
            data = {**data, "requirement": data["description"]}
        return data


class StorySchema(BaseModel):
    user_story_id: str | None
    user_story_code: str
    title: str
    as_a: str
    i_want_to: str
    so_that: str
    acceptance_criteria: list[AcceptanceCriterionSchema]
    technical_notes: str
    story_points: int = Field(..., ge=1, le=8)
    nfrs: list[StoryNFRSchema] = Field(default_factory=list)  # NFRs relevant to this story
    sources: list[SourceRefSchema]

    @field_validator("story_points")
    @classmethod
    def _validate_fibonacci(cls, v):
        if v not in (1, 2, 3, 5, 8):
            raise ValueError(f"story_points must be a Fibonacci number (1, 2, 3, 5, 8): got {v}")
        return v

    @field_validator("user_story_code", mode="before")
    @classmethod
    def _validate_user_story_code(cls, v):
        v = str(v)
        if not re.fullmatch(r"U\.S [1-9]\d*\.[1-9]\d*\.[1-9]\d*", v):
            raise ValueError(
                f"user_story_code must be in format 'U.S 1.1.1' (no zero indices): got '{v}'"
            )
        return v

    @field_validator("sources")
    @classmethod
    def _merge_sources(cls, v):
        return _merge_duplicate_sources(v)

    @model_validator(mode="after")
    def _validate_ac_code_prefix(self):
        for ac in self.acceptance_criteria:
            if ac.ac_code is not None and not ac.ac_code.startswith(self.user_story_code + "."):
                raise ValueError(
                    f"acceptance_criteria ac_code '{ac.ac_code}' does not belong to parent "
                    f"user_story_code '{self.user_story_code}'"
                )
        return self


class EpicSchema(BaseModel):
    epic_code: str
    feature_id: str
    stories: list[StorySchema]


class AgileBacklogOutput(BaseModel):
    persona_glossary: list[PersonaSchema]
    epics: list[EpicSchema]

    @field_validator("persona_glossary", "epics", mode="before")
    @classmethod
    def _coerce_lists(cls, v):
        return _coerce_json_string_to_list(v)


# ─── 2B: Critic Structured Review ─────────────────────────────────────────────


class FlaggedItemSchema(BaseModel):
    entity_id: str  # "U.S 1.1.1" (story), "1.1" (epic), "7.2" (missing_epic fea_code)
    entity_type: Literal["story", "epic", "missing_epic"]
    issue: str  # what's wrong, naming exact values per the critic's criteria
    suggested_fix: str  # imperative instruction for the generator


class CriticReviewSchema(BaseModel):
    status: Literal["PASS", "FAIL"]
    flagged_items: list[FlaggedItemSchema] = Field(default_factory=list)
    summary: str  # 1-2 sentence overall verdict

    @field_validator("flagged_items", mode="before")
    @classmethod
    def _coerce_lists(cls, v):
        return _coerce_json_string_to_list(v)


# ─── Graph Return Schemas ─────────────────────────────────────────────────────


class ModuleFeatureResult(BaseModel):
    output: ModuleFeatureOutput
    status: str
    ai_feedback: str
    iteration: int


class GenerationMetadataSchema(BaseModel):
    final_status: Literal["PASS", "FAIL_CORRECTION_EXHAUSTED"]
    iteration_count: int
    flagged_items: list[FlaggedItemSchema] = Field(default_factory=list)
    flagged_story_ids: list[str] = Field(default_factory=list)
    summary: str


class AgileBacklogResult(BaseModel):
    output: AgileBacklogOutput
    status: str
    ai_feedback: str
    iteration: int
    generation_metadata: GenerationMetadataSchema | None = (
        None  # new, optional — nothing downstream breaks
    )


# ─── Story Patch: Input models ────────────────────────────────────────────────


class SelectionFeedback(BaseModel):
    """A highlighted excerpt from a story with a targeted comment."""

    selected_text: str
    selected_feedback: str


class StoryFeedbackItem(BaseModel):
    story: StorySchema
    overall_feedback: str | None = None
    specific_feedback: list[SelectionFeedback] | None = None


class SiblingStory(BaseModel):
    """Lightweight sibling — enough for scope awareness, not full story weight."""

    user_story_code: str
    title: str
    i_want_to: str
    so_that: str


class FeatureContext(BaseModel):
    """One parent feature and its siblings, scoped to what the LLM needs."""

    fea_code: str  # mapping key — LLM links stories to this via user_story_code prefix
    name: str
    description: str
    sibling_stories: list[SiblingStory]


# ─── Story Patch: Output model ────────────────────────────────────────────────


class StoryPatch(BaseModel):
    revised_stories: list[StorySchema]

    @field_validator("revised_stories", mode="before")
    @classmethod
    def _coerce_lists(cls, v):
        return _coerce_json_string_to_list(v)


# ─── Incremental Update Output ────────────────────────────────────────────


# ── Shared incremental node schemas ───────────────────────────────────────────


class TextDiffSpanSchema(BaseModel):
    """
    A single semantic-diff span self-annotated by the generator: the smallest
    verbatim substring whose *meaning* changed, for a node that has `changed: true`.

    Purely additive / presentational — the frontend locates and highlights each
    span via exact string match against the node's old vs. new field values.

    - Rewording with no meaning change → no entry at all for that span.
    - A span added fresh (no prior text) → `before: null`, `after: "<new text>"`.
    - A span removed outright → `before: "<old text>"`, `after: null`.
    - A span reworded with a real meaning change → both populated.
    """

    before: str | None = None
    after: str | None = None

    @model_validator(mode="after")
    def _validate_before_after(self):
        if self.before is None and self.after is None:
            raise ValueError("before and after cannot both be null")
        if self.before is not None and self.after is not None and self.before == self.after:
            raise ValueError("before and after must differ — identical strings are not a diff")
        return self


def _coerce_json_string_to_dict(v):
    """If the LLM returns a dict/object field as a JSON-encoded string, decode it."""
    if isinstance(v, str):
        try:
            parsed = _json.loads(v)
            if isinstance(parsed, dict):
                return parsed
        except (_json.JSONDecodeError, ValueError):
            pass
    return v


class AcceptanceCriterionFieldDiffs(BaseModel):
    """Diff spans for one specific acceptance criterion, keyed by leaf field."""

    model_config = ConfigDict(extra="forbid")
    given: list[TextDiffSpanSchema] = Field(default_factory=list)
    when: list[TextDiffSpanSchema] = Field(default_factory=list)
    then: list[TextDiffSpanSchema] = Field(default_factory=list)


class FunctionFieldDiffs(BaseModel):
    """Diff spans for one specific function, keyed by leaf field."""

    model_config = ConfigDict(extra="forbid")
    name: list[TextDiffSpanSchema] = Field(default_factory=list)
    description: list[TextDiffSpanSchema] = Field(default_factory=list)


class UserStoryTextDiffs(BaseModel):
    """
    text_diffs container for a user story node. Every key is optional — only
    include a key for a field that actually has a meaning-changed span; an
    unmentioned field had no diff-worthy change. `extra="forbid"` enforces the
    key allowlist structurally: a non-canonical key is a schema validation
    error, not just a prompt instruction.

    `acceptance_criteria` is keyed by `ac_code` (e.g. "U.S 1.5.1.2") — the
    stable identifier assigned when that criterion was created, NOT its
    position in the list. Only include a code for a criterion that actually
    has a diff; within it, only include the given/when/then sub-key(s) that
    changed.
    """

    model_config = ConfigDict(extra="forbid")
    title: list[TextDiffSpanSchema] = Field(default_factory=list)
    i_want_to: list[TextDiffSpanSchema] = Field(default_factory=list)
    so_that: list[TextDiffSpanSchema] = Field(default_factory=list)
    technical_notes: list[TextDiffSpanSchema] = Field(default_factory=list)
    acceptance_criteria: dict[str, AcceptanceCriterionFieldDiffs] = Field(default_factory=dict)

    @field_validator("acceptance_criteria", mode="before")
    @classmethod
    def _coerce_ac_diffs(cls, v):
        return _coerce_json_string_to_dict(v)


class FeatureTextDiffs(BaseModel):
    """
    text_diffs container for a feature node. `functions` is keyed by `fun_code`
    (e.g. "1.5.2") — the stable identifier for that function, not its position
    in the list. Same optional-key / extra="forbid" rules as UserStoryTextDiffs.

    Key is `description` (not `feature_description`) — since text_diffs is
    already nested inside this specific feature node, the parser already knows
    it's a feature's description from tree position alone; no need to repeat
    that in the key name, same reasoning as ModuleTextDiffs below.
    """

    model_config = ConfigDict(extra="forbid")
    description: list[TextDiffSpanSchema] = Field(default_factory=list)
    functions: dict[str, FunctionFieldDiffs] = Field(default_factory=dict)

    @field_validator("functions", mode="before")
    @classmethod
    def _coerce_function_diffs(cls, v):
        return _coerce_json_string_to_dict(v)


class ModuleTextDiffs(BaseModel):
    """
    text_diffs container for a module node — only one describable field.
    Key is `description` (not `module_description`) for the same reason as
    FeatureTextDiffs: nesting position already tells the parser this is a
    module, so the key doesn't need to repeat it.
    """

    model_config = ConfigDict(extra="forbid")
    description: list[TextDiffSpanSchema] = Field(default_factory=list)


class IncrementalFunctionNodeSchema(BaseModel):
    """A function entry inside a feature node. No sources field."""

    fun_code: str | None = None
    name: str
    description: str

    @field_validator("fun_code")
    @classmethod
    def _validate_fun_code(cls, v):
        if v is None:
            return v
        if not re.fullmatch(r"[1-9]\d*\.[1-9]\d*\.[1-9]\d*", v):
            raise ValueError(f"fun_code must be in format '1.1.1': got '{v}'")
        return v


class IncrementalUserStoryNodeSchema(BaseModel):
    """A user story node inside the updates/adds tree."""

    item_code: str | None = None
    # Per-pass tracking id (e.g. "U3", "A5") assigned by the generator so the
    # critic — and this pass's report only — can reference this node even when
    # its real user_story_code/user_story_id is null (SUBSET-mode adds). Never
    # persisted, never shown to end users, discarded after this generation pass.
    user_story_id: str | None = None
    user_story_code: str | None = None
    title: str
    as_a: str
    i_want_to: str
    so_that: str
    acceptance_criteria: list[AcceptanceCriterionSchema]
    story_points: int = Field(..., ge=1, le=8)
    technical_notes: str
    nfrs: list[StoryNFRSchema] = Field(default_factory=list)
    changed: bool
    justification: str
    source_reference: str | None = None
    flag_ids: list[str] | None = None
    sources: list[SourceRefSchema] | None = None
    text_diffs: UserStoryTextDiffs = Field(default_factory=UserStoryTextDiffs)

    @field_validator("user_story_code", mode="before")
    @classmethod
    def _validate_code(cls, v):
        if v is None:
            return v
        v = str(v)
        if not re.fullmatch(r"U\.S [1-9]\d*\.[1-9]\d*\.[1-9]\d*", v):
            raise ValueError(f"user_story_code must be in format 'U.S 1.1.1': got '{v}'")
        return v

    @model_validator(mode="after")
    def _validate_ac_code_prefix(self):
        if self.user_story_code is not None:
            for ac in self.acceptance_criteria:
                if ac.ac_code is not None and not ac.ac_code.startswith(self.user_story_code + "."):
                    raise ValueError(
                        f"acceptance_criteria ac_code '{ac.ac_code}' does not belong to parent "
                        f"user_story_code '{self.user_story_code}'"
                    )
        return self

    @field_validator("acceptance_criteria", "nfrs", mode="before")
    @classmethod
    def _coerce_lists(cls, v):
        if v is None:
            return []
        return _coerce_json_string_to_list(v)

    @field_validator("sources", mode="before")
    @classmethod
    def _coerce_sources_string(cls, v):
        if v is None:
            return v
        return _coerce_json_string_to_list(v)

    @field_validator("sources")
    @classmethod
    def _merge_sources(cls, v):
        # Runs after `sources` is already parsed into list[SourceRefSchema] —
        # _merge_duplicate_sources needs real instances (attribute access),
        # not the raw dicts a mode="before" validator would still be holding.
        if v is None:
            return v
        return _merge_duplicate_sources(v)


class IncrementalFeatureNodeSchema(BaseModel):
    """A feature node inside the updates/adds tree."""

    item_code: str | None = None  # per-pass tracking id, see IncrementalUserStoryNodeSchema
    feature_code: str | None = None
    feature_id: str | None = None
    feature_name: str
    changed: bool
    justification: str
    source_reference: str | None = None
    flag_ids: list[str] | None = None
    sources: list[SourceRefSchema] | None = None
    functions: list[IncrementalFunctionNodeSchema] | None = None
    user_stories: list[IncrementalUserStoryNodeSchema] = Field(default_factory=list)
    text_diffs: FeatureTextDiffs = Field(default_factory=FeatureTextDiffs)

    @field_validator("feature_code", mode="before")
    @classmethod
    def _validate_code(cls, v):
        if v is None:
            return v
        v = str(v)
        if not re.fullmatch(r"[1-9]\d*\.[1-9]\d*", v):
            raise ValueError(f"feature_code must be in format '1.1': got '{v}'")
        return v

    @field_validator("sources", mode="before")
    @classmethod
    def _coerce_sources_string(cls, v):
        if v is None:
            return v
        return _coerce_json_string_to_list(v)

    @field_validator("sources")
    @classmethod
    def _merge_sources(cls, v):
        if v is None:
            return v
        return _merge_duplicate_sources(v)

    @field_validator("functions", "user_stories", mode="before")
    @classmethod
    def _coerce_lists(cls, v):
        if v is None:
            return v
        return _coerce_json_string_to_list(v)


class IncrementalModuleNodeSchema(BaseModel):
    """A module node inside the updates/adds tree."""

    item_code: str | None = None  # per-pass tracking id, see IncrementalUserStoryNodeSchema
    module_code: str | None = None
    module_name: str
    module_id: str | None = None
    changed: bool
    justification: str
    source_reference: str | None = None
    flag_ids: list[str] | None = None
    features: list[IncrementalFeatureNodeSchema] = Field(default_factory=list)
    text_diffs: ModuleTextDiffs = Field(default_factory=ModuleTextDiffs)

    @field_validator("module_code", mode="before")
    @classmethod
    def _validate_code(cls, v):
        if v is None:
            return v
        v = str(v)
        if not re.fullmatch(r"[1-9]\d*", v):
            raise ValueError(f"module_code must be a bare positive integer like '1': got '{v}'")
        return v

    @field_validator("features", mode="before")
    @classmethod
    def _coerce_lists(cls, v):
        return _coerce_json_string_to_list(v)


# ── Delete entry ───────────────────────────────────────────────────────────────


class IncrementalDeleteEntrySchema(BaseModel):
    """A single delete entry in the flat deletes list."""

    item_code: str | None = None  # per-pass tracking id, see IncrementalUserStoryNodeSchema
    uuid: str
    user_story_code: str | None = None
    type: str  # "module" | "feature" | "user_story"
    justification: str
    source_reference: str
    flag_ids: list[str] | None = None
    sources: list[SourceRefSchema] | None = None

    @field_validator("sources", mode="before")
    @classmethod
    def _coerce_sources_string(cls, v):
        if v is None:
            return v
        return _coerce_json_string_to_list(v)

    @field_validator("sources")
    @classmethod
    def _merge_sources(cls, v):
        if v is None:
            return v
        return _merge_duplicate_sources(v)


# ── Flags ──────────────────────────────────────────────────────────────────────


class IncrementalFlagSchema(BaseModel):
    flag_id: str
    type: Literal["ambiguous", "conflict", "source_gap"]
    description: str
    source_reference: str
    # source_reference is required (not Optional) — every flag, including
    # source_gap, must cite the fragment that prompted it. There is no valid
    # "flag with no reason" case; a fragment-less flag should not exist at all.
    related_item_ids: list[str] = Field(default_factory=list)
    recommendation: str


# ── Source enrichment entries ──────────────────────────────────────────────────


class IncrementalFeatureEnrichmentSchema(BaseModel):
    """A source enrichment entry for a feature."""

    item_code: str | None = None  # per-pass tracking id, see IncrementalUserStoryNodeSchema
    feature_id: str
    feature_code: str
    source_reference: str
    reasoning: str
    new_sources: list[SourceRefSchema]

    @field_validator("new_sources", mode="before")
    @classmethod
    def _coerce_sources_string(cls, v):
        return _coerce_json_string_to_list(v)

    @field_validator("new_sources")
    @classmethod
    def _merge_sources(cls, v):
        return _merge_duplicate_sources(v)


class IncrementalUserStoryEnrichmentSchema(BaseModel):
    """A source enrichment entry for a user story."""

    item_code: str | None = None  # per-pass tracking id, see IncrementalUserStoryNodeSchema
    user_story_id: str
    user_story_code: str
    source_reference: str
    reasoning: str
    new_sources: list[SourceRefSchema]

    @field_validator("new_sources", mode="before")
    @classmethod
    def _coerce_sources_string(cls, v):
        return _coerce_json_string_to_list(v)

    @field_validator("new_sources")
    @classmethod
    def _merge_sources(cls, v):
        return _merge_duplicate_sources(v)


# ── Top-level output ───────────────────────────────────────────────────────────


class IncrementalUpdateOutput(BaseModel):
    source_summary: str
    persona_glossary_additions: list[PersonaSchema] = Field(default_factory=list)
    updates: list[IncrementalModuleNodeSchema] = Field(default_factory=list)
    adds: list[IncrementalModuleNodeSchema] = Field(default_factory=list)
    deletes: list[IncrementalDeleteEntrySchema] = Field(default_factory=list)
    source_enrichments: list[
        IncrementalFeatureEnrichmentSchema | IncrementalUserStoryEnrichmentSchema
    ] = Field(default_factory=list)
    flags: list[IncrementalFlagSchema] = Field(default_factory=list)
    no_changes_explanation: str | None = None
    # Populated by the generator ONLY when updates/adds/deletes/source_enrichments
    # are all empty — a polite, plain-language explanation for the end user of why
    # nothing was proposed (e.g. notes unrelated to this backlog, already reflected,
    # too ambiguous to act on). Must be null whenever any of those arrays is non-empty.

    @field_validator(
        "persona_glossary_additions",
        "updates",
        "adds",
        "deletes",
        "source_enrichments",
        "flags",
        mode="before",
    )
    @classmethod
    def _coerce_lists(cls, v):
        return _coerce_json_string_to_list(v)

    @model_validator(mode="after")
    def _validate_no_text_diffs_in_adds(self):
        """
        text_diffs is an updates-only concept: a brand-new item in `adds` has no
        prior version to diff against, and its own fields already ARE the full
        new content — populating text_diffs there is pure duplication, not a
        diff. Reject it outright rather than silently accepting wasted tokens.
        """
        offenders = []

        def check_story(s, path):
            if s.text_diffs != UserStoryTextDiffs():
                offenders.append(f"user_story {s.user_story_code} ({path})")

        def check_feature(f, path):
            if f.text_diffs != FeatureTextDiffs():
                offenders.append(f"feature {f.feature_code} ({path})")
            for s in f.user_stories:
                check_story(s, path)

        def check_module(m, path):
            if m.text_diffs != ModuleTextDiffs():
                offenders.append(f"module {m.module_code} ({path})")
            for f in m.features:
                check_feature(f, path)

        for m in self.adds:
            check_module(m, "adds")

        if offenders:
            raise ValueError(
                "text_diffs must be empty on every node under `adds` — found non-empty "
                "text_diffs on: " + "; ".join(offenders)
            )
        return self


# ─── Incremental Critic Structured Review ────────────────────────────────────


class IncrementalFlaggedItemSchema(BaseModel):
    entity_id: str
    # The item's `item_code` (e.g. "U3", "A1", "D2", "E1") for module/feature/
    # user_story/delete/source_enrichment issues. For an AC-level issue, this is
    # still the PARENT story's item_code — name the specific ac_code (or, if the
    # story itself is new in SUBSET mode, the AC's type + position) inside `issue`.
    # For a "general" issue not tied to one node (e.g. flag cross-reference
    # integrity, malformed JSON), use the relevant flag_id or a short label.
    entity_type: Literal[
        "module", "feature", "user_story", "delete", "source_enrichment", "general"
    ]
    issue: str
    suggested_fix: str
    # issue/suggested_fix stay fully technical (criterion names, exact field
    # names, exact wrong/right values) — this is what gets flattened into
    # ai_feedback and fed back to the generator. Never simplify these for
    # readability; precision here is what makes the next generation pass fix
    # the right thing.
    user_summary: str
    # ONE plain-language sentence for a non-technical reviewer: no criterion
    # numbers, no field names, no fragment/UUID references, no jargon. State
    # what's actually wrong or what will change once fixed, in ordinary words.
    # This is what gets surfaced in generation_metadata's report — issue/
    # suggested_fix are NOT exposed there.


class IncrementalCriticReviewSchema(BaseModel):
    reasoning: str
    # Full step-by-step verification across all 12 criteria — as much
    # back-and-forth, re-checking, or self-correction as genuinely needed.
    # Populate this FIRST, before status/flagged_items/summary. This is the
    # scratchpad: work out every uncertain case here, reach a settled
    # conclusion, THEN write status/flagged_items/summary to match that
    # conclusion. Never surfaced to the generator or the end user — internal
    # only. Putting the messy verification work here, instead of inside
    # `issue` or `summary`, is what keeps those fields clean and consistent
    # with your actual final answer.
    status: Literal["PASS", "FAIL"]
    flagged_items: list[IncrementalFlaggedItemSchema] = Field(default_factory=list)
    summary: str

    @field_validator("flagged_items", mode="before")
    @classmethod
    def _coerce_lists(cls, v):
        return _coerce_json_string_to_list(v)


# ── Result wrapper ─────────────────────────────────────────────────────────────


class IncrementalUpdateResult(BaseModel):
    output: IncrementalUpdateOutput
    status: str  # "PASS" | "FAIL" | "UNKNOWN"
    ai_feedback: str  # critic FEEDBACK + SUGGESTED_FIX block; empty string on PASS
    iteration: int


# ─── Incremental Selector Output ─────────────────────────────────────────────


class SelectorOutput(BaseModel):
    selected_ids: list[str]
    meta: dict[str, str]  # uuid → "direct" | "context-only"

    @field_validator("meta")
    @classmethod
    def _validate_meta_values(cls, v):
        for key, val in v.items():
            if val not in ("direct", "context-only"):
                raise ValueError(
                    f"meta values must be 'direct' or 'context-only', got '{val}' for key '{key}'"
                )
        return v

    @model_validator(mode="after")
    def _validate_cross(self):
        # Every selected_id must have a meta entry
        for uid in self.selected_ids:
            if uid not in self.meta:
                raise ValueError(f"UUID '{uid}' in selected_ids has no entry in meta")
        return self


class SelectorResult(BaseModel):
    output: SelectorOutput
    status: str
    ai_feedback: str
    iteration: int
