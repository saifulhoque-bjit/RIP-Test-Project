export type SyncGroup = "new" | "changed" | "deprecated";

export function calculateSyncGroup({
  version,
  deletedAt,
}: {
  version: number;
  deletedAt?: string | null;
}): SyncGroup {
  if (deletedAt) return "deprecated";
  if (version === 1) return "new";
  return "changed";
}
