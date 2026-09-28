import { cn } from "@/lib/utils";

export interface PillTabItem<TValue extends string = string> {
  label: string;
  value: TValue;
  disabled?: boolean;
}

export interface PillTabsProps<TValue extends string = string> {
  items: PillTabItem<TValue>[];
  value: TValue;
  onChange: (value: TValue) => void;
  /** Stretch to fill the container width, with each tab sharing equal space. */
  full?: boolean;
  className?: string;
  buttonClassName?: string;
}

function PillTabs<TValue extends string = string>({
  items,
  value,
  onChange,
  full = false,
  className,
  buttonClassName,
}: PillTabsProps<TValue>) {
  return (
    <div
      className={cn(
        "inline-flex justify-start items-start rounded-lg border border-border bg-surface p-0.5 text-[12.5px] font-semibold shadow-e1",
        full ? "w-full" : "w-max",
        className,
      )}
      role="tablist"
      aria-orientation="horizontal"
    >
      {items.map((item) => {
        const isActive = item.value === value;

        return (
          <button
            key={item.value}
            type="button"
            role="tab"
            aria-selected={isActive}
            aria-disabled={item.disabled || undefined}
            disabled={item.disabled}
            onClick={() => {
              if (!item.disabled) {
                onChange(item.value);
              }
            }}
            className={cn(
              "rounded-md px-3.5 py-1.5 transition-colors duration-200 ease-in-out",
              full && "flex-1 text-center",
              isActive ? "bg-accent text-white" : "text-sec hover:text-ink",
              "disabled:cursor-not-allowed disabled:opacity-50",
              buttonClassName,
            )}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}

export { PillTabs };
export default PillTabs;
