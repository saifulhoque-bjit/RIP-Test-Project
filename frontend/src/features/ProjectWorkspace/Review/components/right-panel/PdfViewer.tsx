import { useMemo, useState, useImperativeHandle, forwardRef } from "react";
import { pdfjs } from "react-pdf";
import { ZoomInIcon } from "@/assets/icons/ZoomInIcon";
import { ZoomOutIcon } from "@/assets/icons/ZoomOutIcon";
import type {
  RequirementFragment,
  RequirementFragmentBbox,
  RequirementDetailBbox,
  RequirementSource,
} from "@/types";
import { useUpdateSourceFragmentBboxMutation } from "@/services/api/modules/sources";
import { useUpdateRequirementBboxesMutation } from "@/services/api/modules/user-stories";
import { toast } from "@/lib/toast";
import PdfSourceViewer from "@/features/ProjectWorkspace/Review/components/right-panel/PdfSourceViewer";
import fullscreenIcon from "@/assets/icons/full-screen.svg";

interface PdfViewerProps {
  srcName: string;
  fileUrl?: string;
  fragment?: RequirementFragment;
  /** Bbox data from API (alternative to fragment) */
  bboxData?: RequirementDetailBbox[];
  /** Every source's bbox groups for this requirement (not just the one
   * currently open) — used to build a complete save payload. */
  allBboxData?: RequirementDetailBbox[];
  selectedPage: number;
  /** Index of bbox to display when page has multiple bboxes */
  selectedBboxIndex?: number;
  /** Required for updating user story bboxes */
  projectId?: string;
  requirementId?: string;
  /** Sync updated requirement bbox data back to parent modal state */
  onRequirementBboxUpdated?: (updatedBboxData: RequirementDetailBbox) => void;
  /** Bump this signal to force scrolling to selected bbox even when index is unchanged */
  scrollToSelectedSignal?: number;
  fullScreenIcon?: boolean;
  fullScreenViewClickHandler?: () => void;
}

export interface PdfViewerRef {
  adjustGrounding: boolean;
  isSavingBbox: boolean;
  handleStartAdjustGrounding: () => void;
  handleCancelAdjustGrounding: () => void;
  handleSaveGrounding: () => Promise<void>;
}

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

const cloneBbox = (
  bboxList: RequirementFragmentBbox[] | undefined,
): RequirementFragmentBbox[] =>
  (bboxList ?? []).map((item) => ({
    page: item.page,
    bbox: [...item.bbox],
  }));

const flattenRequirementBboxes = (
  bboxDataList: RequirementDetailBbox[] | undefined,
): RequirementFragmentBbox[] =>
  (bboxDataList ?? []).flatMap((bboxData) =>
    bboxData.bboxes.map((bbox) => ({
      page: bboxData.page,
      bbox: [bbox.x, bbox.y, bbox.w, bbox.h],
    })),
  );

const buildRequirementSourcesPayload = (
  bboxGroups: RequirementDetailBbox[],
): RequirementSource[] | null => {
  const sourcesById = new Map<string, RequirementSource>();

  for (const group of bboxGroups) {
    let sourceEntry = sourcesById.get(group.source_id);

    if (!sourceEntry) {
      sourceEntry = {
        source_id: group.source_id,
        pages: [],
      };
      sourcesById.set(group.source_id, sourceEntry);
    }

    const pageBboxes = group.bboxes.map((bboxItem) => {
      if (!bboxItem.fragment_id) {
        return undefined;
      }

      return {
        bbox: {
          x: bboxItem.x,
          y: bboxItem.y,
          w: bboxItem.w,
          h: bboxItem.h,
        },
        fragment_id: bboxItem.fragment_id,
      };
    });

    if (pageBboxes.some((bboxItem) => !bboxItem)) {
      return null;
    }

    sourceEntry.pages.push({
      page: group.page,
      bboxes: pageBboxes as RequirementSource["pages"][number]["bboxes"],
    });
  }

  return Array.from(sourcesById.values());
};

