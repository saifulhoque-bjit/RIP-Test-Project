// STUB (test-only): the browser globals the auth slice and store read at
// import time. `localStorage` is shared by every tab of the browser, while
// `sessionStorage` belongs to this tab only — the same split a real browser
// has. `window` is a plain EventTarget so tests can deliver the `storage`
// events a real browser sends to *other* tabs when one tab writes
// localStorage. Must be imported before anything under `@/store`.
class MemoryStorage {
  private data = new Map<string, string>();
  getItem(key: string): string | null {
    return this.data.has(key) ? (this.data.get(key) as string) : null;
  }
  setItem(key: string, value: string): void {
    this.data.set(key, String(value));
  }
  removeItem(key: string): void {
    this.data.delete(key);
  }
  clear(): void {
    this.data.clear();
  }
}

const g = globalThis as unknown as Record<string, unknown>;
g.localStorage = new MemoryStorage();
g.sessionStorage = new MemoryStorage();
g.window = Object.assign(new EventTarget(), {
  localStorage: g.localStorage,
  sessionStorage: g.sessionStorage,
});

/**
 * Simulates another tab of the same browser writing `authUser` to the shared
 * localStorage: the value really changes, and this tab receives the `storage`
 * event a browser fires for it.
 */
export function writeFromOtherTab(key: string, newValue: string | null): void {
  const storage = g.localStorage as MemoryStorage;
  const oldValue = storage.getItem(key);
  if (newValue === null) storage.removeItem(key);
  else storage.setItem(key, newValue);
  const event = new Event("storage") as Event & {
    key: string;
    oldValue: string | null;
    newValue: string | null;
  };
  Object.assign(event, { key, oldValue, newValue });
  (g.window as EventTarget).dispatchEvent(event);
}
