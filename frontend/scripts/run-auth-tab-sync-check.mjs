// Bundles scripts/auth-tab-sync.check.ts with Vite (same `@` alias as the app)
// and runs it in Node with stubbed browser storage. There is no test runner in
// this repo, so this uses only existing dev dependencies.
import { build } from "vite";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

// Inside node_modules so the bundle resolves its external packages.
const outDir = resolve("node_modules/.cache/auth-tab-sync-check");
await build({
  configFile: "vite.config.ts",
  logLevel: "error",
  build: {
    ssr: "scripts/auth-tab-sync.check.ts",
    outDir,
    emptyOutDir: true,
    rollupOptions: { output: { entryFileNames: "check.mjs" } },
  },
});

// STUB (browser boundary): Storage with the Web Storage API surface.
class MemoryStorage {
  #m = new Map();
  getItem(k) { return this.#m.has(k) ? this.#m.get(k) : null; }
  setItem(k, v) { this.#m.set(k, String(v)); }
  removeItem(k) { this.#m.delete(k); }
  clear() { this.#m.clear(); }
}

// STUB: Tab 1's browser. localStorage is shared by every tab of the browser;
// sessionStorage belongs to Tab 1 only. Tab 1 restored Account A on boot.
globalThis.window = new EventTarget();
globalThis.localStorage = new MemoryStorage();
globalThis.sessionStorage = new MemoryStorage();
globalThis.localStorage.setItem(
  "authUser",
  JSON.stringify({ id: "user-a", displayName: "Client Admin A", roles: ["client_admin"], permissions: [], email: "a@example.com" }),
);
globalThis.sessionStorage.setItem("rip_access_token", "token-a");

await import(pathToFileURL(join(outDir, "check.mjs")).href);
