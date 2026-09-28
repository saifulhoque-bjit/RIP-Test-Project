import { useState } from "react";
import { toast } from "@/lib/toast";

import Modal from "@/components/common/Modal";
import Button from "@/components/common/Button/Button";
import { Dropdown } from "@/components/common/Dropdown";
import { WarnNote } from "@/components/common/Note";
import { PillTabs } from "@/components/common/PillTabs";
import { Toggle } from "@/components/common/Toggle";
import { useExportProjectMutation } from "@/services/api/modules/export";
import { useGetRequirementsSummaryQuery } from "@/services/api/modules/user-stories";
import { getErrorMessage } from "@/utils/getErrorMessage";
import type { Project } from "@/types/project";

type StatusFilter = "approved" | "all";
type BacklogFormat = "JSON" | "PDF";

// MVP: JSON and PDF only.
const FORMATS: readonly BacklogFormat[] = ["JSON", "PDF"];
// The pipeline-generated documents (SRS specs, domain knowledge, architecture
// document) are always the already-generated Markdown files — no format choice
// to make.
const MARKDOWN_FORMATS = ["Markdown"] as const;

export function ExportModal({
  isOpen,
  onClose,
  project,
}: {
  isOpen: boolean;
  onClose: () => void;
  project: Project;
}) {
  // SRS group specs, domain knowledge and the architecture document only ever
  // exist for the source-code pipeline — every other project type has nothing
  // to export for these rows.
  const sourceCodeArtifactsAvailable = project.project_type === "source_code";

  const [status, setStatus] = useState<StatusFilter>("approved");
  const [content, setContent] = useState({
    backlog: true,
    srs: sourceCodeArtifactsAvailable,
    domainKnowledge: sourceCodeArtifactsAvailable,
    architectureDocument: sourceCodeArtifactsAvailable,
  });
  const [backlogFormat, setBacklogFormat] = useState<BacklogFormat>("JSON");
  const [exportProject, { isLoading: isExporting }] =
    useExportProjectMutation();

  // Sourced from the Requirement-tagged summary query (kept fresh by every
  // approve/status-change mutation and by the story-generation WS event) —
  // not `project.user_stories`/`approved_user_stories`, which are a snapshot
  // cached under the Project tag that approvals never invalidate.
  const { data: summaryData } = useGetRequirementsSummaryQuery(project.id, {
    skip: !isOpen,
  });
  const totalCount =
    summaryData?.data.total_user_stories ?? project.user_stories ?? 0;
  const approvedCount =
    summaryData?.data.approved_count ??
    project.approved_user_stories ??
    totalCount;
  const shownCount = status === "approved" ? approvedCount : totalCount;

  const hasSelection =
    content.backlog ||
    (sourceCodeArtifactsAvailable &&
      (content.srs || content.domainKnowledge || content.architectureDocument));

  const handleExport = async () => {
    try {
      const blob = await exportProject({
        projectId: project.id,
        body: {
          status_filter: status,
          include_backlog: content.backlog,
          backlog_format: backlogFormat.toLowerCase() as "json" | "pdf",
          include_srs: sourceCodeArtifactsAvailable && content.srs,
          include_domain_knowledge:
            sourceCodeArtifactsAvailable && content.domainKnowledge,
          include_architecture_document:
            sourceCodeArtifactsAvailable && content.architectureDocument,
        },
      }).unwrap();

      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${project.name}_export.zip`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);

      onClose();
      toast.success("Export downloaded.");
    } catch (error) {
      toast.error(
        getErrorMessage(error, "Failed to export. Please try again."),
      );
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Export requirements & SRS"
      width={640}
      footer={
        <div className="flex gap-2.5">
          <Button
            variant="ghost"
            size="sm"
            onClick={onClose}
            disabled={isExporting}
            className="mr-auto"
          >
            Cancel
          </Button>
          <Button
            size="sm"
            onClick={() => void handleExport()}
            disabled={!hasSelection}
            loading={isExporting}
          >
            {isExporting ? "Exporting…" : "Export"}
          </Button>
        </div>
      }
    >
      <label className="mb-1.5 block text-[13px] font-semibold">Scope</label>
      <div className="mb-4 flex items-center gap-2 rounded-md border border-border bg-[#fafbfd] px-3.5 py-2.5 text-[13px]">
        <span className="font-semibold">Whole project</span>
        <span className="text-mut">— {project.name}</span>
      </div>

      <label className="mb-1.5 block text-[13px] font-semibold">
        Status filter
      </label>
      <PillTabs<StatusFilter>
        full
        items={[
          { value: "approved", label: "Approved only" },
          { value: "all", label: "All" },
        ]}
        value={status}
        onChange={setStatus}
      />
      {status === "all" && (
        <WarnNote className="mt-2.5">
          Includes unapproved and failed requirements. Not recommended for
          downstream or client delivery.
        </WarnNote>
      )}

      <div className="mt-4 mb-1.5 text-[11px] font-bold uppercase tracking-wide text-mut">
        Content &amp; format
      </div>
      <ContentRow
        label="Requirements backlog"
        on={content.backlog}
        onToggle={() => setContent((c) => ({ ...c, backlog: !c.backlog }))}
        formats={FORMATS}
        value={backlogFormat}
        onChange={(value) => setBacklogFormat(value as BacklogFormat)}
      />
      {sourceCodeArtifactsAvailable && (
        <>
          <ContentRow
            label="SRS specifications"
            on={content.srs}
            onToggle={() => setContent((c) => ({ ...c, srs: !c.srs }))}
            formats={MARKDOWN_FORMATS}
            value="Markdown"
            onChange={() => {}}
          />
          <ContentRow
            label="Domain Knowledge"
            on={content.domainKnowledge}
            onToggle={() =>
              setContent((c) => ({ ...c, domainKnowledge: !c.domainKnowledge }))
            }
            formats={MARKDOWN_FORMATS}
            value="Markdown"
            onChange={() => {}}
          />
          <ContentRow
            label="Architecture Document"
            on={content.architectureDocument}
            onToggle={() =>
              setContent((c) => ({
                ...c,
                architectureDocument: !c.architectureDocument,
              }))
            }
            formats={MARKDOWN_FORMATS}
            value="Markdown"
            onChange={() => {}}
          />
        </>
      )}

      <div className="mt-3.5 mb-3.5 flex gap-6 rounded-md border border-border bg-[#fafbfd] px-4 py-3 text-xs text-sec">
        {shownCount} requirement{shownCount === 1 ? "" : "s"}
      </div>
    </Modal>
  );
}

function ContentRow({
  label,
  on,
  onToggle,
  formats,
  value,
  onChange,
  disabled = false,
  disabledHint,
}: {
  label: string;
  on: boolean;
  onToggle: () => void;
  formats: readonly string[];
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  disabledHint?: string;
}) {
  return (
    <div
      className={`mt-2.5 flex items-center justify-between gap-3 rounded-md border border-border px-3.5 py-3 ${
        disabled ? "opacity-50" : ""
      }`}
    >
      <div className="flex items-center gap-2.5">
        <Toggle
          on={on}
          onChange={disabled ? undefined : onToggle}
          disabled={disabled}
        />
        <div className="text-[13px] font-semibold">{label}</div>
        {disabled && disabledHint && (
          <span className="text-[11px] font-normal text-mut">
            — {disabledHint}
          </span>
        )}
      </div>
      <Dropdown
        className="w-[350px]"
        size="sm"
        disabled={disabled || !on}
        selected={value}
        options={formats.map((f) => ({ label: f, value: f }))}
        onChange={(option) => onChange(option.value)}
      />
    </div>
  );
}

export default ExportModal;
