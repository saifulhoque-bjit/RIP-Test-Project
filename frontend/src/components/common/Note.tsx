import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** Info (blue) and warning (amber) inline callouts, matching the v3 mockup. */
export function InfoNote({
  icon = "ℹ",
  children,
  className,
}: {
  icon?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex gap-2.5 rounded-md border border-[#bcd8f7] bg-[var(--color-info-50)] px-3.5 py-3 text-[12.5px] leading-snug text-[#0d4a86]",
        className,
      )}
    >
      <span>{icon}</span>
      <span>{children}</span>
    </div>
  );
}

export function WarnNote({
  icon = "⚠",
  children,
  className,
}: {
  icon?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex gap-2.5 rounded-md border border-[#f0d69a] bg-warn-50 px-3.5 py-3 text-[12.5px] leading-snug text-[#7a4e00]",
        className,
      )}
    >
      <span>{icon}</span>
      <span>{children}</span>
    </div>
  );
}

export function Baseline({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-[#b6e3cd] bg-success-50 px-3 py-1 text-xs font-semibold text-[#0a7a52]">
      ◆ {children}
    </span>
  );
}
