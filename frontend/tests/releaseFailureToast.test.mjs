// Regression test for P1821-352: a Jira sync whose request is lost or times
// out (the sync keeps running on the server and completes) must not show a
// "Failed to sync to Jira" error toast.
//
// No test framework is installed in this package, so this uses node:test and
// the TypeScript compiler already in devDependencies to load the real modules
// SyncTray uses to pick its toast. Run: node --test tests/
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import ts from "typescript";

const src = new URL("../src/", import.meta.url);
const out = mkdtempSync(join(tmpdir(), "release-toast-"));
for (const [from, to] of [
  ["utils/getErrorMessage.ts", "getErrorMessage.mjs"],
  ["features/ProjectWorkspace/Sync/releaseFailureToast.ts", "releaseFailureToast.mjs"],
]) {
  const code = ts.transpileModule(readFileSync(new URL(from, src), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText.replace("@/utils/getErrorMessage", "./getErrorMessage.mjs");
  writeFileSync(join(out, to), code);
}
const { getReleaseFailureToast } = await import(join(out, "releaseFailureToast.mjs"));

for (const error of [
  { status: "FETCH_ERROR", error: "TypeError: Failed to fetch" },
  { status: "TIMEOUT_ERROR", error: "AbortError" },
  { status: 502, data: null },
  { status: 503, data: null },
  { status: 504, data: "<html>504 Gateway Time-out</html>" },
]) {
  test(`no failed toast when the outcome is unknown (${error.status})`, () => {
    const t = getReleaseFailureToast(error, "Jira");
    assert.notEqual(t.kind, "error");
    assert.doesNotMatch(t.message, /failed/i);
  });
}

test("an API error answer is still reported as a failed sync", () => {
  const t = getReleaseFailureToast(
    { status: 500, data: { message: "Jira sync failed: boom." } },
    "Jira",
  );
  assert.deepEqual(t, { kind: "error", message: "Jira sync failed: boom." });
});

test("an API error without a message falls back to the failed text", () => {
  const t = getReleaseFailureToast({ status: 400, data: {} }, "Jira");
  assert.deepEqual(t, { kind: "error", message: "Failed to sync to Jira." });
});
