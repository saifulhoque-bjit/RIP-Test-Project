// Regression for P1821-518: accepting module + feature feedback updates on an
// RFP project before Stage 1 approval hid the "Approve & generate user
// stories" banner.
//
// The repo has no test runner, so this uses node:test plus the already
// installed `typescript` package to transpile the (import-free) helper.
// Run: node --test frontend/tests/
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const src = fileURLToPath(
  new URL(
    "../src/features/ProjectWorkspace/Review/utils/architectureApprovalVisibility.ts",
    import.meta.url,
  ),
);
const { outputText } = ts.transpileModule(readFileSync(src, "utf8"), {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2020,
  },
});
const { shouldShowApproveArchitecture } = await import(
  `data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`
);

const stage1 = {
  areAllApprovedForMod: false,
  projectType: "rfp",
  hasUserStories: false,
  isProjectBusy: false,
  hasPendingFeedbackChange: false,
};

test("shown for an RFP project in Stage 1", () => {
  assert.equal(shouldShowApproveArchitecture(stage1), true);
});

test("stays visible after module + feature feedback updates are accepted", () => {
  // POST /feedback-updates/accept sets Module.status = "approved"
  // (backend _execute_accept_module_feedback_change), so once every module
  // has had a feedback change accepted, are_all_approved_for_mod flips to
  // true although Stage 1 was never approved and no stories exist.
  assert.equal(
    shouldShowApproveArchitecture({ ...stage1, areAllApprovedForMod: true }),
    true,
  );
});

test("hidden while feedback changes are still pending accept/reject", () => {
  assert.equal(
    shouldShowApproveArchitecture({ ...stage1, hasPendingFeedbackChange: true }),
    false,
  );
});

test("hidden once user stories exist (Stage 2)", () => {
  assert.equal(
    shouldShowApproveArchitecture({
      ...stage1,
      areAllApprovedForMod: true,
      hasUserStories: true,
    }),
    false,
  );
});

test("hidden while a project task (e.g. story generation) is running", () => {
  assert.equal(
    shouldShowApproveArchitecture({
      ...stage1,
      areAllApprovedForMod: true,
      isProjectBusy: true,
    }),
    false,
  );
});

test("hidden for non-RFP projects", () => {
  assert.equal(
    shouldShowApproveArchitecture({ ...stage1, projectType: "source_code" }),
    false,
  );
});
