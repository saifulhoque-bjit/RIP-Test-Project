import { baseApi } from '@/services/api/baseApi';
import { API_ENDPOINTS } from "@/services/api/endpoints";
import type {
  PipelinesResponse,
} from "@/types";


const pipelineApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    getPipelines: build.query<PipelinesResponse, void>({
      query: () => API_ENDPOINTS.PIPELINES.GET_LIST,
      providesTags: [{ type: "Pipeline", id: "LIST" }],
    }),
  }),
  overrideExisting: false,
});

export const {
  useGetPipelinesQuery,
} = pipelineApi;