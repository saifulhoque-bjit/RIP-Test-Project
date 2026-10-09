import { useMemo, useState } from "react";
import { toast } from "@/lib/toast";

import Modal from "@/components/common/Modal";
import Button from "@/components/common/Button/Button";
import { Chip } from "@/components/common/Chip";
import { InfoNote } from "@/components/common/Note";
import { Switch } from "@/components/ui/switch";
import { useAppDispatch } from "@/store/hooks";
import { baseApi } from "@/services/api/baseApi";
import {
  useGetSyncCandidatesQuery,
  useLazyGetSyncStoryDetailQuery,
  useExecuteJiraSyncMutation,
  type JiraSyncModule,
  type JiraSyncPayload,
} from "@/services/api/modules/jiraSync";
import { useExecuteTapSyncMutation } from "@/services/api/modules/tapSync";
import { describeSyncFailure } from "./syncOutcome";
import { calculateSyncGroup, type SyncGroup } from "./syncGroup";

export type SyncTarget = "jira" | "tap";

const TARGET_LABEL: Record<SyncTarget, string> = {
  jira: "Jira",
  tap: "TAP",
};

interface SyncItem {
  /** user story uuid — stable key for hold-toggling. */
  storyId: string;
  /** user_story_code — display label (e.g. "US-1.1.1"). */
  code: string;
  title: string;
  /** e.g. "v1", "v2" — read directly from the sync-candidates tree. */
  version: string;
  group: SyncGroup;
  held: boolean;
}

