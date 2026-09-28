import type { ProjectType } from "@/types";
import { fileCategories } from "./staticData";

const INCREMENTAL_ALLOWED_EXTENSIONS = [
  "jpg",
  "jpeg",
  "png",
  "txt",
  "pdf",
  "doc",
  "docx",
];

interface GetAllowedExtensionsParams {
  incrementalProcess: boolean;
  selectedCategory: ProjectType | null;
}

export function getAllowedExtensions({
  incrementalProcess,
  selectedCategory,
}: GetAllowedExtensionsParams): string[] | undefined {
  // Source-code projects only ever ingest an archive, even on a follow-up
  // ("incremental") upload — the RFP-oriented incremental extension list
  // (docs/images) must not leak in just because files > 0.
  if (selectedCategory === "source_code") {
    return fileCategories
      .find((cat) => cat.key === "source_code")
      ?.allowedExtensions.map((ext) => ext.toLowerCase());
  }

  if (incrementalProcess) {
    return INCREMENTAL_ALLOWED_EXTENSIONS;
  }

  if (!selectedCategory) {
    return undefined;
  }

  return fileCategories
    .find((cat) => cat.key === selectedCategory)
    ?.allowedExtensions.map((ext) => ext.toLowerCase());
}
