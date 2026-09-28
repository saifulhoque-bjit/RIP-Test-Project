import { baseApi } from "@/services/api/baseApi";
import { API_ENDPOINTS } from "@/services/api/endpoints";
import type {
  CanonicalRequirement,
  RequirementStatus,
  StatusTransitionPayload,
  BulkApprovePayload,
  BulkStatusPayload,
  // RequirementListParams,
  // RequirementListResponse,
  RequirementSummaryResponse,
  RegenerateModulesResponse,
  RegenerateByFeedbackItem,
  RegenerateByFeedbackResponse,
  RegenerateForSourceCodeFeedbackItem,
  RequirementDetailResponse,
  RequirementSource,
  UserStoryTreeResponse,
} from "@/types";

interface UpdateRequirementBboxesPayload {
  projectId: string;
  requirementId: string;
  sources: RequirementSource[];
}

interface DeleteUserStoryPayload {
  projectId: string;
  userStoryId: string;
  /** Required by the backend when deleting an already-approved user story. */
  reason?: string;
}

interface GetRequirementsParams {
  projectId: string;
  status?: RequirementStatus;
  page?: number;
  pageSize?: number;
  sortBy?: string;
  sortOrder?: "asc" | "desc";
}

interface PaginatedRequirements {
  items: CanonicalRequirement[];
  total: number;
  page: number;
  pageSize: number;
}

type UpdateRequirementStatusPayload = StatusTransitionPayload & {
  projectId?: string;
};

const requirementsApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    getRequirements: build.query<PaginatedRequirements, GetRequirementsParams>({
      query: ({
        projectId,
        status,
        page = 1,
        pageSize = 10,
        sortBy,
        sortOrder,
      }) => ({
        url: "/requirements",
        params: {
          projectId,
          ...(status && { status }),
          page,
          pageSize,
          ...(sortBy && { sortBy }),
          ...(sortOrder && { sortOrder }),
        },
      }),
      providesTags: (result) =>
        result
          ? [
              ...result.items.map(({ id }) => ({
                type: "Requirement" as const,
                id,
              })),
              { type: "Requirement", id: "LIST" },
            ]
          : [{ type: "Requirement", id: "LIST" }],
    }),

    getRequirementById: build.query<CanonicalRequirement, string>({
      query: (id) => `/requirements/${id}`,
      providesTags: (_result, _error, id) => [{ type: "Requirement", id }],
    }),

    updateRequirementStatus: build.mutation<
      CanonicalRequirement,
      UpdateRequirementStatusPayload
    >({
      query: ({ requirementId, targetStatus, rationale }) => ({
        url: `/requirements/${requirementId}/status`,
        method: "PATCH",
        body: { status: targetStatus, ...(rationale && { rationale }) },
      }),
      invalidatesTags: (result, _error, { requirementId, projectId }) => {
        const tags: Array<{ type: "Requirement"; id: string }> = [
          { type: "Requirement", id: requirementId },
          { type: "Requirement", id: "LIST" },
        ];
        // Use projectId from payload, fallback to result
        const effectiveProjectId = projectId || result?.projectId;
        if (effectiveProjectId) {
          tags.push({
            type: "Requirement",
            id: `SUMMARY-${effectiveProjectId}`,
          });
        }
        return tags;
      },
    }),

    /** Bulk approve — only applies to IN_REVIEW requirements. Requires rationale. */
    bulkApproveRequirements: build.mutation<
      { approvedCount: number; projectId?: string },
      BulkApprovePayload & { projectId?: string }
    >({
      query: ({ requirementIds, rationale }) => ({
        url: "/requirements/bulk-approve",
        method: "POST",
        body: { requirementIds, rationale },
      }),
      invalidatesTags: (_result, _error, { projectId }) => [
        { type: "Requirement", id: "LIST" },
        ...(projectId
          ? [{ type: "Requirement" as const, id: `SUMMARY-${projectId}` }]
          : []),
      ],
    }),

    getRequirementsSummary: build.query<RequirementSummaryResponse, string>({
      query: (projectId) => ({
        url: API_ENDPOINTS.REQUIREMENTS.GET_SUMMARY(projectId),
      }),
      providesTags: (_result, _error, projectId) => [
        { type: "Requirement", id: `SUMMARY-${projectId}` },
      ],
    }),

    getProjectRequirementDetail: build.query<
      RequirementDetailResponse,
      { projectId: string; requirementId: string }
    >({
      query: ({ projectId, requirementId }) => ({
        url: API_ENDPOINTS.REQUIREMENTS.GET_DETAIL_BY_PROJECT(
          projectId,
          requirementId,
        ),
      }),
      // A 404 here is expected right after rejecting an "ADDED" change or
      // accepting a "DELETE_SUGGESTED" one — the story is gone by design, and
      // UserStoryDetails already renders its own "not found" state. Suppress
      // the shared error toast so that expected background refetch doesn't
      // surface a spurious global notification.
      extraOptions: { suppressToastFor: [404] },
      providesTags: (_result, _error, { requirementId }) => [
        { type: "Requirement", id: requirementId },
      ],
    }),

    bulkStatusRequirements: build.mutation<
      { message: string },
      BulkStatusPayload
    >({
      query: ({ projectId, user_story_ids, status }) => ({
        url: API_ENDPOINTS.REQUIREMENTS.BULK_STATUS(projectId),
        method: "PATCH",
        body: { user_story_ids, status },
      }),
      invalidatesTags: (_result, _error, { projectId, user_story_ids }) => [
        { type: "Requirement", id: "LIST" },
        { type: "Requirement", id: `SUMMARY-${projectId}` },
        { type: "Requirement", id: `TREE-${projectId}` },
        { type: "Requirement", id: `SYNC-jira-${projectId}` },
        { type: "Requirement", id: `SYNC-tap-${projectId}` },
        // approved_user_stories (and the Jira/TAP pending-sync counts derived
        // from it) lives on the project detail record, not the requirement —
        // must invalidate it too or the SubHeader badges go stale.
        { type: "Project", id: projectId },
        // The Dashboard's portfolio-wide Approved/Pending-sync KPI cards
        // aggregate the same counts across every project — stale otherwise.
        { type: "Dashboard", id: "STATS" },
        ...user_story_ids.map((id) => ({
          type: "Requirement" as const,
          id,
        })),
      ],
    }),

    deleteUserStoryById: build.mutation<
      { message?: string },
      DeleteUserStoryPayload
    >({
      query: ({ projectId, userStoryId, reason }) => ({
        url: API_ENDPOINTS.REQUIREMENTS.DELETE_BY_PROJECT(
          projectId,
          userStoryId,
        ),
        method: "DELETE",
        body: reason ? { reason } : undefined,
      }),
      // invalidatesTags: (_result, _error, { projectId, userStoryId }) => [
        // { type: "Requirement", id: userStoryId },
        invalidatesTags: (_result, _error, { projectId }) => [
        { type: "Requirement", id: "LIST" },
        { type: "Requirement", id: `SUMMARY-${projectId}` },
        { type: "Requirement", id: `TREE-${projectId}` },
      ],
    }),

    regenerateRequirements: build.mutation<
      RegenerateModulesResponse,
      { projectId: string; feedback: string }
    >({
      query: ({ projectId, feedback }) => ({
        url: API_ENDPOINTS.REQUIREMENTS.REGENERATE(projectId),
        method: "POST",
        body: { feedback },
      }),
    }),

    regenerateUserStoriesByFeedback: build.mutation<
      RegenerateByFeedbackResponse,
      { projectId: string; feedbackItems: RegenerateByFeedbackItem[] }
    >({
      query: ({ projectId, feedbackItems }) => ({
        url: API_ENDPOINTS.REQUIREMENTS.REGENERATE_BY_FEEDBACK(projectId),
        method: "POST",
        body: feedbackItems,
      }),
      // The regeneration task shows up as its own run on the Pipelines
      // table immediately (not just once the WS reports it terminal).
      invalidatesTags: (_result, _error, { projectId, feedbackItems }) => [
        { type: "Requirement", id: "LIST" },
        { type: "Requirement", id: `SUMMARY-${projectId}` },
        { type: "Requirement", id: `TREE-${projectId}` },
        { type: "IngestionJob", id: `LIST-${projectId}` },
        { type: "Pipeline", id: "LIST" },
        ...feedbackItems.map((item) => ({
          type: "Requirement" as const,
          id: item.user_story_id,
        })),
      ],
    }),

    // source_code only — covers feature-level AND user-story-level (overall +
    // specific) feedback in one heterogeneous array. See
    // RegenerateForSourceCodeFeedbackItem for the per-item shape.
    regenerateForSourceCodeFeedback: build.mutation<
      RegenerateByFeedbackResponse,
      {
        projectId: string;
        feedbackItems: RegenerateForSourceCodeFeedbackItem[];
        skipProcessing?: boolean;
      }
    >({
      query: ({ projectId, feedbackItems, skipProcessing }) => ({
        url: API_ENDPOINTS.REQUIREMENTS.REGENERATE_FOR_SOURCE_CODE(projectId),
        method: "POST",
        body: {
          feedback_items: feedbackItems,
          skip_processing: skipProcessing ?? false,
        },
      }),
      // The regeneration task shows up as its own run on the Pipelines
      // table immediately (not just once the WS reports it terminal).
      invalidatesTags: (_result, _error, { projectId }) => [
        { type: "IngestionJob", id: `LIST-${projectId}` },
        { type: "Pipeline", id: "LIST" },
      ],
    }),

    // Get requirements as tree structure (same endpoint, returns hierarchical data)
    getProjectRequirementsTree: build.query<
      UserStoryTreeResponse,
      { project_id: string; module_id?: string }
    >({
      query: ({ project_id, module_id }) => ({
        url: API_ENDPOINTS.REQUIREMENTS.GET_BY_PROJECT(project_id),
        params: {
          skip: 0,
          limit: 100,
          ...(module_id && { module_id }),
        },
      }),
      keepUnusedDataFor: 0,
      providesTags: (_result, _error, { project_id }) => [
        { type: "Requirement", id: `TREE-${project_id}` },
      ],
    }),

    updateRequirementBboxes: build.mutation<
      { success: boolean; message: string },
      UpdateRequirementBboxesPayload
    >({
      query: ({ projectId, requirementId, sources }) => ({
        url: API_ENDPOINTS.REQUIREMENTS.UPDATE_BBOXES(projectId, requirementId),
        method: "PATCH",
        body: { sources },
      }),
      invalidatesTags: (_result, _error, { requirementId, projectId }) => [
        { type: "Requirement", id: requirementId },
        { type: "Requirement", id: `TREE-${projectId}` },
        { type: "Requirement", id: "LIST" },
      ],
    }),
  }),
  overrideExisting: false,
});

export const {
  useGetRequirementsQuery,
  useGetRequirementByIdQuery,
  useUpdateRequirementStatusMutation,
  useBulkApproveRequirementsMutation,
  useGetRequirementsSummaryQuery,
  useRegenerateRequirementsMutation,
  useRegenerateUserStoriesByFeedbackMutation,
  useRegenerateForSourceCodeFeedbackMutation,
  useGetProjectRequirementsTreeQuery,
  useGetProjectRequirementDetailQuery,
  useBulkStatusRequirementsMutation,
  useDeleteUserStoryByIdMutation,
  useUpdateRequirementBboxesMutation,
} = requirementsApi;
