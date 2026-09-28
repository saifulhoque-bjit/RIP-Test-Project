// import { useMemo, useState } from "react";
import { useMemo, useRef, useState } from "react";
import Button from "@/components/common/Button/Button";
import Modal from "@/components/common/Modal";
import type {
  AcceptanceCriterion,
  SourceEvidenceItem,
  RequirementDetailData,
  RequirementSource,
  RequirementSrsEvidence,
} from "@/types/user-story";
import type { FeatureDetails } from "@/types/feature";
import FileNameButton from "@/components/common/FileNameButton";
import PdfSource from "@/features/ProjectWorkspace/Review/components/right-panel/PdfSource";
import MarkdownViewer from "@/features/ProjectWorkspace/Review/components/right-panel/MarkdownViewer";
import MdEvidencePreview from "./MdEvidencePreview";
import EmptyState from "@/components/common/EmptyState/EmptyState";
import type { ReviewStage } from "@/features/ProjectWorkspace/Review/stage";
import { EditIcon } from "@/assets/icons/EditIcon";
import { getEffectiveChangeType } from "@/utils/changeType";

interface SrcEvidenceProps {
  stage: ReviewStage;
  selectedStory: RequirementDetailData | null;
  /** The selected (or story's parent) feature — used to fall back to the
   * feature's own cited source files when no user story is selected. */
  selectedFeature?: FeatureDetails | null;
  selectedAc?: AcceptanceCriterion | null;
  isLoading?: boolean;
  isError?: boolean;
  /** When false, hides the PDF full-SRS viewer's grounding toolbar actions (e.g. on the /requirements route). */
  enableGroundingActions?: boolean;
  /** Show the "Add feedback" affordance for all modules. */
  showModulesFeedbackBtn?: boolean;
  isProjectBusy?: boolean;
  onAddModulesFeedback?: () => void;
  /** True while a feedback-regeneration task is in flight for the selected story (or its parent feature) — shows a skeleton and hides evidence in the meantime. */
  isFeedbackRegenerating?: boolean;
}

interface ManualSourceSelection {
  sourceKey: string;
  acFingerprint: string;
}

function buildSourceKey(
  sourceType: string,
  sourceFileName: string,
  sourceFilePath: string,
): string {
  return `${sourceType}::${sourceFileName}::${sourceFilePath}`;
}

function toSourceKey(evidence: SourceEvidenceItem): string {
  return buildSourceKey(
    evidence.sourceType,
    evidence.sourceFileName,
    evidence.sourceFilePath,
  );
}

function buildEvidenceFromMatch(
  matchedEvidence: RequirementSrsEvidence,
  l2SourceRef: string,
): SourceEvidenceItem {
  return {
    sourceType: "md",
    sourceFileName: matchedEvidence.file_name || "",
    sourceFilePath: matchedEvidence.group_spec?.storage_key || "",
    sectionAnchor: matchedEvidence.section_anchor || null,
    highlightType: matchedEvidence.highlight_type || null,
    targetString: matchedEvidence.target_string || null,
    exactQuote: matchedEvidence.exact_quote || null,
    contextSnippet: matchedEvidence.context_snippet || null,
    lineNumber: matchedEvidence.line_number || null,
    acIds: matchedEvidence.ac_ids || null,
    l2_id: matchedEvidence.l2_id || null,
    precision: matchedEvidence.precision || null,
    l2SourceRef,
    isGapReference: false,
    isUnresolved: false,
    tier: matchedEvidence.tier,
  };
}

