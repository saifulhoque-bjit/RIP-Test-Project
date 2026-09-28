import { createSlice, type PayloadAction } from "@reduxjs/toolkit";
import type { RootState } from "@/store";
import type { ModuleFeedback } from "@/types/module";

interface ModuleFeedbackState {
  byProjectId: Record<string, ModuleFeedback>;
}

interface UpsertModuleFeedbackPayload {
  projectId: string;
  moduleId: string;
  moduleLabel: string;
  note: string;
}

interface RemoveModuleFeedbackPayload {
  projectId: string;
  moduleId: string;
}

const initialState: ModuleFeedbackState = {
  byProjectId: {},
};

function ensureProjectFeedback(
  state: ModuleFeedbackState,
  projectId: string,
): ModuleFeedback {
  if (!state.byProjectId[projectId]) {
    state.byProjectId[projectId] = {
      project_id: projectId,
      notes: [],
    };
  }

  return state.byProjectId[projectId];
}

const moduleFeedbackSlice = createSlice({
  name: "moduleFeedback",
  initialState,
  reducers: {
    clearProjectModuleFeedback(state, action: PayloadAction<string>) {
      delete state.byProjectId[action.payload];
    },

    upsertModuleFeedback(
      state,
      action: PayloadAction<UpsertModuleFeedbackPayload>,
    ) {
      const projectId = action.payload.projectId.trim();
      const moduleId = action.payload.moduleId.trim();
      if (!projectId || !moduleId) return;

      const nextNote = action.payload.note.trim();
      const projectFeedback = ensureProjectFeedback(state, projectId);

      const existing = projectFeedback.notes.find(
        (item) => item.module_id === moduleId,
      );

      if (existing) {
        existing.note = nextNote;
        existing.module_label = action.payload.moduleLabel;
        return;
      }

      projectFeedback.notes.push({
        module_id: moduleId,
        module_label: action.payload.moduleLabel,
        note: nextNote,
      });
    },

    removeModuleFeedback(
      state,
      action: PayloadAction<RemoveModuleFeedbackPayload>,
    ) {
      const projectId = action.payload.projectId.trim();
      const moduleId = action.payload.moduleId.trim();
      if (!projectId || !moduleId) return;

      const projectFeedback = state.byProjectId[projectId];
      if (!projectFeedback) return;

      projectFeedback.notes = projectFeedback.notes.filter(
        (item) => item.module_id !== moduleId,
      );

      if (!projectFeedback.notes.length) {
        delete state.byProjectId[projectId];
      }
    },
  },
});

export const {
  clearProjectModuleFeedback,
  upsertModuleFeedback,
  removeModuleFeedback,
} = moduleFeedbackSlice.actions;

export const selectProjectModuleFeedback =
  (projectId: string) => (state: RootState): ModuleFeedback | undefined =>
    state.moduleFeedback.byProjectId[projectId];

export default moduleFeedbackSlice.reducer;
