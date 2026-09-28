/**
 * Extracts a readable error message from unknown error objects.
 */
export function getErrorMessage(error: unknown, fallback: string): string {
  if (typeof error === "object" && error !== null) {
    const messageFromData = (error as { data?: { message?: unknown } }).data
      ?.message;

    if (typeof messageFromData === "string" && messageFromData.trim()) {
      return messageFromData;
    }

    const message = (error as { message?: unknown }).message;
    if (typeof message === "string" && message.trim()) {
      return message;
    }
  }

  return fallback;
}
