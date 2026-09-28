import { createSlice, type PayloadAction } from "@reduxjs/toolkit";
import type { RootState } from "@/store";
import type {
  SuggestedFeedbackAction,
  UserStoryFeedback,
} from "@/types/user-story";

interface UserStoryFeedbackState {
  byProjectId: Record<string, UserStoryFeedback>;
}

interface UpsertOverallFeedbackPayload {
  projectId: string;
  userStoryId: string;
  overallFeedback: string;
  suggestedAction?: SuggestedFeedbackAction;
  /** source_code drafts only — parent feature's module code + mfu id, and this story's own code. */
  modCode?: string;
  mfuId?: string | null;
  userStoryCode?: string;
}

interface UpsertSpecificFeedbackPayload {
  projectId: string;
  userStoryId: string;
  selectedText: string;
  selectedFeedback: string;
  suggestedAction?: SuggestedFeedbackAction;
  /** source_code drafts only — parent feature's module code + mfu id, and this story's own code. */
  modCode?: string;
  mfuId?: string | null;
  userStoryCode?: string;
}

interface RemoveSpecificFeedbackPayload {
  projectId: string;
  userStoryId: string;
  selectedText: string;
}

interface RemoveOverallFeedbackPayload {
  projectId: string;
  userStoryId: string;
}

const initialState: UserStoryFeedbackState = {
  byProjectId: {},
};

function ensureProjectFeedback(
  state: UserStoryFeedbackState,
  projectId: string,
): UserStoryFeedback {
  if (!state.byProjectId[projectId]) {
    state.byProjectId[projectId] = {
      project_id: projectId,
      user_stories: [],
    };
  }

  return state.byProjectId[projectId];
}

