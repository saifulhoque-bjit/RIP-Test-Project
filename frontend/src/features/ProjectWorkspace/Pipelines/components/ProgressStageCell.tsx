import { normalizeIngestionStatus } from "@/features/ProjectWorkspace/Sources/sourceColumns";
import { useGetEnumCatalogQuery } from "@/services/api/modules/settings";
import type { IngestionItem } from "@/types";
import { useLatestProjectTask } from "@/hooks/useProjectTaskStatus";
import { TASK_TYPE, isTerminalTaskStatus } from "@/types/projectTask";
import {
  getCompletedSegments,
  getStageLabel,
  getTotalSegments,
  isFeedbackRun,
  isIncrementalUploadRun,
  isSegmentCurrent,
} from "@/features/ProjectWorkspace/Pipelines/utils/runStageProgress";

/** "incremental_update.ai_processing" -> "AI Processing", "incremental_update.queued" -> "Queued". */
function humanizeTaskStage(stage: string): string {
  const withoutPrefix = stage.includes(".") ? stage.slice(stage.indexOf(".") + 1) : stage;
  return withoutPrefix
    .split(/[._]/)
    .filter(Boolean)
    .map((word) => (word.toLowerCase() === "ai" ? "AI" : word[0].toUpperCase() + word.slice(1)))
    .join(" ");
}

function getCountsLabel(run: IngestionItem): string | null {
  if (isFeedbackRun(run)) {
    if (run.tot_user_stories === 0) return null;
    return `${run.tot_user_stories} user ${run.tot_user_stories === 1 ? "story" : "stories"} updated`;
  }

  // Added + modified + deleted, combined per entity type.
  const totalModules = run.tot_modules + run.tot_modules_updated + run.tot_modules_deleted;
  const totalFeatures = run.tot_features + run.tot_features_updated + run.tot_features_deleted;
  const totalUserStories =
    run.tot_user_stories + run.tot_user_stories_updated + run.tot_user_stories_deleted;

  if (totalModules === 0 && totalFeatures === 0 && totalUserStories === 0) {
    return null;
  }
  return `${totalModules} Modules / ${totalFeatures} Features / ${totalUserStories} User Stories`;
}

export function ProgressStageCell({ run }: { run: IngestionItem }) {
  const { data: enumCatalog } = useGetEnumCatalogQuery();
  const stageLabels = enumCatalog?.source_ingestion_stage ?? {};
  const countsLabel = getCountsLabel(run);
  const completedCount = getCompletedSegments(run);
  const status = normalizeIngestionStatus(run.status);
  const isRunningNow = status === "running";
  const totalSegments = getTotalSegments(run);

  // Sources-page incremental-upload runs (meeting_notes, requirement_update,
  // additional_rfp) only report "generating_requirements" in the ingestion
  // row itself while running — show the live incremental_update task's own
  // stage (queued / started / ai_processing / ...) instead, straight from
  // the project WebSocket, so the message actually reflects what's happening.
  const liveIncrementalTask = useLatestProjectTask(
    run.project_id,
    TASK_TYPE.INCREMENTAL_UPDATE,
  );
  const liveStage =
    isIncrementalUploadRun(run) &&
    isRunningNow &&
    liveIncrementalTask &&
    !isTerminalTaskStatus(liveIncrementalTask.status) &&
    liveIncrementalTask.stage
      ? `Stage ${run.stages.length || 1} of ${getTotalSegments(run)} - ${humanizeTaskStage(liveIncrementalTask.stage)}`
      : null;
  const stageLabel = liveStage ?? getStageLabel(run, stageLabels);

  return (
    <div>
      <div className="mb-1 flex items-center gap-[5px]">
        {Array.from({ length: totalSegments }).map((_, index) => {
          const isDone = index < completedCount;
          const isCurrent = isSegmentCurrent(run, index, status, isDone);
          return (
            <span
              key={index}
              className={[
                "h-[5px] w-[26px] rounded-[3px]",
                isDone
                  ? "bg-[var(--success)]"
                  : isCurrent
                    ? "bg-[var(--warning)]"
                    : "bg-[#e4e8ef]",
              ].join(" ")}
            />
          );
        })}
      </div>
      <div className="text-[11px] text-[var(--text-tertiary)]">{stageLabel}</div>
      {countsLabel && (
        <div className="mt-[2px] text-[11px] text-[var(--text-quaternary)]">{countsLabel}</div>
      )}
    </div>
  );
}
