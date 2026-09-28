/**
 * Centralized API Endpoints Configuration
 *
 * This file serves as the single source of truth for all API endpoints.
 * All endpoints are organized by feature/module for easy management and maintenance.
 *
 * Usage:
 * import { API_ENDPOINTS } from '@/services/api/endpoints';
 * Then reference: API_ENDPOINTS.AUTH.LOGIN, API_ENDPOINTS.PROJECTS.GET_LIST, etc.
 */

export const API_ENDPOINTS = {
  // ── Authentication ────────────────────────────────────────────────────────
  AUTH: {
    LOGIN: "/auth/login",
    LOGOUT: "/auth/logout",
    REFRESH: "/auth/refresh",
    FORGOT_PASSWORD: "/auth/forgot-password",
    RESET_PASSWORD: "/auth/reset-password",
  },

  // ── Projects ──────────────────────────────────────────────────────────────
  PROJECTS: {
    GET_LIST: "/projects",
    /** Admin/super_admin only — every project on a given tenant. */
    GET_ALL: "/projects/all",
    /**
     * Unpaginated id/name list for pickers — no `total`/`skip`/`limit`
     * envelope, just `data: [{ id, name, files }]`.
     */
    GET_NAME_LIST: "/projects/list",
    GET_DETAIL: (id: string) => `/projects/${id}`,
    CREATE: "/projects",
    UPDATE: (id: string) => `/projects/${id}`,
  },

  // ── Sources ───────────────────────────────────────────────────────────────
  SOURCES: {
    GET_LIST: "/sources",
    GET_DETAIL: (id: string) => `/sources/${id}`,
    UPLOAD: "/sources/upload/bulk",
    LINK_UPLOAD: "/sources/upload/link",
    DELETE_BULK: "/sources/delete/bulk",
    INGESTION_LIST: "/sources/ingestion",
    UPDATE_FRAGMENT_BBOX: (sourceId: string, fragmentId: string) =>
      `/sources/${sourceId}/fragments/${fragmentId}/bbox`,
  },

  // ── Requirements ──────────────────────────────────────────────────────────
  REQUIREMENTS: {
    GET_BY_PROJECT: (projectId: string) =>
      `/projects/${projectId}/user-stories/list/tree`,
    /** Approved, not-yet-synced tree for the Jira/TAP Sync Tray (one call, no client-side filtering). */
    SYNC_CANDIDATES: (projectId: string) =>
      `/projects/${projectId}/user-stories/sync-candidates`,
    GET_DETAIL_BY_PROJECT: (projectId: string, requirementId: string) =>
      `/projects/${projectId}/user-stories/${requirementId}`,
    GET_SUMMARY: (projectId: string) =>
      `/projects/${projectId}/user-stories/summary`,
    BULK_STATUS: (projectId: string) =>
      `/projects/${projectId}/user-stories/bulk-status`,
    DELETE_BY_PROJECT: (projectId: string, userStoryId: string) =>
      `/projects/${projectId}/user-stories/${userStoryId}`,
    REGENERATE: (projectId: string) =>
      `/projects/${projectId}/user-stories/regenerate`,
    REGENERATE_BY_FEEDBACK: (projectId: string) =>
      `/projects/${projectId}/user-stories/regenerate-by-feedback`,
    REGENERATE_FOR_SOURCE_CODE: (projectId: string) =>
      `/projects/${projectId}/user-stories/regenerate-for-source-code`,
    UPDATE_BBOXES: (projectId: string, requirementId: string) =>
      `/projects/${projectId}/user-stories/${requirementId}/bboxes`,
  },

  // ── Modules ───────────────────────────────────────────────────────────────
  MODULES: {
    GET_LIST: (projectId: string) => `/projects/${projectId}/modules/list`,
    GET_DETAIL: (projectId: string, moduleId: string) =>
      `/projects/${projectId}/modules/${moduleId}`,
    REGENERATE: (projectId: string) =>
      `/projects/${projectId}/modules/regenerate`,
    UPDATE_STATUS: (projectId: string) =>
      `/projects/${projectId}/modules/status`,
  },

  // ── Features ──────────────────────────────────────────────────────────────
  FEATURES: {
    GET_DETAIL: (projectId: string, moduleId: string, featureId: string) =>
      `/projects/${projectId}/modules/${moduleId}/features/${featureId}`,
  },

  // ── Incremental Updates (accept/reject a module/feature/story change) ─────
  UPDATES: {
    ACCEPT: (projectId: string) => `/projects/${projectId}/updates/accept`,
    REJECT: (projectId: string) => `/projects/${projectId}/updates/reject`,
  },

  // ── JIRA Integration ──────────────────────────────────────────────────────
  JIRA: {
    /** Per-project Jira integration config (GET/POST/PATCH). */
    PROJECT_INTEGRATION: (projectId: string) =>
      `/projects/${projectId}/integrations/jira`,
    /** Execute the full Jira sync for a project (POST). */
    SYNC_EXECUTE: (projectId: string) =>
      `/projects/${projectId}/integrations/jira/sync`,
  },

  // ── TAP Integration ───────────────────────────────────────────────────────
  TAP: {
    /** Per-project TAP integration config (GET/POST/PATCH). */
    PROJECT_INTEGRATION: (projectId: string) =>
      `/projects/${projectId}/integrations/tap`,
    /** Stage the RIP hierarchy for TAP and notify it to pull (POST). */
    SYNC_EXECUTE: (projectId: string) =>
      `/projects/${projectId}/integrations/tap/sync`,
  },


  // ── Export ────────────────────────────────────────────────────────────────
  EXPORT: {
    /** Build and download a project export ZIP (requirements backlog + SRS specs). */
    CREATE: (projectId: string) => `/projects/${projectId}/export`,
  },

  // ── Dashboard ─────────────────────────────────────────────────────────────
  DASHBOARD: {
    GET_STATS: "/projects/dashboard/stats",
  },

  // ── Pipelines ─────────────────────────────────────────────────────────────
  PIPELINES: {
    GET_LIST: "/projects/me/pipelines",
  },

  // ── Tasks ─────────────────────────────────────────────────────────────────
  TASKS: {
    CANCEL: (taskId: string) => `/tasks/${taskId}`,
  },

  // ── Activity Logs ─────────────────────────────────────────────────────────
  ACTIVITY_LOGS: {
    GET_LIST: (projectId: string) => `/projects/${projectId}/activity-logs`,
  },

  // ── Notifications ─────────────────────────────────────────────────────────
  NOTIFICATIONS: {
    GET_LIST: "/notifications",
    UNREAD_COUNT: "/notifications/unread-count",
    MARK_ALL_READ: "/notifications/read-all",
    MARK_READ: (id: string) => `/notifications/${id}/read`,
  },

  // ── Settings ───────────────────────────────────────────────────────────────
  SETTINGS: {
    GET_CONFIG: "/settings",
    GET_ENUMS: "/settings/enums",
  },

  // ── Users ─────────────────────────────────────────────────────────────────
  USERS: {
    GET_LIST: "/users/",
    UPDATE_ROLE: (userId: string) => `/users/${userId}/roles`,
    ASSIGN_PROJECTS: (userId: string) => `/users/${userId}/project-assignments`,
    UPDATE_STATUS: (userId: string) => `/users/${userId}/status`,
    DELETE: (userId: string) => `/users/${userId}`,
  },

  // ── Tenants ───────────────────────────────────────────────────────────────
  TENANTS: {
    GET_LIST: "/tenants/",
    GET_ME: "/tenants/me",
    /** Super admin only — client counts by status plus total projects. */
    GET_STATS: "/tenants/stats",
    CREATE: "/tenants/",
    UPDATE: (tenantId: string) => `/tenants/${tenantId}`,
    UPDATE_STATUS: (tenantId: string) => `/tenants/${tenantId}`,
    INVITE_USER: (tenantId: string) => `/tenants/${tenantId}/invitations`,
    GET_INVITATIONS: (tenantId: string) => `/tenants/${tenantId}/invitations`,
    RESEND_INVITATION: (tenantId: string, invitationId: string) =>
      `/tenants/${tenantId}/invitations/${invitationId}/resend`,
    REVOKE_INVITATION: (tenantId: string, invitationId: string) =>
      `/tenants/${tenantId}/invitations/${invitationId}/revoke`,
    LLM_PROVIDERS_LIST: (tenantId: string) =>
      `/tenants/${tenantId}/llm-providers`,
    LLM_PROVIDER_UPDATE: (tenantId: string, provider: string) =>
      `/tenants/${tenantId}/llm-providers/${provider}`,
    LLM_PROVIDER_TEST: (tenantId: string, provider: string) =>
      `/tenants/${tenantId}/llm-providers/${provider}/test`,
  },

  // ── Invitations ───────────────────────────────────────────────────────────
  INVITATIONS: {
    GET_DETAIL: (token: string) => `/invitations/${token}`,
    ACCEPT: (token: string) => `/invitations/${token}/accept`,
  },
} as const;
