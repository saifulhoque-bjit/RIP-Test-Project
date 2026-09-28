import type {
  AcceptanceCriterion,
  ChangeType,
  NfrItem,
  TextDiffItem,
} from "@/types/user-story";
import type { FeatureFunctionItem } from "@/types/feature";

export interface ChangeDiffVersion {
  /** Column heading, e.g. "Current" / "Proposed". */
  label: string;
  /** Title/summary line — always rendered plain, never diff-highlighted. */
  narrative: string;
  /** Content lines diffed by set-membership against the other version's lines. */
  lines: string[];
}

/** Full field snapshot for one side of a user story attribute comparison. */
export interface UserStoryAttributeSnapshot {
  title?: string | null;
  as_a?: string | null;
  i_want_to?: string | null;
  so_that?: string | null;
  technical_notes?: string | null;
  story_points?: number | null;
  acceptance_criteria?: AcceptanceCriterion[];
  nfrs?: NfrItem[];
}

/** Full field snapshot for one side of a module/feature attribute comparison. */
export interface EntityAttributeSnapshot {
  name?: string | null;
  description?: string | null;
  functions?: FeatureFunctionItem[];
}

interface ChangeDiffPanelProps {
  changedAction: Exclude<ChangeType, null>;
  /** Present for "DELETE_SUGGESTED", and as a fallback for "UPDATED" when textDiffs/attributeDiff are absent. */
  previous?: ChangeDiffVersion;
  /** Present for "ADDED", and as a fallback for "UPDATED" when textDiffs/attributeDiff are absent. */
  proposed?: ChangeDiffVersion;
  /** Field-level before/after pairs for "UPDATED" — takes priority over previous/proposed. */
  textDiffs?: TextDiffItem[];
  /**
   * Full before/after attribute snapshots for "UPDATED" — renders an
   * attribute-by-attribute comparison table showing every field (changed or
   * unchanged) in aligned before/after columns. Takes priority over
   * textDiffs/previous/proposed.
   */
  attributeDiff?:
    | {
        kind: "user_story";
        current: UserStoryAttributeSnapshot;
        previous?: UserStoryAttributeSnapshot;
      }
    | {
        kind: "entity";
        current: EntityAttributeSnapshot;
        previous?: EntityAttributeSnapshot;
      };
}

type LineTone = "plain" | "add" | "del";

function DiffLine({ text, tone }: { text: string; tone: LineTone }) {
  if (!text) return null;

  return (
    <div
      className={`border-b border-[#f0f2f6] p-[6px_10px] text-[12px] leading-[1.55] last:border-b-0 ${
        tone === "del"
          ? "bg-[var(--error-50)] text-[#8a1c12] line-through"
          : tone === "add"
            ? "bg-[var(--success-50)] text-[#0a6b4a]"
            : "text-[var(--text-primary)]"
      }`}
    >
      {text}
    </div>
  );
}

function DiffColumn({
  tone,
  version,
  lineTone,
}: {
  tone: "old" | "new";
  version: ChangeDiffVersion;
  lineTone: (line: string) => LineTone;
}) {
  return (
    <div className="overflow-hidden rounded-[8px] border border-[var(--border-primary)]">
      <div
        className={`px-[10px] py-[7px] text-[10px] font-bold uppercase tracking-[0.4px] ${
          tone === "old"
            ? "bg-[var(--error-50)] text-[var(--error)]"
            : "bg-[var(--success-50)] text-[var(--success)]"
        }`}
      >
        {version.label}
      </div>
      <DiffLine text={version.narrative} tone="plain" />
      {version.lines.map((line, idx) => (
        <DiffLine key={idx} text={line} tone={lineTone(line)} />
      ))}
    </div>
  );
}

// Border classes for one cell of a two-column card grid — first row closes the
// top corners, last row closes the bottom corners, so each column still reads
// as its own card even though cells are flat grid items (kept in lockstep row
// heights with their opposite-column counterpart).
function textDiffCellClass(position: "first" | "middle" | "last") {
  const sides = "border-l border-r border-[var(--border-primary)]";
  if (position === "first") {
    return `${sides} rounded-t-[8px] border-t border-b border-b-[#f0f2f6]`;
  }
  if (position === "last") {
    return `${sides} rounded-b-[8px] border-b`;
  }
  return `${sides} border-b border-b-[#f0f2f6]`;
}

