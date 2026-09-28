/**
 * Resolves the WebSocket base URL from environment variables.
 *
 * Priority:
 * 1. VITE_WS_BASE_URL (explicit override)
 * 2. Derived from VITE_API_BASE_URL (swaps http→ws, strips path)
 * 3. Same-origin fallback (works with Vite dev proxy)
 */
export const WS_BASE_URL = (() => {
  const configured = import.meta.env.VITE_WS_BASE_URL as string | undefined;
  if (configured) return configured;

  const apiBase = import.meta.env.VITE_API_BASE_URL as string | undefined;
  if (apiBase && /^https?:\/\//.test(apiBase)) {
    try {
      const url = new URL(apiBase);
      const wsProtocol = url.protocol === "https:" ? "wss:" : "ws:";
      return `${wsProtocol}//${url.host}`;
    } catch { /* fall through */ }
  }

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}`;
})();
