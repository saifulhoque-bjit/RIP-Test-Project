import type { TableColumn } from "@/components/common/Table";
import { StatusChip } from "@/components/common/StatusChip";
import { normalizeIngestionStatus } from "@/features/ProjectWorkspace/Sources/sourceColumns";
import { ProgressStageCell } from "./components/ProgressStageCell";
import { CancelPipelineButton } from "./components/CancelPipelineButton";
import { FailureReasonButton } from "./components/FailureReasonButton";
import {
  hasFailureReason,
  needsModuleFeatureApproval,
} from "./utils/runStageProgress";
import type { IngestionItem, Project } from "@/types";
import { formatDate } from "@/utils/formatDate";
import { getTypeLabel } from "@/utils/getTypeLabel";
import Button from "@/components/common/Button/Button";
import { ArrowNarrowRight } from "@/assets/icons/arrow/ArrowNarrowRight";

// Backend processing has finished (nothing left to generate) — whether
// there's still something to review depends on hasPendingApproval below.
// Also true at the RFP module-feature-approval gate (same generic
// "ready_for_review" status) — the OR below with needsModuleFeatureApproval
// still shows the Review button there regardless.
function isDoneProcessing(run: IngestionItem): boolean {
  const status = normalizeIngestionStatus(run.status);
  return status === "completed" || status === "ready_for_review";
}

// Once every user story in the project is already approved, no row's Review
// button has anywhere useful left to send the user — they'd land on a
// Review tab with nothing pending. Fails open (shows the button) if the
// project hasn't loaded yet, rather than risk hiding it incorrectly.
function hasPendingApproval(project?: Project): boolean {
  if (!project) return true;
  const total = project.user_stories ?? 0;
  const approved = project.approved_user_stories ?? 0;
  return total === 0 || approved < total;
}

interface PipelineColumnHandlers {
  onReviewClick: (run: IngestionItem) => void;
  project?: Project;
}

/** Pipelines page table: Run, Type, Progress / Stage, Status, Started, actions. */
export function createPipelineRunColumns({
  onReviewClick,
  project,
}: PipelineColumnHandlers): TableColumn<IngestionItem>[] {
  return [
    {
      key: "run_code",
      label: "Run",
      className: "min-w-[110px]",
      render: (_, run) => (
        <span className="font-semibold text-[var(--text-primary)]">
          {run.run_code}
        </span>
      ),
    },
    {
      key: "source_type",
      label: "Type",
      className: "min-w-[130px]",
      render: (_, run) => (
        <span className="whitespace-nowrap text-[13px] text-[var(--text-secondary)]">
          {getTypeLabel(run.source_type)}
        </span>
      ),
    },
    {
      key: "stages",
      label: "Progress",
      className: "min-w-[280px]",
      render: (_, run) => <ProgressStageCell run={run} />,
    },
    {
      key: "status",
      label: "Status",
      className: "min-w-[150px]",
      render: (_, run) => (
        <StatusChip status={normalizeIngestionStatus(run.status)} />
      ),
    },
    {
      key: "started_at",
      label: "Started",
      className: "min-w-[120px]",
      render: (_, run) => (
        <span>{formatDate(run.started_at ?? run.created_at)}</span>
      ),
    },
    {
      key: "id",
      label: "",
      className: "min-w-[250px]",
      render: (_, run) => {
        // A failed run has nothing to cancel and nothing to review; the only
        // useful action left is reading why it failed.
        if (hasFailureReason(run)) {
          return <FailureReasonButton run={run} />;
        }
        const isCompleted =
          normalizeIngestionStatus(run.status) === "completed";
        if (
          !isCompleted &&
          (needsModuleFeatureApproval(run) ||
            (isDoneProcessing(run) && hasPendingApproval(project)))
        ) {
          return (
            <Button
              type="button"
              size="xs"
              iconTrailing={<ArrowNarrowRight className="w-3.5 h-3.5" />}
              onClick={(event) => {
                event.stopPropagation();
                onReviewClick(run);
              }}
            >
              Review
            </Button>
          );
        }
        return <CancelPipelineButton run={run} />;
      },
    },
  ];
}
