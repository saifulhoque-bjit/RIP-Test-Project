import {
  isValidElement,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import rehypeSanitize from "rehype-sanitize";
import type { SourceEvidenceItem } from "@/types";
import { resolveSourceFileUrl } from "@/utils/resolveSourceFileUrl";

type HighlightMode = "none" | "table_row" | "section" | "bullet_item" | "file";

interface HighlightPlan {
  mode: HighlightMode;
  rowNeedles: string[];
  matchedLineNumber: number | null;
  sectionStartIndex: number | null;
  sectionEndIndexExclusive: number | null;
  sectionHeading: string;
  bulletNeedle: string;
  message: string | null;
}

interface MarkdownViewerProps {
  srcEvidence: SourceEvidenceItem | null;
  fileUrl?: string;
  activeAcceptanceCriteriaCode?: string | null;
}

const headingPattern = /^(#{1,6})\s+(.+)$/;
const bulletPattern = /^\s*([*+-]|\d+\.)\s+/;

function normalizeText(value: string): string {
  return value.replace(/\s+/g, " ").trim().toLowerCase();
}

function normalizeLooseText(value: string): string {
  return normalizeText(
    value.normalize("NFKC").replace(/[^\p{L}\p{N}]+/gu, " "),
  );
}

function containsInsensitive(value: string, target: string): boolean {
  const normalizedTarget = normalizeText(target);
  if (!normalizedTarget) return false;
  return normalizeText(value).includes(normalizedTarget);
}

function isHeadingTextMatch(value: string, target: string): boolean {
  const normalizedValue = normalizeText(value);
  const normalizedTarget = normalizeText(target);
  if (!normalizedValue || !normalizedTarget) return false;

  return (
    normalizedValue === normalizedTarget ||
    normalizedValue.includes(normalizedTarget) ||
    normalizedTarget.includes(normalizedValue)
  );
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

function nodeToText(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === "boolean") {
    return "";
  }

  if (typeof node === "string" || typeof node === "number") {
    return String(node);
  }

  if (Array.isArray(node)) {
    return node.map((item) => nodeToText(item)).join(" ");
  }

  if (isValidElement(node)) {
    const children = (node.props as { children?: ReactNode }).children;
    return nodeToText(children);
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

function findNearestHeadingAbove(
  lines: string[],
  fromIndex: number,
): { index: number; level: number; text: string } | null {
  for (let index = fromIndex; index >= 0; index -= 1) {
    const heading = getHeadingMatch(lines[index]);
    if (!heading) continue;

    return {
      index,
      level: heading.level,
      text: heading.text,
    };
  }

  return null;
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

function normalizeRowCandidate(value: string): string {
  return normalizeText(value.replace(/\|/g, " "));
}

function isLikelyMarkdownTableRow(line: string): boolean {
  return (line.match(/\|/g) ?? []).length >= 2;
}

function stripLeadingRowIndex(value: string): string {
  return value.replace(/^\s*\d+\s*[.)-]?\s+/, "");
}

function stripLeadingLocale(value: string): string {
  return value.replace(/^\s*[a-z]{2,5}\s*[:-]\s+/i, "");
}

function getNeedleVariants(value: string): string[] {
  const normalized = normalizeText(value);
  const loose = normalizeLooseText(value);
  const variants = [normalized, loose].filter(Boolean);
  const result = new Set<string>();

  variants.forEach((variant) => {
    result.add(variant);

    const withoutIndex = stripLeadingRowIndex(variant);
    if (withoutIndex) result.add(withoutIndex);

    const withoutLocale = stripLeadingLocale(withoutIndex);
    if (withoutLocale) result.add(withoutLocale);

    const words = withoutLocale.split(" ").filter(Boolean);
    if (words.length >= 2) {
      result.add(words.slice(0, 2).join(" "));
    }
    if (words.length >= 3) {
      result.add(words.slice(0, 3).join(" "));
    }
  });

  return [...result].filter((item) => item.length >= 3);
}

function isTableHeaderRow(children: ReactNode): boolean {
  const childArray = Array.isArray(children) ? children : [children];
  const elementChildren = childArray.filter((item) => isValidElement(item));
  if (elementChildren.length === 0) return false;

  return elementChildren.every((item) => {
    if (typeof item.type !== "string") return false;
    return item.type.toLowerCase() === "th";
  });
}

function getLineNeedles(line: string, targetString: string): string[] {
  const rowSource = line.replace(/\|/g, " ");
  const needleSet = new Set<string>([
    ...getNeedleVariants(rowSource),
    ...getNeedleVariants(targetString),
  ]);

  return [...needleSet];
}

function getHeadingLevel(heading: HTMLHeadingElement): number {
  return Number(heading.tagName.replace("H", ""));
}

function findSectionHeadingElement(
  viewport: HTMLElement,
  sectionHeading: string,
): HTMLHeadingElement | null {
  if (!sectionHeading.trim()) return null;

  const normalizedTarget = normalizeText(
    stripMarkdownInlineSyntax(sectionHeading),
  );
  if (!normalizedTarget) return null;

  const headings = Array.from(
    viewport.querySelectorAll<HTMLHeadingElement>("h1,h2,h3,h4,h5,h6"),
  );

  for (const heading of headings) {
    const headingText = normalizeText(
      stripMarkdownInlineSyntax(heading.textContent ?? ""),
    );
    if (!headingText) continue;

    if (
      headingText === normalizedTarget ||
      headingText.includes(normalizedTarget) ||
      normalizedTarget.includes(headingText)
    ) {
      return heading;
    }
  }

  return null;
}

function findSectionEndHeading(
  viewport: HTMLElement,
  sectionStart: HTMLHeadingElement,
): HTMLHeadingElement | null {
  const startLevel = getHeadingLevel(sectionStart);
  const headings = Array.from(
    viewport.querySelectorAll<HTMLHeadingElement>("h1,h2,h3,h4,h5,h6"),
  );
  const startIndex = headings.indexOf(sectionStart);
  if (startIndex < 0) return null;

  for (let index = startIndex + 1; index < headings.length; index += 1) {
    const level = getHeadingLevel(headings[index]);
    if (level <= startLevel) {
      return headings[index];
    }
  }

  return null;
}

function isNodeAfter(reference: Node, node: Node): boolean {
  return Boolean(
    reference.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING,
  );
}

function isNodeBefore(node: Node, reference: Node): boolean {
  return Boolean(
    node.compareDocumentPosition(reference) & Node.DOCUMENT_POSITION_FOLLOWING,
  );
}

function isNodeInsideSectionRange(
  node: Node,
  sectionStart: HTMLHeadingElement,
  sectionEnd: HTMLHeadingElement | null,
): boolean {
  if (!isNodeAfter(sectionStart, node)) return false;
  if (!sectionEnd) return true;
  return isNodeBefore(node, sectionEnd);
}

function findRenderedTableRowByNeedles(
  viewport: HTMLElement,
  rowNeedles: string[],
  sectionHeading: string,
): HTMLTableRowElement | null {
  const normalizedNeedles = rowNeedles.map((needle) => normalizeText(needle));
  const looseNeedles = rowNeedles.map((needle) => normalizeLooseText(needle));
  const tableRows = Array.from(
    viewport.querySelectorAll<HTMLTableRowElement>("table tr"),
  );
  const sectionStart = findSectionHeadingElement(viewport, sectionHeading);
  const sectionEnd = sectionStart
    ? findSectionEndHeading(viewport, sectionStart)
    : null;

  for (const row of tableRows) {
    if (row.querySelectorAll("td").length === 0) continue;
    if (
      sectionStart &&
      !isNodeInsideSectionRange(row, sectionStart, sectionEnd)
    ) {
      continue;
    }

    const rowText = normalizeRowCandidate(row.textContent ?? "");
    const rowTextLoose = normalizeLooseText(row.textContent ?? "");

    const isMatch =
      normalizedNeedles.some(
        (needle) => !!needle && rowText.includes(needle),
      ) ||
      looseNeedles.some((needle) => !!needle && rowTextLoose.includes(needle));

    if (isMatch) {
      return row;
    }
  }

  return null;
}

function applyRenderedTableRowHighlight(row: HTMLTableRowElement): void {
  row.setAttribute("data-evidence-highlight", "true");
  row.setAttribute("data-evidence-highlight-fallback", "true");
  row.style.backgroundColor = "#FFF59D";

  row.querySelectorAll<HTMLElement>("td").forEach((cell) => {
    cell.style.backgroundColor = "#FFF59D";
    cell.style.transition = "background-color 160ms ease";
  });
}

function clearRenderedTableRowFallbackHighlight(viewport: HTMLElement): void {
  const previouslyHighlightedRows = Array.from(
    viewport.querySelectorAll<HTMLTableRowElement>(
      'tr[data-evidence-highlight-fallback="true"]',
    ),
  );

  previouslyHighlightedRows.forEach((row) => {
    row.removeAttribute("data-evidence-highlight-fallback");
    row.style.backgroundColor = "";

    row.querySelectorAll<HTMLElement>("td").forEach((cell) => {
      cell.style.backgroundColor = "";
      cell.style.transition = "";
    });
  });
}

export default function MarkdownViewer({
  srcEvidence,
  fileUrl,
  activeAcceptanceCriteriaCode,
}: MarkdownViewerProps) {
  const [content, setContent] = useState<string>("");
  const [resultUrl, setResultUrl] = useState<string>("");
  const [error, setError] = useState<string>("");
  const contentViewportRef = useRef<HTMLDivElement | null>(null);

  const resolvedUrl = useMemo(
    () =>
      resolveSourceFileUrl({
        fileUrl,
        sourceFilePath: srcEvidence?.sourceFilePath,
      }),
    [fileUrl, srcEvidence?.sourceFilePath],
  );

  const highlightPlan = useMemo<HighlightPlan>(() => {
    const basePlan: HighlightPlan = {
      mode: "none",
      rowNeedles: [],
      matchedLineNumber: null,
      sectionStartIndex: null,
      sectionEndIndexExclusive: null,
      sectionHeading: "",
      bulletNeedle: "",
      message: null,
    };

    if (!content.trim()) return basePlan;
    if (srcEvidence?.isGapReference || srcEvidence?.isUnresolved)
      return basePlan;

    const lines = content.split("\n");
    const highlightType = normalizeHighlightType(srcEvidence?.highlightType);
    const precision = srcEvidence?.precision;
    const targetString = srcEvidence?.targetString?.trim() || "";
    const sectionAnchor = srcEvidence?.sectionAnchor?.trim() || "";
    const lineNumber = srcEvidence?.lineNumber ?? 0;

    if (precision === "file" || highlightType === "file") {
      return {
        ...basePlan,
        mode: "file",
      };
    }

    const hasRowEvidence = lineNumber > 0 || !!targetString;
    const effectiveHighlightType: HighlightMode | "" =
      highlightType || (hasRowEvidence ? "table_row" : "");

    if (effectiveHighlightType === "section") {
      const sectionByTarget = findHeadingByText(lines, targetString);
      if (sectionByTarget) {
        return {
          ...basePlan,
          mode: "section",
          sectionHeading: sectionByTarget.text,
        };
      }

      const sectionByAnchor = findHeadingByText(lines, sectionAnchor);
      if (sectionByAnchor) {
        return {
          ...basePlan,
          mode: "section",
          sectionHeading: sectionByAnchor.text,
          message: "Approximate match",
        };
      }

      return basePlan;
    }

    if (effectiveHighlightType === "table_row") {
      let matchedLineIndex = -1;
      let resolvedSectionHeading = "";

      const sectionByAnchor = findHeadingByText(lines, sectionAnchor);
      const sectionBounds = sectionByAnchor
        ? getSectionBounds(lines, sectionByAnchor.index, sectionByAnchor.level)
        : null;

      if (lineNumber > 0) {
        const deterministicIndex = lineNumber - 1;
        if (deterministicIndex >= 0 && deterministicIndex < lines.length) {
          if (isLikelyMarkdownTableRow(lines[deterministicIndex])) {
            matchedLineIndex = deterministicIndex;
          }
        }
      }

      if (matchedLineIndex < 0 && sectionByAnchor && targetString) {
        matchedLineIndex = lines.findIndex(
          (line, index) =>
            !!sectionBounds &&
            index > sectionBounds.start &&
            index < sectionBounds.end &&
            isLikelyMarkdownTableRow(line) &&
            containsInsensitive(line, targetString),
        );
      }

      if (matchedLineIndex < 0 && targetString) {
        matchedLineIndex = lines.findIndex(
          (line) =>
            isLikelyMarkdownTableRow(line) &&
            containsInsensitive(line, targetString),
        );
      }

      if (matchedLineIndex < 0) {
        matchedLineIndex = findLineByTargetString(lines, targetString);
      }

      if (matchedLineIndex >= 0) {
        const nearestHeading = findNearestHeadingAbove(lines, matchedLineIndex);
        resolvedSectionHeading =
          sectionByAnchor?.text || sectionAnchor || nearestHeading?.text || "";

        return {
          ...basePlan,
          mode: "table_row",
          rowNeedles: getLineNeedles(lines[matchedLineIndex], targetString),
          matchedLineNumber: matchedLineIndex + 1,
          sectionStartIndex: sectionBounds?.start ?? null,
          sectionEndIndexExclusive: sectionBounds?.end ?? null,
          sectionHeading: resolvedSectionHeading,
        };
      }

      if (sectionByAnchor) {
        return {
          ...basePlan,
          mode: "section",
          sectionHeading: sectionByAnchor.text,
          message: "Exact line not found - showing section context.",
        };
      }

      return {
        ...basePlan,
        message: "Exact line not found - showing section context.",
      };
    }

    if (effectiveHighlightType === "bullet_item") {
      const sectionByAnchor = findHeadingByText(lines, sectionAnchor);
      if (!sectionByAnchor) return basePlan;

      const bounds = getSectionBounds(
        lines,
        sectionByAnchor.index,
        sectionByAnchor.level,
      );
      let bulletLineIndex = -1;

      for (let index = bounds.start + 1; index < bounds.end; index += 1) {
        const line = lines[index];
        if (
          bulletPattern.test(line) &&
          containsInsensitive(line, targetString)
        ) {
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
        const bulletNeedle = normalizeText(
          lines[bulletLineIndex].replace(bulletPattern, ""),
        );

        return {
          ...basePlan,
          mode: "bullet_item",
          sectionHeading: sectionByAnchor.text,
          bulletNeedle,
        };
      }

      return {
        ...basePlan,
        mode: "section",
        sectionHeading: sectionByAnchor.text,
        message: "Approximate match",
      };
    }

    return basePlan;
  }, [
    content,
    srcEvidence?.highlightType,
    srcEvidence?.lineNumber,
    srcEvidence?.targetString,
    srcEvidence?.sectionAnchor,
    srcEvidence?.precision,
    srcEvidence?.isGapReference,
    srcEvidence?.isUnresolved,
  ]);

  const normalizedSectionHeading = useMemo(
    () => normalizeText(highlightPlan.sectionHeading),
    [highlightPlan.sectionHeading],
  );

  const normalizedBulletNeedle = useMemo(
    () => normalizeText(highlightPlan.bulletNeedle),
    [highlightPlan.bulletNeedle],
  );

  const highlightFingerprint = useMemo(
    () =>
      [
        highlightPlan.mode,
        highlightPlan.sectionHeading,
        highlightPlan.sectionStartIndex ?? "",
        highlightPlan.sectionEndIndexExclusive ?? "",
        highlightPlan.bulletNeedle,
        highlightPlan.rowNeedles.join("|"),
        highlightPlan.matchedLineNumber ?? "",
        activeAcceptanceCriteriaCode ?? "",
        srcEvidence?.l2_id ?? "",
        srcEvidence?.lineNumber ?? "",
        srcEvidence?.targetString ?? "",
      ].join("::"),
    [
      highlightPlan.mode,
      highlightPlan.sectionHeading,
      highlightPlan.sectionStartIndex,
      highlightPlan.sectionEndIndexExclusive,
      highlightPlan.bulletNeedle,
      highlightPlan.rowNeedles,
      highlightPlan.matchedLineNumber,
      activeAcceptanceCriteriaCode,
      srcEvidence?.l2_id,
      srcEvidence?.lineNumber,
      srcEvidence?.targetString,
    ],
  );

  const renderState = {
    isInTargetSection: false,
    targetSectionLevel: 0,
    highlightedRow: false,
    highlightedBullet: false,
  };

  useEffect(() => {
    if (!resolvedUrl || srcEvidence?.isUnresolved) {
      return;
    }

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
          setContent("");
          setError("Markdown file is empty.");
          setResultUrl(resolvedUrl);
          return;
        }

        setContent(markdownText);
        setError("");
        setResultUrl(resolvedUrl);
      })
      .catch((err) => {
        if ((err as Error).name === "AbortError") return;
        setContent("");
        setError("Unable to load markdown preview.");
        setResultUrl(resolvedUrl);
      });

    return () => controller.abort();
  }, [resolvedUrl, srcEvidence?.isUnresolved]);

  useLayoutEffect(() => {
    const viewport = contentViewportRef.current;
    if (!viewport) return;

    if (!content.trim()) return;
    if (highlightPlan.mode !== "table_row") {
      clearRenderedTableRowFallbackHighlight(viewport);
      return;
    }

    const fallbackNeedles =
      highlightPlan.rowNeedles.length > 0
        ? highlightPlan.rowNeedles
        : getNeedleVariants(srcEvidence?.targetString ?? "");

    const highlightedRow =
      findRenderedTableRowByNeedles(
        viewport,
        fallbackNeedles,
        highlightPlan.sectionHeading,
      ) ||
      (highlightPlan.sectionHeading
        ? findRenderedTableRowByNeedles(viewport, fallbackNeedles, "")
        : null) ||
      viewport.querySelector<HTMLTableRowElement>(
        'tr[data-evidence-highlight="true"]',
      );

    if (!highlightedRow) return;

    clearRenderedTableRowFallbackHighlight(viewport);
    applyRenderedTableRowHighlight(highlightedRow);
  });

  useEffect(() => {
    if (!content.trim()) return;
    if (highlightPlan.mode === "none" || highlightPlan.mode === "file") return;

    const frameId = window.requestAnimationFrame(() => {
      const viewport = contentViewportRef.current;
      if (!viewport) return;

      const highlightedElement =
        highlightPlan.mode === "table_row"
          ? viewport.querySelector<HTMLElement>(
              'tr[data-evidence-highlight="true"], [data-evidence-highlight="true"]',
            )
          : viewport.querySelector<HTMLElement>(
              '[data-evidence-highlight="true"]',
            );

      if (!highlightedElement) return;

      highlightedElement.scrollIntoView({
        behavior: "smooth",
        block: "center",
        inline: "nearest",
      });
    });

    return () => window.cancelAnimationFrame(frameId);
  }, [content, highlightFingerprint, highlightPlan.mode]);

  const updateSectionState = (level: number, text: string) => {
    if (!normalizedSectionHeading) {
      return { isInTargetSection: false };
    }

    const normalizedHeadingText = normalizeText(text);
    const isTargetHeading =
      normalizedHeadingText === normalizedSectionHeading ||
      normalizedHeadingText.includes(normalizedSectionHeading) ||
      normalizedSectionHeading.includes(normalizedHeadingText);

    if (isTargetHeading) {
      renderState.isInTargetSection = true;
      renderState.targetSectionLevel = level;
    } else if (
      renderState.isInTargetSection &&
      level <= renderState.targetSectionLevel
    ) {
      renderState.isInTargetSection = false;
      renderState.targetSectionLevel = 0;
    }

    return { isInTargetSection: renderState.isInTargetSection };
  };

  const getSectionHighlightStyle = () => {
    if (highlightPlan.mode !== "section" || !renderState.isInTargetSection) {
      return undefined;
    }

    return {
      borderLeft: "4px solid #FFA726",
      paddingLeft: "0.75rem",
    };
  };

  const shouldHighlightSectionHeading = (headingText: string) => {
    return (
      (highlightPlan.mode === "section" ||
        highlightPlan.mode === "table_row") &&
      isHeadingTextMatch(headingText, highlightPlan.sectionHeading)
    );
  };

  const getHeadingHighlightStyle = (
    isInTargetSection: boolean,
    isTargetHeading: boolean,
  ) => {
    if (highlightPlan.mode === "section" && isInTargetSection) {
      return getSectionHighlightStyle();
    }

    if (highlightPlan.mode === "table_row" && isTargetHeading) {
      return {
        borderLeft: "4px solid #FFA726",
        paddingLeft: "0.75rem",
        backgroundColor: "#FFFDE7",
      };
    }

    return undefined;
  };

  if (!resolvedUrl && !content) {
    if (srcEvidence?.isUnresolved) {
      return <div className="w-full" />;
    }

    return (
      <div className="w-full">
        <p className="text-[length:var(--font-size-xxsm)] text-[color:var(--text-warning)]">
          {srcEvidence?.isGapReference
            ? "No source file available for this GAP reference."
            : "No markdown URL provided."}
        </p>
      </div>
    );
  }

  const isLoading = !srcEvidence?.isUnresolved && resultUrl !== resolvedUrl;

  if (isLoading) {
    return (
      <div className="w-full">
        <p className="text-[length:var(--font-size-xxsm)] text-[color:var(--color-neutral-400)] animate-pulse">
          Loading markdown preview...
        </p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="w-full">
        <p className="text-[length:var(--font-size-xxsm)] text-[color:var(--text-warning)]">
          {error}
        </p>
        {resolvedUrl && (
          <a
            href={resolvedUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-2 inline-block text-[length:var(--font-size-xxsm)] text-[color:var(--color-blue-500)] hover:underline"
          >
            Open source file
          </a>
        )}
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 w-full min-w-0 flex-col">
      {srcEvidence?.isGapReference && (
        <div className="mb-2 break-words rounded-[6px] border border-[#FDB022] bg-[#FFFAEB] px-2 py-1 text-[length:var(--font-size-xxsm)] text-[#B54708]">
          No exact anchor — honest GAP marker (human decision required).
          {!!srcEvidence?.l2SourceRef?.trim() &&
            ` Reference: ${srcEvidence.l2SourceRef}`}
        </div>
      )}

      {highlightPlan.mode === "file" && (
        <div className="mb-2 break-words rounded-[6px] border border-[#FDB022] bg-[#FFFAEB] px-2 py-1 text-[length:var(--font-size-xxsm)] text-[#B54708]">
          Low Precision - exact location could not be determined.
        </div>
      )}

      {highlightPlan.mode === "file" &&
        !!srcEvidence?.contextSnippet?.trim() && (
          <div className="mb-2 break-words rounded-[6px] border border-[color:var(--color-neutral-200)] bg-[color:var(--bg-base2)] px-2 py-1 text-[length:var(--font-size-xxsm)] text-[color:var(--color-neutral-400)]">
            {srcEvidence.contextSnippet}
          </div>
        )}

      {highlightPlan.mode === "file" && !!srcEvidence?.l2SourceRef?.trim() && (
        <div className="mb-2 break-words text-[length:var(--font-size-xxsm)] text-[color:var(--color-neutral-400)]">
          Reference: {srcEvidence.l2SourceRef}
        </div>
      )}

      {highlightPlan.mode !== "file" && !!highlightPlan.message && (
        <div className="mb-2 break-words rounded-[6px] border border-[color:var(--color-neutral-200)] bg-[color:var(--bg-base2)] px-2 py-1 text-[length:var(--font-size-xxsm)] text-[color:var(--color-neutral-400)]">
          {highlightPlan.message}
        </div>
      )}

      <div
        ref={contentViewportRef}
        className="flex-1 min-h-0 min-w-0 overflow-y-auto overflow-x-hidden py-2"
      >
        <article className="prose prose-slate max-w-none break-words">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[rehypeRaw, rehypeSanitize]}
            components={{
              h1: (props) => {
                const headingText = nodeToText(props.children);
                const { isInTargetSection } = updateSectionState(
                  1,
                  headingText,
                );
                const isTargetHeading =
                  shouldHighlightSectionHeading(headingText);
                return (
                  <h1
                    data-evidence-highlight={
                      isTargetHeading ? "true" : undefined
                    }
                    style={getHeadingHighlightStyle(
                      isInTargetSection,
                      isTargetHeading,
                    )}
                    {...props}
                  />
                );
              },
              h2: (props) => {
                const headingText = nodeToText(props.children);
                const { isInTargetSection } = updateSectionState(
                  2,
                  headingText,
                );
                const isTargetHeading =
                  shouldHighlightSectionHeading(headingText);
                return (
                  <h2
                    data-evidence-highlight={
                      isTargetHeading ? "true" : undefined
                    }
                    style={getHeadingHighlightStyle(
                      isInTargetSection,
                      isTargetHeading,
                    )}
                    {...props}
                  />
                );
              },
              h3: (props) => {
                const headingText = nodeToText(props.children);
                const { isInTargetSection } = updateSectionState(
                  3,
                  headingText,
                );
                const isTargetHeading =
                  shouldHighlightSectionHeading(headingText);
                return (
                  <h3
                    data-evidence-highlight={
                      isTargetHeading ? "true" : undefined
                    }
                    style={getHeadingHighlightStyle(
                      isInTargetSection,
                      isTargetHeading,
                    )}
                    {...props}
                  />
                );
              },
              h4: (props) => {
                const headingText = nodeToText(props.children);
                const { isInTargetSection } = updateSectionState(
                  4,
                  headingText,
                );
                const isTargetHeading =
                  shouldHighlightSectionHeading(headingText);
                return (
                  <h4
                    data-evidence-highlight={
                      isTargetHeading ? "true" : undefined
                    }
                    style={getHeadingHighlightStyle(
                      isInTargetSection,
                      isTargetHeading,
                    )}
                    {...props}
                  />
                );
              },
              h5: (props) => {
                const headingText = nodeToText(props.children);
                const { isInTargetSection } = updateSectionState(
                  5,
                  headingText,
                );
                const isTargetHeading =
                  shouldHighlightSectionHeading(headingText);
                return (
                  <h5
                    data-evidence-highlight={
                      isTargetHeading ? "true" : undefined
                    }
                    style={getHeadingHighlightStyle(
                      isInTargetSection,
                      isTargetHeading,
                    )}
                    {...props}
                  />
                );
              },
              h6: (props) => {
                const headingText = nodeToText(props.children);
                const { isInTargetSection } = updateSectionState(
                  6,
                  headingText,
                );
                const isTargetHeading =
                  shouldHighlightSectionHeading(headingText);
                return (
                  <h6
                    data-evidence-highlight={
                      isTargetHeading ? "true" : undefined
                    }
                    style={getHeadingHighlightStyle(
                      isInTargetSection,
                      isTargetHeading,
                    )}
                    {...props}
                  />
                );
              },
              a: (props) => (
                <a
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[color:var(--color-blue-500)] hover:underline"
                  {...props}
                />
              ),
              table: (props) => (
                <div
                  className="overflow-x-auto"
                  style={getSectionHighlightStyle()}
                >
                  <table
                    className="min-w-full border border-[color:var(--color-brand-green-50)]"
                    {...props}
                  />
                </div>
              ),
              tr: (props) => {
                const rawRowText = nodeToText(props.children);
                const rowText = normalizeRowCandidate(rawRowText);
                const rowTextLoose = normalizeLooseText(rawRowText);
                const isHeaderRow = isTableHeaderRow(props.children);
                const rowStartLine = (
                  props as {
                    node?: { position?: { start?: { line?: number } } };
                  }
                ).node?.position?.start?.line;
                const isDeterministicMatch =
                  !!rowStartLine &&
                  highlightPlan.matchedLineNumber === rowStartLine;
                const rowStartIndex =
                  typeof rowStartLine === "number" ? rowStartLine - 1 : null;
                const hasSectionBounds =
                  highlightPlan.sectionStartIndex !== null &&
                  highlightPlan.sectionEndIndexExclusive !== null;
                const sectionStartIndex = highlightPlan.sectionStartIndex;
                const sectionEndIndexExclusive =
                  highlightPlan.sectionEndIndexExclusive;
                const rowNeedlesForRender =
                  highlightPlan.rowNeedles.length > 0
                    ? highlightPlan.rowNeedles
                    : getNeedleVariants(srcEvidence?.targetString ?? "");
                const isInScopedSection =
                  !hasSectionBounds ||
                  (rowStartIndex !== null
                    ? sectionStartIndex !== null &&
                      sectionEndIndexExclusive !== null &&
                      rowStartIndex > sectionStartIndex &&
                      rowStartIndex < sectionEndIndexExclusive
                    : true);
                const shouldHighlightRow =
                  highlightPlan.mode === "table_row" &&
                  isInScopedSection &&
                  !isHeaderRow &&
                  !renderState.highlightedRow &&
                  (isDeterministicMatch ||
                    rowNeedlesForRender.some((needle) => {
                      const normalizedNeedle = normalizeText(needle);
                      const looseNeedle = normalizeLooseText(needle);

                      return (
                        (!!normalizedNeedle &&
                          rowText.includes(normalizedNeedle)) ||
                        (!!looseNeedle && rowTextLoose.includes(looseNeedle))
                      );
                    }));

                if (shouldHighlightRow) {
                  renderState.highlightedRow = true;
                }

                return (
                  <tr
                    data-evidence-highlight={
                      shouldHighlightRow ? "true" : undefined
                    }
                    className={
                      shouldHighlightRow
                        ? `${props.className ?? ""} [&>td]:!bg-[#FFF59D] [&>td]:transition-colors`.trim()
                        : props.className
                    }
                    style={
                      shouldHighlightRow
                        ? { backgroundColor: "#FFF59D" }
                        : getSectionHighlightStyle()
                    }
                    {...props}
                  />
                );
              },
              th: (props) => (
                <th
                  className="border border-[color:var(--color-brand-green-50)] bg-[color:var(--bg-base2)] px-2 py-1 text-left"
                  {...props}
                />
              ),
              td: (props) => (
                <td
                  className="border border-[color:var(--color-brand-green-50)] px-2 py-1"
                  {...props}
                />
              ),
              p: (props) => <p style={getSectionHighlightStyle()} {...props} />,
              ul: (props) => (
                <ul style={getSectionHighlightStyle()} {...props} />
              ),
              ol: (props) => (
                <ol style={getSectionHighlightStyle()} {...props} />
              ),
              pre: (props) => (
                <pre style={getSectionHighlightStyle()} {...props} />
              ),
              blockquote: (props) => (
                <blockquote style={getSectionHighlightStyle()} {...props} />
              ),
              li: (props) => {
                const listText = normalizeText(nodeToText(props.children));
                const shouldHighlightBullet =
                  highlightPlan.mode === "bullet_item" &&
                  renderState.isInTargetSection &&
                  !!normalizedBulletNeedle &&
                  !renderState.highlightedBullet &&
                  listText.includes(normalizedBulletNeedle);

                if (shouldHighlightBullet) {
                  renderState.highlightedBullet = true;
                }

                return (
                  <li
                    data-evidence-highlight={
                      shouldHighlightBullet ? "true" : undefined
                    }
                    style={
                      shouldHighlightBullet
                        ? { backgroundColor: "#FFFDE7" }
                        : getSectionHighlightStyle()
                    }
                    {...props}
                  />
                );
              },
              code: (props) => {
                const isInline =
                  !props.className || !props.className.includes("language-");
                if (isInline) {
                  return (
                    <code
                      className="rounded bg-[color:var(--bg-base2)] px-1 py-0.5 text-[length:var(--font-size-xxsm)]"
                      {...props}
                    />
                  );
                }

                return (
                  <code
                    className="block overflow-x-auto rounded bg-[color:var(--bg-base2)] p-2 text-[length:var(--font-size-xxsm)]"
                    {...props}
                  />
                );
              },
            }}
          >
            {content}
          </ReactMarkdown>
        </article>
      </div>
    </div>
  );
}
