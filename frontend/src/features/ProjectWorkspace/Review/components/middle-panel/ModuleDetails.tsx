import type { ModuleItem } from "@/components/common/TreePanel";
import { Button } from "@/components/common/Button/Button";
import type { ReviewStage } from "@/features/ProjectWorkspace/Review/stage";
import Card from "@/components/common/Card";
import type { ModuleDetailsData } from "@/types";
import DetailPanelHeader from "./DetailPanelHeader";
import ChangeDiffPanel, { type ChangeDiffVersion } from "./ChangeDiffPanel";
import { getEffectiveChangeType } from "@/utils/changeType";
import { ArrowNarrowRight } from "@/assets/icons/arrow/ArrowNarrowRight";

interface ModuleDetailsProps {
  selectedModule: ModuleItem | null;
  moduleDetail?: ModuleDetailsData | null;
  isLoadingDetail?: boolean;
  isDetailError?: boolean;
  setSelectedId: (id: string) => void;
  handleApproveModule: () => void | Promise<void>;
  isApproving?: boolean;
  canApprove?: boolean;
  stage?: ReviewStage;
  /** True while a modules-feedback regeneration task is in flight — shows a skeleton and blocks all actions. */
  isModuleRegenerating?: boolean;
}

export default function ModuleDetails({
  selectedModule,
  moduleDetail = null,
  isLoadingDetail = false,
  isDetailError = false,
  setSelectedId,
  handleApproveModule,
  isApproving = false,
  canApprove = true,
  stage,
  isModuleRegenerating = false,
}: ModuleDetailsProps) {
  if (!selectedModule) {
    return (
      <div className="flex h-full w-full items-center justify-center text-[var(--text-tertiary)]">
        No module selected.
      </div>
    );
  }

  if (isModuleRegenerating) {
    return (
      <div
        className="animate-pulse rounded-[0_12px_12px_0] border border-[var(--border-primary)] border-l-4 border-l-[var(--ai)] bg-white shadow-[var(--e1)]"
        id="ip-module"
        aria-busy="true"
        aria-label="Regenerating modules from feedback"
      >
        <div className="border-b border-[var(--border-primary)] px-[18px] py-4">
          <div className="h-4 w-1/2 rounded bg-[#e4e8ef]" />
        </div>
        <div className="flex flex-col gap-2.5 p-[16px_18px]">
          <div className="h-3 w-full rounded bg-[#e4e8ef]" />
          <div className="h-3 w-11/12 rounded bg-[#e4e8ef]" />
          <div className="h-3 w-3/4 rounded bg-[#e4e8ef]" />
        </div>
        <div className="flex flex-col gap-2 border-t-[2px] border-[var(--border-primary)] bg-[#fafbfd] px-[18px] py-3">
          <div className="h-3 w-40 rounded bg-[#e4e8ef]" />
          <p className="m-0 text-[11px] text-[var(--text-tertiary)]">
            Regenerating modules (and their features) from feedback…
          </p>
        </div>
      </div>
    );
  }

  const moduleDescription =
    moduleDetail?.description ?? selectedModule.description ?? "";

  const storyCount =
    moduleDetail?.total_user_stories ??
    selectedModule.children?.reduce(
      (acc, feat) => acc + (feat.children?.length ?? 0),
      0,
    ) ??
    0;

  const featureCount =
    moduleDetail?.total_features ?? selectedModule.children?.length ?? 0;

  const shouldShowPendingStageTwo = stage === "first" && storyCount === 0;

  const changedAction = getEffectiveChangeType(selectedModule);
  // `moduleDetail` always holds the module's own current attributes. For
  // "UPDATED", moduleDetail.last_previous_items additionally holds the full
  // prior-version snapshot, so the attribute-by-attribute before → after
  // table can be built from both. For "ADDED"/"DELETE_SUGGESTED" there's
  // nothing to diff against — moduleDetail alone (rendered as a single
  // success/error-tinted column) is the newly proposed or soon-to-be-removed
  // module.
  const attributeDiff =
    changedAction && moduleDetail
      ? {
          kind: "entity" as const,
          current: {
            name: moduleDetail.name,
            description: moduleDetail.description,
          },
          previous:
            changedAction === "UPDATED" && moduleDetail.last_previous_items
              ? {
                  name: moduleDetail.last_previous_items.name,
                  description: moduleDetail.last_previous_items.description,
                }
              : undefined,
        }
      : undefined;
  const currentVersion: ChangeDiffVersion | undefined = changedAction
    ? {
        label: "Current",
        narrative: moduleDetail?.name ?? selectedModule.name ?? "",
        lines: [moduleDescription].filter(Boolean),
      }
    : undefined;
  const previousVersion =
    changedAction === "DELETE_SUGGESTED" ? currentVersion : undefined;
  const proposedVersion =
    changedAction === "ADDED" ? currentVersion : undefined;

  return (
    <div
      id="ip-module"
      className="rounded-[0_12px_12px_0] border border-[var(--border-primary)] bg-white shadow-[var(--e1)]"
    >
      <DetailPanelHeader
        title={
          moduleDetail?.name ??
          selectedModule.name ??
          selectedModule.label ??
          ""
        }
        // code={moduleDetail?.mod_code ?? selectedModule.mod_code}
      />
      <div className="p-[16px_18px]">
        {changedAction ? (
          <ChangeDiffPanel
            changedAction={changedAction}
            previous={previousVersion}
            proposed={proposedVersion}
            attributeDiff={attributeDiff}
          />
        ) : (
          <>
            <p className="m-0 mb-2 text-[11px] font-bold uppercase tracking-[0.5px] text-[var(--text-tertiary)]">
              Module description
            </p>
            <Card
              className="p-[14px] !shadow-none text-[13.5px] leading-[1.6] text-[var(--text-secondary)]"
              id="mod-desc"
            >
              {isLoadingDetail
                ? "Loading module details..."
                : isDetailError
                  ? "Something went wrong! Unable to load module details."
                  : moduleDescription || "—"}
            </Card>
          </>
        )}
        <div className="my-[14px] grid grid-cols-2 gap-3.5">
          <Card className="p-[14px] !shadow-none">
            <div className="text-[11px] uppercase text-[var(--text-tertiary)]">
              Features
            </div>
            <div className="mt-1 text-[22px] font-bold" id="mov-f">
              {featureCount}
            </div>
          </Card>
          <Card className="p-[14px] !shadow-none">
            <div className="text-[11px] uppercase text-[var(--text-tertiary)]">
              Stories
            </div>
            <div
              className={
                shouldShowPendingStageTwo
                  ? "mt-1 text-[13px] font-semibold text-[var(--text-tertiary)]"
                  : "mt-1 text-[22px] font-bold"
              }
              id="mov-s"
            >
              {shouldShowPendingStageTwo
                ? "Awaiting Stage 2 (user stories not generated yet)"
                : storyCount}
            </div>
          </Card>
        </div>
        <p className="m-0 mb-2 text-[11px] font-bold uppercase tracking-[0.5px] text-[var(--text-tertiary)]">
          Mapped features
        </p>
        <div id="mod-feats">
          {selectedModule.children?.map((feat) => (
            <button
              key={feat.id}
              type="button"
              onClick={() => setSelectedId(feat.id)}
              className="mb-2 flex w-full cursor-pointer items-center justify-between rounded-lg border border-[var(--border-primary)] bg-white px-[14px] py-3 text-[13px] hover:border-[var(--border-strong)]"
            >
              <span>{feat.name}</span>
              <ArrowNarrowRight className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]" />
            </button>
          ))}
        </div>
        {canApprove && !changedAction && (
          <div
            className="w-full flex items-center justify-end"
            style={{ marginTop: "16px" }}
          >
            <Button
              size="xs"
              variant="success"
              onClick={handleApproveModule}
              loading={isApproving}
            >
              Approve
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
