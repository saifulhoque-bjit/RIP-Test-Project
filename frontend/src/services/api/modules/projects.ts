import { baseApi } from "@/services/api/baseApi";
import { API_ENDPOINTS } from "@/services/api/endpoints";
import type {
  CreateProjectRequest,
  ProjectDetailResponse,
  ProjectListParams,
  ProjectListResponse,
  ProjectNameListResponse,
  UpdateProjectRequest,
} from "@/types";

const projectsApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    getProjects: build.query<ProjectListResponse, ProjectListParams>({
      query: (params) => ({
        url: API_ENDPOINTS.PROJECTS.GET_LIST,
        params: {
          skip: params.skip ?? 0,
          limit: params.limit ?? 20,
          ...(params.search ? { search: params.search } : {}),
          ...(params.tenant_id ? { tenant_id: params.tenant_id } : {}),
        },
      }),
      providesTags: (result) =>
        result?.data?.items
          ? [
              ...result.data.items.map(({ id }) => ({
                type: "Project" as const,
                id,
              })),
              { type: "Project", id: "LIST" },
            ]
          : [{ type: "Project", id: "LIST" }],
    }),

    // Admin/super_admin only — every project on the given tenant.
    getAllProjects: build.query<ProjectListResponse, ProjectListParams>({
      query: (params) => ({
        url: API_ENDPOINTS.PROJECTS.GET_ALL,
        params: {
          skip: params.skip ?? 0,
          limit: params.limit ?? 20,
          ...(params.search ? { search: params.search } : {}),
          ...(params.tenant_id ? { tenant_id: params.tenant_id } : {}),
        },
      }),
      providesTags: (result) =>
        result?.data?.items
          ? [
              ...result.data.items.map(({ id }) => ({
                type: "Project" as const,
                id,
              })),
              { type: "Project", id: "LIST" },
            ]
          : [{ type: "Project", id: "LIST" }],
    }),

    // Picker feed — the whole tenant's projects as id/name, unpaginated.
    getProjectNameList: build.query<
      ProjectNameListResponse,
      { tenant_id?: string } | void
    >({
      query: (params) => ({
        url: API_ENDPOINTS.PROJECTS.GET_NAME_LIST,
        params: params?.tenant_id ? { tenant_id: params.tenant_id } : undefined,
      }),
      providesTags: (result) =>
        result?.data
          ? [
              ...result.data.map(({ id }) => ({
                type: "Project" as const,
                id,
              })),
              { type: "Project", id: "LIST" },
            ]
          : [{ type: "Project", id: "LIST" }],
    }),

    getProject: build.query<ProjectDetailResponse, string>({
      query: (projectId) => API_ENDPOINTS.PROJECTS.GET_DETAIL(projectId),
      providesTags: (_result, _error, id) => [{ type: "Project", id }],
    }),

    createProject: build.mutation<ProjectDetailResponse, CreateProjectRequest>({
      query: (body) => ({
        url: API_ENDPOINTS.PROJECTS.CREATE,
        method: "POST",
        body,
      }),
      invalidatesTags: [
        { type: "Project", id: "LIST" },
        // A new project changes the owning tenant's project count, which
        // feeds both Clients tables and the super admin's Dashboard KPI, and
        // the portfolio-wide "My projects" count on everyone else's Dashboard.
        { type: "Tenant", id: "LIST" },
        { type: "Tenant", id: "ME" },
        { type: "Tenant", id: "STATS" },
        { type: "Dashboard", id: "STATS" },
      ],
      // ProjectList's own catch block already shows a context-aware toast
      // (via getErrorMessage, which reads the same backend message) —
      // suppress the generic baseApi toast here so a failure doesn't
      // double-toast. 401 stays un-suppressed since that status also
      // dispatches a global sign-out side effect that must still run.
      extraOptions: {
        suppressToastFor: [400, 403, 404, 409, 422, 429, 500, 502, 503, 504],
      },
    }),

    updateProject: build.mutation<
      ProjectDetailResponse,
      { projectId: string; body: UpdateProjectRequest }
    >({
      query: ({ projectId, body }) => ({
        url: API_ENDPOINTS.PROJECTS.UPDATE(projectId),
        method: "PATCH",
        body,
      }),
      invalidatesTags: (_result, _error, { projectId }) => [
        { type: "Project", id: projectId },
        { type: "Project", id: "LIST" },
      ],
      // IdentitySection's own catch block already shows a context-aware
      // toast (via getErrorMessage) — suppress the generic baseApi toast
      // here so a failure doesn't double-toast. 401 stays un-suppressed
      // since that status also dispatches a global sign-out side effect.
      extraOptions: {
        suppressToastFor: [400, 403, 404, 409, 422, 429, 500, 502, 503, 504],
      },
    }),
  }),
});

export const {
  useGetProjectsQuery,
  useGetAllProjectsQuery,
  useGetProjectNameListQuery,
  useGetProjectQuery,
  useCreateProjectMutation,
  useUpdateProjectMutation,
} = projectsApi;
export default projectsApi;
