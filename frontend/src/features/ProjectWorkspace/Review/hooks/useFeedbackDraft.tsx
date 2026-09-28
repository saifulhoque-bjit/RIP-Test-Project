import { useState, type ReactNode } from "react";
import { toast } from "@/lib/toast";
import { useAppDispatch } from "@/store/hooks";
import {
  upsertOverallFeedback,
  upsertSpecificFeedback,
  removeOverallFeedback,
  removeSpecificFeedback,
  clearProjectFeedback,
} from "@/store/slices/userStoryFeedbackSlice";
import {
  upsertModuleFeedback,
  removeModuleFeedback,
  clearProjectModuleFeedback,
} from "@/store/slices/moduleFeedbackSlice";
import {
  upsertFeatureFeedback,
  removeFeatureFeedback,
  clearProjectFeatureFeedback,
} from "@/store/slices/featureFeedbackSlice";
import {
  SCOPE_NOUN,
  type ActiveFeedbackTarget,
  type FeedbackScope,
} from "@/features/ProjectWorkspace/Review/components/ReviewContext";
import type { ModuleFeedbackNote } from "@/types/module";
import type { FeatureFeedbackItem } from "@/types/feature";
import type {
  ModuleTreeItem,
  SuggestedFeedbackAction,
  UserStoryFeedbackStoryItem,
} from "@/types/user-story";
import {
  findFeatureLabel,
  findStoryLabel,
} from "@/features/ProjectWorkspace/Review/utils/userStoryTree";

export interface StoryFeedbackEntry {
  key: string;
  text: ReactNode;
  suggestedAction?: string;
  onRemove: () => void;
}

export interface StoryFeedbackGroup {
  userStoryId: string;
  label: string;
  entries: StoryFeedbackEntry[];
}

interface UseFeedbackDraftParams {
  projectId?: string;
  feedbackScope: FeedbackScope | null;
  activeFeedbackTarget: ActiveFeedbackTarget | null;
  selectedStoryText: string;
  isCombinedFeedbackView: boolean;
  closeFeedbackDrawerFromContext: () => void;
  treeItems: ModuleTreeItem[];
  moduleNotes: ModuleFeedbackNote[];
  featureItems: FeatureFeedbackItem[];
  storyItems: UserStoryFeedbackStoryItem[];
}

