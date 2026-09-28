import { cn } from "@/lib/utils";

export interface RadioOption<T extends string> {
  value: T;
  label: string;
}

export function RadioGroup<T extends string>({
  name,
  options,
  value,
  onChange,
  disabled = false,
  className,
}: {
  name: string;
  options: RadioOption<T>[];
  value: T;
  onChange: (value: T) => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center gap-6", className)}>
      {options.map((opt) => (
        <label
          key={opt.value}
          className={cn(
            "inline-flex items-center gap-2 text-sm text-[var(--text-secondary)]",
            disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer",
          )}
        >
          <input
            type="radio"
            name={name}
            value={opt.value}
            checked={value === opt.value}
            disabled={disabled}
            onChange={() => onChange(opt.value)}
            className={cn(
              "h-4 w-4 shrink-0 cursor-pointer appearance-none rounded-full border-2 border-border-strong bg-white transition-colors",
              "checked:border-[5px] checked:border-accent",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1",
              "disabled:cursor-not-allowed disabled:opacity-60",
            )}
          />
          {opt.label}
        </label>
      ))}
    </div>
  );
}

export default RadioGroup;
