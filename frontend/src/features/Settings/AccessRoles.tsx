import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "@/lib/toast";
import Button from "@/components/common/Button/Button";
import { Chip } from "@/components/common/Chip";
import { Dropdown } from "@/components/common/Dropdown";
import Input from "@/components/common/Input";
import Modal from "@/components/common/Modal";
import { InfoNote } from "@/components/common/Note";
import { Pill } from "@/components/common/Pill";
import { PermissionGate } from "@/components/common/PermissionGate";
import { PERMISSION } from "@/constants/permissions";
import { Table, type TableColumn } from "@/components/common/Table";
import {
  useInviteUserMutation,
  useGetTenantInvitationsQuery,
  useResendInvitationMutation,
  useRevokeInvitationMutation,
} from "@/services/api/modules/tenants";
import {
  useAssignUserProjectsMutation,
  useDeleteUserMutation,
  useGetUsersQuery,
  // useUpdateUserRoleMutation,
  useUpdateUserStatusMutation,
} from "@/services/api/modules/users";
import { useGetProjectNameListQuery } from "@/services/api/modules/projects";
import { useAppDispatch, useAppSelector } from "@/store/hooks";
import { setActiveTenantId } from "@/store/slices/tenantSlice";
import { useHasPermission } from "@/hooks/usePermission";
import { useActiveTenant } from "@/hooks/useActiveTenant";
import { USER_ROLE } from "@/types/auth";
import { formatDate } from "@/utils/formatDate";
import type { UserProjectInfo, UserRoleInfo } from "@/types";
import { Panel } from "../ProjectWorkspace/Overview/Panel";
import { AssignDrawer, type ProjectAssignment } from "./AssignDrawer";
import { hasRole } from "@/utils/hasRole";
import { PlusIcon } from "@/assets/icons/PlusIcon";

export type Role = "super_admin" | "admin" | "member";

const ROLE_LABEL: Record<Role, string> = {
  super_admin: "Super Admin",
  admin: "Client Admin",
  member: "Member",
};
const ROLE_SCOPE_LABEL: Record<Role, string> = {
  super_admin: "Platform-wide",
  admin: "This client",
  member: "Assigned projects",
};
const ROLE_DESCRIPTION: Record<Role, string> = {
  super_admin: "Manages every client, provider, and users across the platform.",
  admin: "Manages users, providers, and projects for their own client.",
  member: "Views and works within the projects they're assigned to.",
};
const ROLE_PILL: Record<Role, "err" | "conf" | "neutral" | "warn"> = {
  super_admin: "err",
  admin: "conf",
  member: "neutral",
};
const ROLE_ORDER: Role[] = ["super_admin", "admin", "member"];

/** Roles assignable from the edit-role modal — not the full role set. */
// const EDITABLE_ROLES: Role[] = ["member"];

/** Which roles the inviter is allowed to assign, based on their own role. */
const ASSIGNABLE_ROLES_BY_INVITER: Record<Role, Role[]> = {
  super_admin: ["admin"],
  admin: ["member"],
  member: [],
};

interface User {
  [key: string]: unknown;
  id: string;
  name: string;
  email: string;
  /** Highest-priority role — used for the edit-role modal and local override. */
  role: Role;
  /** All of the user's real roles, for display and filtering. */
  roles: Role[];
  projectsLabel: string;
  projects: UserProjectInfo[];
  defaultStatus: Status;
}

const pickRoles = (roles: UserRoleInfo[] | null | undefined): Role[] => {
  const names = (roles ?? []).map((r) => r.name);
  const matched = ROLE_ORDER.filter((r) => names.includes(r));
  return matched.length ? matched : ["member"];
};

type Status = "active" | "suspended";

interface PendingInvite {
  [key: string]: unknown;
  id: string;
  email: string;
  expires_at: string;
  created_at: string;
}

