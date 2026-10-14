// Shared state for the react-pdf boundary stub (react-pdf.stub.tsx).
export type PageLoadHandler = (page: {
  originalWidth: number;
  originalHeight: number;
}) => void;

/** Latest `onLoadSuccess` handed to each <Page>, keyed by page number. */
export const pageLoadHandlers = new Map<number, PageLoadHandler>();
/** Widths each <Page> was asked to render at, keyed by page number. */
export const pageRenderWidths = new Map<number, number>();
export const documentState = { numPages: 0 };
