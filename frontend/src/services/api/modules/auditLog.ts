import { baseApi } from '@/services/api/baseApi';

/** A single audit log entry. */
interface AuditLogEntry {
  id: string;
  action: string;
  /** The entity type affected (e.g., "Requirement", "Source"). */
  entityType: string;
  entityId: string;
  /** Display name of the actor — PII is masked/truncated before rendering. */
  actorName: string;
  /** Brief description of what changed. */
  summary: string;
  occurredAt: string;
}

interface GetAuditLogParams {
  projectId: string;
  /** ISO 8601 start date. */
  fromDate?: string;
  /** ISO 8601 end date. */
  toDate?: string;
  actorId?: string;
  actionType?: string;
  page?: number;
  pageSize?: number;
}

interface PaginatedAuditLog {
  items: AuditLogEntry[];
  total: number;
  page: number;
  pageSize: number;
}

const auditLogApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    getAuditLog: build.query<PaginatedAuditLog, GetAuditLogParams>({
      query: ({
        projectId,
        fromDate,
        toDate,
        actorId,
        actionType,
        page = 1,
        pageSize = 20,
      }) => ({
        url: '/audit-log',
        params: {
          projectId,
          ...(fromDate && { fromDate }),
          ...(toDate && { toDate }),
          ...(actorId && { actorId }),
          ...(actionType && { actionType }),
          page,
          pageSize,
        },
      }),
      providesTags: [{ type: 'AuditLog', id: 'LIST' }],
    }),
  }),
  overrideExisting: false,
});

export const { useGetAuditLogQuery } = auditLogApi;
