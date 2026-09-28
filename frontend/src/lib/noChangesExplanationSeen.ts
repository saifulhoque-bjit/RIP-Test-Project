/**
 * Tracks which notifications' `data.no_changes_explanation` acknowledgement
 * modal has already been shown and dismissed, so a page reload or WebSocket
 * reconnect (which replays the full current notification list) never
 * re-surfaces a message the user already clicked "Ok" on.
 *
 * Persisted to localStorage with the same load-once-at-module-scope,
 * try/catch-wrapped persist-on-write shape as the existing pending-state
 * slices (e.g. pipelineCancellationSlice) — but as a plain module rather
 * than a Redux slice, since this state has exactly one reader/writer
 * (useNotifications.tsx) and a per-id Redux selector would have to be
 * called inside an array callback, which isn't allowed.
 */

const STORAGE_KEY = "rip_no_changes_explanation_seen";

function loadFromStorage(): Set<string> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY); 
    if (!raw) return new Set();

    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return new Set();

    return new Set(parsed.filter((id): id is string => typeof id === "string"));
  } catch {
    return new Set();
  }
}

function persist(seen: Set<string>): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(Array.from(seen)));
  } catch {
    // Ignore storage errors (quota exceeded, private mode, etc.)
  }
}

const seen = loadFromStorage();

export function isNoChangesExplanationSeen(id: string): boolean {
  return seen.has(id);
}

export function markNoChangesExplanationSeen(id: string): void {
  seen.add(id);
  persist(seen);
}