function TextDiffCard({ textDiffs }: { textDiffs: TextDiffItem[] }) {
  const lastIdx = textDiffs.length - 1;

  return (
    <div className="grid grid-cols-2 gap-x-3 gap-y-0">
      <div
        className={`${textDiffCellClass("first")} bg-[var(--error-50)] px-[10px] py-[7px] text-[10px] font-bold uppercase tracking-[0.4px] text-[var(--error)]`}
      >
        Before
      </div>
      <div
        className={`${textDiffCellClass("first")} bg-[var(--success-50)] px-[10px] py-[7px] text-[10px] font-bold uppercase tracking-[0.4px] text-[var(--success)]`}
      >
        After
      </div>
      {textDiffs.flatMap((diff, idx) => {
        const position = idx === lastIdx ? "last" : "middle";
        return [
          <div
            key={`before-${idx}`}
            className={`${textDiffCellClass(position)} bg-[var(--error-50)] p-[8px_10px] text-[12px] leading-[1.55] text-[#8a1c12] line-through`}
          >
            {diff.before}
          </div>,
          <div
            key={`after-${idx}`}
            className={`${textDiffCellClass(position)} bg-[var(--success-50)] p-[8px_10px] text-[12px] leading-[1.55] text-[#0a6b4a]`}
          >
            {diff.after}
          </div>,
        ];
      })}
    </div>
  );
}

// ── Attribute-by-attribute comparison (user story "UPDATED") ────────────────
// Shows every field — changed and unchanged — as an aligned before/after row,
// built directly from the full previous/current snapshots so the table stays
// correct even where the backend's text_diffs omits a field. Acceptance
// criteria and NFRs are clustered into their own labeled groups (one per
// ac_code / NFR) so a multi-AC story stays easy to scan.

type AttributeRowStatus = "same" | "changed" | "added" | "removed";

interface AttributeRow {
  key: string;
  label: string;
  before: string;
  after: string;
  status: AttributeRowStatus;
}

/** A cluster of rows belonging to one acceptance criterion / NFR. */
interface AttributeRowGroup {
  key: string;
  heading: string;
  /** Set when the whole item (not just a field within it) was added/removed. */
  status?: "added" | "removed";
  rows: AttributeRow[];
}

interface AttributeSection {
  key: string;
  title?: string;
  rows?: AttributeRow[];
  groups?: AttributeRowGroup[];
}

function pushAttributeRow(
  rows: AttributeRow[],
  key: string,
  label: string,
  currentVal?: string | number | null,
  previousVal?: string | number | null,
) {
  const before =
    previousVal !== undefined && previousVal !== null && previousVal !== ""
      ? String(previousVal)
      : "";
  const after =
    currentVal !== undefined && currentVal !== null && currentVal !== ""
      ? String(currentVal)
      : "";
  if (!before && !after) return;

  let status: AttributeRowStatus;
  if (!before) status = "added";
  else if (!after) status = "removed";
  else status = before.trim() === after.trim() ? "same" : "changed";

  rows.push({ key, label, before, after, status });
}

