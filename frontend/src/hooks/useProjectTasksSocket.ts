/**
 * useProjectTasksSocket
 *
 * Opens ONE WebSocket per project — GET /ws/projects/{project_id} — and
 * mirrors every task update into the projectTasks Redux slice.
 *
 * Mount this once per project, in ProjectPage (the layout shared by every
 * tab: Overview/Sources/Pipelines/Requirements/Review). All tabs then read
 * live status from the store via the selectors in projectTasksSlice, so
 * switching tabs never opens a second connection.
 *
 * Handles the three cases called out for this feature:
 *  - Page reload: Redux state lives only in memory, but the server always
 *    replays a full "tasks.current" snapshot immediately after connect, so
 *    a fresh mount is fully rehydrated with no extra work.
 *  - Dropped internet: reconnects with exponential back-off, and retries
 *    right away when the browser reports connectivity is back.
 *  - Cross-page reactivity: on a terminal status for a task, invalidates the
 *    RTK Query list tag that task type feeds, so the relevant table refetches.
 */

import { useCallback, useEffect, useRef } from "react";
import { useDispatch } from "react-redux";
import type { AppDispatch } from "@/store";
import { useAppSelector } from "@/store/hooks";
import { baseApi } from "@/services/api/baseApi";
import { WS_BASE_URL } from "@/lib/wsBaseUrl";
import {
  setProjectTasks,
  upsertProjectTask,
} from "@/store/slices/projectTasksSlice";
import {
  isTerminalTaskStatus,
  TASK_TYPE,
  type ProjectSocketFrame,
  type ProjectTask,
} from "@/types/projectTask";

const BASE_RECONNECT_DELAY_MS = 1_000;
const MAX_RECONNECT_DELAY_MS = 30_000;

// The server sends a "heartbeat" frame on a steady cadence. If nothing at all
// arrives for this long, the browser's readyState can no longer be trusted —
// e.g. after the device sleeps or switches networks, the socket often stays
// "OPEN" indefinitely without actually being connected to anything. Checked
// this often; timed out at roughly 3x a typical heartbeat cadence to leave
// headroom for a single delayed/dropped heartbeat.
const STALE_CHECK_INTERVAL_MS = 20_000;
const STALE_CONNECTION_TIMEOUT_MS = 90_000;

/** Maps a completed task_type to the RTK Query cache tag(s) its data feeds. */
function invalidateTagsForTaskType(
  dispatch: AppDispatch,
  projectId: string,
  taskType: string,
): void {
  switch (taskType) {
    case TASK_TYPE.SOURCE_PROCESS:
    case TASK_TYPE.INCREMENTAL_UPDATE:
      // Feeds the Sources and Pipelines tables (both read the ingestion
      // list) — and these tasks are what actually generates the
      // modules/features/stories in the first place, so the Modules list and
      // the Review left-panel tree need refreshing too. Also the Project
      // entity: its user_stories/approved_user_stories counts drive whether
      // Pipelines shows a Review button (hasPendingApproval), and newly
      // generated stories shift those counts.
      dispatch(
        baseApi.util.invalidateTags([
          { type: "IngestionJob", id: `LIST-${projectId}` },
          { type: "Pipeline", id: "LIST" },
          { type: "Module", id: `LIST-${projectId}` },
          { type: "Requirement", id: "LIST" },
          { type: "Requirement", id: `SUMMARY-${projectId}` },
          { type: "Requirement", id: `TREE-${projectId}` },
          { type: "Project", id: projectId },
        ]),
      );
      break;
    case TASK_TYPE.MODULE_REGENERATION:
    case TASK_TYPE.MODULE_FEEDBACK_PATCH:
    case TASK_TYPE.FEATURE_REGENERATION:
      // Also feeds Pipelines — module/feature-feedback regeneration shows up
      // as its own run there — and the Review left-panel tree, which reads
      // the same module/feature nodes via the Requirement TREE tag. Module
      // regeneration can add/remove stories too, so the Project entity
      // (feeds hasPendingApproval on Pipelines) needs refreshing as well.
      dispatch(
        baseApi.util.invalidateTags([
          { type: "Module", id: `LIST-${projectId}` },
          { type: "Requirement", id: `TREE-${projectId}` },
          { type: "IngestionJob", id: `LIST-${projectId}` },
          { type: "Pipeline", id: "LIST" },
          { type: "Project", id: projectId },
        ]),
      );
      break;
    case TASK_TYPE.STORY_GENERATION:
    case TASK_TYPE.STORY_REGENERATION:
    case TASK_TYPE.STORY_FEEDBACK_PATCH:
      // Feeds Requirements/Review (list, summary counts, tree view) and
      // Pipelines — story-feedback regeneration shows up as its own run
      // there. Also the Project entity (feeds hasPendingApproval on
      // Pipelines), since these tasks change the story counts it reads.
      dispatch(
        baseApi.util.invalidateTags([
          { type: "Requirement", id: "LIST" },
          { type: "Requirement", id: `SUMMARY-${projectId}` },
          { type: "Requirement", id: `TREE-${projectId}` },
          { type: "IngestionJob", id: `LIST-${projectId}` },
          { type: "Pipeline", id: "LIST" },
          { type: "Project", id: projectId },
        ]),
      );
      break;
  }
}

