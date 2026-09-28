import { useState } from "react";
import { toast } from "@/lib/toast";
import { getErrorMessage } from "@/utils/getErrorMessage";
import {
  useAcceptUpdateMutation,
  useRejectUpdateMutation,
} from "@/services/api/modules/updates";
import type {
  FeatureNode,
  ModuleNode,
  StoryNode,
} from "@/components/common/TreePanel";
import type { ChangeType, UpdateEntityType } from "@/types/user-story";
import { flattenAllNodeIds } from "@/features/ProjectWorkspace/Review/utils/userStoryTree";
import { getEffectiveChangeType } from "@/utils/changeType";

export interface ActiveChangeEntity {
  entityType: UpdateEntityType;
  entityId: string;
  changeType: Exclude<ChangeType, null>;
}

interface PendingChangeAction extends ActiveChangeEntity {
  action: "accept" | "reject";
  title: string;
  description: string;
}

interface UseIncrementalChangeActionParams {
  projectId?: string;
  selectedModule: ModuleNode | null;
  selectedFeature: FeatureNode | null;
  selectedStory: StoryNode | null;
  visibleTreeData: ModuleNode[];
  /** Called after a change resolution that deletes the entity outright, with the id to move tree selection to. */
  onEntityRemoved: (nextSelectedId: string) => void;
}

/** Owns the Accept/Reject flow for a pending incremental change on whichever module/feature/story is selected. */
export function useIncrementalChangeAction({
  projectId,
  selectedModule,
  selectedFeature,
  selectedStory,
  visibleTreeData,
  onEntityRemoved,
}: UseIncrementalChangeActionParams) {
  const [acceptUpdate, { isLoading: isAcceptingChange }] =
    useAcceptUpdateMutation();
  const [rejectUpdate, { isLoading: isRejectingChange }] =
    useRejectUpdateMutation();

  const [pendingChangeAction, setPendingChangeAction] =
    useState<PendingChangeAction | null>(null);

  // Whichever of module/feature/story is currently selected, at most one can
  // carry a pending change — surfaces the Accept/Reject actions. The change
  // can arrive via incremental_change_type or feedback_change_type.
  const moduleChangeType = getEffectiveChangeType(selectedModule);
  const featureChangeType = getEffectiveChangeType(selectedFeature);
  const storyChangeType = getEffectiveChangeType(selectedStory);

  const activeChangeEntity: ActiveChangeEntity | null =
    selectedModule && moduleChangeType
      ? {
          entityType: "module",
          entityId: selectedModule.id,
          changeType: moduleChangeType,
        }
      : selectedFeature && featureChangeType
        ? {
            entityType: "feature",
            entityId: selectedFeature.id,
            changeType: featureChangeType,
          }
        : selectedStory && storyChangeType
          ? {
              entityType: "user_story",
              entityId: selectedStory.id,
              changeType: storyChangeType,
            }
          : null;

  const handleRequestAcceptChange = () => {
    if (!activeChangeEntity) return;

    setPendingChangeAction({
      action: "accept",
      ...activeChangeEntity,
      title: "Accept change",
      description:
        "Are you sure you want to accept this change? The proposed content will replace the current version.",
    });
  };

  const handleRequestRejectChange = () => {
    if (!activeChangeEntity) return;

    setPendingChangeAction({
      action: "reject",
      ...activeChangeEntity,
      title: "Reject change",
      description:
        "Are you sure you want to reject this change? The proposed update will be discarded.",
    });
  };

  const closePendingChangeAction = () => {
    if (!isAcceptingChange && !isRejectingChange) {
      setPendingChangeAction(null);
    }
  };

  const handleConfirmChangeAction = async () => {
    if (!projectId || !pendingChangeAction) return;

    const mutate =
      pendingChangeAction.action === "accept" ? acceptUpdate : rejectUpdate;

    try {
      await mutate({
        projectId,
        entity_type: pendingChangeAction.entityType,
        entity_id: pendingChangeAction.entityId,
        change_type: pendingChangeAction.changeType,
      }).unwrap();

      toast.success(
        pendingChangeAction.action === "accept"
          ? "Change accepted."
          : "Change rejected.",
      );
      setPendingChangeAction(null);

      // Rejecting an "ADDED" change discards the newly proposed entity, and
      // accepting a "DELETE_SUGGESTED" one finalizes its removal — either way
      // the currently selected entity no longer exists. Move selection away
      // now, before the tree refetches, so the detail panel doesn't try to
      // re-fetch the now-gone id and flash a "not found" state.
      const entityWasRemoved =
        (pendingChangeAction.action === "reject" &&
          pendingChangeAction.changeType === "ADDED") ||
        (pendingChangeAction.action === "accept" &&
          pendingChangeAction.changeType === "DELETE_SUGGESTED");

      if (entityWasRemoved) {
        const allIds = flattenAllNodeIds(visibleTreeData);
        const removedIndex = allIds.indexOf(pendingChangeAction.entityId);
        const nextId =
          allIds[removedIndex + 1] ?? allIds[removedIndex - 1] ?? "";
        onEntityRemoved(nextId);
      }
    } catch (error) {
      toast.error(
        getErrorMessage(
          error,
          pendingChangeAction.action === "accept"
            ? "Failed to accept change."
            : "Failed to reject change.",
        ),
      );
    }
  };

  return {
    activeChangeEntity,
    pendingChangeAction,
    handleRequestAcceptChange,
    handleRequestRejectChange,
    handleConfirmChangeAction,
    closePendingChangeAction,
    isAcceptingChange,
    isRejectingChange,
  };
}
