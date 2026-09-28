import * as React from "react";
import { cn } from "@/lib/utils";
import { SidebarContext, useSidebar } from "@/components/ui/sidebar-context";

export function SidebarProvider({
  defaultOpen = true,
  children,
}: {
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = React.useState(defaultOpen);

  const toggleSidebar = React.useCallback(() => {
    setOpen((current) => !current);
  }, []);

  return (
    <SidebarContext.Provider value={{ open, setOpen, toggleSidebar }}>
      <div
        className="group/sidebar flex h-screen w-full"
        data-collapsed={!open}
      >
        {children}
      </div>
    </SidebarContext.Provider>
  );
}

export function Sidebar({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  const { open } = useSidebar();

  return (
    <aside
      className={cn(
        "fixed top-0 left-0 h-screen flex flex-col z-[1000] bg-gradient-to-b from-brand-green-50 to-brand-blue-50 shadow-[0_2px_4px_0_rgba(176,228,221,1)] transition-[width] duration-200",
        open ? "w-sidebar" : "w-sidebar-collapsed 2xl:w-sidebar-collapsed-2xl",
        className,
      )}
    >
      {children}
    </aside>
  );
}

export function SidebarHeader({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={cn("border-b border-brand-green-100", className)}>
      {children}
    </div>
  );
}

export function SidebarContent({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex-1 overflow-y-auto overflow-x-hidden scrollbar-thin scrollbar-thumb-transparent scrollbar-track-transparent",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function SidebarGroup({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return <div className={cn(className)}>{children}</div>;
}

export function SidebarFooter({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return <div className={cn(className)}>{children}</div>;
}
