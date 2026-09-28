import type {
  // AcceptanceCriterion,
  ChangeType,
  // FeatureFunctionItem,
  // NfrItem,
  // RequirementSource,
} from "@/types";
// import { useMemo, useState } from "react";
import { useMemo, useState } from "react";
import { cn } from "@/lib/cn";
import { getEffectiveChangeType } from "@/utils/changeType";
import EmptyState from "./EmptyState/EmptyState";

export type UserStoryStatus =
  | "ready"
  | "approved"
  | "needs_edit"
  | "needs-edit"
  | "failed";

export interface UserStoryItem {
  id: string;
  user_story_code?: string;
  fun_code?: string;
  name?: string;
  label?: string;
  description?: string;
  status?: UserStoryStatus;
  type?: "story";
  // Attributes to manage feedback/incremental changes
  incremental_change_type?: ChangeType;
  feedback_change_type?: ChangeType;
  source_ingestion_id?: string;
  // changed?: false;
  // changed_action?: ChangeType;
  // proposed_items?: {
  //   user_story_code: string;
  //   title: string;
  //   as_a: string;
  //   i_want_to: string;
  //   so_that: string;
  //   acceptance_criteria: AcceptanceCriterion[];
  //   sources: RequirementSource[];
  //   nfrs: NfrItem[];
  // };
}

export interface FeatureItem {
  id: string;
  fea_code?: string;
  name?: string;
  label?: string;
  description?: string;
  type?: "feature";
  children?: UserStoryItem[];
  kids?: UserStoryItem[];
  // Attributes to manage feedback/incremental changes
  incremental_change_type?: ChangeType;
  feedback_change_type?: ChangeType;
  source_ingestion_id?: string;
  // changed?: false;
  // changed_action?: ChangeType;
  // proposed_items?: {
  //   fea_code: string;
  //   name: string;
  //   description: string;
  //   functions: FeatureFunctionItem[];
  //   sources: RequirementSource[];
  // };
}

export interface ModuleItem {
  id: string;
  mod_code?: string;
  name?: string;
  label?: string;
  description?: string;
  type?: "module";
  children?: FeatureItem[];
  features?: FeatureItem[];
  kids?: FeatureItem[];
  // Attributes to manage feedback/incremental changes
  incremental_change_type?: ChangeType;
  feedback_change_type?: ChangeType;
  source_ingestion_id?: string;
  // changed?: false;
  // changed_action?: ChangeType;
  // proposed_items?: {
  //   mod_code: string;
  //   name: string;
  //   description: string;
  // };
}

export type TreeDataItem = ModuleItem | FeatureItem | UserStoryItem;

// Backward-compatible aliases used by current consumers.
export type StoryNode = UserStoryItem;
export type FeatureNode = FeatureItem;
export type ModuleNode = ModuleItem;

export interface ModuleTreeViewProps {
  data: ModuleItem[];
  onItemSelect?: (item: TreeDataItem | undefined) => void;
  initialSelectedItemId?: string;
  className?: string;
  expandAll?: boolean;
}

type TreePanelProps = Partial<ModuleTreeViewProps> & {
  tree?: ModuleItem[];
  query?: string;
  selectedId?: string;
  onSelect?: (id: string) => void;
};

const CHANGE_BADGE: Record<
  Exclude<ChangeType, null>,
  { cls: string; label: string; dot: string }
> = {
  ADDED: { cls: "new", label: "NEW", dot: "＋" },
  UPDATED: { cls: "modf", label: "MODIFIED", dot: "~" },
  DELETE_SUGGESTED: { cls: "rem", label: "REMOVED", dot: "−" },
};

const getLabel = (item: { name?: string; label?: string }) =>
  item.name ?? item.label ?? "";

const getFeatures = (mod: ModuleItem): FeatureItem[] =>
  mod.children ?? mod.features ?? mod.kids ?? [];

const getStories = (feat: FeatureItem): UserStoryItem[] =>
  feat.children ?? feat.kids ?? [];

const normalizeStoryStatus = (status?: UserStoryStatus) =>
  (status ?? "")
    .trim()
    .toLowerCase()
    .replace(/[\s-]+/g, "_");

const getFirstFeatureWithStoriesFromModules = (
  modules: ModuleItem[],
): string | null => {
  for (const mod of modules) {
    for (const feat of getFeatures(mod)) {
      if (getStories(feat).length > 0) {
        return feat.id;
      }
    }
  }
  return null;
};

