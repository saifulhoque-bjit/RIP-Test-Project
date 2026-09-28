import { ArrowNarrowRight } from "@/assets/icons/arrow/ArrowNarrowRight";
import Button from "@/components/common/Button/Button";
import { Panel } from "@/features/ProjectWorkspace/Overview/Panel";

interface IngestionPanelProps {
  projectType: "rfp" | "source_code";
  hasExistingSource: boolean;
  onUploadInitial: () => void;
  onIngestUpdate: () => void;
  enableIncrementalUpload: boolean;
  /** True while the project has an in-progress WebSocket task — disables every button here. */
  isBusy?: boolean;
}

export default function IngestionPanel({
  projectType,
  hasExistingSource,
  onUploadInitial,
  onIngestUpdate,
  enableIncrementalUpload = false,
  isBusy = false,
}: IngestionPanelProps) {
  if (projectType === "rfp") {
    return (
      <Panel title="Ingestion">
        <div className="flex items-start justify-between gap-3">
          <p className="text-[12.5px] leading-relaxed text-[var(--sec)]">
            Upload the initial <b>RFP (PDF)</b> to start the two-stage pipeline
            (modules &amp; features → your approval → user stories). Once an RFP
            exists, add further <b>updates</b> as PDFs — each becomes a
            reviewable change-set.
          </p>
          <div className="flex shrink-0 flex-col gap-2">
            <Button
              size="sm"
              disabled={hasExistingSource || isBusy}
              iconLeading={
                <ArrowNarrowRight className="w-3.5 h-3.5 -rotate-90" />
              }
              onClick={onUploadInitial}
            >
              Upload RFP (PDF)
            </Button>
            <Button
              variant="ghost"
              size="sm"
              disabled={!enableIncrementalUpload || isBusy}
              iconLeading={
                <ArrowNarrowRight className="w-3.5 h-3.5 -rotate-90" />
              }
              onClick={onIngestUpdate}
            >
              Ingest update (PDF)
            </Button>
          </div>
        </div>
        <div className="mt-2 text-[11px] text-[var(--mut)]">
          {isBusy
            ? "A pipeline task is currently running — ingestion actions are disabled until it finishes."
            : hasExistingSource
              ? "RFP already ingested — initial upload is disabled (one RFP per project in MVP)."
              : 'No RFP yet — "Ingest update" stays disabled until the initial RFP is uploaded.'}
        </div>
      </Panel>
    );
  }

  return (
    <Panel title="Ingest source">
      <div className="flex items-start justify-between gap-3">
        <p className="text-[12.5px] leading-relaxed text-[var(--sec)]">
          Ingest a single <b>source folder</b> (whole tree) or a ZIP archive. In
          MVP a project has <b>one source</b>; ingesting starts the governed
          pipeline automatically.
        </p>
        <div className="flex shrink-0 flex-col gap-2">
          <Button
            size="sm"
            disabled={hasExistingSource || isBusy}
            onClick={onUploadInitial}
          >
            ↑ Ingest source code
          </Button>
        </div>
      </div>
      {isBusy ? (
        <div className="mt-2 text-[11px] text-[var(--mut)]">
          A pipeline task is currently running — ingestion actions are disabled
          until it finishes.
        </div>
      ) : (
        hasExistingSource && (
          <div className="mt-2 text-[11px] text-[var(--mut)]">
            A source is already ingested — re-ingest is disabled (one source per
            project in MVP).
          </div>
        )
      )}
    </Panel>
  );
}
