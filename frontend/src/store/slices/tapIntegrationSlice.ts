import { createSlice, type PayloadAction } from "@reduxjs/toolkit";
import type { RootState } from "@/store";
import type { TapIntegrationConfig } from "@/services/api/modules/tapIntegration";

interface TapIntegrationState {
  byProjectId: Record<string, TapIntegrationConfig>;
}

const initialState: TapIntegrationState = {
  byProjectId: {},
};

const tapIntegrationSlice = createSlice({
  name: "tapIntegration",
  initialState,
  reducers: {
    setTapIntegration(
      state,
      action: PayloadAction<{ projectId: string; integration: TapIntegrationConfig }>,
    ) {
      state.byProjectId[action.payload.projectId] = action.payload.integration;
    },

    clearTapIntegration(state, action: PayloadAction<string>) {
      delete state.byProjectId[action.payload];
    },
  },
});

export const { setTapIntegration, clearTapIntegration } =
  tapIntegrationSlice.actions;

export const selectProjectTapIntegration =
  (projectId: string) =>
  (state: RootState): TapIntegrationConfig | undefined =>
    state.tapIntegration.byProjectId[projectId];

export default tapIntegrationSlice.reducer;