function buildUserStoryAttributeSections(
  current: UserStoryAttributeSnapshot,
  previous?: UserStoryAttributeSnapshot,
): AttributeSection[] {
  const sections: AttributeSection[] = [];

  const narrativeRows: AttributeRow[] = [];
  pushAttributeRow(
    narrativeRows,
    "title",
    "Title",
    current.title,
    previous?.title,
  );
  pushAttributeRow(
    narrativeRows,
    "as_a",
    "As a",
    current.as_a,
    previous?.as_a,
  );
  pushAttributeRow(
    narrativeRows,
    "i_want_to",
    "I want to",
    current.i_want_to,
    previous?.i_want_to,
  );
  pushAttributeRow(
    narrativeRows,
    "so_that",
    "So that",
    current.so_that,
    previous?.so_that,
  );
  if (narrativeRows.length > 0) {
    sections.push({ key: "narrative", rows: narrativeRows });
  }

  const currentAcs = current.acceptance_criteria ?? [];
  const previousAcs = previous?.acceptance_criteria ?? [];
  const acCodes: string[] = [];
  [...currentAcs, ...previousAcs].forEach((ac) => {
    if (ac.ac_code && !acCodes.includes(ac.ac_code)) acCodes.push(ac.ac_code);
  });

  const acGroups: AttributeRowGroup[] = [];
  acCodes.forEach((code) => {
    const currAc = currentAcs.find((ac) => ac.ac_code === code);
    const prevAc = previousAcs.find((ac) => ac.ac_code === code);
    const type = currAc?.type ?? prevAc?.type;
    const heading = type ? `${code} · ${type}` : code;

    const rows: AttributeRow[] = [];
    pushAttributeRow(rows, `${code}-given`, "Given", currAc?.given, prevAc?.given);
    pushAttributeRow(rows, `${code}-when`, "When", currAc?.when, prevAc?.when);
    pushAttributeRow(rows, `${code}-then`, "Then", currAc?.then, prevAc?.then);
    if (rows.length === 0) return;

    acGroups.push({
      key: code,
      heading,
      status: !prevAc ? "added" : !currAc ? "removed" : undefined,
      rows,
    });
  });
  if (acGroups.length > 0) {
    sections.push({
      key: "acceptance_criteria",
      title: "Acceptance criteria",
      groups: acGroups,
    });
  }

  const currentNfrs = current.nfrs ?? [];
  const previousNfrs = previous?.nfrs ?? [];
  const nfrKey = (nfr: NfrItem, idx: number) => nfr.id || `idx-${idx}`;
  const nfrKeys: string[] = [];
  currentNfrs.forEach((nfr, idx) => {
    const key = nfrKey(nfr, idx);
    if (!nfrKeys.includes(key)) nfrKeys.push(key);
  });
  previousNfrs.forEach((nfr, idx) => {
    const key = nfrKey(nfr, idx);
    if (!nfrKeys.includes(key)) nfrKeys.push(key);
  });

  const nfrGroups: AttributeRowGroup[] = [];
  nfrKeys.forEach((key) => {
    const currNfr = currentNfrs.find((nfr, idx) => nfrKey(nfr, idx) === key);
    const prevNfr = previousNfrs.find((nfr, idx) => nfrKey(nfr, idx) === key);
    const category = currNfr?.category ?? prevNfr?.category;
    const heading = category ? `NFR · ${category}` : "NFR";

    const rows: AttributeRow[] = [];
    pushAttributeRow(
      rows,
      `${key}-requirement`,
      "Requirement",
      currNfr?.requirement,
      prevNfr?.requirement,
    );
    pushAttributeRow(
      rows,
      `${key}-description`,
      "Description",
      currNfr?.description,
      prevNfr?.description,
    );
    if (rows.length === 0) return;

    nfrGroups.push({
      key,
      heading,
      status: !prevNfr ? "added" : !currNfr ? "removed" : undefined,
      rows,
    });
  });
  if (nfrGroups.length > 0) {
    sections.push({
      key: "nfrs",
      title: "Non-functional requirements",
      groups: nfrGroups,
    });
  }

  const otherRows: AttributeRow[] = [];
  pushAttributeRow(
    otherRows,
    "story_points",
    "Story points",
    current.story_points,
    previous?.story_points,
  );
  if (otherRows.length > 0) {
    sections.push({ key: "other", rows: otherRows });
  }

  return sections;
}

function buildEntityAttributeSections(
  current: EntityAttributeSnapshot,
  previous?: EntityAttributeSnapshot,
): AttributeSection[] {
  const sections: AttributeSection[] = [];

  const narrativeRows: AttributeRow[] = [];
  pushAttributeRow(narrativeRows, "name", "Name", current.name, previous?.name);
  pushAttributeRow(
    narrativeRows,
    "description",
    "Description",
    current.description,
    previous?.description,
  );
  if (narrativeRows.length > 0) {
    sections.push({ key: "narrative", rows: narrativeRows });
  }

  const currentFns = current.functions ?? [];
  const previousFns = previous?.functions ?? [];
  const funCodes: string[] = [];
  [...currentFns, ...previousFns].forEach((fn) => {
    if (fn.fun_code && !funCodes.includes(fn.fun_code))
      funCodes.push(fn.fun_code);
  });

  const fnGroups: AttributeRowGroup[] = [];
  funCodes.forEach((code) => {
    const currFn = currentFns.find((fn) => fn.fun_code === code);
    const prevFn = previousFns.find((fn) => fn.fun_code === code);

    const rows: AttributeRow[] = [];
    pushAttributeRow(rows, `${code}-name`, "Name", currFn?.name, prevFn?.name);
    pushAttributeRow(
      rows,
      `${code}-description`,
      "Description",
      currFn?.description,
      prevFn?.description,
    );
    if (rows.length === 0) return;

    fnGroups.push({
      key: code,
      heading: code,
      status: !prevFn ? "added" : !currFn ? "removed" : undefined,
      rows,
    });
  });
  if (fnGroups.length > 0) {
    sections.push({ key: "functions", title: "Functions", groups: fnGroups });
  }

  return sections;
}

