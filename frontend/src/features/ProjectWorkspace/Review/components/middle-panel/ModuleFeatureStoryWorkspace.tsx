import { useMemo, useState } from "react";
import { useLocation, useParams, useSearchParams } from "react-router-dom";
import TreePanel from "../../../../../components/common/TreePanel";
import ModuleDetails from "./ModuleDetails";
import FeatureDetails from "./FeatureDetails";
import UserStoryDetails from "./UserStoryDetails";
import { useGetProjectRequirementDetailQuery } from "@/services/api/modules/user-stories";
import { useGetProjectFeatureDetailQuery } from "@/services/api/modules/features";
import { useGetProjectModuleDetailQuery } from "@/services/api/modules/modules";
import UserStoryTreeControls, {
  type StoryFilter,
  type UpdateFilter,
} from "../UserStoryTreeControls";
import Modal from "@/components/common/Modal";
import Button from "@/components/common/Button/Button";
import PillTabs from "@/components/common/PillTabs";
import SrcEvidence from "../right-panel/SrcEvidence";
import type { AcceptanceCriterion } from "@/types/user-story";
import EmptyState from "@/components/common/EmptyState/EmptyState";
import Loader from "@/components/common/Loader";
import { useReviewState } from "@/features/ProjectWorkspace/Review/components/ReviewContext";
import { useIsProjectBusy } from "@/hooks/useProjectTaskStatus";
import { TASK_TYPE } from "@/types/projectTask";
import { useStoryFeedbackRegenerationStatus } from "@/features/ProjectWorkspace/Review/hooks/useStoryFeedbackRegenerationStatus";
import { useFeatureFeedbackRegenerationStatus } from "@/features/ProjectWorkspace/Review/hooks/useFeatureFeedbackRegenerationStatus";
import { useIncrementalChangeAction } from "@/features/ProjectWorkspace/Review/hooks/useIncrementalChangeAction";
import { useUserStoryApproval } from "@/features/ProjectWorkspace/Review/hooks/useUserStoryApproval";
import {
  computeEffectiveBusyStoryIds,
  countChangesByType,
  countStoriesByStatusCategory,
  countTreeChanges,
  filterTreeByApprovedScope,
  filterTreeByStoryFilter,
  filterTreeByUpdateFilter,
  findNodeById,
  findParentFeatureIdByStoryId,
  findParentModuleIdByFeatureId,
  getDefaultSelectedId,
  hasAnyFeedbackChangeType,
  mapTreeData,
  resolveActiveSelectedId,
} from "@/features/ProjectWorkspace/Review/utils/userStoryTree";
import MiddlePanelLayout from "./MiddlePanelLayout";
import { getEffectiveChangeType } from "@/utils/changeType";

type TreeViewMode = "baseline" | "updates";

// When the URL's "source_type" param is one of these, the source that landed
// the user here is itself an incremental change, so the Updates tab (rather
// than Baseline) should be selected by default.
const SOURCE_TYPES_DEFAULTING_TO_UPDATES = new Set([
  "requirement_update",
  "requirement_update_from_feedback",
  "meeting_notes",
  "additional_rfp",
]);

interface ModuleFeatureStoryWorkspaceProps {
  /** Show only approved/locked stories instead of excluding them. */
  approvedOnly?: boolean;
  /** Show the status filter chips (attention/ready/all). Search stays available either way. */
  enableFilters?: boolean;
  /** Show approve affordances (module/feature/story) and the approval modal. */
  enableApprove?: boolean;
  /** Show "add feedback" affordances and the selection-to-feedback hint. */
  enableFeedback?: boolean;
  /** Called when the user clicks "Approve architecture & generate user stories". */
  onApproveArchitecture?: () => void;
}

