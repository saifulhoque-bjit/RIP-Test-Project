// @vitest-environment jsdom
// Regression test for P1821-481: in a multi-page source, a page whose PDF
// page finished loading after the target page rendered its highlight with an
// unscaled (1:1) factor, so its coordinates were misplaced / effectively
// missing.
import { act, render } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

// STUB (lowest external boundary): react-pdf renders via pdf.js in a canvas,
// which jsdom cannot run. Document renders its children; each Page records
// its props so the test can fire its onLoadSuccess in a chosen order.
const pageProps: Record<
  number,
  { width: number; onLoadSuccess: (p: { originalWidth: number; originalHeight: number }) => void }
> = {};

vi.mock("react-pdf", () => ({
  pdfjs: { GlobalWorkerOptions: {} },
  Document: ({
    children,
    onLoadSuccess,
  }: {
    children: ReactNode;
    onLoadSuccess: (d: { numPages: number }) => void;
  }) => {
    (globalThis as { __docLoad?: typeof onLoadSuccess }).__docLoad =
      onLoadSuccess;
    return <div>{children}</div>;
  },
  Page: (props: {
    pageNumber: number;
    width: number;
    onLoadSuccess: (p: { originalWidth: number; originalHeight: number }) => void;
  }) => {
    pageProps[props.pageNumber] = props;
    return <div data-page={props.pageNumber} />;
  },
}));

beforeAll(() => {
  Element.prototype.scrollTo = () => {};
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
  globalThis.requestAnimationFrame = ((cb: FrameRequestCallback) =>
    setTimeout(() => cb(0), 0)) as unknown as typeof requestAnimationFrame;
  globalThis.cancelAnimationFrame = ((id: number) =>
    clearTimeout(id)) as unknown as typeof cancelAnimationFrame;
});

afterEach(() => {
  for (const k of Object.keys(pageProps)) delete pageProps[Number(k)];
});

describe("PdfSourceViewer multi-page highlights", () => {
  it("scales highlights on every page, whatever order the pages load in", async () => {
    const { default: PdfSourceViewer } = await import(
      "../src/features/ProjectWorkspace/Review/components/right-panel/PdfSourceViewer"
    );
    const { container } = render(
      <PdfSourceViewer
        fileUrl="file.pdf"
        pageNumber={1}
        coordinates={[
          { page: 1, bbox: [100, 100, 50, 20] },
          { page: 2, bbox: [100, 200, 50, 20] },
        ]}
      />,
    );

    act(() => {
      (globalThis as { __docLoad?: (d: { numPages: number }) => void })
        .__docLoad!({ numPages: 2 });
    });

    // Target page (1) loads first, page 2 loads afterwards.
    const renderedWidth = pageProps[1].width;
    expect(pageProps[2].width).toBe(renderedWidth);
    act(() => {
      pageProps[1].onLoadSuccess({ originalWidth: 500, originalHeight: 700 });
    });
    act(() => {
      pageProps[2].onLoadSuccess({ originalWidth: 500, originalHeight: 700 });
    });

    const scale = renderedWidth / 500;
    expect(scale).not.toBe(1);
    const boxes = container.querySelectorAll<HTMLElement>(
      '[aria-label^="Grounding coordinates highlight"]',
    );
    expect(boxes).toHaveLength(2);
    for (const [i, box] of Array.from(boxes).entries()) {
      const y = i === 0 ? 100 : 200;
      expect(box.style.left).toBe(`${100 * scale}px`);
      expect(box.style.top).toBe(`${y * scale}px`);
      expect(box.style.width).toBe(`${50 * scale}px`);
    }
  });
});
