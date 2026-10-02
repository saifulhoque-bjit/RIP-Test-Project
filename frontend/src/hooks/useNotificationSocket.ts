/**
 * useNotificationSocket
 *
 * Opens a WebSocket to /ws/notifications and dispatches structured events
 * to the provided callbacks. Handles reconnection with exponential back-off,
 * heartbeat ignore, and clean teardown on unmount.
 *
 * The caller (useNotifications) owns all state — this hook is purely
 * responsible for the connection lifecycle and event routing.
 */

import { useCallback, useEffect, useRef } from "react";
import type { Notification } from "@/types/notification";
import { WS_BASE_URL } from "@/lib/wsBaseUrl";

// ── Event shapes sent by the server ───────────────────────────────────────

interface CurrentEvent {
  event: "notifications.current";
  notifications: Notification[];
  unread_count: number;
}

interface NewEvent {
  event: "notification.new";
  notification: Notification;
  timestamp: string;
}

interface ReadEvent {
  event: "notification.read";
  notification: Notification;
  timestamp: string;
}

interface ReadAllEvent {
  event: "notification.read_all";
  timestamp: string;
}

interface HeartbeatEvent {
  event: "heartbeat";
  timestamp: string;
}

type ServerEvent = CurrentEvent | NewEvent | ReadEvent | ReadAllEvent | HeartbeatEvent;

// ── Hook options ──────────────────────────────────────────────────────────

export interface NotificationSocketCallbacks {
  onCurrent: (notifications: Notification[], unreadCount: number) => void;
  onNew: (notification: Notification) => void;
  onRead: (notification: Notification) => void;
  onReadAll: () => void;
}

const BASE_RECONNECT_DELAY_MS = 1_000;
const MAX_RECONNECT_DELAY_MS = 30_000;

// ── Hook ─────────────────────────────────────────────────────────────────

export function useNotificationSocket(
  token: string | null,
  callbacks: NotificationSocketCallbacks,
) {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectDelayRef = useRef(BASE_RECONNECT_DELAY_MS);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unmountedRef = useRef(false);

  // Keep callbacks in a ref so the stable connect closure always sees the
  // latest versions without needing to be listed as a dependency.
  const callbacksRef = useRef(callbacks);
  callbacksRef.current = callbacks;

  const connect = useCallback(() => {
    if (unmountedRef.current) return;

    // A new tab has no sessionStorage token, but shares the HttpOnly auth cookie.
    const tokenQuery = token ? `?token=${encodeURIComponent(token)}` : "";
    const url = `${WS_BASE_URL}/ws/notifications${tokenQuery}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      reconnectDelayRef.current = BASE_RECONNECT_DELAY_MS; // reset back-off on success
    };

    ws.onmessage = (event) => {
      let parsed: ServerEvent;
      try {
        parsed = JSON.parse(event.data) as ServerEvent;
      } catch {
        return;
      }

      const cb = callbacksRef.current;
      switch (parsed.event) {
        case "notifications.current":
          cb.onCurrent(parsed.notifications, parsed.unread_count);
          break;
        case "notification.new":
          cb.onNew(parsed.notification);
          break;
        case "notification.read":
          cb.onRead(parsed.notification);
          break;
        case "notification.read_all":
          cb.onReadAll();
          break;
        case "heartbeat":
          // intentionally ignored
          break;
      }
    };

    ws.onclose = (e) => {
      // 4001 = unauthorized, 4004 = user not found — don't reconnect
      if (e.code === 4001 || e.code === 4004) return;
      if (unmountedRef.current) return;

      reconnectTimerRef.current = setTimeout(() => {
        reconnectDelayRef.current = Math.min(
          reconnectDelayRef.current * 2,
          MAX_RECONNECT_DELAY_MS,
        );
        connect();
      }, reconnectDelayRef.current);
    };

    ws.onerror = () => {
      // onclose fires right after onerror, reconnect handled there
      ws.close();
    };
  }, [token]);

  useEffect(() => {
    unmountedRef.current = false;
    connect();

    return () => {
      unmountedRef.current = true;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close();
    };
  }, [connect]);
}