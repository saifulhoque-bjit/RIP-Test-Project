import { useEffect, useMemo, useRef, useState } from "react";
import { CursorBoxIcon } from "@/assets/icons/CursorBoxIcon";
import { getFileIcon } from "@/utils/getFileIcon";
import { resolveSourceFileUrl } from "@/utils/resolveSourceFileUrl";
import SourceViewerModal from "./SourceViewerModal";
import type {
  RequirementDetailBbox,
  RequirementSource,
  SourceEvidenceItem,
} from "@/types/user-story";
import PdfViewer, { type PdfViewerRef } from "./PdfViewer";
import Button from "@/components/common/Button/Button";

interface PdfSourceProps {
  projectId?: string;
  userStoryId?: string;
  srcEvidence: SourceEvidenceItem | null;
  /** All PDF source evidence entries for this requirement — needed so a
   * grounding save can resend the full set (the backend PATCH replaces
   * `sources` wholesale; omitting entries here would delete them). */
  allSources?: RequirementSource[];
  isFullScreenOpen?: boolean;
  onFullScreenOpenChange?: (open: boolean) => void;
  /** When false, hides the full-screen viewer's grounding toolbar actions (e.g. on the /requirements route). */
  enableGroundingActions?: boolean;
  /** When true (user-story already approved), hides the "Adjust Grounding" action. */
  isApproved?: boolean;
}

