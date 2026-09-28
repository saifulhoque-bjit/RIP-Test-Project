import { baseApi } from '@/services/api/baseApi';
import type { IngestionJob, RetryIngestionPayload } from '@/types';

interface GetIngestionQueueParams {
  projectId: string;
  page?: number;
  pageSize?: number;
}

interface PaginatedIngestionJobs {
  items: IngestionJob[];
  total: number;
  page: number;
  pageSize: number;
}

const ingestionApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    getIngestionQueue: build.query<PaginatedIngestionJobs, GetIngestionQueueParams>({
      query: ({ projectId, page = 1, pageSize = 20 }) => ({
        url: '/ingestion/queue',
        params: { projectId, page, pageSize },
      }),
      providesTags: (result) =>
        result
          ? [
              ...result.items.map(({ id }) => ({
                type: 'IngestionJob' as const,
                id,
              })),
              { type: 'IngestionJob', id: 'LIST' },
            ]
          : [{ type: 'IngestionJob', id: 'LIST' }],
    }),

    getIngestionStatus: build.query<IngestionJob, string>({
      query: (jobId) => `/ingestion/jobs/${jobId}`,
      providesTags: (_result, _error, id) => [{ type: 'IngestionJob', id }],
    }),

    retryIngestion: build.mutation<IngestionJob, RetryIngestionPayload>({
      query: ({ sourceId }) => ({
        url: `/ingestion/retry`,
        method: 'POST',
        body: { sourceId },
      }),
      invalidatesTags: [{ type: 'IngestionJob', id: 'LIST' }],
    }),
  }),
  overrideExisting: false,
});

export const {
  useGetIngestionQueueQuery,
  useGetIngestionStatusQuery,
  useRetryIngestionMutation,
} = ingestionApi;