export function AccessRoles() {
  const [drawer, setDrawer] = useState<User | null>(null);
  const [q, setQ] = useState("");
  const [roleFilter, setRoleFilter] = useState<Role | "all">("all");
  // const [editing, setEditing] = useState<{
  //   id: string;
  //   name: string;
  //   role: Role;
  // } | null>(null);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<{
    id: string;
    name: string;
  } | null>(null);

  const currentUser = useAppSelector((s) => s.auth.user);
  const isSuperAdmin = hasRole(USER_ROLE.SUPER_ADMIN);
  const canManageUsers = isSuperAdmin || hasRole(USER_ROLE.CLIENT_ADMIN);

  const { activeTenant, isLoading: isTenantLoading } = useActiveTenant();
  const activeTenantId = activeTenant?.id ?? null;
  const clientName = activeTenant?.name ?? "—";

  // Full candidate list for the assign-projects drawer — every project on
  // the active tenant, not just the ones already assigned to a given user.
  const { data: tenantProjectsResponse } = useGetProjectNameListQuery(
    { tenant_id: activeTenantId ?? undefined },
    { skip: !activeTenantId },
  );
  const tenantProjects = tenantProjectsResponse?.data ?? [];

  const canInviteUsers = useHasPermission(PERMISSION.USER_INVITE);

  // Hand-off from "create client → invite a Client Admin": the Clients tab
  // sends us here with ?invite=1. Consume the flag so a reload or a manual
  // tab switch doesn't reopen the modal.
  const [params, setParams] = useSearchParams();
  useEffect(() => {
    if (params.get("invite") !== "1") return;
    setParams({ tab: "access" }, { replace: true });
    if (canInviteUsers) setInviteOpen(true);
  }, [params, setParams, canInviteUsers]);

  const { data: invitesResponse } = useGetTenantInvitationsQuery(
    { tenantId: activeTenantId ?? "", statusFilter: "pending" },
    { skip: !activeTenantId || !canInviteUsers },
  );
  const invites: PendingInvite[] = (invitesResponse?.data?.items ?? []).map(
    (inv) => ({ ...inv }),
  );

  const [resendInvitation] = useResendInvitationMutation();
  const [revokeInvitation] = useRevokeInvitationMutation();

  const handleResendInvite = async (inv: PendingInvite) => {
    if (!activeTenantId) return;
    try {
      await resendInvitation({
        tenantId: activeTenantId,
        invitationId: inv.id,
      }).unwrap();
      toast.success(`A new invite email was sent to ${inv.email}.`);
    } catch {
      // Error toast is already shown by the shared RTK Query error handler.
    }
  };

  const handleRevokeInvite = async (inv: PendingInvite) => {
    if (!activeTenantId) return;
    try {
      await revokeInvitation({
        tenantId: activeTenantId,
        invitationId: inv.id,
      }).unwrap();
      toast.info(`Invite to ${inv.email} was revoked.`);
    } catch {
      // Error toast is already shown by the shared RTK Query error handler.
    }
  };

  const {
    data: usersResponse,
    isLoading,
    error,
  } = useGetUsersQuery(
    { tenant_id: activeTenantId ?? undefined },
    { skip: !activeTenantId },
  );
  const hasError = !!error;

  const users = useMemo<User[]>(
    () =>
      (usersResponse?.data ?? []).map((u) => {
        const roles = pickRoles(u.roles);
        const projects = u.projects ?? [];
        return {
          id: u.id,
          name: u.name,
          email: u.email,
          role: roles[0],
          roles,
          projectsLabel: projects.length ? `${projects.length}` : "—",
          projects,
          defaultStatus: u.is_active ? "active" : "suspended",
        };
      }),
    [usersResponse],
  );

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return users.filter((u) => {
      const matchesQ =
        !needle ||
        u.name.toLowerCase().includes(needle) ||
        u.email.toLowerCase().includes(needle);
      const matchesRole = roleFilter === "all" || u.roles.includes(roleFilter);
      return matchesQ && matchesRole;
    });
  }, [users, q, roleFilter]);

  // A Client Admin's user list is scoped to their own tenant, which can never
  // contain a Super Admin (a platform-wide role, not tied to any tenant) — so
  // offering it as a filter option is a dead choice that always empties the
  // table. Only a Super Admin, who can see every tenant's users, needs it.
  const filterableRoles = isSuperAdmin
    ? ROLE_ORDER
    : ROLE_ORDER.filter((r) => r !== USER_ROLE.SUPER_ADMIN);

  const goToClientsProviders = () => {
    setParams({ tab: "clients" }, { replace: true });
  };

  // Guards against a stale "Super Admin" filter surviving a role change (e.g.
  // a super_admin's session switching how it's scoped) and silently leaving
  // the table empty with no matching option visible in the dropdown.
  useEffect(() => {
    if (roleFilter !== "all" && !filterableRoles.includes(roleFilter)) {
      setRoleFilter("all");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterableRoles, roleFilter]);

  // const [updateUserRole, { isLoading: isSavingRole }] =
  //   useUpdateUserRoleMutation();

  // const saveRole = async (id: string, role: Role, name: string) => {
  //   try {
  //     await updateUserRole({ userId: id, roleName: role }).unwrap();
  //     setEditing(null);
  //     toast.success(`Role updated — ${name} is now ${ROLE_LABEL[role]}.`);
  //   } catch {
  //     // Error toast is already shown by the shared RTK Query error handler.
  //   }
  // };

  const [assignUserProjects] = useAssignUserProjectsMutation();

  const saveAssignments = (userId: string, assignments: ProjectAssignment[]) =>
    assignUserProjects({
      userId,
      assignments: assignments.map((a) => ({
        project_id: a.projectId,
        roles: [a.role],
      })),
    }).unwrap();

  const [updateUserStatus] = useUpdateUserStatusMutation();

  const toggleStatus = async (id: string, name: string, base: Status) => {
    const next: Status = base === "active" ? "suspended" : "active";
    try {
      await updateUserStatus({
        userId: id,
        isActive: next === "active",
      }).unwrap();
      toast.info(
        next === "suspended"
          ? `${name}'s access is now suspended.`
          : `${name}'s access is now active.`,
      );
    } catch {
      // Error toast is already shown by the shared RTK Query error handler.
    }
  };

  const [deleteUser, { isLoading: isDeletingAccount }] =
    useDeleteUserMutation();

  const confirmDeleteUser = async () => {
    if (!deleteTarget) return;
    try {
      await deleteUser({ userId: deleteTarget.id }).unwrap();
      toast.success(`${deleteTarget.name}'s account has been deleted.`);
      setDeleteTarget(null);
    } catch {
      // Error toast is already shown by the shared RTK Query error handler.
    }
  };

  const userColumns: TableColumn<User>[] = [
    {
      key: "name",
      label: "User",
      render: (_, u) => <span className="font-bold">{u.name}</span>,
    },
    {
      key: "email",
      label: "Email",
      render: (_, u) => <span className="text-mut">{u.email}</span>,
    },
    {
      key: "role",
      label: "Role",
      render: (_, u) => (
        <div className="flex flex-wrap gap-1">
          {u.roles.map((r) => (
            <Pill key={r} tone={ROLE_PILL[r]}>
              {ROLE_LABEL[r]}
            </Pill>
          ))}
        </div>
      ),
    },
    {
      key: "status",
      label: "Status",
      render: (_, u) =>
        u.defaultStatus === "active" ? (
          <Chip tone="ok" dot>
            Active
          </Chip>
        ) : (
          <Chip tone="off" dot>
            Suspended
          </Chip>
        ),
    },
    {
      key: "projectsLabel",
      label: "Projects",
      render: (_, u) => <span className="text-mut">{u.projectsLabel}</span>,
    },
    {
      key: "actions",
      label: "",
      align: "right",
      render: (_, u) => {
        if (!canManageUsers) return null;
        const status = u.defaultStatus;
        const isSelf =
          !!currentUser?.email &&
          u.email.toLowerCase() === currentUser.email.toLowerCase();
        const showProjectBtn = !isSelf && !hasRole(USER_ROLE.SUPER_ADMIN);
        // const showEditRoleBtn = !isSelf && !hasRole(USER_ROLE.SUPER_ADMIN);
        return (
          <>
            {showProjectBtn && (
              <button
                className="text-[12px] font-semibold text-accent hover:underline"
                onClick={() => setDrawer(u)}
              >
                Projects
              </button>
            )}
            {/* {showEditRoleBtn && (
              <button
                className="ml-3 text-[12px] font-semibold text-accent hover:underline"
                onClick={() =>
                  setEditing({ id: u.id, name: u.name, role: u.role })
                }
              >
                Edit role
              </button>
            )} */}
            {!isSelf && (
              <button
                className={`ml-3 text-[12px] font-semibold hover:underline ${status === "active" ? "text-error" : "text-success"}`}
                onClick={() => void toggleStatus(u.id, u.name, u.defaultStatus)}
              >
                {status === "active" ? "Suspend" : "Reactivate"}
              </button>
            )}
            {!isSelf && (
              <button
                className="ml-3 text-[12px] font-semibold text-error hover:underline"
                onClick={() => setDeleteTarget({ id: u.id, name: u.name })}
              >
                Delete
              </button>
            )}
          </>
        );
      },
    },
  ];

  const inviteColumns: TableColumn<PendingInvite>[] = [
    {
      key: "email",
      label: "Email",
      render: (_, inv) => <span className="font-semibold">{inv.email}</span>,
    },
    {
      key: "expires_at",
      label: "Expires",
      render: (_, inv) => (
        <span className="text-mut">{formatDate(inv.expires_at, false)}</span>
      ),
    },
    {
      key: "created_at",
      label: "Invited",
      render: (_, inv) => (
        <span className="text-mut">{formatDate(inv.created_at, false)}</span>
      ),
    },
    {
      key: "status",
      label: "Status",
      render: () => (
        <Chip tone="warn" dot>
          Invite sent
        </Chip>
      ),
    },
    {
      key: "actions",
      label: "",
      align: "right",
      render: (_, inv) => (
        <>
          <button
            className="text-[12px] font-semibold text-accent hover:underline"
            onClick={() => handleResendInvite(inv)}
          >
            Resend
          </button>
          <button
            className="ml-3 text-[12px] font-semibold text-error hover:underline"
            onClick={() => handleRevokeInvite(inv)}
          >
            Revoke
          </button>
        </>
      ),
    },
  ];

  return (
    <div className="w-full">
      <Panel title="Roles">
        <div className="flex flex-col gap-3 md:flex-row">
          {filterableRoles.map((r) => (
            <div
              key={r}
              className="flex-1 rounded-lg border border-border p-3.5"
            >
              <div className="flex items-center gap-2.5">
                <Pill tone={ROLE_PILL[r]}>{ROLE_LABEL[r]}</Pill>
                <b className="text-[13px]">{ROLE_SCOPE_LABEL[r]}</b>
              </div>
              <div className="mt-1.5 text-xs leading-relaxed text-mut">
                {ROLE_DESCRIPTION[r]}
              </div>
            </div>
          ))}
        </div>
      </Panel>

      <Panel
        title={
          <h3 className="text-[15px] font-semibold">
            Users
            {activeTenant && (
              <>
                <span> — </span>
                <span className="font-normal text-mut">{clientName}</span>
              </>
            )}
          </h3>
        }
        aside={
          <div className="flex items-center gap-2.5">
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search name or email…"
              className="h-8 w-[180px] rounded-md border border-border-strong px-2.5 text-[12.5px] outline-none focus:border-accent"
            />
            <Dropdown
              size="xs"
              width="150px"
              selected={roleFilter}
              onChange={(opt) => setRoleFilter(opt.value as Role | "all")}
              options={[
                { label: "All roles", value: "all" },
                ...filterableRoles.map((r) => ({ label: ROLE_LABEL[r], value: r })),
              ]}
            />
            <PermissionGate allow={PERMISSION.USER_INVITE}>
              <Button
                size="xs"
                iconLeading={<PlusIcon className="w-3 h-3" />}
                onClick={() => setInviteOpen(true)}
              >
                Invite user
              </Button>
            </PermissionGate>
          </div>
        }
        bodyClassName="p-0"
      >
        <Table
          columns={userColumns}
          data={isLoading || hasError ? [] : rows}
          emptyMessage={
            !activeTenantId
              ? isTenantLoading
                ? "Loading clients…"
                : (
                  <div className="flex flex-col items-center gap-2">
                    <span>
                      Select a client in the Clients & Providers tab to view its
                      users.
                    </span>
                    <Button size="xs" onClick={goToClientsProviders}>
                      Go to Clients & Providers
                    </Button>
                  </div>
                )
              : isLoading
                ? "Loading users…"
                : hasError
                  ? "Failed to load users."
                  : "No users match your search."
          }
          className="!rounded-none !border-none !shadow-none"
        />
      </Panel>

      {invites.length > 0 && (
        <Panel title="Pending invites" bodyClassName="p-0">
          <Table
            columns={inviteColumns}
            data={invites}
            className="!rounded-none !border-none !shadow-none"
          />
        </Panel>
      )}

      <InfoNote>
        <b>Scoping:</b> a Super Admin sees this list for every client and can
        invite Client Admins. A Client Admin sees only their own client's users
        and projects. Members never see the Settings area.
      </InfoNote>

      <AssignDrawer
        key={drawer?.id ?? "none"}
        open={drawer !== null}
        userName={drawer?.name ?? ""}
        allProjects={tenantProjects}
        assignedProjects={drawer?.projects ?? []}
        onClose={() => setDrawer(null)}
        onSave={(assignments) => {
          if (!drawer) return;
          return saveAssignments(drawer.id, assignments);
        }}
      />

      {/* edit-role modal */}
      {/* <Modal
        isOpen={editing !== null}
        onClose={() => setEditing(null)}
        title={`Edit role — ${editing?.name ?? ""}`}
        className="min-h-[50vh]"
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => setEditing(null)}
              disabled={isSavingRole}
            >
              Cancel
            </Button>
            <Button
              onClick={() =>
                editing && void saveRole(editing.id, editing.role, editing.name)
              }
              loading={isSavingRole}
              disabled={isSavingRole}
            >
              Save role
            </Button>
          </>
        }
      >
        {editing && (
          <div>
            <Dropdown
              label="Role"
              className="w-full"
              selected={editing.role}
              onChange={(opt) =>
                setEditing({ ...editing, role: opt.value as Role })
              }
              options={EDITABLE_ROLES.map((r) => ({
                label: `${ROLE_LABEL[r]} — ${ROLE_SCOPE_LABEL[r]}`,
                value: r,
              }))}
            />
            <div className="mt-1.5 text-xs text-mut">
              Determines what this user can see and do across the platform.
            </div>
          </div>
        )}
      </Modal> */}

      {/* delete-user confirmation */}
      <Modal
        isOpen={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        title="Delete account"
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => setDeleteTarget(null)}
              disabled={isDeletingAccount}
            >
              Cancel
            </Button>
            <Button
              variant="danger"
              onClick={() => void confirmDeleteUser()}
              loading={isDeletingAccount}
              disabled={isDeletingAccount}
            >
              Delete account
            </Button>
          </>
        }
      >
        <div className="text-[13px] leading-relaxed">
          This will permanently delete <b>{deleteTarget?.name}</b>&apos;s
          account. This action cannot be undone.
        </div>
      </Modal>

      {/* invite modal */}
      <PermissionGate allow={PERMISSION.USER_INVITE}>
        <InviteModal
          open={inviteOpen}
          onClose={() => setInviteOpen(false)}
          onInvite={(inv) => {
            setInviteOpen(false);
            toast.success(
              `${inv.email} invited as ${ROLE_LABEL[inv.role]}${inv.projects.length ? ` with ${inv.projects.length} project(s)` : ""}.`,
            );
          }}
        />
      </PermissionGate>
    </div>
  );
}

