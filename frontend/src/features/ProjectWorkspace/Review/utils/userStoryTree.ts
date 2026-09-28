import type {
  FeatureNode,
  ModuleNode,
  StoryNode,
} from "@/components/common/TreePanel";
import type {
  StoryFilter,
  UpdateFilter,
} from "@/features/ProjectWorkspace/Review/components/UserStoryTreeControls";
import type { ReviewStage } from "@/features/ProjectWorkspace/Review/stage";
import type { ChangeType, ModuleTreeItem } from "@/types/user-story";
import { getEffectiveChangeType } from "@/utils/changeType";

export type TreeNode = StoryNode | FeatureNode | ModuleNode;

export const normalizeStatus = (status?: string) =>
  (status ?? "").trim().toLowerCase().replace(/\s+/g, "_");

export const isApprovableStatus = (status?: string) =>
  normalizeStatus(status) === "ready";

export const isAttentionStatus = (status?: string) => {
  const normalized = normalizeStatus(status);
  return normalized === "needs_edit" || normalized === "failed";
};

export const isApprovedStatus = (status?: string) => {
  const normalized = normalizeStatus(status);
  return normalized.includes("approved") || normalized.includes("locked");
};

export const getStoryCategory = (
  status?: string,
): Exclude<StoryFilter, "all"> => {
  const normalized = normalizeStatus(status);

  if (
    normalized === "ready" ||
    normalized === "verified" ||
    normalized === "done" ||
    normalized === "success" ||
    normalized.startsWith("pass")
  ) {
    return "ready";
  }

  return "attention";
};

export const hasChange = (action?: ChangeType) =>
  action === "ADDED" || action === "UPDATED" || action === "DELETE_SUGGESTED";

export const matchesUpdateFilter = (
  filter: UpdateFilter,
  action?: ChangeType,
) => {
  if (filter === "all") return hasChange(action);
  if (filter === "new") return action === "ADDED";
  if (filter === "modified") return action === "UPDATED";
  return action === "DELETE_SUGGESTED";
};

// When the "ingestion_id" URL query param is present, the Updates view (count,
// per-type breakdown, and the filtered tree) narrows to changes produced by
// that one source ingestion run — every module/feature/story change carries
// its own source_ingestion_id. Absent the param, every change counts, same as
// before this scoping existed.
export const matchesIngestion = (
  node: { source_ingestion_id?: string },
  ingestionId?: string,
) => !ingestionId || node.source_ingestion_id === ingestionId;

export const countStoriesByStatusCategory = (
  treeData: ModuleNode[],
): { attention: number; ready: number; approved: number } => {
  const counts = { attention: 0, ready: 0, approved: 0 };

  for (const mod of treeData) {
    for (const feat of mod.children ?? []) {
      for (const story of feat.children ?? []) {
        if (isApprovedStatus(story.status)) {
          counts.approved += 1;
        } else if (getStoryCategory(story.status) === "ready") {
          counts.ready += 1;
        } else if (isAttentionStatus(story.status)) {
          counts.attention += 1;
        }
      }
    }
  }

  return counts;
};

export const countTreeChanges = (
  tree: ModuleNode[],
  ingestionId?: string,
): number => {
  let count = 0;
  for (const mod of tree) {
    if (hasChange(getEffectiveChangeType(mod)) && matchesIngestion(mod, ingestionId))
      count += 1;
    for (const feat of mod.children ?? []) {
      if (
        hasChange(getEffectiveChangeType(feat)) &&
        matchesIngestion(feat, ingestionId)
      )
        count += 1;
      for (const story of feat.children ?? []) {
        if (
          hasChange(getEffectiveChangeType(story)) &&
          matchesIngestion(story, ingestionId)
        )
          count += 1;
      }
    }
  }
  return count;
};

// True when any module/feature/story in the tree carries a pending
// feedback-driven change (feedback_change_type) — distinct from
// incremental_change_type, which is AI-driven and doesn't gate this.
export const hasAnyFeedbackChangeType = (tree: ModuleNode[]): boolean => {
  for (const mod of tree) {
    if (mod.feedback_change_type) return true;
    for (const feat of mod.children ?? []) {
      if (feat.feedback_change_type) return true;
      for (const story of feat.children ?? []) {
        if (story.feedback_change_type) return true;
      }
    }
  }
  return false;
};

export const countChangesByType = (
  tree: ModuleNode[],
  ingestionId?: string,
): { new: number; modified: number; removed: number } => {
  const counts = { new: 0, modified: 0, removed: 0 };
  const tally = (node: TreeNode) => {
    if (!matchesIngestion(node, ingestionId)) return;
    const changeType = getEffectiveChangeType(node);
    if (changeType === "ADDED") counts.new += 1;
    else if (changeType === "UPDATED") counts.modified += 1;
    else if (changeType === "DELETE_SUGGESTED") counts.removed += 1;
  };

  for (const mod of tree) {
    tally(mod);
    for (const feat of mod.children ?? []) {
      tally(feat);
      for (const story of feat.children ?? []) {
        tally(story);
      }
    }
  }

  return counts;
};

