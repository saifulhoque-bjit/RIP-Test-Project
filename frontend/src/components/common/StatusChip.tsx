import { useGetEnumCatalogQuery } from "@/services/api/modules/settings";
import type { RunStatus } from "@/types";

// Label text is looked up by key from the enum catalog's
// source_ingestion_status at render time; the `label` here is only the
// fallback for before that catalog has loaded (colors/styling have no
// catalog equivalent, so they stay local).
const STATUS_MAP: Record<
  RunStatus,
  { containerCls: string; containerStyle: React.CSSProperties; dotStyle: React.CSSProperties; label: string }
> = {
  running: {
    containerCls: "text-[#C77700]",
    containerStyle: { backgroundColor: "#FBF0DD" },
    dotStyle: { backgroundColor: "#C77700" },
    label: "Running",
  },
  ready_for_review: {
    containerCls: "text-[#1570EF]",
    containerStyle: { backgroundColor: "#E7F1FE" },
    dotStyle: { backgroundColor: "#1570EF" },
    label: "Ready for Review",
  },
  completed: {
    containerCls: "text-[#0E9F6E]",
    containerStyle: { backgroundColor: "#E7F6EF" },
    dotStyle: { backgroundColor: "#0E9F6E" },
    label: "Completed",
  },
  failed: {
    containerCls: "text-[#D92D20]",
    containerStyle: { backgroundColor: "#FDECEA" },
    dotStyle: { backgroundColor: "#D92D20" },
    label: "Failed",
  },
  cancelled: {
    containerCls: "text-[#667085]",
    containerStyle: { backgroundColor: "#F2F4F7" },
    dotStyle: { backgroundColor: "#667085" },
    label: "Cancelled",
  },
};

export function StatusChip({ status }: { status: RunStatus }) {
  const { data: enumCatalog } = useGetEnumCatalogQuery();
  const { containerCls, containerStyle, dotStyle, label: fallbackLabel } =
    STATUS_MAP[status] ?? STATUS_MAP.failed; // Default to "failed" if status is not recognized
  const label = enumCatalog?.source_ingestion_status?.[status] ?? fallbackLabel;

  return (
    <span
      className={`inline-flex items-center gap-1.5 text-[11.5px] font-semibold px-2.5 py-[3px] rounded-full ${containerCls}`}
      style={containerStyle}
    >
      <span
        className="w-1.5 h-1.5 rounded-full flex-shrink-0"
        style={dotStyle}
      />
      {label}
    </span>
  );
}
