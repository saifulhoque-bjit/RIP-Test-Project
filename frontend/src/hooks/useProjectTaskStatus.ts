/**
 * useProjectTaskStatus
 *
 * Thin selector wrappers so Overview/Sources/Pipelines/Requirements/Review
 * can each read live task status without importing Redux selectors
 * directly. useProjectTasksSocket (mounted once in ProjectPage) is what
 * keeps this data current — these hooks just read the resulting state.
 */

import { useAppSelector } from "@/store/hooks";
import {
  selectIsProjectBusy,
  selectLatestTaskOfType,
  selectProjectTasks,
} from "@/store/slices/projectTasksSlice";
import type { ProjectTask, TaskType } from "@/types/projectTask";

/** All live tasks currently tracked for a project. */
export function useProjectTasks(projectId: string | undefined): ProjectTask[] {
  return useAppSelector(selectProjectTasks(projectId));
}

/** The most recently updated task of one type, e.g. the active source_process run. */
export function useLatestProjectTask(
  projectId: string | undefined,
  taskType: TaskType,
): ProjectTask | undefined {
  return useAppSelector(selectLatestTaskOfType(projectId, taskType));
}

/**
 * True while any task (optionally scoped to one task_type) is still running.
 * Not enforced anywhere yet — feedback pages will use this to disable
 * actions while the relevant pipeline stage is in progress.
 */
export function useIsProjectBusy(projectId: string | undefined, taskType?: TaskType): boolean {
  return useAppSelector(selectIsProjectBusy(projectId, taskType));
}
