import { useState } from "react";
import Modal from "@/components/common/Modal";
import Button from "@/components/common/Button/Button";
import { parseRunError } from "../utils/parseRunError";
import type { IngestionItem } from "@/types";

/**
 * Pipelines row action for a failed run: opens the failure message(s) from
 * `run.errors` in a modal.
 *
 * Each entry is a raw provider string wrapping a serialized payload, so only
 * the code and `message` inside it are shown — see parseRunError. An entry
 * that can't be parsed falls back to its raw text (rendered monospaced, since
 * it's then machine output rather than prose) instead of being hidden.
 *
 * Rendered only when `hasFailureReason(run)` says there's something to show —
 * see the predicate in ../utils/runStageProgress.
 */
export function FailureReasonButton({ run }: { run: IngestionItem }) {
  const [isOpen, setIsOpen] = useState(false);
  const errors = run.errors ?? [];
  const hasMultiple = errors.length > 1;

  return (
    <>
      <Button
        variant="ghost"
        size="xs"
        className="!text-[var(--error)] hover:!bg-[var(--error)]/10"
        onClick={(event) => {
          event.stopPropagation();
          setIsOpen(true);
        }}
      >
        Error details
      </Button>

      <Modal
        isOpen={isOpen}
        onClose={() => setIsOpen(false)}
        title="Error details"
        subtitle={`${run.run_code} could not be processed.`}
        width={620}
        footer={
          <Button variant="ghost" size="sm" onClick={() => setIsOpen(false)}>
            Close
          </Button>
        }
      >
        <div className="flex flex-col gap-3">
          {errors.map((raw, index) => {
            const parsed = parseRunError(raw);
            return (
              <div
                key={index}
                className="rounded-lg border border-[var(--error)]/25 bg-[var(--error-50)] p-3"
              >
                {(hasMultiple || parsed?.code) && (
                  <div className="mb-1.5 flex items-center gap-2">
                    {hasMultiple && (
                      <span className="text-[11px] font-semibold uppercase tracking-wide text-[var(--error)]">
                        Error {index + 1} of {errors.length}
                      </span>
                    )}
                    {parsed?.code && (
                      <span className="ml-auto rounded border border-[var(--error)]/30 px-1.5 py-0.5 font-mono text-[11px] font-semibold text-[var(--error)]">
                        Error Code: {parsed.code}
                      </span>
                    )}
                  </div>
                )}
                <p
                  className={
                    parsed
                      ? "m-0 whitespace-pre-wrap break-words text-[13px] leading-[1.6] text-[var(--text-primary)]"
                      : "m-0 whitespace-pre-wrap break-words font-mono text-[12px] leading-[1.6] text-[var(--text-primary)]"
                  }
                >
                  {parsed?.message ?? raw}
                </p>
              </div>
            );
          })}
        </div>
      </Modal>
    </>
  );
}
