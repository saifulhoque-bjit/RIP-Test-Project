/**
 * Regression check for P1821-333: notifications disappear in a new tab.
 *
 * Run: node --test tests/notificationSocketNewTab.test.mjs  (from frontend/)
 *
 * A new browser tab shares `localStorage` (so `authUser` restores
 * `isAuthenticated: true`) but starts with an empty `sessionStorage`, so the
 * auth slice boots with `accessToken: null` while REST keeps working through
 * the HttpOnly `access_token` cookie. The notification socket must still
 * open (the backend authenticates `/ws/notifications` from that cookie) and
 * hand the `notifications.current` snapshot to the bell.
 *
 * Real code under test (transpiled with the repo's own TypeScript):
 *   - src/store/slices/authSlice.ts      (initial state from storage)
 *   - src/hooks/useNotificationSocket.ts (connection + event routing)
 * Stubs (labelled):
 *   - STUB storage: in-memory localStorage/sessionStorage for "new tab".
 *   - STUB WebSocket: fake browser WebSocket at the network boundary; it
 *     records the URL it is opened with.
 *   - STUB react: minimal hook runtime (useRef/useCallback/useEffect run
 *     once, synchronously) — no DOM renderer is installed in this repo.
 *   - STUB @/lib/wsBaseUrl: fixed base URL (it reads import.meta.env).
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import ts from "typescript";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const work = mkdtempSync(join(tmpdir(), "notif-newtab-"));
symlinkSync(join(root, "node_modules"), join(work, "node_modules"), "dir");

function transpile(srcRel, outName, rewrites) {
  let code = ts.transpileModule(readFileSync(join(root, srcRel), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  for (const [from, to] of rewrites) code = code.split(from).join(to);
  writeFileSync(join(work, outName), code);
  return pathToFileURL(join(work, outName)).href;
}

// STUB react: one-shot hook runtime.
writeFileSync(
  join(work, "react-stub.mjs"),
  `export const useRef = (v) => ({ current: v });
   export const useCallback = (fn) => fn;
   export const pendingEffects = [];
   export const useEffect = (fn) => { pendingEffects.push(fn); };`,
);
// STUB @/lib/wsBaseUrl
writeFileSync(join(work, "wsBaseUrl-stub.mjs"), `export const WS_BASE_URL = "ws://app.test";`);

function memoryStorage(initial = {}) {
  const data = new Map(Object.entries(initial));
  return {
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => data.set(k, String(v)),
    removeItem: (k) => data.delete(k),
  };
}

// STUB storage — state of a freshly opened tab of a logged-in user.
globalThis.localStorage = memoryStorage({
  authUser: JSON.stringify({ id: "u1", displayName: "U", roles: [], permissions: [], email: "u@x.test" }),
});
globalThis.sessionStorage = memoryStorage(); // per-tab: empty in a new tab

// STUB WebSocket — records every socket the hook opens.
const sockets = [];
globalThis.WebSocket = class {
  constructor(url) {
    this.url = url;
    sockets.push(this);
  }
  close() {}
};

test("a new tab opens the notification socket and receives the current list", async () => {
  const authUrl = transpile("src/store/slices/authSlice.ts", "authSlice.mjs", []);
  const { default: authReducer } = await import(authUrl);
  const auth = authReducer(undefined, { type: "@@INIT" });

  // Precondition: this really is the new-tab state the customer is in.
  assert.equal(auth.isAuthenticated, true);
  assert.equal(auth.accessToken, null);

  const hookUrl = transpile("src/hooks/useNotificationSocket.ts", "useNotificationSocket.mjs", [
    ['from "react"', 'from "./react-stub.mjs"'],
    ['from "@/lib/wsBaseUrl"', 'from "./wsBaseUrl-stub.mjs"'],
  ]);
  const { useNotificationSocket } = await import(hookUrl);
  const { pendingEffects } = await import(pathToFileURL(join(work, "react-stub.mjs")).href);

  const received = [];
  // Same call useNotifications makes: token = state.auth.accessToken ?? null
  useNotificationSocket(auth.accessToken ?? null, {
    onCurrent: (items) => received.push(...items),
    onNew: () => {},
    onRead: () => {},
    onReadAll: () => {},
  });
  pendingEffects.forEach((fn) => fn());

  assert.equal(sockets.length, 1, "new tab must open /ws/notifications (cookie auth)");
  const ws = sockets[0];
  assert.equal(ws.url, "ws://app.test/ws/notifications", "no `token=` param when the tab holds no token");

  const snapshot = [
    { id: "n1", title: "A", message: "a", is_read: false, created_at: "2026-10-01T00:00:00Z" },
    { id: "n2", title: "B", message: "b", is_read: true, created_at: "2026-10-02T00:00:00Z" },
  ];
  ws.onmessage({ data: JSON.stringify({ event: "notifications.current", notifications: snapshot, unread_count: 1 }) });
  assert.deepEqual(received, snapshot);
});

test("a tab holding a token still sends it as the query param", async () => {
  const { useNotificationSocket } = await import(pathToFileURL(join(work, "useNotificationSocket.mjs")).href);
  const { pendingEffects } = await import(pathToFileURL(join(work, "react-stub.mjs")).href);
  pendingEffects.length = 0;
  sockets.length = 0;

  useNotificationSocket("tok en", { onCurrent() {}, onNew() {}, onRead() {}, onReadAll() {} });
  pendingEffects.forEach((fn) => fn());

  assert.equal(sockets.length, 1);
  assert.equal(sockets[0].url, "ws://app.test/ws/notifications?token=tok%20en");
});
