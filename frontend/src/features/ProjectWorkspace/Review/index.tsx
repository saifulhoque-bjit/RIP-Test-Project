import { useParams } from "react-router-dom";
import Button from "@/components/common/Button/Button";
import { useGetIngestionListQuery } from "@/services/api/modules/sources";
import RightDrawerLayout from "@/components/common/RightDrawerLayout";
import { Chip } from "@/components/common/Chip";
import Dropdown from "@/components/common/Dropdown";
import ModuleFeatureStoryWorkspace from "@/features/ProjectWorkspace/Review/components/middle-panel/ModuleFeatureStoryWorkspace";
import ReviewProvider from "@/features/ProjectWorkspace/Review/components/ReviewProvider";
import {
  useReviewState,
  SCOPE_NOUN,
} from "@/features/ProjectWorkspace/Review/components/ReviewContext";
import { useFeedbackSubmitHandlers } from "@/features/ProjectWorkspace/Review/hooks/useFeedbackSubmitHandlers";
import { useFeedbackDraft } from "@/features/ProjectWorkspace/Review/hooks/useFeedbackDraft";
import { useArchitectureApproval } from "@/features/ProjectWorkspace/Review/hooks/useArchitectureApproval";
import FeedbackBar from "@/features/ProjectWorkspace/Review/components/FeedbackBar";
import Modal from "@/components/common/Modal/index";
import { useAppSelector } from "@/store/hooks";
import { selectProjectFeedback } from "@/store/slices/userStoryFeedbackSlice";
import { selectProjectModuleFeedback } from "@/store/slices/moduleFeedbackSlice";
import { selectProjectFeatureFeedback } from "@/store/slices/featureFeedbackSlice";
import { SUGGESTED_FEEDBACK_ACTIONS } from "@/types/user-story";
import type { SuggestedFeedbackAction } from "@/types/user-story";

const SUGGESTED_ACTION_OPTIONS = [
  { label: "None", value: "" },
  ...SUGGESTED_FEEDBACK_ACTIONS.map((action) => ({
    label: action,
    value: action,
  })),
];

export default function Review() {
  const { id: projectId } = useParams<{ id: string }>();
  return (
    //  ReviewProvider wrapper fetches MODULE-FEAT-USER_STORY TREE DATA
    <ReviewProvider projectId={projectId}>
      <ReviewPageContent />
    </ReviewProvider>
  );
}

