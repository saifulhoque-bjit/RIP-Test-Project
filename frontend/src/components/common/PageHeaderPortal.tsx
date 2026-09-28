import { useContext } from "react";
import { createPortal } from "react-dom";
import type { ReactNode } from "react";
import { HeaderPortalContext } from "@/contexts/HeaderPortalContext";

/**
 * Portals its children into MainLayout's header slot, rendered once below
 * the global top bar. Lets a page own rich, interactive header markup
 * without MainLayout needing to know about it (avoids lifting JSX into
 * context state, which would re-render the whole route tree every time).
 */
export function PageHeaderPortal({ children }: { children: ReactNode }) {
  const container = useContext(HeaderPortalContext);
  if (!container) return null;
  return createPortal(children, container);
}
