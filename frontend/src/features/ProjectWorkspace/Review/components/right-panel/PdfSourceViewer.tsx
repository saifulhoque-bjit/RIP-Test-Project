import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";

interface PageCoordinate {
  page: number;
  bbox: [number, number, number, number];
}

interface PdfSourceViewerProps {
  fileUrl: string;
  pageNumber: number;
  coordinates?: PageCoordinate[];
  onCoordinatesChange?: (
    coords: [number, number, number, number],
    coordinateIndex: number,
  ) => void;
  zoom?: number;
  showBoundingBox?: boolean;
  editable?: boolean;
  selectedCoordinateIndex?: number;
  scrollToSelectedSignal?: number;
}

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

type ResizeHandle = "n" | "ne" | "e" | "se" | "s" | "sw" | "w" | "nw";

interface BoxCoords {
  x: number;
  y: number;
  w: number;
  h: number;
}

interface HandleDef {
  id: ResizeHandle;
  cursor: string;
  posClass: string;
}

const MIN_BOX_SIZE = 20;

// offsetWidth rounds to the nearest integer, but the container's actual
// layout width can be fractional — rendering the page at exactly that
// rounded width can still overflow by a sub-pixel amount, which is enough
// for `overflow-x: auto` to permanently show a horizontal scrollbar.
const WIDTH_ROUNDING_SAFETY_MARGIN_PX = 2;

const HANDLES: HandleDef[] = [
  {
    id: "nw",
    cursor: "nwse-resize",
    posClass: "top-0 left-0 -translate-x-1/2 -translate-y-1/2",
  },
  {
    id: "n",
    cursor: "ns-resize",
    posClass: "top-0 left-1/2 -translate-x-1/2 -translate-y-1/2",
  },
  {
    id: "ne",
    cursor: "nesw-resize",
    posClass: "top-0 right-0 translate-x-1/2 -translate-y-1/2",
  },
  {
    id: "e",
    cursor: "ew-resize",
    posClass: "top-1/2 right-0 translate-x-1/2 -translate-y-1/2",
  },
  {
    id: "se",
    cursor: "nwse-resize",
    posClass: "bottom-0 right-0 translate-x-1/2 translate-y-1/2",
  },
  {
    id: "s",
    cursor: "ns-resize",
    posClass: "bottom-0 left-1/2 -translate-x-1/2 translate-y-1/2",
  },
  {
    id: "sw",
    cursor: "nesw-resize",
    posClass: "bottom-0 left-0 -translate-x-1/2 translate-y-1/2",
  },
  {
    id: "w",
    cursor: "ew-resize",
    posClass: "top-1/2 left-0 -translate-x-1/2 -translate-y-1/2",
  },
];

