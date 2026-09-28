import { baseApi } from '@/services/api/baseApi';
import type { CanonicalRequirement, Fragment } from '@/types';

/** A pair of Fragment + Canonical Requirement flagged as potential duplicates. */
interface DuplicateCandidate {
  id: string;
  fragment: Fragment;
  canonicalRequirement: CanonicalRequirement;
  /** Similarity score from AI deduplication (0–1). */
  similarityScore: number;
  createdAt: string;
}

interface GetDuplicateCandidatesParams {
  projectId: string;
  page?: number;
  pageSize?: number;
}

interface PaginatedDuplicates {
  items: DuplicateCandidate[];
  total: number;
  page: number;
  pageSize: number;
}

interface MergeRequirementsPayload {
  duplicateCandidateId: string;
  /** Fragment to merge into the existing canonical requirement. */
  fragmentId: string;
  canonicalRequirementId: string;
  rationale: string;
}

interface CreateNewCanonicalPayload {
  duplicateCandidateId: string;
  fragmentId: string;
  /** Optional title override; if omitted, extracted from the fragment. */
  title?: string;
  description?: string;
}

const deduplicationApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    getDuplicateCandidates: build.query<PaginatedDuplicates, GetDuplicateCandidatesParams>(
      {
        query: ({ projectId, page = 1, pageSize = 20 }) => ({
          url: '/deduplication/candidates',
          params: { projectId, page, pageSize },
        }),
        providesTags: (result) =>
          result
            ? [
                ...result.items.map(({ id }) => ({
                  type: 'DuplicateCandidate' as const,
                  id,
                })),
                { type: 'DuplicateCandidate', id: 'LIST' },
              ]
            : [{ type: 'DuplicateCandidate', id: 'LIST' }],
      },
    ),

    /** Merge a fragment into an existing canonical requirement. */
    mergeRequirements: build.mutation<CanonicalRequirement, MergeRequirementsPayload>({
      query: ({ duplicateCandidateId, fragmentId, canonicalRequirementId, rationale }) => ({
        url: `/deduplication/merge`,
        method: 'POST',
        body: { duplicateCandidateId, fragmentId, canonicalRequirementId, rationale },
      }),
      invalidatesTags: [
        { type: 'DuplicateCandidate', id: 'LIST' },
        { type: 'Requirement', id: 'LIST' },
      ],
    }),

    /** Create a new canonical requirement from a fragment (keep both). */
    createNewCanonical: build.mutation<
      CanonicalRequirement,
      CreateNewCanonicalPayload
    >({
      query: ({ duplicateCandidateId, fragmentId, title, description }) => ({
        url: `/deduplication/create-canonical`,
        method: 'POST',
        body: { duplicateCandidateId, fragmentId, ...(title && { title }), ...(description && { description }) },
      }),
      invalidatesTags: [
        { type: 'DuplicateCandidate', id: 'LIST' },
        { type: 'Requirement', id: 'LIST' },
      ],
    }),
  }),
  overrideExisting: false,
});

export const {
  useGetDuplicateCandidatesQuery,
  useMergeRequirementsMutation,
  useCreateNewCanonicalMutation,
} = deduplicationApi;