export default function ModuleFeatureStoryWorkspace({
  approvedOnly = false,
  enableFilters = true,
  enableApprove = true,
  enableFeedback = true,
  onApproveArchitecture,
}: ModuleFeatureStoryWorkspaceProps = {}) {
  const { id: projectId } = useParams<{ id: string }>();
  const location = useLocation();
  const isReviewPage = location.pathname.endsWith("/review");
  const [searchParams] = useSearchParams();
  // When set, narrows the "Updates" tab (count, per-type breakdown, and the
  // listed tree) to changes from this one source ingestion run.
  const ingestionId = searchParams.get("ingestion_id") ?? undefined;
  const sourceType = searchParams.get("source_type");
  const isProjectBusy = useIsProjectBusy(projectId);
  const busyFeedbackStoryIds = useStoryFeedbackRegenerationStatus(projectId);
  const busyFeatureIds = useFeatureFeedbackRegenerationStatus(projectId);
  // Module-feedback regeneration rebuilds the whole module/feature tree, so
  // (unlike feature/story) there's no per-entity id to track — the backend
  // broadcasts it project-wide, and this flag alone drives the skeletons in
  // ModuleDetails/FeatureDetails for as long as either task type is live.
  const isModuleRegenerationBusy = useIsProjectBusy(
    projectId,
    TASK_TYPE.MODULE_REGENERATION,
  );
  const isModuleFeedbackPatchBusy = useIsProjectBusy(
    projectId,
    TASK_TYPE.MODULE_FEEDBACK_PATCH,
  );
  const isModulesRegenerating =
    isModuleRegenerationBusy || isModuleFeedbackPatchBusy;
  // The initial module/feature/user-story generation pipeline for
  // source_code projects runs as a single SOURCE_PROCESS task (unlike
  // feedback-driven regeneration, which uses its own per-entity task types
  // tracked separately above) — while it's still running, module-feature-
  // user-story nodes arrive into the tree one at a time and aren't final yet,
  // so feedback/approve/reject actions must stay hidden until it completes.
  const isSourceProcessBusy = useIsProjectBusy(
    projectId,
    TASK_TYPE.SOURCE_PROCESS,
  );
  //  ReviewProvider wrapper fetches MODULE-FEAT-USER_STORY TREE DATA
  const {
    projectType,
    treeItems,
    stage,
    isTreeLoading,
    areAllApprovedForMod,
    openComposeFeedback,
    setSelectedStoryText,
  } = useReviewState();

  const treeData = useMemo(() => mapTreeData(treeItems), [treeItems]);

  const effectiveBusyStoryIds = useMemo(
    () =>
      computeEffectiveBusyStoryIds(
        treeData,
        busyFeedbackStoryIds,
        busyFeatureIds,
      ),
    [busyFeedbackStoryIds, busyFeatureIds, treeData],
  );

  const countCat = useMemo(
    () => countStoriesByStatusCategory(treeData),
    [treeData],
  );

  const hasUserStories =
    countCat.attention + countCat.ready + countCat.approved > 0;

  const hasPendingFeedbackChange = useMemo(
    () => hasAnyFeedbackChangeType(treeData),
    [treeData],
  );

  const [storyFilter, setStoryFilter] = useState<StoryFilter>("all");
  const [updateFilter, setUpdateFilter] = useState<UpdateFilter>("all");
  const [treeViewMode, setTreeViewMode] = useState<TreeViewMode>(
    sourceType && SOURCE_TYPES_DEFAULTING_TO_UPDATES.has(sourceType)
      ? "updates"
      : "baseline",
  );
  const [selectedAc, setSelectedAc] = useState<{
    storyId: string;
    acceptanceCriterion: AcceptanceCriterion;
  } | null>(null);

  const filteredTreeData = useMemo(
    () =>
      filterTreeByStoryFilter(treeData, { stage, approvedOnly, storyFilter }),
    [storyFilter, treeData, approvedOnly, stage],
  );

  const approvedScopedTreeData = useMemo(
    () => filterTreeByApprovedScope(treeData, approvedOnly),
    [treeData, approvedOnly],
  );

  const updatesTreeData = useMemo(
    () =>
      filterTreeByUpdateFilter(
        approvedScopedTreeData,
        updateFilter,
        ingestionId,
      ),
    [approvedScopedTreeData, updateFilter, ingestionId],
  );

  const updatesCount = useMemo(
    () => countTreeChanges(approvedScopedTreeData, ingestionId),
    [approvedScopedTreeData, ingestionId],
  );

  const updateCounts = useMemo(
    () => countChangesByType(approvedScopedTreeData, ingestionId),
    [approvedScopedTreeData, ingestionId],
  );

  const [treeQuery, setTreeQuery] = useState("");
  const [selectedId, setSelectedId] = useState("");

  // The Baseline/Updates tab strip only renders while updatesCount > 0. Once
  // the last pending change is accepted/rejected, it disappears — force
  // baseline view instead of leaving the user stranded on the (now-hidden)
  // Updates tab with an empty tree and no control to switch back.
  const effectiveTreeViewMode: TreeViewMode =
    updatesCount === 0 ? "baseline" : treeViewMode;

  const visibleTreeData =
    effectiveTreeViewMode === "updates" ? updatesTreeData : filteredTreeData;

  const defaultSelectedId = useMemo(
    () => getDefaultSelectedId(visibleTreeData),
    [visibleTreeData],
  );

  const activeSelectedId = useMemo(
    () =>
      resolveActiveSelectedId(visibleTreeData, selectedId, defaultSelectedId),
    [defaultSelectedId, visibleTreeData, selectedId],
  );

  const selectedNode = useMemo(
    () => findNodeById(visibleTreeData, activeSelectedId),
    [visibleTreeData, activeSelectedId],
  );
  const selectedModule = selectedNode?.type === "module" ? selectedNode : null;
  const selectedFeature =
    selectedNode?.type === "feature" ? selectedNode : null;
  const selectedStory = selectedNode?.type === "story" ? selectedNode : null;

  // Whether the current selection (or, for a story, its parent feature) has a
  // feedback-regeneration task in flight — drives the skeleton loaders in
  // ModuleDetails/FeatureDetails/UserStoryDetails/SrcEvidence and gates the
  // header quick actions in MiddlePanelLayout so they can't be used
  // mid-regeneration. A module-feedback regeneration rebuilds the whole
  // module/feature tree, so it counts toward both a module and a feature
  // selection, not just its own.
  const isSelectionFeedbackRegenerating = selectedStory
    ? effectiveBusyStoryIds.has(selectedStory.id)
    : selectedFeature
      ? busyFeatureIds.has(selectedFeature.id) || isModulesRegenerating
      : selectedModule
        ? isModulesRegenerating
        : false;

  const {
    activeChangeEntity,
    pendingChangeAction,
    handleRequestAcceptChange,
    handleRequestRejectChange,
    handleConfirmChangeAction,
    closePendingChangeAction,
    isAcceptingChange,
    isRejectingChange,
  } = useIncrementalChangeAction({
    projectId,
    selectedModule,
    selectedFeature,
    selectedStory,
    visibleTreeData,
    onEntityRemoved: setSelectedId,
  });

  const {
    isApproving,
    approvingTargetId,
    pendingApproval,
    handleApproveModule,
    handleApproveFeature,
    handleApproveStory,
    handleConfirmApprove,
    closePendingApproval,
    canApproveModule: canApproveModuleForSelection,
    canApproveFeature: canApproveFeatureForSelection,
    canApproveStory: canApproveStoryForSelection,
    isDeleting,
    pendingDelete,
    deleteReason,
    setDeleteReason,
    handleRequestDeleteStory,
    handleConfirmDelete,
    closePendingDelete,
  } = useUserStoryApproval({
    projectId,
    selectedModule,
    selectedFeature,
    selectedStory,
    visibleTreeData,
    effectiveBusyStoryIds,
    busyFeatureIds,
    onStoryDeleted: setSelectedId,
  });

  // When user clicks a story directly (skipping the feature node), we still need
  // the parent feature's generation_metadata to drive ReviewGuidanceMessage.
  const storyParentFeatureId = useMemo(
    () => findParentFeatureIdByStoryId(treeData, selectedStory?.id),
    [treeData, selectedStory],
  );

  // Effective feature id: the selected feature node OR the parent feature of the selected story.
  const effectiveFeatureId = selectedFeature?.id ?? storyParentFeatureId ?? "";

  const effectiveFeatureModuleId = useMemo(
    () =>
      findParentModuleIdByFeatureId(treeData, effectiveFeatureId || undefined),
    [treeData, effectiveFeatureId],
  );

  // Fetch User-Story Details
  const {
    data: detailResponse,
    // isFetching: isLoadingDetail,
    isLoading: isLoadingDetail,
    isError: isDetailError,
  } = useGetProjectRequirementDetailQuery(
    {
      projectId: projectId ?? "",
      requirementId: selectedStory?.id ?? "",
    },
    {
      skip: !projectId || !selectedStory?.id,
    },
  );

  const requirementDetail = selectedStory
    ? (detailResponse?.data ?? null)
    : null;

  // Fetch Feature Details
  const {
    data: featureDetailResponse,
    isFetching: isLoadingFeatureDetail,
    isError: isFeatureDetailError,
  } = useGetProjectFeatureDetailQuery(
    {
      projectId: projectId ?? "",
      moduleId: effectiveFeatureModuleId ?? "",
      featureId: effectiveFeatureId,
    },
    {
      skip: !projectId || !effectiveFeatureId || !effectiveFeatureModuleId,
    },
  );

  const featureDetail = effectiveFeatureId
    ? (featureDetailResponse?.data?.feature ?? null)
    : null;
  const featureModCode = effectiveFeatureId
    ? (featureDetailResponse?.data?.mod_code ?? null)
    : null;

  // Fetch Module Details
  const {
    data: moduleDetailResponse,
    isFetching: isLoadingModuleDetail,
    isError: isModuleDetailError,
  } = useGetProjectModuleDetailQuery(
    {
      projectId: projectId ?? "",
      moduleId: selectedModule?.id ?? "",
    },
    {
      skip: !projectId || !selectedModule?.id,
    },
  );

  const moduleDetail = selectedModule
    ? (moduleDetailResponse?.data?.module ?? null)
    : null;

  const activeSelectedAc =
    projectType === "source_code" &&
    selectedAc &&
    selectedStory?.id === selectedAc.storyId
      ? selectedAc.acceptanceCriterion
      : null;

  const handleSelectAcceptanceCriterion = (
    acceptanceCriterion: AcceptanceCriterion | null,
  ) => {
    if (!selectedStory?.id || projectType !== "source_code") {
      setSelectedAc(null);
      return;
    }

    if (!acceptanceCriterion) {
      setSelectedAc(null);
      return;
    }

    setSelectedAc({
      storyId: selectedStory.id,
      acceptanceCriterion,
    });
  };

  const handleSelectionCheck = () => {
    if (
      !isReviewPage ||
      (projectType !== "rfp" && projectType !== "source_code") ||
      !selectedStory ||
      getEffectiveChangeType(selectedStory) ||
      selectedStory.status === "approved" ||
      effectiveBusyStoryIds.has(selectedStory.id) ||
      isPipelineRunning
    ) {
      return;
    }

    const selection = window.getSelection();
    const text = selection?.toString().trim() ?? "";
    if (!text) return;

    const anchor = selection?.getRangeAt(0).commonAncestorContainer;
    const anchorElement =
      anchor instanceof Element ? anchor : anchor?.parentElement;
    if (anchorElement?.closest("[data-feedback-scope='excluded']")) return;

    setSelectedStoryText(text);
    openComposeFeedback("story", {
      id: selectedStory.id,
      label:
        requirementDetail?.title ??
        selectedStory.name ??
        selectedStory.label ??
        "",
      modCode: requirementDetail?.mod_code,
      mfuId: requirementDetail?.mfu_id,
      userStoryCode: requirementDetail?.user_story_code,
    });
  };

  const isPipelineRunning =
    projectType === "source_code" && isSourceProcessBusy;

  const canApproveModule = enableApprove && canApproveModuleForSelection;
  const canApproveFeature = enableApprove && canApproveFeatureForSelection;
  const canApproveStory = enableApprove && canApproveStoryForSelection;
  const showEmptyState =
    treeData.length === 0 || (approvedOnly && filteredTreeData.length === 0);
  const showApproveAllModulesBtn =
    projectType === "rfp" &&
    !hasUserStories &&
    !isProjectBusy &&
    !hasPendingFeedbackChange;
  const showModulesFeedbackBtn =
    !areAllApprovedForMod && projectType === "rfp" && !hasUserStories;

  return (
    <>
      {isTreeLoading ? (
        <div className="flex h-full w-full items-center justify-center">
          <Loader
            ariaLabel="Loading user stories"
            subtitle="Loading user stories..."
          />
        </div>
      ) : showEmptyState ? (
        <div className="flex min-h-[50vh] w-full flex-col items-center justify-center gap-2">
          <EmptyState
            title={
              approvedOnly
                ? "No approved user stories"
                : "No user stories available"
            }
            description={
              approvedOnly
                ? "There are no approved user stories to display for this project yet."
                : "There are no user stories to display for this project."
            }
          />
        </div>
      ) : (
        <>
          {/* Approve all Modules-Features for Stage-1 */}
          {showApproveAllModulesBtn && (
            <div className="w-full flex items-center justify-between gap-3 rounded-lg border border-[#bcd8f7] bg-[var(--info-50)] px-4 py-3">
              <div>
                <div className="text-[13px] font-bold">
                  RFP Story Review — Stage 1 of 2 · Modules &amp; Features
                </div>
                <div className="mt-0.5 text-[12px] text-sec">
                  Human approval required before Stage 2 (user-story generation)
                  begins.
                </div>
              </div>
              <Button
                variant="success"
                size="sm"
                onClick={onApproveArchitecture}
                disabled={isProjectBusy}
              >
                ✓ Approve &amp; generate user stories
              </Button>
            </div>
          )}

          {/* Stitching between BaseLine and Updates */}
          {updatesCount > 0 && !approvedOnly && (
            <div className="w-full flex justify-start">
              <PillTabs
                value={treeViewMode}
                onChange={setTreeViewMode}
                items={[
                  { value: "baseline", label: "Baseline" },
                  { value: "updates", label: `Updates (${updatesCount})` },
                ]}
              />
            </div>
          )}

          <div
            className="w-full h-full max-h-[700px] flex-1 flex min-h-0"
            data-stage={stage}
          >
            {/* Left Panel */}
            <div className="flex w-[21.94%] shrink-0 flex-col rounded-l-lg border-r border-[var(--border-primary)] bg-white">
              {/* Tree Search and Filter */}
              <UserStoryTreeControls
                stage={stage}
                hasUserStories={hasUserStories}
                viewMode={effectiveTreeViewMode}
                storyFilter={storyFilter}
                onStoryFilterChange={setStoryFilter}
                updateFilter={updateFilter}
                onUpdateFilterChange={setUpdateFilter}
                onTreeQueryChange={setTreeQuery}
                counts={countCat}
                updateCounts={updateCounts}
                enableFilters={enableFilters}
              />

              <div className="flex-1 overflow-auto p-2" id="tree">
                <TreePanel
                  data={visibleTreeData}
                  query={treeQuery}
                  selectedId={activeSelectedId}
                  onSelect={setSelectedId}
                />
              </div>
            </div>

            {/* Middle Panel */}
            <MiddlePanelLayout
              selectedModule={selectedModule}
              selectedFeature={selectedFeature}
              selectedStory={selectedStory}
              enableFeedback={enableFeedback}
              activeChangeEntity={activeChangeEntity}
              rejectChange={handleRequestRejectChange}
              acceptChange={handleRequestAcceptChange}
              projectType={projectType}
              featureDetail={featureDetail}
              featureModCode={featureModCode}
              canApproveFeature={canApproveFeature}
              handleApproveFeature={handleApproveFeature}
              requirementDetail={requirementDetail}
              canApproveStory={canApproveStory}
              handleApproveStory={handleApproveStory}
              canApproveModule={canApproveModule}
              handleApproveModule={handleApproveModule}
              isApproving={isApproving}
              approvingTargetId={approvingTargetId}
              isRegenerating={isSelectionFeedbackRegenerating}
              isPipelineRunning={isPipelineRunning}
            >
              <div
                className="flex-1 overflow-auto p-5"
                onMouseUp={handleSelectionCheck}
              >
                {selectedModule ? (
                  <ModuleDetails
                    selectedModule={selectedModule}
                    moduleDetail={moduleDetail}
                    isLoadingDetail={isLoadingModuleDetail}
                    isDetailError={isModuleDetailError}
                    stage={stage}
                    setSelectedId={setSelectedId}
                    handleApproveModule={handleApproveModule}
                    isApproving={
                      isApproving && approvingTargetId === selectedModule.id
                    }
                    canApprove={canApproveModule}
                    isModuleRegenerating={isModulesRegenerating}
                  />
                ) : selectedFeature ? (
                  <FeatureDetails
                    selectedFeature={selectedFeature}
                    featureDetail={featureDetail}
                    modCode={featureModCode}
                    isLoadingDetail={isLoadingFeatureDetail}
                    isDetailError={isFeatureDetailError}
                    setSelectedId={setSelectedId}
                    handleApproveFeature={handleApproveFeature}
                    isApproving={
                      isApproving && approvingTargetId === selectedFeature.id
                    }
                    canApprove={canApproveFeature}
                    projectType={projectType}
                    stage={stage}
                    enableFeedback={enableFeedback}
                    isFeatureRegenerating={
                      busyFeatureIds.has(selectedFeature.id) ||
                      isModulesRegenerating
                    }
                    isPipelineRunning={isPipelineRunning}
                  />
                ) : selectedStory ? (
                  <UserStoryDetails
                    selectedStory={selectedStory}
                    requirementDetail={requirementDetail}
                    isLoadingDetail={isLoadingDetail}
                    isDetailError={isDetailError}
                    selectedAcceptanceCriterion={activeSelectedAc}
                    onSelectAcceptanceCriterion={
                      handleSelectAcceptanceCriterion
                    }
                    handleApproveStory={handleApproveStory}
                    handleDeleteStory={handleRequestDeleteStory}
                    isApproving={
                      isApproving && approvingTargetId === selectedStory.id
                    }
                    isDeleting={isDeleting}
                    canApprove={canApproveStory}
                    projectType={projectType}
                    featureGenerationMetadata={
                      featureDetail?.generation_metadata ?? null
                    }
                    enableFeedback={enableFeedback}
                    isFeedbackRegenerating={effectiveBusyStoryIds.has(
                      selectedStory.id,
                    )}
                    isPipelineRunning={isPipelineRunning}
                  />
                ) : (
                  <div className="flex h-full w-full items-center justify-center bg-white rounded-[12px] border border-[var(--border-primary)]">
                    <EmptyState
                      title="No item selected"
                      description="Please select an item to view its details."
                    />
                  </div>
                )}
              </div>
            </MiddlePanelLayout>

            {/* Right Panel */}
            <SrcEvidence
              stage={stage}
              selectedStory={requirementDetail}
              selectedFeature={featureDetail}
              selectedAc={activeSelectedAc}
              isLoading={isTreeLoading || isLoadingDetail}
              isError={isDetailError}
              enableGroundingActions={isReviewPage}
              showModulesFeedbackBtn={showModulesFeedbackBtn && enableFeedback}
              isProjectBusy={isProjectBusy}
              onAddModulesFeedback={() =>
                openComposeFeedback("module", {
                  id: "general",
                  label: "All modules",
                })
              }
              isFeedbackRegenerating={isSelectionFeedbackRegenerating}
            />
          </div>
        </>
      )}

      {/* User Story Approval Confirmation Modal  */}
      {pendingApproval && (
        <Modal
          isOpen={!!pendingApproval}
          onClose={closePendingApproval}
          title={pendingApproval.title}
          disableBackdropClose={isApproving}
          disableEscClose={isApproving}
          width={450}
          footer={
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={closePendingApproval}
                disabled={isApproving}
              >
                Cancel
              </Button>
              <Button
                variant="success"
                size="sm"
                onClick={handleConfirmApprove}
                loading={isApproving}
              >
                Approve
              </Button>
            </>
          }
        >
          <p className="m-0 text-[length:var(--font-size-sm)] leading-6 text-[var(--color-neutral-400)]">
            {pendingApproval.description}
          </p>
        </Modal>
      )}

      {/* User Story Delete Confirmation Modal */}
      {pendingDelete && (
        <Modal
          isOpen={!!pendingDelete}
          onClose={closePendingDelete}
          title={pendingDelete.title}
          disableBackdropClose={isDeleting}
          disableEscClose={isDeleting}
          width={450}
          footer={
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={closePendingDelete}
                disabled={isDeleting}
              >
                Cancel
              </Button>
              <Button
                variant="danger"
                size="sm"
                onClick={handleConfirmDelete}
                loading={isDeleting}
                disabled={pendingDelete.requiresReason && !deleteReason.trim()}
              >
                Delete
              </Button>
            </>
          }
        >
          <p className="m-0 text-[length:var(--font-size-sm)] leading-6 text-[var(--color-neutral-400)]">
            {pendingDelete.description}
          </p>
          {pendingDelete.requiresReason && (
            <textarea
              value={deleteReason}
              onChange={(e) => setDeleteReason(e.target.value)}
              placeholder="Reason for deleting this approved user story…"
              disabled={isDeleting}
              className="mt-3 min-h-[100px] w-full resize-y rounded-md border border-[var(--border-strong)] p-2.5 text-[13px] outline-none focus:border-[var(--accent)]"
            />
          )}
        </Modal>
      )}

      {/* Accept/Reject Change Confirmation Modal */}
      {pendingChangeAction && (
        <Modal
          isOpen={!!pendingChangeAction}
          onClose={closePendingChangeAction}
          title={pendingChangeAction.title}
          disableBackdropClose={isAcceptingChange || isRejectingChange}
          disableEscClose={isAcceptingChange || isRejectingChange}
          width={450}
          footer={
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={closePendingChangeAction}
                disabled={isAcceptingChange || isRejectingChange}
              >
                Cancel
              </Button>
              <Button
                variant={
                  pendingChangeAction.action === "accept" ? "success" : "danger"
                }
                size="sm"
                onClick={handleConfirmChangeAction}
                loading={
                  pendingChangeAction.action === "accept"
                    ? isAcceptingChange
                    : isRejectingChange
                }
              >
                {pendingChangeAction.action === "accept" ? "Accept" : "Reject"}
              </Button>
            </>
          }
        >
          <p className="m-0 text-[length:var(--font-size-sm)] leading-6 text-[var(--color-neutral-400)]">
            {pendingChangeAction.description}
          </p>
        </Modal>
      )}
    </>
  );
}
