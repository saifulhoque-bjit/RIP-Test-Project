import { baseApi } from '@/services/api/baseApi';
import { API_ENDPOINTS } from '@/services/api/endpoints';
import type { JiraSyncModule } from './jiraSync';

// ── Stage/notify result ──────────────────────────────────────────────────────
// TAP is fire-and-forget: this is the "staged + notified" result, not a final
// sync result — `is_tap_synced` only flips later, server-side, once TAP's
// async /ack callback lands. There are no created/updated/deprecated *items*
// to report yet at this point, just staging counts.

export interface TapSyncExecuteResult {
  sync_id: string;
  pull_url: string | null;
  created: number;
  updated: number;
  skipped: number;
  total_synced: number;
  message: string;
  /**
   * Non-empty when the notify step itself failed (e.g. TAP rejected the
   * project, was unreachable, etc.). Staging still succeeds and the HTTP
   * call still returns 2xx in this case — the failure is only visible here,
   * not as a thrown error, so callers must check this before treating the
   * response as a clean success.
   */
  errors: { stage?: string; error?: string }[];
}

interface TapSyncExecuteApiResponse {
  success: boolean;
  message: string;
  data: TapSyncExecuteResult;
}

const tapSyncApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    /**
     * Stage the module→feature→story hierarchy for TAP and notify it to pull.
     * Only `{ modules }` goes in the body — same convention as
     * `executeJiraSync` (backend schema is `extra="forbid"`, project_id is
     * already part of the URL).
     *
     * Deliberately does NOT invalidate the `Requirement:TREE-{projectId}` or
     * `Integration` cache tags the way Jira's sync does — a successful call
     * here only means TAP was *notified*, not that any story is actually
     * synced yet. `is_tap_synced` flips server-side once TAP's async `/ack`
     * callback lands, and the tray picks that up next time it's opened.
     */
    executeTapSync: build.mutation<
      TapSyncExecuteResult,
      { projectId: string; modules: JiraSyncModule[] }
    >({
      query: ({ projectId, modules }) => ({
        url: API_ENDPOINTS.TAP.SYNC_EXECUTE(projectId),

        method: 'POST',
        body: { modules },
      }),
      // SyncTray's own catch block already shows a context-aware toast (via
      // getErrorMessage, which reads the same backend message) — suppress
      // the generic baseApi toast here so a failure doesn't double-toast.
      // 401 stays un-suppressed since that status also dispatches a global
      // sign-out side effect that must still run.
      extraOptions: {
        suppressToastFor: [400, 403, 404, 409, 422, 429, 500, 502, 503, 504],
      },
      transformResponse: (response: TapSyncExecuteApiResponse) => response.data,
    }),
  }),
  overrideExisting: false,
});

export const { useExecuteTapSyncMutation } = tapSyncApi;
