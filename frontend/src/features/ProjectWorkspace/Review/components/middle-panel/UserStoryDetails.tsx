import { useState } from "react";
import type { UserStoryItem } from "@/components/common/TreePanel";
import { Button } from "@/components/common/Button/Button";
import ButtonPopover from "@/components/common/PopoverWrapper/ButtonPopover";
import { MenuIcon } from "@/assets/icons/MenuIcon";
import ReviewGuidanceMessage from "../ReviewGuidanceMessage";
import DetailPanelHeader from "./DetailPanelHeader";
import type {
  AcceptanceCriterion,
  RequirementDetailData,
} from "@/types/user-story";
import type { FeatureGenerationMetadata } from "@/types";
import { displayAsciiLayout } from "./displayAsciiLayout";
import Modal from "@/components/common/Modal/index";
import ChangeDiffPanel, { type ChangeDiffVersion } from "./ChangeDiffPanel";
import { shouldShowStoryFeedback } from "@/features/ProjectWorkspace/Review/utils/reviewActionVisibility";
import { useStoryFeedbackAction } from "@/features/ProjectWorkspace/Review/hooks/useFeedbackComposeActions";
import { getEffectiveChangeType } from "@/utils/changeType";

const storyToLines = (story?: {
  as_a?: string;
  i_want_to?: string;
  so_that?: string;
  acceptance_criteria?: AcceptanceCriterion[];
}) => {
  if (!story) return [];

  const narrativeSentence =
    story.as_a || story.i_want_to || story.so_that
      ? `As a ${story.as_a ?? ""}, I want to ${story.i_want_to ?? ""}, so that ${story.so_that ?? ""}`
      : "";

  return [
    narrativeSentence,
    ...(story.acceptance_criteria ?? []).flatMap((ac) => [
      ac.given,
      ac.when,
      ac.then,
    ]),
  ].filter(Boolean);
};

interface UserStoryDetailsProps {
  selectedStory: UserStoryItem | null;
  requirementDetail: RequirementDetailData | null;
  isLoadingDetail: boolean;
  isDetailError: boolean;
  selectedAcceptanceCriterion: AcceptanceCriterion | null;
  onSelectAcceptanceCriterion: (
    acceptanceCriterion: AcceptanceCriterion | null,
  ) => void;
  handleApproveStory: () => void | Promise<void>;
  handleDeleteStory: () => void | Promise<void>;
  isApproving?: boolean;
  isDeleting?: boolean;
  canApprove?: boolean;
  projectType?: string | null;
  /** Generation metadata from the parent feature — drives ReviewGuidanceMessage. */
  featureGenerationMetadata?: FeatureGenerationMetadata | null;
  /** When false, the "Add feedback" affordance is hidden. */
  enableFeedback?: boolean;
  /** True while a feedback-regeneration task is in flight for this exact story — shows a skeleton and blocks all actions. */
  isFeedbackRegenerating?: boolean;
  /** source_code only — the source-code ingestion pipeline (module/feature/user-story generation) is still running, so the tree isn't final yet: hides Add feedback/Approve/Reject & delete. */
  isPipelineRunning?: boolean;
}

