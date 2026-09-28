import { useEffect, useMemo, useState } from "react";
import { useAppDispatch, useAppSelector } from "@/store/hooks";
import { baseApi } from "@/services/api/baseApi";
import { useProjectTasks } from "@/hooks/useProjectTaskStatus";
import { isTerminalTaskStatus } from "@/types/projectTask";
import {
  clearStoryFeedbackRegeneration,
  selectPendingStoryRegenerationMap,
} from "@/store/slices/storyFeedbackRegenerationSlice";

// How long a pending entry is kept "busy" after being tracked before its
// task_id has shown up in the live task list at all. Covers the ordinary
// race between the mutation's HTTP response (which hands us the task_id)
// and the first WebSocket task.update frame for that task actually
// arriving — without this, "not found yet" would be indistinguishable from
// "task already finished and dropped off the snapshot".
const RESOLUTION_GRACE_MS = 60_000;
const RECHECK_INTERVAL_MS = 5_000;

function usePendingEntries(projectId: string | undefined) {
  const pendingMap = useAppSelector(selectPendingStoryRegenerationMap(projectId));
  const liveTasks = useProjectTasks(projectId);
  const pendingEntries = useMemo(() => Object.entries(pendingMap), [pendingMap]);
  return { pendingEntries, liveTasks };
}

/**
 * Owns resolving pending story-feedback-regeneration entries: watches the
 * persisted "story -> in-flight task" map against the live per-project task
 * snapshot and, once a tracked task resolves (or the grace window for an
 * unseen task_id elapses), clears the entry and invalidates that story's
 * Requirement cache tag so it refetches.
 *
 * Mount this ONCE at the project layout level (alongside
 * useProjectTasksSocket) rather than inside the Review tab — Review
 * unmounts whenever the user switches tabs, and resolution must keep
 * running regardless of which tab is active, otherwise a story can finish
 * regenerating while the user is elsewhere and stay stuck looking busy
 * until they happen to remount Review (or reload the page).
 */
export function useStoryFeedbackRegenerationResolver(projectId: string | undefined): void {
  const dispatch = useAppDispatch();
  const { pendingEntries, liveTasks } = usePendingEntries(projectId);

  // Forces a re-check every few seconds so a grace window can expire even
  // without a fresh WS frame arriving to trigger a re-render.
  const [, setTick] = useState(0);
  useEffect(() => {
    if (pendingEntries.length === 0) return;
    const id = setInterval(() => setTick((t) => t + 1), RECHECK_INTERVAL_MS);
    return () => clearInterval(id);
  }, [pendingEntries.length]);

  useEffect(() => {
    if (!projectId || pendingEntries.length === 0) return;

    const now = Date.now();
    const resolvedIds = pendingEntries
      .filter(([, { taskId, startedAt }]) => {
        const task = liveTasks.find((t) => t.task_id === taskId);
        if (task) return isTerminalTaskStatus(task.status);
        return now - startedAt >= RESOLUTION_GRACE_MS;
      })
      .map(([userStoryId]) => userStoryId);

    if (resolvedIds.length === 0) return;

    dispatch(
      baseApi.util.invalidateTags(
        resolvedIds.map((id) => ({ type: "Requirement" as const, id })),
      ),
    );
    dispatch(clearStoryFeedbackRegeneration({ projectId, userStoryIds: resolvedIds }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, pendingEntries, liveTasks, dispatch]);
}

/**
 * Pure reader — returns the set of user story ids currently busy with a
 * feedback-regeneration task (skeleton in UserStoryDetails, feedback
 * actions blocked for that story only). Purely derived from redux state:
 * relies on useStoryFeedbackRegenerationResolver (mounted at the project
 * layout level) to actually clear entries once resolved, so this hook
 * itself needs no timer — it just re-renders whenever that shared state
 * changes.
 */
export function useStoryFeedbackRegenerationStatus(
  projectId: string | undefined,
): Set<string> {
  const { pendingEntries, liveTasks } = usePendingEntries(projectId);

  return useMemo(() => {
    const ids = new Set<string>();
    const now = Date.now();
    for (const [userStoryId, { taskId, startedAt }] of pendingEntries) {
      const task = liveTasks.find((t) => t.task_id === taskId);
      if (task) {
        if (!isTerminalTaskStatus(task.status)) ids.add(userStoryId);
        continue;
      }
      if (now - startedAt < RESOLUTION_GRACE_MS) ids.add(userStoryId);
    }
    return ids;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingEntries, liveTasks]);
}
