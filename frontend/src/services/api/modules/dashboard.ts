import { baseApi } from '@/services/api/baseApi';
import { API_ENDPOINTS } from "@/services/api/endpoints";
import type {
  DashboardStatsResponse,
} from "@/types";

/** Authority & Coverage dashboard metrics. */
interface AuthorityDashboard {
  projectId: string;
  /** % of requirements with status APPROVED. */
  approvalCoveragePercent: number;
  /** % of APPROVED requirements synced to Jira. */
  jiraCoveragePercent: number;
  /** % of APPROVED requirements linked to a TAP test case. */
  testCoveragePercent: number;
  /** Count of requirements by status. */
  statusBreakdown: Record<string, number>;
  /** Total canonical requirements in the project. */
  totalRequirements: number;
  /** Count of open conflicts. */
  openConflicts: number;
  /** Project readiness score 0–100. */
  readinessScore: number;
  lastCalculatedAt: string;
}

const dashboardApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    getAuthorityDashboard: build.query<AuthorityDashboard, string>({
      query: (projectId) => ({
        url: '/dashboard/authority',
        params: { projectId },
      }),
      providesTags: [{ type: 'Dashboard', id: 'AUTHORITY' }],
    }),

    getDashboardStats: build.query<DashboardStatsResponse, void>({
      query: () => API_ENDPOINTS.DASHBOARD.GET_STATS,
      providesTags: [{ type: "Dashboard", id: "STATS" }],
    }),

  }),
  overrideExisting: false,
});

export const {
  useGetAuthorityDashboardQuery,
  useGetDashboardStatsQuery,
} = dashboardApi;