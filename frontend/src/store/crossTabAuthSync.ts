import type { Dispatch, UnknownAction } from "@reduxjs/toolkit";
import { baseApi } from "@/services/api/baseApi";
import type { AuthUser } from "@/types";
import { setSessionEndedInOtherTab } from "@/store/slices/authSlice";
import { pushToast } from "@/store/slices/toastSlice";

const AUTH_USER_STORAGE_KEY = "authUser";

interface SyncedStore {
  dispatch: Dispatch<UnknownAction>;
  getState: () => { auth: { user: AuthUser | null; isAuthenticated: boolean } };
}

/** Id of the user whose profile is stored now, or null when none (or unreadable). */
function readStoredUserId(): string | null {
  try {
    const stored = localStorage.getItem(AUTH_USER_STORAGE_KEY);
    if (!stored) return null;
    const user: Partial<AuthUser> | null = JSON.parse(stored);
    return typeof user?.id === "string" ? user.id : null;
  } catch {
    return null;
  }
}

/**
 * The session cookies are shared by every tab of the browser, but each tab's
 * auth state is read from storage once, at load. When another tab logs out or
 * signs in as a different user (the `authUser` key changes), this tab would go
 * on presenting the previous account while its requests carry the new
 * session's cookies. End this tab's session instead, as logout does, and tell
 * the user why.
 */
export function syncAuthAcrossTabs(store: SyncedStore): () => void {
  if (typeof window === "undefined") return () => {};

  const onStorage = (event: StorageEvent) => {
    // `key` is null when another tab cleared the whole storage.
    if (event.key !== AUTH_USER_STORAGE_KEY && event.key !== null) return;

    const { user, isAuthenticated } = store.getState().auth;
    if (!isAuthenticated) return;
    if (user && readStoredUserId() === user.id) return;

    store.dispatch(setSessionEndedInOtherTab());
    store.dispatch(baseApi.util.resetApiState());
    store.dispatch(
      pushToast({
        kind: "info",
        title: "Your session ended because you signed out or switched accounts in another tab.",
        toastId: "session-ended-in-other-tab",
      }),
    );
  };

  window.addEventListener("storage", onStorage);
  return () => window.removeEventListener("storage", onStorage);
}
