// Bundles the regression with the repo's own rolldown (aliasing `react-pdf`
// to the labelled stub) and runs it under Node.
//
// Needs jsdom, which this repo does not depend on. Install it anywhere and
// point JSDOM_PATH at it, e.g.:
//   npm i --prefix /tmp/harness jsdom@24
//   JSDOM_PATH=/tmp/harness/node_modules/jsdom node tests/pdf-source-viewer/run.mjs
import { spawnSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "rolldown";

const here = fileURLToPath(new URL(".", import.meta.url));
const frontendRoot = resolve(here, "../..");
const outDir = mkdtempSync(join(tmpdir(), "pdf-source-viewer-"));

await build({
  input: join(here, "pdfSourceViewer.regression.tsx"),
  platform: "node",
  resolve: {
    alias: {
      "react-pdf": join(here, "react-pdf.stub.tsx"),
      "@": join(frontendRoot, "src"),
    },
  },
  transform: {
    jsx: "react-jsx",
    define: { "process.env.NODE_ENV": '"development"' },
  },
  output: { dir: outDir, format: "esm", entryFileNames: "regression.mjs" },
  logLevel: "warn",
});

const result = spawnSync(process.execPath, [join(outDir, "regression.mjs")], {
  stdio: "inherit",
});
process.exit(result.status ?? 1);
