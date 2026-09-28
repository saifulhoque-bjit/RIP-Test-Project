import { useCallback, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/utils";
import FieldLabel from "@/components/common/FieldLabel";

export interface DropdownOption {
  label: string;
  value: string;
}

export interface DropdownProps {
  label?: string;
  options: DropdownOption[];
  selected?: string;
  onChange: (option: DropdownOption) => void;
  placeholder?: string;
  width?: string;
  className?: string;
  disabled?: boolean;
  size?: "xs" | "sm" | "md" | "lg";
  required?: boolean;
}

const Dropdown = ({
  label,
  options,
  selected,
  onChange,
  placeholder,
  width,
  className,
  disabled = false,
  size = "md",
  required = false,
}: DropdownProps) => {
  const [open, setOpen] = useState(false);
  const [coords, setCoords] = useState<
    | { left: number; width: number; maxHeight: number; top: number; bottom?: undefined }
    | { left: number; width: number; maxHeight: number; bottom: number; top?: undefined }
    | null
  >(null);
  const ref = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const labelId = useId();

  const selectedOption = options.find((o) => o.value === selected);
  const displayLabel = selectedOption?.label ?? placeholder ?? "Select…";

  const OPTIONS_GAP = 4;
  const OPTIONS_MAX_HEIGHT = 256; // matches Tailwind's max-h-64
  const OPTIONS_ITEM_HEIGHT = 40; // approx. li height + gap
  const OPTIONS_CHROME = 12; // ul padding + border

  const optionsCount = options.length;
  const updatePosition = useCallback(() => {
    const rect = ref.current?.getBoundingClientRect();
    if (!rect) return;

    const spaceBelow = window.innerHeight - rect.bottom - OPTIONS_GAP;
    const spaceAbove = rect.top - OPTIONS_GAP;
    // Flip only if the list's *actual* estimated size won't fit below —
    // not the theoretical max height, which would flip short lists unnecessarily.
    const requiredHeight = Math.min(
      optionsCount * OPTIONS_ITEM_HEIGHT + OPTIONS_CHROME,
      OPTIONS_MAX_HEIGHT,
    );

    if (spaceBelow >= requiredHeight || spaceBelow >= spaceAbove) {
      setCoords({
        left: rect.left,
        width: rect.width,
        top: rect.bottom + OPTIONS_GAP,
        maxHeight: Math.max(Math.min(spaceBelow, OPTIONS_MAX_HEIGHT), 0),
      });
    } else {
      setCoords({
        left: rect.left,
        width: rect.width,
        bottom: window.innerHeight - rect.top + OPTIONS_GAP,
        maxHeight: Math.max(Math.min(spaceAbove, OPTIONS_MAX_HEIGHT), 0),
      });
    }
  }, [optionsCount]);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as Node;
      if (
        ref.current &&
        !ref.current.contains(target) &&
        !listRef.current?.contains(target)
      ) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  useEffect(() => {
    if (!open) return;
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [open, updatePosition]);

  return (
    <div
      ref={ref}
      className={cn("relative inline-block", className)}
      style={width ? { width } : undefined}
    >
      {label && <FieldLabel label={label} required={required && !selected} />}

      {/* Trigger — matches v3.html .inp / select field style */}
      <button
        type="button"
        disabled={disabled}
        onClick={() => {
          if (!open) updatePosition();
          setOpen((prev) => !prev);
        }}
        className={cn(
          "inline-flex w-full items-center justify-between rounded-lg border box-border",
          "border-[var(--border-strong)] bg-white text-left font-normal",
          "text-[var(--text-primary)]",
          size === "xs" && "h-8 px-3 py-1.5 text-[13px]",
          size === "sm" && "h-9 px-4 py-2 text-sm",
          size === "md" && "h-[42px] px-4 py-3 text-base",
          size === "lg" && "h-[46px] px-5 py-4 text-sm",
          "transition-[border-color,box-shadow,background-color] duration-150",
          open &&
            "border-[var(--accent-600)] shadow-[0_0_0_3px_rgba(26,80,200,0.15)]",
          "focus-visible:outline-none focus-visible:border-[var(--accent-600)] focus-visible:shadow-[0_0_0_3px_rgba(26,80,200,0.15)]",
          "disabled:cursor-not-allowed disabled:bg-[var(--color-blue-neutral-50)] disabled:text-[var(--color-blue-neutral-700)]",
        )}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-labelledby={label ? labelId : undefined}
      >
        <span className="truncate text-[var(--text-primary)]">
          {displayLabel}
        </span>
        <span
          className={cn(
            "ml-2 shrink-0 text-[13px] leading-none text-[var(--text-tertiary)] transition-transform duration-150",
            open && "rotate-180",
          )}
          aria-hidden="true"
        >
          ▾
        </span>
      </button>

      {/* Options panel — portaled so ancestor `overflow-hidden` (e.g. Panel) never clips it */}
      {open &&
        coords &&
        createPortal(
          <ul
            ref={listRef}
            role="listbox"
            className={cn(
              // pointer-events-auto overrides the `pointer-events: none` Radix Dialog sets on
              // <body> while open — without it, this portaled list would be unclickable.
              "pointer-events-auto fixed z-[1300] flex flex-col gap-1 overflow-auto rounded-lg border border-[var(--border-primary)]",
              "bg-white p-1 shadow-[0_16px_40px_rgba(8,21,52,.16)]",
            )}
            style={{
              left: coords.left,
              width: coords.width,
              maxHeight: coords.maxHeight,
              top: coords.top,
              bottom: coords.bottom,
            }}
          >
            {options.map((option) => (
              <li
                key={option.value}
                role="option"
                aria-selected={option.value === selected}
                onClick={() => {
                  onChange(option);
                  setOpen(false);
                }}
                className={cn(
                  "flex cursor-pointer items-center rounded-md px-3 py-2 text-sm",
                  "text-[var(--text-secondary)] hover:bg-[var(--accent-50)]",
                  option.value === selected &&
                    "font-semibold text-[var(--text-primary)] bg-[var(--accent-50)]",
                )}
              >
                {option.label}
              </li>
            ))}
          </ul>,
          document.body,
        )}
    </div>
  );
};

Dropdown.displayName = "Dropdown";

export { Dropdown };
export default Dropdown;
