import type { FeatureItem } from "@/components/common/TreePanel";
import { Button } from "@/components/common/Button/Button";
import Card from "@/components/common/Card";
import type { FeatureDetails as FeatureDetailData } from "@/types";
import DetailPanelHeader from "./DetailPanelHeader";
import ChangeDiffPanel, { type ChangeDiffVersion } from "./ChangeDiffPanel";
import {
  shouldShowFeatureApprove,
  shouldShowFeatureFeedback,
} from "@/features/ProjectWorkspace/Review/utils/reviewActionVisibility";
import { useFeatureFeedbackAction } from "@/features/ProjectWorkspace/Review/hooks/useFeedbackComposeActions";
import { getEffectiveChangeType } from "@/utils/changeType";
import { ChevronNext } from "@/assets/icons/arrow/ChevronNext";

const functionsToLines = (
  functions?: { name?: string; description?: string }[],
) =>
  (functions ?? [])
    .map((fn) => [fn.name, fn.description].filter(Boolean).join(": "))
    .filter(Boolean);

interface FeatureDetailsProps {
  selectedFeature: FeatureItem | null;
  featureDetail?: FeatureDetailData | null;
  /** Parent module's code, from the feature-detail response payload — needed for feedback submission. */
  modCode?: string | null;
  isLoadingDetail?: boolean;
  isDetailError?: boolean;
  setSelectedId: (id: string) => void;
  handleApproveFeature: () => void | Promise<void>;
  isApproving?: boolean;
  canApprove?: boolean;
  projectType?: string | null;
  stage?: string;
  /** When false, the "Add feedback" affordance is hidden. */
  enableFeedback?: boolean;
  /** True while a feedback-regeneration task is in flight for this exact feature — shows a skeleton and blocks all actions. */
  isFeatureRegenerating?: boolean;
  /** source_code only — the source-code ingestion pipeline (module/feature/user-story generation) is still running, so the tree isn't final yet: hides Add feedback/Approve. */
  isPipelineRunning?: boolean;
}

