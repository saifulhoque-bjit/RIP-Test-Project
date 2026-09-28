import type { FeatureItem, UserStoryItem } from "@/components/common/TreePanel";
import type { FeatureDetails as FeatureDetailData } from "@/types";
import type { RequirementDetailData } from "@/types/user-story";
import { useReviewState } from "@/features/ProjectWorkspace/Review/components/ReviewContext";

/**
 * Builds the "Add feedback" compose-target payload the same way everywhere it's
 * triggered from (feature/story footers and the middle-panel header), so the
 * two call sites can't drift out of sync.
 */
export function useFeatureFeedbackAction() {
  const { openComposeFeedback } = useReviewState();

  return (
    selectedFeature: FeatureItem,
    featureDetail?: FeatureDetailData | null,
    modCode?: string | null,
  ) => {
    openComposeFeedback("feature", {
      id: selectedFeature.id,
      label:
        featureDetail?.name ??
        selectedFeature.name ??
        selectedFeature.label ??
        "",
      modCode: modCode ?? undefined,
      mfuId: featureDetail?.mfu_id,
    });
  };
}

export function useStoryFeedbackAction() {
  const { openComposeFeedback, setSelectedStoryText } = useReviewState();

  return (
    selectedStory: UserStoryItem,
    detail?: RequirementDetailData | null,
  ) => {
    // This action always composes overall_feedback — clear any stray
    // text-selection so it doesn't get sent as specific_feedback.
    setSelectedStoryText("");
    openComposeFeedback("story", {
      id: selectedStory.id,
      label: detail?.title ?? selectedStory.name ?? selectedStory.label ?? "",
      modCode: detail?.mod_code,
      mfuId: detail?.mfu_id,
      userStoryCode: detail?.user_story_code,
    });
  };
}