const userStoryFeedbackSlice = createSlice({
  name: "userStoryFeedback",
  initialState,
  reducers: {
    replaceProjectFeedback(state, action: PayloadAction<UserStoryFeedback>) {
      const projectId = action.payload.project_id.trim();
      if (!projectId) return;

      state.byProjectId[projectId] = {
        ...action.payload,
        project_id: projectId,
      };
    },

    clearProjectFeedback(state, action: PayloadAction<string>) {
      delete state.byProjectId[action.payload];
    },

    upsertOverallFeedback(
      state,
      action: PayloadAction<UpsertOverallFeedbackPayload>,
    ) {
      const projectId = action.payload.projectId.trim();
      const userStoryId = action.payload.userStoryId.trim();
      if (!projectId || !userStoryId) return;

      const projectFeedback = ensureProjectFeedback(state, projectId);
      const nextOverallFeedback = action.payload.overallFeedback.trim();
      const nextSuggestedAction = action.payload.suggestedAction || undefined;
      const { modCode, mfuId, userStoryCode } = action.payload;

      const existingStory = projectFeedback.user_stories.find(
        (story) => story.user_story_id === userStoryId,
      );

      if (existingStory) {
        existingStory.overall_feedback = nextOverallFeedback || undefined;
        existingStory.suggested_action = nextSuggestedAction;
        existingStory.mod_code = modCode;
        existingStory.mfu_id = mfuId;
        existingStory.user_story_code = userStoryCode;
        return;
      }

      projectFeedback.user_stories.push({
        user_story_id: userStoryId,
        overall_feedback: nextOverallFeedback || undefined,
        suggested_action: nextSuggestedAction,
        mod_code: modCode,
        mfu_id: mfuId,
        user_story_code: userStoryCode,
      });
    },

    upsertSpecificFeedback(
      state,
      action: PayloadAction<UpsertSpecificFeedbackPayload>,
    ) {
      const projectId = action.payload.projectId.trim();
      const userStoryId = action.payload.userStoryId.trim();
      const selectedText = action.payload.selectedText.trim();
      if (!projectId || !userStoryId || !selectedText) return;

      const nextSelectedFeedback = action.payload.selectedFeedback.trim();
      const nextSuggestedAction = action.payload.suggestedAction || undefined;
      const { modCode, mfuId, userStoryCode } = action.payload;
      const projectFeedback = ensureProjectFeedback(state, projectId);

      let story = projectFeedback.user_stories.find(
        (item) => item.user_story_id === userStoryId,
      );

      if (!story) {
        story = {
          user_story_id: userStoryId,
          specific_feedback: [],
        };
        projectFeedback.user_stories.push(story);
      }

      story.mod_code = modCode;
      story.mfu_id = mfuId;
      story.user_story_code = userStoryCode;

      if (!story.specific_feedback) {
        story.specific_feedback = [];
      }

      const existingTextFeedback = story.specific_feedback.find(
        (item) => item.selected_text === selectedText,
      );

      if (existingTextFeedback) {
        existingTextFeedback.selected_feedback = nextSelectedFeedback;
        existingTextFeedback.suggested_action = nextSuggestedAction;
        return;
      }

      story.specific_feedback.push({
        selected_text: selectedText,
        selected_feedback: nextSelectedFeedback,
        suggested_action: nextSuggestedAction,
      });
    },

    removeSpecificFeedback(
      state,
      action: PayloadAction<RemoveSpecificFeedbackPayload>,
    ) {
      const projectId = action.payload.projectId.trim();
      const userStoryId = action.payload.userStoryId.trim();
      const selectedText = action.payload.selectedText.trim();
      if (!projectId || !userStoryId || !selectedText) return;

      const projectFeedback = state.byProjectId[projectId];
      if (!projectFeedback) return;

      const storyIndex = projectFeedback.user_stories.findIndex(
        (item) => item.user_story_id === userStoryId,
      );
      if (storyIndex === -1) return;

      const story = projectFeedback.user_stories[storyIndex];
      const specificFeedback = story.specific_feedback ?? [];
      if (!specificFeedback.length) return;

      story.specific_feedback = specificFeedback.filter(
        (item) => item.selected_text !== selectedText,
      );

      if (!story.specific_feedback.length) {
        delete story.specific_feedback;
      }

      const hasOverallFeedback = !!story.overall_feedback?.trim();
      const hasSpecificFeedback = !!story.specific_feedback?.length;

      if (!hasOverallFeedback && !hasSpecificFeedback) {
        projectFeedback.user_stories.splice(storyIndex, 1);
      }

      if (!projectFeedback.user_stories.length) {
        delete state.byProjectId[projectId];
      }
    },

    removeOverallFeedback(
      state,
      action: PayloadAction<RemoveOverallFeedbackPayload>,
    ) {
      const projectId = action.payload.projectId.trim();
      const userStoryId = action.payload.userStoryId.trim();
      if (!projectId || !userStoryId) return;

      const projectFeedback = state.byProjectId[projectId];
      if (!projectFeedback) return;

      const storyIndex = projectFeedback.user_stories.findIndex(
        (item) => item.user_story_id === userStoryId,
      );
      if (storyIndex === -1) return;

      const story = projectFeedback.user_stories[storyIndex];
      delete story.overall_feedback;
      delete story.suggested_action;

      const hasSpecificFeedback = !!story.specific_feedback?.length;
      if (!hasSpecificFeedback) {
        projectFeedback.user_stories.splice(storyIndex, 1);
      }

      if (!projectFeedback.user_stories.length) {
        delete state.byProjectId[projectId];
      }
    },
  },
});

export const {
  replaceProjectFeedback,
  clearProjectFeedback,
  removeOverallFeedback,
  upsertOverallFeedback,
  upsertSpecificFeedback,
  removeSpecificFeedback,
} = userStoryFeedbackSlice.actions;

export const selectProjectFeedback =
  (projectId: string) =>
  (state: RootState): UserStoryFeedback | undefined =>
    state.userStoryFeedback.byProjectId[projectId];

export default userStoryFeedbackSlice.reducer;
