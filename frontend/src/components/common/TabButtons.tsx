import { cn } from "@/lib/utils";

export interface TabButtonItem<TValue extends string = string> {
  label: string;
  value: TValue;
  id?: string;
  disabled?: boolean;
}

export interface TabButtonsProps<TValue extends string = string> {
  items: TabButtonItem<TValue>[];
  value: TValue;
  onChange: (value: TValue) => void;
  className?: string;
  buttonClassName?: string;
}

function TabButtons<TValue extends string = string>({
  items,
  value,
  onChange,
  className,
  buttonClassName,
}: TabButtonsProps<TValue>) {
  return (
    <div
      className={cn(
        "inline-flex overflow-hidden rounded-lg border border-[var(--border-primary)] bg-white",
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
            id={item.id}
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
              "h-[34px] border-none px-[14px] text-[13px] font-semibold transition-colors",
              "text-[var(--text-secondary)]",
              "disabled:cursor-not-allowed disabled:opacity-50",
              isActive
                ? "bg-[var(--accent-50)] text-[var(--navy-900)]"
                : "bg-white hover:bg-[#fafbfd]",
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

export { TabButtons };
export default TabButtons;
