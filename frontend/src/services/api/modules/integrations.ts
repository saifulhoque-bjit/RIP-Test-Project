import { baseApi } from '@/services/api/baseApi';

/** Health status for a single downstream integration (Jira, TAP, etc.). */
interface IntegrationHealth {
  system: 'JIRA' | 'TAP';
  /** Whether a recent heartbeat was received. */
  isHealthy: boolean;
  lastSeenAt: string | null;
  /** Human-readable status (e.g., "Connected", "Unreachable"). */
  statusMessage: string;
}

/** A single event in the sync event log. */
interface SyncEvent {
  id: string;
  system: 'JIRA' | 'TAP';
  action: string;
  status: 'SUCCESS' | 'FAILURE';
  details: string;
  occurredAt: string;
}

interface GetSyncEventLogParams {
  system?: 'JIRA' | 'TAP';
  page?: number;
  pageSize?: number;
}

interface PaginatedSyncEvents {
  items: SyncEvent[];
  total: number;
  page: number;
  pageSize: number;
}

const integrationsApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    getIntegrationHealth: build.query<IntegrationHealth[], void>({
      query: () => '/integrations/health',
      providesTags: [{ type: 'Integration', id: 'HEALTH' }],
    }),

    getSyncEventLog: build.query<PaginatedSyncEvents, GetSyncEventLogParams>({
      query: ({ system, page = 1, pageSize = 20 }) => ({
        url: '/integrations/events',
        params: { ...(system && { system }), page, pageSize },
      }),
      providesTags: [{ type: 'Integration', id: 'EVENTS' }],
    }),
  }),
  overrideExisting: false,
});

export const { useGetIntegrationHealthQuery, useGetSyncEventLogQuery } =
  integrationsApi;
