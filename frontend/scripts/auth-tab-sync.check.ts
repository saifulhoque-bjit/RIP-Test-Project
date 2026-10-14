/**
 * Regression check for P1821-482: a tab still showing Account A must end its
 * session when another tab of the same browser logs out / logs in as another
 * account. Runs the real production store; only the browser globals
 * (window, localStorage, sessionStorage) are stubbed by run-auth-tab-sync-check.mjs.
 */
import { store } from "@/store";

type Check = { name: string; ok: boolean; detail?: string };
const checks: Check[] = [];
const expect = (name: string, ok: boolean, detail?: unknown) =>
  checks.push({ name, ok, detail: ok ? undefined : JSON.stringify(detail) });

const ACCOUNT_A = { id: "user-a", displayName: "Client Admin A", roles: ["client_admin"], permissions: [], email: "a@example.com" };
const ACCOUNT_B = { id: "user-b", displayName: "Super Admin B", roles: ["super_admin"], permissions: [], email: "b@example.com" };

/** What the browser delivers to Tab 1 when Tab 2 writes localStorage. */
function otherTabWrites(key: string | null, newValue: string | null) {
  const shared = globalThis.localStorage;
  if (key !== null) {
    if (newValue === null) shared.removeItem(key);
    else shared.setItem(key, newValue);
  }
  const event = new Event("storage");
  Object.assign(event, { key, newValue, storageArea: shared });
  globalThis.window.dispatchEvent(event);
}

const auth = () => store.getState().auth;

// Tab 1 booted with Account A restored from storage.
expect("tab 1 starts as Account A", auth().isAuthenticated && auth().user?.id === ACCOUNT_A.id, auth().user);

// Surrounding outcome: unrelated keys and a same-account rewrite keep Tab 1 logged in.
otherTabWrites("rip_active_tenant_id", "tenant-1");
otherTabWrites("authUser", JSON.stringify(ACCOUNT_A));
expect("unrelated key / same account keeps Tab 1 logged in", auth().isAuthenticated && auth().user?.id === ACCOUNT_A.id, auth());

// Customer steps 4: Tab 2 logs out Account A, then logs in as Account B.
otherTabWrites("authUser", null);
otherTabWrites("authUser", JSON.stringify(ACCOUNT_B));

expect("tab 1 is no longer authenticated as Account A", !auth().isAuthenticated && auth().user === null, auth());
expect("tab 1 dropped its per-tab tokens", globalThis.sessionStorage.getItem("rip_access_token") === null, globalThis.sessionStorage.getItem("rip_access_token"));
expect(
  "tab 1 shows a session-ended message",
  store.getState().ui.toasts.some((t) => /session/i.test(t.title)),
  store.getState().ui.toasts,
);
expect(
  "Account B's stored session in the other tab is left intact",
  JSON.parse(globalThis.localStorage.getItem("authUser") ?? "null")?.id === ACCOUNT_B.id,
  globalThis.localStorage.getItem("authUser"),
);

// Direct account switch without a logout first (authUser overwritten A -> B).
store.dispatch({ type: "auth/setAuthenticated", payload: { ...ACCOUNT_A, accessToken: "token-a" } });
otherTabWrites("authUser", JSON.stringify(ACCOUNT_B));
expect("direct A -> B switch also ends Tab 1's session", !auth().isAuthenticated, auth());

let failed = 0;
for (const c of checks) {
  console.log(`${c.ok ? "PASS" : "FAIL"} ${c.name}${c.detail ? `  got: ${c.detail}` : ""}`);
  if (!c.ok) failed++;
}
console.log(`${checks.length - failed}/${checks.length} passed`);
if (failed) process.exitCode = 1;
