import { baseApi } from "@/services/api/baseApi";
import { API_ENDPOINTS } from "@/services/api/endpoints";
import type {
  TenantLlmProvidersListResponse,
  UpdateTenantLlmProviderPayload,
  UpdateTenantLlmProviderResponse,
  TestTenantLlmProviderResponse,
} from "@/types";

const llmProvidersApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    getTenantLlmProviders: build.query<TenantLlmProvidersListResponse, string>({
      query: (tenantId) => API_ENDPOINTS.TENANTS.LLM_PROVIDERS_LIST(tenantId),
      providesTags: (_result, _error, tenantId) => [
        { type: "LlmProvider", id: tenantId },
      ],
    }),

    updateTenantLlmProvider: build.mutation<
      UpdateTenantLlmProviderResponse,
      UpdateTenantLlmProviderPayload
    >({
      query: ({ tenantId, provider, ...body }) => ({
        url: API_ENDPOINTS.TENANTS.LLM_PROVIDER_UPDATE(tenantId, provider),
        method: "PATCH",
        body,
      }),
      // Activating/deactivating a provider here also changes the tenant's
      // `providers` allow-list on the backend — invalidate the Tenant cache
      // too so pages relying on `useActiveTenant()` (e.g. the New Project
      // dropdown) pick up the change without a full app reload.
      invalidatesTags: (_result, _error, { tenantId }) => [
        { type: "LlmProvider", id: tenantId },
        { type: "Tenant", id: "LIST" },
        { type: "Tenant", id: "ME" },
      ],
    }),

    testTenantLlmProvider: build.mutation<
      TestTenantLlmProviderResponse,
      { tenantId: string; provider: string }
    >({
      query: ({ tenantId, provider }) => ({
        url: API_ENDPOINTS.TENANTS.LLM_PROVIDER_TEST(tenantId, provider),
        method: "POST",
      }),
      invalidatesTags: (_result, _error, { tenantId }) => [
        { type: "LlmProvider", id: tenantId },
      ],
    }),
  }),
  overrideExisting: false,
});

export const {
  useGetTenantLlmProvidersQuery,
  useLazyGetTenantLlmProvidersQuery,
  useUpdateTenantLlmProviderMutation,
  useTestTenantLlmProviderMutation,
} = llmProvidersApi;
export default llmProvidersApi;
