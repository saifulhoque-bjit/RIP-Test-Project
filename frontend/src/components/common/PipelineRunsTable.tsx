import type { TableColumn } from "@/components/common/Table";
import { StatusChip } from "@/components/common/StatusChip";
import { formatDuration } from "@/utils/formatDuration";
import type { PipelineRun, RunStage } from "@/types";

// ── Constants ────────────────────────────────────────────────────────────────

const RFP_STAGE_LABELS: Record<RunStage, string> = {
  module_feature: "Modules & Features",
  user_story: "User Stories",
};

const CODE_STAGE_LABEL = "UI / API Spec & Features & User Stories";

const SOURCE_LABEL: Record<PipelineRun["source_type"], string> = {
  rfp: "RFP",
  source_code: "CODE",
};

// ── Pure helpers ─────────────────────────────────────────────────────────────

export function isCodeRun(run: PipelineRun): boolean {
  return run.source_type === "source_code";
}

export function getSourceLabel(run: PipelineRun): string {
  return run.source_type ? SOURCE_LABEL[run.source_type] : "—";
}

export function getStageLabel(run: PipelineRun): string {
  if (isCodeRun(run)) {
    return `Stage 2 of 2 - ${CODE_STAGE_LABEL}`;
  }
  if (run.stages.length === 0) return "—";
  const currentStage = run.stages.length;
  const activeStage = run.stages[run.stages.length - 1];
  return `Stage ${currentStage} of 2 - ${RFP_STAGE_LABELS[activeStage]}`;
}

export function getCountsLabel(run: PipelineRun): string | null {
  if (run.tot_modules === 0 && run.tot_features === 0 && run.tot_user_stories === 0) {
    return null;
  }
  return `${run.tot_modules} Modules / ${run.tot_features} Features / ${run.tot_user_stories} User Stories`;
}

export function getCompletedSegments(run: PipelineRun): number {
  if (isCodeRun(run)) {
    return run.status === "ready_for_review" || run.status === "completed" ? 2 : 0;
  }
  if (run.status === "ready_for_review" || run.status === "completed") return run.stages.length;
  return Math.max(run.stages.length - 1, 0);
}

export function isReviewReady(run: PipelineRun): boolean {
  return run.status === "ready_for_review";
}

export function isCompleted(run: PipelineRun): boolean {
  return run.status === "completed";
}

export function getElapsedSeconds(run: PipelineRun, now: Date = new Date()): number {
  return Math.max(0, Math.floor((now.getTime() - new Date(run.created_at).getTime()) / 1000));
}

export function getElapsedLabel(run: PipelineRun): string {
  return isReviewReady(run) || isCompleted(run)
    ? "Completed"
    : formatDuration(getElapsedSeconds(run));
}