export const mapTreeData = (items: ModuleTreeItem[]): ModuleNode[] =>
  items.map((mod) => ({
    id: mod.id,
    mod_code: mod.mod_code,
    name: mod.name,
    description: mod.description,
    type: "module" as const,
    incremental_change_type: mod.incremental_change_type,
    feedback_change_type: mod.feedback_change_type,
    source_ingestion_id: mod.source_ingestion_id,
    children: mod.children?.map((feat) => ({
      id: feat.id,
      fea_code: feat.fea_code,
      name: feat.name,
      description: feat.description,
      type: "feature" as const,
      incremental_change_type: feat.incremental_change_type,
      feedback_change_type: feat.feedback_change_type,
      source_ingestion_id: feat.source_ingestion_id,
      children: feat.children?.map((story) => ({
        id: story.id,
        user_story_code: story.user_story_code,
        name: story.name,
        status: story.status,
        type: "story" as const,
        incremental_change_type: story.incremental_change_type,
        feedback_change_type: story.feedback_change_type,
        source_ingestion_id: story.source_ingestion_id,
      })),
    })),
  }));

interface FilterTreeByStoryFilterOptions {
  stage: ReviewStage;
  approvedOnly: boolean;
  storyFilter: StoryFilter;
}

// At stage "first" the API returns modules/features with no user stories
// yet, so there's nothing to filter — show the tree as-is instead of pruning
// every feature/module down to empty.
export const filterTreeByStoryFilter = (
  treeData: ModuleNode[],
  { stage, approvedOnly, storyFilter }: FilterTreeByStoryFilterOptions,
): ModuleNode[] => {
  if (stage === "first" && !approvedOnly) {
    return treeData;
  }

  return treeData
    .map((mod) => {
      // Requirements page (approvedOnly): a pending incremental change must
      // be resolved on the Review page first, so hide the whole
      // module/feature/story subtree until incremental_change_type (or
      // feedback_change_type) clears.
      if (approvedOnly && getEffectiveChangeType(mod)) return null;

      const features = (mod.children ?? [])
        .map((feat) => {
          if (approvedOnly && getEffectiveChangeType(feat)) return null;

          const stories = (feat.children ?? []).filter((story) => {
            if (approvedOnly && getEffectiveChangeType(story)) return false;

            const approved = isApprovedStatus(story.status);
            if (approvedOnly) return approved;
            if (storyFilter === "all") return true;
            if (storyFilter === "approved") return approved;
            if (approved) return false;
            return getStoryCategory(story.status) === storyFilter;
          });

          if (stories.length === 0) return null;
          return { ...feat, children: stories };
        })
        .filter((feat): feat is NonNullable<typeof feat> => feat !== null);

      if (features.length === 0) return null;
      return { ...mod, children: features };
    })
    .filter((mod): mod is NonNullable<typeof mod> => mod !== null);
};

// The "Updates" view ignores the baseline status filter entirely (it has its
// own All/New/Modified/Removed filter) — only approvedOnly scoping
// (Requirements page) carries over. Same pending-change exclusion as
// filterTreeByStoryFilter — the Requirements page never surfaces unresolved
// incremental changes.
export const filterTreeByApprovedScope = (
  treeData: ModuleNode[],
  approvedOnly: boolean,
): ModuleNode[] => {
  if (!approvedOnly) return treeData;

  return treeData
    .map((mod) => {
      if (getEffectiveChangeType(mod)) return null;

      const features = (mod.children ?? [])
        .map((feat) => {
          if (getEffectiveChangeType(feat)) return null;

          const stories = (feat.children ?? []).filter(
            (story) =>
              !getEffectiveChangeType(story) && isApprovedStatus(story.status),
          );

          if (stories.length === 0) return null;
          return { ...feat, children: stories };
        })
        .filter((feat): feat is NonNullable<typeof feat> => feat !== null);

      if (features.length === 0) return null;
      return { ...mod, children: features };
    })
    .filter((mod): mod is NonNullable<typeof mod> => mod !== null);
};

// Narrows down to modules/features/stories matching the selected change type
// (all/new/modified/removed). A matching module/feature keeps its full
// subtree (e.g. a newly created module implies its features/stories are new
// too); otherwise only the matching descendants survive. When ingestionId is
// set (from the "ingestion_id" URL param), a node must also belong to that
// source ingestion run to match.
export const filterTreeByUpdateFilter = (
  tree: ModuleNode[],
  updateFilter: UpdateFilter,
  ingestionId?: string,
): ModuleNode[] => {
  return tree
    .map((mod) => {
      const modMatches =
        matchesUpdateFilter(updateFilter, getEffectiveChangeType(mod)) &&
        matchesIngestion(mod, ingestionId);
      const features = (mod.children ?? [])
        .map((feat) => {
          const featMatches =
            matchesUpdateFilter(updateFilter, getEffectiveChangeType(feat)) &&
            matchesIngestion(feat, ingestionId);
          const stories = (feat.children ?? []).filter(
            (story) =>
              matchesUpdateFilter(updateFilter, getEffectiveChangeType(story)) &&
              matchesIngestion(story, ingestionId),
          );

          if (!featMatches && stories.length === 0) {
            return null;
          }

          return {
            ...feat,
            children: featMatches ? (feat.children ?? []) : stories,
          };
        })
        .filter((feat): feat is NonNullable<typeof feat> => feat !== null);

      if (!modMatches && features.length === 0) {
        return null;
      }

      return {
        ...mod,
        children: modMatches ? (mod.children ?? []) : features,
      };
    })
    .filter((mod): mod is NonNullable<typeof mod> => mod !== null);
};

