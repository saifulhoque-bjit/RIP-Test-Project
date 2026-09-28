import { createSlice, type PayloadAction } from "@reduxjs/toolkit";

interface TenantState {
  activeTenantId: string | null;
}

const ACTIVE_TENANT_STORAGE_KEY = "rip_active_tenant_id";

const getStoredTenantId = (): string | null => {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(ACTIVE_TENANT_STORAGE_KEY);
};

const initialState: TenantState = {
  activeTenantId: getStoredTenantId(),
};

const tenantSlice = createSlice({
  name: "tenant",
  initialState,
  reducers: {
    setActiveTenantId(state, action: PayloadAction<string | null>) {
      state.activeTenantId = action.payload;
      if (typeof window !== "undefined") {
        if (action.payload) {
          localStorage.setItem(ACTIVE_TENANT_STORAGE_KEY, action.payload);
        } else {
          localStorage.removeItem(ACTIVE_TENANT_STORAGE_KEY);
        }
      }
    },
  },
});

export const { setActiveTenantId } = tenantSlice.actions;

export default tenantSlice.reducer;

// ── Selectors ────────────────────────────────────────────────────────────────
export const selectActiveTenantId = (state: { tenant: TenantState }) =>
  state.tenant.activeTenantId;
