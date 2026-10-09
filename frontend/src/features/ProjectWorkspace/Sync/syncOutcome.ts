import type { FetchBaseQueryError } from "@reduxjs/toolkit/query/react";
import { getErrorMessage } from "@/utils/getErrorMessage";

export interface SyncFailureToast {
  kind: "error" | "info";
  message: string;
}

/**
 * Statuses where the request never got the backend's answer: the connection
 * dropped, the client timed out, or a gateway in front of the API gave up
 * (502/503/504). The sync runs inside the request on the server and keeps
 * going after the client stops waiting, so none of these say it failed.
 */
export const SYNC_OUTCOME_UNKNOWN_STATUSES: FetchBaseQueryError["status"][] = [
  "FETCH_ERROR",
  "TIMEOUT_ERROR",
  502,
  503,
  504,
];

function isOutcomeUnknown(error: unknown): boolean {
  if (typeof error !== "object" || error === null || !("status" in error)) {
    return false;
  }
  const { status, originalStatus } = error as {
    status: unknown;
    originalStatus?: unknown;
  };
  // A gateway's HTML error page on a JSON endpoint surfaces as PARSING_ERROR,
  // with the gateway's HTTP status kept in `originalStatus`.
  const effective = status === "PARSING_ERROR" ? originalStatus : status;
  return SYNC_OUTCOME_UNKNOWN_STATUSES.some((s) => s === effective);
}

/** Decides which toast a failed sync release request shows. */
export function describeSyncFailure(
  error: unknown,
  targetLabel: string,
): SyncFailureToast {
  if (isOutcomeUnknown(error)) {
    return {
      kind: "info",
      message: `Lost contact with the server before the ${targetLabel} sync reported back. It may still be running — you'll get a notification when it finishes, so check it before syncing again.`,
    };
  }
  return {
    kind: "error",
    message: getErrorMessage(error, `Failed to sync to ${targetLabel}.`),
  };
}
