/**
 * Formats a duration in seconds into a human-readable string.
 * @param seconds - The duration in seconds.
 * @returns A formatted string representing the duration.
 */
export function formatDuration(seconds: number | null): string {
  if (!seconds) return "—";

  const units = [
    { label: "d", value: 86400 },
    { label: "h", value: 3600 },
    { label: "m", value: 60 },
  ];

  for (let i = 0; i < units.length; i++) {
    const primary = Math.floor(seconds / units[i].value);
    if (primary > 0) {
      const next = units[i + 1];
      const secondary = next
        ? Math.floor((seconds % units[i].value) / next.value)
        : 0;
      return `~${primary}${units[i].label}${secondary > 0 ? ` ${secondary}${next.label}` : ""}`;
    }
  }

  return "~0m";
}