export default function FeatureDetails({
  selectedFeature,
  featureDetail = null,
  modCode = null,
  isLoadingDetail = false,
  isDetailError = false,
  setSelectedId,
  handleApproveFeature,
  isApproving = false,
  canApprove = true,
  projectType,
  stage,
  enableFeedback = true,
  isFeatureRegenerating = false,
  isPipelineRunning = false,
}: FeatureDetailsProps) {
  const addFeatureFeedback = useFeatureFeedbackAction();

  if (!selectedFeature) {
    return (
      <div className="flex h-full w-full items-center justify-center text-[var(--text-tertiary)]">
        No feature selected.
      </div>
    );
  }

  if (isFeatureRegenerating) {
    return (
      <div
        className="animate-pulse rounded-[0_12px_12px_0] border border-[var(--border-primary)] border-l-4 border-l-[var(--ai)] bg-white shadow-[var(--e1)]"
        id="ip-feature"
        aria-busy="true"
        aria-label="Regenerating feature from feedback"
      >
        <div className="border-b border-[var(--border-primary)] px-[18px] py-4">
          <div className="h-4 w-1/2 rounded bg-[#e4e8ef]" />
        </div>
        <div className="flex flex-col gap-2.5 p-[16px_18px]">
          <div className="h-3 w-full rounded bg-[#e4e8ef]" />
          <div className="h-3 w-11/12 rounded bg-[#e4e8ef]" />
          <div className="h-3 w-3/4 rounded bg-[#e4e8ef]" />
        </div>
        <div className="flex flex-col gap-2 border-t-[2px] border-[var(--border-primary)] bg-[#fafbfd] px-[18px] py-3">
          <div className="h-3 w-40 rounded bg-[#e4e8ef]" />
          <p className="m-0 text-[11px] text-[var(--text-tertiary)]">
            Regenerating this feature (and its user stories) from feedback…
          </p>
        </div>
      </div>
    );
  }

  const featureDescription =
    featureDetail?.description ?? selectedFeature.description ?? "";

  const changedAction = getEffectiveChangeType(selectedFeature);
  // `featureDetail` always holds the feature's own current attributes. For
  // "UPDATED", featureDetail.last_previous_items additionally holds the full
  // prior-version snapshot, so the attribute-by-attribute before → after
  // table can be built from both. For "ADDED"/"DELETE_SUGGESTED" there's
  // nothing to diff against — featureDetail alone (rendered as a single
  // success/error-tinted column) is the newly proposed or soon-to-be-removed
  // feature.
  const attributeDiff =
    changedAction && featureDetail
      ? {
          kind: "entity" as const,
          current: {
            name: featureDetail.name,
            description: featureDetail.description,
            functions: featureDetail.functions,
          },
          previous:
            changedAction === "UPDATED" && featureDetail.last_previous_items
              ? {
                  name: featureDetail.last_previous_items.name,
                  description: featureDetail.last_previous_items.description,
                  functions: featureDetail.last_previous_items.functions,
                }
              : undefined,
        }
      : undefined;
  const currentVersion: ChangeDiffVersion | undefined = changedAction
    ? {
        label: "Current",
        narrative: featureDetail?.name ?? selectedFeature.name ?? "",
        lines: [
          featureDescription,
          ...functionsToLines(featureDetail?.functions),
        ].filter(Boolean),
      }
    : undefined;
  const previousVersion =
    changedAction === "DELETE_SUGGESTED" ? currentVersion : undefined;
  const proposedVersion =
    changedAction === "ADDED" ? currentVersion : undefined;

  const showApproveButton = shouldShowFeatureApprove({
    canApprove,
    projectType,
    featureDetail,
    hasIncrementalChange: !!changedAction,
    isPipelineRunning,
  });
  const showFeedbackButton = shouldShowFeatureFeedback({
    enableFeedback,
    projectType,
    hasIncrementalChange: !!changedAction,
    isPipelineRunning,
  });

  return (
    <div
      className={`${selectedFeature ? "block" : "hidden"} rounded-[0_12px_12px_0] border border-[var(--border-primary)] bg-white shadow-[var(--e1)]`}
      id="ip-feature"
    >
      <DetailPanelHeader
        title={
          featureDetail?.name ??
          selectedFeature.name ??
          selectedFeature.label ??
          ""
        }
        // code={featureDetail?.fea_code ?? selectedFeature.fea_code}
      />
      <div className="p-[16px_18px]">
        {changedAction ? (
          <div className="mb-4">
            <ChangeDiffPanel
              changedAction={changedAction}
              previous={previousVersion}
              proposed={proposedVersion}
              attributeDiff={attributeDiff}
            />
          </div>
        ) : (
          <>
            <p className="m-0 mb-2 text-[11px] font-bold uppercase tracking-[0.5px] text-[var(--mut)]">
              Feature description
            </p>
            <Card
              className="p-[14px] !shadow-none text-[13.5px] leading-[1.6] text-[var(--text-secondary)] mb-4"
              id="feat-desc"
            >
              {isLoadingDetail
                ? "Loading feature details..."
                : isDetailError
                  ? "Something went wrong! Unable to load feature details."
                  : featureDescription}
            </Card>
          </>
        )}

        {/* User Stories will be generated in the second stage */}
        {stage === "second" && (
          <>
            <p
              className="m-0 mb-2 text-[11px] font-bold uppercase tracking-[0.5px] text-[var(--mut)]"
              id="feat-stories-lbl"
            >
              Derived user stories
            </p>
            <div id="feat-stories" className="mb-4">
              {selectedFeature?.children?.map((story) => (
                <button
                  key={story.id}
                  type="button"
                  onClick={() => setSelectedId(story.id)}
                  className="mb-2 flex w-full cursor-pointer items-center justify-between gap-3 rounded-lg border border-[var(--border-primary)] bg-white px-[14px] py-3 text-[13px] hover:border-[var(--border-strong)]"
                >
                  <span className="min-w-0 flex-1 text-left">{story.name}</span>
                  <span className="inline-flex shrink-0 rounded-[14px] bg-[var(--info-50)] px-[9px] py-[3px] text-[11px] font-semibold text-[var(--info)] uppercase">
                    {story.status}
                  </span>
                  <ChevronNext className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]" />
                </button>
              ))}
            </div>
          </>
        )}

        {/* Feature Functions */}
        {!changedAction &&
          featureDetail?.functions &&
          featureDetail.functions.length > 0 && (
            <>
              <p className="m-0 mb-2 text-[11px] font-bold uppercase tracking-[0.5px] text-[var(--mut)]">
                Feature functions
              </p>
              <ul className="flex flex-col gap-2.5">
                {featureDetail?.functions?.map((feat_function, idx) => (
                  <li
                    className="flex flex-col gap-1.5 py-3 px-4 bg-[#FAFBFD] rounded-[8px]"
                    key={feat_function.fun_code || idx}
                  >
                    <span className="w-full min-h-5.5 rounded-[5px] inline-flex justify-start items-center font-bold text-[13px] text-[#1B253B] break-words">
                      {feat_function.name || "——"}
                    </span>
                    <span className="inline-flex justify-start items-center text-[10px] text-[var(--ink)]">
                      {feat_function.description || "——"}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}

        {/* ————— Footer ———————————————————————————————————————— */}
        {stage === "second" && (showFeedbackButton || showApproveButton) && (
          <div
            data-feedback-scope="excluded"
            className="flex flex-col items-start justify-between gap-2 border-t-[2px] border-[var(--border-primary)] bg-[#fafbfd] py-3"
          >
            <div className="w-full flex items-center justify-end gap-3.5">
              {showFeedbackButton && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    addFeatureFeedback(selectedFeature, featureDetail, modCode)
                  }
                >
                  Add feedback
                </Button>
              )}
              {showApproveButton && (
                <Button
                  size="xs"
                  variant="success"
                  onClick={handleApproveFeature}
                  loading={isApproving}
                >
                  Approve
                </Button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
