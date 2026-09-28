export type ReviewStage = "initial" | "first" | "second";

type StageFeatureLike = {
  children?: unknown[];
};

type StageModuleLike = {
  children?: StageFeatureLike[];
};

export const getReviewStageSnapshot = (
  modules: readonly StageModuleLike[],
): {
  hasModuleFeatureGenerated: boolean;
  hasUserStories: boolean;
  stage: ReviewStage;
} => {
  const hasModuleFeatureGenerated = modules.some(
    (mod) => (mod.children?.length ?? 0) > 0,
  );

  const hasUserStories = modules.some((mod) =>
    (mod.children ?? []).some((feat) => (feat.children?.length ?? 0) > 0),
  );

  const stage: ReviewStage = !hasModuleFeatureGenerated
    ? "initial"
    : hasUserStories
      ? "second"
      : "first";

  return {
    hasModuleFeatureGenerated,
    hasUserStories,
    stage,
  };
};

export const deriveReviewStage = (
  modules: readonly StageModuleLike[],
): ReviewStage => getReviewStageSnapshot(modules).stage;
