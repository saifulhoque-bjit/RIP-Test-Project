/**
 * useNotifications
 *
 * State owner for the notification bell. Sources data exclusively from the
 * WebSocket (useNotificationSocket) rather than polling REST.
 *
 * Exposes `pendingDismissIds` — a Set of notification ids that should be
 * animated out. Header watches this via useEffect and triggers dismissOne()
 * for each id, keeping animation ownership inside Header while allowing
 * both click-driven and WS-driven dismissals to go through the same path.
 */

import { useCallback, useMemo, useState, type ReactNode } from "react";
import { BellIcon } from "@/assets/icons/notifications/BellIcon";
import {
  useMarkAllReadMutation,
  useMarkAsReadMutation,
} from "@/services/api/modules/notification";
import type { Notification } from "@/types/notification";
import { useNotificationSocket } from "@/hooks/useNotificationSocket";
import { useAppDispatch, useAppSelector } from "@/store/hooks";
import { invalidateTagsForNotification } from "@/hooks/notificationInvalidation";
import {
  isNoChangesExplanationSeen,
  markNoChangesExplanationSeen,
} from "@/lib/noChangesExplanationSeen";
import codeIcon from "@/assets/icons/notifications/CodeIcon.svg";
import exportIcon from "@/assets/icons/notifications/ExportIcon.svg";
import rfpIcon from "@/assets/icons/notifications/RFPIcon.svg";
import syncIcon from "@/assets/icons/notifications/SyncIcon.svg";
import { formatDate } from "@/utils/formatDate";

export interface HeaderNotification {
  id: string | number;
  title?: string;
  message?: string;
  createdAt?: string;
  unread?: boolean;
  icon?: ReactNode;
}

const NOTIFICATION_TYPE_ICONS: Record<string, string> = {
  export: exportIcon,
  sync: syncIcon,
  rfp: rfpIcon,
  sourcecode: codeIcon,
  incremental: syncIcon,
};

function getNotificationIcon(type: string | undefined): ReactNode {
  const normalizedType = type?.trim().toLowerCase().replace(/[_-]/g, "");
  const iconSource = normalizedType
    ? NOTIFICATION_TYPE_ICONS[normalizedType]
    : undefined;

  if (!iconSource) {
    return <BellIcon className="h-4 w-4" aria-hidden="true" />;
  }

  return <img src={iconSource} alt="" aria-hidden="true" className="h-4 w-4" />;
}

function toHeaderNotification(n: Notification): HeaderNotification {

  return {
    id: n.id,
    title: n.title,
    message: n.message,
    createdAt: formatDate(n.created_at),
    unread: !n.is_read,
    icon: getNotificationIcon(n.notification_type),
  };
}

/** A one-time acknowledgement message the backend wants shown in a modal, distinct from the notification's own title/message. */
function extractNoChangesExplanation(notification: Notification): string | null {
  const value = notification.data?.no_changes_explanation;
  if (typeof value !== "string") return null;

  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

interface PendingExplanation {
  id: string;
  title: string;
  explanation: string;
}

export function useNotifications() {
  const token = useAppSelector((state) => state.auth.accessToken) ?? null;
  const dispatch = useAppDispatch();

  const [markAllReadMutation] = useMarkAllReadMutation();
  const [markAsReadMutation] = useMarkAsReadMutation();

  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [explanationQueue, setExplanationQueue] = useState<PendingExplanation[]>([]);

  // ── WebSocket handlers ───────────────────────────────────────────────────

  const onCurrent = useCallback((items: Notification[]) => {
    setNotifications(items);

    // Catches messages that already existed before this page load/reconnect
    // — not just ones that arrive live via onNew below.
    const qualifying = items
      .filter((n) => extractNoChangesExplanation(n) && !isNoChangesExplanationSeen(n.id))
      .sort((a, b) => a.created_at.localeCompare(b.created_at));
    if (qualifying.length === 0) return;

    setExplanationQueue((prev) => {
      const queuedIds = new Set(prev.map((p) => p.id));
      const fresh = qualifying
        .filter((n) => !queuedIds.has(n.id))
        .map((n) => ({
          id: n.id,
          title: n.title,
          explanation: extractNoChangesExplanation(n)!,
        }));
      return fresh.length > 0 ? [...prev, ...fresh] : prev;
    });
  }, []);

  const onNew = useCallback(
    (notification: Notification) => {
      setNotifications((prev) => {
        if (prev.some((n) => n.id === notification.id)) return prev;
        return [notification, ...prev];
      });

      // No-op for every notification except TAP's async /ack, which is the
      // one server-side data change no project task frame covers. Without it
      // the TAP Sync count stays stale until a reload.
      invalidateTagsForNotification(dispatch, notification);

      const explanation = extractNoChangesExplanation(notification);
      if (explanation && !isNoChangesExplanationSeen(notification.id)) {
        setExplanationQueue((prev) =>
          prev.some((p) => p.id === notification.id)
            ? prev
            : [...prev, { id: notification.id, title: notification.title, explanation }],
        );
      }
    },
    [dispatch],
  );

  const onRead = useCallback((updated: Notification) => {
    setNotifications((prev) =>
      prev.map((notification) =>
        notification.id === updated.id ? updated : notification,
      ),
    );
  }, []);

  const onReadAll = useCallback(() => {
    setNotifications((prev) =>
      prev.map((notification) => ({ ...notification, is_read: true })),
    );
  }, []);

  useNotificationSocket(token, {
    onCurrent,
    onNew,
    onRead,
    onReadAll,
  });

  const handleMarkAllRead = useCallback(() => {
    markAllReadMutation();
  }, [markAllReadMutation]);

  const handleNotificationClick = useCallback(
    (notification: HeaderNotification) => {
      if (!notification.unread) return;

      markAsReadMutation(String(notification.id));
    },
    [markAsReadMutation],
  );

  const handleAcknowledgeExplanation = useCallback(() => {
    const current = explanationQueue[0];
    if (!current) return;

    markNoChangesExplanationSeen(current.id);
    setExplanationQueue((prev) => prev.slice(1));
  }, [explanationQueue]);

  const headerNotifications: HeaderNotification[] = useMemo(
    () => notifications.map(toHeaderNotification),
    [notifications],
  );

  return {
    notifications: headerNotifications,
    isLoading: false,
    handleMarkAllRead,
    handleNotificationClick,
    pendingExplanation: explanationQueue[0] ?? null,
    handleAcknowledgeExplanation,
  };
}
