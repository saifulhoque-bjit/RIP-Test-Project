import { useMemo, useState } from "react";
import { toast } from "@/lib/toast";
import { Dropdown } from "@/components/common/Dropdown";
import { RightDrawerLayout } from "@/components/common/RightDrawerLayout";
import { cn } from "@/lib/cn";
import type { Project, UserProjectInfo } from "@/types";

const ROLE_OPTIONS = [{ label: "Member", value: "member" }];
const DEFAULT_ROLE = "member";
const KNOWN_ROLES = new Set(ROLE_OPTIONS.map((r) => r.value));

const initialRole = (roles: string[] | null | undefined) =>
  (roles ?? []).find((r) => KNOWN_ROLES.has(r)) ?? DEFAULT_ROLE;

export interface ProjectAssignment {
  projectId: string;
  projectName: string;
  role: string;
}

export function AssignDrawer({
  open,
  userName,
  allProjects,
  assignedProjects,
  onClose,
  onSave,
}: {
  open: boolean;
  userName: string;
  /** Every project belonging to the active tenant — the full candidate list. */
  allProjects: Project[];
  /** The subset already assigned to this user, each with its own role. */
  assignedProjects: UserProjectInfo[];
  onClose: () => void;
  /** Receives only the currently-checked projects, each with its own role. */
  onSave?: (assignments: ProjectAssignment[]) => void | Promise<unknown>;
}) {
  const assignedById = useMemo(
    () => new Map(assignedProjects.map((p) => [p.id, p])),
    [assignedProjects],
  );

  // Every tenant project is listed; only the ones already on this user's
  // profile start checked.
  const [assigned, setAssigned] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(allProjects.map((p) => [p.id, assignedById.has(p.id)])),
  );
  const [roleByProject, setRoleByProject] = useState<Record<string, string>>(
    () =>
      Object.fromEntries(
        allProjects.map((p) => [
          p.id,
          initialRole(assignedById.get(p.id)?.roles),
        ]),
      ),
  );
  const [isSaving, setIsSaving] = useState(false);

  const handleSave = async () => {
    const assignments: ProjectAssignment[] = allProjects
      .filter((p) => assigned[p.id])
      .map((p) => ({
        projectId: p.id,
        projectName: p.name,
        role: roleByProject[p.id] ?? DEFAULT_ROLE,
      }));

    try {
      setIsSaving(true);
      await onSave?.(assignments);
      onClose();
      toast.success("Project assignments saved.");
    } catch {
      // Error toast is already shown by the shared RTK Query error handler.
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <RightDrawerLayout
      isOpen={open}
      onClose={onClose}
      title={`Manage access — ${userName}`}
      secondaryActionLabel="Cancel"
      primaryActionLabel="Save"
      primaryActionDisabled={isSaving}
      primaryActionLoading={isSaving}
      onSecondaryAction={onClose}
      onPrimaryAction={() => void handleSave()}
    >
      <div className="mb-1.5 text-[11px] font-bold uppercase tracking-wide text-mut">
        Assigned projects
      </div>
      {allProjects.length === 0 ? (
        <div className="text-[12px] text-mut">
          This tenant doesn't have any projects yet.
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          {allProjects.map((p) => {
            const on = !!assigned[p.id];
            return (
              <div key={p.id} className="flex gap-2 justify-between">
                <div className="flex flex-1 min-w-0 gap-2 items-center justify-between rounded-lg border box-border border-[var(--border-strong)] px-3.5 py-2.5">
                  <div
                    className="text-[13px] font-semibold truncate min-w-0"
                    title={p.name}
                  >
                    {p.name}
                  </div>
                  <button
                    onClick={() =>
                      setAssigned((a) => ({ ...a, [p.id]: !on }))
                    }
                    className={cn(
                      "grid h-[18px] w-[18px] shrink-0 place-items-center rounded-[5px] border text-[11px]",
                      on
                        ? "border-accent bg-accent text-white"
                        : "border-border-strong",
                    )}
                  >
                    {on ? "✓" : ""}
                  </button>
                </div>
                <Dropdown
                  disabled={!on}
                  selected={roleByProject[p.id] ?? DEFAULT_ROLE}
                  onChange={(opt) =>
                    setRoleByProject((m) => ({ ...m, [p.id]: opt.value }))
                  }
                  options={ROLE_OPTIONS}
                />
              </div>
            );
          })}
        </div>
      )}
    </RightDrawerLayout>
  );
}
