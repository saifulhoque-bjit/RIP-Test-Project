import { useEffect, useMemo, useState } from "react";
import type { SourceEvidenceItem } from "@/types/user-story";
import EmptyState from "@/components/common/EmptyState/EmptyState";
import { resolveSourceFileUrl } from "@/utils/resolveSourceFileUrl";
import MarkdownViewer from "@/features/ProjectWorkspace/Review/components/right-panel/MarkdownViewer";
import type { ReactNode } from "react";

type HighlightMode = "none" | "table_row" | "section" | "bullet_item" | "file";

interface ResolvedPreview {
  key: string;
  mode: HighlightMode;
  sectionHeading: string;
  highlightedText: string;
  message: string | null;
  status: "resolved" | "not-found" | "error";
}

const headingPattern = /^(#{1,6})\s+(.+)$/;
const bulletPattern = /^\s*([*+-]|\d+\.)\s+/;
const cardClass =
  "rounded-[8px] border border-[color:var(--border)] p-3 flex flex-col gap-1.5";
const contentTextClass = "text-[13.5px] leading-relaxed text-[#1c2533]";

function EvidenceCard({
  children,
  acCode,
}: {
  children: ReactNode;
  acCode?: string | null;
}) {
  return (
    <div className={cardClass}>
      <span>{children}</span>
      {acCode && (
        <span className="w-max rounded px-1.5 py-0.5 text-[10px] font-bold bg-[#eef1f5] text-sec">
          {acCode}
        </span>
      )}
    </div>
  );
}

function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <span className="text-[10.5px] text-[color:var(--mut)] uppercase">
      {children}
    </span>
  );
}

function HighlightedSnippet({
  snippet,
  target,
}: {
  snippet: string;
  target: string;
}) {
  const normalizedTarget = target.trim().toLowerCase();
  if (!normalizedTarget) return <>{snippet}</>;

  const index = snippet.toLowerCase().indexOf(normalizedTarget);
  if (index === -1) return <>{snippet}</>;

  const matchLength = target.trim().length;

  return (
    <>
      {snippet.slice(0, index)}
      <span className="font-medium not-italic">
        {snippet.slice(index, index + matchLength)}
      </span>
      {snippet.slice(index + matchLength)}
    </>
  );
}

function getSectionHeading(evidence: SourceEvidenceItem): string {
  if (evidence.sectionAnchor?.trim()) return evidence.sectionAnchor.trim();

  if (
    evidence.highlightType === "section" &&
    evidence.targetString &&
    evidence.targetString.trim()
  ) {
    return evidence.targetString.trim();
  }

  return "";
}

function containsInsensitive(value: string, target: string): boolean {
  const normalizedTarget = normalizeText(target);
  if (!normalizedTarget) return false;
  return normalizeText(value).includes(normalizedTarget);
}

function normalizeText(value: string): string {
  return value.replace(/\s+/g, " ").trim().toLowerCase();
}

function normalizeHighlightType(
  value: string | null | undefined,
): HighlightMode | "" {
  if (!value) return "";

  const normalized = value.trim().toLowerCase().replace(/-/g, "_");

  if (
    normalized === "table_row" ||
    normalized === "section" ||
    normalized === "bullet_item" ||
    normalized === "file"
  ) {
    return normalized;
  }

  return "";
}

function stripMarkdownInlineSyntax(value: string): string {
  return value
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/_([^_]+)_/g, "$1")
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, "$1")
    .replace(/\[([^\]]+)\]\[([^\]]*)\]/g, "$1")
    .trim();
}

function getHeadingMatch(line: string): { level: number; text: string } | null {
  const match = line.match(headingPattern);
  if (!match) return null;

  return {
    level: match[1].length,
    text: stripMarkdownInlineSyntax(match[2].trim()),
  };
}

