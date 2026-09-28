import { type ReactNode, useState, useRef, useEffect } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { toast } from "@/lib/toast";
import type { Project } from "@/types/project";
import { PROJECT_TABS, type ProjectTabKey } from "@/constants/projectTabs";
import { useGetProjectNameListQuery } from "@/services/api/modules/projects";
import { cn } from "@/lib/utils";
import Button from "@/components/common/Button/Button";
import Loader from "@/components/common/Loader";
import { ArrowNarrowRight } from "@/assets/icons/arrow/ArrowNarrowRight";

interface SubHeaderProps {
  project: Project;
  actions?: ReactNode;
  tabs?: ReactNode;
}

export default function SubHeader({ project, actions, tabs }: SubHeaderProps) {
  const navigate = useNavigate();
  const location = useLocation();

  // Derive active tab from current URL
  const activeTab: ProjectTabKey | null =
    PROJECT_TABS.find((t) => location.pathname === t.buildPath(project.id))
      ?.key ?? null;

  return (
    <section className="relative z-20 shrink-0 overflow-visible border-b border-[var(--border-primary)] bg-[#eef1f6] px-6 py-4">
      {/* Crumb */}
      <div className="mb-1.5 py-1 text-xs text-[var(--text-quaternary)] flex justify-start items-center">
        <Button
          type="button"
          variant="link"
          size="xs"
          iconLeading={<ArrowNarrowRight className="w-3.5 h-3.5 rotate-180" />}
          onClick={() => navigate("/projects")}
        >
          All projects
        </Button>
        <span>&nbsp;·&nbsp;{project?.name}</span>
      </div>

      {/* Title + actions */}
      <div className="flex items-center gap-2.5">
        <ProjectSwitcher project={project} activeTab={activeTab} />
        {actions && <div className="ml-auto flex gap-2.5">{actions}</div>}
      </div>

      {/* Tabs */}
      {tabs ?? (
        <div className="mt-[14px] flex gap-[22px]">
          {PROJECT_TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              onClick={() => navigate(tab.buildPath(project.id))}
              className={cn(
                "border-b-2 bg-transparent px-0.5 py-2 text-[13px] font-semibold cursor-pointer",
                activeTab === tab.key
                  ? "border-[var(--accent)] text-[var(--accent)]"
                  : "border-transparent text-[var(--text-quaternary)] hover:text-[var(--text-secondary)]",
              )}
            >
              {tab.label}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// ProjectSwitcher — dropdown to switch between projects, stays on current tab
// ---------------------------------------------------------------------------

interface ProjectSwitcherProps {
  project: Project;
  activeTab: ProjectTabKey | null;
}

function ProjectSwitcher({ project, activeTab }: ProjectSwitcherProps) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // `skip: !open` matters more here than it looks: this list tags every
  // project id it returns (not just LIST), so ANY single project's
  // { type: "Project", id } invalidation refetches the whole list — story
  // approvals, terminal WS tasks, TAP acks. Holding a subscription while the
  // dropdown is shut means paying for all of it to render nothing. Unsubscribed,
  // RTK Query drops the cache entry on invalidation instead of refetching it,
  // and opening the dropdown fetches a fresh list — which is what you want at
  // that moment anyway. Same pattern as SyncTray / ExportModal.
  // GET /projects/list is unpaginated and returns just id/name/files, which is
  // exactly what this dropdown renders; only `projects.slice(0, 5)` is shown.
  const { data, isLoading } = useGetProjectNameListQuery(undefined, {
    skip: !open,
  });
  const projects = data?.data ?? [];

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const switchTo = (id: string) => {
    localStorage.setItem("rip_current_project_id", id);
    const target = projects.find((p) => p.id === id);
    const targetName = target?.name ?? "Project";
    const isNotStarted = target?.files != null && target.files === 0;

    toast.info("Switched project", {
      body: `${targetName} — Requirements, Review, Settings, and Activity now act on this project.`,
      toastId: `project-switch-${id}`,
    });

    if (isNotStarted) {
      // Always land on overview — Overview's not_started branch renders
      // "No requirements yet / Go to Sources" automatically
      navigate(`/projects/${id}/overview`);
    } else {
      const tabDef = activeTab
        ? PROJECT_TABS.find((t) => t.key === activeTab)
        : null;
      navigate(tabDef ? tabDef.buildPath(id) : `/projects/${id}/overview`);
    }
    setOpen(false);
  };

  return (
    <div ref={ref} className="relative z-30">
      {/* Trigger */}
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="inline-flex w-[250px] items-center justify-between gap-1.5 rounded-md border border-border-strong bg-white px-2.5 py-1 text-[19px] font-semibold hover:bg-[#f7f9fc] cursor-pointer"
      >
        <span className="min-w-0 truncate">{project.name}</span>
        <svg
          className={cn(
            "h-3.5 w-3.5 shrink-0 text-mut transition-transform duration-150",
            open && "rotate-180",
          )}
          viewBox="0 0 16 16"
          fill="currentColor"
          aria-hidden="true"
        >
          <path d="M8 10.5 3.5 5.5h9L8 10.5Z" />
        </svg>
      </button>

      {/* Dropdown */}
      {open && (
        <div className="absolute left-0 top-[42px] z-50 w-[250px] overflow-hidden rounded-[10px] border border-border bg-white shadow-e3">
          <div className="max-h-64 overflow-y-auto">
            {/* The list is fetched on open, so "no projects" is only true
                once the request has actually settled. */}
            {isLoading && (
              <div className="px-4 py-4">
                <Loader height={28} width={28} ariaLabel="Loading projects" />
              </div>
            )}
            {!isLoading && projects.length === 0 && (
              <div className="px-4 py-3 text-sm text-[var(--text-quaternary)]">
                No projects found
              </div>
            )}
            {projects.slice(0, 5).map((p) => {
              const isCurrent = p.id === project.id;
              return (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => switchTo(p.id)}
                  className={cn(
                    "flex w-full items-start justify-between border-b border-[var(--border-primary)] px-3 py-2.5 text-[13px] text-[var(--text-primary)] transition-colors hover:bg-canvas cursor-pointer border-x-0 border-t-0 bg-transparent",
                    isCurrent ? "bg-accent-50" : "",
                  )}
                >
                  <span className="min-w-0 truncate">{p.name}</span>
                  {p.files != null && p.files === 0 && (
                    <span className="ml-auto shrink-0 inline-flex items-center gap-1 rounded-full bg-[var(--surface-muted,#f3f4f6)] px-2 py-0.5 text-[11px] font-medium text-[var(--text-quaternary)]">
                      <span className="h-1.5 w-1.5 rounded-full bg-[var(--text-quaternary)]" />
                      Not started
                    </span>
                  )}
                </button>
              );
            })}
          </div>
          <div className="px-3 py-2.5">
            <button
              type="button"
              onClick={() => {
                navigate("/projects");
                setOpen(false);
              }}
              className="text-[13px] font-semibold text-accent bg-transparent border-none cursor-pointer"
            >
              View all projects →
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
