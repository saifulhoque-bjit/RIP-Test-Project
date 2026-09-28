import { Panel } from "@/features/ProjectWorkspace/Overview/Panel";
import Button from "@/components/common/Button/Button";
import { StatusChip } from "@/components/common/StatusChip";
import { Timeline, type TimelineItem } from "@/components/common/Timeline";
import { normalizeIngestionStatus } from "@/features/ProjectWorkspace/Sources/sourceColumns";
import { useGetEnumCatalogQuery } from "@/services/api/modules/settings";
import type { IngestionItem } from "@/types";
import {
  getCompletedSegments,
  getExtractingRequirementsSubtitle,
  getSingleStageLabel,
  getStageSteps,
  isSegmentCurrent,
  needsModuleFeatureApproval,
} from "@/features/ProjectWorkspace/Pipelines/utils/runStageProgress";

/**
 * Journey for the run selected in the Pipeline Runs table. Steps and their
 * done/now/waiting coloring are derived from the same stage data and helpers
 * as that table's Progress column, so the two always show the same story.
 */
interface RunJourneyProps {
  run: IngestionItem;
  onReviewClick: (run: IngestionItem) => void;
}

export function RunJourney({ run, onReviewClick }: RunJourneyProps) {
  const { data: enumCatalog } = useGetEnumCatalogQuery();
  const stageLabels = enumCatalog?.source_ingestion_stage ?? {};
  const status = normalizeIngestionStatus(run.status);
  const isCompleted = status === "completed";
  const isModuleFeatureApprovalReady =
    !isCompleted && needsModuleFeatureApproval(run);
  const isReviewReady =
    status === "ready_for_review" && !isModuleFeatureApprovalReady;
  const completedCount = getCompletedSegments(run);

  const singleStageLabel = getSingleStageLabel(run);
  const stageSteps = singleStageLabel
    ? [{ key: "single", label: singleStageLabel }]
    : getStageSteps(run, stageLabels);

  // Built in real stage order (index 0 = first stage) since done/current
  // state depends on that ordering, then reversed for display so the
  // running/most recent stage renders at the top of the timeline.
  const items: TimelineItem[] = stageSteps.map((step, index) => {
    const isDone = index < completedCount;
    const isCurrent = isSegmentCurrent(run, index, status, isDone);
    const state = isDone ? "done" : isCurrent ? "now" : "wait";
    return {
      key: `${run.id}-${index}`,
      icon: state === "done" ? "✓" : state === "now" ? "•" : "",
      iconClassName:
        state === "done"
          ? "bg-[var(--success)] text-white"
          : state === "now"
            ? "bg-[var(--warning)] text-white"
            : "border-[1.5px] border-border-strong bg-surface text-mut",
      title: step.label,
      titleClassName: state === "wait" ? "font-normal text-mut" : undefined,
      connectorClassName: isDone
        ? "bg-[var(--success)]"
        : "bg-[var(--border-primary)]",
      subtitle:
        state === "done"
          ? (getExtractingRequirementsSubtitle(run, step.key) ?? "complete")
          : state === "now"
            ? "in progress"
            : "waiting",
      subtitleClassName: "capitalize",
    };
  });

  return (
    <Panel
      title={`${run.run_code} · JOURNEY`}
      aside={<StatusChip status={status} />}
    >
      <Timeline items={items} />

      {(isReviewReady || isModuleFeatureApprovalReady) && (
        <div className="mt-3 flex items-center justify-between gap-3 rounded-lg border border-[#a9e2cd] bg-success-50 px-3.5 py-3">
          <div className="text-[12.5px]">
            {isModuleFeatureApprovalReady ? (
              <>
                <b>Awaiting approval.</b> Modules & Features are generated —
                open the Review tab to approve and generate user stories.
              </>
            ) : (
              <>
                <b>Ready for review.</b> Requirements generated and grounded —
                open the Review tab to approve.
              </>
            )}
          </div>
          <Button
            variant="success"
            size="sm"
            onClick={() => onReviewClick(run)}
          >
            Open review →
          </Button>
        </div>
      )}
    </Panel>
  );
}
