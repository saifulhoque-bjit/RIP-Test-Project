import type { Notification } from "@/types/notification";

// The Jira sync runs inside one long POST. If the connection to it drops
// (gateway timeout, proxy/network cut-off) the backend keeps going and still
// finishes the sync, so the client error says nothing about the outcome.
// For those errors the tray records the project here, and the backend's own
// "Jira Sync Completed"/"Jira Sync Failed" notification settles the toast.

// Titles the backend sends (SUMMARY_ACTIVITY_JIRA_SYNC_* in core/messages.py).
const JIRA_SYNC_COMPLETED_TITLE = "Jira Sync Completed";
const JIRA_SYNC_FAILED_TITLE = "Jira Sync Failed";

const UNCONFIRMED_HTTP_STATUSES = [502, 503, 504];

const pendingProjectIds = new Set<string>();

/**
 * Whether a failed sync request leaves the sync's real outcome unknown —
 * the request never got the backend's answer, rather than the backend
 * answering with an error.
 */
export function isUnconfirmedSyncError(error: unknown): boolean {
  if (typeof error !== "object" || error === null) return false;
  const status = (error as { status?: unknown }).status;
  return (
    status === "FETCH_ERROR" ||
    status === "TIMEOUT_ERROR" ||
    (typeof status === "number" && UNCONFIRMED_HTTP_STATUSES.includes(status))
  );
}

export function markJiraSyncPending(projectId: string): void {
  pendingProjectIds.add(projectId);
}

/**
 * Returns the outcome of a pending Jira sync this notification reports, and
 * clears it — or null when the notification isn't about one.
 */
export function resolvePendingJiraSync(
  notification: Notification,
): "completed" | "failed" | null {
  const projectId = notification.data?.project_id;
  if (typeof projectId !== "string" || !pendingProjectIds.has(projectId)) {
    return null;
  }

  if (notification.title === JIRA_SYNC_COMPLETED_TITLE) {
    pendingProjectIds.delete(projectId);
    return "completed";
  }
  if (notification.title === JIRA_SYNC_FAILED_TITLE) {
    pendingProjectIds.delete(projectId);
    return "failed";
  }
  return null;
}
