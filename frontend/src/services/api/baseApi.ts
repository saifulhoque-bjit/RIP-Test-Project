import {
  createApi,
  fetchBaseQuery,
  type BaseQueryApi,
  type BaseQueryFn,
  type FetchArgs,
  type FetchBaseQueryError,
} from "@reduxjs/toolkit/query/react";
import { toast } from "@/lib/toast";
import {
  setAccessToken,
  setUnauthenticated,
} from "@/store/slices/authSlice";
import { API_ENDPOINTS } from "@/services/api/endpoints";
import type { RefreshTokenResponse } from "@/types";

/**
 * Error response structure from the API
 */
interface ApiErrorResponse {
  message?: string;
  error?: string;
  detail?: string;
  [key: string]: unknown;
}

/**
 * Raw fetchBaseQuery configured for cookie-based authentication.
 * Browsers attach HttpOnly JWT cookies automatically when credentials are included.
 */
const rawBaseQuery = fetchBaseQuery({
  baseUrl: import.meta.env.VITE_API_BASE_URL ?? "/api",
  credentials: "include",
});

/**
 * Extracts error message from API error response.
 */
const getErrorMessage = (error: FetchBaseQueryError): string => {
  if ("data" in error && error.data) {
    const data = error.data as ApiErrorResponse;
    return data?.message || data?.error || data?.detail || "An error occurred";
  }
  return "An error occurred";
};

/**
 * Per-endpoint options an injected query/mutation can pass via its
 * `extraOptions` field to opt out of the shared error-toast behavior below
 * (e.g. a "does this exist yet?" GET where a 404 is an expected, normal state).
 */
export interface RipBaseQueryExtraOptions {
  suppressToastFor?: (number | "FETCH_ERROR" | "TIMEOUT_ERROR")[];
}

/** Auth endpoints must never trigger a refresh-and-retry cycle on their own 401s. */
const NO_REFRESH_URLS: string[] = [
  API_ENDPOINTS.AUTH.LOGIN,
  API_ENDPOINTS.AUTH.REFRESH,
  API_ENDPOINTS.AUTH.LOGOUT,
];

function isNoRefreshEndpoint(args: string | FetchArgs): boolean {
  const url = typeof args === "string" ? args : args.url;
  return NO_REFRESH_URLS.includes(url);
}

/**
 * Ensures concurrent 401s from several in-flight requests only trigger a
 * single POST /auth/refresh call; every caller awaits the same promise.
 */
let refreshPromise: Promise<boolean> | null = null;

/**
 * Calls POST /auth/refresh with the stored refresh_token/cognito_username
 * and, on success, dispatches the new access token into the auth slice.
 * Returns whether the refresh succeeded.
 */
async function refreshAccessToken(api: BaseQueryApi): Promise<boolean> {
  const state = api.getState() as {
    auth: { refreshToken: string | null; cognitoUsername: string | null };
  };
  const { refreshToken, cognitoUsername } = state.auth;

  if (!refreshToken || !cognitoUsername) {
    return false;
  }

  const result = await rawBaseQuery(
    {
      url: API_ENDPOINTS.AUTH.REFRESH,
      method: "POST",
      body: { refresh_token: refreshToken, cognito_username: cognitoUsername },
    },
    api,
    {},
  );

  if (result.error) {
    return false;
  }

  const data = (result.data as RefreshTokenResponse | undefined)?.data;
  if (!data?.access_token) {
    return false;
  }

  api.dispatch(
    setAccessToken({
      accessToken: data.access_token,
      refreshToken: data.refresh_token,
    }),
  );

  return true;
}

/**
 * Refreshes the access token and retries once on a 401, and clears client
 * auth state when the refresh itself fails (session truly expired).
 * Shows a toast notification for network failures and server errors.
 */
const baseQueryWithReauth: BaseQueryFn<
  string | FetchArgs,
  unknown,
  FetchBaseQueryError,
  RipBaseQueryExtraOptions
