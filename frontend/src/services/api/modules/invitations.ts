// Module: Invitations API
// Handles the public invitation-acceptance flow (no auth cookie required).
import { baseApi } from "@/services/api/baseApi";
import { API_ENDPOINTS } from "@/services/api/endpoints";
import type {
  AcceptInvitationRequest,
  AcceptInvitationResponse,
  GetInvitationResponse,
} from "@/types";

export const invitationsApi = baseApi.injectEndpoints({
  endpoints: (builder) => ({
    /**
     * GET /invitations/{token}
     * Resolves an invitation token to the invited email and tenant.
     */
    getInvitationDetails: builder.query<GetInvitationResponse, string>({
      query: (token) => API_ENDPOINTS.INVITATIONS.GET_DETAIL(token),
      extraOptions: { suppressToastFor: [400, 404, 410] },
    }),

    /**
     * POST /invitations/{token}/accept
     * Sets the invited user's password and activates their account.
     */
    acceptInvitation: builder.mutation<
      AcceptInvitationResponse,
      { token: string; body: AcceptInvitationRequest }
    >({
      query: ({ token, body }) => ({
        url: API_ENDPOINTS.INVITATIONS.ACCEPT(token),
        method: "POST",
        body,
      }),
      extraOptions: { suppressToastFor: [400, 404, 410, 422] },
    }),
  }),
  overrideExisting: false,
});

export const {
  useGetInvitationDetailsQuery,
  useAcceptInvitationMutation,
} = invitationsApi;
export default invitationsApi;
