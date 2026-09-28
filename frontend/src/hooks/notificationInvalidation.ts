/**
 * Cache invalidation for incoming notifications.
 *
 * Counterpart to `invalidateTagsForTaskType` in `useProjectTasksSocket`, but
 * for the *global* notification socket. Almost every notification is purely a
 * message and must invalidate nothing — the one exception today is TAP's
 * inbound `/ack` callback, which mutates data no project task frame covers.
 *
 * TAP sync is fire-and-forget: `executeTapSync` only stages (`sync_status =
 * "pending_ack"`) and notifies, so it deliberately invalidates nothing.
 * `is_tap_synced` flips server-side later, when TAP calls back on `/ack`, and
 * the only thing the client hears about it is a notification. Without this,
 * the TAP Sync badge on the project header (`getSyncCandidates` →
 * `total_count`) keeps showing the pre-ack count until a full page reload.
 *
 * Identification is by `data` shape, NOT by `notification_type`:
 * `notification_type` is severity only (`info`/`success`/`warning`/`error`),
 * so it cannot distinguish a TAP ack from any other notification. The backend
 * emits `sync_id` + `job_id` from exactly one place — `_notify_tap_ack` in
 * `tap_sync_service.py` — and no other `publish_notification` call site sends
 * either key, which makes that pair a unique discriminator.
 */

import { baseApi } from "@/services/api/baseApi";
import type { AppDispatch } from "@/store";
import type { Notification } from "@/types/notification";

interface TapAckData {
  project_id: string;
  /** "COMPLETED" on success; anything else is a recorded failure. */
  status?: string;
  /** How many entities actually had `is_tap_synced` flipped by this ack. */
  entities_synced?: number;
}

function readString(
  data: Record<string, unknown>,
  key: string,
): string | undefined {
  const value = data[key];
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

/**
 * Narrows to a TAP `/ack` notification, or returns null for everything else —
 * which is every other notification the app receives.
 */
function asTapAck(data: Record<string, unknown> | null): TapAckData | null {
  if (!data) return null;

  const projectId = readString(data, "project_id");
  // sync_id + job_id together are emitted only by _notify_tap_ack.
  if (!projectId || !readString(data, "sync_id") || !readString(data, "job_id")) {
    return null;
  }

  const entitiesSynced = data.entities_synced;
  return {
    project_id: projectId,
    status: readString(data, "status"),
    entities_synced:
      typeof entitiesSynced === "number" ? entitiesSynced : undefined,
  };
}

export function invalidateTagsForNotification(
  dispatch: AppDispatch,
  notification: Notification,
): void {
  const ack = asTapAck(notification.data);
  if (!ack) return;

  // `handle_ack` only flips `is_tap_synced` when status is COMPLETED, so a
  // "TAP Sync Failed" ack leaves every count exactly as it was — nothing to
  // refetch. Same for a COMPLETED ack that flipped nothing (TAP acked ids that
  // matched no mapping). `entities_synced` missing means an unexpected payload:
  // refetch rather than risk a stale badge.
  if (ack.status !== "COMPLETED" || ack.entities_synced === 0) return;

  const projectId = ack.project_id;

  dispatch(
    baseApi.util.invalidateTags([
      // The count on the project header's TAP Sync button.
      { type: "Requirement", id: `SYNC-tap-${projectId}` },
      // NOT `TREE-${projectId}`: the Review/Requirements tree carries
      // `is_tap_synced` on its nodes but no component renders it, and the
      // tray now reads the server-filtered sync-candidates endpoint instead
      // of filtering that tree. Invalidating it would refetch the whole tree
      // (`keepUnusedDataFor: 0`) for zero visible change. Add it back if a
      // TAP badge ever lands on a tree node.
      // `tap_synced_count` on the project entity, which Overview's
      // "Pending sync (TAP)" tile derives from.
      { type: "Project", id: projectId },
      // Portfolio-wide pending-sync (TAP) KPI on the Dashboard.
      { type: "Dashboard", id: "STATS" },
    ]),
  );
}
