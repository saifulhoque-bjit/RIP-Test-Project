import { createSlice, type PayloadAction } from '@reduxjs/toolkit';

interface SelectionState {
  /** Bulk-selected Canonical Requirement IDs for batch operations (e.g., Bulk Approve). */
  selectedRequirementIds: string[];
}

const initialState: SelectionState = {
  selectedRequirementIds: [],
};

const selectionSlice = createSlice({
  name: 'selection',
  initialState,
  reducers: {
    toggleRequirementSelection(state, action: PayloadAction<string>) {
      const id = action.payload;
      const index = state.selectedRequirementIds.indexOf(id);
      if (index === -1) {
        state.selectedRequirementIds.push(id);
      } else {
        state.selectedRequirementIds.splice(index, 1);
      }
    },

    selectAllRequirements(state, action: PayloadAction<string[]>) {
      state.selectedRequirementIds = action.payload;
    },

    clearRequirementSelection(state) {
      state.selectedRequirementIds = [];
    },
  },
});

export const {
  toggleRequirementSelection,
  selectAllRequirements,
  clearRequirementSelection,
} = selectionSlice.actions;

export default selectionSlice.reducer;