function findHeadingByText(
  lines: string[],
  headingText: string,
): { index: number; level: number; text: string } | null {
  if (!headingText.trim()) return null;
  const normalizedTarget = normalizeText(
    stripMarkdownInlineSyntax(headingText),
  );

  for (let index = 0; index < lines.length; index += 1) {
    const heading = getHeadingMatch(lines[index]);
    if (!heading) continue;

    const normalizedHeading = normalizeText(heading.text);
    if (
      normalizedHeading === normalizedTarget ||
      normalizedHeading.includes(normalizedTarget) ||
      normalizedTarget.includes(normalizedHeading)
    ) {
      return {
        index,
        level: heading.level,
        text: heading.text,
      };
    }
  }

  return null;
}

function getSectionBounds(
  lines: string[],
  startIndex: number,
  level: number,
): { start: number; end: number } {
  let end = lines.length;

  for (let index = startIndex + 1; index < lines.length; index += 1) {
    const heading = getHeadingMatch(lines[index]);
    if (heading && heading.level <= level) {
      end = index;
      break;
    }
  }

  return {
    start: startIndex,
    end,
  };
}

function parseHeading(line: string): string {
  const match = line.match(headingPattern);
  if (!match) return "";
  return stripMarkdownInlineSyntax(match[2].trim());
}

function findNearestHeading(lines: string[], fromIndex: number): string {
  for (let i = fromIndex; i >= 0; i -= 1) {
    const heading = parseHeading(lines[i]);
    if (heading) return heading;
  }
  return "";
}

function findTargetRowIndex(
  lines: string[],
  lineNumber: number,
  sectionBounds: { start: number; end: number } | null,
  targetString: string,
): number {
  if (lineNumber > 0) {
    const deterministicIndex = lineNumber - 1;
    if (deterministicIndex >= 0 && deterministicIndex < lines.length) {
      const deterministicLine = lines[deterministicIndex];
      if (isLikelyMarkdownTableRow(deterministicLine)) {
        return deterministicIndex;
      }
    }
  }

  if (targetString) {
    if (sectionBounds) {
      const sectionTableRowMatch = lines.findIndex(
        (line, index) =>
          index > sectionBounds.start &&
          index < sectionBounds.end &&
          isLikelyMarkdownTableRow(line) &&
          containsInsensitive(line, targetString),
      );
      if (sectionTableRowMatch >= 0) return sectionTableRowMatch;
    }

    const tableRowMatch = lines.findIndex(
      (line) =>
        isLikelyMarkdownTableRow(line) &&
        containsInsensitive(line, targetString),
    );
    if (tableRowMatch >= 0) return tableRowMatch;

    const textMatch = lines.findIndex((line) =>
      containsInsensitive(line, targetString),
    );
    if (textMatch >= 0) return textMatch;
  }

  return -1;
}

function findLineByTargetString(
  lines: string[],
  targetString: string,
  start = 0,
  end = lines.length,
): number {
  if (!targetString.trim()) return -1;

  for (let index = start; index < end; index += 1) {
    if (containsInsensitive(lines[index], targetString)) {
      return index;
    }
  }

  return -1;
}

function isLikelyMarkdownTableRow(line: string): boolean {
  return (line.match(/\|/g) ?? []).length >= 2;
}

