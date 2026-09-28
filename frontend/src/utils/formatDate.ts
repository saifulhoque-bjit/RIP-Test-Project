/**
 * Formats an ISO date string to a short readable format.
 * Example: "2026-04-21T09:57:08.967140+06:00" → "Apr 21, 2026"
 */
export function formatDate(dateString: string, includeTime: boolean = true): string {
  const date = new Date(dateString);
  const options: Intl.DateTimeFormatOptions = {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  };
  if (includeTime) {
    options.hour = '2-digit';
    options.minute = '2-digit';
    options.hour12 = true;
  }
  return date.toLocaleDateString('en-US', options);
}
