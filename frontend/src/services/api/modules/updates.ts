import { baseApi } from "@/services/api/baseApi";
import { API_ENDPOINTS } from "@/services/api/endpoints";
import type { UpdateActionPayload, UpdateActionResponse } from "@/types";

type UpdateActionRequest = UpdateActionPayload & { projectId: string };

const updatesApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    acceptUpdate: build.mutation<UpdateActionResponse, UpdateActionRequest>({
      query: ({ projectId, ...body }) => ({
        url: API_ENDPOINTS.UPDATES.ACCEPT(projectId),
        method: "POST",
        body,
      }),
      invalidatesTags: (_result, _error, { projectId, entity_id, change_type }) => [
        { type: "Project", id: projectId },
        { type: "Requirement", id: `TREE-${projectId}` },
        { type: "Requirement", id: `SYNC-jira-${projectId}` },
        { type: "Requirement", id: `SYNC-tap-${projectId}` },
        { type: "Requirement", id: "LIST" },
        { type: "Module", id: `LIST-${projectId}` },
        // The ingestion that landed this change shows its resolution on the
        // Pipelines table immediately (not just once the WS reports it terminal).
        { type: "IngestionJob", id: `LIST-${projectId}` },
        { type: "Pipeline", id: "LIST" },
        // Accepting a "DELETE_SUGGESTED" change finalizes the entity's
        // removal — it no longer exists, so invalidating its own detail tag
        // would only trigger a doomed refetch against a deleted resource.
        ...(change_type === "DELETE_SUGGESTED"
          ? []
          : [
              { type: "Requirement" as const, id: entity_id },
              { type: "Module" as const, id: `DETAIL-${entity_id}` },
              { type: "Module" as const, id: `FEATURE-${entity_id}` },
            ]),
      ],
    }),
    rejectUpdate: build.mutation<UpdateActionResponse, UpdateActionRequest>({
      query: ({ projectId, ...body }) => ({
        url: API_ENDPOINTS.UPDATES.REJECT(projectId),
        method: "POST",
        body,
      }),
      invalidatesTags: (_result, _error, { projectId, entity_id, change_type }) => [
        { type: "Project", id: projectId },
        { type: "Requirement", id: `TREE-${projectId}` },
        { type: "Requirement", id: `SYNC-jira-${projectId}` },
        { type: "Requirement", id: `SYNC-tap-${projectId}` },
        { type: "Requirement", id: "LIST" },
        { type: "Module", id: `LIST-${projectId}` },
        // The ingestion that landed this change shows its resolution on the
        // Pipelines table immediately (not just once the WS reports it terminal).
        { type: "IngestionJob", id: `LIST-${projectId}` },
        { type: "Pipeline", id: "LIST" },
        // Rejecting an "ADDED" change discards the newly proposed entity —
        // it never really existed, so invalidating its own detail tag would
        // only trigger a doomed refetch against a deleted resource.
        ...(change_type === "ADDED"
          ? []
          : [
              { type: "Requirement" as const, id: entity_id },
              { type: "Module" as const, id: `DETAIL-${entity_id}` },
              { type: "Module" as const, id: `FEATURE-${entity_id}` },
            ]),
      ],
    }),
  }),
  overrideExisting: false,
});

export const { useAcceptUpdateMutation, useRejectUpdateMutation } =
  updatesApi;
export default updatesApi;