export default function SrcEvidence({
  stage,
  selectedStory,
  selectedFeature = null,
  selectedAc,
  isLoading = false,
  isError = false,
  enableGroundingActions = true,
  showModulesFeedbackBtn = false,
  isProjectBusy = false,
  onAddModulesFeedback,
  isFeedbackRegenerating = false,
}: SrcEvidenceProps) {
  const [manualSelection, setManualSelection] =
    useState<ManualSourceSelection | null>(null);
  const [isFullSrsOpen, setIsFullSrsOpen] = useState(false);

  /** Outer panel — its width comes purely from the 3-pane row layout, never
   * from anything scrollable inside it. */
  const panelRef = useRef<HTMLDivElement>(null);

  const effectiveAcceptanceCriterion =
    useMemo<AcceptanceCriterion | null>(() => {
      if (!selectedAc || !selectedStory) return null;

      const selectedAcCode = selectedAc.ac_code?.trim();
      if (!selectedAcCode) return null;

      const belongsToCurrentStory =
        selectedStory.acceptance_criteria?.some(
          (ac) => ac.ac_code?.trim() === selectedAcCode,
        ) ?? false;

      return belongsToCurrentStory ? selectedAc : null;
    }, [selectedAc, selectedStory]);

  const acCode = effectiveAcceptanceCriterion?.ac_code?.trim() ?? "";
  const l2SourceRef = effectiveAcceptanceCriterion?.l2_source_ref?.trim() ?? "";
  const isGapReference = l2SourceRef.startsWith("GAP::");

  const effectiveAcFingerprint = useMemo(
    () => `${acCode}::${l2SourceRef}`,
    [acCode, l2SourceRef],
  );

  // An AC is tied to SRS evidence by matching its l2_source_ref against each
  // evidence entry's l2_id. The same l2_id may appear in entries belonging to
  // more than one file, so this can resolve to several entries across files.
  const relatedEvidenceEntries = useMemo(() => {
    if (!selectedStory?.srs_evidence?.length || !l2SourceRef) {
      return [];
    }

    return selectedStory.srs_evidence.filter(
      (evidence) => evidence.l2_id === l2SourceRef,
    );
  }, [selectedStory, l2SourceRef]);

  // Null means "no AC-driven restriction" — every evidence chip stays
  // selectable (either no AC is selected, or it has no known related file).
  const relatedSourceKeys = useMemo(() => {
    if (relatedEvidenceEntries.length === 0) return null;

    const keys = new Set<string>();
    relatedEvidenceEntries.forEach((evidence) => {
      if (!evidence.file_name) return;
      keys.add(
        buildSourceKey(
          "md",
          evidence.file_name,
          evidence.group_spec?.storage_key || "",
        ),
      );
    });

    return keys.size > 0 ? keys : null;
  }, [relatedEvidenceEntries]);

  const srcEvidenceList = useMemo<SourceEvidenceItem[]>(() => {
    const evidenceList: SourceEvidenceItem[] = [];
    const seenFileNames = new Set<string>();
    const getFileNameKey = (fileName: string) => fileName.trim().toLowerCase();

    if (selectedStory) {
      if (
        selectedStory.sources?.length > 0 &&
        selectedStory.source_files?.length > 0
      ) {
        selectedStory.sources.forEach((source: RequirementSource) => {
          const sourceFile = selectedStory.source_files.find(
            (file) => file.id === source.source_id,
          );

          if (sourceFile?.type === "pdf") {
            evidenceList.push({
              sourceType: sourceFile.type,
              sourceFileName: sourceFile.name,
              sourceFilePath: sourceFile.storage_key,
              source,
            });
          }
        });
      }

      if (selectedStory.srs_evidence?.length > 0) {
        selectedStory.srs_evidence.forEach((evidence) => {
          const extension = evidence.file_name?.split(".").pop()?.toLowerCase();

          if (extension === "md" && evidence.file_name) {
            // Keep only one list chip per markdown file.
            const fileNameKey = getFileNameKey(evidence.file_name);
            if (seenFileNames.has(fileNameKey)) return;
            seenFileNames.add(fileNameKey);

            evidenceList.push({
              sourceType: "md",
              sourceFileName: evidence.file_name,
              sourceFilePath: evidence.group_spec?.storage_key || "",
            });
          }
        });
      }

      return evidenceList;
    }

    // No user story selected — fall back to the selected (or story's parent)
    // feature's own cited source files, but only for incremental changes
    // (ADDED/UPDATED/DELETE_SUGGESTED), where seeing what the change was
    // grounded in matters.
    if (
      getEffectiveChangeType(selectedFeature) &&
      selectedFeature?.source_files?.length
    ) {
      selectedFeature.source_files.forEach((file) => {
        if (!file.name) return;
        const fileNameKey = getFileNameKey(file.name);
        if (seenFileNames.has(fileNameKey)) return;
        seenFileNames.add(fileNameKey);

        const source = selectedFeature.sources?.find(
          (candidate) => candidate.source_id === file.id,
        );

        evidenceList.push({
          sourceType: file.type || "pdf",
          sourceFileName: file.name,
          sourceFilePath: file.storage_key,
          source,
        });
      });
    }

    return evidenceList;
  }, [selectedStory, selectedFeature]);

  const selectedSrcEvidence = useMemo<SourceEvidenceItem | null>(() => {
    // When an AC restricts the list to specific related files, default to
    // the first related chip (in list order); otherwise the first chip.
    const defaultSelection =
      (relatedSourceKeys
        ? srcEvidenceList.find((evidence) =>
            relatedSourceKeys.has(toSourceKey(evidence)),
          )
        : srcEvidenceList[0]) ?? null;

    const manualPick =
      manualSelection?.acFingerprint === effectiveAcFingerprint
        ? (srcEvidenceList.find(
            (evidence) => toSourceKey(evidence) === manualSelection.sourceKey,
          ) ?? null)
        : null;
    // A manual pick only wins if it isn't excluded by the current AC's
    // related-file restriction (unrelated chips are disabled anyway).
    const manualPickIsUsable =
      !!manualPick &&
      (!relatedSourceKeys || relatedSourceKeys.has(toSourceKey(manualPick)));

    const activeSelection = manualPickIsUsable ? manualPick : defaultSelection;

    if (!l2SourceRef) {
      if (!activeSelection) return null;
      return {
        ...activeSelection,
        isGapReference: false,
        isUnresolved: false,
        l2SourceRef: null,
      };
    }

    if (isGapReference) {
      if (!activeSelection) {
        return {
          sourceType: "md",
          sourceFileName: "",
          sourceFilePath: "",
          l2SourceRef,
          isGapReference: true,
          isUnresolved: false,
        };
      }

      return {
        ...activeSelection,
        sectionAnchor: null,
        highlightType: null,
        targetString: null,
        exactQuote: null,
        contextSnippet: null,
        lineNumber: null,
        acIds: null,
        l2_id: null,
        precision: null,
        l2SourceRef,
        isGapReference: true,
        isUnresolved: false,
        tier: undefined,
      };
    }

    // Resolve the specific evidence entry backing the active file, so
    // switching between an AC's related files keeps each one's own
    // highlight/quote metadata (preferring an exact l2_id match).
    const ownMatch = activeSelection
      ? (relatedEvidenceEntries.find(
          (evidence) =>
            evidence.file_name === activeSelection.sourceFileName &&
            evidence.l2_id === l2SourceRef,
        ) ??
        relatedEvidenceEntries.find(
          (evidence) => evidence.file_name === activeSelection.sourceFileName,
        ))
      : undefined;

    if (ownMatch) {
      return buildEvidenceFromMatch(ownMatch, l2SourceRef);
    }

    if (!activeSelection) {
      return {
        sourceType: "md",
        sourceFileName: "",
        sourceFilePath: "",
        l2SourceRef,
        isGapReference: false,
        isUnresolved: true,
      };
    }

    return {
      ...activeSelection,
      sectionAnchor: null,
      highlightType: null,
      targetString: null,
      exactQuote: null,
      contextSnippet: null,
      lineNumber: null,
      acIds: null,
      l2_id: null,
      precision: null,
      l2SourceRef,
      isGapReference: false,
      isUnresolved: true,
      tier: undefined,
    };
  }, [
    l2SourceRef,
    isGapReference,
    effectiveAcFingerprint,
    manualSelection,
    relatedEvidenceEntries,
    relatedSourceKeys,
    srcEvidenceList,
  ]);

  const handleViewSource = (evidence: SourceEvidenceItem) => {
    const evidenceKey = toSourceKey(evidence);
    // Switching between files related to the active AC keeps that AC
    // selected; switching outside any AC restriction clears the AC so the
    // panel goes back to free browsing.
    const isWithinAcRestriction =
      !!relatedSourceKeys && relatedSourceKeys.has(evidenceKey);

    setManualSelection({
      sourceKey: evidenceKey,
      acFingerprint: isWithinAcRestriction ? effectiveAcFingerprint : "::",
    });

    // if (!isWithinAcRestriction) {
    //   onClearSelectedAc?.();
    // }
  };

  const isMarkdownSelected = selectedSrcEvidence?.sourceType === "md";
  const isPdfSelected = selectedSrcEvidence?.sourceType === "pdf";
  // A GAP reference with a resolved file now renders the actual markdown
  // (see MdEvidencePreview/MarkdownViewer) instead of a bare "no anchor"
  // message, so it's not a negative state here — only an unresolved
  // reference (no file identified at all) still blocks the full viewer.
  const isNegativeMarkdownState = !!selectedSrcEvidence?.isUnresolved;
  const hasMarkdownPath = !!selectedSrcEvidence?.sourceFilePath?.trim();
  const canRenderFullMarkdownViewer =
    isMarkdownSelected && !isNegativeMarkdownState && hasMarkdownPath;
  const canOpenFullSrs = isMarkdownSelected || isPdfSelected;

  const handleOpenFullSrs = () => {
    if (!canOpenFullSrs) return;
    setIsFullSrsOpen(true);
  };

  if (isFeedbackRegenerating) {
    return (
      <div
        ref={panelRef}
        className="animate-pulse flex w-[34.98%] shrink-0 flex-col rounded-r-lg border-l border-[var(--border-primary)] bg-white"
        aria-busy="true"
        aria-label="Regenerating source evidence from feedback"
      >
        <div className="min-h-11 flex shrink-0 items-center gap-2 border-b border-[var(--border-primary)] px-2.5 py-2">
          <div className="h-6 w-24 rounded-full bg-[#e4e8ef]" />
          <div className="h-6 w-20 rounded-full bg-[#e4e8ef]" />
        </div>
        <div className="flex flex-col gap-2.5 p-[14px]">
          <div className="h-3 w-full rounded bg-[#e4e8ef]" />
          <div className="h-3 w-11/12 rounded bg-[#e4e8ef]" />
          <div className="h-3 w-3/4 rounded bg-[#e4e8ef]" />
          <div className="h-3 w-5/6 rounded bg-[#e4e8ef]" />
        </div>
        <div className="mt-auto flex flex-col gap-2 border-t-[2px] border-[var(--border-primary)] bg-[#fafbfd] px-[14px] py-3">
          <div className="h-3 w-40 rounded bg-[#e4e8ef]" />
          <p className="m-0 text-[11px] text-[var(--text-tertiary)]">
            Regenerating source evidence from feedback…
          </p>
        </div>
      </div>
    );
  }

  if (isLoading) {
    return (
      // <div className="flex w-[34.98%] shrink-0 flex-col rounded-r-lg border-l border-[var(--border-primary)] bg-white">
      <div
        ref={panelRef}
        className="flex w-[34.98%] shrink-0 flex-col rounded-r-lg border-l border-[var(--border-primary)] bg-white"
      >
        <div className="flex h-11 shrink-0 items-center border-b border-[var(--border-primary)] px-2.5 text-xs text-[var(--text-tertiary)]">
          Loading evidence...
        </div>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="flex w-[34.98%] shrink-0 items-center justify-center rounded-r-lg border-l border-[var(--border-primary)] bg-white p-4 text-sm text-red-500">
        Failed to load source evidence.
      </div>
    );
  }

  if (!selectedStory && srcEvidenceList.length === 0) {
    const isStageTwo = stage === "second";

    return (
      <div className="flex w-[34.98%] shrink-0 rounded-r-lg border-l border-[var(--border-primary)] bg-white">
        <div className="flex min-h-[50vh] h-full w-full px-5 flex-col items-center justify-center gap-2">
          {isStageTwo ? (
            <EmptyState
              title={
                isStageTwo
                  ? "No user story selected"
                  : "Source evidence will appear in Stage 2"
              }
              description={
                isStageTwo
                  ? "Please select a user story to view its source evidence."
                  : "No source evidence in Stage 1. Please wait for Stage 2 completion to view source evidence."
              }
            />
          ) : showModulesFeedbackBtn ? (
            <div className="flex flex-col items-center justify-center gap-5 py-5">
              <span className="w-[60%] text-xs font-semibold text-center text-[var(--text-tertiary)]">
                You can adjust or modify modules and features just by telling
                the AI what you want.
              </span>
              <Button
                variant="link"
                size="sm"
                disabled={isProjectBusy}
                iconLeading={<EditIcon className="w-3.5 h-3.5 scale-x-[-1]" />}
                onClick={onAddModulesFeedback}
              >
                Add feedback
              </Button>
            </div>
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <>
      <div
        ref={panelRef}
        className="flex w-[34.98%] shrink-0 flex-col rounded-r-lg border-l border-[var(--border-primary)] bg-white"
      >
        {/* ————— Evidence Header ———————————————————————————————————————— */}
        <div
          className="min-h-11 flex shrink-0 items-center justify-between gap-1 border-b border-[var(--border-primary)] px-2 py-1"
          id="evi-head"
        >
          <div className="min-h-7 inline-flex items-center justify-center cursor-pointer rounded-md text-xs font-semibold text-[var(--accent)]">
            {srcEvidenceList.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {srcEvidenceList.map((evidence, index) => (
                  <FileNameButton
                    key={`${evidence.sourceType}-${evidence.sourceFilePath}-${evidence.sourceFileName}-${index}`}
                    fileName={evidence.sourceFileName ?? "Unnamed File"}
                    isSelected={
                      selectedSrcEvidence?.sourceType === evidence.sourceType &&
                      selectedSrcEvidence?.sourceFileName ===
                        evidence.sourceFileName &&
                      selectedSrcEvidence?.sourceFilePath ===
                        evidence.sourceFilePath
                    }
                    disabled={
                      !!relatedSourceKeys &&
                      !relatedSourceKeys.has(toSourceKey(evidence))
                    }
                    onClick={() => handleViewSource(evidence)}
                  />
                ))}
              </div>
            )}
          </div>
          <Button
            className="flex-shrink-0"
            size="xxs"
            variant="ghost"
            disabled={!canOpenFullSrs}
            onClick={handleOpenFullSrs}
          >
            ⤢ Open full SRS
          </Button>
        </div>

        {/* ————— Evidence Preview ———————————————————————————————————————— */}
        <div className="flex-1 overflow-auto p-[14px]" id="evi-body">
          {!selectedSrcEvidence ? (
            <div className="w-full min-h-[50vh] inline-flex justify-center items-center text-sm text-[var(--text-tertiary)]">
              <EmptyState title="No evidence items available." description="" />
            </div>
          ) : selectedSrcEvidence.sourceType === "pdf" ? (
            <div className="h-full min-h-0">
              <PdfSource
                projectId={
                  selectedStory?.project_id || selectedFeature?.project_id || ""
                }
                userStoryId={selectedStory?.id || selectedFeature?.id || ""}
                srcEvidence={selectedSrcEvidence}
                allSources={
                  selectedStory?.sources ?? selectedFeature?.sources ?? []
                }
                isFullScreenOpen={isFullSrsOpen}
                onFullScreenOpenChange={setIsFullSrsOpen}
                enableGroundingActions={enableGroundingActions}
                isApproved={
                  selectedStory ? selectedStory.status === "approved" : true
                }
              />
            </div>
          ) : selectedSrcEvidence.sourceType === "md" ? (
            <MdEvidencePreview
              evidence={selectedSrcEvidence}
              activeAcceptanceCriteriaCode={
                effectiveAcceptanceCriterion?.ac_code ?? null
              }
            />
          ) : null}
        </div>
      </div>

      {/* ————— Full SRS Modal - MD File ———————————————————————————————————————— */}
      <Modal
        isOpen={isFullSrsOpen && isMarkdownSelected}
        onClose={() => setIsFullSrsOpen(false)}
        title={selectedSrcEvidence?.sourceFileName || "Full SRS"}
        width={1100}
        maxHeight="90vh"
      >
        {canRenderFullMarkdownViewer && selectedSrcEvidence ? (
          <div className="h-[70vh] min-h-0">
            <MarkdownViewer
              srcEvidence={selectedSrcEvidence}
              activeAcceptanceCriteriaCode={
                effectiveAcceptanceCriterion?.ac_code ?? null
              }
            />
          </div>
        ) : isMarkdownSelected && selectedSrcEvidence?.isGapReference ? (
          <div className="w-full min-h-[300px] inline-flex rounded-[8px] border border-[color:var(--color-warning-300)] bg-[color:var(--color-warning-50)] p-3 text-sm text-[color:var(--text-secondary)]">
            <EmptyState
              title="No full SRS location is available for this GAP reference."
              description=""
            />
          </div>
        ) : isMarkdownSelected && selectedSrcEvidence?.isUnresolved ? (
          <div className="rounded-[8px] border border-[color:var(--color-neutral-200)] bg-[color:var(--bg-base2)] p-3 text-sm text-[color:var(--text-secondary)]">
            Evidence was not pre-resolved for this reference. Open the source
            document manually to inspect.
          </div>
        ) : isMarkdownSelected && !hasMarkdownPath ? (
          <div className="rounded-[8px] border border-[color:var(--color-neutral-200)] bg-[color:var(--bg-base2)] p-3 text-sm text-[color:var(--text-secondary)]">
            Markdown source path is unavailable for full preview.
          </div>
        ) : (
          <div className="text-sm text-[var(--text-tertiary)]">
            Markdown source is not available for full preview.
          </div>
        )}
      </Modal>
    </>
  );
}
