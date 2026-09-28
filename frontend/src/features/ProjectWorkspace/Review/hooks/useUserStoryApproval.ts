import { useState } from "react";
import { toast } from "@/lib/toast";
import { getErrorMessage } from "@/utils/getErrorMessage";
import { useAppDispatch } from "@/store/hooks";
import { baseApi } from "@/services/api/baseApi";
import {
  useBulkStatusRequirementsMutation,
  useDeleteUserStoryByIdMutation,
} from "@/services/api/modules/user-stories";
import type {
  FeatureNode,
  ModuleNode,
  StoryNode,
} from "@/components/common/TreePanel";
import {
  flattenStoryIds,
  isApprovableStatus,
  type TreeNode,
} from "@/features/ProjectWorkspace/Review/utils/userStoryTree";
import { getEffectiveChangeType } from "@/utils/changeType";

interface PendingApproval {
  ids: string[];
  targetId: string;
  title: string;
  description: string;
}

interface PendingDelete {
  userStoryId: string;
  title: string;
  description: string;
  /** Deleting an already-approved story requires the user to state why. */
  requiresReason: boolean;
}

interface UseUserStoryApprovalParams {
  projectId?: string;
  selectedModule: ModuleNode | null;
  selectedFeature: FeatureNode | null;
  selectedStory: StoryNode | null;
  visibleTreeData: ModuleNode[];
  effectiveBusyStoryIds: Set<string>;
  busyFeatureIds: Set<string>;
  /** Called after a successful delete with the id to move tree selection to. */
  onStoryDeleted: (nextSelectedId: string) => void;
}

