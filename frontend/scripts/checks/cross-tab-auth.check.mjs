// Cross-tab session check (P1821-482).
//
// Loads the real Redux store (src/store/index.ts) through Vite's SSR loader
// with minimal in-memory stand-ins for the browser boundary (localStorage,
// sessionStorage, window event target). It then simulates what another tab of
// the same browser does to the shared localStorage when a different account
// logs in or the current account logs out, and asserts this tab's in-memory
// session reacts.
//
// Run: node scripts/checks/cross-tab-auth.check.mjs
import assert from "node:assert/strict";
import { createServer } from "vite";

// ── Browser boundary stubs ────────────────────────────────────────────────
class MemoryStorage {
  #m = new Map();
  getItem(k) { return this.#m.has(k) ? this.#m.get(k) : null; }
  setItem(k, v) { this.#m.set(k, String(v)); }
  removeItem(k) { this.#m.delete(k); }
  clear() { this.#m.clear(); }
}
const local = new MemoryStorage();
const session = new MemoryStorage();
const win = new EventTarget();
globalThis.localStorage = local;
globalThis.sessionStorage = session;
globalThis.window = win;
win.localStorage = local;
win.sessionStorage = session;

/** Emulates the `storage` event the browser fires in *other* tabs. */
function otherTabWrites(key, newValue) {
  const oldValue = local.getItem(key);
  if (newValue === null) local.removeItem(key);
  else local.setItem(key, newValue);
  const ev = new Event("storage");
  Object.assign(ev, { key, oldValue, newValue, storageArea: local });
  win.dispatchEvent(ev);
}

const userA = { id: "user-a", displayName: "A", roles: ["client_admin"], permissions: [], email: "a@x.com" };
const userB = { id: "user-b", displayName: "B", roles: ["super_admin"], permissions: [], email: "b@x.com" };

// Tab 1 is logged in as Account A before the app boots.
local.setItem("authUser", JSON.stringify(userA));
session.setItem("rip_access_token", "token-a");

const server = await createServer({
  configFile: new URL("../../vite.config.ts", import.meta.url).pathname,
  server: { middlewareMode: true, hmr: false, ws: false },
  appType: "custom",
  logLevel: "error",
});

let failures = 0;
function check(name, fn) {
  try { fn(); console.log(`ok   - ${name}`); }
  catch (e) { failures++; console.log(`FAIL - ${name}\n       ${e.message.split("\n")[0]}`); }
}

try {
  const { store } = await server.ssrLoadModule("/src/store/index.ts");
  const auth = () => store.getState().auth;

  check("tab 1 boots as Account A", () => {
    assert.equal(auth().isAuthenticated, true);
    assert.equal(auth().user?.id, "user-a");
  });

  // Unrelated shared keys and same-user re-persists must not end the session.
  otherTabWrites("rip_active_tenant_id", "t-1");
  otherTabWrites("authUser", JSON.stringify({ ...userA, displayName: "A2" }));
  check("unrelated key / same user in another tab keeps Account A signed in", () => {
    assert.equal(auth().isAuthenticated, true);
    assert.equal(auth().user?.id, "user-a");
  });

  // Customer steps 4: another tab logs out A, then logs in as B.
  otherTabWrites("authUser", null);
  otherTabWrites("authUser", JSON.stringify(userB));
  check("after Account B logs in elsewhere, tab 1 is no longer Account A", () => {
    assert.notEqual(auth().user?.id, "user-a", "tab 1 still holds Account A's identity");
    assert.equal(auth().isAuthenticated, false, "tab 1 still treated as authenticated");
    assert.equal(auth().accessToken, null);
  });
  check("tab 1's cleanup does not clobber Account B's shared profile", () => {
    assert.equal(JSON.parse(local.getItem("authUser")).id, "user-b");
  });
} finally {
  await server.close();
}

if (failures) {
  console.log(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nall checks passed");
