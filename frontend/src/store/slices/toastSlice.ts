import { createSlice, nanoid, type PayloadAction } from "@reduxjs/toolkit";

export type ToastType = "success" | "info" | "error" | "warning";

export interface Toast {
  id: string;
  kind: ToastType;
  title: string;
  body?: string;
  toastId?: string;
}

interface UiState {
  toasts: Toast[];
}

const initialState: UiState = { toasts: [] };

const toastSlice = createSlice({
  name: "ui",
  initialState,
  reducers: {
    pushToast: {
      reducer(state, action: PayloadAction<Toast>) {
        state.toasts.push(action.payload);
      },
      prepare(input: { kind?: ToastType; title: string; body?: string; toastId?: string }) {
        return {
          payload: {
            id: nanoid(),
            kind: input.kind ?? "success",
            title: input.title,
            body: input.body,
            toastId: input.toastId,
          } satisfies Toast,
        };
      },
    },
    dismissToast(state, action: PayloadAction<string>) {
      state.toasts = state.toasts.filter((t) => t.id !== action.payload);
    },
  },
});

export const { pushToast, dismissToast } = toastSlice.actions;
export default toastSlice.reducer;