export function useProjectTasksSocket(
  projectId: string | null | undefined,
): void {
  const dispatch = useDispatch<AppDispatch>();
  const token = useAppSelector((state) => state.auth.accessToken);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectDelayRef = useRef(BASE_RECONNECT_DELAY_MS);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unmountedRef = useRef(false);
  // Holds the latest `connect` so onclose can retry without a self-reference.
  const connectRef = useRef<() => void>(() => {});
  // Timestamp of the last frame received (any type, heartbeats included) —
  // the only reliable signal that a connection is actually still alive.
  const lastFrameAtRef = useRef(0);

  const connect = useCallback(() => {
    if (unmountedRef.current || !projectId || !token) return;

    const url = `${WS_BASE_URL}/ws/projects/${projectId}?token=${encodeURIComponent(token)}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;
    lastFrameAtRef.current = Date.now();

    // The browser's readyState can lie: after the device sleeps, switches
    // networks, or a proxy silently drops an idle connection, the socket can
    // stay "OPEN" forever without onclose/onerror ever firing. Watch for
    // silence instead and force a close ourselves so the reconnect logic
    // below gets a chance to run.
    const staleCheckId = setInterval(() => {
      if (wsRef.current !== ws) {
        clearInterval(staleCheckId);
        return;
      }
      if (Date.now() - lastFrameAtRef.current > STALE_CONNECTION_TIMEOUT_MS) {
        ws.close();
      }
    }, STALE_CHECK_INTERVAL_MS);

    ws.onopen = () => {
      reconnectDelayRef.current = BASE_RECONNECT_DELAY_MS; // reset back-off after a healthy connect

      // A fresh connection doesn't guarantee the server replays every task's
      // true current state — a task that finished while we were disconnected
      // is no longer "current", so a reconnect can surface nothing but
      // heartbeats. Treat every successful connect (first load, tab revisit,
      // or an automatic reconnect) as a cue to resync from REST too, rather
      // than trusting the socket to volunteer the correction.
      dispatch(
        baseApi.util.invalidateTags([
          { type: "IngestionJob", id: `LIST-${projectId}` },
          { type: "Module", id: `LIST-${projectId}` },
          { type: "Requirement", id: "LIST" },
          { type: "Requirement", id: `SUMMARY-${projectId}` },
          { type: "Requirement", id: `TREE-${projectId}` },
        ]),
      );
    };

    ws.onmessage = (event) => {
      lastFrameAtRef.current = Date.now();

      let frame: ProjectSocketFrame;
      try {
        frame = JSON.parse(event.data) as ProjectSocketFrame;
      } catch {
        return;
      }

      if (frame.event === "heartbeat") return; // keep-alive only, no state change

      if (frame.event === "tasks.current") {
        const tasks: ProjectTask[] = frame.tasks.map((task) => ({
          task_id: task.task_id,
          task_type: task.task_type,
          status: task.status,
          progress: task.progress,
          stage: task.stage ?? null,
          meta: task.meta ?? null,
          error: task.error ?? null,
          updated_at: task.updated_at ?? new Date().toISOString(),
        }));
        dispatch(setProjectTasks({ projectId, tasks }));
        return;
      }

      // event === "task.update"
      dispatch(
        upsertProjectTask({
          projectId,
          task: {
            task_id: frame.task_id,
            task_type: frame.task_type,
            status: frame.status,
            progress: frame.progress,
            stage: frame.stage ?? null,
            meta: frame.meta,
            error: frame.error,
            updated_at: frame.timestamp,
          },
        }),
      );

      if (isTerminalTaskStatus(frame.status)) {
        invalidateTagsForTaskType(dispatch, projectId, frame.task_type);
      }
    };

    ws.onclose = (e) => {
      clearInterval(staleCheckId);

      // This event belongs to a socket that's already been superseded (e.g.
      // projectId changed and a newer connect() has taken over wsRef) —
      // unmountedRef alone isn't reliable for that case, since the newer
      // effect's setup resets it to false before this stale close event
      // (fired asynchronously for the old socket) gets a chance to check it.
      if (wsRef.current !== ws) return;
      // 4001 = unauthorized, 4004 = project not found — retrying can't fix either.
      if (e.code === 4001 || e.code === 4004) return;
      if (unmountedRef.current) return;

      reconnectTimerRef.current = setTimeout(() => {
        reconnectDelayRef.current = Math.min(
          reconnectDelayRef.current * 2,
          MAX_RECONNECT_DELAY_MS,
        );
        connectRef.current();
      }, reconnectDelayRef.current);
    };

    ws.onerror = () => {
      ws.close(); // onclose above owns the reconnect decision
    };
  }, [projectId, token, dispatch]);

  useEffect(() => {
    unmountedRef.current = false;
    connectRef.current = connect;
    connect();

    // The browser doesn't always notice a dropped connection right away —
    // reconnect immediately once it reports connectivity is back rather than
    // waiting out whatever back-off delay is currently queued.
    const handleOnline = () => {
      if (wsRef.current?.readyState === WebSocket.OPEN) return;
      reconnectDelayRef.current = BASE_RECONNECT_DELAY_MS;
      connect();
    };
    window.addEventListener("online", handleOnline);

    // The periodic staleness check can be slow to notice while the tab is
    // backgrounded (browsers throttle timers there) — so also check right
    // when the tab regains visibility, which is exactly when a device wakes
    // from sleep and the user comes back to look at it.
    const handleVisibility = () => {
      if (document.visibilityState !== "visible") return;
      if (Date.now() - lastFrameAtRef.current > STALE_CONNECTION_TIMEOUT_MS) {
        wsRef.current?.close();
      }
    };
    document.addEventListener("visibilitychange", handleVisibility);

    return () => {
      unmountedRef.current = true;
      window.removeEventListener("online", handleOnline);
      document.removeEventListener("visibilitychange", handleVisibility);
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close(1000, "unmount");
    };
  }, [connect]);
}
