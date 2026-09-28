import { cn } from "@/lib/utils";

export function Toggle({
  on,
  onChange,
  disabled = false,
}: {
  on: boolean;
  onChange?: (next: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      disabled={disabled}
      onClick={() => !disabled && onChange?.(!on)}
      className={cn(
        "relative h-[22px] w-10 shrink-0 rounded-full transition-colors",
        on ? "bg-accent" : "bg-[#cdd5e0]",
        disabled && "cursor-not-allowed opacity-45",
      )}
    >
      <span
        className={cn(
          "absolute top-0.5 h-[18px] w-[18px] rounded-full bg-white transition-all",
          on ? "left-5" : "left-0.5",
        )}
      />
    </button>
  );
}

export default Toggle;
