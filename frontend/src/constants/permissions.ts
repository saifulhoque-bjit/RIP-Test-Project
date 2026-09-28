// Central source of truth for every permission string returned by the login API.
// Format: "<resource>:<action>". Add new values here as the backend adds them —
// do not inline raw permission strings elsewhere in the app.

export const PERMISSION = {
  USER_VIEW: "user:view",
  USER_INVITE: "user:invite",
  USER_REMOVE: "user:remove",
  USER_MANAGE_ROLES: "user:manage_roles",

  STORY_VIEW: "story:view",
  STORY_UPDATE: "story:update",
  STORY_DELETE: "story:delete",
  STORY_APPROVE: "story:approve",
  STORY_REJECT: "story:reject",
  STORY_REGENERATE: "story:regenerate",

  PROJECT_VIEW: "project:view",
  PROJECT_CREATE: "project:create",
  PROJECT_UPDATE: "project:update",
  PROJECT_DELETE: "project:delete",

  FRAGMENT_VIEW: "fragment:view",
  FRAGMENT_UPDATE: "fragment:update",

  INTEGRATION_VIEW: "integration:view",
  INTEGRATION_CREATE: "integration:create",
  INTEGRATION_UPDATE: "integration:update",
  INTEGRATION_DELETE: "integration:delete",
  INTEGRATION_SYNC: "integration:sync",

  TENANT_VIEW: "tenant:view",
  TENANT_CREATE: "tenant:create",
  TENANT_UPDATE: "tenant:update",
  TENANT_DELETE: "tenant:delete",

  MODULE_VIEW: "module:view",
  MODULE_UPDATE: "module:update",
  MODULE_REGENERATE: "module:regenerate",

  FEATURE_VIEW: "feature:view",
  FEATURE_UPDATE: "feature:update",
  FEATURE_REGENERATE: "feature:regenerate",

  SOURCE_VIEW: "source:view",
  SOURCE_CREATE: "source:create",
  SOURCE_DELETE: "source:delete",

  INCREMENTAL_UPDATE_VIEW: "incremental_update:view",
  INCREMENTAL_UPDATE_ACCEPT: "incremental_update:accept",
  INCREMENTAL_UPDATE_REJECT: "incremental_update:reject",

  TASK_VIEW: "task:view",
  TASK_CANCEL: "task:cancel",

  NOTIFICATION_VIEW: "notification:view",
  NOTIFICATION_UPDATE: "notification:update",

  OBSERVABILITY_VIEW: "observability:view",
  OBSERVABILITY_MANAGE: "observability:manage",

  EXPORT_CREATE: "export:create",
} as const;

export type Permission = (typeof PERMISSION)[keyof typeof PERMISSION];

export const ALL_PERMISSIONS: Permission[] = Object.values(PERMISSION);
