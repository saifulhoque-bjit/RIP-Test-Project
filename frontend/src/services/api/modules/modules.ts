import { baseApi } from "@/services/api/baseApi";
import { API_ENDPOINTS } from "@/services/api/endpoints";
import type {
  ModuleDetailsResponse,
  ModulesListResponse,
  RegenerateModulesResponse,
  UpdateModulesStatusResponse,
} from "@/types";

const modulesApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    getModulesList: build.query<ModulesListResponse, string>({
      query: (projectId) => ({
        url: API_ENDPOINTS.MODULES.GET_LIST(projectId),
      }),
      providesTags: (_result, _error, projectId) => [
        { type: "Module", id: `LIST-${projectId}` },
      ],
    }),
    getProjectModuleDetail: build.query<
      ModuleDetailsResponse,
      { projectId: string; moduleId: string }
    >({
      query: ({ projectId, moduleId }) => ({
        url: API_ENDPOINTS.MODULES.GET_DETAIL(projectId, moduleId),
      }),
      // A 404 here is expected right after rejecting an "ADDED" change or
      // accepting a "DELETE_SUGGESTED" one — the module is gone by design,
      // and ModuleDetails already renders its own "not found" state.
      // Suppress the shared error toast so that expected background refetch
      // doesn't surface a spurious global notification.
      extraOptions: { suppressToastFor: [404] },
      keepUnusedDataFor: 0,
      // Also tagged under the project's module LIST id — pipeline/regen
      // tasks invalidate that tag broadly (they don't know which specific
      // module changed), so a currently-open detail view still refetches.
      providesTags: (_result, _error, { projectId, moduleId }) => [
        { type: "Module", id: `DETAIL-${moduleId}` },
        { type: "Module", id: `LIST-${projectId}` },
      ],
    }),
    regenerateModules: build.mutation<
      RegenerateModulesResponse,
      { projectId: string; feedback: string }
    >({
      query: ({ projectId, feedback }) => ({
        url: API_ENDPOINTS.MODULES.REGENERATE(projectId),
        method: "POST",
        body: { feedback },
      }),
      // The regeneration task shows up as its own run on the Pipelines
      // table immediately (not just once the WS reports it terminal).
      invalidatesTags: (_result, _error, { projectId }) => [
        { type: "IngestionJob", id: `LIST-${projectId}` },
        { type: "Pipeline", id: "LIST" },
      ],
    }),
    approveModules: build.mutation<
      UpdateModulesStatusResponse,
      { projectId: string; isIncremental?: boolean; skipProcessing?: boolean }
    >({
      query: ({ projectId, isIncremental, skipProcessing }) => ({
        url: API_ENDPOINTS.MODULES.UPDATE_STATUS(projectId),
        method: "PATCH",
        body: {
          status: "approved",
          skip_processing: skipProcessing ?? false,
          is_incremental: isIncremental ?? false,
        },
      }),
      // NO invalidatesTags — refetch triggered by WS completion via baseApi.util.invalidateTags
    }),
  }),
  overrideExisting: false,
});

export const {
  useGetModulesListQuery,
  useGetProjectModuleDetailQuery,
  useRegenerateModulesMutation,
  useApproveModulesMutation,
} = modulesApi;
export default modulesApi;
