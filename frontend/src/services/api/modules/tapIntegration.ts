import { baseApi } from '@/services/api/baseApi';
import { API_ENDPOINTS } from '@/services/api/endpoints';


// ── Per-project TAP integration config (persisted — GET/POST/PATCH) ─────────

export interface TapIntegrationConfig {
  id: string;
  project_id: string;
  base_url: string;
  /** Fixed for the product ("RIP") — echoed for display, never configurable. */
  app_client_name: string;
  /** Masked — the real key is never returned by the API. */
  api_key_hint: string;
  client_id: string;
  is_active: boolean;
  last_verified_at: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * No `app_client_name`: it is fixed for the product, so the server supplies
 * it. The API rejects unknown fields, so sending one would fail the request.
 */
export interface CreateTapIntegrationPayload {
  base_url: string;
  api_key: string;
  client_id: string;
}

/** All fields optional — only send what changed. */
export type UpdateTapIntegrationPayload = Partial<CreateTapIntegrationPayload>;

interface TapIntegrationApiResponse {
  success: boolean;
  message: string;
  data: TapIntegrationConfig;
}

// Credential verification is not a separate call: create/update verify
// against TAP server-side and only persist once TAP accepts them.

// ─────────────────────────────────────────────────────────────────────────────

const tapIntegrationApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    /**
     * Fetch this project's saved TAP integration config, if any.
     * A 404 is expected/normal (project has no TAP config yet) — the shared
     * error-toast is suppressed for it via extraOptions.
     */
    getProjectTapIntegration: build.query<TapIntegrationConfig, string>({
      query: (projectId) => API_ENDPOINTS.TAP.PROJECT_INTEGRATION(projectId),
      extraOptions: { suppressToastFor: [404] },
      transformResponse: (response: TapIntegrationApiResponse) => response.data,
      providesTags: (_result, _error, projectId) => [
        { type: 'TapIntegration', id: projectId },
      ],
    }),

    /** Create this project's TAP integration config. */
    createProjectTapIntegration: build.mutation<
      TapIntegrationConfig,
      { projectId: string; body: CreateTapIntegrationPayload }
    >({
      query: ({ projectId, body }) => ({
        url: API_ENDPOINTS.TAP.PROJECT_INTEGRATION(projectId),
        method: 'POST',
        body,
      }),
      // A rejected connection is expected user-facing flow, not an app error:
      // TapSection already turns it into a message naming the four fields to
      // check. Without this the shared handler raises a second, rawer toast
      // for the same failure.
      extraOptions: { suppressToastFor: [400, 401, 403, 404, 409, 422, 500, 503] },
      transformResponse: (response: TapIntegrationApiResponse) => response.data,
      invalidatesTags: (_result, _error, { projectId }) => [
        { type: 'TapIntegration', id: projectId },
      ],
    }),

    /** Update this project's existing TAP integration config (partial). */
    updateProjectTapIntegration: build.mutation<
      TapIntegrationConfig,
      { projectId: string; body: UpdateTapIntegrationPayload }
    >({
      query: ({ projectId, body }) => ({
        url: API_ENDPOINTS.TAP.PROJECT_INTEGRATION(projectId),
        method: 'PATCH',
        body,
      }),
      // Same as create — TapSection owns the message for a rejected save.
      extraOptions: { suppressToastFor: [400, 401, 403, 404, 409, 422, 500, 503] },
      transformResponse: (response: TapIntegrationApiResponse) => response.data,
      invalidatesTags: (_result, _error, { projectId }) => [
        { type: 'TapIntegration', id: projectId },
      ],
    }),

  }),
  overrideExisting: false,
});

export const {
  useGetProjectTapIntegrationQuery,
  useCreateProjectTapIntegrationMutation,
  useUpdateProjectTapIntegrationMutation,
} = tapIntegrationApi;
