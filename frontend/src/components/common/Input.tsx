import React, { forwardRef, useState } from "react";

import { cn } from "@/lib/utils";
import FieldLabel from "./FieldLabel";

export interface InputProps extends Omit<
  React.InputHTMLAttributes<HTMLInputElement>,
  "size"
> {
  label?: string;
  error?: boolean;
  errorMessage?: string;
  required?: boolean;
  showCharacterCount?: boolean;
  maxLength?: number;
  hint?: string;
  size?: "xs" | "sm" | "md" | "lg";
  startIcon?: React.ReactNode;
  endIcon?: React.ReactNode;
  startIconAriaLabel?: string;
  endIconAriaLabel?: string;
  onStartIconClick?: () => void;
  onEndIconClick?: () => void;
}

const Input = forwardRef<HTMLInputElement, InputProps>(
  (
    {
      label,
      error = false,
      errorMessage,
      required = false,
      showCharacterCount = false,
      maxLength,
      hint,
      size = "md",
      type = "text",
      startIcon,
      endIcon,
      startIconAriaLabel,
      endIconAriaLabel,
      onStartIconClick,
      onEndIconClick,
      className,
      value,
      onChange,
      defaultValue,
      ...props
    },
    ref,
  ) => {
    const initialValue =
      typeof defaultValue === "string" || typeof defaultValue === "number"
        ? String(defaultValue)
        : "";
    const [internalValue, setInternalValue] = useState(initialValue);
    const isControlled = value !== undefined;
    const currentValue = isControlled
      ? String(value ?? "")
      : String(internalValue ?? "");
    const charCount = currentValue.length;

    const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
      if (!isControlled) {
        setInternalValue(e.target.value);
      }
      onChange?.(e);
    };

    return (
      <div className="w-full">
        {label && <FieldLabel label={label} required={required} />}

        <div className="relative">
          {startIcon &&
            (onStartIconClick ? (
              <button
                type="button"
                onClick={onStartIconClick}
                aria-label={startIconAriaLabel}
                className="absolute left-3 top-1/2 z-10 -translate-y-1/2 text-[var(--text-tertiary)] hover:text-[var(--text-primary)]"
              >
                {startIcon}
              </button>
            ) : (
              <span className="pointer-events-none absolute left-3 top-1/2 z-10 -translate-y-1/2 text-[var(--text-tertiary)]">
                {startIcon}
              </span>
            ))}

          <input
            ref={ref}
            type={type}
            value={isControlled ? value : internalValue}
            defaultValue={isControlled ? undefined : defaultValue}
            onChange={handleChange}
            maxLength={maxLength}
            className={cn(
              "w-full rounded-lg border font-normal outline-none transition-colors flex items-center",
              size === "xs" && "h-8 px-3 py-1.5 text-[13px]",
              size === "sm" && "h-9 px-4 py-2 text-sm",
              size === "md" && "h-[42px] px-4 py-3 text-base",
              size === "lg" && "h-[46px] px-5 py-4 text-sm",
              startIcon && "pl-9",
              endIcon && "pr-9",
              "text-[var(--text-primary)] placeholder-[var(--text-tertiary)]",
              error
                ? "border-[var(--error)] focus:border-[var(--error)] focus:shadow-[0_0_0_3px_rgba(217,45,32,0.15)]"
                : "border-[var(--border-primary)] focus:border-[var(--accent-600)] focus:shadow-[0_0_0_3px_rgba(26,80,200,0.15)]",
              "disabled:cursor-not-allowed disabled:bg-[var(--color-blue-neutral-50)] disabled:text-[var(--color-blue-neutral-700)]",
              className,
            )}
            {...props}
          />

          {endIcon &&
            (onEndIconClick ? (
              <button
                type="button"
                onClick={onEndIconClick}
                aria-label={endIconAriaLabel}
                className="absolute right-3 top-1/2 z-10 -translate-y-1/2 text-[var(--text-tertiary)] hover:text-[var(--text-primary)]"
              >
                {endIcon}
              </button>
            ) : (
              <span className="pointer-events-none absolute right-3 top-1/2 z-10 -translate-y-1/2 text-[var(--text-tertiary)]">
                {endIcon}
              </span>
            ))}
        </div>

        {showCharacterCount && typeof maxLength === "number" && (
          <div className="mt-1 text-left text-xs text-[var(--text-tertiary)]">
            {charCount} / {maxLength}
          </div>
        )}

        {error && errorMessage && (
          <div className="mt-1 text-xs text-[var(--error)]">{errorMessage}</div>
        )}

        {hint && !error && (
          <div className="mt-1 text-xs text-[var(--text-tertiary)]">{hint}</div>
        )}
      </div>
    );
  },
);

Input.displayName = "Input";

export { Input };
export default Input;
