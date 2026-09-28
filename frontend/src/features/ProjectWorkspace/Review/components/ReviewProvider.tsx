import { useCallback, useMemo, useState, type ReactNode } from "react";
import { useGetProjectRequirementsTreeQuery } from "@/services/api/modules/user-stories";
import { useGetProjectQuery } from "@/services/api/modules/projects";
import {
  deriveReviewStage,
  type ReviewStage,
} from "@/features/ProjectWorkspace/Review/stage";
import {
  ReviewStateContext,
  type ActiveFeedbackTarget,
  type FeedbackDrawerMode,
  type FeedbackScope,
} from "@/features/ProjectWorkspace/Review/components/ReviewContext";

interface ReviewProviderProps {
  projectId?: string;
  children: ReactNode;
}

export default function ReviewProvider({
  projectId,
  children,
}: ReviewProviderProps) {
  const {
    // `currentData` (not `data`) — RTK Query's `data` deliberately lags
    // behind arg changes and keeps returning the PREVIOUS project's tree
    // while the new one is in flight. Consumers below select/fetch
    // module/feature/story ids out of this tree, so serving stale data
    // here made them briefly resolve ids that belong to the old project,
    // which then 404'd against the new project id ("X not found" toast).
    currentData: treeResponse,
    isLoading: isTreeLoading,
    isFetching: isTreeFetching,
  } = useGetProjectRequirementsTreeQuery(
    { project_id: projectId! },
    { skip: !projectId },
  );

  const { currentData: projectResponse, isLoading: isProjectLoading } =
    useGetProjectQuery(projectId!, {
      skip: !projectId,
    });

  const treeItems = useMemo(
    () => treeResponse?.data?.items ?? [],
    [treeResponse],
  );

  const stage = useMemo<ReviewStage>(
    () => deriveReviewStage(treeItems),
    [treeItems],
  );

  const [feedbackDrawerMode, setFeedbackDrawerMode] =
    useState<FeedbackDrawerMode>(null);
  const [feedbackScope, setFeedbackScope] = useState<FeedbackScope | null>(
    null,
  );
  const [activeFeedbackTarget, setActiveFeedbackTarget] =
    useState<ActiveFeedbackTarget | null>(null);
  const [selectedStoryText, setSelectedStoryText] = useState("");

  const openComposeFeedback = useCallback(
    (scope: FeedbackScope, target: ActiveFeedbackTarget) => {
      setFeedbackScope(scope);
      setActiveFeedbackTarget(target);
      setFeedbackDrawerMode("compose");
    },
    [],
  );

  const openFeedbackList = useCallback((scope: FeedbackScope) => {
    setFeedbackScope(scope);
    setFeedbackDrawerMode("list");
  }, []);

  const closeFeedbackDrawer = useCallback(() => {
    setFeedbackDrawerMode(null);
    setFeedbackScope(null);
    setActiveFeedbackTarget(null);
    setSelectedStoryText("");
  }, []);

  const value = useMemo(
    () => ({
      stage,
      treeItems,
      areAllApprovedForMod:
        treeResponse?.data?.are_all_approved_for_mod ?? false,
      projectType: projectResponse?.data?.project_type,
      projectName: projectResponse?.data?.name,
      isTreeLoading,
      isTreeFetching,
      isProjectLoading,
      feedbackDrawerMode,
      feedbackScope,
      activeFeedbackTarget,
      openComposeFeedback,
      openFeedbackList,
      closeFeedbackDrawer,
      selectedStoryText,
      setSelectedStoryText,
    }),
    [
      stage,
      treeItems,
      treeResponse?.data?.are_all_approved_for_mod,
      projectResponse?.data?.project_type,
      projectResponse?.data?.name,
      isTreeLoading,
      isTreeFetching,
      isProjectLoading,
      feedbackDrawerMode,
      feedbackScope,
      activeFeedbackTarget,
      openComposeFeedback,
      openFeedbackList,
      closeFeedbackDrawer,
      selectedStoryText,
    ],
  );

  return (
    <ReviewStateContext.Provider value={value}>
      {children}
    </ReviewStateContext.Provider>
  );
}
