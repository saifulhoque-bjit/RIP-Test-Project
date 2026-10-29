// @vitest-environment jsdom
// SPEC: spec-d586c9
import { act, createElement, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { Provider } from "react-redux";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { HeaderNotification } from "@/hooks/useNotifications";
import type { Notification } from "@/types/notification";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function makeNotification(i: number): Notification {
  return {
    id: `n${i}`,
    user_id: "u1",
    title: `Title ${i}`,
    message: "message",
    notification_type: "info",
    is_read: false,
    data: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
  };
}

/** Stands in for the server: every socket that opens receives the user's 3 notifications. */
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  readonly url: string;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  serverOpen() {
    this.onopen?.();
    this.onmessage?.({
      data: JSON.stringify({
        event: "notifications.current",
        notifications: [1, 2, 3].map(makeNotification),
        unread_count: 3,
      }),
    });
  }

  serverClose(code: number) {
    this.onclose?.({ code });
  }

  close() {}
}

const AUTH_USER = {
  id: "u1",
  displayName: "QA",
  roles: [],
  permissions: [],
  email: "qa@example.com",
};

let root: Root | null = null;
let seen: HeaderNotification[] = [];

/** Seeds browser storage, then loads a fresh store (it reads storage on import) and mounts the bell hook. */
async function mountBell(opts: { loggedIn: boolean; tabToken: string | null }) {
  if (opts.loggedIn) localStorage.setItem("authUser", JSON.stringify(AUTH_USER));
  if (opts.tabToken) sessionStorage.setItem("rip_access_token", opts.tabToken);

  vi.resetModules();
  const { store } = await import("@/store");
  const { useNotifications } = await import("@/hooks/useNotifications");

  function Bell() {
    const { notifications } = useNotifications();
    useEffect(() => {
      seen = notifications;
    }, [notifications]);
    return null;
  }

  const container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root!.render(createElement(Provider, { store, children: createElement(Bell) }));
  });
}

async function openFromServer(ws: FakeWebSocket) {
  await act(async () => {
    ws.serverOpen();
  });
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  seen = [];
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("notification bell socket", () => {
  it("connects by cookie and shows the list in a new tab that has no per-tab token", async () => {
    await mountBell({ loggedIn: true, tabToken: null });

    expect(FakeWebSocket.instances).toHaveLength(1);
    const ws = FakeWebSocket.instances[0];
    expect(new URL(ws.url).pathname).toBe("/ws/notifications");
    expect(ws.url).not.toContain("token=");

    await openFromServer(ws);
    expect(seen).toHaveLength(3);
    expect(seen.filter((n) => n.unread)).toHaveLength(3);
  });

  it("keeps sending the tab's token as ?token= when the tab has one", async () => {
    await mountBell({ loggedIn: true, tabToken: "jwt-abc" });

    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(FakeWebSocket.instances[0].url).toMatch(/\/ws\/notifications\?token=jwt-abc$/);

    await openFromServer(FakeWebSocket.instances[0]);
    expect(seen).toHaveLength(3);
  });

  it("opens no socket when the user is not logged in", async () => {
    await mountBell({ loggedIn: false, tabToken: null });

    expect(FakeWebSocket.instances).toHaveLength(0);
    expect(seen).toHaveLength(0);
  });

  it.each([4001, 4004])("does not reconnect a cookie socket after close code %i", async (code) => {
    vi.useFakeTimers();
    await mountBell({ loggedIn: true, tabToken: null });

    act(() => FakeWebSocket.instances[0].serverClose(code));
    act(() => vi.advanceTimersByTime(60_000));

    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("does not reconnect after unmount", async () => {
    vi.useFakeTimers();
    await mountBell({ loggedIn: true, tabToken: null });
    const ws = FakeWebSocket.instances[0];

    act(() => root!.unmount());
    root = null;
    ws.serverClose(1000);
    vi.advanceTimersByTime(60_000);

    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("backs off from 1 s, caps at 30 s, and resets after a successful open", async () => {
    vi.useFakeTimers();
    await mountBell({ loggedIn: true, tabToken: null });

    const delays: number[] = [];
    for (let i = 0; i < 7; i++) {
      const count = FakeWebSocket.instances.length;
      act(() => FakeWebSocket.instances[count - 1].serverClose(1006));
      let waited = 0;
      while (FakeWebSocket.instances.length === count) {
        act(() => vi.advanceTimersByTime(500));
        waited += 500;
      }
      delays.push(waited);
    }
    expect(delays).toEqual([1_000, 2_000, 4_000, 8_000, 16_000, 30_000, 30_000]);

    const latest = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
    await act(async () => latest.serverOpen());
    act(() => latest.serverClose(1006));
    const before = FakeWebSocket.instances.length;
    act(() => vi.advanceTimersByTime(1_000));
    expect(FakeWebSocket.instances).toHaveLength(before + 1);
    expect(FakeWebSocket.instances.every((ws) => !ws.url.includes("token="))).toBe(true);
  });
});
