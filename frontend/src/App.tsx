import { useEffect } from "react";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { ToastContainer } from "@/components/common/ToastContainer";
import { toast } from "@/lib/toast";
import { baseApi } from "@/services/api/baseApi";
import { store } from "@/store";
import {
  selectCurrentUser,
  setUnauthenticatedInCurrentTab,
} from "@/store/slices/authSlice";
import routes from "@/routes";

// Create router once outside component
const router = createBrowserRouter(routes);

function AuthSessionSync() {
  useEffect(() => {
    const handleStorageChange = (event: StorageEvent) => {
      if (event.key !== "authUser" || event.storageArea !== localStorage) {
        return;
      }

      const currentUser = selectCurrentUser(store.getState());
      if (!currentUser) {
        return;
      }

      let nextUserId: string | null = null;
      if (event.newValue) {
        try {
          const parsed: unknown = JSON.parse(event.newValue);
          if (
            typeof parsed === "object" &&
            parsed !== null &&
            "id" in parsed &&
            typeof parsed.id === "string"
          ) {
            nextUserId = parsed.id;
          }
        } catch {
          // Treat malformed persisted auth data as a session change.
        }
      }

      if (nextUserId === currentUser.id) {
        return;
      }

      store.dispatch(setUnauthenticatedInCurrentTab());
      store.dispatch(baseApi.util.resetApiState());
      toast.info("Your session changed in another tab. Please sign in again.", {
        toastId: "cross-tab-session-changed",
      });
    };

    window.addEventListener("storage", handleStorageChange);
    return () => window.removeEventListener("storage", handleStorageChange);
  }, []);

  return null;
}

export default function App() {
  return (
    <>
      <AuthSessionSync />
      <RouterProvider router={router} />
      <ToastContainer />
    </>
  );
}
