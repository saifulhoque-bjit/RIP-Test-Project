// ── Ingestion Status ─────────────────────────────────────────────────────────
export const INGESTION_STATUS = {
  QUEUED: 'QUEUED',
  PARSING: 'PARSING',
  NORMALIZATION_COMPLETED: 'NORMALIZATION_COMPLETED',
  COMPLETED: 'COMPLETED',
  FAILED: 'FAILED',
} as const;

export type IngestionStatus =
  (typeof INGESTION_STATUS)[keyof typeof INGESTION_STATUS];

// ── Ingestion Job ────────────────────────────────────────────────────────────
export interface IngestionJob {
  id: string;
  sourceId: string;
  filename: string;
  status: IngestionStatus;
  /** Granular human-readable status message (e.g., "Extracting Tables from Page 12"). */
  statusMessage: string;
  /** 0–100 progress percentage. */
  progressPercent: number;
  /** ISO 8601 timestamp when ingestion was queued. */
  queuedAt: string;
  /** ISO 8601 timestamp when ingestion completed or failed. Null if in progress. */
  completedAt: string | null;
  /** JSON error structure — available for download when status is FAILED. */
  errorDetails: IngestionErrorDetail[] | null;
  /** Number of fragments extracted so far. */
  fragmentsExtracted: number;
}

// ── Ingestion Error Detail ───────────────────────────────────────────────────
export interface IngestionErrorDetail {
  code: string;
  message: string;
  context: Record<string, unknown>;
}

// ── Retry Ingestion Payload ──────────────────────────────────────────────────
export interface RetryIngestionPayload {
  sourceId: string;
}
