// Module: Authentication API
// Handles cookie-backed authentication endpoints.
import { baseApi } from "@/services/api/baseApi";
import { API_ENDPOINTS } from "@/services/api/endpoints";
import type {
  LoginResponse,
  RefreshTokenRequest,
  RefreshTokenResponse,
} from "@/types";

// ── Request / Response shapes ─────────────────────────────────────────────────

export interface LoginRequest {
  email: string;
  password: string;
}

export interface ForgotPasswordRequest {
  email: string;
}

export interface ForgotPasswordResponse {
  success: boolean;
  message: string;
  data: {
    email: string;
    message: string;
  };
}

export interface ResetPasswordRequest {
  email: string;
  code: string;
  new_password: string;
}

export interface ResetPasswordResponse {
  success: boolean;
  message: string;
  data: {
    email: string;
    message: string;
  };
}

// ── Auth API module ───────────────────────────────────────────────────────────

export const authApi = baseApi.injectEndpoints({
  endpoints: (builder) => ({
    /**
     * POST /auth/login
     * Authenticates the user and establishes an HttpOnly cookie-backed session.
     */
    login: builder.mutation<LoginResponse, LoginRequest>({
      query: (credentials) => ({
        url: API_ENDPOINTS.AUTH.LOGIN,
        method: "POST",
        body: credentials,
      }),
    }),

    logout: builder.mutation<void, void>({
      query: () => ({
        url: API_ENDPOINTS.AUTH.LOGOUT,
        method: "POST",
      }),
      extraOptions: { suppressToastFor: [401] },
    }),

    /**
     * POST /auth/refresh
     * Exchanges a refresh token for a new access token. `baseApi`'s
     * `baseQueryWithReauth` calls this endpoint directly (bypassing this hook,
     * to avoid a circular import) whenever a request comes back 401; this
     * mutation is exposed for any call site that needs to trigger it manually.
     */
    refresh: builder.mutation<RefreshTokenResponse, RefreshTokenRequest>({
      query: (body) => ({
        url: API_ENDPOINTS.AUTH.REFRESH,
        method: "POST",
        body,
      }),
      extraOptions: { suppressToastFor: [401] },
    }),

    /**
     * POST /auth/forgot-password
     * Requests a password-reset code by email. Always returns a generic
     * success response, whether or not an account exists for the email.
     */
    forgotPassword: builder.mutation<
      ForgotPasswordResponse,
      ForgotPasswordRequest
    >({
      query: (body) => ({
        url: API_ENDPOINTS.AUTH.FORGOT_PASSWORD,
        method: "POST",
        body,
      }),
    }),

    /**
     * POST /auth/reset-password
     * Sets a new password using the code emailed by forgot-password.
     */
    resetPassword: builder.mutation<
      ResetPasswordResponse,
      ResetPasswordRequest
    >({
      query: (body) => ({
        url: API_ENDPOINTS.AUTH.RESET_PASSWORD,
        method: "POST",
        body,
      }),
    }),
  }),
  overrideExisting: false,
});

export const {
  useLoginMutation,
  useLogoutMutation,
  useRefreshMutation,
  useForgotPasswordMutation,
  useResetPasswordMutation,
} = authApi;