function resolvePreviewFromMarkdown(
  markdownText: string,
  evidence: SourceEvidenceItem,
): Omit<ResolvedPreview, "key" | "status"> & {
  status: "resolved" | "not-found";
} {
  const lines = markdownText.split("\n");
  const highlightType = normalizeHighlightType(evidence.highlightType);
  const precision = evidence.precision;
  const targetString = evidence.targetString?.trim() || "";
  const sectionAnchor = evidence.sectionAnchor?.trim() || "";
  const lineNumber = evidence.lineNumber ?? 0;

  const baseResult = {
    mode: "none" as HighlightMode,
    sectionHeading: getSectionHeading(evidence),
    highlightedText: "",
    message: null as string | null,
  };

  if (precision === "file" || highlightType === "file") {
    return {
      ...baseResult,
      mode: "file",
      message: "Low Precision - exact location could not be determined.",
      status: "resolved",
    };
  }

  const hasRowEvidence = lineNumber > 0 || !!targetString;
  const effectiveHighlightType: HighlightMode | "" =
    highlightType || (hasRowEvidence ? "table_row" : "");

  if (effectiveHighlightType === "section") {
    const sectionByTarget = findHeadingByText(lines, targetString);
    if (sectionByTarget) {
      return {
        ...baseResult,
        mode: "section",
        sectionHeading: sectionByTarget.text,
        highlightedText: sectionByTarget.text,
        status: "resolved",
      };
    }

    const sectionByAnchor = findHeadingByText(lines, sectionAnchor);
    if (sectionByAnchor) {
      return {
        ...baseResult,
        mode: "section",
        sectionHeading: sectionByAnchor.text,
        highlightedText: sectionByAnchor.text,
        message: "Approximate match",
        status: "resolved",
      };
    }

    return {
      ...baseResult,
      mode: "section",
      message: "Section heading not found in source markdown.",
      status: "not-found",
    };
  }

  if (effectiveHighlightType === "table_row") {
    const sectionByAnchor = findHeadingByText(lines, sectionAnchor);
    const sectionBounds = sectionByAnchor
      ? getSectionBounds(lines, sectionByAnchor.index, sectionByAnchor.level)
      : null;

    const rowIndex = findTargetRowIndex(
      lines,
      lineNumber,
      sectionBounds,
      targetString,
    );

    if (rowIndex >= 0) {
      const row = lines[rowIndex].trim();
      const nearestHeading = findNearestHeading(lines, rowIndex);
      return {
        ...baseResult,
        mode: "table_row",
        sectionHeading:
          sectionByAnchor?.text || sectionAnchor || nearestHeading,
        highlightedText: row,
        status: "resolved",
      };
    }

    if (sectionByAnchor) {
      return {
        ...baseResult,
        mode: "section",
        sectionHeading: sectionByAnchor.text,
        highlightedText: sectionByAnchor.text,
        message: "Exact line not found - showing section context.",
        status: "resolved",
      };
    }

    return {
      ...baseResult,
      mode: "table_row",
      message: "Exact line not found - showing section context.",
      status: "not-found",
    };
  }

  if (effectiveHighlightType === "bullet_item") {
    const sectionByAnchor = findHeadingByText(lines, sectionAnchor);
    if (!sectionByAnchor) {
      return {
        ...baseResult,
        mode: "bullet_item",
        message: "Section heading not found in source markdown.",
        status: "not-found",
      };
    }

    const bounds = getSectionBounds(
      lines,
      sectionByAnchor.index,
      sectionByAnchor.level,
    );

    let bulletLineIndex = -1;
    for (let index = bounds.start + 1; index < bounds.end; index += 1) {
      const line = lines[index];
      if (bulletPattern.test(line) && containsInsensitive(line, targetString)) {
        bulletLineIndex = index;
        break;
      }
    }

    if (bulletLineIndex < 0) {
      bulletLineIndex = findLineByTargetString(
        lines,
        targetString,
        bounds.start + 1,
        bounds.end,
      );
    }

    if (bulletLineIndex >= 0) {
      return {
        ...baseResult,
        mode: "bullet_item",
        sectionHeading: sectionByAnchor.text,
        highlightedText: lines[bulletLineIndex].trim(),
        status: "resolved",
      };
    }

    return {
      ...baseResult,
      mode: "section",
      sectionHeading: sectionByAnchor.text,
      highlightedText: sectionByAnchor.text,
      message: "Approximate match",
      status: "resolved",
    };
  }

  const fallbackLine =
    findLineByTargetString(lines, targetString) >= 0
      ? lines[findLineByTargetString(lines, targetString)].trim()
      : "";

  if (fallbackLine) {
    return {
      ...baseResult,
      mode: "none",
      highlightedText: fallbackLine,
      status: "resolved",
    };
  }

  return {
    ...baseResult,
    status: "not-found",
    message: "No highlightable content found for this reference.",
  };
}

