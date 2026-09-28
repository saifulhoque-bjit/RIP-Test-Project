import * as React from "react";
import * as SwitchPrimitive from "@radix-ui/react-switch";
import { cn } from "@/lib/utils";

type SwitchProps = React.ComponentPropsWithoutRef<
  typeof SwitchPrimitive.Root
> & {
  label?: React.ReactNode;
  labelClassName?: string;
  wrapperClassName?: string;
};

const Switch = React.forwardRef<
  React.ComponentRef<typeof SwitchPrimitive.Root>,
  SwitchProps
>(
  (
    {
      className,
      label,
      labelClassName,
      wrapperClassName,
      id,
      disabled,
      ...props
    },
    ref,
  ) => {
    const generatedId = React.useId();
    const switchId = id ?? generatedId;

    const switchControl = (
      <SwitchPrimitive.Root
        ref={ref}
        id={switchId}
        disabled={disabled}
        className={cn(
          "peer inline-flex h-[22px] w-[40px] shrink-0 items-center rounded-full border-2 border-transparent",
          "transition-colors duration-200",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-green-300)]",
          "focus-visible:ring-offset-2",
          "disabled:cursor-not-allowed disabled:opacity-60",
          "data-[state=checked]:bg-accent",
          "data-[state=unchecked]:bg-[var(--color-neutral-200)]",
          className,
        )}
        {...props}
      >
        <SwitchPrimitive.Thumb
          className={cn(
            "pointer-events-none block h-[18px] w-[18px] rounded-full bg-[var(--white)] shadow-sm",
            "transition-transform duration-200",
            "data-[state=checked]:translate-x-[18px]",
            "data-[state=unchecked]:translate-x-0",
          )}
        />
      </SwitchPrimitive.Root>
    );

    if (label === undefined || label === null || label === "") {
      return switchControl;
    }

    return (
      <label
        htmlFor={switchId}
        className={cn(
          "inline-flex items-center gap-3",
          disabled ? "cursor-not-allowed" : "cursor-pointer",
          wrapperClassName,
        )}
      >
        <span
          className={cn(
            "text-sm leading-5 text-[var(--text-secondary)] select-none",
            disabled && "opacity-60",
            labelClassName,
          )}
        >
          {label}
        </span>
        {switchControl}
      </label>
    );
  },
);

Switch.displayName = "Switch";

export { Switch };
