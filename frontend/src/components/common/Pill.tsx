import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

type Tone = "neutral" | "req" | "opt" | "ai" | "ver" | "conf" | "warn" | "err";

const TONE: Record<Tone, string> = {
  neutral: "bg-[#eef1f5] text-sec",
  req: "bg-error-50 text-error",
  opt: "bg-[#eef1f5] text-sec",
  ai: "bg-ai-50 text-ai",
  ver: "bg-success-50 text-success",
  conf: "bg-info-50 text-info",
  warn: "bg-warn-50 text-warn",
  err: "bg-error-50 text-error",
};

export function Pill({
  tone = "neutral",
  children,
  className,
}: {
  tone?: Tone;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md px-2 py-[2px] text-[10.5px] font-bold",
        TONE[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
