import { baseApi } from "@/services/api/baseApi";
import { API_ENDPOINTS } from "@/services/api/endpoints";
import type {
  Source,
  SourceUploadResponse,
  SourceListParams,
  SourceListResponse,
  IngestionListParams,
  IngestionListResponse,
} from "@/types";

interface FragmentBboxItem {
  page: number;
  bbox: number[];
}

interface UpdateSourceFragmentBboxPayload {
  sourceId: string;
  fragmentId: string;
  bbox: FragmentBboxItem[];
}

export const sourcesApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    getSources: build.query<SourceListResponse, SourceListParams>({
      query: ({ projectId, skip = 0, limit = 20, status, file_format }) => ({
        url: API_ENDPOINTS.SOURCES.GET_LIST,
        params: {
          project_id: projectId,
          skip,
          limit,
          ...(status && { status }),
          ...(file_format && { file_format }),
        },
      }),
      providesTags: (result) =>
        result?.data?.items
          ? [
              ...result.data.items.map(({ id }) => ({
                type: "Source" as const,
                id,
              })),
              { type: "Source", id: "LIST" },
            ]
          : [{ type: "Source", id: "LIST" }],
    }),

    getSourceById: build.query<Source, string>({
      query: (id) => API_ENDPOINTS.SOURCES.GET_DETAIL(id),
      providesTags: (_result, _error, id) => [{ type: "Source", id }],
    }),

    uploadSources: build.mutation<
      SourceUploadResponse,
      { files: File[]; projectId: string }
    >({
      query: ({ files, projectId }) => {
        const formData = new FormData();
        files.forEach((file) => formData.append("files", file));
        formData.append("project_id", projectId);
        return {
          url: API_ENDPOINTS.SOURCES.UPLOAD,
          method: "POST",
          body: formData,
        };
      },
      invalidatesTags: [{ type: "Source", id: "LIST" }],
    }),

    uploadSourceLink: build.mutation<
      SourceUploadResponse,
      { projectId: string; linkUrl: string }
    >({
      query: ({ projectId, linkUrl }) => ({
        url: API_ENDPOINTS.SOURCES.LINK_UPLOAD,
        method: "POST",
        body: {
          project_id: projectId,
          link_url: linkUrl,
        },
      }),
      invalidatesTags: [{ type: "Source", id: "LIST" }],
    }),

    deleteSource: build.mutation<void, string[]>({
      query: (sourceIds) => ({
        url: API_ENDPOINTS.SOURCES.DELETE_BULK,
        method: "DELETE",
        body: {
          source_ids: sourceIds,
        },
      }),
      invalidatesTags: (_result, _error, sourceIds) => [
        ...sourceIds.map((id) => ({ type: "Source" as const, id })),
        { type: "Source", id: "LIST" },
      ],
    }),

    updateSourceFragmentBbox: build.mutation<
      { success: boolean; message: string },
      UpdateSourceFragmentBboxPayload
    >({
      query: ({ sourceId, fragmentId, bbox }) => ({
        url: API_ENDPOINTS.SOURCES.UPDATE_FRAGMENT_BBOX(sourceId, fragmentId),
        method: "PATCH",
        body: { bbox },
      }),
      invalidatesTags: (_result, _error, { sourceId }) => [
        { type: "Source", id: sourceId },
        { type: "Source", id: "LIST" },
        { type: "Requirement", id: "LIST" },
      ],
    }),

    getIngestionList: build.query<IngestionListResponse, IngestionListParams>({
      query: ({ projectId, skip = 0, limit = 20 }) => ({
        url: API_ENDPOINTS.SOURCES.INGESTION_LIST,
        params: { project_id: projectId, skip, limit },
      }),
      // Project-scoped id so a write in one project doesn't evict another
      // project's cached ingestion list (every arg variant of this query —
      // Sources' limit 100, Review's limit 1 — carries the same tag).
      providesTags: (_result, _error, { projectId }) => [
        { type: "IngestionJob", id: `LIST-${projectId}` },
      ],
    }),
  }),
  overrideExisting: false,
});

export const {
  useGetSourcesQuery,
  useLazyGetSourcesQuery,
  useGetSourceByIdQuery,
  useUploadSourcesMutation,
  useUploadSourceLinkMutation,
  useDeleteSourceMutation,
  useUpdateSourceFragmentBboxMutation,
  useGetIngestionListQuery,
} = sourcesApi;
