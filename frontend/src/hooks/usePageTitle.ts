import { useEffect } from "react";
import { usePageHeader } from "@/contexts/usePageHeader";
import type { PageHeaderData } from "@/contexts/PageHeaderContext";

/** Sync a page's header metadata (title/description/icon) into the global top bar. */
export function usePageTitle(data: PageHeaderData) {
  const { setHeader } = usePageHeader();
  useEffect(() => {
    setHeader(data);
    return () => setHeader({});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data.title, data.description, data.icon]);
}
