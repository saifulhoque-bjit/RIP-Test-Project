import type { TableColumn } from "@/components/common/Table";
import { Chip } from "@/components/common/Chip";
import { formatDate } from "@/utils/formatDate";
import type { Project } from "@/types";
import { ArrowNarrowRight } from "@/assets/icons/arrow/ArrowNarrowRight";
import Button from "@/components/common/Button/Button";

export type ProjectRow = Project & {
  isProcessing: boolean;
  notStarted: boolean;
  approvedPct: number | null;
  pendingSync: number;
  isAllSynced: boolean;
};

interface ProjectColumnOptions {
  /** Hide the row-level "Open →" action, e.g. for super_admin read-only rows. */
  showOpenAction: boolean;
}

/** Dashboard "My projects" / "All projects" table: Project, Type, Last activity, Actions. */
export function createProjectListColumns({
  showOpenAction,
}: ProjectColumnOptions): TableColumn<ProjectRow>[] {
  return [
    {
      key: "name",
      label: "Project",
      render: (_value, row) => (
        <span className="text-[13.5px] font-semibold">{row.name}</span>
      ),
    },
    {
      key: "project_type",
      label: "Type",
      render: (_value, row) => (
        <div className="my-2 flex flex-wrap items-center gap-1.5">
          {row.project_type === "rfp" ? (
            <span className="inline-flex items-center h-[22px] text-[10.5px] font-semibold px-2.5 rounded-full bg-[var(--info-50)] text-[var(--info)]">
              RFP
            </span>
          ) : row.project_type === "source_code" ? (
            <span className="inline-flex items-center h-[22px] text-[10.5px] font-semibold px-2.5 rounded-full bg-[var(--ai-50)] text-[var(--ai-draft)]">
              Source · Code
            </span>
          ) : null}
        </div>
      ),
    },
    {
      key: "last_activity_at",
      label: "Last activity",
      render: (_value, row) =>
        row.last_activity_at ? formatDate(row.last_activity_at) : "—",
    },
    {
      key: "id",
      label: "Actions",
      align: "right",
      render: (_value, row) => (
        <div className="flex items-center justify-end gap-3">
          {row.isProcessing ? (
            <Chip tone="warn" dot>
              Processing
            </Chip>
          ) : row.notStarted ? (
            <Chip tone="off" dot>
              Not started
            </Chip>
          ) : (
            <>
              {row.approvedPct !== null && (
                <span className="text-[12px] text-sec">
                  {row.approvedPct}% approved
                </span>
              )}
              {row.pendingSync > 0 ? (
                <Chip tone="warn" dot>
                  {row.pendingSync} to sync
                </Chip>
              ) : row.isAllSynced ? (
                <Chip tone="ok" dot>
                  Synced
                </Chip>
              ) : (
                <Chip tone="info" dot>
                  Reviewing
                </Chip>
              )}
            </>
          )}
          {showOpenAction && (
            <Button
              size="xs"
              variant="link"
              iconTrailing={<ArrowNarrowRight className="w-3 h-3" />}
            >
              Open
            </Button>
          )}
        </div>
      ),
    },
  ];
}
