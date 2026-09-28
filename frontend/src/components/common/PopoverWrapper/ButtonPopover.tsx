import { useState } from "react";
import type { ReactNode } from "react";
import PopoverWrapper from "./PopoverWrapper";

interface ReusablePopoverBodyProps {
  close: () => void;
  isOpen: boolean;
}

type ButtonPopoverBody =
  | ReactNode
  | ((props: ReusablePopoverBodyProps) => ReactNode);

interface ButtonPopoverProps {
  /** Trigger element wrapped by the popover */
  children: ReactNode;
  /** Dynamic popover body content */
  body: ButtonPopoverBody;
  /** Optional className for popover content */
  contentClassName?: string;
  /** Popover alignment relative to trigger */
  align?: "start" | "center" | "end";
  /** Popover side relative to trigger */
  side?: "top" | "right" | "bottom" | "left";
  /** Distance from trigger */
  sideOffset?: number;
  /** Controlled open state */
  open?: boolean;
  /** Initial open state for uncontrolled usage */
  defaultOpen?: boolean;
  /** Optional callback when open state changes */
  onOpenChange?: (open: boolean) => void;
  /** Optional callback when popover closes */
  onClose?: () => void;
}

export default function ButtonPopover({
  children,
  body,
  contentClassName = "w-56 p-2",
  align = "end",
  side = "bottom",
  sideOffset = 4,
  open: openProp,
  defaultOpen = false,
  onOpenChange,
  onClose,
}: ButtonPopoverProps) {
  const isControlled = openProp !== undefined;
  const [uncontrolledOpen, setUncontrolledOpen] = useState(defaultOpen);

  const open = isControlled ? openProp : uncontrolledOpen;

  const handleOpenChange = (nextOpen: boolean) => {
    if (!isControlled) {
      setUncontrolledOpen(nextOpen);
    }

    if (!nextOpen && open) {
      onClose?.();
    }

    onOpenChange?.(nextOpen);
  };

  const close = () => {
    if (!open) return;
    handleOpenChange(false);
  };

  const resolvedBody =
    typeof body === "function" ? body({ close, isOpen: open }) : body;

  return (
    <PopoverWrapper
      trigger={children}
      contentClassName={contentClassName}
      align={align}
      side={side}
      sideOffset={sideOffset}
      open={open}
      onOpenChange={handleOpenChange}
      controlled={true}
    >
      {resolvedBody}
    </PopoverWrapper>
  );
}
