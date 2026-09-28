import type { UserStoryStatus } from "@/components/common/TreePanel";
import type { FeatureFunctionItem } from "./feature";

// ── Requirement Status ──────────────────────────────────────────────────────
export const REQUIREMENT_STATUS = {
  READY: "ready",
  NEEDS_EDIT: "needs_edit",
  FAILED: "failed",
  APPROVED: "approved",
} as const;

export type RequirementStatus =
  (typeof REQUIREMENT_STATUS)[keyof typeof REQUIREMENT_STATUS];

// ── Canonical Requirement ────────────────────────────────────────────────────
export interface CanonicalRequirement {
  id: string;
  title: string;
  description: string;
  status: RequirementStatus;
  /** Sequential version counter for this requirement lineage (1, 2, 3…). */
  version: number;
  /** Only one version per lineage has isCurrent = true at a time. */
  isCurrent: boolean;
  /** Trust rating calculated from supporting fragments (0–1). */
  consensusScore: number;
  projectId: string;
  authorId: string;
  createdAt: string;
  updatedAt: string;
}

// ── Requirement Version (Version History) ───────────────────────────────────
export interface RequirementVersion {
  id: string;
  requirementId: string;
  version: number;
  isCurrent: boolean;
  title: string;
  description: string;
  status: RequirementStatus;
  changeRationale: string;
  authorId: string;
  authorName: string;
  createdAt: string;
}

// ── Governance State Transition ──────────────────────────────────────────────
export interface StatusTransitionPayload {
  requirementId: string;
  targetStatus: RequirementStatus;
  /** Mandatory when creating a new version of an APPROVED requirement. */
  rationale?: string;
}

export interface BulkApprovePayload {
  requirementIds: string[];
  rationale: string;
}

export interface BulkStatusPayload {
  projectId: string;
  user_story_ids: string[];
  status: string;
}

// Mirrors the "Suggested action" dropdown on the feedback compose drawer —
// optional hint for how the AI should apply the feedback on regeneration.
export const SUGGESTED_FEEDBACK_ACTIONS = [
  "Rewrite this statement",
  "Append as acceptance criterion",
  "Flag as conflict / exclude",
  "Split into separate stories",
  "Fix grounding / cite correct SRS",
] as const;

export type SuggestedFeedbackAction =
  (typeof SUGGESTED_FEEDBACK_ACTIONS)[number];

export interface SpecificFeedbackItem {
  selected_text: string;
  selected_feedback: string;
  suggested_action?: SuggestedFeedbackAction;
}

export interface RegenerateByFeedbackItem {
  user_story_id: string;
  overall_feedback?: string;
  suggested_action?: SuggestedFeedbackAction;
  specific_feedback?: SpecificFeedbackItem[];
}

// ── Feedback Draft / Submission Model ──────────────────────────────────────
// mod_code/mfu_id/user_story_code are captured for source_code drafts only
// (the parent feature's module code + mfu id, and this story's own code) —
// used when submitting via regenerate-for-source-code, which addresses
// stories by user_story_code rather than the rfp regenerate-by-feedback
// wire shape's user_story_id.
export type UserStoryFeedbackStoryItem = RegenerateByFeedbackItem & {
  mod_code?: string;
  mfu_id?: string | null;
  user_story_code?: string;
};

export interface UserStoryFeedback {
  // id: string;
  project_id: string;
  // submitted_at: string;
  user_stories: UserStoryFeedbackStoryItem[];
}

export interface RegenerateByFeedbackResponse {
  success: boolean;
  message: string;
  data?: {
    task_id?: string;
    project_id?: string;
    status?: string;
  };
}

