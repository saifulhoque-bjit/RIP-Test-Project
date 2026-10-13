import type { Store } from "@reduxjs/toolkit";
import { baseApi } from "@/services/api/baseApi";
import { endSessionFromOtherTab } from "@/store/slices/authSlice";
import { pushToast } from "@/store/slices/toastSlice";

const AUTH_USER_STORAGE_KEY = "authUser";
const SESSION_ENDED_TOAST_ID = "session-ended-other-tab";

function parseUserId(value: string | null): string | null {
  if (!value) return null;
  try {
    const parsed: unknown = JSON.parse(value);
    if (parsed && typeof parsed === "object" && "id" in parsed) {
      const { id } = parsed as { id: unknown };
      return id == null ? null : String(id);
    }
  } catch {
    /* ignore malformed value */
  }
  return null;
}

/**
 * Auth cookies are HttpOnly and shared by every tab of the browser, while each
 * tab keeps its own in-memory user. When another tab logs out or signs in as a
 * different user (it rewrites the shared `authUser` entry), end this tab's
 * session so it stops acting as the previous user; ProtectedRoute then
 * redirects to /login.
 */
export function installCrossTabAuthSync(store: Store): () => void {
  if (typeof window === "undefined") return () => {};

  const onStorage = (event: StorageEvent): void => {
    // `key === null` means the other tab called localStorage.clear().
    if (event.key !== AUTH_USER_STORAGE_KEY && event.key !== null) return;

    const state = store.getState() as {
      auth: { isAuthenticated: boolean; user: { id: string } | null };
    };
    if (!state.auth.isAuthenticated) return;

    const currentId = state.auth.user?.id == null ? null : String(state.auth.user.id);
    const nextId = parseUserId(
      event.key === null ? null : event.newValue,
    );
    if (nextId !== null && nextId === currentId) return;

    store.dispatch(endSessionFromOtherTab());
    store.dispatch(baseApi.util.resetApiState());
    store.dispatch(
      pushToast({
        kind: "warning",
        title: "Your session has ended",
        body: "You were signed out because the account changed in another tab. Please log in again.",
        toastId: SESSION_ENDED_TOAST_ID,
      }),
    );
  };

  window.addEventListener("storage", onStorage);
  return () => window.removeEventListener("storage", onStorage);
}
