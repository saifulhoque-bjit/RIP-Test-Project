import type { Dispatch } from "@reduxjs/toolkit";
import { baseApi } from "@/services/api/baseApi";
import { toast } from "@/lib/toast";
import type { AuthUser } from "@/types";
import {
  AUTH_USER_STORAGE_KEY,
  endSessionFromOtherTab,
} from "@/store/slices/authSlice";

interface AuthTabSyncStore {
  getState: () => { auth: { user: AuthUser | null } };
  dispatch: Dispatch;
}

function storedUserId(value: string | null): string | null {
  if (!value) return null;
  try {
    return (JSON.parse(value) as Partial<AuthUser>)?.id ?? null;
  } catch {
    return null;
  }
}

/**
 * The session lives in HttpOnly cookies shared by every tab of the browser,
 * so when another tab logs out or signs in as a different account this tab's
 * in-memory user no longer matches the session the server sees. Listen for
 * the other tab's write to the shared `authUser` key (the `storage` event
 * fires only in *other* tabs) and end this tab's session so ProtectedRoute
 * sends it to /login.
 */
export function registerAuthTabSync(
  store: AuthTabSyncStore,
  target: Pick<Window, "addEventListener"> = window,
): void {
  target.addEventListener("storage", (event: StorageEvent) => {
    // key === null means the other tab cleared localStorage entirely.
    if (event.key !== null && event.key !== AUTH_USER_STORAGE_KEY) return;

    const currentUser = store.getState().auth.user;
    if (!currentUser) return;
    if (storedUserId(event.newValue) === currentUser.id) return;

    store.dispatch(endSessionFromOtherTab());
    store.dispatch(baseApi.util.resetApiState());
    toast.warning("Your session has ended", {
      toastId: "session-ended-other-tab",
      body: "You were signed out in another tab. Please log in again.",
    });
  });
}