export function SyncTray({
  projectId,
  projectName,
  target,
  isOpen,
  onClose,
}: {
  projectId: string;
  projectName: string;
  target: SyncTarget;
  isOpen: boolean;
  onClose: () => void;
}) {
  const targetLabel = TARGET_LABEL[target];
  const dispatch = useAppDispatch();

  /**
   * Always invalidate the tree cache and the project detail when the tray
   * closes — not just on a successful sync's own invalidatesTags — so the
   * SubHeader's Jira/TAP badge (derived from the project's jira_synced_count/
   * tap_synced_count), the tray's own list, and the project detail cache are
   * guaranteed to refetch fresh counts, regardless of how the tray was closed.
   */
  const handleClose = () => {
    dispatch(
      baseApi.util.invalidateTags([
        { type: "Requirement", id: `TREE-${projectId}` },
        { type: "Requirement", id: `SYNC-${target}-${projectId}` },
        { type: "Project", id: projectId },
      ]),
    );
    onClose();
  };

  const { data, isFetching, isError } = useGetSyncCandidatesQuery(
    { projectId, target },
    {
      skip: !isOpen || !projectId,
    },
  );

  const [fetchStoryDetail] = useLazyGetSyncStoryDetailQuery();
  const [executeJiraSync] = useExecuteJiraSyncMutation();
  const [executeTapSync] = useExecuteTapSyncMutation();

  const [heldIds, setHeldIds] = useState<Set<string>>(new Set());
  const [releasing, setReleasing] = useState(false);

  // The backend already returns only approved, not-yet-synced-to-`target`
  // stories, version included — just flatten the tree, no client-side
  // status/sync filtering and no per-story detail fetch for display.
  const approvedStories = useMemo(() => {
    const modules = data?.data?.items ?? [];
    const stories: {
      id: string;
      user_story_code: string;
      name: string;
      version: number;
      deleted_at?: string | null;
    }[] = [];
    for (const mod of modules) {
      for (const feature of mod.children ?? []) {
        for (const story of feature.children ?? []) {
          stories.push(story);
        }
      }
    }
    return stories;
  }, [data]);

  const items: SyncItem[] = useMemo(
    () =>
      approvedStories.map((story) => ({
        storyId: story.id,
        code: story.user_story_code,
        title: story.name,
        version: `v${story.version}`,
        group: calculateSyncGroup({
          version: story.version,
          deletedAt: story.deleted_at,
        }),
        held: heldIds.has(story.id),
      })),
    [approvedStories, heldIds],
  );

  const toggleHold = (storyId: string) => {
    setHeldIds((prev) => {
      const next = new Set(prev);
      if (next.has(storyId)) {
        next.delete(storyId);
      } else {
        next.add(storyId);
      }
      return next;
    });
  };

  const counts = useMemo(() => {
    const active = items.filter((i) => !i.held);
    return {
      new: active.filter((i) => i.group === "new").length,
      changed: active.filter((i) => i.group === "changed").length,
      deprecated: active.filter((i) => i.group === "deprecated").length,
      total: active.length,
    };
  }, [items]);

  /**
   * Walks the module → feature → story tree (already pre-filtered to
   * approved, not-yet-synced stories by the sync-candidates endpoint),
   * keeping only non-held stories, and fetches each kept story's full detail
   * (as_a/i_want_to/so_that/acceptance_criteria/nfrs/story_points — not on
   * the tree) to assemble the full sync payload. Shared by both Jira and
   * TAP — only what's done with the assembled payload differs (see
   * handleRelease).
   */
  const buildSyncPayload = async (): Promise<JiraSyncPayload> => {
    const modules = data?.data?.items ?? [];
    const resultModules: JiraSyncModule[] = [];

    for (const mod of modules) {
      const resultFeatures: JiraSyncModule["features"] = [];

      for (const feature of mod.children ?? []) {
        const activeApprovedStories = (feature.children ?? []).filter(
          (story) => !heldIds.has(story.id),
        );
        if (activeApprovedStories.length === 0) continue;

        const details = await Promise.all(
          activeApprovedStories.map((story) =>
            fetchStoryDetail({ projectId, requirementId: story.id }).unwrap(),
          ),
        );

        resultFeatures.push({
          feature_id: feature.id,
          feature_code: feature.fea_code,
          feature_name: feature.name,
          feature_description: feature.description ?? "",
          user_stories: details.map(({ data: detail }) => ({
            user_story_id: detail.id,
            user_story_code: detail.user_story_code,
            title: detail.title,
            as_a: detail.as_a,
            i_want_to: detail.i_want_to,
            so_that: detail.so_that,
            story_points: detail.story_points,
            version_id: parseInt(detail.version, 10),
            is_deleted: !!detail.deleted_at,
            test_type: "",
            acceptance_criteria: detail.acceptance_criteria.map((ac) => ({
              type: ac.type,
              given: ac.given,
              when: ac.when,
              then: ac.then,
            })),
            nfrs: (detail.nfrs ?? []).map((nfr) => ({
              category: nfr.category,
              requirement: nfr.requirement,
              description: nfr.description,
            })),
          })),
        });
      }

      if (resultFeatures.length === 0) continue;

      resultModules.push({
        module_id: mod.id,
        module_code: mod.mod_code,
        module_name: mod.name,
        module_description: mod.description ?? "",
        features: resultFeatures,
      });
    }

    return {
      project_id: projectId,
      project_name: projectName,
      modules: resultModules,
    };
  };

  const handleRelease = async () => {
    setReleasing(true);
    try {
      const payload = await buildSyncPayload();

      if (payload.modules.length === 0) {
        toast.info(`Nothing to sync — all approved stories are on hold.`);
        return;
      }

      if (target === "tap") {
        const result = await executeTapSync({
          projectId,
          modules: payload.modules,
        }).unwrap();

        // A 2xx response here only means staging succeeded — TAP itself may
        // still have rejected the notify (e.g. unknown project, unreachable).
        // That failure lands in `errors`, not as a thrown error, so it must
        // be checked explicitly rather than treated as a clean success.
        if (result.errors.length > 0) {
          toast.warning(
            result.message ||
              "Data was staged, but TAP could not be notified. It will remain available for TAP to pull once reachable.",
          );
        } else {
          toast.success(
            result.message ||
              "Sync started — TAP will begin processing shortly.",
          );
        }
        return;
      }

      // Only { modules } goes to the backend — project_id/project_name are
      // for this payload's own bookkeeping and aren't accepted by the API
      // (project_id is already in the URL; the schema forbids extra fields).
      const result = await executeJiraSync({
        projectId,
        modules: payload.modules,
      }).unwrap();

      // The backend's created/updated/deprecated counters span modules +
      // features + user stories together — filter down to user_story-only
      // counts for display, per the requested summary.
      const storyCreated = result.created_items.filter(
        (i) => i.rip_entity_type === "user_story",
      ).length;
      const storyUpdated = result.updated_items.filter(
        (i) => i.rip_entity_type === "user_story",
      ).length;
      const storyDeprecated = result.deprecated_items.filter(
        (i) => i.rip_entity_type === "user_story",
      ).length;
      const storyTotal = storyCreated + storyUpdated + storyDeprecated;

      toast.success(
        `Synced ${storyTotal} user stor${storyTotal === 1 ? "y" : "ies"} to Jira — ${storyCreated} created, ${storyUpdated} updated, ${storyDeprecated} deprecated.`,
      );
      if (result.errors.length > 0) {
        toast.warning(
          `${result.errors.length} item(s) failed to sync — check the sync history for details.`,
        );
      }
    } catch (error) {
      const failure = describeSyncFailure(error, targetLabel);
      toast[failure.kind](failure.message);
    } finally {
      setReleasing(false);
      // Closed here rather than on each success path so a failed release
      // dismisses the tray too. The reason is always surfaced as a toast, and
      // leaving the modal up behind it stranded the user with a dialog whose
      // action had already been decided.
      handleClose();
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={handleClose}
      title={`Sync Tray — release to ${targetLabel}`}
      width={870}
      footer={
        <div className="flex gap-2.5">
          <Button
            variant="ghost"
            size="sm"
            onClick={handleClose}
            disabled={releasing}
            className="mr-auto"
          >
            Cancel
          </Button>
          <Button
            size="sm"
            onClick={() => void handleRelease()}
            disabled={counts.total === 0}
            loading={releasing}
          >
            {releasing ? "Syncing…" : `Sync ${counts.total} to ${targetLabel}`}
          </Button>
        </div>
      }
    >
      <div className="mb-3 flex items-center gap-2.5">
        <span className="text-xs text-mut">Target</span>
        <Chip tone="info">{targetLabel}</Chip>
        <span className="text-[11px] text-mut">
          selected from the workspace header
        </span>
      </div>

      <div className="mb-3.5 flex gap-6 rounded-md border border-[var(--border-primary)] bg-[#fafbfd] px-4 py-3">
        <Summary label="New" value={counts.new} tone="text-info" />
        <Summary
          label="Changed"
          value={counts.changed}
          tone="text-[var(--warn,var(--warning))]"
        />
        <Summary label="Deprecated" value={counts.deprecated} tone="text-mut" />
        <div className="ml-auto text-right">
          <div className="text-xs text-[var(--text-secondary)]">
            Will release
          </div>
          <div className="mt-0.5 text-[19px] font-bold">{counts.total}</div>
        </div>
      </div>

      {target === "jira" && (
        <InfoNote icon="🔒" className="mb-2">
          RIP updates only the fields it owns (title, acceptance criteria,
          traceability). Status, assignee, sprint, comments, and test links in
          Jira are never overwritten.
        </InfoNote>
      )}

      {isFetching ? (
        <div className="py-8 text-center text-[12.5px] text-mut">
          Loading approved user stories…
        </div>
      ) : isError ? (
        <div className="py-8 text-center text-[12.5px] text-[var(--error)]">
          Could not load approved user stories. Try again.
        </div>
      ) : items.length === 0 ? (
        <div className="py-8 text-center text-[12.5px] text-mut">
          No approved user stories yet — approve requirements in the Review tab
          to make them available for {targetLabel} sync.
        </div>
      ) : (
        <>
          <SyncGroupTable
            title="New"
            tone="info"
            items={items.filter((i) => i.group === "new")}
            onToggleHold={toggleHold}
          />
          <SyncGroupTable
            title="Changed"
            tone="warn"
            note="previously synced, content changed since last release"
            items={items.filter((i) => i.group === "changed")}
            onToggleHold={toggleHold}
          />
          <SyncGroupTable
            title="Deprecated"
            tone="neutral"
            note="marked as deleted"
            items={items.filter((i) => i.group === "deprecated")}
            onToggleHold={toggleHold}
          />
        </>
      )}
    </Modal>
  );
}

function Summary({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: string;
}) {
  return (
    <div className="text-xs text-[var(--text-secondary)]">
      {label}
      <b className={`mt-0.5 block text-[19px] ${tone}`}>{value}</b>
    </div>
  );
}

function SyncGroupTable({
  title,
  tone,
  note,
  items,
  onToggleHold,
}: {
  title: string;
  tone: "info" | "warn" | "neutral";
  note?: string;
  items: SyncItem[];
  onToggleHold: (id: string) => void;
}) {
  if (items.length === 0) return null;
  return (
    <>
      <div className="mt-4 mb-1.5 flex items-center gap-2 text-[11px] font-bold uppercase tracking-wide text-mut">
        {title} <Chip tone={tone}>{items.length}</Chip>
        {note && (
          <span className="font-normal normal-case tracking-normal text-mut">
            — {note}
          </span>
        )}
      </div>
      <div className="overflow-hidden rounded-md border border-[var(--border-primary)]">
        {/* `table-fixed` + this colgroup, NOT content-driven auto layout:
            New/Changed/Deprecated each render their own <table>, so with auto
            layout every group sizes its columns to its own longest title and
            the version/Hold columns land at a different x in each group. Fixed
            columns are shared geometry, so the three tables line up. */}
        <table className="w-full table-fixed border-collapse">
          <colgroup>
            {/* title — absorbs whatever the two fixed columns leave */}
            <col />
            <col className="w-[112px]" />
            <col className="w-[132px]" />
          </colgroup>
          <tbody>
            {items.map((item) => (
              <tr
                key={item.storyId}
                className="border-t border-[var(--border-primary)] first:border-t-0"
              >
                <td className="px-4 py-3 text-left text-[13px]">
                  <b>{item.code}</b> · {item.title}
                </td>
                <td className="px-4 py-3 text-right whitespace-nowrap">
                  <Chip tone="ok">{item.version}</Chip>
                </td>
                <td className="px-4 py-3 whitespace-nowrap">
                  <div className="flex items-center justify-end gap-2 text-xs text-mut">
                    <span>Hold</span>
                    <Switch
                      checked={item.held}
                      onCheckedChange={() => onToggleHold(item.storyId)}
                    />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export default SyncTray;
