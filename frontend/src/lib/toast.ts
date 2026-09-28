import { store } from "@/store";
import { pushToast, type ToastType } from "@/store/slices/toastSlice";

interface ToastOptions {
  toastId?: string;
  body?: string;
  [key: string]: unknown;
}

function show(kind: ToastType, message: string, options?: ToastOptions) {
  const { toastId, body } = options ?? {};
  if (toastId && store.getState().ui.toasts.some((t) => t.toastId === toastId)) {
    return;
  }
  store.dispatch(pushToast({ kind, title: message, body, toastId }));
}

export const toast = {
  success: (message: string, options?: ToastOptions) => show("success", message, options),
  error: (message: string, options?: ToastOptions) => show("error", message, options),
  info: (message: string, options?: ToastOptions) => show("info", message, options),
  warning: (message: string, options?: ToastOptions) => show("warning", message, options),
};
