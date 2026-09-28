import { createSlice, type PayloadAction } from "@reduxjs/toolkit";
import type { RootState } from "@/store";
import type { JiraIntegrationConfig } from "@/services/api/modules/jira";

interface JiraIntegrationState {
  byProjectId: Record<string, JiraIntegrationConfig>;
}

const initialState: JiraIntegrationState = {
  byProjectId: {},
};

const jiraIntegrationSlice = createSlice({
  name: "jiraIntegration",
  initialState,
  reducers: {
    setJiraIntegration(
      state,
      action: PayloadAction<{ projectId: string; integration: JiraIntegrationConfig }>,
    ) {
      state.byProjectId[action.payload.projectId] = action.payload.integration;
    },

    clearJiraIntegration(state, action: PayloadAction<string>) {
      delete state.byProjectId[action.payload];
    },
  },
});

export const { setJiraIntegration, clearJiraIntegration } =
  jiraIntegrationSlice.actions;

export const selectProjectJiraIntegration =
  (projectId: string) => (state: RootState): JiraIntegrationConfig | undefined =>
    state.jiraIntegration.byProjectId[projectId];

export default jiraIntegrationSlice.reducer;