> = async (args, api, extraOptions) => {
  let result = await rawBaseQuery(args, api, extraOptions);

  if (
    result.error?.status === 401 &&
    !isNoRefreshEndpoint(args)
  ) {
    refreshPromise ??= refreshAccessToken(api).finally(() => {
      refreshPromise = null;
    });
    const refreshed = await refreshPromise;

    if (refreshed) {
      result = await rawBaseQuery(args, api, extraOptions);
    }
  }

  if (result.error) {
    const { status } = result.error;

    if (
      (typeof status === "number" ||
        status === "FETCH_ERROR" ||
        status === "TIMEOUT_ERROR") &&
      extraOptions?.suppressToastFor?.includes(status)
    ) {
      return result;
    }

    // fetchBaseQuery reports a cancelled request (component unmounted, query
    // args changed, or RTK Query dropped it for a superseding one) the same
    // way it reports a real connectivity failure: status "FETCH_ERROR". An
    // aborted request isn't a network problem the user needs to hear about —
    // check the signal RTK Query gave this call before treating it as one.
    if (status === "FETCH_ERROR" && api.signal.aborted) {
      return result;
    }

    const errorMessage = getErrorMessage(result.error);

    // Handle network and timeout errors
    if (status === "FETCH_ERROR") {
      toast.error("Cannot connect to the server. Please try again.", {
        toastId: "network-error",
      });
      return result;
    }

    if (status === "TIMEOUT_ERROR") {
      toast.error("Request timed out. Please try again.", {
        toastId: "timeout-error",
      });
      return result;
    }

    // Handle HTTP status codes
    if (typeof status === "number") {
      switch (status) {
        case 400:
          // Bad Request - usually validation errors
          toast.error(
            errorMessage || "Invalid request. Please check your input.",
            {
              toastId: "bad-request",
            },
          );
          break;

        case 401:
          // Unauthorized - clear auth state
          api.dispatch(setUnauthenticated());
          toast.error(
            errorMessage || "Authentication failed. Please login again.",
            {
              toastId: "unauthorized",
            },
          );
          break;

        case 403:
          // Forbidden - user doesn't have permission
          toast.error(
            errorMessage ||
              "You do not have permission to perform this action.",
            {
              toastId: "forbidden",
            },
          );
          break;

        case 404:
          // Not Found
          toast.error(errorMessage || "The requested resource was not found.", {
            toastId: "not-found",
          });
          break;

        case 409:
          // Conflict - usually duplicate resources
          toast.error(
            errorMessage ||
              "A conflict occurred. The resource may already exist.",
            {
              toastId: "conflict",
            },
          );
          break;

        case 422:
          // Unprocessable Entity - validation error
          toast.error(
            errorMessage || "Validation failed. Please check your input.",
            {
              toastId: "validation-error",
            },
          );
          break;

        case 429:
          // Too Many Requests - rate limiting
          toast.warning("Too many requests. Please slow down and try again.", {
            toastId: "rate-limit",
          });
          break;

        case 500:
          // Internal Server Error
          toast.error("Internal server error. Please try again later.", {
            toastId: "server-error-500",
          });
          break;

        case 502:
          // Bad Gateway
          toast.error(
            "Service temporarily unavailable. Please try again later.",
            {
              toastId: "bad-gateway",
            },
          );
          break;

        case 503:
          // Service Unavailable
          toast.error(
            "Service is currently unavailable. Please try again later.",
            {
              toastId: "service-unavailable",
            },
          );
          break;

        case 504:
          // Gateway Timeout
          toast.error("Request timed out. Please try again.", {
            toastId: "gateway-timeout",
          });
          break;

        default:
          // Handle other server errors (5xx)
          if (status >= 500) {
            toast.error(
              errorMessage || "Server error. Please try again later.",
              {
                toastId: `server-error-${status}`,
              },
            );
          } else if (status >= 400) {
            // Handle other client errors (4xx)
            toast.error(
              errorMessage || "An error occurred. Please try again.",
              {
                toastId: `client-error-${status}`,
              },
            );
          }
          break;
      }
    }
  }

  return result;
};

/**
 * RTK Query base API.
 * All API modules must inject endpoints into this instance via baseApi.injectEndpoints().
 */
export const baseApi = createApi({
  reducerPath: "api",
  baseQuery: baseQueryWithReauth,
  tagTypes: [
    "Project",
    "Source",
    "IngestionJob",
    "Module",
    "Requirement",
    "Fragment",
    "DuplicateCandidate",
    "Version",
    "Grounding",
    "Integration",
    "TapIntegration",
    "AuditLog",
    "ActivityLog",
    "Dashboard",
    "Settings",
    "Enum",
    "Pipeline",
    "Notification",
    "User",
    "Tenant",
    "Invitation",
    "LlmProvider",
  ],
  endpoints: () => ({}),
});

export default baseApi;
