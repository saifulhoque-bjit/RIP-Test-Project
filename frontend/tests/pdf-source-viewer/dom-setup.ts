// Installs a jsdom window as the global DOM before React loads.
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const jsdomPath = process.env.JSDOM_PATH ?? "jsdom";
const { JSDOM } = require(jsdomPath);

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  pretendToBeVisual: true,
});
const g = globalThis as Record<string, unknown>;
for (const key of [
  "window",
  "document",
  "navigator",
  "HTMLElement",
  "Element",
  "Node",
  "Event",
  "requestAnimationFrame",
  "cancelAnimationFrame",
]) {
  Object.defineProperty(g, key, {
    value: dom.window[key],
    configurable: true,
    writable: true,
  });
}
// jsdom has no layout: stub the two layout APIs the viewer calls.
class ResizeObserverStub {
  observe() {}
  disconnect() {}
}
g.ResizeObserver = ResizeObserverStub;
dom.window.ResizeObserver = ResizeObserverStub;
dom.window.Element.prototype.scrollTo = () => {};
g.IS_REACT_ACT_ENVIRONMENT = true;
