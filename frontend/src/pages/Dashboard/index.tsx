import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { PageHeaderPortal } from "@/components/common/PageHeaderPortal";
import {
  useGetProjectsQuery,
  useGetAllProjectsQuery,
} from "@/services/api/modules/projects";
import { useGetDashboardStatsQuery } from "@/services/api/modules/dashboard";
import { useGetTenantStatsQuery } from "@/services/api/modules/tenants";
import { useGetPipelinesQuery } from "@/services/api/modules/pipeline";
import Card from "@/components/common/Card";
import { InfoNote } from "@/components/common/Note";
import { Pagination } from "@/components/common/Pagination";
import { Table } from "@/components/common/Table";
import { createProjectListColumns } from "@/features/Dashboard/dashboardProjectColumns";
import {
  createClientColumns,
  toClientRows,
} from "@/features/Dashboard/dashboardClientColumns";
import { useActiveTenant } from "@/hooks/useActiveTenant";
import { USER_ROLE } from "@/types/auth";
import type { Project } from "@/types";
import { hasRole } from "@/utils/hasRole";

type KpiCard = {
  ov: string;
  big: string | number;
  s: string;
  tone?: string;
};

const PROJECTS_PAGE_LIMIT = 10;

export default function DashboardPage() {
  const navigate = useNavigate();

  const {
    activeTenant,
    tenants,
    isLoading: isClientsLoading,
    hasError: hasClientsError,
  } = useActiveTenant();
  // super_admin sees the client list instead of a project table (below);
  // tenant admin sees every project on their own tenant; members only see
  // their own assigned projects, scoped server-side.
  const isSuperAdmin = hasRole(USER_ROLE.SUPER_ADMIN);
  const isTenantAdmin = hasRole(USER_ROLE.CLIENT_ADMIN);
  const seesAllTenantProjects = isSuperAdmin || isTenantAdmin;
  const activeTenantId = activeTenant?.id ?? null;

  const [projectsSkip, setProjectsSkip] = useState(0);
  const allProjectsResult = useGetAllProjectsQuery(
    {
      skip: projectsSkip,
      limit: PROJECTS_PAGE_LIMIT,
      tenant_id: activeTenantId ?? undefined,
    },
    { skip: !isTenantAdmin || !activeTenantId },
  );
  const ownProjectsResult = useGetProjectsQuery(
    { skip: projectsSkip, limit: PROJECTS_PAGE_LIMIT },
    { skip: seesAllTenantProjects },
  );
  const { data: projectData } = isTenantAdmin
    ? allProjectsResult
    : ownProjectsResult;
  const { data: statsData, isLoading: isStatsLoading } =
    useGetDashboardStatsQuery(undefined, { skip: isSuperAdmin });
  const { data: tenantStatsData, isLoading: isTenantStatsLoading } =
    useGetTenantStatsQuery(undefined, { skip: !isSuperAdmin });
  const { data: pipelinesData } = useGetPipelinesQuery();

  const clientRows = useMemo(() => toClientRows(tenants), [tenants]);

  const mine: Project[] = projectData?.data?.items ?? [];
  const totalProjects = projectData?.data?.total ?? 0;
  const stats = statsData?.data;

  const runningProjectIds = new Set(
    (pipelinesData?.data?.items ?? [])
      .filter((run) => run.status === "running")
      .map((run) => run.project_id),
  );

  const projectRows = mine.map((p) => {
    const approved = p.approved_user_stories ?? 0;
    const totalStories = p.user_stories ?? 0;
    const jiraDiff = approved - (p.jira_synced_count ?? 0);
    const tapDiff = approved - (p.tap_synced_count ?? 0);
    const isAllSynced =
      totalStories > 0 &&
      totalStories === (p.jira_synced_count ?? 0) &&
      totalStories === (p.tap_synced_count ?? 0);

    return {
      ...p,
      isProcessing: runningProjectIds.has(p.id),
      notStarted: (p.files ?? 0) <= 0,
      approvedPct:
        approved > 0 && totalStories > 0
          ? Math.round((approved / totalStories) * 100)
          : null,
      pendingSync: Math.max(jiraDiff, tapDiff, 0),
      isAllSynced,
    };
  });

  const n = (value: number | undefined) =>
    isStatsLoading || value === undefined ? "—" : value;

  const approvedPct =
    !isStatsLoading && stats && stats.total_stories > 0
      ? Math.round((stats.approved_user_stories / stats.total_stories) * 100)
      : null;

  const tenantStats = tenantStatsData?.data;
  const nTenant = (value: number | undefined) =>
    isTenantStatsLoading || value === undefined ? "—" : value;

  // A super admin manages clients, not their own projects, so the cards read
  // as client counts by status rather than the per-project stats everyone
  // else sees.
  const kpis: KpiCard[] = isSuperAdmin
    ? [
        { ov: "Client", big: nTenant(tenantStats?.total_tenants), s: "" },
        { ov: "Active", big: nTenant(tenantStats?.active_tenants), s: "" },
        {
          ov: "Deactivated",
          big: nTenant(tenantStats?.deactivated_tenants),
          s: "",
        },
        {
          ov: "Client Admin Pending Invitation",
          big: nTenant(tenantStats?.pending_invitation_client_admin),
          s: "",
        },
        {
          ov: "Total Project",
          big: nTenant(tenantStats?.total_projects),
          s: "",
        },
      ]
    : [
        {
          ov: "My projects",
          big: n(stats?.total_projects),
          s: `${stats?.active_projects ?? 0} active`,
        },
        {
          ov: "Requirements",
          big: n(stats?.total_stories),
          s: "across projects",
        },
        {
          ov: "Approved",
          big: approvedPct === null ? "—" : `${approvedPct}%`,
          s: "portfolio average",
          tone: "text-[var(--color-success)]",
        },
        {
          ov: "Pending sync (TAP)",
          big: n(stats?.pending_tap_sync_count),
          s: "awaiting release",
          tone: "text-[var(--color-warn)]",
        },
        {
          ov: "Pending sync (JIRA)",
          big: n(stats?.pending_jira_sync_count),
          s: "awaiting release",
          tone: "text-[var(--color-warn)]",
        },
      ];

  const open = (id: string) => {
    navigate(`/projects/${id}/overview`);
  };

  const projectColumns = createProjectListColumns({ showOpenAction: true });
  const clientColumns = createClientColumns();

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="min-h-full">
      <PageHeaderPortal>
        <div className="shrink-0 border-b border-[var(--border-primary)] bg-[#eef1f6] px-6 py-4">
          {activeTenant?.name && (
            <div className="mb-1.5 text-xs text-[var(--text-quaternary)]">
              {activeTenant.name}
            </div>
          )}
          <div className="flex items-center gap-2.5">
            <h2 className="text-[19px] font-semibold text-[var(--text-primary)] m-0">
              Dashboard
            </h2>
          </div>
        </div>
      </PageHeaderPortal>

      <InfoNote className="mb-4">
        The Dashboard is the <b>only cross-project surface</b> — a rollup across
        the projects
        <i> you</i> are assigned to. All operational work happens inside a
        project.
      </InfoNote>

      {/* Stat Cards */}
      <div className="mb-[18px] grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-5">
        {kpis.map((k) => (
          <Card
            key={k.ov}
            className="rounded-lg border border-border bg-surface p-4 shadow-e1"
          >
            <div className="text-[10.5px] font-bold uppercase tracking-wide text-mut">
              {k.ov}
            </div>
            <div className={`my-1 text-[26px] font-bold ${k.tone ?? ""}`}>
              {k.big}
            </div>
            <div className="text-xs text-sec">{k.s}</div>
          </Card>
        ))}
      </div>

      {isSuperAdmin ? (
        <Table
          title="Clients"
          columns={clientColumns}
          data={isClientsLoading || hasClientsError ? [] : clientRows}
          emptyMessage={
            isClientsLoading
              ? "Loading clients…"
              : hasClientsError
                ? "Failed to load clients."
                : "No clients yet."
          }
          className="mb-3"
        />
      ) : (
        <>
          <Table
            title="My projects"
            actionHelperText={activeTenant?.name}
            columns={projectColumns}
            data={projectRows}
            onRowClick={(row) => open(row.id)}
            isClickable
            emptyMessage="No projects found"
            className="mb-3"
          />

          {totalProjects > PROJECTS_PAGE_LIMIT && (
            <Card className="rounded-lg border border-border bg-surface shadow-e1">
              <Pagination
                skip={projectsSkip}
                limit={PROJECTS_PAGE_LIMIT}
                total={totalProjects}
                onSkipChange={setProjectsSkip}
                itemLabel="projects"
              />
            </Card>
          )}
        </>
      )}
    </div>
  );
}
