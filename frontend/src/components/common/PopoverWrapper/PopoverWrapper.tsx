import { useState } from "react";
import type { ReactNode } from "react";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

interface PopoverWrapperProps {
  /** The trigger element (button, icon, etc.) */
  trigger: ReactNode;
  /** The content to display inside the popover */
  children: ReactNode;
  /** Optional className for PopoverContent */
  contentClassName?: string;
  /** Alignment of the popover relative to the trigger */
  align?: "start" | "center" | "end";
  /** Side of the trigger where popover appears */
  side?: "top" | "right" | "bottom" | "left";
  /** Distance from the trigger in pixels */
  sideOffset?: number;
  /** Controlled open state (optional) */
  open?: boolean;
  /** Callback when open state changes (optional) */
  onOpenChange?: (open: boolean) => void;
  /** Whether to use controlled state (default: false) */
  controlled?: boolean;
}

export default function PopoverWrapper({
  trigger,
  children,
  contentClassName = "w-56 p-2",
  align = "end",
  side = "bottom",
  sideOffset = 4,
  open: controlledOpen,
  onOpenChange: controlledOnOpenChange,
  controlled = false,
}: PopoverWrapperProps) {
  const [internalOpen, setInternalOpen] = useState(false);

  const open = controlled ? controlledOpen : internalOpen;
  const onOpenChange = controlled ? controlledOnOpenChange : setInternalOpen;

  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger asChild>{trigger}</PopoverTrigger>
      <PopoverContent
        className={`border-[1px] border-[color:var(--color-brand-green-50)] ${contentClassName}`}
        align={align}
        side={side}
        sideOffset={sideOffset}
      >
        {children}
      </PopoverContent>
    </Popover>
  );
}
