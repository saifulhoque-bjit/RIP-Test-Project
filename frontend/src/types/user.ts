// ── User (as returned by GET /users/) ───────────────────────────────────────
export interface UserRoleInfo {
  id: string;
  name: string;
  display_name: string;
  description: string;
}

export interface UserProjectInfo {
  id: string;
  name: string;
  status: string;
  roles: string[];
}

export interface ApiUser {
  id: string;
  cognito_sub: string;
  email: string;
  name: string;
  is_active: boolean;
  is_verified: boolean;
  tenant_id: string;
  roles: UserRoleInfo[];
  projects: UserProjectInfo[];
  created_at: string;
  updated_at: string;
}

// ── User List API Response ──────────────────────────────────────────────────
export interface UserListResponse {
  success: boolean;
  message: string;
  data: ApiUser[];
}

// ── Update user role (POST /users/{user_id}/roles) ──────────────────────────
export interface UpdateUserRoleRequest {
  role_name: string;
}

export interface UpdateUserRoleResponse {
  success: boolean;
  message: string;
  data: ApiUser;
}

// ── Assign user to projects (POST /users/{user_id}/project-assignments) ────
export interface ProjectAssignmentInput {
  project_id: string;
  /** One role per project — the API takes an array, but only ever one entry today. */
  roles: string[];
}

export interface AssignUserProjectsRequest {
  assignments: ProjectAssignmentInput[];
}

export interface AssignUserProjectsResponse {
  success: boolean;
  message: string;
  data: ApiUser;
}
