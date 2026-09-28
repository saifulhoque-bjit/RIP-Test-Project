import { type ReactNode } from "react";

import Button from "@/components/common/Button/Button";
import { cn } from "@/lib/utils";

export interface RightDrawerLayoutProps {
  isOpen: boolean;
  title: string;
  count?: number;
  onClose: () => void;
  children?: ReactNode;
  className?: string;
  bodyClassName?: string;
  footerClassName?: string;
  width?: number;
  hideFooter?: boolean;
  footerContent?: ReactNode;
  secondaryActionLabel?: string;
  primaryActionLabel?: string;
  onSecondaryAction?: () => void;
  onPrimaryAction?: () => void;
  primaryActionDisabled?: boolean;
  primaryActionLoading?: boolean;
}

function RightDrawerLayout({
  isOpen,
  title,
  count,
  onClose,
  children,
  className,
  bodyClassName,
  footerClassName,
  width = 400,
  hideFooter = false,
  footerContent,
  secondaryActionLabel = "Cancel",
  primaryActionLabel = "Add to feedback",
  onSecondaryAction,
  onPrimaryAction,
  primaryActionDisabled = false,
  primaryActionLoading = false,
}: RightDrawerLayoutProps) {
  return (
    <aside
      className={cn(
        "fixed inset-y-0 right-0 z-[200] flex h-screen max-w-full flex-col",
        "bg-white border-l border-[var(--border-primary)]",
        "shadow-[0_16px_40px_rgba(8,21,52,.24)]",
        "transform-gpu will-change-transform transition-transform duration-[220ms] ease-out motion-reduce:transition-none",
        isOpen ? "translate-x-0" : "translate-x-full pointer-events-none",
        className,
      )}
      style={{ width }}
      aria-hidden={!isOpen}
    >
      <div className="flex items-center justify-between border-b border-[var(--border-primary)] px-[18px] py-4">
        <h3 className="m-0 text-[15px] font-semibold text-[var(--text-primary)]">
          {title}
          {typeof count === "number" && (
            <span className="font-semibold"> · {count}</span>
          )}
        </h3>

        <button
          type="button"
          onClick={onClose}
          className="border-none bg-transparent p-0 text-[19px] text-[var(--text-tertiary)]"
          aria-label="Close drawer"
        >
          ✕
        </button>
      </div>

      <div className={cn("flex-1 overflow-auto p-[18px]", bodyClassName)}>
        {children}
      </div>

      {!hideFooter && (
        <div
          className={cn(
            "flex justify-end gap-2.5 border-t border-[var(--border-primary)] px-[18px] py-[14px]",
            footerClassName,
          )}
        >
          {footerContent ?? (
            <>
              <Button
                size="sm"
                variant="ghost"
                onClick={onSecondaryAction ?? onClose}
              >
                {secondaryActionLabel}
              </Button>

              <Button
                size="sm"
                onClick={onPrimaryAction}
                disabled={primaryActionDisabled}
                loading={primaryActionLoading}
              >
                {primaryActionLabel}
              </Button>
            </>
          )}
        </div>
      )}
    </aside>
  );
}

export { RightDrawerLayout };
export default RightDrawerLayout;