export default function PdfSource({
  projectId,
  userStoryId,
  srcEvidence,
  allSources,
  isFullScreenOpen,
  onFullScreenOpenChange,
  enableGroundingActions = true,
  isApproved = false,
}: PdfSourceProps) {
  const [internalFullScreenView, setInternalFullScreenView] =
    useState<boolean>(false);
  const isFullScreenControlled = isFullScreenOpen !== undefined;
  const fullScreenView = isFullScreenControlled
    ? isFullScreenOpen
    : internalFullScreenView;

  const setFullScreenView = (value: boolean) => {
    if (!isFullScreenControlled) {
      setInternalFullScreenView(value);
    }
    onFullScreenOpenChange?.(value);
  };
  const [editedBboxDataBySourceKey, setEditedBboxDataBySourceKey] = useState<
    Record<string, RequirementDetailBbox[]>
  >({});
  const [selectedBboxIndex, setSelectedBboxIndex] = useState<number>(0);
  const [scrollToSelectedSignal, setScrollToSelectedSignal] =
    useState<number>(0);
  const sourceViewerRef = useRef<PdfViewerRef>(null);
  const [groundingState, setGroundingState] = useState<{
    adjustGrounding: boolean;
    isSavingBbox: boolean;
  }>({ adjustGrounding: false, isSavingBbox: false });

  const mapSourceToRequirementBboxes = (
    source: RequirementSource | undefined,
  ): RequirementDetailBbox[] => {
    if (!source || source.pages.length === 0) {
      return [];
    }

    return source.pages
      .map((page) => ({
        source_id: source.source_id,
        page: page.page,
        bboxes: page.bboxes.map((bboxItem) => ({
          x: bboxItem.bbox.x,
          y: bboxItem.bbox.y,
          w: bboxItem.bbox.w,
          h: bboxItem.bbox.h,
          fragment_id: bboxItem.fragment_id,
        })),
      }))
      .filter((page) => page.bboxes.length > 0);
  };

  /** Identifies the underlying PDF file only — used as the PdfViewer `key` so
   * switching between user stories that cite the same file doesn't remount
   * (and re-flicker) the PDF document. */
  const sourceFileKey = srcEvidence
    ? `${srcEvidence.sourceType}::${srcEvidence.sourceFileName}::${srcEvidence.sourceFilePath}`
    : "";

  /** Scopes the local edited-bbox cache to this specific user story. Without
   * the userStoryId, two different user stories citing the same PDF file
   * would share (and leak) each other's unsaved/just-saved grounding edits. */
  const sourceSelectionKey = srcEvidence
    ? `${userStoryId ?? ""}::${sourceFileKey}`
    : "";

  const resolvedFileUrl = useMemo(
    () =>
      resolveSourceFileUrl({
        sourceFilePath: srcEvidence?.sourceFilePath,
      }),
    [srcEvidence?.sourceFilePath],
  );

  const baseModalBboxData = useMemo(
    () => mapSourceToRequirementBboxes(srcEvidence?.source),
    [srcEvidence?.source],
  );

  const modalBboxData =
    editedBboxDataBySourceKey[sourceSelectionKey] ?? baseModalBboxData;

  const allBboxData = useMemo(
    () => (allSources ?? []).flatMap(mapSourceToRequirementBboxes),
    [allSources],
  );

  const handleCloseSourceViewer = () => {
    setFullScreenView(false);
  };

  const handleRequirementBboxUpdated = (
    updatedBboxData: RequirementDetailBbox,
  ) => {
    if (!sourceSelectionKey) return;

    setEditedBboxDataBySourceKey((prevByKey) => {
      const currentForSource =
        prevByKey[sourceSelectionKey] ?? baseModalBboxData;
      const activeSourceId = srcEvidence?.source?.source_id;
      if (!activeSourceId) return prevByKey;
      if (updatedBboxData.source_id !== activeSourceId) return prevByKey;

      const targetIndex = currentForSource.findIndex(
        (bbox) =>
          bbox.source_id === updatedBboxData.source_id &&
          bbox.page === updatedBboxData.page,
      );

      let nextForSource: RequirementDetailBbox[];
      if (targetIndex < 0) {
        nextForSource = [...currentForSource, updatedBboxData];
      } else {
        nextForSource = [...currentForSource];
        nextForSource[targetIndex] = updatedBboxData;
      }

      return {
        ...prevByKey,
        [sourceSelectionKey]: nextForSource,
      };
    });
  };

  useEffect(() => {
    if (!fullScreenView) return;

    const interval = setInterval(() => {
      if (sourceViewerRef.current) {
        setGroundingState({
          adjustGrounding: sourceViewerRef.current.adjustGrounding,
          isSavingBbox: sourceViewerRef.current.isSavingBbox,
        });
      }
    }, 100);

    return () => clearInterval(interval);
  }, [fullScreenView]);

  const bboxPageIndicators = useMemo(() => {
    if (modalBboxData.length === 0) return [];

    const indicators = modalBboxData.flatMap((bboxGroup, groupIndex) => {
      const hasMultipleBboxes = bboxGroup.bboxes.length > 1;

      return bboxGroup.bboxes.map((_, bboxIndex) => ({
        page: bboxGroup.page,
        bboxIndex,
        groupIndex,
        label: hasMultipleBboxes
          ? `Page ${bboxGroup.page}-BBOX ${bboxIndex + 1}`
          : `Page ${bboxGroup.page}`,
      }));
    });

    return indicators.map((indicator, globalIndex) => ({
      ...indicator,
      globalIndex,
    }));
  }, [modalBboxData]);

  const safeSelectedBboxIndex =
    selectedBboxIndex >= 0 && selectedBboxIndex < bboxPageIndicators.length
      ? selectedBboxIndex
      : 0;

  if (!srcEvidence) return null;
  return (
    <>
      {srcEvidence && (
        <div className="flex h-full flex-1 min-h-0">
          <PdfViewer
            key={`inline-${sourceFileKey}`}
            ref={sourceViewerRef}
            srcName={srcEvidence.sourceFileName}
            fileUrl={resolvedFileUrl}
            bboxData={modalBboxData}
            allBboxData={allBboxData}
            selectedBboxIndex={safeSelectedBboxIndex}
            selectedPage={
              bboxPageIndicators[safeSelectedBboxIndex]?.page ??
              modalBboxData[0]?.page ??
              1
            }
            projectId={projectId}
            requirementId={userStoryId}
            onRequirementBboxUpdated={handleRequirementBboxUpdated}
            scrollToSelectedSignal={scrollToSelectedSignal}
            // fullScreenIcon={true}
            fullScreenViewClickHandler={() => setFullScreenView(true)}
          />
        </div>
      )}

      {fullScreenView && (
        <SourceViewerModal
          isOpen={fullScreenView}
          onClose={handleCloseSourceViewer}
          fileName={srcEvidence.sourceFileName}
          fileIcon={
            <img
              src={getFileIcon(srcEvidence.sourceType, true)}
              alt="File Icon"
              className="w-4 h-4 inline-block"
            />
          }
          subtitle={
            bboxPageIndicators.length > 0 &&
            bboxPageIndicators.map((indicator) => (
              <button
                key={`${indicator.page}-${indicator.groupIndex}-${indicator.bboxIndex}`}
                onClick={() => {
                  setSelectedBboxIndex(indicator.globalIndex);
                  setScrollToSelectedSignal((prev) => prev + 1);
                }}
                className={`h-5.5 mr-2 inline-flex items-center text-[length:var(--font-size-xxsm)] px-2 rounded-[4px] cursor-pointer transition-colors ${
                  safeSelectedBboxIndex === indicator.globalIndex
                    ? "bg-[var(--color-brand-green-100)] text-[var(--color-brand-green-600)]"
                    : "bg-[var(--color-brand-green-50)] text-[var(--color-neutral-300)] hover:bg-[var(--color-brand-green-100)]"
                }`}
              >
                {indicator.label}
              </button>
            ))
          }
          toolbarActions={
            !enableGroundingActions ? undefined : groundingState.adjustGrounding ? (
              <>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    sourceViewerRef.current?.handleCancelAdjustGrounding()
                  }
                  disabled={groundingState.isSavingBbox}
                >
                  Cancel
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => sourceViewerRef.current?.handleSaveGrounding()}
                  disabled={groundingState.isSavingBbox}
                >
                  Save
                </Button>
              </>
            ) : isApproved ? undefined : (
              <Button
                variant="ghost"
                size="sm"
                iconLeading={<CursorBoxIcon />}
                disabled={bboxPageIndicators.length === 0}
                onClick={() =>
                  sourceViewerRef.current?.handleStartAdjustGrounding()
                }
              >
                Adjust Grounding
              </Button>
            )
          }
        >
          <div className="p-1 bg-[var(--bg-base2)] h-full overflow-hidden">
            <PdfViewer
              key={`modal-${sourceFileKey}`}
              ref={sourceViewerRef}
              srcName={srcEvidence.sourceFileName}
              fileUrl={resolvedFileUrl}
              bboxData={modalBboxData}
              allBboxData={allBboxData}
              selectedBboxIndex={safeSelectedBboxIndex}
              selectedPage={
                bboxPageIndicators[safeSelectedBboxIndex]?.page ??
                modalBboxData[0]?.page ??
                1
              }
              projectId={projectId}
              requirementId={userStoryId}
              onRequirementBboxUpdated={handleRequirementBboxUpdated}
              scrollToSelectedSignal={scrollToSelectedSignal}
            />
          </div>
        </SourceViewerModal>
      )}
    </>
  );
}