function InviteModal({
  open,
  onClose,
  onInvite,
}: {
  open: boolean;
  onClose: () => void;
  onInvite: (inv: { email: string; role: Role; projects: string[] }) => void;
}) {
  // Highest-priority role among the current user's roles (mirrors pickRoles
  // below) — a user can hold more than one role, in no particular order.
  const currentUserRoles = useAppSelector((s) => s.auth.user?.roles ?? []);
  const currentUserRole = ROLE_ORDER.find((r) => currentUserRoles.includes(r));
  const assignableRoles: Role[] = currentUserRole
    ? (ASSIGNABLE_ROLES_BY_INVITER[currentUserRole] ?? [])
    : [];

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>(assignableRoles[0] ?? "member");
  const [picked, setPicked] = useState<string[]>([]);
  const validEmail = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email);

  // Reset to a role the current inviter is actually allowed to assign
  // whenever the modal opens (covers the rare case the picked role is no
  // longer valid, e.g. stale state from a previous open).
  useEffect(() => {
    if (open && assignableRoles.length > 0 && !assignableRoles.includes(role)) {
      setRole(assignableRoles[0]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, assignableRoles]);

  // The invite targets the active tenant, and a super_admin can retarget it
  // from here — picking a tenant sets the active one rather than holding a
  // second copy of the selection, so the users and pending-invite lists
  // behind the modal stay in step with what the invite will do.
  const dispatch = useAppDispatch();
  const { tenants, activeTenant, isSuperAdmin } = useActiveTenant();
  const activeTenantId = activeTenant?.id ?? null;
  const canPickTenant = isSuperAdmin && tenants.length > 0;

  const valid = validEmail && !!name.trim() && !!activeTenantId;
  const [inviteUser, { isLoading: isInviting }] = useInviteUserMutation();

  const resetForm = () => {
    setName("");
    setEmail("");
    setRole(assignableRoles[0] ?? "member");
    setPicked([]);
  };

  const handleClose = () => {
    resetForm();
    onClose();
  };

  const submit = async () => {
    if (!valid || !activeTenantId) return;
    try {
      await inviteUser({
        tenantId: activeTenantId,
        body: {
          email: email.trim(),
          name: name.trim(),
          ...(role === "admin" ? {} : { role }),
        },
      }).unwrap();
      onInvite({
        email: email.trim(),
        role,
        projects: picked,
      });
      resetForm();
    } catch {
      // Error toast is already shown by the shared RTK Query error handler.
    }
  };

  return (
    <Modal
      isOpen={open}
      onClose={handleClose}
      title="Invite user"
      footer={
        <>
          <Button variant="ghost" onClick={handleClose}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={!valid || isInviting}>
            {isInviting ? "Sending…" : "Send invite"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <div className="w-full">
          <Dropdown
            label="Tenant"
            required
            disabled={!canPickTenant}
            className="w-full"
            selected={activeTenantId ?? ""}
            onChange={(opt) => dispatch(setActiveTenantId(opt.value))}
            placeholder={
              canPickTenant ? "Select a client…" : "No client available"
            }
            options={
              canPickTenant
                ? tenants.map((t) => ({ label: t.name, value: t.id }))
                : activeTenant
                  ? [{ label: activeTenant.name, value: activeTenant.id }]
                  : []
            }
          />
        </div>
        <Input
          label="User Name"
          required
          error={!!name && !name.trim()}
          errorMessage="Enter user name"
          value={name}
          maxLength={100}
          onChange={(e) => setName(e.target.value)}
          placeholder="Enter user name"
        />
        <Input
          label="User Email"
          required
          error={!!email && !validEmail}
          errorMessage="Enter a valid email address."
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="name@company.com"
        />
        <div className="w-full">
          <Dropdown
            label="Role"
            className="w-full"
            selected={role}
            onChange={(opt) => setRole(opt.value as Role)}
            options={assignableRoles.map((r) => ({
              label: `${ROLE_LABEL[r]} — ${ROLE_SCOPE_LABEL[r]}`,
              value: r,
            }))}
          />
        </div>
      </div>
    </Modal>
  );
}
