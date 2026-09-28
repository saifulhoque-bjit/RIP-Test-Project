import documentIcon from "@/assets/icons/file-icons/file-categories/document-icon.svg";
import codeIcon from "@/assets/icons/file-icons/file-categories/source-code-icon.svg";
import type { ProjectType } from "@/types";
import type { DropdownOption } from "@/components/common/Dropdown";
import { getTypeLabel } from "@/utils/getTypeLabel";

export interface FileCategory {
  key: ProjectType;
  title: string;
  subTitle: string;
  icon: string;
  allowedExtensions: string[];
}

type FileCategoryDefinition = Omit<FileCategory, "key">;

const fileCategoriesByProjectType: Record<ProjectType, FileCategoryDefinition> =
  {
    rfp: {
      title: "Documents",
      subTitle: "RFP (PDF)",
      icon: documentIcon,
      allowedExtensions: ["PDF"],
    },
    source_code: {
      title: "Code Base",
      subTitle: "Sourcecode",
      icon: codeIcon,
      allowedExtensions: ["ZIP"],
    },
    additional_rfp: {
      title: "Additional Documents",
      subTitle: "Additional RFP files",
      icon: documentIcon,
      allowedExtensions: ["PDF", "DOCX", "DOC", "XLSX", "TXT"],
    },
    meeting_notes: {
      title: "Meeting Notes",
      subTitle: "Discussion notes and MoM",
      icon: documentIcon,
      allowedExtensions: ["DOCX", "DOC", "TXT", "PDF"],
    },
    requirement_update: {
      title: "Requirement Update",
      subTitle: "Updated requirement documents",
      icon: documentIcon,
      allowedExtensions: ["DOCX", "DOC", "PDF", "TXT", "XLSX"],
    },
  };

export const statusOptions: DropdownOption[] = [
  { label: "All", value: "" },
  { label: "Uploaded", value: "uploaded" },
  { label: "Processing", value: "processing" },
  { label: "Completed", value: "completed" },
  { label: "Failed", value: "failed" },
];

export const fileTypeOptions: DropdownOption[] = [
  { label: "All", value: "" },
  { label: "PDF", value: "PDF" },
  { label: "DOCX", value: "DOCX" },
  { label: "DOC", value: "DOC" },
  { label: "XLSX", value: "XLSX" },
  { label: "TXT", value: "TXT" },
  { label: "PNG", value: "PNG" },
  { label: "JPG", value: "JPG" },
  { label: "JPEG", value: "JPEG" },
  { label: "ZIP", value: "ZIP" },
];

export const fileCategories: FileCategory[] = (
  Object.entries(fileCategoriesByProjectType) as Array<
    [ProjectType, FileCategoryDefinition]
  >
).map(([key, category]) => ({
  key,
  ...category,
}));

const INCREMENTAL_UPDATE_TYPE_VALUES = [
  "meeting_notes",
  "requirement_update",
  "additional_rfp",
] as const;

// Labels are derived from getTypeLabel so this dropdown always matches how
// the same source_type renders in the Sources/Pipelines tables' Type column.
export const incrementalUpdateTypes: DropdownOption[] =
  INCREMENTAL_UPDATE_TYPE_VALUES.map((value) => ({
    label: getTypeLabel(value),
    value,
  }));

export const contextModeOptions: { label: string; value: "full" | "subset" }[] =
  [
    { label: "Full", value: "full" },
    { label: "Subset", value: "subset" },
  ];
