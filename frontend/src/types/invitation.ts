// ── Invitation details (as returned by GET /invitations/{token}) ───────────
export interface InvitationDetails {
  email: string;
  tenant_name: string;
  expires_at: string;
}

export interface GetInvitationResponse {
  success: boolean;
  message: string;
  data: InvitationDetails;
}

// ── Accept invitation (POST /invitations/{token}/accept) ───────────────────
export interface AcceptInvitationRequest {
  password: string;
  confirm_password: string;
}

export interface AcceptInvitationResponse {
  success: boolean;
  message: string;
  data: InvitationDetails;
}
