import React, { forwardRef } from "react";

import { cn } from "@/lib/utils";
import FieldLabel from "./FieldLabel";

export interface TextAreaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  error?: boolean;
  errorMessage?: string;
  required?: boolean;
  hint?: string;
  helperText?: string;
  rows?: number;
}

const TextArea = forwardRef<HTMLTextAreaElement, TextAreaProps>(
  (
    {
      label,
      error = false,
      errorMessage,
      required = false,
      hint,
      helperText,
      rows,
      className,
      ...props
    },
    ref,
  ) => {
    return (
      <div className="w-full">
        {label && <FieldLabel label={label} required={required} />}

        <textarea
          ref={ref}
          rows={rows}
          className={cn(
            "w-full resize-vertical rounded-lg border px-3 py-3 font-normal outline-none transition-colors",
            rows ? "min-h-0" : "min-h-[84px]",
            "text-sm text-[var(--text-primary)] placeholder-[var(--text-tertiary)]",
            error
              ? "border-[var(--error)] focus:border-[var(--error)] focus:shadow-[0_0_0_3px_rgba(217,45,32,0.15)]"
              : "border-[var(--border-primary)] focus:border-[var(--accent-600)] focus:shadow-[0_0_0_3px_rgba(26,80,200,0.15)]",
            "disabled:cursor-not-allowed disabled:bg-[var(--color-blue-neutral-50)] disabled:text-[var(--color-blue-neutral-700)]",
            className,
          )}
          {...props}
        />

        {error && errorMessage && (
          <div className="mt-1 text-xs text-[var(--error)]">{errorMessage}</div>
        )}

        {hint && !error && (
          <div className="mt-1 text-xs text-[var(--text-tertiary)]">{hint}</div>
        )}

        {helperText && !error && (
          <div className="mt-1 text-xs text-[var(--text-quaternary)]">
            {helperText}
          </div>
        )}
      </div>
    );
  },
);

TextArea.displayName = "TextArea";

export { TextArea };
export default TextArea;