// Picks the single body to render inside the (one and only) EvidenceCard —
// first matching case wins, so ordering mirrors resolvePreviewFromMarkdown's
// resolution priority.
function resolveEvidenceCardBody({
  evidence,
  sectionHeading,
  displayRow,
  resolvedMode,
  resolvedMessage,
  resolvedStatus,
}: {
  evidence: SourceEvidenceItem;
  sectionHeading: string;
  displayRow: string;
  resolvedMode: HighlightMode;
  resolvedMessage: string | null;
  resolvedStatus: "resolved" | "not-found" | "error";
}): ReactNode | null {
  if (evidence.isUnresolved && evidence.l2SourceRef) {
    return (
      <>
        <SectionLabel>{evidence.l2SourceRef}</SectionLabel>
        <div className="text-[11.5px] text-mut">
          Evidence not pre-resolved for this reference. Inspect the SRS file
          manually.
        </div>
      </>
    );
  }

  if (displayRow) {
    return (
      <>
        <SectionLabel>{sectionHeading}</SectionLabel>
        <p className={contentTextClass}>
          {evidence.targetString ? (
            <HighlightedSnippet
              snippet={displayRow}
              target={evidence.targetString}
            />
          ) : (
            displayRow
          )}
        </p>
      </>
    );
  }

  if (resolvedMode === "section" && sectionHeading) {
    return <SectionLabel>{sectionHeading}</SectionLabel>;
  }

  if (evidence.exactQuote) {
    return <p className={contentTextClass}>{evidence.exactQuote}</p>;
  }

  if (evidence.contextSnippet) {
    return evidence.targetString ? (
      <HighlightedSnippet
        snippet={evidence.contextSnippet}
        target={evidence.targetString}
      />
    ) : (
      evidence.contextSnippet
    );
  }

  if (evidence.targetString) {
    return <p className={contentTextClass}>{evidence.targetString}</p>;
  }

  if (sectionHeading) {
    return (
      <>
        <SectionLabel>{sectionHeading}</SectionLabel>
        <div className="text-xs text-[color:var(--text-tertiary)]">
          Section matched. Open the full SRS to view content.
        </div>
      </>
    );
  }

  if (resolvedMessage) return resolvedMessage;

  if (resolvedStatus !== "resolved") {
    return "No highlighting content found for this reference.";
  }

  return null;
}

interface MdEvidencePreviewProps {
  evidence: SourceEvidenceItem;
  activeAcceptanceCriteriaCode?: string | null;
}

