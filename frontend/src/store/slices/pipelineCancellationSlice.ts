/**
 * pipelineCancellationSlice
 *
 * Tracks which ingestion run ids have a cancel request in flight
 * (CancelPipelineButton's "Cancelling..." state). This has to live outside
 * the button component: the Pipelines tab unmounts on tab switch (and
 * everything resets on a full reload), but the backend can take a moment
 * after the confirm click to flip the run's row status off "running" — if
 * the pending flag lived in component state, returning to the tab (or
 * reloading) mid-window would silently drop the "Cancelling..." indicator
 * and the row would flash back to a plain "Cancel" button or "-" while the
 * run is, in reality, still shutting down. Persisted to localStorage so a
 * reload survives it too; CancelPipelineButton clears the entry itself once
 * the run's own row confirms it has stopped running.
 */

import { createSlice, type PayloadAction } from "@reduxjs/toolkit";
import type { RootState } from "@/store";

export interface PendingPipelineCancellation {
  taskId: string;
}

type PendingByRunId = Record<string, PendingPipelineCancellation>;

interface PipelineCancellationState {
  byProjectId: Record<string, PendingByRunId>;
}

const STORAGE_KEY = "rip_pipeline_cancellation";

interface ProjectStorageItem {
  projectId: string;
  pending: PendingByRunId;
}

function loadFromStorage(): PipelineCancellationState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { byProjectId: {} };

    const parsed = JSON.parse(raw) as ProjectStorageItem[];
    if (!Array.isArray(parsed)) return { byProjectId: {} };

    const byProjectId: Record<string, PendingByRunId> = {};
    for (const item of parsed) {
      if (!item.projectId) continue;
      byProjectId[item.projectId] = item.pending ?? {};
    }
    return { byProjectId };
  } catch {
    return { byProjectId: {} };
  }
}

function persist(state: PipelineCancellationState): void {
  try {
    const data: ProjectStorageItem[] = Object.entries(state.byProjectId).map(
      ([projectId, pending]) => ({ projectId, pending }),
    );
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  } catch {
    // Ignore storage errors (quota exceeded, private mode, etc.)
  }
}

const initialState: PipelineCancellationState = loadFromStorage();

const pipelineCancellationSlice = createSlice({
  name: "pipelineCancellation",
  initialState,
  reducers: {
    /** Called right after the cancel mutation is confirmed for a run's task. */
    trackPipelineCancellation(
      state,
      action: PayloadAction<{ projectId: string; runId: string; taskId: string }>,
    ) {
      const { projectId, runId, taskId } = action.payload;
      state.byProjectId[projectId] ??= {};
      state.byProjectId[projectId][runId] = { taskId };
      persist(state);
    },

    /** Called once the run's own row reports it's no longer running. */
    clearPipelineCancellation(
      state,
      action: PayloadAction<{ projectId: string; runId: string }>,
    ) {
      const { projectId, runId } = action.payload;
      const pending = state.byProjectId[projectId];
      if (!pending) return;

      delete pending[runId];
      if (Object.keys(pending).length === 0) {
        delete state.byProjectId[projectId];
      }
      persist(state);
    },
  },
});

export const { trackPipelineCancellation, clearPipelineCancellation } =
  pipelineCancellationSlice.actions;

export const selectPendingPipelineCancellation =
  (projectId: string | undefined, runId: string) =>
  (state: RootState): PendingPipelineCancellation | undefined =>
    projectId ? state.pipelineCancellation.byProjectId[projectId]?.[runId] : undefined;

export default pipelineCancellationSlice.reducer;
