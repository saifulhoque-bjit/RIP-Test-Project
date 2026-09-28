import { createSlice, type PayloadAction } from '@reduxjs/toolkit';
import type { IngestionJob, IngestionStatus } from '@/types';

interface IngestionState {
  /** Active ingestion jobs tracked in the current session. */
  activeJobs: IngestionJob[];
  /** IDs of jobs currently being polled for status updates. */
  pollingJobIds: string[];
}

const initialState: IngestionState = {
  activeJobs: [],
  pollingJobIds: [],
};

const ingestionSlice = createSlice({
  name: 'ingestion',
  initialState,
  reducers: {
    addJob(state, action: PayloadAction<IngestionJob>) {
      const existingIndex = state.activeJobs.findIndex(
        (j) => j.id === action.payload.id,
      );
      if (existingIndex === -1) {
        state.activeJobs.push(action.payload);
      } else {
        state.activeJobs[existingIndex] = action.payload;
      }
    },

    updateJobStatus(
      state,
      action: PayloadAction<{
        jobId: string;
        status: IngestionStatus;
        statusMessage: string;
        progressPercent: number;
      }>,
    ) {
      const job = state.activeJobs.find((j) => j.id === action.payload.jobId);
      if (job) {
        job.status = action.payload.status;
        job.statusMessage = action.payload.statusMessage;
        job.progressPercent = action.payload.progressPercent;
      }
    },

    removeJob(state, action: PayloadAction<string>) {
      state.activeJobs = state.activeJobs.filter((j) => j.id !== action.payload);
    },

    startPolling(state, action: PayloadAction<string>) {
      if (!state.pollingJobIds.includes(action.payload)) {
        state.pollingJobIds.push(action.payload);
      }
    },

    stopPolling(state, action: PayloadAction<string>) {
      state.pollingJobIds = state.pollingJobIds.filter((id) => id !== action.payload);
    },

    clearCompletedJobs(state) {
      state.activeJobs = state.activeJobs.filter(
        (j) => j.status !== 'COMPLETED' && j.status !== 'FAILED',
      );
    },
  },
});

export const {
  addJob,
  updateJobStatus,
  removeJob,
  startPolling,
  stopPolling,
  clearCompletedJobs,
} = ingestionSlice.actions;

export default ingestionSlice.reducer;
