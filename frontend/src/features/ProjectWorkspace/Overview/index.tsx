import { useNavigate, useParams } from "react-router-dom";
import Button from "@/components/common/Button/Button";
import { useGetRequirementsSummaryQuery } from "@/services/api/modules/user-stories";
import { useGetProjectQuery } from "@/services/api/modules/projects";
import { PROJECT_TABS, type ProjectTabKey } from "@/constants/projectTabs";
import { Panel } from "./Panel";
import {
  calculateApprovedPercentage,
  calculatePendingJiraSync,
  calculatePendingTapSync,
} from "@/utils/projectMetrics";
import { ArrowNarrowRight } from "@/assets/icons/arrow/ArrowNarrowRight";

export default function Overview() {
  const { id: projectId } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const { data: projectData } = useGetProjectQuery(projectId ?? "", {
    skip: !projectId,
  });
  const project = projectData?.data;

  const { data: summaryData, isLoading: isSummaryLoading } =
    useGetRequirementsSummaryQuery(projectId ?? "", { skip: !projectId });
  const summary = summaryData?.data;

  const approvedPct = calculateApprovedPercentage(project);
  const pendingJiraSync = calculatePendingJiraSync(project);
  const pendingTapSync = calculatePendingTapSync(project);

  const hasRunning = project?.status === "running";
  const hasNoFiles = (project?.files ?? 0) === 0;

  const onGoTab = (key: ProjectTabKey) => {
    if (!projectId) return;
    const tab = PROJECT_TABS.find((t) => t.key === key);
    if (!tab) return;
    navigate(tab.buildPath(projectId));
  };

  const totalStories = summary?.total_user_stories ?? 0;
  const approvedCount = summary?.approved_count ?? 0;
  const pendingApprovalCount = Math.max(totalStories - approvedCount, 0);

  const kpis = [
    {
      ov: "Modules",
      big: String(summary?.total_modules ?? 0),
      s: `${summary?.total_features ?? 0} features`,
      tone: undefined,
      isLoading: isSummaryLoading,
    },
    {
      ov: "Total Requirements",
      big: String(totalStories),
      s: `${pendingApprovalCount} pending approval`,
      tone: undefined,
      isLoading: isSummaryLoading,
    },
    {
      ov: "Approved Requirements",
      big: String(approvedCount),
      s: `${approvedPct}% approved out of ${totalStories} requirements`,
      tone: "var(--success)",
      isLoading: isSummaryLoading,
    },
    {
      ov: "Pending sync (TAP)",
      big: pendingTapSync != null ? String(pendingTapSync) : "—",
      s: approvedCount > 0 ? "ready to release" : "nothing to sync",
      tone: "var(--warn)",
    },
    {
      ov: "Pending sync (JIRA)",
      big: pendingJiraSync != null ? String(pendingJiraSync) : "—",
      s: approvedCount > 0 ? "ready to release" : "nothing to sync",
      tone: "var(--warn)",
    },
  ];

  return (
    <>
      {hasNoFiles || hasRunning ? (
        <div className="w-full">
          <Panel>
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="font-bold">
                  {hasRunning
                    ? "Pipeline processing..."
                    : "No requirements yet"}
                </div>
                <div className="mt-0.5 text-[12.5px] text-[var(--sec)]">
                  {hasRunning
                    ? "The governed pipeline is running for this project. Requirements, review and sync unlock once it's ready."
                    : "Ingest a source folder (or an RFP PDF) to start the governed pipeline."}
                </div>
              </div>
              <Button
                size="sm"
                iconTrailing={<ArrowNarrowRight className="w-3.5 h-3.5" />}
                onClick={() => onGoTab(hasRunning ? "pipelines" : "sources")}
              >
                {hasRunning ? "View pipeline" : "Go to Sources"}
              </Button>
            </div>
          </Panel>
          <div className="text-xs text-[var(--mut)]">
            Project: {project?.name ?? "-"}
          </div>
        </div>
      ) : (
        <div className="w-full">
          <div className="mb-[18px] grid grid-cols-2 gap-4 md:grid-cols-4 lg:grid-cols-5">
            {kpis.map((k) => (
              <div
                key={k.ov}
                className="rounded-lg border border-[var(--border-primary)] bg-white p-4 shadow-[0_1px_3px_rgba(16,24,40,0.1),0_1px_2px_rgba(16,24,40,0.06)]"
              >
                <div className="text-[10.5px] font-bold uppercase tracking-wide text-[var(--mut)]">
                  {k.ov}
                </div>
                <div
                  className="my-1 text-[26px] font-bold text-[var(--text-primary)]"
                  style={k.tone ? { color: k.tone } : undefined}
                >
                  {k.isLoading ? "—" : k.big}
                </div>
                <div className="text-xs text-[var(--sec)]">{k.s}</div>
              </div>
            ))}
          </div>
          {project?.user_stories && project.user_stories > 0 ? (
            <Panel>
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="font-bold">Approved requirements ready</div>
                  <div className="mt-0.5 text-[12.5px] text-[var(--sec)]">
                    {approvedCount} approved user stories are ready to broadcast
                    downstream (Jira / TAP) or export. Use <b>Export</b> /{" "}
                    <b>Sync</b> in the top bar.
                  </div>
                </div>
                <Button
                  size="sm"
                  variant="link"
                  iconTrailing={<ArrowNarrowRight className="w-3.5 h-3.5" />}
                  onClick={() => onGoTab("review")}
                >
                  Review stories
                </Button>
              </div>
            </Panel>
          ) : null}
          <div className="mt-2 text-xs text-[var(--mut)]">
            Active project: {project?.name ?? "—"}
          </div>
        </div>
      )}
    </>
  );
}
