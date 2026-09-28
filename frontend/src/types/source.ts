// ── Source File Format ───────────────────────────────────────────────────────
export const SOURCE_FILE_FORMAT = {
  PDF: "PDF",
  DOCX: "DOCX",
  DOC: "DOC",
  XLSX: "XLSX",
  ZIP: "ZIP",
  JPEG: "JPEG",
  JPG: "JPG",
  PNG: "PNG",
  WEBP: "WEBP",
  MP4: "MP4",
  MP3: "MP3",
  WAV: "WAV",
  MOV: "MOV",
} as const;

export type SourceFileFormat =
  (typeof SOURCE_FILE_FORMAT)[keyof typeof SOURCE_FILE_FORMAT];

// ── Source Type (UI-level viewer category) ──────────────────────────────────
export const SOURCE_TYPE = {
  PDF: "PDF",
  WORD: "WORD",
  IMAGE: "IMAGE",
  FIGMA: "FIGMA",
  RECORDING: "RECORDING",
  CODE: "CODE",
} as const;

export type SourceType = (typeof SOURCE_TYPE)[keyof typeof SOURCE_TYPE];

// ── Source Status ───────────────────────────────────────────────────────────
export const SOURCE_STATUS = {
  RUNNING: "running",
  COMPLETED: "completed",
  FAILED: "failed",
  READY_FOR_REVIEW: "ready_for_review",
  CANCELLED: "cancelled",
} as const;

export type SourceStatus = (typeof SOURCE_STATUS)[keyof typeof SOURCE_STATUS];

// ── Source Upload Type ──────────────────────────────────────────────────────
export const SOURCE_UPLOAD_TYPE = {
  SINGLE: "single",
  BULK: "bulk",
  LINK: "link",
} as const;

export type SourceUploadType =
  (typeof SOURCE_UPLOAD_TYPE)[keyof typeof SOURCE_UPLOAD_TYPE];

/** Max upload size per file: 500 MB */
export const MAX_FILE_SIZE_BYTES = 500 * 1024 * 1024;

// ── Source (Root Knowledge Graph Node) ──────────────────────────────────────
export interface Source {
  id: string;
  project_id: string;
  original_name: string;
  source_type: string;
  storage_key: string;
  storage_url: string;
  file_size_bytes: number;
  mime_type: string;
  file_format: string;
  upload_type: SourceUploadType;
  batch_id: string | null;
  status: SourceStatus;
  checksum_sha256: string;
  description: string;
  link_url: string | null;
  is_deleted: boolean;
  created_by: string;
  created_at: string;
  updated_at: string;
}

// ── Source List API Response ────────────────────────────────────────────────
export interface SourceListResponse {
  success: boolean;
  message: string;
  data: {
    items: Source[];
    total: number;
    skip: number;
    limit: number;
  };
}

// ── Source List Query Params ────────────────────────────────────────────────
export interface SourceListParams {
  projectId: string;
  skip?: number;
  limit?: number;
  status?: SourceStatus;
  file_format?: string;
}

// ── Source Upload Response ──────────────────────────────────────────────────
export interface SourceUploadResult {
  filename: string;
  status: string;
  status_code: number;
  source_id: string | null;
  error: string | null;
}

export interface SourceUploadResponse {
  success: boolean;
  message: string;
  data: {
    batch_id: string;
    total: number;
    succeeded: number;
    failed: number;
    results: SourceUploadResult[];
  };
}

// ── Ingestion List ──────────────────────────────────────────────────────────
export interface IngestionSource {
  id: string;
  original_name: string;
  status: string;
  file_type: string;
  upload_type: string;
  file_size_bytes: number;
  storage_key: string;
  created_at: string;
}

export interface IngestionItem {
  [key: string]: unknown;
  id: string;
  project_id: string;
  run_code: string;
  source_type: string;
  stages: string[];
  status: string;
  description: string | null;
  skip_processing: boolean;
  /** Present for feedback-driven runs (source_type "requirement_update_from_feedback"). */
  entity_json?: {
    feedback_items?: Array<{ user_story_id: string }>;
    user_story_ids?: string[];
  } | null;
  /** Backend failure messages for a run that ended in "failed". Surfaced by the Pipelines row action. */
  errors?: string[] | null;
  tot_modules: number;
  /** source_type "source_code" only: total modules the code-dependency analysis attempted requirement extraction for. */
  tot_modules_from_global_artifact: number;
  tot_features: number;
  tot_user_stories: number;
  tot_modules_updated: number;
  tot_features_updated: number;
  tot_user_stories_updated: number;
  tot_modules_deleted: number;
  tot_features_deleted: number;
  tot_user_stories_deleted: number;
  mod_fea_gen_started_at: string | null;
  mod_fea_gen_completed_at: string | null;
  user_story_gen_started_at: string | null;
  user_story_gen_completed_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
  sources: IngestionSource[];
}

export interface IngestionListParams {
  projectId: string;
  skip?: number;
  limit?: number;
}

export interface IngestionListResponse {
  success: boolean;
  message: string;
  data: {
    items: IngestionItem[];
    total: number;
    skip: number;
    limit: number;
  };
}

// ── Fragment (raw extracted snippet, Layer 2 of Knowledge Graph) ─────────────
export interface Fragment {
  id: string;
  sourceId: string;
  /** Extracted raw text from the source. */
  rawText: string;
  /** Page number (PDF/WORD), bounding box [x, y, w, h] (IMAGE), or seconds offset (RECORDING). */
  grounding: GroundingCoordinates;
  /** Whether this fragment has been discarded to the noise archive. */
  isNoise: boolean;
  /** Candidate canonical requirement this fragment was merged into, if any. */
  canonicalRequirementId: string | null;
  createdAt: string;
}

// ── Grounding Coordinates (physical address of a Fragment) ──────────────────
export type GroundingCoordinates =
  | { type: "document"; page: number; line: number | null }
  | { type: "image"; bbox: [number, number, number, number] }
  | { type: "recording"; timestampSeconds: number };

// ── Trace Node (Layer 4: link to Jira / TAP) ────────────────────────────────
export interface TraceNode {
  id: string;
  canonicalRequirementId: string;
  /** 'JIRA' | 'TAP' */
  system: "JIRA" | "TAP";
  externalId: string;
  externalUrl: string | null;
  syncedAt: string;
}
