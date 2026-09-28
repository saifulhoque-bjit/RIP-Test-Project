import type { Dispatch, SetStateAction } from "react";
import type { RequirementScreen } from "@/types/user-story";
import { resolveSourceFileUrl } from "@/utils/resolveSourceFileUrl";

function getStringByLineRange(
  content: string,
  startLine: number,
  endLine: number,
): string {
  const lines = content.replace(/\r\n/g, "\n").split("\n");
  const normalizedStart = Math.max(1, Math.trunc(startLine || 1));
  const normalizedEnd = Math.max(normalizedStart, Math.trunc(endLine || 0));
  const safeEnd = Math.min(normalizedEnd, lines.length);

  if (normalizedStart > lines.length || safeEnd < normalizedStart) {
    return "";
  }

  return lines.slice(normalizedStart - 1, safeEnd).join("\n");
}

interface DisplayAsciiLayoutParams {
  screens?: RequirementScreen[];
  setAsciiLayoutList: Dispatch<SetStateAction<string[]>>;
  setAsciiLayoutError: Dispatch<SetStateAction<string>>;
  setIsAsciiLayoutLoading: Dispatch<SetStateAction<boolean>>;
}

export async function displayAsciiLayout({
  screens,
  setAsciiLayoutList,
  setAsciiLayoutError,
  setIsAsciiLayoutLoading,
}: DisplayAsciiLayoutParams): Promise<void> {
  const availableScreens = screens ?? [];

  if (availableScreens.length === 0) {
    setAsciiLayoutList([]);
    setAsciiLayoutError("No screen metadata available.");
    return;
  }

  const markdownScreens = availableScreens.filter((screen) =>
    screen.ascii_layout_ref?.srs_file?.trim().toLowerCase().endsWith(".md"),
  );

  if (markdownScreens.length === 0) {
    setAsciiLayoutList([]);
    setAsciiLayoutError("No markdown screen layout references found.");
    return;
  }

  setIsAsciiLayoutLoading(true);
  setAsciiLayoutError("");

  try {
    const extractedLayouts = await Promise.all(
      markdownScreens.map(async (screen) => {
        try {
          const storageKey = screen.ascii_layout_ref?.storage_key?.trim();
          if (!storageKey) return "";

          const fileUrl = resolveSourceFileUrl({
            sourceFilePath: storageKey,
          });
          if (!fileUrl) return "";

          const response = await fetch(fileUrl, {
            headers: {
              Accept: "text/markdown,text/plain,*/*",
            },
          });

          if (!response.ok) return "";

          const markdownText = (await response.text()).replace(/^\uFEFF/, "");
          if (!markdownText) return "";

          return getStringByLineRange(
            markdownText,
            screen.ascii_layout_ref.start_line,
            screen.ascii_layout_ref.end_line,
          );
        } catch {
          return "";
        }
      }),
    );

    const nonEmptyLayouts = extractedLayouts.filter(
      (layout) => layout.trim().length > 0,
    );

    setAsciiLayoutList(nonEmptyLayouts);

    if (nonEmptyLayouts.length === 0) {
      setAsciiLayoutError(
        "Unable to read any ASCII layout content from the configured line ranges.",
      );
    }
  } finally {
    setIsAsciiLayoutLoading(false);
  }
}