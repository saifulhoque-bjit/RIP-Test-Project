import { baseApi } from "@/services/api/baseApi";
import { API_ENDPOINTS } from "@/services/api/endpoints";
import type { FeatureDetailsResponse } from "@/types";

const featuresApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    getProjectFeatureDetail: build.query<
      FeatureDetailsResponse,
      { projectId: string; moduleId: string; featureId: string }
    >({
      query: ({ projectId, moduleId, featureId }) => ({
        url: API_ENDPOINTS.FEATURES.GET_DETAIL(projectId, moduleId, featureId),
      }),
      // A 404 here is expected right after rejecting an "ADDED" change or
      // accepting a "DELETE_SUGGESTED" one — the feature is gone by design,
      // and FeatureDetails already renders its own "not found" state.
      // Suppress the shared error toast so that expected background refetch
      // doesn't surface a spurious global notification.
      extraOptions: { suppressToastFor: [404] },
      keepUnusedDataFor: 0,
      // Also tagged under the project's module LIST id — pipeline/regen
      // tasks invalidate that tag broadly (they don't know which specific
      // feature changed), so a currently-open detail view still refetches.
      providesTags: (_result, _error, { projectId, featureId }) => [
        { type: "Module", id: `FEATURE-${featureId}` },
        { type: "Module", id: `LIST-${projectId}` },
      ],
    }),
  }),
  overrideExisting: false,
});

export const { useGetProjectFeatureDetailQuery } = featuresApi;
