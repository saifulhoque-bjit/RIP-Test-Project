import { createSlice, type PayloadAction } from "@reduxjs/toolkit";
import type { RootState } from "@/store";
import type { FeatureFeedback } from "@/types/feature";

interface FeatureFeedbackState {
  byProjectId: Record<string, FeatureFeedback>;
}

interface UpsertFeatureFeedbackPayload {
  projectId: string;
  featureId: string;
  modCode?: string;
  mfuId?: string | null;
  overallFeedback: string;
}

interface RemoveFeatureFeedbackPayload {
  projectId: string;
  featureId: string;
}

const initialState: FeatureFeedbackState = {
  byProjectId: {},
};

function ensureProjectFeedback(
  state: FeatureFeedbackState,
  projectId: string,
): FeatureFeedback {
  if (!state.byProjectId[projectId]) {
    state.byProjectId[projectId] = {
      project_id: projectId,
      features: [],
    };
  }

  return state.byProjectId[projectId];
}

const featureFeedbackSlice = createSlice({
  name: "featureFeedback",
  initialState,
  reducers: {
    clearProjectFeatureFeedback(state, action: PayloadAction<string>) {
      delete state.byProjectId[action.payload];
    },

    upsertFeatureFeedback(
      state,
      action: PayloadAction<UpsertFeatureFeedbackPayload>,
    ) {
      const projectId = action.payload.projectId.trim();
      const featureId = action.payload.featureId.trim();
      if (!projectId || !featureId) return;

      const nextFeedback = action.payload.overallFeedback.trim();
      const { modCode, mfuId } = action.payload;
      const projectFeedback = ensureProjectFeedback(state, projectId);

      const existing = projectFeedback.features.find(
        (item) => item.feature_id === featureId,
      );

      if (existing) {
        existing.overall_feedback = nextFeedback;
        existing.mod_code = modCode;
        existing.mfu_id = mfuId;
        return;
      }

      projectFeedback.features.push({
        feature_id: featureId,
        mod_code: modCode,
        mfu_id: mfuId,
        overall_feedback: nextFeedback,
      });
    },

    removeFeatureFeedback(
      state,
      action: PayloadAction<RemoveFeatureFeedbackPayload>,
    ) {
      const projectId = action.payload.projectId.trim();
      const featureId = action.payload.featureId.trim();
      if (!projectId || !featureId) return;

      const projectFeedback = state.byProjectId[projectId];
      if (!projectFeedback) return;

      projectFeedback.features = projectFeedback.features.filter(
        (item) => item.feature_id !== featureId,
      );

      if (!projectFeedback.features.length) {
        delete state.byProjectId[projectId];
      }
    },
  },
});

export const {
  clearProjectFeatureFeedback,
  upsertFeatureFeedback,
  removeFeatureFeedback,
} = featureFeedbackSlice.actions;

export const selectProjectFeatureFeedback =
  (projectId: string) => (state: RootState): FeatureFeedback | undefined =>
    state.featureFeedback.byProjectId[projectId];

export default featureFeedbackSlice.reducer;