function AttributeDiffRow({
  row,
  isLast,
  indent,
}: {
  row: AttributeRow;
  isLast: boolean;
  indent?: boolean;
}) {
  const showBefore = row.status !== "added";
  const showAfter = row.status !== "removed";

  return (
    <div
      className={`${indent ? "p-[10px_12px_10px_26px]" : "p-[10px_12px]"} ${isLast ? "" : "border-b border-b-[#f0f2f6]"}`}
    >
      <div className="mb-1.5 text-[10px] font-bold uppercase tracking-[0.4px] text-[var(--text-tertiary)]">
        {row.label}
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div
          className={`rounded-[6px] p-[8px_10px] text-[12px] leading-[1.55] ${
            row.status === "changed" || row.status === "removed"
              ? "bg-[var(--error-50)] text-[#8a1c12] line-through"
              : "bg-[#fafbfd] text-[var(--text-primary)]"
          }`}
        >
          {showBefore ? (
            row.before
          ) : (
            <span className="text-[var(--text-tertiary)]">—</span>
          )}
        </div>
        <div
          className={`rounded-[6px] p-[8px_10px] text-[12px] leading-[1.55] ${
            row.status === "changed" || row.status === "added"
              ? "bg-[var(--success-50)] text-[#0a6b4a]"
              : "bg-[#fafbfd] text-[var(--text-primary)]"
          }`}
        >
          {showAfter ? (
            row.after
          ) : (
            <span className="text-[var(--text-tertiary)]">—</span>
          )}
        </div>
      </div>
    </div>
  );
}

// One acceptance-criterion / NFR cluster — a heading strip (name + optional
// added/removed badge) followed by its indented, left-accented field rows,
// so related fields read as a single unit instead of blending into the list.
function AttributeGroupBlock({
  group,
  isLastInSection,
  lastRowKey,
}: {
  group: AttributeRowGroup;
  isLastInSection: boolean;
  lastRowKey?: string;
}) {
  const lastRowIdx = group.rows.length - 1;

  return (
    <div
      className={`bg-[#fcfcfe] ${isLastInSection ? "" : "border-b border-b-[#f0f2f6]"}`}
    >
      <div className="flex items-center gap-1.5 border-b border-b-[#f0f2f6] bg-[var(--accent-50)] px-[12px] py-[6px] text-[11px] font-semibold text-[var(--text-secondary)]">
        <span>{group.heading}</span>
        {group.status === "added" && (
          <span className="rounded-full bg-[var(--success-50)] px-[6px] py-[1px] text-[9px] font-bold uppercase tracking-[0.2px] text-[var(--success)]">
            Added
          </span>
        )}
        {group.status === "removed" && (
          <span className="rounded-full bg-[var(--error-50)] px-[6px] py-[1px] text-[9px] font-bold uppercase tracking-[0.2px] text-[var(--error)]">
            Removed
          </span>
        )}
      </div>
      <div className="border-l-[3px] border-l-[var(--accent)]">
        {group.rows.map((row, idx) => (
          <AttributeDiffRow
            key={row.key}
            row={row}
            isLast={idx === lastRowIdx || row.key === lastRowKey}
            indent
          />
        ))}
      </div>
    </div>
  );
}

