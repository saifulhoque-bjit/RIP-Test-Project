import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function Panel({
  title,
  aside,
  children,
  className,
  bodyClassName,
}: {
  title?: ReactNode;
  aside?: ReactNode;
  children?: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section
      className={cn(
        "mb-[18px] overflow-hidden rounded-lg border border-border bg-surface shadow-e1",
        className,
      )}
    >
      {(title || aside) && (
        <header className="flex items-center justify-between border-b border-border bg-[var(--white)] px-[18px] py-3.5">
          {typeof title === "string" ? (
            <h3 className="text-[15px] font-semibold">{title}</h3>
          ) : (
            title
          )}
          {aside && <div className="text-xs text-sec">{aside}</div>}
        </header>
      )}
      {children != null && (
        <div className={cn(bodyClassName ?? "p-[18px]")}>{children}</div>
      )}
    </section>
  );
}

export function FormCard({
  title,
  sub,
  action,
  children,
  className,
}: {
  title: string;
  sub?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cn(
        "mb-[18px] rounded-lg border border-border bg-surface p-[22px] shadow-e1",
        className,
      )}
    >
      <div className="flex items-center justify-between">
        <h3 className="text-[15px] font-semibold">{title}</h3>
        {action}
      </div>
      {sub && <p className="mt-1 mb-4 text-[12.5px] leading-relaxed text-sec">{sub}</p>}
      {!sub && <div className="mb-4" />}
      {children}
    </section>
  );
}
