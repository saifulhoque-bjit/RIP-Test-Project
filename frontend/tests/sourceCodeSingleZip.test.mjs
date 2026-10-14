// Regression for P1821-488: a source-code run must accept exactly one ZIP.
//
// Run from frontend/: node --test tests/
//
// Uses only Node's built-in test runner plus `rolldown` (already installed as
// Vite's bundler) to compile the real production modules. No new test
// framework is added.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "rolldown";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const src = path.join(root, "src");
// Inside the package so bundled output can resolve the real `react` package.
const cacheDir = path.join(root, "node_modules", ".cache");
mkdirSync(cacheDir, { recursive: true });
const outDir = mkdtempSync(path.join(cacheDir, "rip-fe-test-"));

// STUB (React state boundary): a single-instance useState that applies
// updater functions synchronously, so the real hook's state transitions can be
// observed without a DOM renderer. useEffect is a no-op; useMemo calls through.
const reactStub = `
const state = [];
let cursor = 0;
export function __reset() { state.length = 0; cursor = 0; }
export function __rerender() { cursor = 0; }
export function useState(init) {
  const i = cursor++;
  if (!(i in state)) state[i] = typeof init === "function" ? init() : init;
  const set = (v) => { state[i] = typeof v === "function" ? v(state[i]) : v; };
  return [state[i], set];
}
export function useEffect() {}
export function useMemo(fn) { return fn(); }
// Unused by the hook; present only so transitive imports resolve.
export const useRef = (v) => ({ current: v });
export const useContext = () => undefined;
export const useCallback = (fn) => fn;
export const useDebugValue = () => {};
export const useLayoutEffect = () => {};
export const createContext = () => ({});
export default { useState, useEffect, useMemo };
`;
// STUB (notification boundary): records every toast so the test can assert
// the user-visible rejection message.
const toastStub = `
export const calls = [];
export const toast = {
  error: (msg, opts) => calls.push({ kind: "error", msg, opts }),
  success: (msg, opts) => calls.push({ kind: "success", msg, opts }),
  info: (msg, opts) => calls.push({ kind: "info", msg, opts }),
};
`;
const stubDir = mkdtempSync(path.join(tmpdir(), "rip-fe-stub-"));
writeFileSync(path.join(stubDir, "react.mjs"), reactStub);
writeFileSync(path.join(stubDir, "toast.mjs"), toastStub);

async function bundle(entry, name, { stubReact }) {
  const file = path.join(outDir, `${name}.mjs`);
  await build({
    input: entry,
    platform: "node",
    logLevel: "silent",
    plugins: [
      {
        name: "stub-boundaries",
        resolveId(id) {
          if (id === "@/lib/toast") return path.join(stubDir, "toast.mjs");
          if (stubReact && id === "react") return path.join(stubDir, "react.mjs");
          return null;
        },
      },
    ],
    resolve: { alias: { "@": src } },
    transform: {
      jsx: { runtime: "automatic" },
      // Vite-only global; not set when bundling outside Vite.
      define: { "import.meta.env": "{}" },
    },
    external: stubReact ? [] : [/^react($|\/)/, /^react-dom($|\/)/],
    output: { file, format: "esm" },
    write: true,
  });
  return import(pathToFileURL(file).href);
}

// Entry that re-exports the real hook together with the stub handles.
const hookEntry = path.join(outDir, "hookEntry.ts");
writeFileSync(
  hookEntry,
  `export { useFileUpload } from ${JSON.stringify(path.join(src, "features/ProjectWorkspace/Sources/useFileUpload.ts"))};
export { __reset, __rerender } from ${JSON.stringify(path.join(stubDir, "react.mjs"))};
export { calls as toastCalls } from ${JSON.stringify(path.join(stubDir, "toast.mjs"))};
`,
);

// STUB (browser storage): the hook and its transitive store imports read
// session/local storage at module load.
for (const key of ["sessionStorage", "localStorage"]) {
  globalThis[key] ??= {
    getItem: () => null,
    setItem: () => {},
    removeItem: () => {},
  };
}

