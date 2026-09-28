import { baseApi } from '@/services/api/baseApi';
import type { Fragment } from '@/types';

interface GetFragmentsParams {
  requirementId: string;
  /** Include noise-archived fragments when true. */
  includeNoise?: boolean;
}

interface ManualLinkPayload {
  fragmentId: string;
  canonicalRequirementId: string;
}

const fragmentsApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    getFragmentsByRequirement: build.query<Fragment[], GetFragmentsParams>({
      query: ({ requirementId, includeNoise = false }) => ({
        url: `/requirements/${requirementId}/fragments`,
        params: { includeNoise },
      }),
      providesTags: (_result, _error, { requirementId }) => [
        { type: 'Fragment', id: requirementId },
      ],
    }),

    /** Promote a noise-archived fragment back to a candidate. */
    promoteFragment: build.mutation<Fragment, string>({
      query: (fragmentId) => ({
        url: `/fragments/${fragmentId}/promote`,
        method: 'PATCH',
      }),
      invalidatesTags: (_result, _error, fragmentId) => [
        { type: 'Fragment', id: fragmentId },
      ],
    }),

    /** Manually link an unlinked fragment to an existing canonical requirement. */
    manualLinkFragment: build.mutation<Fragment, ManualLinkPayload>({
      query: ({ fragmentId, canonicalRequirementId }) => ({
        url: `/fragments/${fragmentId}/link`,
        method: 'PATCH',
        body: { canonicalRequirementId },
      }),
      invalidatesTags: (_result, _error, { fragmentId }) => [
        { type: 'Fragment', id: fragmentId },
        { type: 'Requirement', id: 'LIST' },
      ],
    }),
  }),
  overrideExisting: false,
});

export const {
  useGetFragmentsByRequirementQuery,
  usePromoteFragmentMutation,
  useManualLinkFragmentMutation,
} = fragmentsApi;
