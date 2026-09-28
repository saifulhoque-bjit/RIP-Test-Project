import { useEffect, useState } from "react";
import { Outlet, useMatches } from "react-router-dom";
import Sidebar from "@/components/layout/Sidebar";
import GlobalTopBar from "@/components/layout/GlobalTopBar";
import { SidebarProvider } from "@/components/ui/sidebar";
import {
  useGetAppSettingsQuery,
  useGetEnumCatalogQuery,
} from "@/services/api/modules/settings";
import { usePageHeader } from "@/contexts/usePageHeader";
import type { PageHeaderData } from "@/contexts/PageHeaderContext";
import { HeaderPortalContext } from "@/contexts/HeaderPortalContext";

/**
 * Shared layout for every authenticated route — mounted once at the router
 * level (see routes/index.tsx) so Sidebar/GlobalTopBar never remount when
 * navigating between pages. Each page renders its own rich header block via
 * <PageHeaderPortal> (portaled into the div below) and sets its main-content
 * className statically via the route's `handle.bodyClassName`.
 */
export default function MainLayout() {
  // Preload settings for all authenticated routes handled by MainLayout.
  useGetAppSettingsQuery();
  // Preload the enum catalog (status/type labels) for all authenticated
  // routes; MainLayout only mounts once per full page load, so a reload on
  // any page re-fetches this rather than relying on stale in-memory cache.
  useGetEnumCatalogQuery();

  // Centralized: sync the active route's `handle` metadata into the
  // top bar's context, so no individual page needs to set it itself.
  const matches = useMatches();
  const { setHeader } = usePageHeader();
  const routeHandle = [...matches]
    .reverse()
    .find((m) => (m.handle as PageHeaderData | undefined)?.title)
    ?.handle as PageHeaderData | undefined;

  useEffect(() => {
    setHeader(routeHandle ?? {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeHandle?.title, routeHandle?.description, routeHandle?.icon]);

  const [headerEl, setHeaderEl] = useState<HTMLDivElement | null>(null);

  return (
    <SidebarProvider defaultOpen={false}>
      <div className="flex h-screen w-screen overflow-hidden">
        <Sidebar />
        <div className="flex h-screen min-w-0 flex-1 flex-col overflow-hidden">
          <GlobalTopBar />
          <div ref={setHeaderEl} />

          <main
            className={`flex-1 min-h-0 min-w-0 px-6 bg-[var(--color-canvas)] overflow-y-auto scrollbar-custom ${routeHandle?.bodyClassName ?? ""}`}
          >
            <HeaderPortalContext.Provider value={headerEl}>
              <Outlet />
            </HeaderPortalContext.Provider>
          </main>
        </div>
      </div>
    </SidebarProvider>
  );
}
