// Run: npx tsc -p tests/tsconfig.json && node --test .test-dist/tests/
import { test } from "node:test";
import assert from "node:assert/strict";
import { shouldShowApproveArchitectureBtn } from "../src/features/ProjectWorkspace/Review/utils/approveArchitectureVisibility.js";

const stage1 = {
  projectType: "rfp",
  hasUserStories: false,
  isProjectBusy: false,
  hasPendingFeedbackChange: false,
};

// Backend state after every module/feature feedback update was accepted:
// each accept sets status=approved, so are_all_approved_for_mod is true.
const afterAcceptingAll = { ...stage1, areAllApprovedForMod: true };

test("shown in RFP stage 1 with nothing pending", () => {
  assert.equal(shouldShowApproveArchitectureBtn(stage1), true);
});

// P1821-518: accepting module + feature feedback updates marks every module
// "approved" on the backend, yet no user stories were generated yet.
test("stays visible after all module/feature feedback updates are accepted", () => {
  assert.equal(
    shouldShowApproveArchitectureBtn(afterAcceptingAll),
    true,
  );
});

test("hidden while a feedback change is still pending review", () => {
  assert.equal(
    shouldShowApproveArchitectureBtn({
      ...stage1,
      hasPendingFeedbackChange: true,
    }),
    false,
  );
});

test("hidden once user stories exist (stage 2)", () => {
  assert.equal(
    shouldShowApproveArchitectureBtn({
      ...afterAcceptingAll,
      hasUserStories: true,
    }),
    false,
  );
});

test("hidden while the project is busy (e.g. generation running)", () => {
  assert.equal(
    shouldShowApproveArchitectureBtn({
      ...afterAcceptingAll,
      isProjectBusy: true,
    }),
    false,
  );
});

test("hidden for non-RFP projects", () => {
  assert.equal(
    shouldShowApproveArchitectureBtn({ ...stage1, projectType: "source_code" }),
    false,
  );
});