export default function PdfSourceViewer({
  fileUrl,
  pageNumber,
  coordinates,
  onCoordinatesChange,
  zoom = 1,
  showBoundingBox = true,
  editable = false,
  selectedCoordinateIndex = 0,
  scrollToSelectedSignal = 0,
}: PdfSourceViewerProps) {
  const [numPages, setNumPages] = useState<number>(0);
  const [boxes, setBoxes] = useState<PageCoordinate[]>(() => coordinates ?? []);
  const [pageWidth, setPageWidth] = useState<number>(600);
  const [hoveredCoordinateIndex, setHoveredCoordinateIndex] = useState<
    number | null
  >(null);
  const [isDragging, setIsDragging] = useState(false);
  const [activeCursor, setActiveCursor] = useState<string>("");
  const [hasScrolledToTarget, setHasScrolledToTarget] = useState(false);
  const [targetPageReady, setTargetPageReady] = useState(false);

  const wrapperRef = useRef<HTMLDivElement>(null);
  const targetPageRef = useRef<HTMLDivElement>(null);
  const boundingBoxRef = useRef<HTMLDivElement>(null);
  const coordinatesStringRef = useRef<string>(JSON.stringify(coordinates));
  const pageDimensionsRef = useRef<Record<number, { w: number; h: number }>>(
    {},
  );
  const dragRef = useRef<{
    coordinateIndex: number;
    page: number;
    handle: ResizeHandle;
    pointerId: number;
    startX: number;
    startY: number;
    startBox: BoxCoords;
  } | null>(null);

  const pageWidthRef = useRef(pageWidth);
  pageWidthRef.current = pageWidth;

  const renderedPageWidth = pageWidth * zoom;

  const safePageNumber = useMemo(() => {
    if (!numPages) return pageNumber;
    return Math.min(Math.max(pageNumber, 1), numPages);
  }, [numPages, pageNumber]);

  useEffect(() => {
    if (isDragging) return;

    const currentCoordinatesString = JSON.stringify(coordinates);
    if (currentCoordinatesString !== coordinatesStringRef.current) {
      coordinatesStringRef.current = currentCoordinatesString;
      setBoxes(coordinates ?? []);
    }
  }, [coordinates, isDragging]);

  useEffect(() => {
    const measureTarget = wrapperRef.current;
    if (!measureTarget) return;

    const updateWidth = () => {
      const availableWidth =
        measureTarget.offsetWidth - WIDTH_ROUNDING_SAFETY_MARGIN_PX;
      if (availableWidth > 0 && availableWidth !== pageWidth) {
        setPageWidth(availableWidth);
      }
    };

    updateWidth();

    const resizeObserver = new ResizeObserver(() => {
      updateWidth();
    });

    resizeObserver.observe(measureTarget);

    return () => {
      resizeObserver.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Native scrollIntoView walks up EVERY scrollable ancestor (this viewer's
  // own scroll box, the evidence panel's scroll box, and the page itself),
  // nudging all of them toward the target. That's what made the outer
  // workspace appear to "auto scroll" whenever a bounding box came into
  // view. Scrolling only this viewer's own scroll container directly keeps
  // every ancestor above it untouched.
  const scrollTargetIntoOwnContainer = useCallback(
    (targetEl: HTMLElement, behavior: ScrollBehavior) => {
      const container = wrapperRef.current?.parentElement;
      if (!container) return;

      const containerRect = container.getBoundingClientRect();
      const targetRect = targetEl.getBoundingClientRect();

      container.scrollTo({
        top: container.scrollTop + (targetRect.top - containerRect.top),
        behavior,
      });
    },
    [],
  );

  useEffect(() => {
    if (hasScrolledToTarget || !targetPageReady) return;

    const rafId = requestAnimationFrame(() => {
      const scrollEl = boundingBoxRef.current ?? targetPageRef.current;
      if (scrollEl) {
        scrollTargetIntoOwnContainer(scrollEl, "smooth");
        setHasScrolledToTarget(true);
      }
    });

    return () => cancelAnimationFrame(rafId);
  }, [hasScrolledToTarget, targetPageReady, scrollTargetIntoOwnContainer]);

  useEffect(() => {
    const onFullscreenChange = () => {
      setTimeout(() => {
        const scrollEl = boundingBoxRef.current ?? targetPageRef.current;
        if (scrollEl) {
          scrollTargetIntoOwnContainer(scrollEl, "instant");
        }
      }, 150);
    };

    document.addEventListener("fullscreenchange", onFullscreenChange);
    return () => {
      document.removeEventListener("fullscreenchange", onFullscreenChange);
    };
  }, [scrollTargetIntoOwnContainer]);

  useEffect(() => {
    setHasScrolledToTarget(false);
    const cached = pageDimensionsRef.current[pageNumber];
    if (cached) {
      setTargetPageReady(true);
    } else {
      setTargetPageReady(false);
    }
  }, [pageNumber]);

  useEffect(() => {
    if (editable && isDragging) return;

    setHasScrolledToTarget(false);
    if (targetPageReady) {
      const rafId = requestAnimationFrame(() => {
        const scrollEl = boundingBoxRef.current ?? targetPageRef.current;
        if (scrollEl) {
          scrollTargetIntoOwnContainer(scrollEl, "smooth");
          setHasScrolledToTarget(true);
        }
      });
      return () => cancelAnimationFrame(rafId);
    }
  }, [
    selectedCoordinateIndex,
    scrollToSelectedSignal,
    pageNumber,
    coordinates,
    targetPageReady,
    editable,
    isDragging,
    scrollTargetIntoOwnContainer,
  ]);

  const onHandlePointerDown = useCallback(
    (
      e: React.PointerEvent<HTMLDivElement>,
      handle: ResizeHandle,
      cursor: string,
      coordinateIndex: number,
    ) => {
      e.preventDefault();
      e.stopPropagation();

      const selected = boxes[coordinateIndex];
      if (!selected) return;

      const selectedBox: BoxCoords = {
        x: selected.bbox[0],
        y: selected.bbox[1],
        w: selected.bbox[2],
        h: selected.bbox[3],
      };

      e.currentTarget.setPointerCapture(e.pointerId);
      dragRef.current = {
        coordinateIndex,
        page: selected.page,
        handle,
        pointerId: e.pointerId,
        startX: e.clientX,
        startY: e.clientY,
        startBox: selectedBox,
      };
      setIsDragging(true);
      setActiveCursor(cursor);
    },
    [boxes],
  );

  useEffect(() => {
    const onPointerMove = (e: PointerEvent) => {
      if (!dragRef.current) return;
      if (e.pointerId !== dragRef.current.pointerId) return;
      e.preventDefault();

      const { handle, startX, startY, startBox, page, coordinateIndex } =
        dragRef.current;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;

      const pageDimensions = pageDimensionsRef.current[page];
      if (!pageDimensions) return;

      const curPageWidth = pageWidthRef.current;
      const curOrigW = pageDimensions.w;
      const curOrigH = pageDimensions.h;

      const sf = curOrigW > 0 ? curPageWidth / curOrigW : 1;
      const dxOrig = dx / sf;
      const dyOrig = dy / sf;

      const maxWOrig = curOrigW;
      const maxHOrig = curOrigH;

      let { x, y, w, h } = startBox;

      if (handle.includes("w")) {
        const targetX = startBox.x + dxOrig;
        const clampedX = Math.max(
          0,
          Math.min(targetX, startBox.x + startBox.w - MIN_BOX_SIZE / sf),
        );
        x = clampedX;
        w = startBox.x + startBox.w - clampedX;
      }

      if (handle.includes("e")) {
        const targetRight = startBox.x + startBox.w + dxOrig;
        const clampedRight = Math.max(
          startBox.x + MIN_BOX_SIZE / sf,
          Math.min(targetRight, maxWOrig),
        );
        w = clampedRight - x;
      }

      if (handle.includes("n")) {
        const targetY = startBox.y + dyOrig;
        const clampedY = Math.max(
          0,
          Math.min(targetY, startBox.y + startBox.h - MIN_BOX_SIZE / sf),
        );
        y = clampedY;
        h = startBox.y + startBox.h - clampedY;
      }

      if (handle.includes("s")) {
        const targetBottom = startBox.y + startBox.h + dyOrig;
        const clampedBottom = Math.max(
          startBox.y + MIN_BOX_SIZE / sf,
          Math.min(targetBottom, maxHOrig),
        );
        h = clampedBottom - y;
      }

      setBoxes((prev) => {
        if (!prev[coordinateIndex]) return prev;

        const next = [...prev];
        next[coordinateIndex] = {
          ...next[coordinateIndex],
          bbox: [x, y, w, h],
        };
        return next;
      });

      onCoordinatesChange?.([x, y, w, h], coordinateIndex);
    };

    const onPointerUp = (e: PointerEvent) => {
      if (!dragRef.current) return;
      if (e.pointerId !== dragRef.current.pointerId) return;
      dragRef.current = null;
      setIsDragging(false);
      setActiveCursor("");
      setHoveredCoordinateIndex(null);
    };

    document.addEventListener("pointermove", onPointerMove, {
      passive: false,
    });
    document.addEventListener("pointerup", onPointerUp);
    document.addEventListener("pointercancel", onPointerUp);

    return () => {
      document.removeEventListener("pointermove", onPointerMove);
      document.removeEventListener("pointerup", onPointerUp);
      document.removeEventListener("pointercancel", onPointerUp);
    };
  }, [onCoordinatesChange]);

  useEffect(() => {
    if (!isDragging) return;

    const preventScroll = (e: Event) => {
      e.preventDefault();
    };

    document.addEventListener("wheel", preventScroll, { passive: false });
    document.addEventListener("touchmove", preventScroll, {
      passive: false,
    });

    return () => {
      document.removeEventListener("wheel", preventScroll);
      document.removeEventListener("touchmove", preventScroll);
    };
  }, [isDragging]);

  useEffect(() => {
    document.body.style.cursor = activeCursor;
    document.body.style.userSelect = activeCursor ? "none" : "";
    return () => {
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
  }, [activeCursor]);

  const selectedBox = boxes[selectedCoordinateIndex];
  const selectedBoxPage = selectedBox?.page ?? pageNumber;
  const selectedDragIndex = dragRef.current?.coordinateIndex;

  return (
    <div ref={wrapperRef} className="w-full">
      <Document
        file={fileUrl}
        onLoadSuccess={({ numPages: n }) => setNumPages(n)}
        loading={
          <div className="text-sm text-[var(--text-secondary)]">
            Loading PDF...
          </div>
        }
      >
        <div className="flex flex-col gap-2">
          {Array.from({ length: numPages }, (_, i) => {
            const pageIndex = i + 1;
            const isTargetPage = pageIndex === safePageNumber;
            const pageDimensions = pageDimensionsRef.current[pageIndex];
            const pageScaleFactor = pageDimensions
              ? renderedPageWidth / pageDimensions.w
              : 1;
            const pageBoxes = boxes
              .map((item, index) => ({ item, index }))
              .filter(({ item }) => item.page === pageIndex);

            return (
              <div key={pageIndex} className="w-full">
                {pageIndex > 1 && (
                  <div className="w-full flex justify-center py-1">
                    <div
                      className="h-px bg-[var(--color-neutral-200)]"
                      style={{ width: renderedPageWidth }}
                    />
                  </div>
                )}

                <div
                  ref={isTargetPage ? targetPageRef : undefined}
                  className="relative inline-block"
                >
                  <div className="absolute top-2 left-2 z-10 px-2 py-0.5 rounded-full bg-black/40 text-white text-[11px] leading-tight select-none pointer-events-none">
                    {pageIndex}/{numPages}
                  </div>

                  <Page
                    pageNumber={pageIndex}
                    width={renderedPageWidth}
                    renderTextLayer={false}
                    renderAnnotationLayer={false}
                    onLoadSuccess={(page) => {
                      pageDimensionsRef.current[pageIndex] = {
                        w: page.originalWidth,
                        h: page.originalHeight,
                      };
                      if (isTargetPage) {
                        setTargetPageReady(true);
                      }
                    }}
                  />

                  {showBoundingBox &&
                    pageBoxes.map(({ item, index }) => {
                      const isSelected = index === selectedCoordinateIndex;
                      const isSelectedPage = pageIndex === selectedBoxPage;
                      const showHandlesForBox =
                        isSelected &&
                        editable &&
                        (hoveredCoordinateIndex === index ||
                          (isDragging && selectedDragIndex === index));
                      const highlightClass =
                        editable && isSelected
                          ? "border-[var(--color-brand-green-500)] bg-[color-mix(in_srgb,var(--color-brand-green-500)_20%,transparent)]"
                          : "border-[var(--text-warning)] bg-[color-mix(in_srgb,var(--text-warning)_20%,transparent)]";

                      return (
                        <div
                          key={`${pageIndex}-${index}`}
                          ref={
                            isSelected && isSelectedPage
                              ? boundingBoxRef
                              : undefined
                          }
                          className={`absolute border-2 ${highlightClass}`}
                          style={{
                            left: item.bbox[0] * pageScaleFactor,
                            top: item.bbox[1] * pageScaleFactor,
                            width: item.bbox[2] * pageScaleFactor,
                            height: item.bbox[3] * pageScaleFactor,
                            touchAction: "none",
                          }}
                          onMouseEnter={() => setHoveredCoordinateIndex(index)}
                          onMouseLeave={() => setHoveredCoordinateIndex(null)}
                          aria-label={`Grounding coordinates highlight ${index + 1}`}
                        >
                          {showHandlesForBox &&
                            HANDLES.map(({ id, cursor, posClass }) => (
                              <div
                                key={id}
                                className={`absolute w-3 h-3 rounded-sm bg-white border-2 border-[var(--color-brand-green-500)] shadow z-10 ${posClass}`}
                                style={{ cursor, touchAction: "none" }}
                                draggable={false}
                                onDragStart={(e) => e.preventDefault()}
                                onPointerDown={(e) =>
                                  onHandlePointerDown(e, id, cursor, index)
                                }
                                aria-label={`Resize ${id}`}
                              />
                            ))}
                        </div>
                      );
                    })}
                </div>
              </div>
            );
          })}
        </div>
      </Document>
    </div>
  );
}
