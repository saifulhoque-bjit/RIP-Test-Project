// Run with `npm test`. Loads the TypeScript module through Vite so the `@/`
// alias resolves exactly as it does in the app build.
import { after, before, describe, it } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "vite";

let server;
let describeSyncFailure;

before(async () => {
  server = await createServer({
    configFile: new URL("../vite.config.ts", import.meta.url).pathname,
    server: { middlewareMode: true, hmr: false },
    appType: "custom",
    logLevel: "silent",
  });
  ({ describeSyncFailure } = await server.ssrLoadModule(
    "/src/features/ProjectWorkspace/Sync/syncOutcome.ts",
  ));
});

after(async () => {
  await server?.close();
});

// P1821-352: the request can drop or time out while the backend keeps
// syncing and finishes. Those errors must not be reported as a failed sync.
describe("describeSyncFailure — outcome unknown", () => {
  const unknownOutcomes = [
    { status: "FETCH_ERROR", error: "TypeError: Failed to fetch" },
    { status: "TIMEOUT_ERROR", error: "AbortError: timeout" },
    { status: 502, data: "<html>Bad Gateway</html>" },
    { status: 503, data: "<html>Service Unavailable</html>" },
    { status: 504, data: "<html>Gateway Time-out</html>" },
    { status: "PARSING_ERROR", originalStatus: 504, data: "<html>504</html>", error: "SyntaxError" },
  ];

  for (const error of unknownOutcomes) {
    it(`does not show a failure toast for ${error.status}${error.originalStatus ? `/${error.originalStatus}` : ""}`, () => {
      const toast = describeSyncFailure(error, "Jira");
      assert.equal(toast.kind, "info");
      assert.doesNotMatch(toast.message, /fail/i);
      assert.match(toast.message, /Jira/);
    });
  }
});

describe("describeSyncFailure — real failures", () => {
  it("keeps the backend's message for an error the server answered with", () => {
    const toast = describeSyncFailure(
      { status: 404, data: { message: "Jira integration not found." } },
      "Jira",
    );
    assert.deepEqual(toast, { kind: "error", message: "Jira integration not found." });
  });

  it("falls back to the generic failure message", () => {
    const toast = describeSyncFailure({ status: 500, data: {} }, "Jira");
    assert.deepEqual(toast, { kind: "error", message: "Failed to sync to Jira." });
  });

  it("treats a non-RTK error as a failure", () => {
    const toast = describeSyncFailure(new Error("boom"), "TAP");
    assert.deepEqual(toast, { kind: "error", message: "boom" });
  });
});