/** Owns module/feature/story approval (bulk status → "approved") and single-story deletion. */
export function useUserStoryApproval({
  projectId,
  selectedModule,
  selectedFeature,
  selectedStory,
  visibleTreeData,
  effectiveBusyStoryIds,
  busyFeatureIds,
  onStoryDeleted,
}: UseUserStoryApprovalParams) {
  const dispatch = useAppDispatch();
  const [approveAllRequirements, { isLoading: isApproving }] =
    useBulkStatusRequirementsMutation();
  const [deleteUserStoryById, { isLoading: isDeleting }] =
    useDeleteUserStoryByIdMutation();

  const [approvingTargetId, setApprovingTargetId] = useState<string | null>(
    null,
  );
  const [pendingApproval, setPendingApproval] =
    useState<PendingApproval | null>(null);
  const [pendingDelete, setPendingDelete] = useState<PendingDelete | null>(
    null,
  );
  const [deleteReason, setDeleteReason] = useState<string>("");

  // Excludes any story currently regenerating from feedback (directly, or as
  // a child of a feature that's regenerating) — approving mid-regeneration
  // would race the rewrite in flight.
  const getApprovableStoryIds = (node: TreeNode | null): string[] => {
    if (!node) {
      return [];
    }

    // User Story Level Node
    if (!("children" in node)) {
      const storyNode = node as StoryNode;
      return isApprovableStatus(storyNode.status) &&
        !getEffectiveChangeType(storyNode) &&
        !effectiveBusyStoryIds.has(storyNode.id)
        ? [storyNode.id]
        : [];
    }

    // Feature Level Node — a story with a pending incremental change needs
    // accept/reject first, so it's excluded from the feature's bulk-approve
    // count even when its status alone would look "ready".
    if ("fea_code" in node) {
      const featureNode = node as FeatureNode;
      if (busyFeatureIds.has(featureNode.id)) return [];
      return (featureNode.children ?? [])
        .filter(
          (story) =>
            isApprovableStatus(story.status) &&
            !getEffectiveChangeType(story) &&
            !effectiveBusyStoryIds.has(story.id),
        )
        .map((story) => story.id);
    }

    // Module Level Node
    const moduleNode = node as ModuleNode;
    return (moduleNode.children ?? []).flatMap((feature: FeatureNode) => {
      if (busyFeatureIds.has(feature.id)) return [];
      return (feature.children ?? [])
        .filter(
          (story: StoryNode) =>
            isApprovableStatus(story.status) &&
            !getEffectiveChangeType(story) &&
            !effectiveBusyStoryIds.has(story.id),
        )
        .map((story: StoryNode) => story.id);
    });
  };

  const handleApproveUserStories = async (
    userStoryIds: string[],
  ): Promise<boolean> => {
    if (!projectId || userStoryIds.length === 0) {
      toast.info("No ready user stories to approve.");
      return false;
    }

    try {
      const result = await approveAllRequirements({
        projectId,
        user_story_ids: userStoryIds,
        status: "approved",
      }).unwrap();

      toast.success(
        result.message ||
          `${userStoryIds.length} user ${userStoryIds.length === 1 ? "story" : "stories"} approved successfully.`,
      );

      // bulkStatusRequirements has no IngestionJob tag of its own, but an
      // approval can kick off a backend regeneration/sync run — force the
      // Pipelines table to refetch now rather than showing the pre-approval
      // snapshot until the next reload (mirrors useArchitectureApproval).
      dispatch(
        baseApi.util.invalidateTags([
          { type: "IngestionJob", id: `LIST-${projectId}` },
        ]),
      );

      return true;
    } catch (error) {
      toast.error(
        getErrorMessage(
          error,
          "Failed to approve user stories. Please try again.",
        ),
      );

      return false;
    }
  };

  const handleApproveModule = async () => {
    if (!selectedModule) return;

    const ids = getApprovableStoryIds(selectedModule);
    if (ids.length === 0) {
      toast.info("No ready user stories to approve.");
      return;
    }

    setPendingApproval({
      ids,
      targetId: selectedModule.id,
      title: "Approve Module Stories",
      description: `Are you sure you want to approve ${ids.length} ready user ${ids.length === 1 ? "story" : "stories"} under this module?`,
    });
  };

  const handleApproveFeature = async () => {
    if (!selectedFeature) return;

    const ids = getApprovableStoryIds(selectedFeature);
    if (ids.length === 0) {
      toast.info("No ready user stories to approve.");
      return;
    }

    setPendingApproval({
      ids,
      targetId: selectedFeature.id,
      title: "Approve Feature Stories",
      description: `Are you sure you want to approve ${ids.length} ready user ${ids.length === 1 ? "story" : "stories"} under this feature?`,
    });
  };

  const handleApproveStory = async () => {
    if (!selectedStory) return;

    const ids = getApprovableStoryIds(selectedStory);
    if (ids.length === 0) {
      toast.info("No ready user stories to approve.");
      return;
    }

    setPendingApproval({
      ids,
      targetId: selectedStory.id,
      title: "Approve User Story",
      description:
        "Are you sure you want to approve this user story? This will change status to approved.",
    });
  };

  const handleConfirmApprove = async () => {
    if (!pendingApproval) return;

    setApprovingTargetId(pendingApproval.targetId);
    const isSuccessful = await handleApproveUserStories(pendingApproval.ids);
    setApprovingTargetId(null);

    if (isSuccessful) {
      setPendingApproval(null);
    }
  };

  const closePendingApproval = () => {
    if (!isApproving) {
      setPendingApproval(null);
    }
  };

  const handleRequestDeleteStory = () => {
    if (!selectedStory) return;

    const requiresReason = selectedStory.status === "approved";
    setDeleteReason("");
    setPendingDelete({
      userStoryId: selectedStory.id,
      title: "Delete User Story",
      description: requiresReason
        ? "This user story is already approved. Please state why you're deleting it — this action cannot be undone."
        : "Are you sure you want to delete this user story? This action cannot be undone.",
      requiresReason,
    });
  };

  const handleConfirmDelete = async () => {
    if (!pendingDelete || !projectId) {
      return;
    }

    if (pendingDelete.requiresReason && !deleteReason.trim()) {
      toast.info("Please provide a reason for deleting this user story.");
      return;
    }

    try {
      const result = await deleteUserStoryById({
        projectId,
        userStoryId: pendingDelete.userStoryId,
        reason: pendingDelete.requiresReason
          ? deleteReason.trim()
          : undefined,
      }).unwrap();

      toast.success(result?.message || "User story deleted successfully.");
      setPendingDelete(null);
      setDeleteReason("");
      // Move selection to the next story in visual order (module → feature →
      // story), falling back to the previous one if the deleted story was
      // last overall. Naturally rolls into the next feature/module when the
      // deleted story was the last one in its own feature.
      const allStoryIds = flattenStoryIds(visibleTreeData);
      const deletedIndex = allStoryIds.indexOf(pendingDelete.userStoryId);
      const nextId =
        allStoryIds[deletedIndex + 1] ?? allStoryIds[deletedIndex - 1] ?? "";
      onStoryDeleted(nextId);
    } catch (error) {
      toast.error(
        getErrorMessage(
          error,
          "Failed to delete user story. Please try again.",
        ),
      );
    }
  };

  const closePendingDelete = () => {
    if (!isDeleting) {
      setPendingDelete(null);
      setDeleteReason("");
    }
  };

  const canApproveModule = getApprovableStoryIds(selectedModule).length > 0;
  const canApproveFeature = getApprovableStoryIds(selectedFeature).length > 0;
  const canApproveStory = getApprovableStoryIds(selectedStory).length > 0;

  return {
    isApproving,
    approvingTargetId,
    pendingApproval,
    handleApproveModule,
    handleApproveFeature,
    handleApproveStory,
    handleConfirmApprove,
    closePendingApproval,
    canApproveModule,
    canApproveFeature,
    canApproveStory,
    isDeleting,
    pendingDelete,
    deleteReason,
    setDeleteReason,
    handleRequestDeleteStory,
    handleConfirmDelete,
    closePendingDelete,
  };
}
