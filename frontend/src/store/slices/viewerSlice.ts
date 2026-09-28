import { createSlice, type PayloadAction } from '@reduxjs/toolkit';
import type { SourceType, GroundingCoordinates } from '@/types';

/** Which panel is currently focused in the split-screen viewer. */
export const VIEWER_PANEL = {
  REQUIREMENT: 'REQUIREMENT',
  SOURCE: 'SOURCE',
} as const;
export type ViewerPanel = (typeof VIEWER_PANEL)[keyof typeof VIEWER_PANEL];

interface ViewerState {
  /** ID of the Canonical Requirement being viewed. */
  activeRequirementId: string | null;
  /** ID of the Source currently rendered in the right panel. */
  activeSourceId: string | null;
  /** Type of the active source (drives which viewer controls are shown). */
  activeSourceType: SourceType | null;
  /** Grounding coordinates to auto-navigate / auto-highlight on load. */
  activeGrounding: GroundingCoordinates | null;
  /** ID of the Fragment currently in focus (used for multi-fragment conflict list). */
  focusedFragmentId: string | null;
  /** Which panel is active (keyboard navigation / accessibility). */
  activePanel: ViewerPanel;
  /** Current playback position in seconds (video/audio sources). */
  playbackPositionSeconds: number;
  /** Current zoom level for image/PDF sources (1.0 = 100%). */
  zoomLevel: number;
  /** Rotation in degrees for image/PDF sources (0, 90, 180, 270). */
  rotationDegrees: 0 | 90 | 180 | 270;
}

const initialState: ViewerState = {
  activeRequirementId: null,
  activeSourceId: null,
  activeSourceType: null,
  activeGrounding: null,
  focusedFragmentId: null,
  activePanel: VIEWER_PANEL.REQUIREMENT,
  playbackPositionSeconds: 0,
  zoomLevel: 1.0,
  rotationDegrees: 0,
};

const viewerSlice = createSlice({
  name: 'viewer',
  initialState,
  reducers: {
    openViewer(
      state,
      action: PayloadAction<{
        requirementId: string;
        sourceId: string;
        sourceType: SourceType;
        grounding: GroundingCoordinates;
      }>,
    ) {
      state.activeRequirementId = action.payload.requirementId;
      state.activeSourceId = action.payload.sourceId;
      state.activeSourceType = action.payload.sourceType;
      state.activeGrounding = action.payload.grounding;
      state.focusedFragmentId = null;
      state.playbackPositionSeconds = 0;
      state.zoomLevel = 1.0;
      state.rotationDegrees = 0;
    },

    setFocusedFragment(state, action: PayloadAction<string | null>) {
      state.focusedFragmentId = action.payload;
    },

    setActivePanel(state, action: PayloadAction<ViewerPanel>) {
      state.activePanel = action.payload;
    },

    setPlaybackPosition(state, action: PayloadAction<number>) {
      state.playbackPositionSeconds = action.payload;
    },

    setZoomLevel(state, action: PayloadAction<number>) {
      // Clamp zoom between 0.25× and 4×
      state.zoomLevel = Math.max(0.25, Math.min(4, action.payload));
    },

    setRotation(state, action: PayloadAction<0 | 90 | 180 | 270>) {
      state.rotationDegrees = action.payload;
    },

    resetViewerControls(state) {
      state.zoomLevel = 1.0;
      state.rotationDegrees = 0;
      state.playbackPositionSeconds = 0;
    },

    closeViewer() {
      return initialState;
    },
  },
});

export const {
  openViewer,
  setFocusedFragment,
  setActivePanel,
  setPlaybackPosition,
  setZoomLevel,
  setRotation,
  resetViewerControls,
  closeViewer,
} = viewerSlice.actions;

export default viewerSlice.reducer;