// ── Requirement Detail (GET /projects/{project_id}/requirements/{requirement_id}) ──
export interface RequirementSourceFile {
  id: string;
  name: string;
  type: string;
  storage_key: string;
  storage_url: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface RequirementFragmentBbox {
  page: number;
  bbox: number[];
}

export interface RequirementFragment {
  id: string;
  source_id: string;
  path: string;
  domain: string;
  content: string;
  pages: number[];
  bbox: RequirementFragmentBbox[];
  tokens: number;
  position_index: number;
  content_hash: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface AcceptanceCriterion {
  type: string;
  given: string;
  when: string;
  then: string;
  ac_code: string;
  id?: string | null;
  l2_source_ref?: string | null;
}

export interface RequirementDetailBboxItem {
  x: number;
  y: number;
  w: number;
  h: number;
  fragment_id?: string;
}

export interface RequirementDetailBbox {
  source_id: string;
  fragment_id?: string;
  page: number;
  bboxes: RequirementDetailBboxItem[];
}

export interface RequirementSourcePageBbox {
  bbox: RequirementDetailBboxItem;
  fragment_id: string;
}

export interface RequirementSourcePage {
  page: number;
  bboxes: RequirementSourcePageBbox[];
}

export interface RequirementSource {
  source_id: string;
  pages: RequirementSourcePage[];
}

export interface RequirementSrsGroupSpec {
  id: string;
  mod_code: string;
  fea_code: string;
  filename: string;
  content: string;
  storage_key: string;
  source_id: string;
  project_id: string;
  created_at: string | null;
  updated_at: string | null;
}

export type EvidenceHighlightType =
  | "table_row"
  | "section"
  | "bullet_item"
  | "file";

export type EvidencePrecision = "exact" | "section" | "file";

export interface RequirementSrsEvidence {
  id: string;
  module_code: string;
  feature_code: string;
  user_story_code: string;
  user_story_id: string;
  group_spec_id: string;
  l2_id: string | null;
  file_name: string | null;
  section_anchor: string | null;
  section_path?: string[];
  highlight_type: EvidenceHighlightType | null;
  target_string: string | null;
  exact_quote: string | null;
  context_snippet?: string | null;
  trace_id?: string | null;
  precision: EvidencePrecision | null;
  line_number?: number | null;
  operation_type?: string | null;
  evidence_role?: string | null;
  srs_document_type?: string | null;
  ac_ids?: string[];
  group_spec: RequirementSrsGroupSpec;
  created_at: string | null;
  updated_at: string | null;
  tier: "L1" | "L2" | "L3";
}

export interface RequirementScreenAsciiLayoutRef {
  storage_key: string;
  srs_file: string;
  section: string;
  start_line: number;
  end_line: number;
}

export interface RequirementScreen {
  artifact_id: string;
  screen_id: string;
  srs_file: string;
  ascii_layout_ref: RequirementScreenAsciiLayoutRef;
  controls_touched: string[];
  events_covered: string[];
}

export interface NfrItem {
  id: string;
  category: string;
  requirement: string;
  description: string;
}

export interface RequirementDetailData {
  id: string;
  user_story_code: string;
  title: string;
  description: string | null;
  consensus: number;
  status: string;
  version: string;
  feature_id: string;
  project_id: string;
  /** source_code only — parent feature's mfu id + module code, needed for regenerate-for-source-code feedback. */
  mfu_id?: string | null;
  mod_code?: string;
  as_a: string;
  i_want_to: string;
  so_that: string;
  acceptance_criteria: AcceptanceCriterion[];
  nfrs?: NfrItem[];
  technical_notes: string | null;
  story_points: number;
  is_current?: boolean;
  del_reason?: string | null;
  deleted_at?: string | null;
  module_name: string;
  feature_name: string;
  source_file_count: number;
  source_file_types?: string[];
  source_files: RequirementSourceFile[];
  sources: RequirementSource[];
  screens?: RequirementScreen[];
  srs_evidence: RequirementSrsEvidence[];
  incremental_change_type?: ChangeType;
  feedback_change_type?: ChangeType;
  text_diffs?: RequirementTextDiffs;
  justification?: string | null;
  /** Full snapshot of the prior version — present on "UPDATED" detail responses. */
  last_previous_items?: RequirementPreviousSnapshot;
  created_at: string | null;
  updated_at: string | null;
}

export interface RequirementDetailResponse {
  success: boolean;
  message: string;
  data: RequirementDetailData;
}

// Note: List endpoint returns the same structure as detail endpoint
export interface RequirementListParams {
  project_id: string;
  status?: string;
  module_id?: string;
  feature_id?: string;
  search_text?: string;
  skip?: number;
  limit?: number;
}

export interface RequirementListResponse {
  success: boolean;
  message: string;
  data: {
    total: number;
    are_all_approved: boolean;
    skip: number;
    limit: number;
    items: RequirementDetailData[];
  };
}

// ──────────── Requirement Summary (GET /projects/{project_id}/requirements/summary) ────────────
export interface RequirementSummaryData {
  project_id: string;
  total_user_stories: number;
  ready_count: number;
  needs_edit_count: number;
  failed_count: number;
  approved_count: number;
  total_modules: number;
  total_features: number;
  jira_sync_count: number;
  tap_sync_count: number;
}

export interface RequirementSummaryResponse {
  success: boolean;
  message: string;
  data: RequirementSummaryData;
}

// ──────────── User Story Tree (GET /projects/{project_id}/requirements/tree) ────────────
export type ChangeType = "ADDED" | "UPDATED" | "DELETE_SUGGESTED" | null;

// Field-level before/after pair — populated on detail responses when
// incremental_change_type is "UPDATED".
export interface TextDiffItem {
  before: string;
  after: string;
}

// Before/after pairs for one acceptance criterion's given/when/then fields,
// keyed by ac_code inside RequirementTextDiffs.acceptance_criteria.
export interface AcceptanceCriterionTextDiff {
  given?: TextDiffItem[];
  when?: TextDiffItem[];
  then?: TextDiffItem[];
}

// Field-level diff map on a user story "UPDATED" detail response — keys are
// RequirementDetailData field names, each holding the changed segment(s).
// Acceptance criteria and NFRs are nested by their own code/id.
export interface RequirementTextDiffs {
  as_a?: TextDiffItem[];
  i_want_to?: TextDiffItem[];
  so_that?: TextDiffItem[];
  title?: TextDiffItem[];
  description?: TextDiffItem[];
  technical_notes?: TextDiffItem[];
  story_points?: TextDiffItem[];
  acceptance_criteria?: Record<string, AcceptanceCriterionTextDiff>;
  nfrs?: Record<string, TextDiffItem[]>;
}

// Full snapshot of the prior version of a user story — present as
// `last_previous_items` on "UPDATED" detail responses.
export interface RequirementPreviousSnapshot {
  id: string;
  user_story_id?: string;
  feature_id?: string;
  project_id?: string;
  user_story_code: string;
  title: string;
  description?: string | null;
  consensus?: number;
  status?: string;
  version: number;
  as_a: string;
  i_want_to: string;
  so_that: string;
  acceptance_criteria: AcceptanceCriterion[];
  nfrs?: NfrItem[];
  technical_notes?: string | null;
  story_points?: number;
  justification?: string | null;
  incremental_change_type?: ChangeType;
  feedback_change_type?: ChangeType;
  sources?: RequirementSource[];
  l2_sources?: unknown[];
  is_jira_synced?: boolean;
  is_tap_synced?: boolean;
  created_at?: string | null;
  updated_at?: string | null;
  snapshotted_at?: string | null;
}

// ──────────── Incremental Update Accept/Reject (POST /projects/{project_id}/updates/accept|reject) ──
export type UpdateEntityType = "module" | "feature" | "user_story";

export interface UpdateActionPayload {
  entity_type: UpdateEntityType;
  entity_id: string;
  change_type: Exclude<ChangeType, null>;
}

export interface UpdateActionResponse {
  success: boolean;
  message: string;
}
export interface UserStoryTreeItem {
  id: string;
  user_story_code: string;
  name: string;
  description?: string;
  status?: UserStoryStatus;
  // Attributes to manage feedback/incremental changes
  incremental_change_type?: ChangeType;
  feedback_change_type?: ChangeType;
  is_jira_synced?: boolean;
  is_tap_synced?: boolean;
  source_ingestion_id?: string;
  proposed_items?: {
    user_story_code: string;
    title: string;
    as_a: string;
    i_want_to: string;
    so_that: string;
    acceptance_criteria: AcceptanceCriterion[];
    sources: RequirementSource[];
    nfrs: NfrItem[];
  };
}

export interface FeatureTreeItem {
  id: string;
  fea_code: string;
  name: string;
  description?: string;
  children?: UserStoryTreeItem[];
  // Attributes to manage feedback/incremental changes
  incremental_change_type?: ChangeType;
  feedback_change_type?: ChangeType;
  source_ingestion_id?: string;
  proposed_items?: {
    fea_code: string;
    name: string;
    description: string;
    functions: FeatureFunctionItem[];
    sources: RequirementSource[];
  };
}

export interface ModuleTreeItem {
  id: string;
  mod_code: string;
  name: string;
  description?: string;
  children?: FeatureTreeItem[];
  // Attributes to manage feedback/incremental changes
  incremental_change_type?: ChangeType;
  feedback_change_type?: ChangeType;
  source_ingestion_id?: string;
  proposed_items?: {
    mod_code: string;
    name: string;
    description: string;
  };
}

export interface UserStoryTreeResponse {
  success: boolean;
  message: string;
  data: {
    are_all_approved?: boolean;
    are_all_approved_for_us?: boolean;
    are_all_approved_for_mod?: boolean;
    are_all_approved_for_fea?: boolean;
    items: ModuleTreeItem[];
  };
}

/**
 * `UserStoryTreeItem` plus `version` — the sync-candidates tree carries the
 * story's version directly so the Sync Tray can display it without a
 * per-story detail fetch (avoids one extra request per story on tray open).
 */
export interface SyncCandidateUserStoryItem extends UserStoryTreeItem {
  version: number;
  deleted_at?: string | null;
}

export interface SyncCandidateFeatureItem extends Omit<FeatureTreeItem, "children"> {
  children?: SyncCandidateUserStoryItem[];
}

export interface SyncCandidateModuleItem extends Omit<ModuleTreeItem, "children"> {
  children?: SyncCandidateFeatureItem[];
}

/**
 * Module → feature → user story tree pruned to approved, not-yet-synced
 * stories for a given sync target — backs the Jira/TAP Sync Tray in a single
 * call (`GET .../user-stories/sync-candidates?sync_target=...`).
 */
export interface SyncCandidatesResponse {
  success: boolean;
  message: string;
  data: {
    sync_target: "jira" | "tap";
    total_count: number;
    items: SyncCandidateModuleItem[];
  };
}

// This type is used in SourceEvidencePanel to represent the source evidence items associated with a user story.
// It combines information from RequirementSource and RequirementSourceFile, along with additional metadata for display and interaction purposes.
export interface SourceEvidenceItem {
  // sourceId: string;
  sourceType: string;
  sourceFileName: string;
  sourceFilePath: string;
  source?: RequirementSource;
  sectionAnchor?: string | null;
  highlightType?: EvidenceHighlightType | null;
  targetString?: string | null;
  exactQuote?: string | null;
  contextSnippet?: string | null;
  lineNumber?: number | null;
  acIds?: string[] | null;
  l2_id?: string | null;
  precision?: EvidencePrecision | null;
  l2SourceRef?: string | null;
  isGapReference?: boolean;
  isUnresolved?: boolean;
  tier?: "L1" | "L2" | "L3";
}