const hookMod = await bundle(hookEntry, "hook", { stubReact: true });

function zip(name) {
  return new File(["PK"], name, { type: "application/zip", lastModified: 1 });
}

function mountHook(projectType) {
  hookMod.__reset();
  hookMod.toastCalls.length = 0;
  const opts = {
    projectId: "p1",
    isIncrementalUpload: false,
    projectType,
    codeBaseFormData: null,
    refetchSources: () => {},
    onUploadSuccess: () => {},
  };
  const render = () => {
    hookMod.__rerender();
    return hookMod.useFileUpload(opts);
  };
  return render;
}

test("source_code: selecting two ZIPs at once queues none and tells the user", () => {
  const render = mountHook("source_code");
  render().handleFilesQueuedForUpload([zip("a.zip"), zip("b.zip")]);
  assert.equal(render().uploadModalFiles.length, 0);
  assert.equal(hookMod.toastCalls.length, 1);
  assert.match(hookMod.toastCalls[0].msg, /only one source code zip/i);
});

test("source_code: a second ZIP added after the first is rejected, first kept", () => {
  const render = mountHook("source_code");
  render().handleFilesQueuedForUpload([zip("a.zip")]);
  assert.deepEqual(render().uploadModalFiles.map((f) => f.name), ["a.zip"]);
  render().handleFilesQueuedForUpload([zip("b.zip")]);
  assert.deepEqual(render().uploadModalFiles.map((f) => f.name), ["a.zip"]);
  assert.match(hookMod.toastCalls.at(-1).msg, /only one source code zip/i);
});

test("source_code: after removing the ZIP a different single ZIP can be chosen", () => {
  const render = mountHook("source_code");
  render().handleFilesQueuedForUpload([zip("a.zip")]);
  render().handleRemoveUploadFile(0);
  render().handleFilesQueuedForUpload([zip("b.zip")]);
  assert.deepEqual(render().uploadModalFiles.map((f) => f.name), ["b.zip"]);
});

test("rfp: multiple files are still accepted (surrounding behaviour)", () => {
  const render = mountHook("rfp");
  render().handleFilesQueuedForUpload([zip("a.pdf"), zip("b.pdf")]);
  assert.equal(render().uploadModalFiles.length, 2);
  assert.equal(hookMod.toastCalls.length, 0);
});

test("rfp: the 20-file cap is unchanged (surrounding behaviour)", () => {
  const render = mountHook("rfp");
  const many = Array.from({ length: 21 }, (_, i) => zip(`f${i}.pdf`));
  render().handleFilesQueuedForUpload(many);
  assert.equal(render().uploadModalFiles.length, 0);
  assert.match(hookMod.toastCalls[0].msg, /more than 20 files/);
});

// FileDragDrop is rendered with the real React + react-dom/server.
const dragDrop = await bundle(
  path.join(src, "features/ProjectWorkspace/Sources/components/FileDragDrop.tsx"),
  "dragdrop",
  { stubReact: false },
);
const { createElement } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");

function renderDropZone(props) {
  const noop = () => {};
  return renderToStaticMarkup(
    createElement(dragDrop.default, {
      openFileDialog: noop,
      handleDropZoneKeyDown: noop,
      handleDropZoneDragOver: noop,
      handleDropZoneDragLeave: noop,
      handleDropZoneDrop: noop,
      handleInputChange: noop,
      isDragActive: false,
      isDisabledState: false,
      acceptValue: ".zip",
      inputRef: { current: null },
      ...props,
    }),
  );
}

test("source_code drop zone: the file picker does not allow multi-select", () => {
  const html = renderDropZone({
    uploadScenario: "codebase-initial",
    allowMultiple: false,
  });
  assert.doesNotMatch(html, /<input[^>]*\smultiple/);
});

test("rfp drop zone: the file picker still allows multi-select", () => {
  const html = renderDropZone({ uploadScenario: "rfp-initial", allowMultiple: true });
  assert.match(html, /<input[^>]*\smultiple/);
});
