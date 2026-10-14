// Regression for P1821-481: on a multi-page source, every page's highlight
// must be positioned with that page's own scale, whatever order pdf.js
// reports the pages as loaded in.
//
// Runs the real PdfSourceViewer; only `react-pdf` is replaced (see
// react-pdf.stub.tsx). Run with `node tests/pdf-source-viewer/run.mjs`.
import "./dom-setup";
import assert from "node:assert/strict";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import PdfSourceViewer from "@/features/ProjectWorkspace/Review/components/right-panel/PdfSourceViewer";
import {
  documentState,
  pageLoadHandlers,
  pageRenderWidths,
} from "./react-pdf.stub-state";

// The viewer's default layout width is 600 px; jsdom reports offsetWidth 0,
// so it stays at 600. A 1200-pt-wide PDF page is therefore drawn at scale 0.5.
const PDF_PAGE = { originalWidth: 1200, originalHeight: 1600 };
const EXPECTED_SCALE = 600 / PDF_PAGE.originalWidth;

const coordinates = [
  { page: 1, bbox: [100, 200, 300, 40] as [number, number, number, number] },
  { page: 2, bbox: [120, 1400, 500, 60] as [number, number, number, number] },
  { page: 3, bbox: [80, 900, 400, 50] as [number, number, number, number] },
];

let failures = 0;

async function scenario(name: string, run: () => Promise<void>) {
  try {
    await run();
    console.log(`ok - ${name}`);
  } catch (err) {
    failures += 1;
    console.log(`not ok - ${name}`);
    console.log(`  ${(err as Error).message.split("\n").join("\n  ")}`);
  }
}

async function mount(pageNumber: number, selectedCoordinateIndex = 0) {
  pageLoadHandlers.clear();
  pageRenderWidths.clear();
  documentState.numPages = 3;
  const container = document.createElement("div");
  document.body.appendChild(container);
  let root!: Root;
  await act(async () => {
    root = createRoot(container);
    root.render(
      <PdfSourceViewer
        fileUrl="file.pdf"
        pageNumber={pageNumber}
        coordinates={coordinates}
        selectedCoordinateIndex={selectedCoordinateIndex}
      />,
    );
  });
  return { container, root };
}

async function loadPage(page: number) {
  const handler = pageLoadHandlers.get(page);
  assert.ok(handler, `page ${page} was never rendered`);
  await act(async () => handler(PDF_PAGE));
}

function highlightStyle(container: HTMLElement, coordinateIndex: number) {
  const el = container.querySelector<HTMLElement>(
    `[aria-label="Grounding coordinates highlight ${coordinateIndex + 1}"]`,
  );
  assert.ok(el, `highlight ${coordinateIndex + 1} is not rendered`);
  return {
    left: parseFloat(el.style.left),
    top: parseFloat(el.style.top),
    width: parseFloat(el.style.width),
    height: parseFloat(el.style.height),
  };
}

function assertScaled(container: HTMLElement, coordinateIndex: number) {
  const [x, y, w, h] = coordinates[coordinateIndex].bbox;
  const page = coordinates[coordinateIndex].page;
  const pageHeightPx = PDF_PAGE.originalHeight * EXPECTED_SCALE;
  const actual = highlightStyle(container, coordinateIndex);
  assert.deepEqual(
    actual,
    {
      left: x * EXPECTED_SCALE,
      top: y * EXPECTED_SCALE,
      width: w * EXPECTED_SCALE,
      height: h * EXPECTED_SCALE,
    },
    `page ${page} highlight is not scaled to its page ` +
      `(top ${actual.top}px on a ${pageHeightPx}px-tall page)`,
  );
  assert.ok(
    actual.top + actual.height <= pageHeightPx,
    `page ${page} highlight falls outside its page`,
  );
}

async function main() {
  await scenario(
    "pages that finish loading after the target page get their own scale",
    async () => {
      const { container, root } = await mount(1);
      await loadPage(1); // the target page loads first
      await loadPage(2); // other pages report later, one at a time
      await loadPage(3);
      assertScaled(container, 0);
      assertScaled(container, 1);
      assertScaled(container, 2);
      await act(async () => root.unmount());
      container.remove();
    },
  );

  await scenario(
    "pages that finish loading before the target page are scaled",
    async () => {
      const { container, root } = await mount(3, 2);
      await loadPage(1);
      await loadPage(2);
      await loadPage(3); // the target page loads last
      assertScaled(container, 0);
      assertScaled(container, 1);
      assertScaled(container, 2);
      await act(async () => root.unmount());
      container.remove();
    },
  );

  await scenario(
    "selecting another page keeps every page's highlight scaled",
    async () => {
      const { container, root } = await mount(1);
      await loadPage(1);
      await loadPage(2);
      await loadPage(3);
      await act(async () =>
        root.render(
          <PdfSourceViewer
            fileUrl="file.pdf"
            pageNumber={2}
            coordinates={coordinates}
            selectedCoordinateIndex={1}
          />,
        ),
      );
      assertScaled(container, 0);
      assertScaled(container, 1);
      assertScaled(container, 2);
      assert.equal(pageRenderWidths.get(2), 600);
      await act(async () => root.unmount());
      container.remove();
    },
  );

  await scenario(
    "a page that has not loaded yet still renders its highlight",
    async () => {
      const { container, root } = await mount(1);
      await loadPage(1);
      // page 2 has not reported dimensions yet: its box must still exist
      highlightStyle(container, 1);
      await act(async () => root.unmount());
      container.remove();
    },
  );

  console.log(failures === 0 ? "# all passed" : `# ${failures} failed`);
  // jsdom's visual-mode animation-frame timer keeps the event loop alive.
  process.exit(failures === 0 ? 0 : 1);
}

void main();
