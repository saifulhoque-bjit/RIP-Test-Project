import { toast } from "@/lib/toast";
import { getErrorMessage } from "@/utils/getErrorMessage";
import { useAppDispatch } from "@/store/hooks";
import { useRegenerateModulesMutation } from "@/services/api/modules/modules";
import {
  useRegenerateUserStoriesByFeedbackMutation,
  useRegenerateForSourceCodeFeedbackMutation,
} from "@/services/api/modules/user-stories";
import { clearProjectModuleFeedback } from "@/store/slices/moduleFeedbackSlice";
import { clearProjectFeatureFeedback } from "@/store/slices/featureFeedbackSlice";
import { clearProjectFeedback } from "@/store/slices/userStoryFeedbackSlice";
import { trackStoryFeedbackRegeneration } from "@/store/slices/storyFeedbackRegenerationSlice";
import { trackFeatureFeedbackRegeneration } from "@/store/slices/featureFeedbackRegenerationSlice";
import type { FeedbackScope } from "@/features/ProjectWorkspace/Review/components/ReviewContext";
import type { ModuleFeedbackNote } from "@/types/module";
import type {
  FeatureFeedbackItem,
  RegenerateForSourceCodeFeedbackItem,
} from "@/types/feature";
import type {
  RegenerateByFeedbackItem,
  UserStoryFeedbackStoryItem,
} from "@/types/user-story";

// Merges the suggested-action pick into the feedback text sent to the API —
// the backend takes one string per field, not a separate suggested_action field.
const mergeFeedbackWithAction = (
  feedbackText: string,
  suggestedAction: string | undefined,
): string => {
  if (!suggestedAction) return feedbackText;

  return `HUMAN SELECTED ACTION\n---------------------\n${suggestedAction}\n\nSYSTEM DIRECTIVE\n----------------\n${feedbackText}`;
};

interface UseFeedbackSubmitHandlersParams {
  projectId?: string;
  /** source_code combines feature + story drafts into one regenerate-for-source-code call instead of the rfp regenerate-by-feedback endpoint. */
  projectType?: string | null;
  moduleNotes: ModuleFeedbackNote[];
  featureItems: FeatureFeedbackItem[];
  storyItems: UserStoryFeedbackStoryItem[];
  closeFeedbackDrawer: () => void;
}

