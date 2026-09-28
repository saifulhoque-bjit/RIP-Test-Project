import { useEffect, useMemo, useState } from "react";
import { useAppDispatch, useAppSelector } from "@/store/hooks";
import { baseApi } from "@/services/api/baseApi";
import { useProjectTasks } from "@/hooks/useProjectTaskStatus";
import { isTerminalTaskStatus } from "@/types/projectTask";
import {
  clearFeatureFeedbackRegeneration,
  selectPendingFeatureRegenerationMap,
} from "@/store/slices/featureFeedbackRegenerationSlice";

// Mirrors useStoryFeedbackRegenerationStatus's constants/logic exactly — see
// that file for the full rationale on the grace window and recheck interval.
const RESOLUTION_GRACE_MS = 60_000;
const RECHECK_INTERVAL_MS = 5_000;

function usePendingEntries(projectId: string | undefined) {
  const pendingMap = useAppSelector(selectPendingFeatureRegenerationMap(projectId));
  const liveTasks = useProjectTasks(projectId);
  const pendingEntries = useMemo(() => Object.entries(pendingMap), [pendingMap]);
  return { pendingEntries, liveTasks };
}

/**
 * Owns resolving pending feature-feedback-regeneration entries: watches the
 * persisted "feature -> in-flight task" map against the live per-project task
 * snapshot and, once a tracked task resolves (or the grace window for an
 * unseen task_id elapses), clears the entry and invalidates that feature's
 * detail cache tag so it refetches.
 *
 * Mount this ONCE at the project layout level, alongside
 * useStoryFeedbackRegenerationResolver — see that hook's doc comment for why
 * (Review unmounts on tab switch, resolution must survive that).
 */
export function useFeatureFeedbackRegenerationResolver(
  projectId: string | undefined,
): void {
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
      .map(([featureId]) => featureId);

    if (resolvedIds.length === 0) return;

    dispatch(
      baseApi.util.invalidateTags(
        resolvedIds.map((id) => ({ type: "Module" as const, id: `FEATURE-${id}` })),
      ),
    );
    dispatch(clearFeatureFeedbackRegeneration({ projectId, featureIds: resolvedIds }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, pendingEntries, liveTasks, dispatch]);
}

/**
 * Pure reader — returns the set of feature ids currently busy with a
 * feedback-regeneration task (skeleton in FeatureDetails, all actions
 * blocked for that feature and its child stories). Purely derived from
 * redux state: relies on useFeatureFeedbackRegenerationResolver (mounted at
 * the project layout level) to actually clear entries once resolved.
 */
export function useFeatureFeedbackRegenerationStatus(
  projectId: string | undefined,
): Set<string> {
  const { pendingEntries, liveTasks } = usePendingEntries(projectId);

  return useMemo(() => {
    const ids = new Set<string>();
    const now = Date.now();
    for (const [featureId, { taskId, startedAt }] of pendingEntries) {
      const task = liveTasks.find((t) => t.task_id === taskId);
      if (task) {
        if (!isTerminalTaskStatus(task.status)) ids.add(featureId);
        continue;
      }
      if (now - startedAt < RESOLUTION_GRACE_MS) ids.add(featureId);
    }
    return ids;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingEntries, liveTasks]);
}
