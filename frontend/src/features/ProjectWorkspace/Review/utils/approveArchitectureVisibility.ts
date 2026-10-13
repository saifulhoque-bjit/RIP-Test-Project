export interface ApproveArchitectureVisibilityInput {
  projectType?: string | null;
  hasUserStories: boolean;
  isProjectBusy: boolean;
  hasPendingFeedbackChange: boolean;
}

// RFP Stage 1 gate for "Approve & generate user stories". Deliberately not
// gated on every module being "approved": accepting a feedback update also
// sets the module/feature status to approved, so after accepting module- and
// feature-level updates all modules can be approved while Stage 2 (user-story
// generation) has never run. "No user stories yet" is the real Stage 1 signal.
export const shouldShowApproveArchitectureBtn = ({
  projectType,
  hasUserStories,
  isProjectBusy,
  hasPendingFeedbackChange,
}: ApproveArchitectureVisibilityInput): boolean =>
  projectType === "rfp" &&
  !hasUserStories &&
  !isProjectBusy &&
  !hasPendingFeedbackChange;
