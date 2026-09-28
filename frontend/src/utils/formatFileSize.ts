/**
 * Formats a file size in bytes to a human-readable string.
 * Examples: 500 -> "500 B", 1536 -> "1.5 KB", 3145728 -> "3.0 MB"
 */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
