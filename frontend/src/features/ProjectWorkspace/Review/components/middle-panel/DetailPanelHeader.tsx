import type { ReactNode } from "react";

interface DetailPanelHeaderProps {
  title: string;
  code?: string;
  badges?: ReactNode;
  actions?: ReactNode;
}

export default function DetailPanelHeader({
  title,
  code,
  badges,
  actions,
}: DetailPanelHeaderProps) {
  return (
    <div className="border-b border-[var(--border-primary)] px-[18px] py-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="m-0 text-[15px] font-bold">{title}</h3>
          {code && (
            <p className="m-0 mt-1 text-[12.5px] text-[var(--text-secondary)]">
              {code}
            </p>
          )}
        </div>
        {actions}
      </div>

      {badges && <div className="mt-2 flex flex-wrap gap-1.5">{badges}</div>}
    </div>
  );
}
