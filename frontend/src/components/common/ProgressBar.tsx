import { cn } from "@/lib/utils";
import { Progress } from "@/components/ui/progress";

interface ProgressBarProps {
  value: number;
  max?: number;
  showPercentage?: boolean;
  className?: string;
  valueLabelFormatter?: (percentage: number) => string;
}

const clamp = (value: number, min: number, max: number): number =>
  Math.min(Math.max(value, min), max);

export default function ProgressBar({
  value,
  max = 100,
  showPercentage = true,
  className,
  valueLabelFormatter,
}: ProgressBarProps) {
  const safeMax = max > 0 ? max : 100;
  const percentage = clamp((value / safeMax) * 100, 0, 100);
  const valueLabel = valueLabelFormatter
    ? valueLabelFormatter(percentage)
    : `${Math.round(percentage)}%`;

  return (
    <div className={cn("flex flex-row items-center justify-center gap-3 w-full h-5", className)}>
      <Progress value={percentage} />

      {showPercentage && (
        <span className="flex items-center shrink-0 text-base font-medium leading-none text-[var(--color-text-secondary)]">
          {valueLabel}
        </span>
      )}
    </div>
  );
}
