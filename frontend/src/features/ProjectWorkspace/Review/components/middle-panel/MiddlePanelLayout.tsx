import Button from "@/components/common/Button/Button";
import type {
  FeatureItem,
  ModuleItem,
  UserStoryItem,
} from "@/components/common/TreePanel";
import type { ReactNode } from "react";
import type { ActiveChangeEntity } from "../../hooks/useIncrementalChangeAction";
import type { FeatureDetails as FeatureDetailData } from "@/types";
import type { RequirementDetailData } from "@/types/user-story";
import {
  shouldShowFeatureApprove,
  shouldShowFeatureFeedback,
  shouldShowStoryFeedback,
} from "@/features/ProjectWorkspace/Review/utils/reviewActionVisibility";
import {
  useFeatureFeedbackAction,
  useStoryFeedbackAction,
} from "@/features/ProjectWorkspace/Review/hooks/useFeedbackComposeActions";
import { getEffectiveChangeType } from "@/utils/changeType";

interface MiddlePanelLayoutProps {
  selectedModule: ModuleItem | null;
  selectedFeature: FeatureItem | null;
  selectedStory: UserStoryItem | null;
  enableFeedback: boolean;
  activeChangeEntity: ActiveChangeEntity | null;
  rejectChange: () => void;
  acceptChange: () => void;
  projectType?: string | null;
  featureDetail?: FeatureDetailData | null;
  featureModCode?: string | null;
  canApproveFeature?: boolean;
  handleApproveFeature: () => void | Promise<void>;
  requirementDetail?: RequirementDetailData | null;
  canApproveStory?: boolean;
  handleApproveStory: () => void | Promise<void>;
  canApproveModule?: boolean;
  handleApproveModule: () => void | Promise<void>;
  isApproving?: boolean;
  approvingTargetId?: string | null;
  /** True while a feedback-regeneration task is in flight for the current selection — hides the header quick actions, mirroring the skeleton FeatureDetails/UserStoryDetails show in their place. */
  isRegenerating?: boolean;
  /** source_code only — the source-code ingestion pipeline (module/feature/user-story generation) is still running, so the tree isn't final yet: hides Add feedback/Approve. */
  isPipelineRunning?: boolean;
  children: ReactNode;
}

export default function MiddlePanelLayout({
  selectedModule,
  selectedFeature,
  selectedStory,
  enableFeedback,
  activeChangeEntity,
  rejectChange,
  acceptChange,
  projectType,
  featureDetail = null,
  featureModCode = null,
  canApproveFeature = false,
  handleApproveFeature,
  requirementDetail = null,
  canApproveStory = false,
  handleApproveStory,
  canApproveModule = false,
  handleApproveModule,
  isApproving = false,
  approvingTargetId = null,
  isRegenerating = false,
  isPipelineRunning = false,
  children,
}: MiddlePanelLayoutProps) {
  const addFeatureFeedback = useFeatureFeedbackAction();
  const addStoryFeedback = useStoryFeedbackAction();

  const showFeedbackButton =
    !isRegenerating &&
    (selectedFeature
      ? shouldShowFeatureFeedback({
          enableFeedback,
          projectType,
          hasIncrementalChange: !!getEffectiveChangeType(selectedFeature),
          isPipelineRunning,
        })
      : selectedStory
        ? shouldShowStoryFeedback({
            enableFeedback,
            detail: requirementDetail,
            isPipelineRunning,
          })
        : false);

  const showApproveButton =
    !isRegenerating &&
    (selectedFeature
      ? shouldShowFeatureApprove({
          canApprove: canApproveFeature,
          projectType,
          featureDetail,
          hasIncrementalChange: !!getEffectiveChangeType(selectedFeature),
          isPipelineRunning,
        })
      : selectedStory
        ? !isPipelineRunning && canApproveStory
        : selectedModule
          ? canApproveModule
          : false);

  const isApprovingCurrent =
    isApproving &&
    approvingTargetId ===
      (selectedFeature?.id ?? selectedStory?.id ?? selectedModule?.id);

  // Same handler references the footer "Add feedback"/"Approve" buttons in
  // FeatureDetails/UserStoryDetails/ModuleDetails already bind directly —
  // reused here so the header can't drift out of sync with the footer.
  const handleAddFeedback = selectedFeature
    ? () => addFeatureFeedback(selectedFeature, featureDetail, featureModCode)
    : selectedStory
      ? () => addStoryFeedback(selectedStory, requirementDetail)
      : undefined;

  const handleApprove = selectedFeature
    ? handleApproveFeature
    : selectedStory
      ? handleApproveStory
      : selectedModule
        ? handleApproveModule
        : undefined;

  return (
    <div className="flex min-w-0 w-[43.08%] shrink-0 flex-col bg-[var(--canvas)]">
      {/* Middle Panel Header - Module / Feature / UserStory */}
      <div className="flex items-center justify-between gap-2.5 border-b border-[var(--border-primary)] bg-white px-5 py-[14px]">
        <div>
          <div
            className="text-[10.5px] uppercase tracking-[0.5px] text-[var(--text-tertiary)]"
            id="d-kick"
          >
            {selectedModule
              ? "MODULE"
              : selectedFeature
                ? "FEATURE"
                : "FOCUSED SELECTION"}
          </div>
          <h3 className="m-[2px_0_0] text-base font-semibold" id="d-title">
            {selectedModule
              ? "Module details"
              : selectedFeature
                ? "Feature details"
                : "User story"}
          </h3>
        </div>

        {activeChangeEntity ? (
          <div className="ml-auto flex gap-2">
            <Button size="xs" variant="ghost" onClick={rejectChange}>
              Reject
            </Button>
            <Button size="xs" variant="success" onClick={acceptChange}>
              Accept changes
            </Button>
          </div>
        ) : (
          (showFeedbackButton || showApproveButton) && (
            <div className="ml-auto flex gap-2">
              {showFeedbackButton && (
                <Button size="xs" variant="ghost" onClick={handleAddFeedback}>
                  Add feedback
                </Button>
              )}
              {showApproveButton && (
                <Button
                  size="xs"
                  variant="success"
                  onClick={handleApprove}
                  loading={isApprovingCurrent}
                >
                  Approve
                </Button>
              )}
            </div>
          )
        )}
      </div>

      {children}
    </div>
  );
}
