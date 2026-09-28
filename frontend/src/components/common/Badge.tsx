import React from "react";

import { cn } from "@/lib/utils";

type BadgeKind = "chip" | "pill";

type BadgeStatus =
  | "default"
  | "parsing"
  | "analyzing"
  | "hitl-required"
  | "complete"
  | "failed"
  | "ai-draft"
  | "verified"
  | "confidence";

interface Tone {
  fg: string;
  bg: string;
  border?: string;
  dot?: string;
}

const STATUS_LABELS: Record<BadgeStatus, string> = {
  default: "Default",
  parsing: "Parsing",
  analyzing: "Analyzing",
  "hitl-required": "HITL Required",
  complete: "Complete",
  failed: "Failed",
  "ai-draft": "AI-Draft",
  verified: "Verified",
  confidence: "Confidence",
};

const STATUS_TONES: Record<BadgeStatus, Tone> = {
  default: {
    fg: "var(--sec, var(--text-secondary))",
    bg: "#fff",
    border: "#E4E8EF",
    dot: "#0E9F6E",
  },
  parsing: {
    fg: "var(--info)",
    bg: "color-mix(in srgb, var(--info) 12%, white)",
    dot: "var(--info)",
  },
  analyzing: {
    fg: "var(--warning)",
    bg: "color-mix(in srgb, var(--warning) 14%, white)",
    dot: "var(--warning)",
  },
  "hitl-required": {
    fg: "var(--ai-draft)",
    bg: "color-mix(in srgb, var(--ai-draft) 12%, white)",
    dot: "var(--ai-draft)",
  },
  complete: {
    fg: "var(--success)",
    bg: "color-mix(in srgb, var(--success) 12%, white)",
    dot: "var(--success)",
  },
  failed: {
    fg: "var(--error)",
    bg: "color-mix(in srgb, var(--error) 12%, white)",
    dot: "var(--error)",
  },
  "ai-draft": {
    fg: "var(--ai-draft)",
    bg: "color-mix(in srgb, var(--ai-draft) 12%, white)",
  },
  verified: {
    fg: "var(--success)",
    bg: "color-mix(in srgb, var(--success) 12%, white)",
  },
  confidence: {
    fg: "var(--info)",
    bg: "color-mix(in srgb, var(--info) 12%, white)",
  },
};

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  kind?: BadgeKind;
  status: BadgeStatus;
  label?: string;
  value?: string | number;
  showDot?: boolean;
}

export default function Badge({
  kind = "chip",
  status,
  label,
  value,
  showDot,
  className,
  style,
  ...props
}: BadgeProps) {
  const tone = STATUS_TONES[status];
  const shouldShowDot = showDot ?? kind === "chip";

  const computedLabel =
    label ??
    (status === "confidence" && value !== undefined
      ? `${value}%`
      : STATUS_LABELS[status]);

  return (
    <span
      className={cn(
        "h-[27px] inline-flex items-center",
        kind === "chip"
          ? "gap-1.5 rounded-[14px] px-[10px] py-[3px] text-[11.5px] font-semibold"
          : "gap-1.5 rounded-[14px] px-[9px] py-[3px] text-[11px] font-semibold",
        className,
      )}
      style={{
        color: tone.fg,
        background: tone.bg,
        ...(tone.border ? { border: `1px solid ${tone.border}` } : {}),
        ...style,
      }}
      {...props}
    >
      {shouldShowDot && tone.dot ? (
        <span
          aria-hidden="true"
          className="h-[6px] w-[6px] rounded-full"
          style={{ background: tone.dot }}
        />
      ) : null}
      {computedLabel}
    </span>
  );
}
