import Button from "@/components/common/Button/Button";
import { SCOPE_NOUN, type FeedbackScope } from "./ReviewContext";

interface FeedbackBarProps {
  scope: FeedbackScope;
  count: number;
  onReview: () => void;
  onSubmit: () => void;
  isSubmitting?: boolean;
  /** Overrides the scope-derived noun in the summary text — used when one bar covers more than one scope (e.g. source_code's combined feature + story feedback). */
  label?: string;
}

export default function FeedbackBar({
  scope,
  count,
  onReview,
  onSubmit,
  isSubmitting = false,
  label,
}: FeedbackBarProps) {
  const noun = label ?? SCOPE_NOUN[scope];

  return (
    <div className="flex w-full shrink-0 items-center gap-3 border-t border-[var(--border-primary)] bg-white px-5 py-2.5 text-[13px]">
      <div className="text-[var(--text-secondary)]">
        <b className="text-[var(--accent)]">{count}</b> {noun} feedback item(s)
        pending — send to the AI in one regeneration.
      </div>
      <div className="ml-auto flex gap-2">
        <Button variant="ghost" size="sm" onClick={onReview}>
          Review item
        </Button>
        <Button size="sm" onClick={onSubmit} loading={isSubmitting}>
          Submit & regenerate
        </Button>
      </div>
    </div>
  );
}