export default function UserStoryDetails({
  selectedStory,
  requirementDetail,
  isLoadingDetail,
  isDetailError,
  selectedAcceptanceCriterion,
  onSelectAcceptanceCriterion,
  handleApproveStory,
  handleDeleteStory,
  isApproving = false,
  isDeleting = false,
  canApprove = true,
  projectType,
  featureGenerationMetadata = null,
  enableFeedback = true,
  isFeedbackRegenerating = false,
  isPipelineRunning = false,
}: UserStoryDetailsProps) {
  const [asciiLayoutList, setAsciiLayoutList] = useState<string[]>([]);
  const [isAsciiLayoutLoading, setIsAsciiLayoutLoading] =
    useState<boolean>(false);
  const [asciiLayoutError, setAsciiLayoutError] = useState<string>("");
  const [asciiLayoutSection, setAsciiLayoutSection] = useState<string>("");
  const [isUiLayoutModalOpen, setIsUiLayoutModalOpen] =
    useState<boolean>(false);
  const addStoryFeedback = useStoryFeedbackAction();

  const detail = requirementDetail;
  const canSelectAcceptanceCriteria = projectType === "source_code";

  const changedAction = getEffectiveChangeType(selectedStory);
  // `detail` always holds the story's own current attributes. For "UPDATED",
  // `detail.last_previous_items` additionally holds the full prior-version
  // snapshot, so the attribute-by-attribute before → after table can be built
  // from both. For "ADDED"/"DELETE_SUGGESTED" there's nothing to diff against
  // — `detail` alone (rendered as a single success/error-tinted column) is
  // the newly proposed or soon-to-be-removed story.
  const attributeDiff =
    changedAction && detail
      ? {
          kind: "user_story" as const,
          current: {
            title: detail.title,
            as_a: detail.as_a,
            i_want_to: detail.i_want_to,
            so_that: detail.so_that,
            technical_notes: detail.technical_notes,
            story_points: detail.story_points,
            acceptance_criteria: detail.acceptance_criteria,
            nfrs: detail.nfrs,
          },
          previous:
            changedAction === "UPDATED" && detail.last_previous_items
              ? {
                  title: detail.last_previous_items.title,
                  as_a: detail.last_previous_items.as_a,
                  i_want_to: detail.last_previous_items.i_want_to,
                  so_that: detail.last_previous_items.so_that,
                  technical_notes: detail.last_previous_items.technical_notes,
                  story_points: detail.last_previous_items.story_points,
                  acceptance_criteria:
                    detail.last_previous_items.acceptance_criteria,
                  nfrs: detail.last_previous_items.nfrs,
                }
              : undefined,
        }
      : undefined;
  const currentVersion: ChangeDiffVersion | undefined = changedAction
    ? {
        label: detail?.version ? `Current · v${detail.version}` : "Current",
        narrative: detail?.title ?? selectedStory?.name ?? "",
        lines: storyToLines(detail ?? undefined),
      }
    : undefined;
  const previousVersion =
    changedAction === "DELETE_SUGGESTED" ? currentVersion : undefined;
  const proposedVersion =
    changedAction === "ADDED" ? currentVersion : undefined;

  if (!selectedStory) {
    return (
      <div className="flex h-full w-full items-center justify-center text-[var(--text-tertiary)]">
        No user story selected.
      </div>
    );
  }

  if (isFeedbackRegenerating) {
    return (
      <div
        id="story-slot"
        className="animate-pulse rounded-[0_12px_12px_0] border border-[var(--border-primary)] border-l-4 border-l-[var(--ai)] bg-white shadow-[var(--e1)]"
        aria-busy="true"
        aria-label="Regenerating user story from feedback"
      >
        <div className="border-b border-[var(--border-primary)] px-[18px] py-4">
          <div className="h-4 w-1/2 rounded bg-[#e4e8ef]" />
          <div className="mt-2 h-3 w-1/4 rounded bg-[#e4e8ef]" />
        </div>
        <div className="flex flex-col gap-2.5 p-[16px_18px]">
          <div className="h-3 w-full rounded bg-[#e4e8ef]" />
          <div className="h-3 w-11/12 rounded bg-[#e4e8ef]" />
          <div className="h-3 w-3/4 rounded bg-[#e4e8ef]" />
        </div>
        <div className="flex flex-col gap-2 border-t border-[var(--border-primary)] bg-[#fafbfd] px-[18px] py-3">
          <div className="h-3 w-40 rounded bg-[#e4e8ef]" />
          <p className="m-0 text-[11px] text-[var(--text-tertiary)]">
            Regenerating this user story from feedback…
          </p>
        </div>
      </div>
    );
  }

  if (isLoadingDetail) {
    return (
      <div className="flex h-full w-full items-center justify-center text-[var(--text-tertiary)]">
        Loading user story details...
      </div>
    );
  }

  if (isDetailError) {
    return (
      <div className="flex h-full w-full items-center justify-center text-red-500">
        Failed to load user story details.
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="flex h-full w-full items-center justify-center text-[var(--text-tertiary)]">
        No user story details available.
      </div>
    );
  }

  const showFeedbackButton = shouldShowStoryFeedback({
    enableFeedback,
    detail,
    isPipelineRunning,
  });

  return (
    <div id="ip-story">
      {/* ————— Review guidance — driven by parent feature generation_metadata ————— */}
      {featureGenerationMetadata && (
        <ReviewGuidanceMessage generationMetadata={featureGenerationMetadata} />
      )}

      <div
        id="story-slot"
        className={`rounded-[0_12px_12px_0] border border-[var(--border-primary)] border-l-4 ${detail?.status === "needs_edit" ? "border-l-[var(--color-warn)]" : detail?.status === "failed" ? "border-l-[var(--error)]" : detail?.status === "approved" ? "border-l-[var(--color-success)]" : "border-l-[var(--ai)]"} bg-white shadow-[var(--e1)]`}
      >
        {/* ————— Header ———————————————————————————————————————— */}
        <DetailPanelHeader
          title={
            detail?.title ?? selectedStory.name ?? selectedStory.label ?? ""
          }
          code={detail?.user_story_code}
          actions={
            isPipelineRunning ? undefined : (
              <ButtonPopover
                body={({ close }) => (
                  <div className="flex flex-col">
                    <button
                      type="button"
                      className="h-9 w-full rounded-[6px] px-[10px] text-left text-[13px] text-[var(--error)] hover:bg-[#fafbfd] disabled:cursor-not-allowed disabled:opacity-50"
                      onClick={() => {
                        void handleDeleteStory();
                        close();
                      }}
                      disabled={isDeleting}
                    >
                      Reject & delete
                    </button>
                  </div>
                )}
                contentClassName="z-[60] min-w-[150px] rounded-[8px] border border-[var(--border-primary)] p-[6px] shadow-[var(--e2)]"
                align="end"
              >
                <button
                  type="button"
                  className="inline-flex h-7 w-7 items-center justify-center rounded-[6px] border-none bg-transparent text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]"
                  aria-label="Story actions"
                >
                  <MenuIcon className="h-[18px] w-[18px]" />
                </button>
              </ButtonPopover>
            )
          }
          badges={
            <>
              {detail?.status && (
                <span className="inline-flex rounded-[14px] bg-[var(--info-50)] px-[9px] py-[3px] text-[11px] font-semibold text-[var(--info)]">
                  {detail.status}
                </span>
              )}
              {detail?.acceptance_criteria && (
                <span className="inline-flex rounded-[14px] bg-[var(--info-50)] px-[9px] py-[3px] text-[11px] font-semibold text-[var(--info)]">
                  {detail.acceptance_criteria.length} acceptance criteria
                </span>
              )}
              {detail?.story_points != null && (
                <span className="inline-flex rounded-[14px] bg-[var(--info-50)] px-[9px] py-[3px] text-[11px] font-semibold text-[var(--info)]">
                  {detail.story_points} SP
                </span>
              )}
            </>
          }
        />

        {changedAction && (
          <div className="border-b border-[var(--border-primary)] p-[16px_18px]">
            <ChangeDiffPanel
              changedAction={changedAction}
              previous={previousVersion}
              proposed={proposedVersion}
              attributeDiff={attributeDiff}
            />
          </div>
        )}

        {!changedAction && detail?.as_a && (
          <div className="border-b border-[var(--border-primary)] p-[16px_18px]">
            <p className="m-0 mb-2 text-[11px] font-bold uppercase tracking-[0.5px] text-[var(--mut)]">
              User story
            </p>
            <p className="m-0 text-sm leading-[1.6] text-[var(--text-primary)]">
              <strong>As a</strong> {detail.as_a}, <strong>I want to</strong>{" "}
              {detail.i_want_to}, <strong>so that</strong> {detail.so_that}
            </p>
          </div>
        )}

        {/* ————— Description ———————————————————————————————————————— */}
        {!changedAction && detail?.description && (
          <div className="border-b border-[var(--border-primary)] p-[16px_18px]">
            <p className="m-0 mb-2 text-[11px] font-bold uppercase tracking-[0.5px] text-[var(--mut)]">
              Description
            </p>
            <p className="m-0 text-sm leading-[1.6] text-[var(--text-primary)]">
              {detail.description}
            </p>
          </div>
        )}

        {/* ————— Acceptance Criteria ———————————————————————————————————————— */}
        {!changedAction &&
          detail?.acceptance_criteria &&
          detail.acceptance_criteria.length > 0 && (
            <div className="p-[16px_18px]">
              <div className="mb-2 flex justify-start items-center gap-1.5">
                <p className="m-0 text-[11px] font-bold uppercase tracking-[0.5px] text-[var(--mut)]">
                  Acceptance criteria
                </p>
                <span
                  aria-hidden="true"
                  className="h-[2px] w-[2px] shrink-0 self-center rounded-full bg-[var(--mut)] "
                />
                <p className="m-0 text-[11px] text-[var(--text-tertiary)]">
                  {canSelectAcceptanceCriteria
                    ? "click a row to highlight its source evidence"
                    : "acceptance criteria selection is available for source code projects only"}
                </p>
              </div>
              <div className="flex flex-col gap-2">
                {detail.acceptance_criteria.map((ac, idx) =>
                  (() => {
                    const isSelected =
                      canSelectAcceptanceCriteria &&
                      selectedAcceptanceCriterion?.ac_code?.trim() ===
                        ac.ac_code?.trim();

                    return (
                      <div
                        key={ac.ac_code || idx}
                        className={`flex gap-2.5 rounded-[0_8px_8px_0] border border-l-[3px] p-[11px_12px] text-[13px] leading-[1.5] ${canSelectAcceptanceCriteria ? "cursor-pointer" : "cursor-default opacity-90"} ${isSelected ? "border-[var(--accent)] border-l-[var(--accent)] bg-[var(--accent-50)]" : `border-[var(--border-primary)] border-l-[var(--accent)] bg-[#fafbfd] ${canSelectAcceptanceCriteria ? "hover:border-[var(--border-strong)]" : ""}`}`}
                        onClick={() => {
                          if (!canSelectAcceptanceCriteria) return;
                          onSelectAcceptanceCriterion(ac);
                        }}
                      >
                        <span className="flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-[6px] bg-[var(--accent-50)] text-[12px] text-[var(--accent)]">
                          ◎
                        </span>
                        <div>
                          <div className="mb-[3px] text-[10px] font-bold uppercase tracking-[0.4px] text-[var(--text-tertiary)]">
                            {ac.type || ac.ac_code || `AC ${idx + 1}`}
                          </div>
                          <div>
                            <strong className="text-[var(--accent)]">
                              Given
                            </strong>{" "}
                            {ac.given}{" "}
                            <strong className="text-[var(--accent)]">
                              When
                            </strong>{" "}
                            {ac.when}{" "}
                            <strong className="text-[var(--accent)]">
                              Then
                            </strong>{" "}
                            {ac.then}
                          </div>
                        </div>
                      </div>
                    );
                  })(),
                )}
              </div>
            </div>
          )}

        {/* ————— NFRs ———————————————————————————————————————— */}
        {/* {detail?.nfrs && detail.nfrs.length > 0 && ( */}
        {!changedAction && detail?.nfrs && detail.nfrs.length > 0 && (
          <div className="border-b border-[var(--border-primary)] p-[16px_18px]">
            <p className="m-0 mb-2 text-[11px] font-bold uppercase tracking-[0.5px] text-[var(--mut)]">
              Non-functional requirements
            </p>
            <ul className="flex flex-col gap-2.5">
              {detail.nfrs.map((nfr, idx) => (
                <li
                  className="flex flex-col gap-2 p-3 bg-[#FAFBFD] rounded-[8px]"
                  key={nfr.id || idx}
                >
                  <span className="w-full min-h-5.5 px-[7px] rounded-[5px] inline-flex justify-start items-center font-semibold text-[10px] text-[#818DA9] bg-[var(--accent-50)]">
                    Category: {nfr.category}
                  </span>
                  <span className="inline-flex justify-start items-center text-[13px] text-[var(--ink)]">
                    Requirement: {nfr.requirement || "——"}
                  </span>
                  <span className="inline-flex justify-start items-center text-[13px] text-[var(--ink)]">
                    Description: {nfr.description || "——"}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* ————— Technical Notes ———————————————————————————————————————— */}
        {/* {detail?.technical_notes && (
          <div className="border-b border-[var(--border-primary)] p-[16px_18px]">
            <p className="m-0 mb-2 text-[11px] font-bold uppercase tracking-[0.5px] text-[var(--mut)]">
              Technical notes
            </p>
            <p className="m-0 text-sm leading-[1.6] text-[var(--text-primary)]">
              {detail.technical_notes}
            </p>
          </div>
        )} */}

        {/* ————— Footer ———————————————————————————————————————— */}
        <div
          data-feedback-scope="excluded"
          className="flex flex-col items-start justify-between gap-2 border-t border-[var(--border-primary)] bg-[#fafbfd] px-[18px] py-3"
        >
          <div className="w-full flex items-center justify-end gap-3.5">
            {projectType === "source_code" && (
              <Button
                variant="ghost"
                size="sm"
                iconLeading="▤"
                onClick={() => {
                  const firstMarkdownScreen = detail?.screens?.find((screen) =>
                    screen.ascii_layout_ref?.srs_file
                      ?.trim()
                      .toLowerCase()
                      .endsWith(".md"),
                  );
                  setAsciiLayoutSection(
                    firstMarkdownScreen?.ascii_layout_ref?.section?.trim() ||
                      "selected section",
                  );
                  setIsUiLayoutModalOpen(true);
                  void displayAsciiLayout({
                    screens: detail?.screens,
                    setAsciiLayoutList,
                    setAsciiLayoutError,
                    setIsAsciiLayoutLoading,
                  });
                }}
              >
                View UI layout
              </Button>
            )}
            {showFeedbackButton && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => addStoryFeedback(selectedStory, detail)}
              >
                Add feedback
              </Button>
            )}
            {canApprove && !isPipelineRunning && (
              <Button
                size="xs"
                variant="success"
                onClick={handleApproveStory}
                loading={isApproving}
              >
                Approve
              </Button>
            )}
          </div>
        </div>
      </div>

      <Modal
        isOpen={isUiLayoutModalOpen}
        onClose={() => setIsUiLayoutModalOpen(false)}
        title="UI layout"
        width={720}
        maxHeight="80vh"
      >
        <div className="max-h-[60vh] overflow-auto space-y-2">
          {isAsciiLayoutLoading && (
            <p className="text-[length:var(--font-size-xxsm)] text-[color:var(--color-neutral-400)] animate-pulse">
              Loading ASCII layout...
            </p>
          )}

          {!isAsciiLayoutLoading && !!asciiLayoutError && (
            <p className="text-[length:var(--font-size-xxsm)] text-[color:var(--text-warning)]">
              {asciiLayoutError}
            </p>
          )}

          {!isAsciiLayoutLoading &&
            !asciiLayoutError &&
            asciiLayoutList.map((layout, index) => (
              <div key={`${index}-${layout.length}`} className="space-y-1.5">
                <p className="m-0 pb-3 text-[length:var(--font-size-xxsm)] font-medium text-[color:var(--text-secondary)]">
                  {`ASCII visual zone map parsed from '${asciiLayoutSection}'`}
                </p>
                <pre className="whitespace-pre-wrap break-words rounded-[6px] border border-[color:var(--color-brand-green-100)] bg-[color:var(--navy-900)] p-2 text-[length:var(--font-size-xxsm)] text-[color:#cdd9f0]">
                  {layout}
                </pre>
              </div>
            ))}
        </div>
      </Modal>
    </div>
  );
}
