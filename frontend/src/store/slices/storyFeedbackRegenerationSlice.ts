/**
 * storyFeedbackRegenerationSlice
 *
 * Tracks which user_story_id's have an in-flight feedback-regeneration task
 * (task_id returned by regenerateUserStoriesByFeedback). This association
 * only exists on the frontend — the WebSocket task frames carry a task_id
 * but not the story ids it covers — so it's persisted to localStorage to
 * survive a page reload; useStoryFeedbackRegenerationStatus cross-references
 * it against the live task snapshot (with a grace window for the moment
 * right after submit, before the first WS frame for the new task arrives)
 * to know when to clear an entry.
 */

import { createSlice, type PayloadAction } from "@reduxjs/toolkit";
import type { RootState } from "@/store";

export interface PendingStoryRegeneration {
  taskId: string;
  /** ms since epoch when tracking started — drives the "not seen yet" grace window. */
  startedAt: number;
}

type PendingByUserStoryId = Record<string, PendingStoryRegeneration>;

interface StoryFeedbackRegenerationState {
  byProjectId: Record<string, PendingByUserStoryId>;
}

const STORAGE_KEY = "rip_story_feedback_regeneration";

interface ProjectStorageItem {
  projectId: string;
  pending: PendingByUserStoryId;
}

function loadFromStorage(): StoryFeedbackRegenerationState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { byProjectId: {} };

    const parsed = JSON.parse(raw) as ProjectStorageItem[];
    if (!Array.isArray(parsed)) return { byProjectId: {} };

    const byProjectId: Record<string, PendingByUserStoryId> = {};
    for (const item of parsed) {
      if (!item.projectId) continue;
      byProjectId[item.projectId] = item.pending ?? {};
    }
    return { byProjectId };
  } catch {
    return { byProjectId: {} };
  }
}

function persist(state: StoryFeedbackRegenerationState): void {
  try {
    const data: ProjectStorageItem[] = Object.entries(state.byProjectId).map(
      ([projectId, pending]) => ({ projectId, pending }),
    );
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  } catch {
    // Ignore storage errors (quota exceeded, private mode, etc.)
  }
}

const initialState: StoryFeedbackRegenerationState = loadFromStorage();

const storyFeedbackRegenerationSlice = createSlice({
  name: "storyFeedbackRegeneration",
  initialState,
  reducers: {
    /** Called right after a feedback submission returns a task_id — marks each affected story busy. */
    trackStoryFeedbackRegeneration(
      state,
      action: PayloadAction<{ projectId: string; taskId: string; userStoryIds: string[] }>,
    ) {
      const { projectId, taskId, userStoryIds } = action.payload;
      state.byProjectId[projectId] ??= {};
      const startedAt = Date.now();
      for (const userStoryId of userStoryIds) {
        state.byProjectId[projectId][userStoryId] = { taskId, startedAt };
      }
      persist(state);
    },

    /** Called once a tracked task resolves (or turns out to no longer exist). */
    clearStoryFeedbackRegeneration(
      state,
      action: PayloadAction<{ projectId: string; userStoryIds: string[] }>,
    ) {
      const { projectId, userStoryIds } = action.payload;
      const pending = state.byProjectId[projectId];
      if (!pending) return;

      for (const userStoryId of userStoryIds) {
        delete pending[userStoryId];
      }
      if (Object.keys(pending).length === 0) {
        delete state.byProjectId[projectId];
      }
      persist(state);
    },
  },
});

export const { trackStoryFeedbackRegeneration, clearStoryFeedbackRegeneration } =
  storyFeedbackRegenerationSlice.actions;

export const selectPendingStoryRegenerationMap =
  (projectId: string | undefined) =>
  (state: RootState): PendingByUserStoryId =>
    projectId ? (state.storyFeedbackRegeneration.byProjectId[projectId] ?? {}) : {};

export default storyFeedbackRegenerationSlice.reducer;
