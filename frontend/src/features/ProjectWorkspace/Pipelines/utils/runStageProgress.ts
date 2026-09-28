import { normalizeIngestionStatus } from "@/features/ProjectWorkspace/Sources/sourceColumns";
import type { IngestionItem } from "@/types";

/**
 * Shared stage-progress model for an ingestion run — used by both the
 * Pipeline Runs table's Progress column (ProgressStageCell) and the
 * selected run's journey (RunJourney), so the two always agree on stage
 * count, labels, and done/current/waiting coloring.
 *
 * The backend reports its current stage as a literal entry inside `stages`
 * (not only as the run's overall `status`), so `stages.length` is the
 * source of truth for how far a run has actually progressed through its
 * fixed, per-source_type key sequence below. RFP hits two distinct gates —
 * "module_feature_ready_for_review" after module_feature (awaiting the
 * "Approve & generate user stories" click) and "user_story_ready_for_review"
 * after user_story (truly done) — both reported with the same generic
 * "ready_for_review" status, so the stage key (not the status) is what
 * disambiguates them (see needsModuleFeatureApproval).
 *
 * Stage/status label *text* is never hardcoded here — every label is looked
 * up by key from the enum catalog (`useGetEnumCatalogQuery().data.
 * source_ingestion_stage` / `.source_ingestion_status`), passed in by the
 * caller, so the two callers always show whatever the backend currently
 * calls that key.
 */

export interface StageStep {
  key: string;
  label: string;
}

const CODE_STAGE_KEYS = [
  "ingesting_sources",
  "building_code_dependency_graph",
  "discovering_modules",
  "extracting_requirements",
  "ready_for_review",
];

// Modules & Features generate first and gate on the user's "Approve &
// generate user stories" click — a distinct stage from the final
// "ready_for_review" that follows User Stories — before User Stories start.
const RFP_INITIAL_STAGE_KEYS = [
  "generating_module_feature",
  "module_feature_ready_for_review",
  "generating_user_story",
  "user_story_ready_for_review",
];

// Feedback-driven regeneration (Review tab's "regenerate by feedback") and
// the Sources page's incremental uploads (meeting notes, requirement-update
// documents, additional RFP files) are two different flows, but both report
// this same two-entry stage sequence.
const FEEDBACK_OR_INCREMENTAL_STAGE_KEYS = ["generating_requirements", "ready_for_review"];

const RFP_INCREMENTAL_SOURCE_TYPES = new Set([
  "meeting_notes",
  "requirement_update",
  "additional_rfp",
]);

// An unrecognized source_type has no backend stage key to look up a label
// for, so this one stays a fixed frontend string rather than a catalog
// lookup.
const INCREMENTAL_STAGE_LABEL = "Incremental Update";

export function isCodeRun(run: IngestionItem): boolean {
  return run.source_type === "source_code";
}

// Only the original bulk/initial RFP ingestion runs through the full
// module_feature -> approval gate -> user_story -> review sequence.
export function isMultiStageRfpRun(run: IngestionItem): boolean {
  return run.source_type === "rfp";
}

// The Review page's "regenerate by feedback" flow reports its own distinct
// source_type, separate from the Sources-page incremental "requirement
// update" upload it's easily confused with.
export function isFeedbackRun(run: IngestionItem): boolean {
  return run.source_type === "requirement_update_from_feedback";
}

export function isIncrementalUploadRun(run: IngestionItem): boolean {
  return RFP_INCREMENTAL_SOURCE_TYPES.has(run.source_type);
}

function getStageKeys(run: IngestionItem): string[] {
  if (isCodeRun(run)) return CODE_STAGE_KEYS;
  if (isMultiStageRfpRun(run)) return RFP_INITIAL_STAGE_KEYS;
  if (isFeedbackRun(run) || isIncrementalUploadRun(run)) {
    return FEEDBACK_OR_INCREMENTAL_STAGE_KEYS;
  }
  return [];
}

// Runs that report real progress through `stages` — the array grows one
// entry at a time as the backend completes each step/gate — unlike an
// unrecognized source_type, which only ever carries a single static entry.
export function isStagedRun(run: IngestionItem): boolean {
  return getStageKeys(run).length > 0;
}

/**
 * Full ordered [key, label] steps a staged run progresses through, labels
 * resolved from `stageLabels` (the fetched enum catalog's
 * `source_ingestion_stage`, keyed by stage key) — falls back to the raw key
 * if the catalog hasn't loaded yet or doesn't (yet) have that key. Empty for
 * single-stage runs.
 */
