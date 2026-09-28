// ── Dashboard ──────────────────────────────────────────────────────────────

export interface DashboardLongestProject {
  project_id: string;
  project_name: string;
  duration_seconds: number;
}

export interface DashboardStats {
  total_projects: number;
  active_projects: number;
  active_projects_current_month: number;
  total_modules: number;
  total_features: number;
  total_stories: number;
  running_pipelines: number;
  avg_pipeline_completion_seconds: number;
  longest_completion_project: DashboardLongestProject | null;
  /** Approved user stories not yet synced to Jira (status=approved AND is_jira_synced=false). */
  pending_jira_sync_count: number;
  /** Approved user stories not yet synced to TAP (status=approved AND is_tap_synced=false). */
  pending_tap_sync_count: number;
  /** Approved user stories within the caller's scope. */
  approved_user_stories: number;
}

export interface DashboardStatsResponse {
  success: boolean;
  message: string;
  data: DashboardStats;
}