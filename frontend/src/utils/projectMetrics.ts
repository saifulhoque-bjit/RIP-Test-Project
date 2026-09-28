import type { Project } from "@/types";

export function calculateApprovedPercentage(
  project: Project | null | undefined,
): number | null {
  const totalUserStories = project?.user_stories;
  const approvedUserStories = project?.approved_user_stories;

  if (
    totalUserStories == null ||
    approvedUserStories == null ||
    totalUserStories <= 0
  ) {
    return 0;
  }

  const pct = Math.round((approvedUserStories / totalUserStories) * 100);
  return Math.max(0, Math.min(100, pct));
}

export function calculatePendingJiraSync(
  project: Project | null | undefined,
): number | null {
  if (project?.pending_jira_sync == null) {
    return 0;
  }

  return project.pending_jira_sync;
}

export function calculatePendingTapSync(
  project: Project | null | undefined,
): number | null {
  if (project?.pending_tap_sync == null) {
    return 0;
  }

  return project.pending_tap_sync;

}