export const findNodeById = (
  tree: ModuleNode[],
  id: string,
): TreeNode | null => {
  for (const mod of tree) {
    if (mod.id === id) return mod;
    for (const feat of mod?.children ?? []) {
      if (feat.id === id) return feat;
      for (const story of feat?.children ?? []) {
        if (story.id === id) return story;
      }
    }
  }
  return null;
};

export const findParentModuleIdByFeatureId = (
  tree: ModuleNode[],
  featureId?: string,
): string | null => {
  if (!featureId) {
    return null;
  }

  for (const mod of tree) {
    if ((mod.children ?? []).some((feat) => feat.id === featureId)) {
      return mod.id;
    }
  }

  return null;
};

// When a story is selected directly (skipping its feature node), the parent
// feature's id is still needed to drive feature-scoped lookups (e.g.
// generation_metadata for ReviewGuidanceMessage).
export const findParentFeatureIdByStoryId = (
  tree: ModuleNode[],
  storyId?: string,
): string | null => {
  if (!storyId) {
    return null;
  }

  for (const mod of tree) {
    for (const feat of mod.children ?? []) {
      if ((feat.children ?? []).some((story) => story.id === storyId)) {
        return feat.id;
      }
    }
  }

  return null;
};

// A busy feature also locks its child stories — regenerating the feature
// rewrites the stories under it, so they can't be independently
// approved/deleted/given-feedback while that's in flight either.
export const computeEffectiveBusyStoryIds = (
  treeData: ModuleNode[],
  busyFeedbackStoryIds: ReadonlySet<string>,
  busyFeatureIds: ReadonlySet<string>,
): Set<string> => {
  const ids = new Set(busyFeedbackStoryIds);
  for (const featureId of busyFeatureIds) {
    const node = findNodeById(treeData, featureId);
    if (node && "children" in node) {
      for (const child of node.children ?? []) ids.add(child.id);
    }
  }
  return ids;
};

export const getDefaultSelectedId = (tree: ModuleNode[]): string => {
  if (!tree.length) return "";
  const firstModule = tree[0];
  const firstFeature = firstModule?.children?.[0];
  const firstStory = firstFeature?.children?.[0];
  return firstStory?.id ?? firstFeature?.id ?? firstModule.id;
};

// Keeps the current selection if it still exists in the (possibly re-filtered)
// tree; otherwise falls back to the tree's default selection.
export const resolveActiveSelectedId = (
  tree: ModuleNode[],
  selectedId: string,
  defaultSelectedId: string,
): string => {
  if (selectedId && findNodeById(tree, selectedId)) {
    return selectedId;
  }
  return defaultSelectedId;
};

export const flattenStoryIds = (tree: ModuleNode[]): string[] => {
  const ids: string[] = [];
  for (const mod of tree) {
    for (const feat of mod.children ?? []) {
      for (const story of feat.children ?? []) {
        ids.push(story.id);
      }
    }
  }
  return ids;
};

// Every module/feature/story id in tree (visual) order — used to pick a
// fallback selection when whichever of the three was selected disappears
// from the tree (e.g. rejecting an "ADDED" change or accepting a
// "DELETE_SUGGESTED" one deletes the entity outright).
export const flattenAllNodeIds = (tree: ModuleNode[]): string[] => {
  const ids: string[] = [];
  for (const mod of tree) {
    ids.push(mod.id);
    for (const feat of mod.children ?? []) {
      ids.push(feat.id);
      for (const story of feat.children ?? []) {
        ids.push(story.id);
      }
    }
  }
  return ids;
};

export const findStoryLabel = (
  treeItems: ModuleTreeItem[],
  userStoryId: string,
): string => {
  for (const mod of treeItems) {
    for (const feat of mod.children ?? []) {
      const story = (feat.children ?? []).find((s) => s.id === userStoryId);
      if (story) return story.name;
    }
  }
  return userStoryId;
};

export const findFeatureLabel = (
  treeItems: ModuleTreeItem[],
  featureId: string,
): string => {
  for (const mod of treeItems) {
    const feat = (mod.children ?? []).find((f) => f.id === featureId);
    if (feat) return feat.name;
  }
  return featureId;
};