export function getStageSteps(
  run: IngestionItem,
  stageLabels: Record<string, string>,
): StageStep[] {
  return getStageKeys(run).map((key) => ({ key, label: stageLabels[key] ?? key }));
}

export function getTotalSegments(run: IngestionItem): number {
  const keys = getStageKeys(run);
  return keys.length > 0 ? keys.length : 1;
}

/** Single-stage runs' fixed label (an unrecognized source_type — every known flow is staged now); null for staged runs. */
export function getSingleStageLabel(run: IngestionItem): string | null {
  if (!isStagedRun(run)) return INCREMENTAL_STAGE_LABEL;
  return null;
}

export function getStageLabel(run: IngestionItem, stageLabels: Record<string, string>): string {
  const singleLabel = getSingleStageLabel(run);
  if (singleLabel) {
    return `Stage 1 of 1 - ${singleLabel}`;
  }
  if (run.stages.length === 0) return "—";
  const keys = getStageKeys(run);
  const activeKey = run.stages[run.stages.length - 1];
  const activeLabel = stageLabels[activeKey] ?? activeKey;
  return `Stage ${run.stages.length} of ${keys.length} - ${activeLabel}`;
}

/**
 * The RFP-only mid-pipeline gate: module_feature is done, awaiting the
 * user's "Approve & generate user stories" click. The backend reports this
 * with the same generic "ready_for_review" status as the run's true final
 * stage — checking the stage key is the only way left to tell them apart.
 */
export function needsModuleFeatureApproval(run: IngestionItem): boolean {
  return run.stages[run.stages.length - 1] === "module_feature_ready_for_review";
}

export function getCompletedSegments(run: IngestionItem): number {
  const status = normalizeIngestionStatus(run.status);
  if (!isStagedRun(run)) {
    return status === "ready_for_review" || status === "completed"
      ? getTotalSegments(run)
      : 0;
  }
  // "completed" always means the whole run is finished. "ready_for_review"
  // is unambiguous terminal completion too, except at the RFP
  // module-feature-approval gate, which reports that same status
  // mid-pipeline — needsModuleFeatureApproval's stage-key check is what
  // tells that one apart.
  if (
    status === "completed" ||
    (status === "ready_for_review" && !needsModuleFeatureApproval(run))
  ) {
    return getTotalSegments(run);
  }
  return Math.max(run.stages.length - 1, 0);
}

/**
 * Success/failure breakdown shown beside the completed "extracting_requirements"
 * step for source_code runs — tot_modules_from_global_artifact is the total
 * modules the code-dependency analysis attempted requirement extraction for;
 * tot_modules is how many actually succeeded. Null for any other stage/run.
 */
export function getExtractingRequirementsSubtitle(
  run: IngestionItem,
  stageKey: string,
): string | null {
  if (stageKey !== "extracting_requirements" || !isCodeRun(run)) return null;
  const total = run.tot_modules_from_global_artifact;
  const succeeded = run.tot_modules;
  const failed = total - succeeded;
  return `complete (Successful: ${succeeded}/${total}, Failed: ${failed}/${total})`;
}

/** Whether segment `index` is the one actively in progress / awaiting action right now (the "warning" colored one). */
export function isSegmentCurrent(
  run: IngestionItem,
  index: number,
  status: string,
  isDone: boolean,
): boolean {
  if (isDone) return false;
  if (!isStagedRun(run)) return status === "running";
  // getCompletedSegments already marks this segment "done" (so we never
  // reach here) for a genuine "ready_for_review" terminal state — what's
  // left is either "running", the module-feature-approval gate (both
  // genuinely pending), or a dead-stopped run (failed/cancelled), which has
  // nothing "current" to show.
  if (status === "failed" || status === "cancelled") return false;
  return index === run.stages.length - 1;
}

/**
 * A failed run carries its backend failure messages in `errors`. The field is
 * absent on every non-failed run, and can come back null/empty on a failed one
 * (older runs, or a failure the backend recorded no message for), so the
 * "Failure reason" action only renders when there is something to actually show.
 */
export function hasFailureReason(run: IngestionItem): boolean {
  return (
    normalizeIngestionStatus(run.status) === "failed" &&
    !!run.errors &&
    run.errors.length > 0
  );
}