export function formatStarted(isoString: string): string {
  const date = new Date(isoString);
  const now = new Date();
  const isToday = date.toDateString() === now.toDateString();
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  const isYesterday = date.toDateString() === yesterday.toDateString();
  const time = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (isToday) return `today ${time}`;
  if (isYesterday) return `yesterday ${time}`;
  return `${date.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
}

// ── Stage cell ───────────────────────────────────────────────────────────────

function StageCell({ run, showProgress }: { run: PipelineRun; showProgress: boolean }) {
  const countsLabel = getCountsLabel(run);
  const completedCount = getCompletedSegments(run);
  const isRunningNow = run.status === "running";

  return (
    <div>
      {showProgress && (
        <div className="mb-1 flex items-center gap-[5px]">
          {Array.from({ length: 2 }).map((_, index) => {
            const isDone = index < completedCount;
            const isCurrent = isCodeRun(run)
              ? isRunningNow && !isDone
              : isRunningNow && index === run.stages.length - 1 && !isDone;
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
      )}
      <div
        className={
          showProgress
            ? "text-[11px] text-[var(--text-tertiary)]"
            : "text-[13px] text-[var(--text-secondary)]"
        }
      >
        {getStageLabel(run)}
      </div>
      {countsLabel && (
        <div className="mt-[2px] text-[11px] text-[var(--text-quaternary)]">{countsLabel}</div>
      )}
    </div>
  );
}

// ── Column factory ───────────────────────────────────────────────────────────

export interface PipelineColumnOptions {
  onReviewClick: (run: PipelineRun) => void;
  /** Dashboard widget shows this, the full Pipelines page doesn't. */
  showSourceColumn?: boolean;
  /** Full Pipelines page shows this, the Dashboard widget doesn't. */
  showStartedColumn?: boolean;
  /** Segmented progress bar in the Stage cell — Pipelines page only. */
  showProgressBar?: boolean;
  /** Solid button (Pipelines page) vs. plain text link (Dashboard widget). */
  reviewButtonVariant?: "solid" | "link";
}

export function createPipelineColumns({
  onReviewClick,
  showSourceColumn = false,
  showStartedColumn = false,
  showProgressBar = false,
  reviewButtonVariant = "link",
}: PipelineColumnOptions): TableColumn<PipelineRun>[] {
  const columns: TableColumn<PipelineRun>[] = [
    {
      key: "run_code",
      label: "Run",
      className: "min-w-[110px]",
      render: (_, run) => (
        <span className="font-semibold text-[var(--text-primary)]">{run.run_code}</span>
      ),
    },
    {
      key: "project_name",
      label: "Project",
      className: "min-w-[210px]",
      render: (_, run) => (
        <span className="text-[13px] font-medium text-[var(--text-primary)]">
          {run.project_name ?? "—"}
        </span>
      ),
    },
  ];

  if (showSourceColumn) {
    columns.push({
      key: "source_type",
      label: "Source",
      className: "min-w-[80px]",
      render: (_, run) => (
        <span className="text-[13px] text-[var(--text-secondary)]">{getSourceLabel(run)}</span>
      ),
    });
  }

  columns.push({
    key: "stages",
    label: "Stage",
    className: "min-w-[280px]",
    render: (_, run) => <StageCell run={run} showProgress={showProgressBar} />,
  });

  columns.push({
    key: "status",
    label: "Status",
    className: "min-w-[150px]",
    render: (_, run) => <StatusChip status={run.status} />,
  });

  if (showStartedColumn) {
    columns.push({
      key: "created_at",
      label: "Started",
      className: "min-w-[120px]",
      render: (_, run) => <span>{formatStarted(run.created_at)}</span>,
    });
  }

  columns.push({
    key: "elapsed",
    label: showStartedColumn ? "Elapsed" : "Elapsed",
    className: "min-w-[110px]",
    render: (_, run) => (
      <span className="text-[13px] text-[var(--text-secondary)]">{getElapsedLabel(run)}</span>
    ),
  });

  columns.push({
    key: "actions",
    label: "",
    className: reviewButtonVariant === "solid" ? "min-w-[250px]" : "min-w-[100px]",
    render: (_, run) => {
      if (isCompleted(run)) {
        if (reviewButtonVariant === "solid") {
          return (
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                onReviewClick(run);
              }}
              className="inline-flex h-8 items-center justify-center rounded-lg border border-transparent bg-[var(--accent-600)] px-3 text-[13px] font-semibold text-white transition-colors duration-200 hover:brightness-95"
            >
              Review →
            </button>
          );
        }
        return (
          <button
            type="button"
            className="text-[var(--accent)] font-semibold text-[13px] bg-transparent border-none cursor-pointer hover:underline"
            onClick={(event) => {
              event.stopPropagation();
              onReviewClick(run);
            }}
          >
            Review →
          </button>
        );
      }

      if (!isReviewReady(run)) {
        return reviewButtonVariant === "solid" ? (
          <span className="text-xs text-[var(--text-tertiary)]">-</span>
        ) : null;
      }

      if (reviewButtonVariant === "solid") {
        return (
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              onReviewClick(run);
            }}
            className="inline-flex h-8 items-center justify-center rounded-lg border border-transparent bg-[var(--accent-600)] px-3 text-[13px] font-semibold text-white transition-colors duration-200 hover:brightness-95"
          >
            Review →
          </button>
        );
      }

      return (
        <button
          type="button"
          className="text-[var(--accent)] font-semibold text-[13px] bg-transparent border-none cursor-pointer hover:underline"
          onClick={(event) => {
            event.stopPropagation();
            onReviewClick(run);
          }}
        >
          Review →
        </button>
      );
    },
  });

  return columns;
}