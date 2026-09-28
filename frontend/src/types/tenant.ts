// ── Tenant (as returned by GET /tenants/) ───────────────────────────────────
export interface Tenant {
  id: string;
  name: string;
  contact_email: string | null;
  address: string | null;
  status: string;
  providers: string[];
  created_at: string;
  updated_at: string;
  total_number_of_project: number;
}

export interface TenantListResponse {
  success: boolean;
  message: string;
  data: {
    items: Tenant[];
    total: number;
    skip: number;
    limit: number;
  };
}

// ── Tenant assigned to the logged-in user (GET /tenants/me) ────────────────
export interface MyTenantResponse {
  success: boolean;
  message: string;
  data: Tenant;
}

// ── Create tenant (POST /tenants/) ──────────────────────────────────────────
export interface CreateTenantRequest {
  name: string;
  contact_email: string;
  address: string;
  status: string;
  providers: string[];
}

export interface CreateTenantResponse {
  success: boolean;
  message: string;
  data: Tenant;
}

// ── Update tenant (PATCH /tenants/{tenant_id}) ──────────────────────────────
// Partial by design — the backend rejects an empty body and forbids unknown
// keys, and `providers` replaces the whole enabled set when sent.
export interface UpdateTenantRequest {
  name?: string;
  contact_email?: string;
  address?: string;
  status?: string;
  providers?: string[];
}

export interface UpdateTenantResponse {
  success: boolean;
  message: string;
  data: Tenant;
}

// ── Activate / deactivate tenant (PATCH /tenants/{tenant_id}) — soft status
// toggle, returns no data ───────────────────────────────────────────────────
export interface UpdateTenantStatusRequest {
  status: "active" | "inactive";
}

export interface UpdateTenantStatusResponse {
  success: boolean;
  message: string;
}

// ── Tenant stats (GET /tenants/stats) — super admin only ───────────────────
export interface TenantStats {
  total_tenants: number;
  active_tenants: number;
  pending_invitation_client_admin: number;
  deactivated_tenants: number;
  total_projects: number;
}

export interface TenantStatsResponse {
  success: boolean;
  message: string;
  data: TenantStats;
}

// ── Invite user to a tenant (POST /tenants/{tenant_id}/invitations) ────────
export interface InviteUserRequest {
  email: string;
  name: string;
  /** Omitted when inviting a admin. */
  role?: string;
}

export interface InvitedUser {
  id: string;
  email: string;
  name: string;
  role: string;
  tenant_id: string;
}

export interface InviteUserResponse {
  success: boolean;
  message: string;
  data: InvitedUser;
}

// ── List tenant invitations (GET /tenants/{tenant_id}/invitations) ─────────
export interface TenantInvitation {
  id: string;
  tenant_id: string;
  email: string;
  status: string;
  expires_at: string;
  created_at: string;
}

export interface TenantInvitationListResponse {
  success: boolean;
  message: string;
  data: {
    items: TenantInvitation[];
    total: number;
    skip: number;
    limit: number;
  };
}

// ── Resend / revoke a tenant invitation ─────────────────────────────────────
export interface TenantInvitationActionResponse {
  success: boolean;
  message: string;
  data: TenantInvitation;
}

// ── LLM provider config per tenant ──────────────────────────────────────────
export interface TenantLlmProvider {
  id: string;
  provider: string;
  is_active: boolean;
  has_api_key: boolean;
  api_key_hint: string | null;
  is_verified: boolean;
  last_tested_at: string | null;
  last_test_error: string | null;
}

export interface TenantLlmProvidersListResponse {
  success: boolean;
  message: string;
  data: { items: TenantLlmProvider[] };
}

export interface UpdateTenantLlmProviderPayload {
  tenantId: string;
  provider: string;
  api_key?: string;
  is_active?: boolean;
}

export interface UpdateTenantLlmProviderResponse {
  success: boolean;
  message: string;
  data: TenantLlmProvider;
}

export interface TenantLlmProviderTestResult {
  provider: string;
  verified: boolean;
  tested_at: string;
  error?: string | null;
}

export interface TestTenantLlmProviderResponse {
  success: boolean;
  message: string;
  data: TenantLlmProviderTestResult;
}