/** Owns the draft-composition and pending-list state for the Review page's "Add feedback" flow. */
export function useFeedbackDraft({
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
}: UseFeedbackDraftParams) {
  const dispatch = useAppDispatch();

  const [note, setNote] = useState("");
  const [suggestedAction, setSuggestedAction] = useState<
    SuggestedFeedbackAction | ""
  >("");
  const [noteKey, setNoteKey] = useState<string | null>(null);

  // Wraps the context's close so a cancelled draft never survives into the next open.
  const closeFeedbackDrawer = () => {
    setNote("");
    setSuggestedAction("");
    setNoteKey(null);
    closeFeedbackDrawerFromContext();
  };

  const currentKey =
    feedbackScope && activeFeedbackTarget
      ? `${feedbackScope}:${activeFeedbackTarget.id}:${
          feedbackScope === "story" ? selectedStoryText : ""
        }`
      : null;

  if (currentKey && currentKey !== noteKey) {
    setNoteKey(currentKey);
    const targetId = activeFeedbackTarget!.id;
    const existingStory =
      feedbackScope === "story"
        ? storyItems.find((s) => s.user_story_id === targetId)
        : undefined;

    if (feedbackScope === "story" && selectedStoryText) {
      // Editing feedback for this exact highlighted quote, if it already exists.
      const existingSpecific = existingStory?.specific_feedback?.find(
        (item) => item.selected_text === selectedStoryText,
      );
      setNote(existingSpecific?.selected_feedback ?? "");
      setSuggestedAction(existingSpecific?.suggested_action ?? "");
    } else {
      // A story can only have one overall_feedback — reopening always edits it in place.
      const existing =
        feedbackScope === "module"
          ? moduleNotes.find((n) => n.module_id === targetId)?.note
          : feedbackScope === "feature"
            ? featureItems.find((f) => f.feature_id === targetId)
                ?.overall_feedback
            : existingStory?.overall_feedback;
      setNote(existing ?? "");
      setSuggestedAction(existingStory?.suggested_action ?? "");
    }
  }

  const handleAddFeedback = () => {
    if (!projectId || !feedbackScope || !activeFeedbackTarget) return;

    if (feedbackScope === "module") {
      dispatch(
        upsertModuleFeedback({
          projectId,
          moduleId: activeFeedbackTarget.id,
          moduleLabel: activeFeedbackTarget.label,
          note,
        }),
      );
    } else if (feedbackScope === "feature") {
      dispatch(
        upsertFeatureFeedback({
          projectId,
          featureId: activeFeedbackTarget.id,
          modCode: activeFeedbackTarget.modCode,
          mfuId: activeFeedbackTarget.mfuId,
          overallFeedback: note,
        }),
      );
    } else if (selectedStoryText) {
      dispatch(
        upsertSpecificFeedback({
          projectId,
          userStoryId: activeFeedbackTarget.id,
          selectedText: selectedStoryText,
          selectedFeedback: note,
          suggestedAction: suggestedAction || undefined,
          modCode: activeFeedbackTarget.modCode,
          mfuId: activeFeedbackTarget.mfuId,
          userStoryCode: activeFeedbackTarget.userStoryCode,
        }),
      );
    } else {
      dispatch(
        upsertOverallFeedback({
          projectId,
          userStoryId: activeFeedbackTarget.id,
          overallFeedback: note,
          suggestedAction: suggestedAction || undefined,
          modCode: activeFeedbackTarget.modCode,
          mfuId: activeFeedbackTarget.mfuId,
          userStoryCode: activeFeedbackTarget.userStoryCode,
        }),
      );
    }

    toast.info("Added to feedback. Keep adding, then submit together.");
    closeFeedbackDrawer();
  };

  const handleRemoveFeedback = (scope: FeedbackScope, id: string) => {
    if (!projectId) return;

    if (scope === "module") {
      dispatch(removeModuleFeedback({ projectId, moduleId: id }));
    } else if (scope === "feature") {
      dispatch(removeFeatureFeedback({ projectId, featureId: id }));
    } else {
      dispatch(removeOverallFeedback({ projectId, userStoryId: id }));
    }
  };

  const handleClearAllFeedback = () => {
    if (!projectId || !feedbackScope) return;

    if (isCombinedFeedbackView) {
      dispatch(clearProjectFeatureFeedback(projectId));
      dispatch(clearProjectFeedback(projectId));
      toast.info("Cleared all pending feedback.");
    } else {
      if (feedbackScope === "module") {
        dispatch(clearProjectModuleFeedback(projectId));
      } else if (feedbackScope === "feature") {
        dispatch(clearProjectFeatureFeedback(projectId));
      } else {
        dispatch(clearProjectFeedback(projectId));
      }

      toast.info(`Cleared all pending ${SCOPE_NOUN[feedbackScope]} feedback.`);
    }

    closeFeedbackDrawer();
  };

  const listRows: {
    id: string;
    label: string;
    text: ReactNode;
    suggestedAction?: string;
  }[] =
    feedbackScope === "module"
      ? moduleNotes.map((n) => ({
          id: n.module_id,
          label: n.module_label,
          text: n.note,
        }))
      : feedbackScope === "feature"
        ? featureItems.map((f) => ({
            id: f.feature_id,
            label: findFeatureLabel(treeItems, f.feature_id),
            text: f.overall_feedback,
          }))
        : [];

  // Each story can carry one overall_feedback plus many specific_feedback
  // (per highlighted quote) entries — grouped by story, each individually removable.
  const storyGroups: StoryFeedbackGroup[] = storyItems
    .map((s) => {
      const entries: StoryFeedbackEntry[] = [];

      if (s.overall_feedback) {
        entries.push({
          key: `${s.user_story_id}-overall`,
          text: s.overall_feedback,
          suggestedAction: s.suggested_action,
          onRemove: () => {
            if (!projectId) return;
            dispatch(
              removeOverallFeedback({
                projectId,
                userStoryId: s.user_story_id,
              }),
            );
          },
        });
      }

      (s.specific_feedback ?? []).forEach((item) => {
        entries.push({
          key: `${s.user_story_id}-${item.selected_text}`,
          text: (
            <>
              <p className="m-0 border-l-2 border-[var(--accent)] pl-2 italic text-[var(--text-tertiary)]">
                “{item.selected_text}”
              </p>
              <p className="m-0 mt-1.5">{item.selected_feedback}</p>
            </>
          ),
          suggestedAction: item.suggested_action,
          onRemove: () => {
            if (!projectId) return;
            dispatch(
              removeSpecificFeedback({
                projectId,
                userStoryId: s.user_story_id,
                selectedText: item.selected_text,
              }),
            );
          },
        });
      });

      return {
        userStoryId: s.user_story_id,
        label: findStoryLabel(treeItems, s.user_story_id),
        entries,
      };
    })
    .filter((group) => group.entries.length > 0);

  const storyEntryCount = storyGroups.reduce(
    (acc, group) => acc + group.entries.length,
    0,
  );

  return {
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
  };
}
