import { createSlice, type PayloadAction } from "@reduxjs/toolkit";
import type { AuthUser } from "@/types";

interface AuthState {
  user: AuthUser | null;
  isAuthenticated: boolean;
  /** True while auth state is being restored / validated on app load. */
  isLoading: boolean;
  /** JWT access token — kept in sessionStorage (survives reload, cleared on tab close). */
  accessToken: string | null;
  /** Refresh token used to silently mint a new access token on 401 — sessionStorage only. */
  refreshToken: string | null;
  /** Cognito username required alongside refresh_token by POST /auth/refresh. */
  cognitoUsername: string | null;
}

const SESSION_TOKEN_KEY = "rip_access_token";
const SESSION_REFRESH_TOKEN_KEY = "rip_refresh_token";
const SESSION_COGNITO_USERNAME_KEY = "rip_cognito_username";

function loadAccessToken(): string | null {
  try {
    return sessionStorage.getItem(SESSION_TOKEN_KEY);
  } catch {
    return null;
  }
}

function loadRefreshToken(): string | null {
  try {
    return sessionStorage.getItem(SESSION_REFRESH_TOKEN_KEY);
  } catch {
    return null;
  }
}

function loadCognitoUsername(): string | null {
  try {
    return sessionStorage.getItem(SESSION_COGNITO_USERNAME_KEY);
  } catch {
    return null;
  }
}

/** Restore persisted auth state from localStorage/sessionStorage on app boot. */
function loadAuthState(): Omit<
  AuthState,
  "accessToken" | "refreshToken" | "cognitoUsername"
> {
  try {
    const stored = localStorage.getItem("authUser");
    if (stored) {
      const user: AuthUser = JSON.parse(stored);
      return {
        user,
        isAuthenticated: true,
        isLoading: false,
      };
    }
  } catch {
    localStorage.removeItem("authUser");
  }
  return { user: null, isAuthenticated: false, isLoading: false };
}

const initialState: AuthState = {
  ...loadAuthState(),
  accessToken: loadAccessToken(),
  refreshToken: loadRefreshToken(),
  cognitoUsername: loadCognitoUsername(),
};

const authSlice = createSlice({
  name: "auth",
  initialState,
  reducers: {
    setAuthenticated(
      state,
      action: PayloadAction<
        | (AuthUser & {
            accessToken?: string;
            refreshToken?: string;
            cognitoUsername?: string;
          })
        | null
        | undefined
      >,
    ) {
      const { accessToken, refreshToken, cognitoUsername, ...user } =
        action.payload ?? {};
      state.user = action.payload ? (user as AuthUser) : null;
      state.isAuthenticated = true;
      state.isLoading = false;
      state.accessToken = accessToken ?? null;
      state.refreshToken = refreshToken ?? null;
      state.cognitoUsername = cognitoUsername ?? null;

      // Persist access/refresh tokens to sessionStorage so they survive page
      // reloads. sessionStorage is cleared automatically when the tab is closed.
      if (accessToken) {
        try {
          sessionStorage.setItem(SESSION_TOKEN_KEY, accessToken);
        } catch {
          /* ignore */
        }
      } else {
        try {
          sessionStorage.removeItem(SESSION_TOKEN_KEY);
        } catch {
          /* ignore */
        }
      }

      if (refreshToken) {
        try {
          sessionStorage.setItem(SESSION_REFRESH_TOKEN_KEY, refreshToken);
        } catch {
          /* ignore */
        }
      } else {
        try {
          sessionStorage.removeItem(SESSION_REFRESH_TOKEN_KEY);
        } catch {
          /* ignore */
        }
      }

      if (cognitoUsername) {
        try {
          sessionStorage.setItem(SESSION_COGNITO_USERNAME_KEY, cognitoUsername);
        } catch {
          /* ignore */
        }
      } else {
        try {
          sessionStorage.removeItem(SESSION_COGNITO_USERNAME_KEY);
        } catch {
          /* ignore */
        }
      }

      // Persist non-sensitive profile fields to localStorage for session restoration.
      if (action.payload) {
        const { id, displayName, roles, permissions, email } = action.payload;
        localStorage.setItem(
          "authUser",
          JSON.stringify({ id, displayName, roles, permissions, email }),
        );
      } else {
        localStorage.removeItem("authUser");
      }
    },

    /** Called on auth failure or explicit logout — clears all auth state. */
    setUnauthenticated(state) {
      state.user = null;
      state.isAuthenticated = false;
      state.isLoading = false;
      state.accessToken = null;
      state.refreshToken = null;
      state.cognitoUsername = null;

      // Clear persisted auth data
      localStorage.removeItem("authUser");
      try {
        sessionStorage.removeItem(SESSION_TOKEN_KEY);
        sessionStorage.removeItem(SESSION_REFRESH_TOKEN_KEY);
        sessionStorage.removeItem(SESSION_COGNITO_USERNAME_KEY);
      } catch {
        /* ignore */
      }
    },

    setAuthLoading(state, action: PayloadAction<boolean>) {
      state.isLoading = action.payload;
    },

    /** Called after POST /auth/refresh succeeds — updates tokens without touching user/roles. */
    setAccessToken(
      state,
      action: PayloadAction<{ accessToken: string; refreshToken?: string }>,
    ) {
      state.accessToken = action.payload.accessToken;
      try {
        sessionStorage.setItem(SESSION_TOKEN_KEY, action.payload.accessToken);
      } catch {
        /* ignore */
      }

      if (action.payload.refreshToken) {
        state.refreshToken = action.payload.refreshToken;
        try {
          sessionStorage.setItem(
            SESSION_REFRESH_TOKEN_KEY,
            action.payload.refreshToken,
          );
        } catch {
          /* ignore */
        }
      }
    },
  },
});

export const {
  setAuthenticated,
  setUnauthenticated,
  setAuthLoading,
  setAccessToken,
} = authSlice.actions;

export default authSlice.reducer;

// ── Selectors ────────────────────────────────────────────────────────────────
export const selectCurrentUser = (state: { auth: AuthState }) =>
  state.auth.user;
export const selectIsAuthenticated = (state: { auth: AuthState }) =>
  state.auth.isAuthenticated;
export const selectAuthLoading = (state: { auth: AuthState }) =>
  state.auth.isLoading;
export const selectAccessToken = (state: { auth: AuthState }): string | null =>
  state.auth.accessToken;
export const selectRefreshToken = (state: { auth: AuthState }): string | null =>
  state.auth.refreshToken;
export const selectCognitoUsername = (
  state: { auth: AuthState },
): string | null => state.auth.cognitoUsername;
