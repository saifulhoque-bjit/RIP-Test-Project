import type {
  ChangeType,
  RequirementSource,
  TextDiffItem,
} from "./user-story";

export interface FeatureReviewGuidance {
  severity?: string;
  recommended_action?: string;
  approve_as_is_allowed?: boolean;
  guideline?: string;
  flagged_story_ids?: string[];
  failure_summary?: string;
  gate_reference?: FeatureGuidanceGateReference[];
}

export interface FeatureGenerationMetadata {
  generator_model?: string;
  iteration_count?: number;
  final_status?: string;
  generated_at?: string;
  correction_applied?: boolean;
  gate_profile?: string;
  framework_focal?: string;
  relaxed_gates?: string[];
  /** Populated for FAIL_HALLUCINATION — explains why grounding failed. */
  failure_reason?: string;
  /** Populated for PASS_DETERMINISTIC_FALLBACK — explains why deterministic path was used. */
  fallback_reason?: string;
  review_guidance?: FeatureReviewGuidance;
  [key: string]: unknown;
}

export interface FeatureGuidanceGateReference {
  gate: number;
  name: string;
  meaning: string;
}

export interface FeatureSourceFile {
  id: string;
  name: string;
  type: string;
  storage_key: string;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface FeatureFunctionItem {
  fun_code?: string;
  name?: string;
  description?: string;
  func_src_ref?: string;
}

// Before/after pairs for one function's fields, keyed by fun_code inside
// FeatureTextDiffs.functions.
export interface FeatureFunctionTextDiff {
  name?: TextDiffItem[];
  description?: TextDiffItem[];
}

// Field-level diff map on a feature "UPDATED" detail response — functions
// are nested by fun_code.
export interface FeatureTextDiffs {
  name?: TextDiffItem[];
  description?: TextDiffItem[];
  functions?: Record<string, FeatureFunctionTextDiff>;
}

// Full snapshot of the prior version of a feature — present as
// `last_previous_items` on "UPDATED" detail responses.
export interface FeaturePreviousSnapshot {
  id: string;
  feature_id?: string;
  module_id?: string;
  project_id?: string;
  fea_code?: string;
  mfu_id?: string | null;
  name?: string;
  description?: string;
  version?: number;
  status?: string;
  functions?: FeatureFunctionItem[];
  l2_sources?: unknown[];
  justification?: string | null;
  incremental_change_type?: ChangeType;
  feedback_change_type?: ChangeType;
  is_infrastructure?: boolean | null;
  condensation_note?: string | null;
  is_jira_synced?: boolean;
  is_tap_synced?: boolean;
  created_at?: string | null;
  updated_at?: string | null;
  snapshotted_at?: string | null;
}

export interface FeatureDetails {
  id: string;
  project_id?: string;
  module_id?: string;
  mfu_id?: string | null;
  fea_code?: string;
  name?: string;
  label?: string;
  description?: string;
  status?: string;
  total_user_stories?: number;
  type?: "feature";
  generation_metadata?: FeatureGenerationMetadata;
  is_infrastructure?: boolean;
  condensation_note?: string;
  functions?: FeatureFunctionItem[];
  sources?: RequirementSource[];
  source_files?: FeatureSourceFile[];
  incremental_change_type?: ChangeType;
  feedback_change_type?: ChangeType;
  text_diffs?: FeatureTextDiffs;
  /** Full snapshot of the prior version — present on "UPDATED" detail responses. */
  last_previous_items?: FeaturePreviousSnapshot;
  created_at?: string;
  updated_at?: string;
}

export interface FeatureDetailsPayload {
  project_id: string;
  module_id: string;
  mod_code: string;
  feature: FeatureDetails;
}

export interface FeatureDetailsResponse {
  success: boolean;
  message: string;
  data: FeatureDetailsPayload;
}

// ── Feature Feedback Draft ──────────────────────────────────────────────────
export interface FeatureFeedbackItem {
  feature_id: string;
  mod_code?: string;
  mfu_id?: string | null;
  overall_feedback: string;
}

export interface FeatureFeedback {
  project_id: string;
  features: FeatureFeedbackItem[];
}

// ── Regenerate for Source Code (POST /projects/{project_id}/user-stories/regenerate-for-source-code) ──
// One heterogeneous array covers feature-level feedback (mod_code + mfu_id
// only) and user-story-level feedback (adds user_story_code, and either/both
// of overall_feedback and specific_feedback).
export interface RegenerateForSourceCodeSpecificFeedbackItem {
  selected_text: string;
  selected_feedback: string;
}

export interface RegenerateForSourceCodeFeedbackItem {
  mod_code: string;
  mfu_id?: string | null;
  user_story_code?: string;
  overall_feedback?: string;
  specific_feedback?: RegenerateForSourceCodeSpecificFeedbackItem[];
}
