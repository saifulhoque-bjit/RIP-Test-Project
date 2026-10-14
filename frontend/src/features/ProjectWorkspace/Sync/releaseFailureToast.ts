import { getErrorMessage } from "@/utils/getErrorMessage";

export interface ReleaseFailureToast {
  kind: "error" | "warning";
  message: string;
}

/**
 * fetchBaseQuery statuses that mean no answer came back from the API itself:
 * the connection dropped, the request timed out, or a gateway in front of the
 * API gave up waiting. The sync runs to completion inside that request on the
 * server, so none of these say whether it failed — it may well still be
 * running and go on to complete.
 */
const OUTCOME_UNKNOWN_STATUSES: ReadonlySet<unknown> = new Set([
  "FETCH_ERROR",
  "TIMEOUT_ERROR",
  502,
  503,
  504,
]);

function isOutcomeUnknown(error: unknown): boolean {
  if (typeof error !== "object" || error === null) return false;
  return OUTCOME_UNKNOWN_STATUSES.has((error as { status?: unknown }).status);
}

/**
 * Toast for a release whose request rejected. Only an answer from the API is
 * reported as a failed sync; a lost or timed-out request is reported as an
 * unknown outcome, since the sync may still complete on the server.
 */
export function getReleaseFailureToast(
  error: unknown,
  targetLabel: string,
): ReleaseFailureToast {
  if (isOutcomeUnknown(error)) {
    return {
      kind: "warning",
      message: `No response from the server while syncing to ${targetLabel}. The sync may still be running — check the notifications for its result before syncing again.`,
    };
  }
  return {
    kind: "error",
    message: getErrorMessage(error, `Failed to sync to ${targetLabel}.`),
  };
}
