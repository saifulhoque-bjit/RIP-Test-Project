// Standalone config for the regression tests in this folder (the app has no
// test runner; run with `npx vitest run --config tests/vitest.config.mjs`).
export default {
  esbuild: { jsx: "automatic" },
  test: { include: ["tests/**/*.test.tsx"] },
};
