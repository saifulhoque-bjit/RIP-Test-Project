// ── Task types emitted over GET /ws/projects/{project_id} ───────────────────
export const TASK_TYPE = {
  SOURCE_PROCESS: "source_process",
  INCREMENTAL_UPDATE: "incremental_update",
  MODULE_REGENERATION: "module_regeneration",
  MODULE_FEEDBACK_PATCH: "module_feedback_patch",
  FEATURE_REGENERATION: "feature_regeneration",
  STORY_GENERATION: "story_generation",
  STORY_REGENERATION: "story_regeneration",
  STORY_FEEDBACK_PATCH: "story_feedback_patch",
} as const;

export type TaskType = (typeof TASK_TYPE)[keyof typeof TASK_TYPE];

/**
 * Statuses that mean "this task will not emit further updates". The exact
 * set of statuses differs per task_type, but these terminal values overlap
 * across all of them, so one check works for every task type.
 */
const TERMINAL_STATUSES = new Set([
  "completed",
  "ready_for_review",
  "failed",
  "applied", // TODO: can be removed once the backend stops sending this status for story_generation tasks
  "change_set_ready", // TODO: can be removed once the backend stops sending this status for story_generation tasks
  "cancelled", 
  "canceled",
]);

export function isTerminalTaskStatus(status: string): boolean {
  return TERMINAL_STATUSES.has(status);
}

/** A single task's live state, normalized from tasks.current / task.update frames. */
export interface ProjectTask {
  [key: string]: unknown;
  task_id: string;
  task_type: string;
  status: string;
  progress: number;
  stage: string | null;
  meta: Record<string, unknown> | null;
  error: string | null;
  /** ISO timestamp of the last update received for this task. */
  updated_at: string;
}

// ── Raw server frame shapes ──────────────────────────────────────────────────

export interface TasksCurrentFrame {
  event: "tasks.current";
  project_id: string;
  tasks: Array<{
    task_id: string;
    task_type: string;
    status: string;
    progress: number;
    stage: string;
    meta?: Record<string, unknown> | null;
    error?: string | null;
    updated_at?: string;
  }>;
}

export interface TaskUpdateFrame {
  event: "task.update";
  task_id: string;
  task_type: string;
  project_id: string;
  status: string;
  progress: number;
  stage: string;
  meta: Record<string, unknown> | null;
  error: string | null;
  timestamp: string;
}

export interface HeartbeatFrame {
  event: "heartbeat";
  timestamp: string;
}

export type ProjectSocketFrame =
  | TasksCurrentFrame
  | TaskUpdateFrame
  | HeartbeatFrame;
