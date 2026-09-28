/**
 * featureFeedbackRegenerationSlice
 *
 * Tracks which feature ids have an in-flight feedback-regeneration task
 * (task_id returned by the source_code regenerate-for-source-code submit).
 * Mirrors storyFeedbackRegenerationSlice exactly — see that file for the
 * full rationale (frontend-only task_id <-> entity association, persisted
 * to localStorage, cross-referenced against the live WS task snapshot by
 * useFeatureFeedbackRegenerationStatus).
 */

import { createSlice, type PayloadAction } from "@reduxjs/toolkit";
import type { RootState } from "@/store";

export interface PendingFeatureRegeneration {
  taskId: string;
  /** ms since epoch when tracking started — drives the "not seen yet" grace window. */
  startedAt: number;
}

type PendingByFeatureId = Record<string, PendingFeatureRegeneration>;

interface FeatureFeedbackRegenerationState {
  byProjectId: Record<string, PendingByFeatureId>;
}

const STORAGE_KEY = "rip_feature_feedback_regeneration";

interface ProjectStorageItem {
  projectId: string;
  pending: PendingByFeatureId;
}

function loadFromStorage(): FeatureFeedbackRegenerationState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { byProjectId: {} };

    const parsed = JSON.parse(raw) as ProjectStorageItem[];
    if (!Array.isArray(parsed)) return { byProjectId: {} };

    const byProjectId: Record<string, PendingByFeatureId> = {};
    for (const item of parsed) {
      if (!item.projectId) continue;
      byProjectId[item.projectId] = item.pending ?? {};
    }
    return { byProjectId };
  } catch {
    return { byProjectId: {} };
  }
}

function persist(state: FeatureFeedbackRegenerationState): void {
  try {
    const data: ProjectStorageItem[] = Object.entries(state.byProjectId).map(
      ([projectId, pending]) => ({ projectId, pending }),
    );
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  } catch {
    // Ignore storage errors (quota exceeded, private mode, etc.)
  }
}

const initialState: FeatureFeedbackRegenerationState = loadFromStorage();

const featureFeedbackRegenerationSlice = createSlice({
  name: "featureFeedbackRegeneration",
  initialState,
  reducers: {
    /** Called right after a feedback submission returns a task_id — marks each affected feature busy. */
    trackFeatureFeedbackRegeneration(
      state,
      action: PayloadAction<{ projectId: string; taskId: string; featureIds: string[] }>,
    ) {
      const { projectId, taskId, featureIds } = action.payload;
      state.byProjectId[projectId] ??= {};
      const startedAt = Date.now();
      for (const featureId of featureIds) {
        state.byProjectId[projectId][featureId] = { taskId, startedAt };
      }
      persist(state);
    },

    /** Called once a tracked task resolves (or turns out to no longer exist). */
    clearFeatureFeedbackRegeneration(
      state,
      action: PayloadAction<{ projectId: string; featureIds: string[] }>,
    ) {
      const { projectId, featureIds } = action.payload;
      const pending = state.byProjectId[projectId];
      if (!pending) return;

      for (const featureId of featureIds) {
        delete pending[featureId];
      }
      if (Object.keys(pending).length === 0) {
        delete state.byProjectId[projectId];
      }
      persist(state);
    },
  },
});

export const {
  trackFeatureFeedbackRegeneration,
  clearFeatureFeedbackRegeneration,
} = featureFeedbackRegenerationSlice.actions;

export const selectPendingFeatureRegenerationMap =
  (projectId: string | undefined) =>
  (state: RootState): PendingByFeatureId =>
    projectId
      ? (state.featureFeedbackRegeneration.byProjectId[projectId] ?? {})
      : {};

export default featureFeedbackRegenerationSlice.reducer;
