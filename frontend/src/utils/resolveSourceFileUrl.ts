export interface ResolveSourceFileUrlParams {
  fileUrl?: string | null;
  sourceFilePath?: string | null;
  cdnBase?: string | null;
}

const defaultCdnBase =
  ((import.meta.env.VITE_CDN_URL as string | undefined) ?? "").trim();

export function resolveSourceFileUrl({
  fileUrl,
  sourceFilePath,
  cdnBase,
}: ResolveSourceFileUrlParams): string {
  const explicitUrl = fileUrl?.trim();
  if (explicitUrl) return explicitUrl;

  const sourcePath = sourceFilePath?.trim();
  if (!sourcePath) return "";

  if (/^https?:\/\//i.test(sourcePath)) return sourcePath;

  const base = (cdnBase ?? defaultCdnBase).trim();
  if (!base) return sourcePath;

  const baseHasSlash = base.endsWith("/");
  const pathHasSlash = sourcePath.startsWith("/");

  if (baseHasSlash && pathHasSlash) return `${base}${sourcePath.slice(1)}`;
  if (!baseHasSlash && !pathHasSlash) return `${base}/${sourcePath}`;

  return `${base}${sourcePath}`;
}