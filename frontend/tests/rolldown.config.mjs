// Bundles the node:test auth regression tests so they can import app code
// through the `@/` alias. npm packages stay external and load from node_modules.
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

export default {
  input: path.join(root, "tests/authCrossTabSync.test.ts"),
  platform: "node",
  // Vite injects import.meta.env at build time; give it the empty env.
  transform: { define: { "import.meta.env": "{}" } },
  resolve: { alias: { "@": path.join(root, "src") } },
  external: (id) =>
    !id.startsWith(".") &&
    !id.startsWith("/") &&
    !id.startsWith("@/") &&
    !id.startsWith("\0") &&
    !path.isAbsolute(id),
  output: {
    file: path.join(root, "node_modules/.tmp/authCrossTabSync.test.mjs"),
    format: "esm",
  },
};