function AttributeDiffTable({ sections }: { sections: AttributeSection[] }) {
  const lastSectionIdx = sections.length - 1;

  return (
    <div className="overflow-hidden rounded-[8px] border border-[var(--border-primary)]">
      <div className="grid grid-cols-2 gap-3 border-b border-b-[var(--border-primary)] bg-[#fafbfd] px-[12px] py-[7px]">
        <div className="text-[10px] font-bold uppercase tracking-[0.4px] text-[var(--error)]">
          Before
        </div>
        <div className="text-[10px] font-bold uppercase tracking-[0.4px] text-[var(--success)]">
          After
        </div>
      </div>
      {sections.map((section, sIdx) => {
        const isLastSection = sIdx === lastSectionIdx;
        const lastRowIdx = section.rows ? section.rows.length - 1 : -1;

        return (
          <div key={section.key}>
            {section.title && (
              <div
                className={`${sIdx === 0 ? "" : "border-t border-t-[var(--border-primary)]"} border-b border-b-[#f0f2f6] bg-[#f5f7fb] px-[12px] py-[6px] text-[10px] font-bold uppercase tracking-[0.5px] text-[var(--text-tertiary)]`}
              >
                {section.title}
              </div>
            )}
            {section.rows?.map((row, idx) => (
              <AttributeDiffRow
                key={row.key}
                row={row}
                isLast={isLastSection && idx === lastRowIdx}
              />
            ))}
            {section.groups?.map((group, gIdx) => (
              <AttributeGroupBlock
                key={group.key}
                group={group}
                isLastInSection={
                  isLastSection && gIdx === (section.groups?.length ?? 0) - 1
                }
              />
            ))}
          </div>
        );
      })}
    </div>
  );
}

// ── Single-column attribute view (ADDED / DELETE_SUGGESTED) ─────────────────
// Same section/group structure as the before/after table, but only one value
// per row — the whole thing tinted success (new) or error (proposed removal)
// instead of diffed, since there's nothing to compare against.

function AttributeSingleRow({
  row,
  tone,
  isLast,
  indent,
}: {
  row: AttributeRow;
  tone: "add" | "del";
  isLast: boolean;
  indent?: boolean;
}) {
  return (
    <div
      className={`${indent ? "p-[10px_12px_10px_26px]" : "p-[10px_12px]"} ${isLast ? "" : "border-b border-b-[#f0f2f6]"}`}
    >
      <div className="mb-1.5 text-[10px] font-bold uppercase tracking-[0.4px] text-[var(--text-tertiary)]">
        {row.label}
      </div>
      <div
        className={`rounded-[6px] p-[8px_10px] text-[12px] leading-[1.55] ${
          tone === "add"
            ? "bg-[var(--success-50)] text-[#0a6b4a]"
            : "bg-[var(--error-50)] text-[#8a1c12]"
        }`}
      >
        {row.after}
      </div>
    </div>
  );
}

function AttributeSingleGroupBlock({
  group,
  tone,
  isLastInSection,
  lastRowKey,
}: {
  group: AttributeRowGroup;
  tone: "add" | "del";
  isLastInSection: boolean;
  lastRowKey?: string;
}) {
  const lastRowIdx = group.rows.length - 1;

  return (
    <div
      className={`bg-[#fcfcfe] ${isLastInSection ? "" : "border-b border-b-[#f0f2f6]"}`}
    >
      <div className="border-b border-b-[#f0f2f6] bg-[var(--accent-50)] px-[12px] py-[6px] text-[11px] font-semibold text-[var(--text-secondary)]">
        {group.heading}
      </div>
      <div
        className={`border-l-[3px] ${tone === "add" ? "border-l-[var(--success)]" : "border-l-[var(--error)]"}`}
      >
        {group.rows.map((row, idx) => (
          <AttributeSingleRow
            key={row.key}
            row={row}
            tone={tone}
            isLast={idx === lastRowIdx || row.key === lastRowKey}
            indent
          />
        ))}
      </div>
    </div>
  );
}

