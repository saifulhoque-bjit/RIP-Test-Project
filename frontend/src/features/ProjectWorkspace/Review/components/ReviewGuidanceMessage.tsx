import type { FeatureGenerationMetadata } from "@/types";

interface ReviewGuidanceMessageProps {
    /** `final_status` from `generation_metadata`. Falls back to legacy `status` string. */
    status?: string | null;
    /** Full generation metadata — used to enrich the banner with runtime detail. */
    generationMetadata?: FeatureGenerationMetadata | null;
}

type GuidanceTone = "ok" | "low" | "sub" | "crit";

interface GuidanceInfo {
    title: string;
    message: string;
    tone: GuidanceTone;
}

/** Base static map — runtime details are appended from generationMetadata. */
const GUIDE_STATUS: Record<string, GuidanceInfo> = {
    PASS: {
        title: "Ready · critic PASS",
        message: "Critic approved and grounded. Ready to approve and lock.",
        tone: "ok",
    },
    PASS_DETERMINISTIC_FALLBACK: {
        title: "AI analysis unavailable — deterministic scaffold",
        message:
            "A deterministic fallback was used because AI analysis was unavailable. Recommend a spot-check before approving.",
        tone: "low",
    },
    PASS_RELAXED_GATES: {
        title: "PASS · relaxed gates",
        message:
            "Framework or menu unit detected — business-value gates were waived. A light spot-check is recommended and it is generally safe to accept.",
        tone: "low",
    },
    FAIL_CORRECTION_EXHAUSTED: {
        title: "Correction exhausted",
        message:
            "Gates failed and auto-correction was exhausted after max attempts. Provide guidance and regenerate, then re-validate.",
        tone: "sub",
    },
    FAIL_HALLUCINATION: {
        title: "Hallucination — not grounded in the SRS",
        message:
            "Content is fabricated or contradicts the SRS. Approve is hard-blocked. Regenerate (or fix the upstream SRS) before proceeding. An override requires written justification and reviewer identity.",
        tone: "crit",
    },
    FAIL_SCHEMA_INVALID: {
        title: "Schema invalid — quarantined",
        message:
            "Failed structural validation and has been quarantined. Approve is hard-blocked with no override. Regenerate to proceed.",
        tone: "crit",
    },
    FAIL_PARSE_ERROR: {
        title: "Transient generation error",
        message:
            "A transient error occurred during generation. Re-run the MFU — no manual editing is needed.",
        tone: "sub",
    },
    FAIL_MAX_RETRIES: {
        title: "No usable result — max retries reached",
        message:
            "The generator reached the maximum retry limit with no usable output. Regenerate or reject this slot.",
        tone: "crit",
    },
    FAIL_NO_PARSEABLE_OUTPUT: {
        title: "No usable result — no parseable output",
        message:
            "The AI produced no output that could be parsed. Regenerate or reject this slot.",
        tone: "crit",
    },
    FAIL_CORRECTOR_NO_PROGRESS: {
        title: "No usable result — corrector stalled",
        message:
            "Auto-correction failed to make forward progress. Regenerate or reject this slot.",
        tone: "crit",
    },
    // Legacy entry kept for backward compatibility
    NOT_GENERATED: {
        title: "Not generated",
        message:
            "The AI could not produce a valid output after maximum attempts. Provide guidance and regenerate, or reject this slot.",
        tone: "crit",
    },
};

const normalizeStatus = (value?: string | null) =>
    (value ?? "")
        .trim()
        .replace(/[\s-]+/g, "_")
        .toUpperCase();

const toneClassMap: Record<GuidanceTone, string> = {
    ok: "border-[#a9e2cd] bg-[var(--success-50)]",
    low: "border-[#f0d69a] bg-[var(--warn-50)]",
    sub: "border-[#bcd8f7] bg-[var(--info-50)]",
    crit: "border-[#f3b4b4] bg-[var(--error-50)]",
};

