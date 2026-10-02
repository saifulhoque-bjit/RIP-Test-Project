// ── User Role ────────────────────────────────────────────────────────────────
export const USER_ROLE = {
  SUPER_ADMIN: "super_admin",
  CLIENT_ADMIN: "admin",
  MEMBER: "member",
} as const;

export type UserRole = (typeof USER_ROLE)[keyof typeof USER_ROLE];

export const USER_ROLE_LABEL: Record<UserRole, string> = {
  [USER_ROLE.SUPER_ADMIN]: "Super Admin",
  [USER_ROLE.CLIENT_ADMIN]: "Client Admin",
  [USER_ROLE.MEMBER]: "Member",
};

// ── Auth User ────────────────────────────────────────────────────────────────
export interface AuthUser {
  id: string;
  /** Display name — never include raw email unless explicitly needed. */
  displayName: string;
  email: string;
  roles: string[];
  permissions: string[];
}

// ── Login API response ───────────────────────────────────────────────────────
export interface LoginResponseData {
  access_token: string;
  id_token: string;
  refresh_token: string;
  cognito_username: string;
  email: string;
  /** Display name from the authoritative local user record. */
  name?: string | null;
  token_type: string;
  expires_in: number;
  roles: string[];
  permissions: string[];
}

export interface LoginResponse {
  success: boolean;
  message: string;
  data: LoginResponseData;
}

// ── Refresh Token API ─────────────────────────────────────────────────────────
export interface RefreshTokenRequest {
  refresh_token: string;
  cognito_username: string;
}

export interface RefreshTokenResponseData {
  access_token: string;
  id_token?: string;
  /** Cognito only rotates this when refresh-token rotation is enabled. */
  refresh_token?: string;
  token_type?: string;
  expires_in?: number;
}

export interface RefreshTokenResponse {
  success: boolean;
  message: string;
  data: RefreshTokenResponseData;
}
