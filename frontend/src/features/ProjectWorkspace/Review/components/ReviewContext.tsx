import { createContext, useContext } from "react";
import type { ModuleTreeItem } from "@/types/user-story";
import type { ReviewStage } from "@/features/ProjectWorkspace/Review/stage";

export type FeedbackDrawerMode = "compose" | "list" | null;

/** Which node type feedback is being composed/listed for — kept separate end-to-end since each has its own API contract. */
export type FeedbackScope = "module" | "feature" | "story";

export const SCOPE_NOUN: Record<FeedbackScope, string> = {
  module: "module",
  feature: "feature",
  story: "story",
};

export interface ActiveFeedbackTarget {
  id: string;
  label: string;
  /** source_code only — required by the regenerate-for-source-code feedback payload. */
  modCode?: string;
  mfuId?: string | null;
  /** Story scope only — source_code addresses stories by code, not id. */
  userStoryCode?: string;
}

export interface ReviewStateContextValue {
  stage: ReviewStage;
  treeItems: ModuleTreeItem[];
  areAllApprovedForMod: boolean;
  projectType?: string | null;
  projectName?: string | null;
  isTreeLoading: boolean;
  isTreeFetching: boolean;
  isProjectLoading: boolean;
  feedbackDrawerMode: FeedbackDrawerMode;
  feedbackScope: FeedbackScope | null;
  activeFeedbackTarget: ActiveFeedbackTarget | null;
  /** Open the drawer in compose mode to write feedback for a single module/feature/story. */
  openComposeFeedback: (scope: FeedbackScope, target: ActiveFeedbackTarget) => void;
  /** Open the drawer in list mode to review/submit all pending feedback of one scope for the project. */
  openFeedbackList: (scope: FeedbackScope) => void;
  closeFeedbackDrawer: () => void;
  /** Text highlighted on the user story detail page while composing story feedback — shown in the drawer and submitted as selected_text. */
  selectedStoryText: string;
  setSelectedStoryText: (text: string) => void;
}

export const ReviewStateContext = createContext<ReviewStateContextValue | null>(
  null,
);

export const useReviewState = () => {
  const context = useContext(ReviewStateContext);

  if (!context) {
    throw new Error("useReviewState must be used within a ReviewProvider.");
  }

  return context;
};
