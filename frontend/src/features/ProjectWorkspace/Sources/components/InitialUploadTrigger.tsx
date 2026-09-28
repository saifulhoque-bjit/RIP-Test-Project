import Button from "@/components/common/Button/Button";

export default function InitialUploadTrigger({
  onCardClick,
}: {
  onCardClick: (type: "rfp" | "source_code") => void;
}) {
  return (
    <section className="pb-6">
      <div className="pt-[22px]">
        <p className="m-0 mb-2 text-[11px] font-bold tracking-[0.5px] uppercase text-[var(--mut)]">
          Add a new source
        </p>
      </div>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2 py-[28px]">
        <div className="rounded-2xl border-2 border-dashed border-[var(--border-strong)] bg-white p-7 text-center shadow-[var(--e1)]">
          <div className="mx-auto mb-4 grid h-[52px] w-[52px] place-items-center rounded-[13px] bg-[var(--info-50)] text-[22px] text-[var(--info)]">
            📄
          </div>
          <h3 className="mb-2 text-base font-semibold text-[var(--text-primary)]">
            Documents Ingest (RFP)
          </h3>
          <p className="mx-auto mb-3 max-w-[26rem] text-[13px] leading-6 text-[var(--text-secondary)]">
            Business proposals or statements of work. RFP ingestion runs in{" "}
            <b>2 review stages</b>: modules &amp; features first, then user
            stories after you approve the architecture.
          </p>
          <div className="mb-4 inline-block rounded-md bg-[#f2f4f8] px-2.5 py-1 text-[11px] text-[var(--text-tertiary)]">
            Text-searchable PDF · 50MB
          </div>
          <div>
            <Button size="sm" onClick={() => onCardClick("rfp")}>
              Upload RFP Document
            </Button>
          </div>
        </div>

        <div className="rounded-2xl border-2 border-dashed border-[var(--border-strong)] bg-white p-7 text-center shadow-[var(--e1)]">
          <div
            className="mx-auto mb-4 grid h-[52px] w-[52px] place-items-center rounded-[13px] bg-[var(--ai-50)] text-[22px]"
            style={{ color: "var(--ai)", fontVariantEmoji: "text" }}
          >
            🗜
          </div>
          <h3 className="mb-2 text-base font-semibold text-[var(--text-primary)]">
            Codebase Ingest (Sourcecode)
          </h3>
          <p className="mx-auto mb-3 max-w-[26rem] text-[13px] leading-6 text-[var(--text-secondary)]">
            Legacy repositories for AST reverse engineering.
          </p>
          <div className="mb-4 inline-block rounded-md bg-[#f2f4f8] px-2.5 py-1 text-[11px] text-[var(--text-tertiary)]">
            ZIP (PB, VB6, COBOL) · 200MB
          </div>
          <div>
            <Button
              variant="ai"
              size="sm"
              onClick={() => onCardClick("source_code")}
            >
              Configure Codebase Ingest
            </Button>
          </div>
        </div>
      </div>
    </section>
  );
}
