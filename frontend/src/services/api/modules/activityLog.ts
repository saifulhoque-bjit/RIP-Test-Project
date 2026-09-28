import { baseApi } from "@/services/api/baseApi";
import { API_ENDPOINTS } from "@/services/api/endpoints";
import type { ActivityLogListResponse } from "@/types";

const activityLogApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    getActivityLogs: build.query<
      ActivityLogListResponse,
      { projectId: string; skip?: number; limit?: number }
    >({
      query: ({ projectId, skip = 0, limit = 20 }) => ({
        url: API_ENDPOINTS.ACTIVITY_LOGS.GET_LIST(projectId),
        params: { skip, limit },
      }),
      providesTags: (_result, _error, { projectId }) => [
        { type: "ActivityLog", id: `LIST-${projectId}` },
      ],
    }),
  }),
  overrideExisting: false,
});

export const { useGetActivityLogsQuery } = activityLogApi;
export default activityLogApi;
