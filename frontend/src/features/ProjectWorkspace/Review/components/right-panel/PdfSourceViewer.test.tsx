import { act, cleanup, render } from "@testing-library/react";
import { useEffect, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import PdfSourceViewer from "@/features/ProjectWorkspace/Review/components/right-panel/PdfSourceViewer";

// Only react-pdf (the PDF rendering boundary) is stubbed. Each <Page> waits
// until the test releases it, so the order pages finish loading is controlled.
const pendingLoads = new Map<number, () => void>();

vi.mock("react-pdf", () => ({
  pdfjs: { GlobalWorkerOptions: {} },
  Document: ({
    children,
    onLoadSuccess,
  }: {
    children: ReactNode;
    onLoadSuccess: (doc: { numPages: number }) => void;
  }) => {
    useEffect(() => onLoadSuccess({ numPages: 3 }), [onLoadSuccess]);
    return <div>{children}</div>;
  },
  Page: ({
    pageNumber,
    onLoadSuccess,
  }: {
    pageNumber: number;
    onLoadSuccess: (page: {
      originalWidth: number;
      originalHeight: number;
    }) => void;
  }) => {
    useEffect(() => {
      pendingLoads.set(pageNumber, () =>
        onLoadSuccess({ originalWidth: 300, originalHeight: 400 }),
      );
    }, [pageNumber, onLoadSuccess]);
    return <div data-page={pageNumber} />;
  },
}));

globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
HTMLElement.prototype.scrollTo = () => {};

const box: [number, number, number, number] = [10, 20, 50, 30];
const coordinates = [1, 2, 3].map((page) => ({ page, bbox: box }));

const loadPages = async (order: number[]) => {
  for (const page of order) {
    await act(async () => {
      pendingLoads.get(page)?.();
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
  }
};

describe("PdfSourceViewer multi-page highlights", () => {
  beforeEach(() => pendingLoads.clear());
  afterEach(cleanup);

  it.each([
    ["target page first", [1, 2, 3]],
    ["target page last", [3, 2, 1]],
    ["target page in the middle", [2, 1, 3]],
  ])(
    "scales every page's box once that page loads (%s)",
    async (_label, order) => {
      const { getByLabelText } = render(
        <PdfSourceViewer
          fileUrl="x.pdf"
          pageNumber={1}
          coordinates={coordinates}
        />,
      );
      await act(async () => {});
      await loadPages(order);

      // Rendered 600px wide over a 300pt page: scale 2, so x=10pt -> 20px.
      for (let i = 1; i <= 3; i += 1) {
        const el = getByLabelText(`Grounding coordinates highlight ${i}`);
        expect({ highlight: i, left: el.style.left, width: el.style.width })
          .toEqual({ highlight: i, left: "20px", width: "100px" });
      }
    },
  );

  it("does not draw a box on a page whose size is not known yet", async () => {
    const { queryByLabelText } = render(
      <PdfSourceViewer
        fileUrl="x.pdf"
        pageNumber={1}
        coordinates={coordinates}
      />,
    );
    await act(async () => {});
    await loadPages([1, 2]);

    expect(queryByLabelText("Grounding coordinates highlight 3")).toBeNull();
    expect(
      queryByLabelText("Grounding coordinates highlight 1")?.style.left,
    ).toBe("20px");
  });
});
