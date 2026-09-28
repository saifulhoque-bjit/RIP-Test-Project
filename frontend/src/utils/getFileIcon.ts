// Outline icons
import pdfFileIcon from "@/assets/icons/file-icons/out-line-icons/pdf-file-icon.svg";
import jpgFileIcon from "@/assets/icons/file-icons/out-line-icons/jpg-file-icon.svg";
import jpegFileIcon from "@/assets/icons/file-icons/out-line-icons/jpeg-file-icon.svg";
import pngFileIcon from "@/assets/icons/file-icons/out-line-icons/png-file-icon.svg";
import mp4FileIcon from "@/assets/icons/file-icons/out-line-icons/mp4-file-icon.svg";
import mp3FileIcon from "@/assets/icons/file-icons/out-line-icons/mp3-file-icon.svg";
import docxFileIcon from "@/assets/icons/file-icons/out-line-icons/docx-file-icon.svg";
import docFileIcon from "@/assets/icons/file-icons/out-line-icons/doc-file-icon.svg";
import xlsxFileIcon from "@/assets/icons/file-icons/out-line-icons/xlsx-file-icon.svg";
import txtFileIcon from "@/assets/icons/file-icons/out-line-icons/txt-file-icon.svg";
import zipFileIcon from "@/assets/icons/file-icons/out-line-icons/zip-file-icon.svg";
import figFileIcon from "@/assets/icons/file-icons/out-line-icons/fig-file-icon.svg";
import aepFileIcon from "@/assets/icons/file-icons/out-line-icons/aep-file-icon.svg";
import documentIcon from "@/assets/icons/file-icons/file-categories/document-icon.svg";
import imageIcon from "@/assets/icons/file-icons/file-categories/image-icon.svg";
import codeIcon from "@/assets/icons/file-icons/file-categories/source-code-icon.svg";
import notesIcon from "@/assets/icons/file-icons/file-categories/meeting-notes-icon.svg";
// Solid icons
import pdfFileSolidIcon from "@/assets/icons/file-icons/solid-icons/pdf-icons.svg";
import imageSolidIcon from "@/assets/icons/file-icons/solid-icons/image-icon.svg";
import docFileSolidIcon from "@/assets/icons/file-icons/solid-icons/doc-icon.svg";
import xlsxFileSolidIcon from "@/assets/icons/file-icons/solid-icons/xlsx-icon.svg";
import mp3FileSolidIcon from "@/assets/icons/file-icons/solid-icons/mp3-icon.svg";
import mp4FileSolidIcon from "@/assets/icons/file-icons/solid-icons/mp4-icon.svg";
import sourceCodeSolidIcon from "@/assets/icons/file-icons/solid-icons/source-code-icon.svg";
import zipFileSolidIcon from "@/assets/icons/file-icons/solid-icons/zip-icon.svg";

const fileIconMap: Record<string, string> = {
  pdf: pdfFileIcon,
  jpg: jpgFileIcon,
  jpeg: jpegFileIcon,
  png: pngFileIcon,
  mp4: mp4FileIcon,
  mp3: mp3FileIcon,
  docx: docxFileIcon,
  doc: docFileIcon,
  xlsx: xlsxFileIcon,
  txt: txtFileIcon,
  zip: zipFileIcon,
  fig: figFileIcon,
  aep: aepFileIcon,
  document: documentIcon,
  image: imageIcon,
  "source-code": codeIcon,
  md: codeIcon,
  "meeting-notes": notesIcon,
};

const solidFileIconMap: Record<string, string> = {
  pdf: pdfFileSolidIcon,
  jpg: imageSolidIcon,
  jpeg: imageSolidIcon,
  png: imageSolidIcon,
  mp4: mp4FileSolidIcon,
  mp3: mp3FileSolidIcon,
  docx: docFileSolidIcon,
  doc: docFileSolidIcon,
  xlsx: xlsxFileSolidIcon,
  txt: docFileSolidIcon,
  zip: zipFileSolidIcon,
  fig: figFileIcon,
  aep: aepFileIcon,
  document: docFileSolidIcon,
  image: imageSolidIcon,
  "source-code": sourceCodeSolidIcon,
  md: sourceCodeSolidIcon,
  "meeting-notes": notesIcon,
};

export function getFileIcon(
  value: string,
  isSolidIcon: boolean = false,
): string {
  const normalized = value.trim().toLowerCase();
  if (!normalized) return documentIcon;

  const extension = normalized.includes(".")
    ? normalized.split(".").pop()
    : normalized;

  return (
    (extension &&
      (isSolidIcon ? solidFileIconMap[extension] : fileIconMap[extension])) ||
    fileIconMap[normalized] ||
    documentIcon
  );
}
