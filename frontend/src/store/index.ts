import { configureStore } from '@reduxjs/toolkit';
import { baseApi } from '@/services/api/baseApi';
import authReducer from '@/store/slices/authSlice';
import tenantReducer from '@/store/slices/tenantSlice';
import requirementReducer from '@/store/slices/requirementSlice';
import ingestionReducer from '@/store/slices/ingestionSlice';
import viewerReducer from '@/store/slices/viewerSlice';
import selectionReducer from '@/store/slices/selectionSlice';
import projectTasksReducer from '@/store/slices/projectTasksSlice';
import moduleViewReducer from '@/store/slices/moduleViewSlice';
import userStoryFeedbackReducer from '@/store/slices/userStoryFeedbackSlice';
import moduleFeedbackReducer from '@/store/slices/moduleFeedbackSlice';
import featureFeedbackReducer from '@/store/slices/featureFeedbackSlice';
import jiraIntegrationReducer from '@/store/slices/jiraIntegrationSlice';
import tapIntegrationReducer from '@/store/slices/tapIntegrationSlice';
import storyFeedbackRegenerationReducer from '@/store/slices/storyFeedbackRegenerationSlice';
import featureFeedbackRegenerationReducer from '@/store/slices/featureFeedbackRegenerationSlice';
import pipelineCancellationReducer from '@/store/slices/pipelineCancellationSlice';
import uiReducer from '@/store/slices/toastSlice';

export const store = configureStore({
  reducer: {
    // RTK Query API cache
    [baseApi.reducerPath]: baseApi.reducer,

    // Feature slices
    auth: authReducer,
    tenant: tenantReducer,
    requirement: requirementReducer,
    ingestion: ingestionReducer,
    viewer: viewerReducer,
    selection: selectionReducer,
    projectTasks: projectTasksReducer,
    moduleView: moduleViewReducer,
    userStoryFeedback: userStoryFeedbackReducer,
    moduleFeedback: moduleFeedbackReducer,
    featureFeedback: featureFeedbackReducer,
    jiraIntegration: jiraIntegrationReducer,
    tapIntegration: tapIntegrationReducer,
    storyFeedbackRegeneration: storyFeedbackRegenerationReducer,
    featureFeedbackRegeneration: featureFeedbackRegenerationReducer,
    pipelineCancellation: pipelineCancellationReducer,
    ui: uiReducer,
  },
  middleware: (getDefaultMiddleware) =>
    getDefaultMiddleware().concat(baseApi.middleware),
});

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
