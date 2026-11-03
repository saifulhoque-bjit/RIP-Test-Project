/**
 * Which incoming notifications also pop up as a toast.
 *
 * A failed pipeline run (e.g. RFP module/feature generation with no provider
 * API key) is reported through an `error` notification whose message carries
 * the reason. The bell alone is easy to miss, so a failure that arrives live
 * is also shown as an error toast with that reason. Only live notifications
 * qualify — `onCurrent`'s backlog replay must not re-toast old failures.
 */

import type { Notification } from "@/types/notification";

export interface NotificationToast {
  toastId: string;
  title: string;
  body?: string;
}

export function getFailureToast(
  notification: Notification,
): NotificationToast | null {
  if (notification.notification_type !== "error") return null;
  const body = notification.message?.trim();
  return {
    toastId: `notification-${notification.id}`,
    title: notification.title,
    body: body || undefined,
  };
}
