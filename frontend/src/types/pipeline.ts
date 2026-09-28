export type RunStage = "module_feature" | "user_story";

export type RunStatus =
  | "completed"
  | "running"
  | "ready_for_review"
  | "failed"
  | "cancelled";

export interface PipelineRun {
  [key: string]: unknown; // ← add this line
  id: string;
  run_code: string;
  project_id: string;
  project_name: string | null;
  source_type: "rfp" | "source_code";
  stages: RunStage[];
  status: RunStatus;
  tot_modules: number;
  tot_features: number;
  tot_user_stories: number;
  created_at: string;
  updated_at: string;
}

export interface PipelinesResponse {
  success: boolean;
  message: string;
  data: {
    items: PipelineRun[];
    total: number;
    skip: number;
    limit: number;
    has_next: boolean;
    has_previous: boolean;
  };
}
