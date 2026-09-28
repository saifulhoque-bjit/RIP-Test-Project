/**
 * projectTasksSlice
 *
 * In-memory store for the live task snapshot pushed by GET /ws/projects/{id}.
 * Tasks are kept by project_id → task_id so every tab under a project
 * (Overview/Sources/Pipelines/Requirements/Review) reads the same state
 * instead of each tracking its own copy.
 *
 * Intentionally NOT persisted to localStorage: the server always replays a
 * full "tasks.current" snapshot right after connect (including after a page
 * reload or a reconnect), so this slice never needs to survive on its own —
 * it just mirrors whatever the server last said.
 */

import { createSlice, type PayloadAction } from "@reduxjs/toolkit";
import type { RootState } from "@/store";
import { isTerminalTaskStatus, type ProjectTask, type TaskType } from "@/types/projectTask";

interface ProjectTasksState {
  byProject: Record<string, Record<string, ProjectTask>>;
}

const initialState: ProjectTasksState = {
  byProject: {},
};

const projectTasksSlice = createSlice({
  name: "projectTasks",
  initialState,
  reducers: {
    /** Replaces the full task set for a project — used for the tasks.current frame. */
    setProjectTasks(
      state,
      action: PayloadAction<{ projectId: string; tasks: ProjectTask[] }>,
    ) {
      const { projectId, tasks } = action.payload;
      state.byProject[projectId] = Object.fromEntries(
        tasks.map((task) => [task.task_id, task]),
      );
    },

    /** Applies one task.update frame — inserts the task if it's new, else overwrites it. */
    upsertProjectTask(
      state,
      action: PayloadAction<{ projectId: string; task: ProjectTask }>,
    ) {
      const { projectId, task } = action.payload;
      state.byProject[projectId] ??= {};
      state.byProject[projectId][task.task_id] = task;
    },

    /** Drops all tracked tasks for a project (not currently called — available if ever needed). */
    clearProjectTasks(state, action: PayloadAction<string>) {
      delete state.byProject[action.payload];
    },
  },
});

export const { setProjectTasks, upsertProjectTask, clearProjectTasks } =
  projectTasksSlice.actions;

// ── Selectors ────────────────────────────────────────────────────────────────

/** Every live task tracked for a project, most recently updated first. */
export const selectProjectTasks =
  (projectId: string | undefined) =>
  (state: RootState): ProjectTask[] => {
    if (!projectId) return [];
    const tasks = state.projectTasks.byProject[projectId];
    if (!tasks) return [];
    return Object.values(tasks).sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  };

/** The most recently updated task of one type — e.g. the active source_process run. */
export const selectLatestTaskOfType =
  (projectId: string | undefined, taskType: TaskType) =>
  (state: RootState): ProjectTask | undefined =>
    selectProjectTasks(projectId)(state).find((task) => task.task_type === taskType);

/**
 * True while any task (optionally scoped to one task_type) is still running.
 * Exposed for later use — e.g. disabling feedback actions while the related
 * pipeline stage is in progress.
 */
export const selectIsProjectBusy =
  (projectId: string | undefined, taskType?: TaskType) =>
  (state: RootState): boolean =>
    selectProjectTasks(projectId)(state).some(
      (task) => (!taskType || task.task_type === taskType) && !isTerminalTaskStatus(task.status),
    );

export default projectTasksSlice.reducer;
