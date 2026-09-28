import type { TableColumn } from "@/components/common/Table";
import type { PipelineRun } from "@/types";
import { createPipelineColumns } from "@/components/common/PipelineRunsTable";

interface DashboardColumnHandlers {
  onReviewClick: (run: PipelineRun) => void;
}

/** Compact Dashboard-widget table: Source column + text-link Review button. */
export function createDashboardPipelineColumns({
  onReviewClick,
}: DashboardColumnHandlers): TableColumn<PipelineRun>[] {
  return createPipelineColumns({
    onReviewClick,
    showSourceColumn: true,
    reviewButtonVariant: "link",
  });
}