/**
 * Visibility rule for the RFP Stage-1 "Approve & generate user stories"
 * banner. Kept free of runtime imports so it can be exercised directly by
 * frontend/tests/architectureApprovalVisibility.test.mjs.
 */

interface ApproveArchitectureVisibilityParams {
  projectType?: string | null;
  hasUserStories: boolean;
  isProjectBusy: boolean;
  /** Any module/feature/story still awaiting feedback accept/reject. */
  hasPendingFeedbackChange: boolean;
}

// Stage 1 ends when user stories exist, not when every module reads
// "approved": accepting a module's feedback update also sets its status to
// "approved" (POST /feedback-updates/accept), so `are_all_approved_for_mod`
// can be true before the architecture was ever approved.
export function shouldShowApproveArchitecture({
  projectType,
  hasUserStories,
  isProjectBusy,
  hasPendingFeedbackChange,
}: ApproveArchitectureVisibilityParams): boolean {
  return (
    projectType === "rfp" &&
    !hasUserStories &&
    !isProjectBusy &&
    !hasPendingFeedbackChange
  );
}
