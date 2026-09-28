import { createSlice, type PayloadAction } from '@reduxjs/toolkit';
import type { RequirementDetailData } from '@/types';

interface ModuleViewState {
  activeModuleId: string;
  expandedFeatureId: string | null;
  searchText: string;
  featureFilter: string;
  statusFilter: string;
  /** Requirements cache keyed by featureId. Cleared on search/filter/module change. */
  requirementsCache: Record<string, RequirementDetailData[]>;
}

const initialState: ModuleViewState = {
  activeModuleId: '',
  expandedFeatureId: null,
  searchText: '',
  featureFilter: '',
  statusFilter: '',
  requirementsCache: {},
};

const moduleViewSlice = createSlice({
  name: 'moduleView',
  initialState,
  reducers: {
    setActiveModule(state, action: PayloadAction<string>) {
      if (state.activeModuleId !== action.payload) {
        state.activeModuleId = action.payload;
        state.expandedFeatureId = null;
        state.searchText = '';
        state.featureFilter = '';
        state.statusFilter = '';
        state.requirementsCache = {};
      }
    },

    /** Atomically switches the active module and pre-expands the first feature,
     *  avoiding an intermediate null expandedFeatureId that would cause a
     *  duplicate RTK Query subscription cycle. */
    setActiveModuleWithInitialFeature(
      state,
      action: PayloadAction<{ moduleId: string; firstFeatureId: string | null }>,
    ) {
      const { moduleId, firstFeatureId } = action.payload;
      if (state.activeModuleId !== moduleId) {
        state.activeModuleId = moduleId;
        state.searchText = '';
        state.featureFilter = '';
        state.statusFilter = '';
        state.requirementsCache = {};
      }
      state.expandedFeatureId = firstFeatureId;
    },

    setExpandedFeature(state, action: PayloadAction<string | null>) {
      state.expandedFeatureId = action.payload;
    },

    setSearchText(state, action: PayloadAction<string>) {
      if (state.searchText !== action.payload) {
        state.searchText = action.payload;
        state.requirementsCache = {};
      }
    },

    setFeatureFilter(state, action: PayloadAction<string>) {
      if (state.featureFilter !== action.payload) {
        state.featureFilter = action.payload;
        state.requirementsCache = {};
      }
    },

    setStatusFilter(state, action: PayloadAction<string>) {
      if (state.statusFilter !== action.payload) {
        state.statusFilter = action.payload;
        state.requirementsCache = {};
      }
    },

    cacheFeatureRequirements(
      state,
      action: PayloadAction<{ featureId: string; items: RequirementDetailData[] }>,
    ) {
      state.requirementsCache[action.payload.featureId] = action.payload.items;
    },

    updateRequirementStatusInCache(
      state,
      action: PayloadAction<{ featureId: string; requirementId: string; status: string }>,
    ) {
      const cached = state.requirementsCache[action.payload.featureId];
      if (!cached) return;
      const item = cached.find((r) => r.id === action.payload.requirementId);
      if (item) item.status = action.payload.status;
    },

    resetModuleViewState() {
      return initialState;
    },
  },
});

export const {
  setActiveModule,
  setActiveModuleWithInitialFeature,
  setExpandedFeature,
  setSearchText,
  setFeatureFilter,
  setStatusFilter,
  cacheFeatureRequirements,
  updateRequirementStatusInCache,
  resetModuleViewState,
} = moduleViewSlice.actions;

export default moduleViewSlice.reducer;
