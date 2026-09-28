import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface TimelineItem {
  key: string;
  icon: ReactNode;
  iconClassName?: string;
  title: ReactNode;
  titleClassName?: string;
  subtitle?: ReactNode;
  subtitleClassName?: string;
  /** Color of the connector line below this item (defaults to bg-border). */
  connectorClassName?: string;
}

/** Vertical step/activity timeline: connected nodes with a title + subtitle each. */
export function Timeline({
  items,
  className,
}: {
  items: TimelineItem[];
  className?: string;
}) {
  return (
    <div className={cn("relative ml-1.5", className)}>
      {items.map((item, i) => {
        const isLast = i === items.length - 1;
        return (
          <div key={item.key} className="relative flex gap-3 py-2.5">
            {!isLast && (
              <span
                className={cn(
                  "absolute bottom-[-10px] left-[10px] top-[22px] w-0.5",
                  item.connectorClassName ?? "bg-border",
                )}
              />
            )}
            <div
              className={cn(
                "relative z-10 grid h-[22px] w-[22px] flex-shrink-0 place-items-center rounded-full text-[11px]",
                item.iconClassName,
              )}
            >
              {item.icon}
            </div>
            <div>
              <div className={cn("text-[13px] font-semibold", item.titleClassName)}>
                {item.title}
              </div>
              {item.subtitle && (
                <div className={cn("text-[11px] text-mut", item.subtitleClassName)}>
                  {item.subtitle}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
