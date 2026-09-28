import { baseApi } from "@/services/api/baseApi";
import { API_ENDPOINTS } from "@/services/api/endpoints";

export const tasksApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    // projectId is not part of the request — it's only here to scope the
    // invalidated ingestion-list tag to the project the task belongs to.
    cancelTask: build.mutation<void, { taskId: string; projectId: string }>({
      query: ({ taskId }) => ({
        url: API_ENDPOINTS.TASKS.CANCEL(taskId),
        method: "DELETE",
      }),
      invalidatesTags: (_result, _error, { projectId }) => [
        { type: "IngestionJob", id: `LIST-${projectId}` },
      ],
    }),
  }),
  overrideExisting: false,
});

export const { useCancelTaskMutation } = tasksApi;
