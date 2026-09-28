import React from "react";
import { CloseIcon } from "@/assets/icons/CloseIcon";
import { cn } from "@/lib/utils";
import { Dialog, DialogPortal, DialogOverlay } from "@/components/common/Modal/dialog";
import * as DialogPrimitive from "@radix-ui/react-dialog";

interface SourceViewerModalProps {
  isOpen: boolean;
  onClose: () => void;
  fileName: React.ReactNode;
  fileIcon?: React.ReactNode;
  subtitle?: React.ReactNode;
  toolbarActions?: React.ReactNode;
  children: React.ReactNode;
}

export default function SourceViewerModal({
  isOpen,
  onClose,
  fileName,
  fileIcon,
  subtitle,
  toolbarActions,
  children,
}: SourceViewerModalProps) {
  return (
    <Dialog
      open={isOpen}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogPortal>
        <DialogOverlay>
          <DialogPrimitive.Content
            className={cn(
              "relative z-[1200]",
              "w-screen h-screen max-w-none max-h-none",
              "flex flex-col items-stretch gap-0 overflow-hidden",
              "bg-white outline-none",
              "data-[state=open]:animate-dialog-in",
              "data-[state=closed]:animate-dialog-out",
            )}
          >
            {/* Close button - Fixed at top-right */}
            <button
              type="button"
              className={cn(
                "absolute top-5 right-4 z-10",
                "flex items-center justify-center shrink-0",
                "w-9 h-9 rounded-lg",
                "bg-transparent border border-transparent",
                "text-[var(--text-quaternary)] cursor-pointer",
                "transition-all duration-[var(--transition-fast)]",
                "hover:bg-[var(--bg-active)] hover:text-[var(--color-brand-green-500)]",
                "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-brand-green-500)] focus-visible:border-[var(--color-brand-green-500)] focus-visible:rounded-sm",
                "active:bg-[var(--bg-active)]",
              )}
              onClick={onClose}
              aria-label="Close modal"
            >
              <CloseIcon />
            </button>

            {/* Header */}
            <div
              className={cn(
                "flex items-center justify-between gap-4",
                "px-4 py-3 pr-16 shrink-0",
                "border-b border-[var(--color-brand-green-50)]",
              )}
            >
              {/* Left: File info */}
              <div className="flex flex-col items-start gap-0 min-w-0">
                <div className="flex items-center gap-[2px]">
                  {fileIcon}
                  <h2 className="m-0 font-semibold text-[18px] tracking-[0] text-[var(--color-neutral-500)]">
                    {fileName}
                  </h2>
                </div>
                {subtitle && (
                  <div className="mt-1.5 text-[14px] text-[var(--color-neutral-300)]">
                    {subtitle}
                  </div>
                )}
              </div>

              {/* Center: Toolbar Actions */}
              {toolbarActions && (
                <div className="flex items-center gap-2">{toolbarActions}</div>
              )}
            </div>

            {/* Body */}
            <div className="flex-1 overflow-hidden p-0">{children}</div>
          </DialogPrimitive.Content>
        </DialogOverlay>
      </DialogPortal>
    </Dialog>
  );
}
