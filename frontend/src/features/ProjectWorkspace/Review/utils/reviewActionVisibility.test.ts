import { describe, expect, it } from "vitest";
import {
  shouldShowStage1Approve,
  shouldShowStage1ModulesFeedback,
} from "@/features/ProjectWorkspace/Review/utils/reviewActionVisibility";

// SPEC: spec-f6af3e
// RFP Stage 1 (modules/features drafted, no user stories yet). Each state
// carries the tree's `areAllApprovedForMod` flag as the Review page receives
// it: accepting a feedback change sets a module/feature to `approved`, so the
// flag can be true while Stage 1 was never approved.
const stage1 = {
  projectType: "rfp",
  areAllApprovedForMod: false,
  hasUserStories: false,
  isProjectBusy: false,
  hasPendingFeedbackChange: false,
};
const allFeedbackAccepted = { ...stage1, areAllApprovedForMod: true };
const feedbackPending = { ...stage1, hasPendingFeedbackChange: true };
const busy = { ...stage1, isProjectBusy: true };
const storiesExist = { ...stage1, hasUserStories: true };
// Stage-1 approval ran (every module approved) but generation produced no stories.
const generationProducedNoStories = { ...stage1, areAllApprovedForMod: true };
const sourceCode = { ...stage1, projectType: "source_code" };

describe("shouldShowStage1Approve", () => {
  it("shows the approve bar at Stage 1", () => {
    expect(shouldShowStage1Approve(stage1)).toBe(true);
  });

  it("shows the approve bar after every module/feature feedback change was accepted", () => {
    expect(shouldShowStage1Approve(allFeedbackAccepted)).toBe(true);
  });

  it("hides the approve bar while a feedback change is pending", () => {
    expect(shouldShowStage1Approve(feedbackPending)).toBe(false);
  });

  it("hides the approve bar while the project is busy", () => {
    expect(shouldShowStage1Approve(busy)).toBe(false);
  });

  it("hides the approve bar once user stories exist", () => {
    expect(shouldShowStage1Approve(storiesExist)).toBe(false);
  });

  it("shows the approve bar again when generation produced no user stories", () => {
    expect(shouldShowStage1Approve(generationProducedNoStories)).toBe(true);
  });

  it("never shows the approve bar outside RFP projects", () => {
    expect(shouldShowStage1Approve(sourceCode)).toBe(false);
  });
});

describe("shouldShowStage1ModulesFeedback", () => {
  it("offers module feedback at Stage 1", () => {
    expect(shouldShowStage1ModulesFeedback(stage1)).toBe(true);
  });

  it("offers module feedback after every module/feature feedback change was accepted", () => {
    expect(shouldShowStage1ModulesFeedback(allFeedbackAccepted)).toBe(true);
  });

  it("stops offering module feedback once user stories exist", () => {
    expect(shouldShowStage1ModulesFeedback(storiesExist)).toBe(false);
  });

  it("never offers module feedback outside RFP projects", () => {
    expect(shouldShowStage1ModulesFeedback(sourceCode)).toBe(false);
  });
});
