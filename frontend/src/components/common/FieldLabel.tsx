import { TooltipDisplay } from "@/components/ui/tooltip";

interface FieldLabelProps {
  label: string;
  required?: boolean;
  showTooltip?: boolean;
}

export default function FieldLabel({
  label,
  required,
  showTooltip,
}: FieldLabelProps) {
  return (


    <div className="mb-1.5 flex items-center justify-between">
      <TooltipDisplay content={showTooltip ? label : undefined}>
        <label className="font-semibold text-[13px] text-[var(--ink)]">
          {label}
          {required && (
            <span className="ml-1 text-[var(--error)]" aria-hidden="true">
              *
            </span>
          )}
        </label>
      </TooltipDisplay>
    </div>
  );
}
