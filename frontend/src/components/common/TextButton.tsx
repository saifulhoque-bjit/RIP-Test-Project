import type { ButtonHTMLAttributes } from "react";

const colorVariants = {
  accent: "text-[var(--accent)]",
  success: "text-[var(--success)]",
  error: "text-[var(--error)]",
  warn: "text-[var(--warn)]",
  info: "text-[var(--info)]",
  secondary: "text-[var(--text-secondary)]",
} as const;

type TextButtonVariant = keyof typeof colorVariants;

interface TextButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: TextButtonVariant;
}

export default function TextButton({
  variant = "accent",
  className = "",
  children,
  ...props
}: TextButtonProps) {
  return (
    <button
      type="button"
      className={`border-none bg-transparent text-[13px] font-semibold cursor-pointer hover:opacity-80 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:opacity-50 ${colorVariants[variant]} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}
