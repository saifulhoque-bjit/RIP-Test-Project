/**
 * Module and Feature types for RIP projects
 */

import type { ChangeType, TextDiffItem } from "./user-story";

export interface Function {
  id: string; // Required in API response
  user_story_code?: string; // e.g., "U.S 1.1.1" 
  fun_code?: string; // Legacy/alternate format
  name: string;
  description?: string;
  status?: string;
}

export interface Feature {
  id: string;
  fea_code: string;
  name: string;
  description: string;
  status?: string;
  total_user_stories?: number;
  children?: Function[]; // For nested tree structure
}

export interface Module {
  id: string;
  mod_code: string;
  name: string;
  description: string;
  status?: string;
  features?: Feature[]; // Legacy flat format
  children?: Feature[]; // New nested tree format
  total_features?: number;
  total_user_stories?: number;
}

export interface ModulesListResponse {
  success: boolean;
  message: string;
  data: {
    total: number;
    skip: number;
    limit: number;
    items: Module[];
  };
}

export interface RegenerateModulesResponse {
  success: boolean;
  message: string;
  data: {
    task_id: string;
    project_id: string;
    source_ids: string[];
    status: string;
  };
}

export interface UpdateModulesStatusResponse {
  success: boolean;
  message: string;
  data: {
    task_id: string;
    project_id: string;
    status: string;
  };
}

// Field-level diff map on a module "UPDATED" detail response.
export interface ModuleTextDiffs {
  name?: TextDiffItem[];
  description?: TextDiffItem[];
}

// Full snapshot of the prior version of a module — present as
// `last_previous_items` on "UPDATED" detail responses.
export interface ModulePreviousSnapshot {
  id: string;
  module_id?: string;
  project_id?: string;
  mod_code?: string;
  name?: string;
  description?: string;
  version?: number;
  status?: string;
  justification?: string | null;
  incremental_change_type?: ChangeType;
  feedback_change_type?: ChangeType;
  is_jira_synced?: boolean;
  is_tap_synced?: boolean;
  created_at?: string | null;
  updated_at?: string | null;
  snapshotted_at?: string | null;
}

export interface ModuleDetailsData {
  id: string;
  project_id?: string;
  mod_code?: string;
  name?: string;
  description?: string;
  status?: string;
  total_features?: number;
  total_user_stories?: number;
  children?: Feature[];
  incremental_change_type?: ChangeType;
  feedback_change_type?: ChangeType;
  text_diffs?: ModuleTextDiffs;
  /** Full snapshot of the prior version — present on "UPDATED" detail responses. */
  last_previous_items?: ModulePreviousSnapshot;
  created_at?: string;
  updated_at?: string;
}

export interface ModuleDetailsPayload {
  project_id: string;
  module: ModuleDetailsData;
}

export interface ModuleDetailsResponse {
  success: boolean;
  message: string;
  data: ModuleDetailsPayload;
}

// ── Module Feedback Draft (client-side only — backend takes one joined string) ──
export interface ModuleFeedbackNote {
  module_id: string;
  module_label: string;
  note: string;
}

export interface ModuleFeedback {
  project_id: string;
  notes: ModuleFeedbackNote[];
}