/** Owns the submit-all mutation calls for the three feedback scopes on the Review page. */
export function useFeedbackSubmitHandlers({
  projectId,
  projectType,
  moduleNotes,
  featureItems,
  storyItems,
  closeFeedbackDrawer,
}: UseFeedbackSubmitHandlersParams) {
  const dispatch = useAppDispatch();
  const [regenerateModules, { isLoading: isRegeneratingModules }] =
    useRegenerateModulesMutation();
  const [
    regenerateUserStoriesByFeedback,
    { isLoading: isSubmittingRfpStoryFeedback },
  ] = useRegenerateUserStoriesByFeedbackMutation();
  const [
    regenerateSourceCodeFeedback,
    { isLoading: isSubmittingSourceCodeFeedback },
  ] = useRegenerateForSourceCodeFeedbackMutation();

  const handleSubmitModuleFeedback = async () => {
    if (!projectId || !moduleNotes.length) return;

    const feedback = moduleNotes.map((n) => n.note).join("\n\n");

    try {
      const result = await regenerateModules({ projectId, feedback }).unwrap();

      toast.success(
        result.message || "Module feedback submitted successfully.",
      );
      dispatch(clearProjectModuleFeedback(projectId));
      closeFeedbackDrawer();
    } catch (error) {
      toast.error(
        getErrorMessage(
          error,
          "Failed to submit module feedback. Please try again.",
        ),
      );
    }
  };

  // source_code — feature-level and story-level (overall + specific) drafts
  // are collected into ONE feedback_items array and sent as a single call;
  // the backend distinguishes item kind by which fields are present
  // (mfu_id only vs. + user_story_code vs. + specific_feedback). Stories are
  // addressed by user_story_code on the wire, but busy-tracking (which gates
  // UI actions) keys off the story's actual id, so that's tracked alongside
  // each built wire item rather than read back off it.
  const handleSubmitSourceCodeFeedback = async (projectIdValue: string) => {
    const submittedFeatureItems = featureItems.filter(
      (item) => item.overall_feedback,
    );
    const featurePart: RegenerateForSourceCodeFeedbackItem[] =
      submittedFeatureItems.map((item) => ({
        mod_code: item.mod_code ?? "",
        mfu_id: item.mfu_id,
        overall_feedback: item.overall_feedback,
      }));

    const submittedStoryBuilds = storyItems
      .map((story) => {
        const overallFeedback = story.overall_feedback?.trim();
        const specificFeedback = story.specific_feedback?.map((item) => ({
          selected_text: item.selected_text,
          selected_feedback: mergeFeedbackWithAction(
            item.selected_feedback,
            item.suggested_action,
          ),
        }));

        const wireItem: RegenerateForSourceCodeFeedbackItem = {
          mod_code: story.mod_code ?? "",
          mfu_id: story.mfu_id,
          user_story_code: story.user_story_code ?? "",
          ...(overallFeedback
            ? {
                overall_feedback: mergeFeedbackWithAction(
                  overallFeedback,
                  story.suggested_action,
                ),
              }
            : {}),
          ...(specificFeedback?.length
            ? { specific_feedback: specificFeedback }
            : {}),
        };

        return { userStoryId: story.user_story_id, wireItem };
      })
      .filter(
        ({ wireItem }) =>
          wireItem.overall_feedback || wireItem.specific_feedback?.length,
      );

    const storyPart = submittedStoryBuilds.map((build) => build.wireItem);
    const feedbackItems = [...featurePart, ...storyPart];

    if (!feedbackItems.length) {
      toast.info("Please add feedback before submitting.");
      return;
    }

    try {
      const result = await regenerateSourceCodeFeedback({
        projectId: projectIdValue,
        feedbackItems,
        skipProcessing: false,
      }).unwrap();

      toast.success(result.message || "Feedback submitted successfully.");

      const taskId = result.data?.task_id;
      const userStoryIds = submittedStoryBuilds.map(
        (build) => build.userStoryId,
      );
      if (taskId && userStoryIds.length) {
        dispatch(
          trackStoryFeedbackRegeneration({
            projectId: projectIdValue,
            taskId,
            userStoryIds,
          }),
        );
      }

      const featureIds = submittedFeatureItems.map((item) => item.feature_id);
      if (taskId && featureIds.length) {
        dispatch(
          trackFeatureFeedbackRegeneration({
            projectId: projectIdValue,
            taskId,
            featureIds,
          }),
        );
      }

      dispatch(clearProjectFeatureFeedback(projectIdValue));
      dispatch(clearProjectFeedback(projectIdValue));
      closeFeedbackDrawer();
    } catch (error) {
      toast.error(
        getErrorMessage(error, "Failed to submit feedback. Please try again."),
      );
    }
  };

  const handleSubmitRfpStoryFeedback = async (projectIdValue: string) => {
    const feedbackItems: RegenerateByFeedbackItem[] = storyItems
      .map((story) => {
        const overallFeedback = story.overall_feedback?.trim();
        const specificFeedback = story.specific_feedback?.map((item) => ({
          selected_text: item.selected_text,
          selected_feedback: mergeFeedbackWithAction(
            item.selected_feedback,
            item.suggested_action,
          ),
        }));

        return {
          user_story_id: story.user_story_id,
          ...(overallFeedback
            ? {
                overall_feedback: mergeFeedbackWithAction(
                  overallFeedback,
                  story.suggested_action,
                ),
              }
            : {}),
          ...(specificFeedback?.length
            ? { specific_feedback: specificFeedback }
            : {}),
        };
      })
      .filter(
        (item) => item.overall_feedback || item.specific_feedback?.length,
      );

    if (!feedbackItems.length) {
      toast.info("Please add feedback before submitting.");
      return;
    }

    try {
      const result = await regenerateUserStoriesByFeedback({
        projectId: projectIdValue,
        feedbackItems,
      }).unwrap();

      toast.success(result.message || "Feedback submitted successfully.");

      const taskId = result.data?.task_id;
      if (taskId) {
        dispatch(
          trackStoryFeedbackRegeneration({
            projectId: projectIdValue,
            taskId,
            userStoryIds: feedbackItems.map((item) => item.user_story_id),
          }),
        );
      }

      dispatch(clearProjectFeedback(projectIdValue));
      closeFeedbackDrawer();
    } catch (error) {
      toast.error(
        getErrorMessage(error, "Failed to submit feedback. Please try again."),
      );
    }
  };

  // source_code: both the feature bar and the story bar submit the SAME
  // combined feedback_items array (per-scope splitting isn't supported by
  // the backend) — whichever button is clicked, everything pending (feature
  // AND story drafts) goes out together, so neither queue alone gates it.
  // rfp keeps feature/story fully separate (feature feedback isn't offered
  // for rfp at all, so this is a no-op there).
  const handleSubmitFeatureFeedback = async () => {
    if (!projectId) return;

    if (projectType === "source_code") {
      if (!featureItems.length && !storyItems.length) return;
      await handleSubmitSourceCodeFeedback(projectId);
    }
  };

  const handleSubmitStoryFeedback = async () => {
    if (!projectId) return;

    if (projectType === "source_code") {
      if (!featureItems.length && !storyItems.length) return;
      await handleSubmitSourceCodeFeedback(projectId);
    } else {
      if (!storyItems.length) return;
      await handleSubmitRfpStoryFeedback(projectId);
    }
  };

  const submitHandlers: Record<FeedbackScope, () => void> = {
    module: handleSubmitModuleFeedback,
    feature: handleSubmitFeatureFeedback,
    story: handleSubmitStoryFeedback,
  };

  const submitLoading: Record<FeedbackScope, boolean> = {
    module: isRegeneratingModules,
    feature:
      projectType === "source_code" ? isSubmittingSourceCodeFeedback : false,
    story:
      projectType === "source_code"
        ? isSubmittingSourceCodeFeedback
        : isSubmittingRfpStoryFeedback,
  };

  return {
    handleSubmitModuleFeedback,
    handleSubmitFeatureFeedback,
    handleSubmitStoryFeedback,
    submitHandlers,
    submitLoading,
  };
}