function AttributeSingleColumnTable({
  sections,
  tone,
}: {
  sections: AttributeSection[];
  tone: "add" | "del";
}) {
  const lastSectionIdx = sections.length - 1;

  return (
    <div className="overflow-hidden rounded-[8px] border border-[var(--border-primary)]">
      {sections.map((section, sIdx) => {
        const isLastSection = sIdx === lastSectionIdx;
        const lastRowIdx = section.rows ? section.rows.length - 1 : -1;

        return (
          <div key={section.key}>
            {section.title && (
              <div
                className={`${sIdx === 0 ? "" : "border-t border-t-[var(--border-primary)]"} border-b border-b-[#f0f2f6] bg-[#f5f7fb] px-[12px] py-[6px] text-[10px] font-bold uppercase tracking-[0.5px] text-[var(--text-tertiary)]`}
              >
                {section.title}
              </div>
            )}
            {section.rows?.map((row, idx) => (
              <AttributeSingleRow
                key={row.key}
                row={row}
                tone={tone}
                isLast={isLastSection && idx === lastRowIdx}
              />
            ))}
            {section.groups?.map((group, gIdx) => (
              <AttributeSingleGroupBlock
                key={group.key}
                group={group}
                tone={tone}
                isLastInSection={
                  isLastSection && gIdx === (section.groups?.length ?? 0) - 1
                }
              />
            ))}
          </div>
        );
      })}
    </div>
  );
}

export default function ChangeDiffPanel({
  changedAction,
  previous,
  proposed,
  textDiffs,
  attributeDiff,
}: ChangeDiffPanelProps) {
  const sectionLabelClass =
    "m-0 mb-2 text-[11px] font-bold uppercase tracking-[0.5px] text-[var(--text-tertiary)]";

  if (changedAction === "UPDATED") {
    if (attributeDiff) {
      const sections =
        attributeDiff.kind === "entity"
          ? buildEntityAttributeSections(
              attributeDiff.current,
              attributeDiff.previous,
            )
          : buildUserStoryAttributeSections(
              attributeDiff.current,
              attributeDiff.previous,
            );
      if (sections.length > 0) {
        return (
          <div>
            <p className={sectionLabelClass}>Before → after</p>
            <AttributeDiffTable sections={sections} />
          </div>
        );
      }
    }

    if (textDiffs && textDiffs.length > 0) {
      return (
        <div>
          <p className={sectionLabelClass}>Before → after</p>
          <TextDiffCard textDiffs={textDiffs} />
        </div>
      );
    }

    if (previous && proposed) {
      return (
        <div>
          <p className={sectionLabelClass}>Before → after</p>
          <div className="grid grid-cols-2 gap-3">
            <DiffColumn
              tone="old"
              version={previous}
              lineTone={(line) =>
                proposed.lines.includes(line) ? "plain" : "del"
              }
            />
            <DiffColumn
              tone="new"
              version={proposed}
              lineTone={(line) =>
                previous.lines.includes(line) ? "plain" : "add"
              }
            />
          </div>
        </div>
      );
    }

    if (previous) {
      return (
        <div>
          <p className={sectionLabelClass}>Current</p>
          <DiffColumn tone="old" version={previous} lineTone={() => "plain"} />
        </div>
      );
    }

    return null;
  }

  if (changedAction === "ADDED") {
    if (attributeDiff) {
      const sections =
        attributeDiff.kind === "entity"
          ? buildEntityAttributeSections(attributeDiff.current)
          : buildUserStoryAttributeSections(attributeDiff.current);
      if (sections.length > 0) {
        return (
          <div>
            <p className={sectionLabelClass}>Proposed new</p>
            <AttributeSingleColumnTable sections={sections} tone="add" />
          </div>
        );
      }
    }

    if (proposed) {
      return (
        <div>
          <p className={sectionLabelClass}>Proposed new</p>
          <DiffColumn tone="new" version={proposed} lineTone={() => "add"} />
        </div>
      );
    }
  }

  if (changedAction === "DELETE_SUGGESTED") {
    if (attributeDiff) {
      const sections =
        attributeDiff.kind === "entity"
          ? buildEntityAttributeSections(attributeDiff.current)
          : buildUserStoryAttributeSections(attributeDiff.current);
      if (sections.length > 0) {
        return (
          <div>
            <p className={sectionLabelClass}>Proposed removal</p>
            <AttributeSingleColumnTable sections={sections} tone="del" />
          </div>
        );
      }
    }

    if (previous) {
      return (
        <div>
          <p className={sectionLabelClass}>Proposed removal</p>
          <DiffColumn tone="old" version={previous} lineTone={() => "del"} />
        </div>
      );
    }
  }

  return null;
}
