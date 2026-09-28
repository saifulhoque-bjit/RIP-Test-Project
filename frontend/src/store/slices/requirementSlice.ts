import { createSlice, type PayloadAction } from '@reduxjs/toolkit';
import type { CanonicalRequirement, RequirementStatus, RequirementVersion } from '@/types';

interface RequirementState {
  /** The currently focused/open requirement (detail view). */
  activeRequirement: CanonicalRequirement | null;
  /** The version selected in the version history sidebar. */
  selectedVersionId: string | null;
  /** Versions loaded for the active requirement. */
  versions: RequirementVersion[];
  /** Whether the version history sidebar is open. */
  isVersionSidebarOpen: boolean;
  /** Whether the governance controls are in edit mode. */
  isGovernanceEditMode: boolean;
  /** Status being transitioned to (intermediate state during governance modal flow). */
  pendingStatusTransition: RequirementStatus | null;
}

const initialState: RequirementState = {
  activeRequirement: null,
  selectedVersionId: null,
  versions: [],
  isVersionSidebarOpen: false,
  isGovernanceEditMode: false,
  pendingStatusTransition: null,
};

const requirementSlice = createSlice({
  name: 'requirement',
  initialState,
  reducers: {
    setActiveRequirement(state, action: PayloadAction<CanonicalRequirement | null>) {
      state.activeRequirement = action.payload;
      state.selectedVersionId = null;
      state.isGovernanceEditMode = false;
      state.pendingStatusTransition = null;
    },

    setSelectedVersionId(state, action: PayloadAction<string | null>) {
      state.selectedVersionId = action.payload;
    },

    setVersions(state, action: PayloadAction<RequirementVersion[]>) {
      state.versions = action.payload;
    },

    toggleVersionSidebar(state) {
      state.isVersionSidebarOpen = !state.isVersionSidebarOpen;
    },

    setVersionSidebarOpen(state, action: PayloadAction<boolean>) {
      state.isVersionSidebarOpen = action.payload;
    },

    setGovernanceEditMode(state, action: PayloadAction<boolean>) {
      state.isGovernanceEditMode = action.payload;
    },

    setPendingStatusTransition(state, action: PayloadAction<RequirementStatus | null>) {
      state.pendingStatusTransition = action.payload;
    },

    clearGovernanceState(state) {
      state.isGovernanceEditMode = false;
      state.pendingStatusTransition = null;
    },
  },
});

export const {
  setActiveRequirement,
  setSelectedVersionId,
  setVersions,
  toggleVersionSidebar,
  setVersionSidebarOpen,
  setGovernanceEditMode,
  setPendingStatusTransition,
  clearGovernanceState,
} = requirementSlice.actions;

export default requirementSlice.reducer;
