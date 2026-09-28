/**
 * Project workspace tab definitions.
 * Each tab maps to an existing route in the application.
 *
 * `buildPath` generates the route path for a given project ID.
 */
export const PROJECT_TABS = [
  {
    key: "overview",
    label: "Overview",
    buildPath: (id: string) => `/projects/${id}/overview`,
  },
  {
    key: "sources",
    label: "Sources",
    buildPath: (id: string) => `/projects/${id}/sources`,
  },
  {
    key: "pipelines",
    label: "Pipelines",
    buildPath: (id: string) => `/projects/${id}/pipelines`,
  },
  {
    key: "review",
    label: "Review",
    buildPath: (id: string) => `/projects/${id}/review`,
  },
  {
    key: "requirements",
    label: "Requirements",
    buildPath: (id: string) => `/projects/${id}/requirements`,
  },
  {
    key: "settings",
    label: "Settings",
    buildPath: (id: string) => `/projects/${id}/settings`,
  },
  {
    key: "activity",
    label: "Activity",
    buildPath: (id: string) => `/projects/${id}/activity`,
  },
] as const;

export type ProjectTabKey = (typeof PROJECT_TABS)[number]["key"];
