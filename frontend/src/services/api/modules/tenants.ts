import { baseApi } from "@/services/api/baseApi";
import { API_ENDPOINTS } from "@/services/api/endpoints";
import type {
  CreateTenantRequest,
  CreateTenantResponse,
  UpdateTenantRequest,
  UpdateTenantResponse,
  UpdateTenantStatusRequest,
  UpdateTenantStatusResponse,
  InviteUserRequest,
  InviteUserResponse,
  MyTenantResponse,
  TenantInvitationActionResponse,
  TenantInvitationListResponse,
  TenantListResponse,
  TenantStatsResponse,
} from "@/types";

const tenantsApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    // Super admin only — every tenant on the platform, used for the tenant switcher.
    getTenants: build.query<TenantListResponse, void>({
      query: () => API_ENDPOINTS.TENANTS.GET_LIST,
      providesTags: [{ type: "Tenant", id: "LIST" }],
    }),

    // Non-super-admin roles — the single tenant the logged-in user belongs to.
    getMyTenant: build.query<MyTenantResponse, void>({
      query: () => API_ENDPOINTS.TENANTS.GET_ME,
      providesTags: [{ type: "Tenant", id: "ME" }],
    }),

    // Super admin only — client counts by status plus total projects, used by
    // the super admin's Dashboard stat cards in place of project-scoped stats.
    getTenantStats: build.query<TenantStatsResponse, void>({
      query: () => API_ENDPOINTS.TENANTS.GET_STATS,
      providesTags: [{ type: "Tenant", id: "STATS" }],
    }),

    createTenant: build.mutation<CreateTenantResponse, CreateTenantRequest>({
      query: (body) => ({
        url: API_ENDPOINTS.TENANTS.CREATE,
        method: "POST",
        body,
      }),
      invalidatesTags: [
        { type: "Tenant", id: "LIST" },
        { type: "Tenant", id: "STATS" },
      ],
    }),

    updateTenant: build.mutation<
      UpdateTenantResponse,
      { tenantId: string; body: UpdateTenantRequest }
    >({
      query: ({ tenantId, body }) => ({
        url: API_ENDPOINTS.TENANTS.UPDATE(tenantId),
        method: "PATCH",
        body,
      }),
      invalidatesTags: [
        { type: "Tenant", id: "LIST" },
        { type: "Tenant", id: "ME" },
        { type: "Tenant", id: "STATS" },
      ],
    }),

    // Soft-toggles the tenant's active status; the row stays in the list with
    // a changed status rather than disappearing either direction.
    updateTenantStatus: build.mutation<
      UpdateTenantStatusResponse,
      { tenantId: string } & UpdateTenantStatusRequest
    >({
      query: ({ tenantId, status }) => ({
        url: API_ENDPOINTS.TENANTS.UPDATE_STATUS(tenantId),
        method: "PATCH",
        body: { status },
      }),
      invalidatesTags: [
        { type: "Tenant", id: "LIST" },
        { type: "Tenant", id: "ME" },
        { type: "Tenant", id: "STATS" },
      ],
    }),

    inviteUser: build.mutation<
      InviteUserResponse,
      { tenantId: string; body: InviteUserRequest }
    >({
      query: ({ tenantId, body }) => ({
        url: API_ENDPOINTS.TENANTS.INVITE_USER(tenantId),
        method: "POST",
        body,
      }),
      // Inviting a client's first admin is what takes a brand-new tenant out
      // of "pending_invitation" — its status chip and the super admin's
      // Active/Pending KPI counts read stale otherwise.
      invalidatesTags: (_result, _error, { tenantId }) => [
        { type: "User", id: "LIST" },
        { type: "Invitation", id: tenantId },
        { type: "Tenant", id: "LIST" },
        { type: "Tenant", id: "ME" },
        { type: "Tenant", id: "STATS" },
      ],
    }),

    getTenantInvitations: build.query<
      TenantInvitationListResponse,
      { tenantId: string; statusFilter?: string }
    >({
      query: ({ tenantId, statusFilter }) => ({
        url: API_ENDPOINTS.TENANTS.GET_INVITATIONS(tenantId),
        params: statusFilter ? { status_filter: statusFilter } : undefined,
      }),
      providesTags: (_result, _error, { tenantId }) => [
        { type: "Invitation", id: tenantId },
      ],
    }),

    resendInvitation: build.mutation<
      TenantInvitationActionResponse,
      { tenantId: string; invitationId: string }
    >({
      query: ({ tenantId, invitationId }) => ({
        url: API_ENDPOINTS.TENANTS.RESEND_INVITATION(tenantId, invitationId),
        method: "POST",
      }),
      invalidatesTags: (_result, _error, { tenantId }) => [
        { type: "Invitation", id: tenantId },
      ],
    }),

    revokeInvitation: build.mutation<
      TenantInvitationActionResponse,
      { tenantId: string; invitationId: string }
    >({
      query: ({ tenantId, invitationId }) => ({
        url: API_ENDPOINTS.TENANTS.REVOKE_INVITATION(tenantId, invitationId),
        method: "POST",
      }),
      invalidatesTags: (_result, _error, { tenantId }) => [
        { type: "Invitation", id: tenantId },
      ],
    }),
  }),
  overrideExisting: false,
});

export const {
  useGetTenantsQuery,
  useGetMyTenantQuery,
  useGetTenantStatsQuery,
  useCreateTenantMutation,
  useUpdateTenantMutation,
  useUpdateTenantStatusMutation,
  useInviteUserMutation,
  useGetTenantInvitationsQuery,
  useResendInvitationMutation,
  useRevokeInvitationMutation,
} = tenantsApi;
export default tenantsApi;
