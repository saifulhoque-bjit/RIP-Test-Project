// ── Config Option (Key-Value Pair) ──────────────────────────────────────────
export interface ConfigOption {
  key: string;
  value: string;
}

export interface SourceLanguageManifesto {
  coding_standards: string;
  database_strategy: string;
  architecture: string;
  security: string;
}

export interface LlmModel {
  id: string;
  name: string;
}

export interface LlmProvider {
  id: string;
  name: string;
  models: LlmModel[];
}

// ── Project Settings ────────────────────────────────────────────────────────
export interface ProjectSettings {
  /** Available LLM providers, each with its supported models */
  llm_providers: LlmProvider[];
}

// ── Source Code Pipeline Settings ───────────────────────────────────────────
export interface SourceCodePipelineSettings {
  /** Supported source code languages */
  source_languages: ConfigOption[];
  /** Language-specific modernization defaults */
  source_language_manifestos: Record<string, SourceLanguageManifesto>;
  /** Supported frontend technology stacks */
  frontend_stacks: ConfigOption[];
  /** Supported backend technology stacks */
  backend_stacks: ConfigOption[];
  /** Supported database technology stacks */
  database_stacks: ConfigOption[];
  /** Supported infrastructure stacks */
  infrastructure_stacks: ConfigOption[];
  /** Supported architecture patterns */
  architecture_stacks: ConfigOption[];
}

// ── App Settings Data ───────────────────────────────────────────────────────
export interface AppSettingsData {
  project: ProjectSettings;
  source_code_pipeline: SourceCodePipelineSettings;
}

// ── App Settings Response ───────────────────────────────────────────────────
export interface AppSettings {
  success: boolean;
  message: string;
  data: AppSettingsData;
}

// ── Enum Catalog ─────────────────────────────────────────────────────────────
export interface EnumOption {
  value: string;
  label: string;
}

export interface EnumCatalogData {
  activity_type: EnumOption[];
  context_mode: EnumOption[];
  invitation_status: EnumOption[];
  llm_provider: EnumOption[];
  module_regeneration_status: EnumOption[];
  notification_type: EnumOption[];
  project_member_role: EnumOption[];
  project_status: EnumOption[];
  project_type: EnumOption[];
  run_stage: EnumOption[];
  source_code_stage: EnumOption[];
  source_layout_type: EnumOption[];
  source_status: EnumOption[];
  source_type: EnumOption[];
  tenant_status: EnumOption[];
  user_story_generation_status: EnumOption[];
  /** Maps each RFP run_stage to the set of source_status values that count as "in that stage". */
  rfp_baseline_stages_status_map: Record<string, string[]>;
  /** Maps each feedback/incremental stage to the set of source_status values that count as "in that stage". */
  feedback_or_incremental_stages_status_map: Record<string, string[]>;
  /** Maps each source_code_stage to the set of source_status values that count as "in that stage". */
  source_code_baseline_stages_status_map: Record<string, string[]>;
  /** Flat key -> label lookup for every stage across all three ingestion-pipeline flows (RFP baseline, feedback/incremental, source-code baseline). */
  source_ingestion_stage: Record<string, string>;
  /** Flat key -> label lookup for ingestion-run/pipeline status values (running/ready_for_review/completed/failed/cancelled). */
  source_ingestion_status: Record<string, string>;
}

// ── Enum Catalog Response ────────────────────────────────────────────────────
export interface EnumCatalog {
  success: boolean;
  message: string;
  data: EnumCatalogData;
}
