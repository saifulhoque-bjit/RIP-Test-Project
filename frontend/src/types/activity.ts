export interface ActivityLogEntry {
  id: string;
  project_id: string;
  actor_user_id: string | null;
  actor?: {
    id: string;
    name: string;
    role: string;
  } | null;
  activity_type: string;
  summary: string;
  message: string;
  data: Record<string, unknown> | null;
  created_at: string;
}

export interface ActivityLogListResponse {
  success: boolean;
  message: string;
  data: {
    items: ActivityLogEntry[];
    total: number;
    skip: number;
    limit: number;
  };
}