const PdfViewer = forwardRef<PdfViewerRef, PdfViewerProps>(
  (
    {
      srcName,
      fileUrl,
      fragment,
      bboxData,
      allBboxData = [],
      selectedPage,
      selectedBboxIndex = 0,
      projectId,
      requirementId,
      onRequirementBboxUpdated,
      scrollToSelectedSignal = 0,
      fullScreenIcon = false,
      fullScreenViewClickHandler,
    },
    ref,
  ) => {
    const [zoom, setZoom] = useState(1);
    //   const [isFullscreen, setIsFullscreen] = useState(false);
    const [adjustGrounding, setAdjustGrounding] = useState<boolean>(false);
    const [editableBboxState, setEditableBboxState] = useState<{
      fragmentId: string | undefined;
      bbox: RequirementFragmentBbox[];
    }>({ fragmentId: fragment?.id, bbox: cloneBbox(fragment?.bbox) });
    const [updateSourceFragmentBbox, { isLoading: isFragmentSaving }] =
      useUpdateSourceFragmentBboxMutation();
    const [updateRequirementBboxes, { isLoading: isRequirementSaving }] =
      useUpdateRequirementBboxesMutation();

    const isSavingBbox = isFragmentSaving || isRequirementSaving;

    // Get current bbox state - prioritize editable state during adjustment
    const currentBbox = useMemo(() => {
      // During adjustment, use the editable state
      if (adjustGrounding && editableBboxState.bbox.length > 0) {
        return editableBboxState.bbox;
      }

      // For fragments, use fragment bbox
      if (fragment?.bbox) {
        return cloneBbox(fragment.bbox);
      }

      // For user story bboxData, convert to bbox format
      if (bboxData && bboxData.length > 0) {
        return flattenRequirementBboxes(bboxData);
      }

      return [];
    }, [adjustGrounding, editableBboxState.bbox, fragment, bboxData]);

    const coordinates = useMemo(() => {
      if (currentBbox.length === 0) {
        return undefined;
      }

      return currentBbox.map((bbox) => ({
        page: bbox.page,
        bbox: [bbox.bbox[0], bbox.bbox[1], bbox.bbox[2], bbox.bbox[3]] as [
          number,
          number,
          number,
          number,
        ],
      }));
    }, [currentBbox]);

    const getSelectedRequirementBboxMeta = () => {
      if (!bboxData || bboxData.length === 0) {
        return undefined;
      }

      let runningIndex = 0;
      for (let groupIndex = 0; groupIndex < bboxData.length; groupIndex += 1) {
        const group = bboxData[groupIndex];
        for (
          let bboxIndex = 0;
          bboxIndex < group.bboxes.length;
          bboxIndex += 1
        ) {
          if (runningIndex === selectedBboxIndex) {
            return {
              groupIndex,
              bboxIndex,
            };
          }
          runningIndex += 1;
        }
      }

      return undefined;
    };

    const handleCoordinatesChange = (
      coords: [number, number, number, number],
      coordinateIndex: number,
    ) => {
      setEditableBboxState((prev) => {
        // Clone the current state
        const nextBbox = [...prev.bbox];
        const targetIndex = coordinateIndex;

        if (targetIndex >= 0 && nextBbox[targetIndex]) {
          nextBbox[targetIndex] = {
            ...nextBbox[targetIndex],
            bbox: [coords[0], coords[1], coords[2], coords[3]],
          };
        }

        return {
          fragmentId: prev.fragmentId,
          bbox: nextBbox,
        };
      });
    };

    const handleStartAdjustGrounding = () => {
      // Initialize from fragment if available
      if (fragment?.id) {
        setEditableBboxState({
          fragmentId: fragment.id,
          bbox: cloneBbox(fragment.bbox),
        });
      }
      // Initialize from bboxData if no fragment (keep all bboxes visible)
      else if (bboxData && bboxData.length > 0) {
        setEditableBboxState({
          fragmentId: undefined,
          bbox: flattenRequirementBboxes(bboxData),
        });
      }
      setAdjustGrounding(true);
    };

    const handleCancelAdjustGrounding = () => {
      // Reset to original state
      if (fragment?.id) {
        setEditableBboxState({
          fragmentId: fragment.id,
          bbox: cloneBbox(fragment.bbox),
        });
      } else if (bboxData && bboxData.length > 0) {
        setEditableBboxState({
          fragmentId: undefined,
          bbox: flattenRequirementBboxes(bboxData),
        });
      }
      setAdjustGrounding(false);
    };

    const handleSaveGrounding = async () => {
      // For fragments: use existing fragment API
      if (fragment?.id && fragment.source_id) {
        const bboxPayload = currentBbox
          .filter((item) => item.bbox.length === 4)
          .map((item) => ({
            page: item.page,
            bbox: item.bbox.map((value) => Number(value)),
          }));

        if (!bboxPayload.length) {
          toast.error("No bounding box data available to save.");
          return;
        }

        try {
          await updateSourceFragmentBbox({
            sourceId: fragment.source_id,
            fragmentId: fragment.id,
            bbox: bboxPayload,
          }).unwrap();

          toast.success("Grounding updated successfully.");
          setAdjustGrounding(false);
        } catch {
          toast.error("Failed to save grounding updates.");
        }
        return;
      }

      // For user story bboxes: use requirement bbox API
      if (bboxData && bboxData.length > 0 && projectId && requirementId) {
        if (currentBbox.length === 0) {
          toast.error("No bounding box data available to save.");
          return;
        }

        const selectedRequirementBboxMeta = getSelectedRequirementBboxMeta();
        if (!selectedRequirementBboxMeta) {
          toast.error("Invalid selected bounding box.");
          return;
        }

        const editedBbox = currentBbox[selectedBboxIndex];
        if (!editedBbox || editedBbox.bbox.length !== 4) {
          toast.error("Invalid bounding box data.");
          return;
        }

        const selectedGroup = bboxData[selectedRequirementBboxMeta.groupIndex];
        if (!selectedGroup) {
          toast.error("Invalid selected bounding box group.");
          return;
        }

        if (
          selectedRequirementBboxMeta.bboxIndex < 0 ||
          selectedRequirementBboxMeta.bboxIndex >= selectedGroup.bboxes.length
        ) {
          toast.error("Invalid selected bounding box.");
          return;
        }

        // Merge edited bbox back into original bboxes array at the correct index
        const updatedBboxes = selectedGroup.bboxes.map((bboxItem, index) =>
          index === selectedRequirementBboxMeta.bboxIndex
            ? {
                ...bboxItem,
                x: editedBbox.bbox[0],
                y: editedBbox.bbox[1],
                w: editedBbox.bbox[2],
                h: editedBbox.bbox[3],
              }
            : bboxItem,
        );

        // const updatedBboxData = bboxData.map((group, groupIndex) =>
        //   groupIndex === selectedRequirementBboxMeta.groupIndex
        //     ? {
        //         ...group,
        //         bboxes: updatedBboxes,
        //       }
        //     : group,
        // );

        // const sourcesPayload = buildRequirementSourcesPayload(updatedBboxData);

        // Apply the edit across the FULL evidence set for this requirement, not
        // just the one source currently open — the backend PATCH replaces
        // `sources` wholesale, so sending only the edited source would silently
        // delete every other linked evidence entry.
        const baseGroups = allBboxData.length > 0 ? allBboxData : bboxData;
        const fullGroupsToSend = baseGroups.map((group) =>
          group.source_id === selectedGroup.source_id &&
          group.page === selectedGroup.page
            ? { ...group, bboxes: updatedBboxes }
            : group,
        );

        const sourcesPayload = buildRequirementSourcesPayload(fullGroupsToSend);


        if (!sourcesPayload || sourcesPayload.length === 0) {
          toast.error(
            "Unable to save grounding: missing fragment id for one or more bounding boxes.",
          );
          return;
        }

        try {
          await updateRequirementBboxes({
            projectId,
            requirementId,
            sources: sourcesPayload,
          }).unwrap();

          onRequirementBboxUpdated?.({
            source_id: selectedGroup.source_id,
            page: selectedGroup.page,
            bboxes: updatedBboxes,
          });

          toast.success("Grounding updated successfully.");
          setAdjustGrounding(false);
        } catch {
          toast.error("Failed to save grounding updates.");
        }
        return;
      }

      toast.error("Unable to save grounding: missing required data.");
    };

    const isPdf = useMemo(
      () => srcName.toLowerCase().endsWith(".pdf"),
      [srcName],
    );
    const resolvedFileUrl = useMemo(() => {
      if (fileUrl) return fileUrl;
      return "";
    }, [fileUrl]);

    const zoomIn = () => setZoom((prev) => Math.min(prev + 0.25, 2.5));
    const zoomOut = () => setZoom((prev) => Math.max(prev - 0.25, 0.5));

    const toolbarButtonClass =
      "w-4 h-4 flex items-center justify-center rounded-[4px] disabled:opacity-50 disabled:cursor-not-allowed";

    // Expose state and handlers to parent via ref
    useImperativeHandle(ref, () => ({
      adjustGrounding,
      isSavingBbox,
      handleStartAdjustGrounding,
      handleCancelAdjustGrounding,
      handleSaveGrounding,
    }));

    return (
      <div className="relative w-full h-full flex flex-col bg-[var(--color-white)]">
        {/* File Preview */}
        <div
          className={`w-full h-full overflow-y-auto [scrollbar-gutter:stable] ${
            isPdf ? "overflow-x-auto" : "overflow-x-hidden"
          }`}
        >
          {!isPdf ? (
            <div className="h-full min-h-40 flex items-center justify-center text-center text-[length:var(--font-size-sm)] text-[color:var(--color-neutral-400)]">
              Preview is currently available for PDF files only.
            </div>
          ) : (
            <PdfSourceViewer
              fileUrl={resolvedFileUrl}
              pageNumber={selectedPage}
              coordinates={coordinates}
              onCoordinatesChange={handleCoordinatesChange}
              zoom={zoom}
              editable={adjustGrounding}
              selectedCoordinateIndex={selectedBboxIndex}
              scrollToSelectedSignal={scrollToSelectedSignal}
            />
          )}
        </div>

        {fullScreenIcon && (
          <button
            type="button"
            className={`${toolbarButtonClass} absolute top-2 right-5 z-10`}
            onClick={fullScreenViewClickHandler}
            aria-label="Full Screen"
          >
            <img
              src={fullscreenIcon}
              alt="Full Screen"
              className="w-4 h-4 text-[var(--color-blue-500)]"
            />
          </button>
        )}
        {/* ToolBar - Fixed at bottom-right */}
        <div className="absolute bottom-3 right-6 z-10 flex gap-2 items-center bg-[var(--color-blue-100)] rounded-[16px] ">
          <div className="w-[116px] h-8 flex px-2 items-center justify-between gap-1">
            <button
              type="button"
              className={toolbarButtonClass}
              onClick={zoomOut}
              disabled={zoom <= 0.5 || !isPdf}
              aria-label="Zoom out"
            >
              <ZoomOutIcon className="text-[var(--color-blue-500)]" />
            </button>
            <span className="text-[length:var(--font-size-xsm)] text-[color:var(--color-neutral-500)] px-1">
              {Math.round(zoom * 100)}%
            </span>
            <button
              type="button"
              className={toolbarButtonClass}
              onClick={zoomIn}
              disabled={zoom >= 2.5 || !isPdf}
              aria-label="Zoom in"
            >
              <ZoomInIcon className="text-[var(--color-blue-500)]" />
            </button>
          </div>
        </div>
      </div>
    );
  },
);

PdfViewer.displayName = "PdfViewer";

export default PdfViewer;