const countStories = (feat: FeatureItem) => {
  const stories = getStories(feat);
  const total = stories.length;
  const approved = stories.filter((s) => s.status === "approved").length;
  return { total, approved };
};

const countModuleStories = (mod: ModuleItem) => {
  let total = 0;
  let approved = 0;
  for (const feat of getFeatures(mod)) {
    const c = countStories(feat);
    total += c.total;
    approved += c.approved;
  }
  return { total, approved };
};

function CountPill({ approved, total }: { approved: number; total: number }) {
  if (total === 0) return null;
  const done = approved === total;
  return (
    <span
      className={`ml-auto shrink-0 rounded-[10px] px-[7px] py-[1px] text-[10px] font-bold ${
        done
          ? "bg-[var(--success-50)] text-[var(--success)]"
          : "bg-[#eef1f5] text-[var(--text-secondary)]"
      }`}
    >
      {approved}/{total}
    </span>
  );
}

export default function TreePanel({
  data,
  tree,
  query = "",
  selectedId,
  onSelect,
  onItemSelect,
  initialSelectedItemId,
  className,
  expandAll = true,
}: TreePanelProps) {
  const sourceData = useMemo(() => data ?? tree ?? [], [data, tree]);
  const [internalSelectedId, setInternalSelectedId] = useState(
    initialSelectedItemId ?? "",
  );
  const activeSelectedId = selectedId ?? internalSelectedId;

  const [collapsedModules, setCollapsedModules] = useState<Set<string>>(
    () => new Set(),
  );
  const [hasModuleToggleInteraction, setHasModuleToggleInteraction] =
    useState(false);
  const [expandedFeatureIds, setExpandedFeatureIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [hasFeatureExpandInteraction, setHasFeatureExpandInteraction] =
    useState(false);

  const defaultCollapsedModules = useMemo(() => {
    if (expandAll === false) {
      return new Set(sourceData.map((mod) => mod.id));
    }

    // Keep only the first module expanded by default.
    return new Set(sourceData.slice(1).map((mod) => mod.id));
  }, [expandAll, sourceData]);

  const effectiveCollapsedModules = hasModuleToggleInteraction
    ? collapsedModules
    : defaultCollapsedModules;

  const defaultExpandedFeatureIds = useMemo(() => {
    const firstFeatureId = getFirstFeatureWithStoriesFromModules(sourceData);
    return firstFeatureId ? new Set([firstFeatureId]) : new Set<string>();
  }, [sourceData]);

  const effectiveExpandedFeatureIds = hasFeatureExpandInteraction
    ? expandedFeatureIds
    : defaultExpandedFeatureIds;

  // Reveal the selected item by expanding its containing module/feature.
  // Adjusted during render (React's "adjusting state when a prop changes"
  // pattern) rather than in an effect, to avoid a setState-in-effect cascade.
  const [prevSelectionSync, setPrevSelectionSync] = useState({
    selectedId,
    sourceData,
  });

  if (
    selectedId !== prevSelectionSync.selectedId ||
    sourceData !== prevSelectionSync.sourceData
  ) {
    setPrevSelectionSync({ selectedId, sourceData });

    if (selectedId) {
      for (const mod of sourceData) {
        const containingFeature = getFeatures(mod).find(
          (feat) =>
            feat.id === selectedId ||
            getStories(feat).some((story) => story.id === selectedId),
        );

        if (mod.id !== selectedId && !containingFeature) continue;

        if (effectiveCollapsedModules.has(mod.id)) {
          setHasModuleToggleInteraction(true);
          const next = new Set(effectiveCollapsedModules);
          next.delete(mod.id);
          setCollapsedModules(next);
        }

        if (
          containingFeature &&
          !effectiveExpandedFeatureIds.has(containingFeature.id)
        ) {
          setHasFeatureExpandInteraction(true);
          const next = new Set(effectiveExpandedFeatureIds);
          next.add(containingFeature.id);
          setExpandedFeatureIds(next);
        }
        break;
      }
    }
  }

  const filteredTree = useMemo(() => {
    if (!query.trim()) return sourceData;
    const q = query.toLowerCase();
    return sourceData
      .map((mod) => ({
        ...mod,
        children: getFeatures(mod)
          .map((feat) => ({
            ...feat,
            children: getStories(feat).filter((story) =>
              getLabel(story).toLowerCase().includes(q),
            ),
          }))
          .filter(
            (feat) =>
              getLabel(feat).toLowerCase().includes(q) ||
              (feat.children?.length ?? 0) > 0,
          ),
      }))
      .filter(
        (mod) =>
          getLabel(mod).toLowerCase().includes(q) ||
          (mod.children?.length ?? 0) > 0,
      );
  }, [query, sourceData]);

  const handleSelect = (item: TreeDataItem) => {
    if (selectedId === undefined) {
      setInternalSelectedId(item.id);
    }

    if (item.type === "feature") {
      setHasFeatureExpandInteraction(true);
      setExpandedFeatureIds((prev) => {
        const next = new Set(
          hasFeatureExpandInteraction ? prev : defaultExpandedFeatureIds,
        );
        next.add(item.id);
        return next;
      });
    }

    onSelect?.(item.id);
    onItemSelect?.(item);
  };

  console.log("filteredTree ", filteredTree);

  const content = (
    <>
      {filteredTree.length > 0 ? (
        filteredTree.map((mod) => (
          <div key={mod.id} className="mb-1">
            <div
              className={`flex items-center rounded-md ${
                activeSelectedId === mod.id
                  ? "bg-[var(--accent-50)] text-[var(--navy-900)]"
                  : "text-[var(--text-primary)]"
              }`}
            >
              <button
                type="button"
                aria-label={
                  !effectiveCollapsedModules.has(mod.id)
                    ? `Collapse ${getLabel(mod)}`
                    : `Expand ${getLabel(mod)}`
                }
                onClick={() => {
                  setHasModuleToggleInteraction(true);
                  setCollapsedModules(() => {
                    const next = new Set(effectiveCollapsedModules);
                    if (next.has(mod.id)) {
                      next.delete(mod.id);
                    } else {
                      next.add(mod.id);
                    }
                    return next;
                  });
                }}
                className={`inline-flex h-7 w-4 shrink-0 items-center justify-center rounded-md text-[10px] ${
                  activeSelectedId === mod.id
                    ? "text-[var(--navy-900)]"
                    : "text-[var(--text-tertiary)] hover:bg-[#fafbfd]"
                }`}
              >
                {!effectiveCollapsedModules.has(mod.id) ? "▼" : "▶"}
              </button>

              <button
                type="button"
                onClick={() => handleSelect(mod)}
                className={`flex w-full items-center gap-1.5 rounded-md px-1 py-2 text-left text-[13px] font-semibold ${
                  activeSelectedId === mod.id
                    ? "text-[var(--navy-900)]"
                    : "text-[var(--text-primary)] hover:bg-[#fafbfd]"
                }`}
              >
                {(() => {
                  const modChangeType = getEffectiveChangeType(mod);
                  return (
                    modChangeType && (
                      <span
                        className={cn(
                          "v3-cbadge shrink-0 whitespace-nowrap",
                          CHANGE_BADGE[modChangeType].cls,
                        )}
                      >
                        {CHANGE_BADGE[modChangeType].dot}{" "}
                        {CHANGE_BADGE[modChangeType].label}
                      </span>
                    )
                  );
                })()}
                <span>{getLabel(mod)}</span>
                {(() => {
                  const c = countModuleStories(mod);
                  return c.total > 0 ? (
                    <CountPill approved={c.approved} total={c.total} />
                  ) : null;
                })()}
              </button>
            </div>

            {!effectiveCollapsedModules.has(mod.id) && (
              <div className="pl-3">
                {getFeatures(mod).map((feat) => (
                  <div key={feat.id} className="mb-0.5">
                    <div
                      className={`flex items-center rounded-md ${
                        activeSelectedId === feat.id
                          ? "bg-[var(--accent-50)] text-[var(--navy-900)]"
                          : "text-[var(--text-primary)]"
                      }`}
                    >
                      {getStories(feat).length > 0 ? (
                        <button
                          type="button"
                          aria-label={
                            effectiveExpandedFeatureIds.has(feat.id)
                              ? `Collapse ${getLabel(feat)}`
                              : `Expand ${getLabel(feat)}`
                          }
                          onClick={() => {
                            setHasFeatureExpandInteraction(true);
                            setExpandedFeatureIds(() => {
                              const next = new Set(effectiveExpandedFeatureIds);
                              if (next.has(feat.id)) {
                                next.delete(feat.id);
                              } else {
                                next.add(feat.id);
                              }
                              return next;
                            });
                          }}
                          className={`inline-flex h-7 w-4 shrink-0 items-center justify-center rounded-md text-[10px] ${
                            activeSelectedId === feat.id
                              ? "text-[var(--navy-900)]"
                              : "text-[var(--text-tertiary)] hover:bg-[#fafbfd]"
                          }`}
                        >
                          {effectiveExpandedFeatureIds.has(feat.id) ? "▼" : "▶"}
                        </button>
                      ) : (
                        <span className="inline-flex h-7 w-4 shrink-0" />
                      )}

                      <button
                        type="button"
                        onClick={() => handleSelect(feat)}
                        className={`flex w-full items-center gap-1.5 rounded-md px-1 py-[7px] text-left text-[13px] ${
                          activeSelectedId === feat.id
                            ? "font-semibold text-[var(--navy-900)]"
                            : "text-[var(--text-primary)] hover:bg-[#fafbfd]"
                        }`}
                      >
                        {(() => {
                          const featChangeType = getEffectiveChangeType(feat);
                          return (
                            featChangeType && (
                              <span
                                className={cn(
                                  "v3-cbadge shrink-0 whitespace-nowrap",
                                  CHANGE_BADGE[featChangeType].cls,
                                )}
                              >
                                {CHANGE_BADGE[featChangeType].dot}{" "}
                                {CHANGE_BADGE[featChangeType].label}
                              </span>
                            )
                          );
                        })()}
                        <span>{getLabel(feat)}</span>
                        {(() => {
                          const c = countStories(feat);
                          return c.total > 0 ? (
                            <CountPill approved={c.approved} total={c.total} />
                          ) : null;
                        })()}
                      </button>
                    </div>

                    <div className="pl-3.5">
                      {effectiveExpandedFeatureIds.has(feat.id) &&
                        getStories(feat).map((story) => {
                          const storyChangeType = getEffectiveChangeType(story);
                          return (
                            <button
                              key={story.id}
                              type="button"
                              onClick={() => handleSelect(story)}
                              className={`mb-0.5 flex w-full items-center gap-[7px] rounded-md px-2 py-1.5 text-left text-[12.5px] ${
                                activeSelectedId === story.id
                                  ? "bg-[var(--accent-50)] font-semibold text-[var(--navy-900)]"
                                  : "text-[var(--text-secondary)] hover:bg-[#fafbfd]"
                              }`}
                            >
                              {/* If any change available show change badge */}
                              {/* otherwise show Ready/Approved/Failed/Need_Edit icon */}
                              {storyChangeType ? (
                                <span
                                  className={cn(
                                    "v3-cbadge shrink-0 whitespace-nowrap",
                                    CHANGE_BADGE[storyChangeType].cls,
                                  )}
                                >
                                  {CHANGE_BADGE[storyChangeType].dot}{" "}
                                  {CHANGE_BADGE[storyChangeType].label}
                                </span>
                              ) : (
                                <span
                                  className={`inline-flex h-4 w-4 shrink-0 items-center justify-center text-[11px] 
                            ${
                              normalizeStoryStatus(story.status) === "approved"
                                ? "text-[var(--success)]"
                                : normalizeStoryStatus(story.status) ===
                                    "needs_edit"
                                  ? "text-[var(--warn)]"
                                  : normalizeStoryStatus(story.status) ===
                                      "failed"
                                    ? "text-[var(--error)]"
                                    : "text-[var(--info)]"
                            }
                          `}
                                >
                                  {normalizeStoryStatus(story.status) ===
                                  "approved"
                                    ? "✓"
                                    : normalizeStoryStatus(story.status) ===
                                        "needs_edit"
                                      ? "▲"
                                      : normalizeStoryStatus(story.status) ===
                                          "failed"
                                        ? "■"
                                        : "●"}
                                </span>
                              )}
                              <span>{getLabel(story)}</span>
                            </button>
                          );
                        })}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))
      ) : (
        <EmptyState title="No items available." description="" />
      )}
    </>
  );

  if (className) {
    return <div className={className}>{content}</div>;
  }

  return content;
}
