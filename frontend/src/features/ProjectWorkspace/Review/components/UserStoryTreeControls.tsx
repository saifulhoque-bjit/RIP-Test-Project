import type { ChangeEvent } from "react";
import type { ReviewStage } from "@/features/ProjectWorkspace/Review/stage";

export type StoryFilter = "attention" | "ready" | "approved" | "all";
export type UpdateFilter = "all" | "new" | "modified" | "removed";

interface UserStoryTreeControlsProps {
  stage: ReviewStage;
  hasUserStories: boolean;
  /** "updates" swaps the status chips below for change-type chips (All/New/Modified/Removed). */
  viewMode: "baseline" | "updates";
  storyFilter: StoryFilter;
  onStoryFilterChange: (filter: StoryFilter) => void;
  updateFilter: UpdateFilter;
  onUpdateFilterChange: (filter: UpdateFilter) => void;
  onTreeQueryChange: (query: string) => void;
  counts: {
    attention: number;
    ready: number;
    approved: number;
  };
  updateCounts: {
    new: number;
    modified: number;
    removed: number;
  };
  /** When false, only the search input is shown — no status filter chips. */
  enableFilters?: boolean;
}

export default function UserStoryTreeControls({
  stage,
  hasUserStories,
  viewMode,
  storyFilter,
  onStoryFilterChange,
  updateFilter,
  onUpdateFilterChange,
  onTreeQueryChange,
  counts,
  updateCounts,
  enableFilters = true,
}: UserStoryTreeControlsProps) {
  const isUpdatesView = viewMode === "updates";

  const statusFilterChips: {
    key: StoryFilter;
    label: string;
    count: number | null;
  }[] = [
    {
      key: "attention",
      label: "⚠ Needs attention",
      count: counts.attention,
    },
    { key: "ready", label: "● Ready", count: counts.ready },
    { key: "approved", label: "✓ Approved", count: counts.approved },
    { key: "all", label: "All", count: null },
  ];

  const updateFilterChips: {
    key: UpdateFilter;
    label: string;
    count: number | null;
  }[] = [
    { key: "all", label: "All", count: null },
    { key: "new", label: "+ New", count: updateCounts.new },
    { key: "modified", label: "✎ Modified", count: updateCounts.modified },
    { key: "removed", label: "✕ Removed", count: updateCounts.removed },
  ];

  const handleQueryChange = (event: ChangeEvent<HTMLInputElement>) => {
    onTreeQueryChange(event.target.value);
  };

  return (
    <div className="flex flex-col gap-2 border-b border-[var(--border-primary)] p-3">
      <input
        placeholder="Filter nodes..."
        onChange={handleQueryChange}
        className="h-8 w-full rounded-[6px] border border-[var(--border-strong)] px-2.5 text-[13px] outline-none"
      />
      {enableFilters && (isUpdatesView || hasUserStories) && (
        <div className="flex flex-wrap gap-1" id="tfilters">
          {isUpdatesView
            ? updateFilterChips.map(({ key, label, count }) => {
                const isActive = updateFilter === key;

                return (
                  <button
                    key={key}
                    type="button"
                    onClick={() => onUpdateFilterChange(key)}
                    className={`rounded-[14px] border px-[9px] py-[3px] text-[10.5px] font-semibold transition-colors ${
                      isActive
                        ? "border-[var(--accent)] bg-[var(--accent-50)] text-[var(--navy-900)]"
                        : "border-[var(--border-strong)] bg-white text-[var(--text-secondary)] hover:border-[var(--accent)]"
                    }`}
                  >
                    {label}
                    {count !== null ? ` (${count})` : ""}
                  </button>
                );
              })
            : statusFilterChips.map(({ key, label, count }) => {
                const isActive = storyFilter === key;

                return (
                  <button
                    key={key}
                    type="button"
                    onClick={() => onStoryFilterChange(key)}
                    className={`rounded-[14px] border px-[9px] py-[3px] text-[10.5px] font-semibold transition-colors ${
                      isActive
                        ? "border-[var(--accent)] bg-[var(--accent-50)] text-[var(--navy-900)]"
                        : "border-[var(--border-strong)] bg-white text-[var(--text-secondary)] hover:border-[var(--accent)]"
                    }`}
                  >
                    {label}
                    {count !== null ? ` (${count})` : ""}
                  </button>
                );
              })}
        </div>
      )}
      {enableFilters && !isUpdatesView && stage === "second" && (
        <div className="flex flex-wrap gap-2.5 text-[10px] text-[var(--mut)]">
          <span style={{ color: "var(--info)" }}>●</span> ready{" "}
          <span style={{ color: "var(--warn)" }}>▲</span> needs edit{" "}
          <span style={{ color: "var(--error)" }}>■</span> failed
        </div>
      )}
    </div>
  );
}
