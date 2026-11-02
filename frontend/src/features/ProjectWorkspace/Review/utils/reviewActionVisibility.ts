import type { FeatureDetails as FeatureDetailData } from "@/types";
import type { RequirementDetailData } from "@/types/user-story";
import { getEffectiveChangeType } from "@/utils/changeType";

/**
 * Shared with the header quick-actions in MiddlePanelLayout so the
 * feature/story "Approve"/"Add feedback" visibility rules live in one place.
 */

interface FeatureApproveVisibilityParams {
  canApprove?: boolean;
  projectType?: string | null;
  featureDetail?: FeatureDetailData | null;
  /** The feature itself has a pending ADDED/UPDATED/DELETE_SUGGESTED change — it needs accept/reject first, so bulk-approve can't apply here. */
  hasIncrementalChange?: boolean;
  /** source_code only — the source-code ingestion pipeline (module/feature/user-story generation) is still running, so this feature's tree isn't final yet. */
  isPipelineRunning?: boolean;
}

// Case 1: source_code — only allow Approve when the AI critic allows it and
//         there are still non-approved (ready) stories under this feature.
// Case 2: rfp — allow Approve whenever there are non-approved stories.
export function shouldShowFeatureApprove({
  canApprove,
  projectType,
  featureDetail,
  hasIncrementalChange,
  isPipelineRunning,
}: FeatureApproveVisibilityParams): boolean {
  if (hasIncrementalChange || isPipelineRunning) return false;

  return !!(
    canApprove &&
    (projectType === "rfp" ||
      (projectType === "source_code" &&
        featureDetail?.generation_metadata?.review_guidance
          ?.approve_as_is_allowed === true))
  );
}

interface FeatureFeedbackVisibilityParams {
  enableFeedback?: boolean;
  projectType?: string | null;
  hasIncrementalChange?: boolean;
  /** source_code only — the source-code ingestion pipeline (module/feature/user-story generation) is still running, so this feature's tree isn't final yet. */
  isPipelineRunning?: boolean;
}

export function shouldShowFeatureFeedback({
  enableFeedback,
  projectType,
  hasIncrementalChange,
  isPipelineRunning,
}: FeatureFeedbackVisibilityParams): boolean {
  return !!(
    !hasIncrementalChange &&
    !isPipelineRunning &&
    enableFeedback &&
    projectType === "source_code"
  );
}

interface StoryFeedbackVisibilityParams {
  enableFeedback?: boolean;
  detail?: RequirementDetailData | null;
  /** source_code only — the source-code ingestion pipeline (module/feature/user-story generation) is still running, so this story's tree isn't final yet. */
  isPipelineRunning?: boolean;
}

export function shouldShowStoryFeedback({
  enableFeedback,
  detail,
  isPipelineRunning,
}: StoryFeedbackVisibilityParams): boolean {
  return !!(
    enableFeedback &&
    !isPipelineRunning &&
    detail &&
    !getEffectiveChangeType(detail) &&
    detail.status !== "approved"
  );
}

// SPEC: spec-f6af3e
// RFP Stage 1 ends when user stories exist, not when every module is
// `approved`: accepting a feedback change also sets a module/feature to
// `approved`, so per-module status can't tell whether Stage 1 was approved.
interface Stage1ActionsVisibilityParams {
  projectType?: string | null;
  hasUserStories?: boolean;
  isProjectBusy?: boolean;
  /** A module/feature still awaits accept/reject of a feedback change — the backend refuses Stage-1 approval until it's resolved. */
  hasPendingFeedbackChange?: boolean;
}

export function shouldShowStage1Approve({
  projectType,
  hasUserStories,
  isProjectBusy,
  hasPendingFeedbackChange,
}: Stage1ActionsVisibilityParams): boolean {
  return (
    projectType === "rfp" &&
    !hasUserStories &&
    !isProjectBusy &&
    !hasPendingFeedbackChange
  );
}

export function shouldShowStage1ModulesFeedback({
  projectType,
  hasUserStories,
}: Stage1ActionsVisibilityParams): boolean {
  return projectType === "rfp" && !hasUserStories;
}
