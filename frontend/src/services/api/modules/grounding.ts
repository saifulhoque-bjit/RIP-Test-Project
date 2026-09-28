import { baseApi } from '@/services/api/baseApi';
import type { GroundingCoordinates } from '@/types';

interface GroundingRecord {
  id: string;
  fragmentId: string;
  canonicalRequirementId: string;
  sourceId: string;
  coordinates: GroundingCoordinates;
  /** True once a human reviewer has confirmed the grounding is correct. */
  isConfirmed: boolean;
  createdAt: string;
}

interface UpdateGroundingPayload {
  groundingId: string;
  coordinates: GroundingCoordinates;
}

const groundingApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    getGroundingCoordinates: build.query<GroundingRecord[], string>({
      query: (requirementId) => `/requirements/${requirementId}/grounding`,
      providesTags: (_result, _error, requirementId) => [
        { type: 'Grounding', id: requirementId },
      ],
    }),

    /** Update grounding coordinates — requires an explicit user action; never inline. */
    updateGrounding: build.mutation<GroundingRecord, UpdateGroundingPayload>({
      query: ({ groundingId, coordinates }) => ({
        url: `/grounding/${groundingId}`,
        method: 'PATCH',
        body: { coordinates },
      }),
      invalidatesTags: (_result, _error, { groundingId }) => [
        { type: 'Grounding', id: groundingId },
      ],
    }),

    /** Mark grounding as human-confirmed. */
    confirmGrounding: build.mutation<GroundingRecord, string>({
      query: (groundingId) => ({
        url: `/grounding/${groundingId}/confirm`,
        method: 'POST',
      }),
      invalidatesTags: (_result, _error, groundingId) => [
        { type: 'Grounding', id: groundingId },
      ],
    }),
  }),
  overrideExisting: false,
});

export const {
  useGetGroundingCoordinatesQuery,
  useUpdateGroundingMutation,
  useConfirmGroundingMutation,
} = groundingApi;
