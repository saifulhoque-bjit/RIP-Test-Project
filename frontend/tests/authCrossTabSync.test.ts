// Regression test for P1821-482: an account switch in another tab of the
// same browser must end this tab's session. Runs the real store, auth slice
// and route-guard selector; only browser storage is stubbed (see
// browserStorageStub.ts). Run with `npm run test:auth-sync`.
import { writeFromOtherTab } from "./browserStorageStub";
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { store } from "@/store";
import {
  selectIsAuthenticated,
  selectCurrentUser,
  setAuthenticated,
} from "@/store/slices/authSlice";
import type { AuthUser } from "@/types";

const accountA = {
  id: "user-a",
  displayName: "Client Admin A",
  email: "a@example.com",
  roles: ["client_admin"],
  permissions: ["project:read"],
} as unknown as AuthUser;

const accountB = {
  id: "user-b",
  displayName: "Super Admin B",
  email: "b@example.com",
  roles: ["super_admin"],
  permissions: ["tenant:manage"],
} as unknown as AuthUser;

const profile = (u: AuthUser) => {
  const { id, displayName, roles, permissions, email } = u;
  return JSON.stringify({ id, displayName, roles, permissions, email });
};

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  // Tab 1: Account A logs in.
  store.dispatch(
    setAuthenticated({
      ...accountA,
      accessToken: "access-a",
      refreshToken: "refresh-a",
      cognitoUsername: "cognito-a",
    }),
  );
  assert.equal(selectIsAuthenticated(store.getState()), true);
});

test("Tab 2 logs out A and logs in B: Tab 1 drops Account A's session", () => {
  // Tab 2: log out the previous account, then log in with Account B.
  writeFromOtherTab("authUser", null);
  writeFromOtherTab("authUser", profile(accountB));

  // Tab 1: ProtectedRoute redirects to /login when this is false.
  assert.equal(selectIsAuthenticated(store.getState()), false);
  assert.equal(selectCurrentUser(store.getState()), null);
  assert.equal(store.getState().auth.accessToken, null);
  assert.equal(store.getState().auth.refreshToken, null);
  assert.equal(sessionStorage.getItem("rip_access_token"), null);
  assert.equal(sessionStorage.getItem("rip_refresh_token"), null);
  // The user is told why they were signed out.
  assert.ok(
    store.getState().ui.toasts.some((t) => t.toastId === "session-replaced"),
  );
  // Tab 1 must not wipe Account B's shared profile out from under Tab 2.
  assert.equal(localStorage.getItem("authUser"), profile(accountB));
});

test("Tab 2 logs in B directly over A: Tab 1 drops Account A's session", () => {
  writeFromOtherTab("authUser", profile(accountB));
  assert.equal(selectIsAuthenticated(store.getState()), false);
  assert.equal(localStorage.getItem("authUser"), profile(accountB));
});

test("same account re-written by another tab keeps Tab 1 signed in", () => {
  writeFromOtherTab("authUser", profile(accountA));
  assert.equal(selectIsAuthenticated(store.getState()), true);
  assert.equal(selectCurrentUser(store.getState())?.id, "user-a");
  assert.equal(store.getState().auth.accessToken, "access-a");
});

test("unrelated localStorage keys from another tab keep Tab 1 signed in", () => {
  writeFromOtherTab("rip_current_project_id", "p-1");
  writeFromOtherTab("rip_active_tenant_id", "t-1");
  assert.equal(selectIsAuthenticated(store.getState()), true);
  assert.equal(store.getState().auth.accessToken, "access-a");
});
