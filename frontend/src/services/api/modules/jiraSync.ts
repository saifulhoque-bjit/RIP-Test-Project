import { baseApi } from "@/services/api/baseApi";
import { API_ENDPOINTS } from "@/services/api/endpoints";
import type {
  RequirementDetailResponse,
  SyncCandidatesResponse,
  UserStoryTreeResponse,
} from "@/types";

// ── Shared "approved" logic ─────────────────────────────────────────────────
// Same normalization the Review tab uses to decide what counts as "approved"
// (statuses like "Approved", "approved_locked", etc. all count). Shared here
// so the sync tray and the header's approved-count badge never drift apart.

export const isApprovedTreeStatus = (status?: string): boolean => {
  const normalized = (status ?? "").trim().toLowerCase().replace(/\s+/g, "_");
  return normalized.includes("approved") || normalized.includes("locked");
};

// ── Jira sync payload shape (module → feature → story hierarchy) ───────────

export interface JiraSyncAcceptanceCriteria {
  type: string;
  given: string;
  when: string;
  then: string;
}

export interface JiraSyncNfr {
  category: string;
  requirement: string;
  description: string;
}

export interface JiraSyncUserStory {
  user_story_id: string;
  user_story_code: string;
  title: string;
  as_a: string;
  i_want_to: string;
  so_that: string;
  story_points: number;
  acceptance_criteria: JiraSyncAcceptanceCriteria[];
  nfrs: JiraSyncNfr[];
}

export interface JiraSyncFeature {
  feature_id: string;
  feature_code: string;
  feature_name: string;
  feature_description: string;
  user_stories: JiraSyncUserStory[];
}

export interface JiraSyncModule {
  module_id: string;
  module_code: string;
  module_name: string;
  module_description: string;
  features: JiraSyncFeature[];
}

export interface JiraSyncPayload {
  project_id: string;
  project_name: string;
  modules: JiraSyncModule[];
}

// ── Sync execute result ──────────────────────────────────────────────────────

export interface JiraSyncResultItem {
  rip_entity_id: string;
  rip_entity_type: string;
  rip_entity_code: string;
  title: string;
  jira_key: string | null;
  jira_id: string | null;
  status: string;
}

export interface JiraSyncExecuteResult {
  created: number;
  updated: number;
  deprecated: number;
  skipped: number;
  total_synced: number;
  message: string;
  created_items: JiraSyncResultItem[];
  updated_items: JiraSyncResultItem[];
  deprecated_items: JiraSyncResultItem[];
  errors: Record<string, unknown>[];
  sync_completed_at: string | null;
}

interface JiraSyncExecuteApiResponse {
  success: boolean;
  message: string;
  data: JiraSyncExecuteResult;
}

const jiraSyncApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    /**
     * Module → feature → story tree for a project, used to populate the Jira
     * sync tray (filtered to APPROVED stories client-side). Mirrors exactly
     * how the Review/Requirements tab fetches this data (same URL, same
     * params, same response shape) — the backend's `status` query param on
     * the flat list endpoint isn't reliable, so this avoids it entirely.
     */
    getSyncRequirementsTree: build.query<UserStoryTreeResponse, string>({
      query: (projectId) => ({
        url: API_ENDPOINTS.REQUIREMENTS.GET_BY_PROJECT(projectId),
        params: { skip: 0, limit: 100 },
      }),
      providesTags: (_result, _error, projectId) => [
        { type: "Requirement", id: `TREE-${projectId}` },
      ],
    }),

    /**
     * Approved, not-yet-synced-to-`target` module→feature→story tree —
     * backs the Sync Tray directly, pre-filtered server-side. Replaces the
     * old pattern of fetching the full tree via `getSyncRequirementsTree`
     * and filtering client-side with `isApprovedTreeStatus`/`is_jira_synced`/
     * `is_tap_synced`.
     */
    getSyncCandidates: build.query<
      SyncCandidatesResponse,
      { projectId: string; target: "jira" | "tap" }
    >({
      query: ({ projectId, target }) => ({
        url: API_ENDPOINTS.REQUIREMENTS.SYNC_CANDIDATES(projectId),
        params: { sync_target: target },
      }),
      providesTags: (_result, _error, { projectId, target }) => [
        { type: "Requirement", id: `SYNC-${target}-${projectId}` },
      ],
    }),

    /**
     * Full detail for a single story (as_a/i_want_to/so_that/acceptance
     * criteria/story_points) — the tree above doesn't carry these. Reuses
     * the same GET_DETAIL_BY_PROJECT route already verified by the Review
     * tab's story detail panel. Fetched on-demand (lazy) per approved story
     * when the Jira sync payload is assembled.
     */
    getSyncStoryDetail: build.query<
      RequirementDetailResponse,
      { projectId: string; requirementId: string }
    >({
      query: ({ projectId, requirementId }) => ({
        url: API_ENDPOINTS.REQUIREMENTS.GET_DETAIL_BY_PROJECT(
          projectId,
          requirementId,
        ),
        params: { include_deleted: true },
      }),
    }),

    /**
     * Execute the real Jira sync — pushes the assembled module→feature→story
     * hierarchy. Only `{ modules }` goes in the body: the backend schema has
     * `extra="forbid"` and project_id is already part of the URL, so any
     * other keys (e.g. project_name) must be stripped by the caller first.
     */
    executeJiraSync: build.mutation<
      JiraSyncExecuteResult,
      { projectId: string; modules: JiraSyncModule[] }
    >({
      query: ({ projectId, modules }) => ({
        url: API_ENDPOINTS.JIRA.SYNC_EXECUTE(projectId),
        method: "POST",
        body: { modules },
      }),
      // A gateway error on this long request doesn't mean the sync failed —
      // SyncTray waits for the backend's completion notification instead.
      extraOptions: { suppressToastFor: [502, 503, 504] },
      transformResponse: (response: JiraSyncExecuteApiResponse) =>
        response.data,
      // Also invalidate the tree — a successful sync flips is_jira_synced on
      // the synced stories, so the header badge and the tray's own list (both
      // read via getSyncRequirementsTree) need to refetch to drop them out.
      // Same flip is reflected portfolio-wide in the Dashboard's pending-sync
      // (JIRA) KPI, which reads it via Dashboard STATS.
      invalidatesTags: (_result, _error, { projectId }) => [
        { type: "Integration", id: projectId },
        { type: "Requirement", id: `TREE-${projectId}` },
        { type: "Requirement", id: `SYNC-jira-${projectId}` },
        { type: "Dashboard", id: "STATS" },
      ],
    }),
  }),
  overrideExisting: false,
});

export const {
  useGetSyncRequirementsTreeQuery,
  useGetSyncCandidatesQuery,
  useLazyGetSyncStoryDetailQuery,
  useExecuteJiraSyncMutation,
} = jiraSyncApi;