function ReviewPageContent() {
  const { id: projectId } = useParams<{ id: string }>();
  const {
    treeItems,
    projectType,
    feedbackDrawerMode,
    feedbackScope,
    activeFeedbackTarget,
    openFeedbackList,
    closeFeedbackDrawer: closeFeedbackDrawerFromContext,
    selectedStoryText,
  } = useReviewState();

  const moduleFeedback = useAppSelector((state) =>
    projectId ? selectProjectModuleFeedback(projectId)(state) : undefined,
  );
  const featureFeedback = useAppSelector((state) =>
    projectId ? selectProjectFeatureFeedback(projectId)(state) : undefined,
  );
  const storyFeedback = useAppSelector((state) =>
    projectId ? selectProjectFeedback(projectId)(state) : undefined,
  );

  const moduleNotes = moduleFeedback?.notes ?? [];
  const featureItems = featureFeedback?.features ?? [];
  const storyItems = storyFeedback?.user_stories ?? [];

  // source_code submits feature + story feedback as one combined
  // feedback_items array, so the bar/list UI shows them as one queue too —
  // the combined bar always uses "feature" as its FeedbackScope for routing
  // (submitHandlers/submitLoading are identical for "feature" and "story" in
  // source_code, since both point at the same combined submit action).
  const isSourceCodeFeedback = projectType === "source_code";
  const isCombinedFeedbackView =
    isSourceCodeFeedback && feedbackScope === "feature";

  const {
    note,
    setNote,
    suggestedAction,
    setSuggestedAction,
    closeFeedbackDrawer,
    handleAddFeedback,
    handleRemoveFeedback,
    handleClearAllFeedback,
    listRows,
    storyGroups,
    storyEntryCount,
  } = useFeedbackDraft({
    projectId,
    feedbackScope,
    activeFeedbackTarget,
    selectedStoryText,
    isCombinedFeedbackView,
    closeFeedbackDrawerFromContext,
    treeItems,
    moduleNotes,
    featureItems,
    storyItems,
  });

  const {
    isModalOpen: isApproveArchitectureModalOpen,
    openModal: openApproveArchitectureModal,
    closeModal: closeApproveArchitectureModal,
    isApproving: isApprovingArchitecture,
    handleConfirm: handleConfirmApproveArchitecture,
  } = useArchitectureApproval(projectId);

  // Temporary Solution: to skip AI processing. No separate toggle here —
  // silently reuses whatever was chosen on the originating upload screen's
  // "Skip Processing" switch, so approval carries it through automatically.
  // isIngestionLoading gates the Approve button below: without it, a click
  // that lands before this query resolves would silently fall back to
  // `false` even when the upload was actually skip-processed.
  //
  // Scoped to the modal being open. This flag is read nowhere else on Review,
  // and a standing subscription would refetch on every IngestionJob
  // LIST-<projectId> invalidation — which every story approval, feedback
  // regen, terminal WS task, and WS reconnect fires — to feed a modal that
  // isn't on screen.
  // Unsubscribed, RTK Query drops the entry instead of refetching it. The
  // isIngestionLoading guard still holds: the modal only renders while open,
  // the query starts on open, and a cold/evicted/GC'd entry all report
  // isLoading until skip_processing actually resolves.
  const { data: ingestionResponse, isLoading: isIngestionLoading } =
    useGetIngestionListQuery(
      { projectId: projectId!, skip: 0, limit: 1 },
      { skip: !projectId || !isApproveArchitectureModalOpen },
    );
  const skipProcessing =
    ingestionResponse?.data?.items?.[0]?.skip_processing ?? false;

  const {
    handleSubmitModuleFeedback,
    handleSubmitFeatureFeedback,
    handleSubmitStoryFeedback,
    submitHandlers,
    submitLoading,
  } = useFeedbackSubmitHandlers({
    projectId,
    projectType,
    moduleNotes,
    featureItems,
    storyItems,
    closeFeedbackDrawer,
  });

  return (
    <>
      <div className="relative flex h-full w-full flex-col items-center justify-start gap-4">
        <ModuleFeatureStoryWorkspace
          onApproveArchitecture={openApproveArchitectureModal}
        />

        {/* For RFP Stage 1 Only */}
        {moduleNotes.length > 0 && (
          <FeedbackBar
            scope="module"
            count={moduleNotes.length}
            onReview={() => openFeedbackList("module")}
            onSubmit={handleSubmitModuleFeedback}
            isSubmitting={submitLoading.module}
          />
        )}

        {/* For Source Code Feature and Story level feedback */}
        {isSourceCodeFeedback
          ? (featureItems.length > 0 || storyItems.length > 0) && (
              <FeedbackBar
                scope="feature"
                label="feature & story"
                count={featureItems.length + storyItems.length}
                onReview={() => openFeedbackList("feature")}
                onSubmit={handleSubmitFeatureFeedback}
                isSubmitting={submitLoading.feature}
              />
            )
          : // For RFP Story level feedback
            storyItems.length > 0 && (
              <FeedbackBar
                scope="story"
                count={storyItems.length}
                onReview={() => openFeedbackList("story")}
                onSubmit={handleSubmitStoryFeedback}
                isSubmitting={submitLoading.story}
              />
            )}
      </div>

      <RightDrawerLayout
        isOpen={feedbackDrawerMode === "compose"}
        title="Add feedback"
        onClose={closeFeedbackDrawer}
        onSecondaryAction={closeFeedbackDrawer}
        onPrimaryAction={handleAddFeedback}
        secondaryActionLabel="Cancel"
        primaryActionLabel="Add to feedback"
        primaryActionDisabled={!note.trim()}
      >
        {feedbackScope && activeFeedbackTarget && (
          <>
            <div className="mb-2 flex items-center gap-2 text-[12.5px] font-semibold">
              <Chip tone="info">{SCOPE_NOUN[feedbackScope]}</Chip>
              {activeFeedbackTarget.label}
            </div>

            {feedbackScope === "story" && (
              <div className="mb-3">
                <Dropdown
                  label="Suggested action · optional"
                  options={SUGGESTED_ACTION_OPTIONS}
                  selected={suggestedAction}
                  onChange={(option) =>
                    setSuggestedAction(
                      option.value as SuggestedFeedbackAction | "",
                    )
                  }
                  size="sm"
                  className="w-full"
                />
              </div>
            )}

            {feedbackScope === "story" && selectedStoryText && (
              <div className="mb-3 rounded-md border border-[var(--border-strong)] bg-[#fafbfd] p-2.5 text-[12.5px] text-[var(--text-secondary)]">
                <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.4px] text-[var(--text-tertiary)]">
                  Selected text
                </div>
                “{selectedStoryText}”
              </div>
            )}

            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder={`Describe the change you want the AI to make to this ${SCOPE_NOUN[feedbackScope]} on regeneration…`}
              className="min-h-[120px] w-full resize-y rounded-md border border-[var(--border-strong)] p-2.5 text-[13px] outline-none focus:border-[var(--accent)]"
            />
          </>
        )}
      </RightDrawerLayout>

      {(() => {
        const renderListRows = () =>
          listRows.length === 0 ? (
            <p className="text-[13px] text-[var(--text-tertiary)]">
              No pending feedback yet. Use "Add feedback" on any{" "}
              {feedbackScope ? SCOPE_NOUN[feedbackScope] : "item"} — items
              collect here and submit together.
            </p>
          ) : (
            listRows.map((row) => (
              <div key={row.id} className="mb-4">
                <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[0.4px] text-[var(--text-tertiary)]">
                  {feedbackScope && (
                    <Chip tone="info">{SCOPE_NOUN[feedbackScope]}</Chip>
                  )}
                  {row.label}
                </div>
                <div className="rounded-md border border-[var(--border-primary)] p-3 text-[12.5px]">
                  <div className="mb-1 flex justify-end">
                    <button
                      type="button"
                      className="text-[var(--error)]"
                      onClick={() =>
                        feedbackScope &&
                        handleRemoveFeedback(feedbackScope, row.id)
                      }
                    >
                      remove
                    </button>
                  </div>
                  {row.suggestedAction && (
                    <div className="mb-1">
                      <Chip tone="ai">{row.suggestedAction}</Chip>
                    </div>
                  )}
                  <div className="leading-snug text-[var(--text-secondary)]">
                    {row.text}
                  </div>
                </div>
              </div>
            ))
          );

        const renderStoryGroups = () =>
          storyGroups.length === 0 ? (
            <p className="text-[13px] text-[var(--text-tertiary)]">
              No pending feedback yet. Use "Add feedback" or highlight text on
              any story — items collect here and submit together.
            </p>
          ) : (
            storyGroups.map((group) => (
              <div key={group.userStoryId} className="mb-4">
                <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[0.4px] text-[var(--text-tertiary)]">
                  <Chip tone="info">story</Chip>
                  {group.label}
                </div>
                <div className="flex flex-col gap-2">
                  {group.entries.map((entry) => (
                    <div
                      key={entry.key}
                      className="rounded-md border border-[var(--border-primary)] p-3 text-[12.5px]"
                    >
                      <div className="mb-1 flex justify-end">
                        <button
                          type="button"
                          className="text-[var(--error)]"
                          onClick={entry.onRemove}
                        >
                          remove
                        </button>
                      </div>
                      {entry.suggestedAction && (
                        <div className="mb-1">
                          <Chip tone="ai">{entry.suggestedAction}</Chip>
                        </div>
                      )}
                      <div className="leading-snug text-[var(--text-secondary)]">
                        {entry.text}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))
          );

        return (
          <RightDrawerLayout
            isOpen={feedbackDrawerMode === "list"}
            title={
              isCombinedFeedbackView
                ? "Pending feedback"
                : feedbackScope
                  ? `Pending ${SCOPE_NOUN[feedbackScope]} feedback`
                  : "Pending feedback"
            }
            count={
              isCombinedFeedbackView
                ? featureItems.length + storyEntryCount
                : feedbackScope === "story"
                  ? storyEntryCount
                  : listRows.length
            }
            onClose={closeFeedbackDrawer}
            onSecondaryAction={handleClearAllFeedback}
            onPrimaryAction={
              feedbackScope ? submitHandlers[feedbackScope] : undefined
            }
            secondaryActionLabel="Clear"
            primaryActionLabel="Submit & regenerate"
            primaryActionDisabled={
              isCombinedFeedbackView
                ? !(featureItems.length + storyEntryCount)
                : !(feedbackScope === "story"
                    ? storyEntryCount
                    : listRows.length)
            }
            primaryActionLoading={
              feedbackScope ? submitLoading[feedbackScope] : false
            }
          >
            {isCombinedFeedbackView ? (
              <>
                <p className="m-0 mb-2 text-[11px] font-bold uppercase tracking-[0.4px] text-[var(--text-tertiary)]">
                  Feature feedback
                </p>
                <div className="mb-4">{renderListRows()}</div>
                <p className="m-0 mb-2 text-[11px] font-bold uppercase tracking-[0.4px] text-[var(--text-tertiary)]">
                  Story feedback
                </p>
                {renderStoryGroups()}
              </>
            ) : feedbackScope === "story" ? (
              renderStoryGroups()
            ) : (
              renderListRows()
            )}
          </RightDrawerLayout>
        );
      })()}

      <Modal
        isOpen={isApproveArchitectureModalOpen}
        onClose={closeApproveArchitectureModal}
        title="Approve Architecture"
        disableBackdropClose={isApprovingArchitecture}
        disableEscClose={isApprovingArchitecture}
        width={450}
        footer={
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={closeApproveArchitectureModal}
              disabled={isApprovingArchitecture}
            >
              Cancel
            </Button>
            <Button
              variant="success"
              size="sm"
              onClick={() => handleConfirmApproveArchitecture(skipProcessing)}
              loading={isApprovingArchitecture}
              disabled={isIngestionLoading}
            >
              Approve architecture
            </Button>
          </>
        }
      >
        <p className="m-0 text-[length:var(--font-size-sm)] leading-6 text-[var(--color-neutral-400)]">
          Are you sure you want to approve all modules and features at once?
        </p>
      </Modal>
    </>
  );
}
