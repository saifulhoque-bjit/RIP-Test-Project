// Boundary stub for `react-pdf` (labelled test double, used only by this
// regression). It renders no PDF; it lets the test decide when each page
// reports `onLoadSuccess`, which is the asynchronous, unordered event the real
// pdf.js worker produces.
import { useEffect, type ReactNode } from "react";
import {
  documentState,
  pageLoadHandlers,
  pageRenderWidths,
  type PageLoadHandler,
} from "./react-pdf.stub-state";

// eslint-disable-next-line react-refresh/only-export-components -- mirrors react-pdf
export const pdfjs = { GlobalWorkerOptions: { workerSrc: "" } };

export function Document({
  children,
  onLoadSuccess,
}: {
  file: string;
  loading?: ReactNode;
  children?: ReactNode;
  onLoadSuccess?: (doc: { numPages: number }) => void;
}) {
  useEffect(() => {
    onLoadSuccess?.({ numPages: documentState.numPages });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return <div data-testid="document">{children}</div>;
}

export function Page({
  pageNumber,
  width,
  onLoadSuccess,
}: {
  pageNumber: number;
  width?: number;
  renderTextLayer?: boolean;
  renderAnnotationLayer?: boolean;
  onLoadSuccess?: PageLoadHandler;
}) {
  if (onLoadSuccess) pageLoadHandlers.set(pageNumber, onLoadSuccess);
  if (width !== undefined) pageRenderWidths.set(pageNumber, width);
  return <div data-page={pageNumber} />;
}
