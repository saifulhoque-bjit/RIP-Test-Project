// Regression check for P1821-487: the per-file "remove" button in the Sources
// upload modal must be disabled while the upload/ingestion request is running.
//
// Run from frontend/: node --test tests/
// Loads the real UploadFileItem.tsx through Vite's SSR loader (same alias and
// JSX transform as the app build) and renders it with react-dom/server.
import { test, after } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const server = await createServer({
  root,
  configFile: path.join(root, "vite.config.ts"),
  server: { middlewareMode: true, hmr: false },
  appType: "custom",
  logLevel: "silent",
});
after(() => server.close());

const { default: UploadFileItem } = await server.ssrLoadModule(
  "/src/components/common/UploadFileItem.tsx",
);

const file = { name: "large-rfp.pdf" };
const removeButton = (html) => {
  const match = html.match(/<button[^>]*>(?:(?!<\/button>).)*remove<\/button>/s);
  assert.ok(match, `remove button not rendered in: ${html}`);
  return match[0].match(/^<button[^>]*>/)[0];
};

test("remove button is disabled while the upload is in progress", () => {
  const html = renderToStaticMarkup(
    createElement(UploadFileItem, {
      file,
      index: 0,
      onRemove: () => {},
      disabled: true,
    }),
  );
  assert.match(removeButton(html), /\sdisabled=""/);
});

test("remove button stays enabled before the upload starts", () => {
  const html = renderToStaticMarkup(
    createElement(UploadFileItem, { file, index: 0, onRemove: () => {} }),
  );
  assert.doesNotMatch(removeButton(html), /\sdisabled=""/);
});

test("completed files still show 'Completed' instead of a remove button", () => {
  const html = renderToStaticMarkup(
    createElement(UploadFileItem, {
      file,
      index: 0,
      onRemove: () => {},
      isSuccess: true,
      disabled: true,
    }),
  );
  assert.doesNotMatch(html, /remove<\/button>/);
  assert.match(html, /Completed/);
});
