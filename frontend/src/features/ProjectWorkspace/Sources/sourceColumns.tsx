import Badge from "@/components/common/Badge";
import { StatusChip } from "@/components/common/StatusChip";
import type { TableColumn } from "@/components/common/Table";
import type { Source } from "@/types";
import { getFileIcon } from "@/utils/getFileIcon";
import { getTypeLabel } from "@/utils/getTypeLabel";

/** Maps the backend ingestion status string to the SourceStatus union used by the table. */
export function normalizeIngestionStatus(status: string): Source["status"] {
  if (status === "completed") return "completed";
  if (status === "ready_for_review") return "ready_for_review";
  if (status === "failed") return "failed";
  if (status === "cancelled" || status === "canceled") return "cancelled";
  return "running"; // default
}

export type ProjectSourceTableRow = {
  id: string;
  sourceName: string[];
  type: string;
  size: string[];
  ingestedDate: string;
  status: string;
};

function isReviewable(status: string): boolean {
  const normalized = normalizeIngestionStatus(status);
  return normalized === "completed" || normalized === "ready_for_review";
}

function getCreatedByLabel(createdBy: Source["created_by"] | unknown): string {
  if (typeof createdBy === "string") return createdBy || "—";
  if (createdBy && typeof createdBy === "object" && "name" in createdBy) {
    const name = (createdBy as { name?: string | null }).name;
    return name || "—";
  }
  return "—";
}

interface SourceColumnHandlers {
  onReviewClick?: () => void;
}

export function createSourceColumns({
  onReviewClick,
}: SourceColumnHandlers = {}): TableColumn<ProjectSourceTableRow>[] {
  return [
    {
      key: "sourceName",
      label: "Ingested sources",
      render: (_value, row) => (
        <div className="flex flex-col gap-2">
          {row.sourceName.map((name, i) => (
            <span key={i} className="flex min-w-0 items-center gap-2">
              <img
                src={getFileIcon(name)}
                alt=""
                aria-hidden="true"
                className="h-7 w-7 shrink-0"
              />
              <span className="flex min-w-0 flex-col">
                <span className="min-w-0 truncate font-medium text-[var(--text-primary)]">
                  {name}
                </span>
                <span className="text-xs text-[var(--text-secondary)]">
                  {row.size[i]}
                </span>
              </span>
            </span>
          ))}
        </div>
      ),
    },
    {
      key: "ingestedDate",
      label: "Ingested Date",
      render: (_value, row) => (
        <span className="font-medium text-[var(--text-secondary)]">
          {row.ingestedDate}
        </span>
      ),
    },
    {
      key: "type",
      label: "Type",
      render: (_value, row) => (
        <Badge
          kind="chip"
          status="parsing"
          label={getTypeLabel(row.type)}
          showDot={false}
        />
      ),
    },
    {
      key: "status",
      label: "Status",
      render: (_value, row) => (
        <StatusChip status={normalizeIngestionStatus(row.status)} />
      ),
    },
    {
      key: "id",
      label: "",
      render: (_value, row) =>
        onReviewClick && isReviewable(row.status) ? (
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              onReviewClick();
            }}
            className="inline-flex h-8 items-center justify-center rounded-lg border border-transparent bg-[var(--accent-600)] px-3 text-[13px] font-semibold text-white transition-colors duration-200 hover:brightness-95"
          >
            Review →
          </button>
        ) : null,
    },
  ];
}

export function getProjectSourceContribution(
  createdBy: Source["created_by"] | unknown,
): string {
  return getCreatedByLabel(createdBy);
}

export function getSourceTypeLabel(source: Source): string {
  const typedSource = source as Source & { source_type?: string };
  return typedSource.source_type || typedSource.source_type || "—";
}
