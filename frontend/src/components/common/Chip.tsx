import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

type Tone = "neutral" | "ok" | "off" | "warn" | "info" | "ai" | "err";

type ToneStyle = {
  fg: string;
  bg: string;
  dot: string;
};

const TONE: Record<Tone, ToneStyle> = {
  neutral: {
    fg: "var(--sec, var(--text-secondary))",
    bg: "#eef1f5",
    dot: "var(--sec, var(--text-secondary))",
  },
  ok: {
    fg: "var(--success)",
    bg: "var(--success-50)",
    dot: "var(--success)",
  },
  off: {
    fg: "var(--mut, var(--text-quaternary))",
    bg: "#eef1f5",
    dot: "var(--mut, var(--text-quaternary))",
  },
  warn: {
    fg: "var(--warn, var(--warning))",
    bg: "var(--warn-50, var(--warning-50))",
    dot: "var(--warn, var(--warning))",
  },
  info: {
    fg: "var(--info)",
    bg: "var(--info-50)",
    dot: "var(--info)",
  },
  ai: {
    fg: "var(--ai, var(--ai-draft))",
    bg: "var(--ai-50)",
    dot: "var(--ai, var(--ai-draft))",
  },
  err: {
    fg: "var(--error)",
    bg: "var(--error-50)",
    dot: "var(--error)",
  },
};

export function Chip({
  tone = "neutral",
  dot = false,
  children,
  className,
}: {
  tone?: Tone;
  dot?: boolean;
  children: ReactNode;
  className?: string;
}) {
  const t = TONE[tone];
  return (
    <span
      style={{ color: t.fg, backgroundColor: t.bg }}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-[3px] text-[11.5px] font-semibold",
        className,
      )}
    >
      {dot && (
        <span
          className="h-1.5 w-1.5 rounded-full"
          style={{ backgroundColor: t.dot }}
        />
      )}
      {children}
    </span>
  );
}