export default function MdEvidencePreview({
  evidence,
  activeAcceptanceCriteriaCode = null,
}: MdEvidencePreviewProps) {
  const highlightType = evidence.highlightType;
  const isGapReference = !!evidence.isGapReference;
  const isUnresolved = !!evidence.isUnresolved;
  const lineNumber = evidence.lineNumber ?? 0;
  const targetString = evidence.targetString?.trim() ?? "";
  const sectionAnchor = evidence.sectionAnchor?.trim() ?? "";
  const sourceFilePath = evidence.sourceFilePath;
  const baseSectionHeading = getSectionHeading(evidence);
  const resolvedUrl = useMemo(
    () =>
      resolveSourceFileUrl({
        sourceFilePath,
      }),
    [sourceFilePath],
  );
  const rowResolveKey = useMemo(
    () =>
      [sourceFilePath ?? "", lineNumber, targetString, sectionAnchor].join(
        "::",
      ),
    [lineNumber, sectionAnchor, sourceFilePath, targetString],
  );
  const [resolvedPreview, setResolvedPreview] = useState<{
    key: string;
    mode: HighlightMode;
    highlightedText: string;
    sectionHeading: string;
    message: string | null;
    status: "resolved" | "not-found" | "error";
  }>({
    key: "",
    mode: "none",
    highlightedText: "",
    sectionHeading: "",
    message: null,
    status: "not-found",
  });

  useEffect(() => {
    if (isGapReference || isUnresolved) return;
    if (highlightType !== "table_row") return;
    if (!resolvedUrl) return;

    const controller = new AbortController();

    void fetch(resolvedUrl, {
      signal: controller.signal,
      headers: {
        Accept: "text/markdown,text/plain,*/*",
      },
    })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Unable to load markdown (${response.status}).`);
        }
        return response.text();
      })
      .then((text) => text.replace(/^\uFEFF/, ""))
      .then((markdownText) => {
        if (!markdownText.trim()) {
          setResolvedPreview({
            key: rowResolveKey,
            mode: "none",
            highlightedText: "",
            sectionHeading: baseSectionHeading,
            message: "Markdown file is empty.",
            status: "not-found",
          });
          return;
        }

        const resolved = resolvePreviewFromMarkdown(markdownText, evidence);

        setResolvedPreview({
          key: rowResolveKey,
          mode: resolved.mode,
          highlightedText: resolved.highlightedText,
          sectionHeading: resolved.sectionHeading,
          message: resolved.message,
          status: resolved.status,
        });
      })
      .catch((error: unknown) => {
        if ((error as Error).name === "AbortError") return;
        setResolvedPreview({
          key: rowResolveKey,
          mode: "none",
          highlightedText: "",
          sectionHeading: baseSectionHeading,
          message: "Unable to load markdown preview.",
          status: "error",
        });
      });

    return () => controller.abort();
  }, [
    highlightType,
    isGapReference,
    isUnresolved,
    lineNumber,
    resolvedUrl,
    rowResolveKey,
    targetString,
    baseSectionHeading,
    evidence,
  ]);

  const hasResolvedPreview = resolvedPreview.key === rowResolveKey;
  const sectionHeading =
    (hasResolvedPreview ? resolvedPreview.sectionHeading : "") ||
    baseSectionHeading;
  const resolvedMode = hasResolvedPreview ? resolvedPreview.mode : "none";
  const resolvedMessage = hasResolvedPreview ? resolvedPreview.message : null;
  const resolvedStatus = hasResolvedPreview
    ? resolvedPreview.status
    : "not-found";
  const displayRow =
    evidence.highlightType === "table_row" ||
    evidence.highlightType === "bullet_item"
      ? (hasResolvedPreview ? resolvedPreview.highlightedText : "") ||
        (evidence.highlightType === "table_row"
          ? evidence.exactQuote || ""
          : evidence.contextSnippet || "")
      : "";

  if (!evidence.l2SourceRef) {
    return (
      <div className="flex min-h-[50vh] h-full w-full px-5 flex-col items-center justify-center gap-2">
        <EmptyState
          title="No source evidence available"
          description="Evidence is not available. Please select an acceptance criteria to view the source evidence."
        />
      </div>
    );
  }

  // A GAP reference has no anchor to resolve a snippet from — show the
  // selected md file itself (picked via the file chips in SrcEvidence) so a
  // human can inspect it directly, instead of a bare "no anchor" message.
  if (isGapReference) {
    return (
      <div className="flex h-full min-h-0 w-full min-w-0 flex-col gap-2">
        <MarkdownViewer
          srcEvidence={evidence}
          activeAcceptanceCriteriaCode={activeAcceptanceCriteriaCode}
        />
      </div>
    );
  }

  const cardBody = resolveEvidenceCardBody({
    evidence,
    sectionHeading,
    displayRow,
    resolvedMode,
    resolvedMessage,
    resolvedStatus,
  });

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      {cardBody && (
        <EvidenceCard acCode={activeAcceptanceCriteriaCode}>
          {cardBody}
        </EvidenceCard>
      )}
    </div>
  );
}
