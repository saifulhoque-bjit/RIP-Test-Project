import type { DashboardStats } from "@/types";

export const MOCK_DASHBOARD_ENABLED = false;

export const MOCK_STATS: DashboardStats = {
  total_projects: 15,
  active_projects: 12,
  active_projects_current_month: 3,
  total_modules: 42,
  total_features: 184,
  total_stories: 1256,
  running_pipelines: 2,
  avg_pipeline_completion_seconds: 43200,
  longest_completion_project: {
    project_id: "run-1039",
    project_name: "EPHRS Payroll (VB6)",
    duration_seconds: 104400,
  },
  pending_jira_sync_count: 42,
  pending_tap_sync_count: 37,
  approved_user_stories: 980,
};