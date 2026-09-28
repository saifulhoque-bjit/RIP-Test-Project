export type ProjectType =
  | "rfp"
  | "additional_rfp"
  | "source_code"
  | "meeting_notes"
  | "requirement_update";

export interface Project {
  id: string;
  name: string;
  description?: string;
  status?: string;
  project_type?: ProjectType | null;
  llm_provider?: string;
  llm_model?: string;
  files?: number;
  user_stories?: number;
  approved_user_stories?: number;
  jira_synced_count?: number;
  tap_synced_count?: number;
  pending_jira_sync?: number;
  pending_tap_sync?: number;
  team_members?: number;
  owner_id?: string;
  created_at?: string;
  updated_at?: string;
  /** Most recent activity on the project (backend-derived), distinct from the row's own updated_at. */
  last_activity_at?: string;
}

export interface ProjectDetailResponse {
  success: boolean;
  message: string;
  data: Project;
}

export interface ProjectListParams {
  skip?: number;
  limit?: number;
  search?: string;
  /** Scope the list to a specific tenant (e.g. for a super_admin browsing another client's projects). */
  tenant_id?: string;
}

export interface ProjectListResponse {
  success: boolean;
  message: string;
  data: {
    items: Project[];
    total: number;
    skip: number;
    limit: number;
  };
}

/**
 * Trimmed project shape returned by `GET /projects/list` — enough to render a
 * picker, and assignable to `Project` wherever only id/name/files are read.
 */
export interface ProjectNameListItem {
  id: string;
  name: string;
  files?: number;
}

export interface ProjectNameListResponse {
  success: boolean;
  message: string;
  data: ProjectNameListItem[];
}

export interface CreateProjectRequest {
  name: string;
  description?: string;
  llm_provider?: string;
  llm_model?: string;
  project_type?: ProjectType;
}

export interface UpdateProjectRequest {
  name?: string;
  description?: string;
  llm_provider?: string;
  llm_model?: string;
  project_type?: ProjectType;
}
