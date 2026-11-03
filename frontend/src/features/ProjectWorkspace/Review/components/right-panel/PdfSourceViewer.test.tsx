import type { ReactNode } from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import PdfSourceViewer from "./PdfSourceViewer";

type PageLoadCallback = (page: {
  originalWidth: number;
  originalHeight: number;
}) => void;

// react-pdf is the external boundary: each page "finishes loading" only when
// the test calls loadPage(n), so the load order is under test control.
const pendingPageLoads = vi.hoisted(
  () => ({}) as Record<number, PageLoadCallback>,
);

vi.mock("react-pdf", async () => {
  const { useEffect } = await import("react");
  return {
    pdfjs: { GlobalWorkerOptions: { workerSrc: "" } },
    Document: ({
      onLoadSuccess,
      children,
    }: {
      onLoadSuccess?: (doc: { numPages: number }) => void;
      children?: ReactNode;
    }) => {
      useEffect(() => {
        onLoadSuccess?.({ numPages: 3 });
      }, [onLoadSuccess]);
      return <div>{children}</div>;
    },
    Page: ({
      pageNumber,
      onLoadSuccess,
    }: {
      pageNumber: number;
      onLoadSuccess: PageLoadCallback;
    }) => {
      pendingPageLoads[pageNumber] = onLoadSuccess;
      return <canvas data-page={pageNumber} />;
    },
  };
});

// The PDF page is 1200pt wide and jsdom has no layout, so the viewer keeps its
// default 600px page width: every box must be drawn at scale 0.5.
const PDF_PAGE = { originalWidth: 1200, originalHeight: 1600 };
const coordinates = [1, 2, 3].map((page) => ({
  page,
  bbox: [200, 400, 100, 50] as [number, number, number, number],
}));

const loadPage = async (pageNumber: number) => {
  await act(async () => pendingPageLoads[pageNumber](PDF_PAGE));
  // Let the viewer's requestAnimationFrame scroll (and its re-render) run.
  await act(() => new Promise((resolve) => setTimeout(resolve, 50)));
};

const boxStyle = (index: number) => {
  const box = screen.queryByLabelText(
    `Grounding coordinates highlight ${index}`,
  ) as HTMLElement | null;
  if (!box) return undefined;
  const { left, top, width, height } = box.style;
  return { left, top, width, height };
};

beforeAll(() => {
  Element.prototype.scrollTo = () => {};
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

afterEach(cleanup);

describe("PdfSourceViewer bounding boxes on a multi-page PDF", () => {
  it.each([
    [[1, 2, 3]],
    [[2, 1, 3]],
    [[1, 3, 2]],
    [[3, 2, 1]],
  ])(
    "draws every page's box at that page's scale when pages load in order %j",
    async (order) => {
      render(
        <PdfSourceViewer
          fileUrl="srs.pdf"
          pageNumber={1}
          coordinates={coordinates}
        />,
      );
      await act(async () => {});

      for (const pageNumber of order) await loadPage(pageNumber);

      for (const index of [1, 2, 3]) {
        expect(boxStyle(index)).toMatchObject({
          left: "100px",
          top: "200px",
          width: "50px",
          height: "25px",
        });
      }
    },
  );

  it("draws no box on a page whose size is not known yet", async () => {
    render(
      <PdfSourceViewer
        fileUrl="srs.pdf"
        pageNumber={1}
        coordinates={coordinates}
      />,
    );
    await act(async () => {});

    await loadPage(1);

    expect(boxStyle(1)).toMatchObject({ left: "100px" });
    expect(boxStyle(2)).toBeUndefined();
    expect(boxStyle(3)).toBeUndefined();
  });
});
