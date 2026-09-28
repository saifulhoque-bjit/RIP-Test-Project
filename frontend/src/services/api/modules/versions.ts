import { baseApi } from '@/services/api/baseApi';
import type { RequirementVersion } from '@/types';

interface CreateNewVersionPayload {
  requirementId: string;
  /** Updated content for the new version. */
  title: string;
  description: string;
  /** Mandatory — UI must enforce non-empty before dispatching. */
  changeRationale: string;
}

interface RevertVersionPayload {
  requirementId: string;
  targetVersionId: string;
  /** Mandatory rationale for the revert. */
  changeRationale: string;
}

const versionsApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    getVersionHistory: build.query<RequirementVersion[], string>({
      query: (requirementId) => `/requirements/${requirementId}/versions`,
      providesTags: (_result, _error, requirementId) => [
        { type: 'Version', id: requirementId },
      ],
    }),

    /** Creates a new version (N+1) — never overwrites an existing version. */
    createNewVersion: build.mutation<RequirementVersion, CreateNewVersionPayload>({
      query: ({ requirementId, title, description, changeRationale }) => ({
        url: `/requirements/${requirementId}/versions`,
        method: 'POST',
        body: { title, description, changeRationale },
      }),
      invalidatesTags: (_result, _error, { requirementId }) => [
        { type: 'Version', id: requirementId },
        { type: 'Requirement', id: requirementId },
      ],
    }),

    /**
     * Reverts to a previous version by creating a new version with the same content.
     * The IS_CURRENT flag is updated server-side only — never toggled from the UI.
     */
    revertVersion: build.mutation<RequirementVersion, RevertVersionPayload>({
      query: ({ requirementId, targetVersionId, changeRationale }) => ({
        url: `/requirements/${requirementId}/versions/revert`,
        method: 'POST',
        body: { targetVersionId, changeRationale },
      }),
      invalidatesTags: (_result, _error, { requirementId }) => [
        { type: 'Version', id: requirementId },
        { type: 'Requirement', id: requirementId },
      ],
    }),
  }),
  overrideExisting: false,
});

export const {
  useGetVersionHistoryQuery,
  useCreateNewVersionMutation,
  useRevertVersionMutation,
} = versionsApi;
