import React, { forwardRef } from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { SpinnerIcon } from "@/assets/icons/SpinnerIcon";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  [
    "inline-flex items-center justify-center gap-1.5",
    "rounded-lg border font-semibold leading-none",
    "transition-colors duration-200",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-600)]/35 focus-visible:ring-offset-2",
    "disabled:cursor-not-allowed disabled:pointer-events-none",
    "disabled:bg-[#aebfdd] disabled:border-transparent disabled:text-white",
  ],
  {
    variants: {
      variant: {
        primary:
          "bg-[var(--accent-600)] border-transparent text-white hover:brightness-95",
        ghost:
          "bg-transparent border-[var(--color-border-strong)] text-[var(--text-secondary)] hover:bg-[var(--accent-50)]",
        success:
          "bg-[var(--success)] border-transparent text-white hover:brightness-95",
        ai: "bg-[var(--ai-draft)] border-transparent text-white hover:brightness-95",
        danger:
          "bg-[var(--error)] border-transparent text-white hover:brightness-95",
        link: "bg-transparent border-transparent text-[var(--accent-600)] underline-offset-4 disabled:bg-transparent disabled:text-[var(--text-disabled,#aebfdd)]",
      },
      size: {
        xxs: "h-7 px-3 text-[12px]",
        xs: "h-8 px-3 text-[13px]",
        sm: "h-9 px-4 text-sm",
        md: "h-[42px] px-4 text-base",
        lg: "h-[46px] px-5 text-sm",
      },
      fullWidth: {
        true: "w-full",
        false: "",
      },
    },
    compoundVariants: [
      {
        variant: "link",
        className: "h-auto w-auto p-0",
      },
    ],
    defaultVariants: {
      variant: "primary",
      size: "md",
      fullWidth: false,
    },
  },
);

export interface ButtonProps
  extends
    React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  loading?: boolean;
  loadingText?: string;
  iconLeading?: React.ReactNode;
  iconTrailing?: React.ReactNode;
}

const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      variant,
      size,
      fullWidth,
      loading = false,
      loadingText,
      iconLeading,
      iconTrailing,
      disabled = false,
      className,
      children,
      onClick,
      type = "button",
      ...props
    },
    ref,
  ) => {
    const isInactive = Boolean(disabled || loading);
    const label = loading && loadingText ? loadingText : children;

    return (
      <button
        ref={ref}
        type={type}
        className={cn(
          buttonVariants({ variant, size, fullWidth }),
          loading && "cursor-wait",
          className,
        )}
        disabled={isInactive}
        aria-disabled={isInactive || undefined}
        aria-busy={loading || undefined}
        onClick={isInactive ? undefined : onClick}
        {...props}
      >
        {loading ? (
          <SpinnerIcon
            className={cn(
              "h-4 w-4 flex-shrink-0 animate-spin",
              size === "lg" && "h-4 w-4",
            )}
          />
        ) : iconLeading ? (
          <span className="w-4 h-4 inline-flex items-center justify-center flex-shrink-0">
            {iconLeading}
          </span>
        ) : null}

        {label !== undefined && label !== null && (
          <span className="leading-3 inline-flex justify-center items-center text-center">
            {label}
          </span>
        )}

        {!loading && iconTrailing ? (
          <span className="w-4 h-4 inline-flex items-center justify-center flex-shrink-0">
            {iconTrailing}
          </span>
        ) : null}
      </button>
    );
  },
);

Button.displayName = "Button";

export { Button };
export default Button;