export default function ReviewGuidanceMessage({
    status,
    generationMetadata,
}: ReviewGuidanceMessageProps) {
    const normalized = normalizeStatus(
        generationMetadata?.final_status ?? status,
    );

    // FAIL_CORRECTION_EXHAUSTED has two variants based on approve_as_is_allowed
    const isExhaustedTrivial =
        normalized === "FAIL_CORRECTION_EXHAUSTED" &&
        generationMetadata?.review_guidance?.approve_as_is_allowed === true;

    const guidance: GuidanceInfo | undefined = isExhaustedTrivial
        ? {
              title: "Correction exhausted · trivial (controls only)",
              message:
                  "Only the control list (Gate 7c) has noise — this is a trivial issue. One-click approve is allowed.",
              tone: "low",
          }
        : GUIDE_STATUS[normalized];

    if (!guidance) {
        return null;
    }

    const rg = generationMetadata?.review_guidance;

    return (
        <div
            className={`mb-4 rounded-[10px] border p-[14px_16px] ${toneClassMap[guidance.tone]}`}
        >
            <h4 className="m-0 mb-1 text-sm font-bold text-[var(--text-primary)]">
                {guidance.title}
            </h4>
            <p className="m-0 text-[13px] leading-[1.55] text-[var(--text-primary)]">
                {guidance.message}
            </p>

            {/* Relaxed gates detail */}
            {normalized === "PASS_RELAXED_GATES" &&
                generationMetadata?.relaxed_gates &&
                generationMetadata.relaxed_gates.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1">
                        {generationMetadata.relaxed_gates.map((g) => (
                            <span
                                key={g}
                                className="inline-flex rounded-[6px] bg-[var(--warn)] bg-opacity-10 px-[8px] py-[2px] text-[11px] font-semibold text-[var(--white)]"
                            >
                                {g}
                            </span>
                        ))}
                        {generationMetadata.gate_profile && (
                            <span className="ml-1 text-[11px] text-[var(--text-tertiary)]">
                                · profile: {generationMetadata.gate_profile}
                            </span>
                        )}
                    </div>
                )}

            {/* Deterministic fallback reason */}
            {normalized === "PASS_DETERMINISTIC_FALLBACK" &&
                generationMetadata?.fallback_reason && (
                    <p className="mt-2 m-0 text-[12px] leading-[1.5] text-[var(--text-secondary)]">
                        Reason: {generationMetadata.fallback_reason}
                    </p>
                )}

            {/* Hallucination failure reason */}
            {normalized === "FAIL_HALLUCINATION" &&
                generationMetadata?.failure_reason && (
                    <details className="mt-2">
                        <summary className="cursor-pointer text-[12px] font-semibold text-[var(--text-secondary)]">
                            Failure detail
                        </summary>
                        <pre className="mt-1 whitespace-pre-wrap break-words text-[11.5px] leading-[1.5] text-[var(--text-primary)]">
                            {generationMetadata.failure_reason}
                        </pre>
                    </details>
                )}

            {/* Failure summary for FAIL_CORRECTION_EXHAUSTED (substantive) */}
            {normalized === "FAIL_CORRECTION_EXHAUSTED" &&
                !isExhaustedTrivial &&
                rg?.failure_summary && (
                    <details className="mt-2">
                        <summary className="cursor-pointer text-[12px] font-semibold text-[var(--text-secondary)]">
                            Failure summary
                        </summary>
                        <pre className="mt-1 whitespace-pre-wrap break-words text-[11.5px] leading-[1.5] text-[var(--text-primary)]">
                            {rg.failure_summary}
                        </pre>
                    </details>
                )}

            {/* Gate reference list */}
            {rg?.gate_reference && rg.gate_reference.length > 0 && (
                <ul className="mt-2 flex flex-col gap-1 m-0 p-0 list-none">
                    {rg.gate_reference.map((gr) => (
                        <li
                            key={gr.gate}
                            className="text-[12px] text-[var(--text-secondary)]"
                        >
                            <strong>Gate {gr.gate} — {gr.name}:</strong>{" "}
                            {gr.meaning}
                        </li>
                    ))}
                </ul>
            )}

            {/* Flagged story IDs */}
            {rg?.flagged_story_ids && rg.flagged_story_ids.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                    <span className="text-[11px] font-semibold text-[var(--text-secondary)]">
                        Flagged:
                    </span>
                    {rg.flagged_story_ids.map((sid) => (
                        <span
                            key={sid}
                            className="inline-flex rounded-[6px] border border-current px-[7px] py-[1px] text-[10.5px] font-mono text-[var(--text-secondary)]"
                        >
                            {sid}
                        </span>
                    ))}
                </div>
            )}
        </div>
    );
}