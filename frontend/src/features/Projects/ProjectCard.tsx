import { useNavigate } from "react-router-dom";
import { USER_ROLE, type Project } from "@/types";
import { Chip } from "@/components/common/Chip";
import { formatDate } from "@/utils/formatDate";
import { cn } from "@/lib/utils";
import { PERMISSION } from "@/constants/permissions";
import { useHasPermission } from "@/hooks/usePermission";
import { hasRole } from "@/utils/hasRole";
import {
  calculateApprovedPercentage,
  calculatePendingJiraSync,
  calculatePendingTapSync,
} from "@/utils/projectMetrics";

interface ProjectCardProps {
  project: Project;
}

export default function ProjectCard({ project }: ProjectCardProps) {
  const navigate = useNavigate();
  const approvedPct = calculateApprovedPercentage(project);
  const approvedCount = project.approved_user_stories ?? 0;
  const pendingJiraSync = calculatePendingJiraSync(project);
  const pendingTapSync = calculatePendingTapSync(project);
  const lastActivity = project.updated_at
    ? formatDate(String(project.updated_at), false)
    : "—";

  // Permissions
  const projectDetailsView =
    useHasPermission([PERMISSION.PROJECT_VIEW]) &&
    !hasRole(USER_ROLE.SUPER_ADMIN);

  return (
    <button
      onClick={() => {
        if (projectDetailsView) {
          localStorage.setItem("rip_current_project_id", project.id);
          navigate(`/projects/${project.id}/overview`);
        }
      }}
      className={cn(
        "rounded-lg border border-[var(--border-primary)] bg-white p-[18px] text-left shadow-[0_1px_3px_rgba(16,24,40,0.1),0_1px_2px_rgba(16,24,40,0.06)] transition-all",
        projectDetailsView
          ? "cursor-pointer hover:border-[var(--accent)] hover:shadow-[0_4px_12px_rgba(16,24,40,0.12)]"
          : "cursor-default",
      )}
    >
      <div className="text-[15px] truncate font-bold text-[var(--text-primary)]">
        {project.name}
      </div>

      <div className="my-2 flex flex-wrap items-center gap-1.5">
        {/* Chips row */}
        <div className="my-2 flex flex-wrap items-center gap-1.5">
          {project.project_type === "rfp" ? (
            <span className="inline-flex items-center h-[22px] text-[10.5px] font-semibold px-2.5 rounded-full bg-[var(--info-50)] text-[var(--info)]">
              RFP
            </span>
          ) : project.project_type === "source_code" ? (
            <span className="inline-flex items-center h-[22px] text-[10.5px] font-semibold px-2.5 rounded-full bg-[var(--ai-50)] text-[var(--ai-draft)]">
              Source · Code
            </span>
          ) : null}
        </div>
        {project.status === "running" ? (
          <Chip tone="warn" dot>
            Processing
          </Chip>
        ) : project.status === "not_started" ? (
          <Chip tone="off" dot>
            Not started
          </Chip>
        ) : (
          <Chip tone="ok" dot>
            Active
          </Chip>
        )}
      </div>

      {/* Bottom metrics */}
      <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-3 border-t border-[var(--border-primary)] pt-3 sm:grid-cols-4">
        <Metric
          label="Approved"
          value={
            approvedPct != null ? `${approvedCount} (${approvedPct}%)` : "—"
          }
          tone={approvedPct != null ? "text-[var(--success)]" : ""}
        />
        <Metric
          label="Pending Jira"
          value={pendingJiraSync != null ? String(pendingJiraSync) : "—"}
          tone={pendingJiraSync ? "text-[var(--warning)]" : ""}
        />
        <Metric
          label="Pending TAP"
          value={pendingTapSync != null ? String(pendingTapSync) : "—"}
          tone={pendingTapSync ? "text-[var(--warning)]" : ""}
        />
        <Metric label="Last activity" value={lastActivity} />
      </div>
    </button>
  );
}

function Metric({
  label,
  value,
  tone = "",
}: {
  label: string;
  value: string;
  tone?: string;
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-[var(--text-quaternary)]">
        {label}
      </div>
      <div
        className={`mt-0.5 text-sm font-bold ${tone || "text-[var(--text-primary)]"}`}
      >
        {value}
      </div>
    </div>
  );
}
