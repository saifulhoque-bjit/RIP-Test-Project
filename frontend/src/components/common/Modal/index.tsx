import React from "react";
import { CloseIcon } from "@/assets/icons/CloseIcon";
import IconContainer from "@/components/common/IconContainer";
import { cn } from "@/lib/utils";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/common/Modal/dialog";

// ─────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────

export interface ModalProps {
  /** Controls visibility of the modal */
  isOpen: boolean;
  /** Called when the modal requests to be closed (backdrop click, ESC key, close icon) */
  onClose: () => void;
  /** Optional header title displayed on the left */
  title?: React.ReactNode;
  /** Optional subtitle rendered beneath the title */
  subtitle?: React.ReactNode;
  /** Optional icon rendered at top-left of modal header */
  headerIcon?: React.ReactNode;
  /** If false, the header section is not rendered even if title/subtitle are provided.
   *  Defaults to true when title or subtitle is present. Can be explicitly set. */
  showHeader?: boolean;
  /** If false, the header text content (icon, title, subtitle) is not rendered.
   *  Defaults to true when headerIcon, title, or subtitle are provided. Can be explicitly set. */
  showHeaderContent?: boolean;
  /** If false, the close button in the header is not rendered. Defaults to true. */
  showCloseButton?: boolean;
  /** Optional footer content rendered at the bottom of the modal */
  footer?: React.ReactNode;
  /** If false, the footer section is not rendered. Defaults to true when footer is provided. */
  showFooter?: boolean;
  /** Modal body content */
  children?: React.ReactNode;
  /** Override modal width. Accepts any valid CSS width value. */
  width?: string | number;
  /** Override modal max-height. Accepts any valid CSS value. */
  maxHeight?: string | number;
  /** Additional className applied to the modal panel */
  className?: string;
  /** Accessible label for the modal dialog */
  "aria-label"?: string;
  /** ID of an element that labels the modal (takes precedence over aria-label) */
  "aria-labelledby"?: string;
  /** ID of an element that describes the modal */
  "aria-describedby"?: string;
  /** If true, clicking the backdrop will NOT close the modal */
  disableBackdropClose?: boolean;
  /** If true, pressing ESC will NOT close the modal */
  disableEscClose?: boolean;
}

// ─────────────────────────────────────────────
// Modal Component
// ─────────────────────────────────────────────

const Modal = React.forwardRef<HTMLDivElement, ModalProps>(
  (
    {
      isOpen,
      onClose,
      title,
      subtitle,
      headerIcon,
      showHeader,
      showHeaderContent,
      showCloseButton = true,
      footer,
      showFooter,
      children,
      width,
      maxHeight,
      className,
      "aria-label": ariaLabel,
      "aria-labelledby": ariaLabelledBy,
      "aria-describedby": ariaDescribedBy,
      disableBackdropClose = true,
      disableEscClose = false,
    },
    ref,
  ) => {
    // Derive whether header/footer sections are rendered
    const renderHeaderContent =
      showHeaderContent !== undefined
        ? showHeaderContent
        : !!(headerIcon || title || subtitle);
    const renderHeader =
      showHeader !== undefined
        ? showHeader
        : renderHeaderContent || showCloseButton;
    const renderFooter = showFooter !== undefined ? showFooter : !!footer;

    // ── Inline override styles ───────────────────────────────────────────────
    const panelStyle: React.CSSProperties = {};
    if (width !== undefined)
      panelStyle.width = typeof width === "number" ? `${width}px` : width;
    if (maxHeight !== undefined)
      panelStyle.maxHeight =
        typeof maxHeight === "number" ? `${maxHeight}px` : maxHeight;

    return (
      <Dialog
        open={isOpen}
        onOpenChange={(open) => {
          if (!open) onClose();
        }}
      >
        <DialogContent
          ref={ref}
          className={cn(
            "h-auto w-[560px] max-w-[92vw] max-h-[88vh] !rounded-[14px] !overflow-hidden border border-[var(--border-primary)]",
            className,
          )}
          style={panelStyle}
          aria-label={!ariaLabelledBy && !title ? ariaLabel : undefined}
          aria-labelledby={ariaLabelledBy}
          aria-describedby={ariaDescribedBy}
          onPointerDownOutside={
            disableBackdropClose ? (e) => e.preventDefault() : undefined
          }
          onEscapeKeyDown={
            disableEscClose ? (e) => e.preventDefault() : undefined
          }
          data-testid="modal-panel"
        >
          {/* ── Header ──────────────────────────────────────────────────── */}
          {renderHeader && (
            <DialogHeader
              className={cn(
                "!px-5 !pt-4 !pb-4 pr-14",
                renderHeaderContent &&
                  "border-b border-[var(--border-primary)]",
              )}
            >
              {renderHeaderContent && (
                <div className="flex flex-col items-start gap-0 min-w-0">
                  {headerIcon && (
                    <IconContainer className="mb-4">{headerIcon}</IconContainer>
                  )}
                  {title && (
                    <DialogTitle className="text-[17px] font-semibold text-[var(--text-primary)]">
                      {title}
                    </DialogTitle>
                  )}
                  {subtitle && (
                    <DialogDescription className={cn(title && "mt-1.5")}>
                      {subtitle}
                    </DialogDescription>
                  )}
                </div>
              )}
              {showCloseButton && (
                <button
                  type="button"
                  className={cn(
                    "absolute top-4 right-4",
                    "flex items-center justify-center shrink-0",
                    "w-7 h-7 rounded-md",
                    "border-none bg-transparent",
                    "text-[19px] text-[var(--text-quaternary)] cursor-pointer",
                    "transition-colors duration-150",
                    "hover:text-[var(--text-secondary)]",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-600)]/35",
                  )}
                  onClick={onClose}
                  aria-label="Close modal"
                >
                  <CloseIcon />
                </button>
              )}
            </DialogHeader>
          )}

          {/* ── Body ────────────────────────────────────────────────────── */}
          <div
            className={cn(
              "flex-1 overflow-auto p-5",
              "[&::-webkit-scrollbar]:w-1.5",
              "[&::-webkit-scrollbar-track]:bg-[#f4f5f6] [&::-webkit-scrollbar-track]:rounded-sm",
              "[&::-webkit-scrollbar-thumb]:bg-[#dadfe2] [&::-webkit-scrollbar-thumb]:rounded-sm",
              "[&::-webkit-scrollbar-thumb:hover]:bg-[var(--color-blue-neutral-700)]",
            )}
          >
            {children}
          </div>

          {/* ── Footer ──────────────────────────────────────────────────── */}
          {renderFooter && footer && (
            <DialogFooter className="!justify-end !gap-2.5 !px-5 !pt-[14px] !pb-[14px] border-t border-[var(--border-primary)]">
              {footer}
            </DialogFooter>
          )}
        </DialogContent>
      </Dialog>
    );
  },
);

Modal.displayName = "Modal";

export default Modal;
