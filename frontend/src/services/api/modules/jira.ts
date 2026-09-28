import { baseApi } from '@/services/api/baseApi';
import { API_ENDPOINTS } from '@/services/api/endpoints';

// ── Project Jira integration config (persisted — GET/POST/PATCH) ────────────

export interface JiraIntegrationConfig {
  id: string;
  project_id: string;
  jira_base_url: string;
  jira_project_key: string;
  jira_board_id: string | null;
  jira_user_email: string;
  /** Masked — the real token is never returned by the API. */
  api_token_hint: string;
  issue_type_name: string;
  epic_issue_type_name: string;
  traceability_field_ids: Record<string, unknown> | null;
  deprecated_transition_id: string | null;
  is_active: boolean;
  last_synced_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreateJiraIntegrationPayload {
  jira_base_url: string;
  jira_project_key: string;
  jira_board_id?: string;
  jira_user_email: string;
  api_token: string;
  issue_type_name?: string;
  epic_issue_type_name?: string;
  deprecated_transition_id?: string;
}

/** All fields optional — only send what changed. */
export type UpdateJiraIntegrationPayload = Partial<CreateJiraIntegrationPayload>;

interface JiraIntegrationApiResponse {
  success: boolean;
  message: string;
  data: JiraIntegrationConfig;
}

const jiraApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    /**
     * Fetch this project's saved Jira integration config, if any.
     * A 404 is expected/normal (project has no Jira config yet) — the shared
     * error-toast is suppressed for it via extraOptions; callers should check
     * `error?.status === 404` to distinguish "not configured" from a real failure.
     */
    getProjectJiraIntegration: build.query<JiraIntegrationConfig, string>({
      query: (projectId) => API_ENDPOINTS.JIRA.PROJECT_INTEGRATION(projectId),
      extraOptions: { suppressToastFor: [404] },
      transformResponse: (response: JiraIntegrationApiResponse) =>
        response.data,
      providesTags: (_result, _error, projectId) => [
        { type: 'Integration', id: projectId },
      ],
    }),

    /** Create this project's Jira integration config (fails 409 if one is already active). */
    createProjectJiraIntegration: build.mutation<
      JiraIntegrationConfig,
      { projectId: string; body: CreateJiraIntegrationPayload }
    >({
      query: ({ projectId, body }) => ({
        url: API_ENDPOINTS.JIRA.PROJECT_INTEGRATION(projectId),
        method: 'POST',
        body,
      }),
      transformResponse: (response: JiraIntegrationApiResponse) =>
        response.data,
      invalidatesTags: (_result, _error, { projectId }) => [
        { type: 'Integration', id: projectId },
      ],
    }),

    /** Update this project's existing Jira integration config (partial). */
    updateProjectJiraIntegration: build.mutation<
      JiraIntegrationConfig,
      { projectId: string; body: UpdateJiraIntegrationPayload }
    >({
      query: ({ projectId, body }) => ({
        url: API_ENDPOINTS.JIRA.PROJECT_INTEGRATION(projectId),
        method: 'PATCH',
        body,
      }),
      transformResponse: (response: JiraIntegrationApiResponse) =>
        response.data,
      invalidatesTags: (_result, _error, { projectId }) => [
        { type: 'Integration', id: projectId },
      ],
    }),
  }),
  overrideExisting: false,
});

export const {
  useGetProjectJiraIntegrationQuery,
  useCreateProjectJiraIntegrationMutation,
  useUpdateProjectJiraIntegrationMutation,
} = jiraApi;
