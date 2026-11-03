import { getErrorMessage } from "@/utils/getErrorMessage";

export interface JiraSyncErrorToast {
  kind: "error" | "info";
  message: string;
}

/**
 * True when the Jira sync request ended without the API's answer: a gateway
 * timeout from the load balancer (its HTML 504 page reaches us as a
 * PARSING_ERROR carrying originalStatus 504) or a dropped connection. The API
 * keeps running the sync in that case and reports the result through the
 * JIRA_SYNC_COMPLETED / JIRA_SYNC_FAILED notification, so it is not a failure.
 */
export function isJiraSyncOutcomeUnknown(error: unknown): boolean {
  if (typeof error !== "object" || error === null) return false;
  const { status, originalStatus } = error as {
    status?: unknown;
    originalStatus?: unknown;
  };
  return (
    status === 504 ||
    originalStatus === 504 ||
    status === "FETCH_ERROR" ||
    status === "TIMEOUT_ERROR"
  );
}

/** The toast the Sync Tray shows when the Jira sync request rejects. */
export function jiraSyncErrorToast(error: unknown): JiraSyncErrorToast {
  if (isJiraSyncOutcomeUnknown(error)) {
    return {
      kind: "info",
      message:
        "Jira sync is still running — you'll get a notification when it finishes.",
    };
  }
  return {
    kind: "error",
    message: getErrorMessage(error, "Failed to sync to Jira."),
  };
}
