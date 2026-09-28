import React, { forwardRef } from "react";

import { cn } from "@/lib/utils";
import { SearchIcon } from "@/assets/icons/SearchIcon";

export interface SearchInputProps extends Omit<
  React.InputHTMLAttributes<HTMLInputElement>,
  "size"
> {
  icon?: React.ReactNode;
  wrapperClassName?: string;
  size?: "xs" | "sm" | "md" | "lg";
}

const SearchInput = forwardRef<HTMLInputElement, SearchInputProps>(
  (
    {
      icon,
      className,
      wrapperClassName,
      size = "md",
      placeholder = "Search requirements...",
      type = "search",
      ...props
    },
    ref,
  ) => {
    return (
      <div className={cn("relative w-[230px]", wrapperClassName)}>
        <span
          aria-hidden="true"
          className={cn(
            "pointer-events-none absolute inset-y-0 left-[11px] flex items-center",
            "text-[var(--text-tertiary)] leading-none",
          )}
        >
          {icon ?? <SearchIcon className="w-3.5 h-3.5" />}
        </span>

        <input
          ref={ref}
          type={type}
          placeholder={placeholder}
          className={cn(
            "w-full rounded-lg border border-[var(--border-primary)] bg-white text-[var(--text-primary)]",
            size === "xs" && "h-8 pl-7 pr-3 text-[13px]",
            size === "sm" && "h-9 pl-8 pr-4 text-sm",
            size === "md" && "h-[42px] pl-9 pr-4 text-base",
            size === "lg" && "h-[46px] pl-10 pr-5 text-sm",
            "placeholder:text-[var(--text-tertiary)] outline-none",
            "focus:border-[var(--accent-600)] focus:shadow-[0_0_0_3px_rgba(46,99,222,.35)]",
            className,
          )}
          {...props}
        />
      </div>
    );
  },
);

SearchInput.displayName = "SearchInput";

export { SearchInput };
export default SearchInput;
