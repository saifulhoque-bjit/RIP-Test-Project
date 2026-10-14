import type { Dispatch, UnknownAction } from "@reduxjs/toolkit";
import { toast } from "@/lib/toast";
import { baseApi } from "@/services/api/baseApi";
import { setSessionReplacedElsewhere } from "@/store/slices/authSlice";
import type { AuthUser } from "@/types";

/** localStorage key the auth slice persists the signed-in profile under. */
const AUTH_USER_STORAGE_KEY = "authUser";

interface SyncableStore {
  dispatch: Dispatch<UnknownAction>;
  getState: () => { auth: { isAuthenticated: boolean; user: AuthUser | null } };
}

function storedUserId(value: string | null): string | null {
  if (!value) return null;
  try {
    return (JSON.parse(value) as { id?: string }).id ?? null;
  } catch {
    return null;
  }
}

/**
 * Auth cookies are shared by every tab of the browser, but each tab keeps its
 * own in-memory auth state. When another tab logs out or signs in as a
 * different account, end this tab's session too so it cannot keep showing the
 * previous account (ProtectedRoute then redirects to /login).
 */
export function syncAuthAcrossTabs(store: SyncableStore): () => void {
  const onStorage = (event: StorageEvent) => {
    // key === null means another tab cleared localStorage entirely.
    if (event.key !== AUTH_USER_STORAGE_KEY && event.key !== null) return;

    const { isAuthenticated, user } = store.getState().auth;
    if (!isAuthenticated) return;

    const nextUserId = storedUserId(
      event.key === null ? null : event.newValue,
    );
    if (nextUserId !== null && nextUserId === user?.id) return;

    store.dispatch(setSessionReplacedElsewhere());
    store.dispatch(baseApi.util.resetApiState());
    toast.warning("Your session has ended.", {
      body: "You were signed out in another tab. Please log in again.",
      toastId: "session-replaced",
    });
  };

  window.addEventListener("storage", onStorage as EventListener);
  return () =>
    window.removeEventListener("storage", onStorage as EventListener);
}
