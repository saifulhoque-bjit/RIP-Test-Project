import type { TableColumn } from "@/components/common/Table";
import { Chip } from "@/components/common/Chip";
import type { Tenant } from "@/types";

interface ClientProvider {
  id: string;
  name: string;
}

export interface ClientRow {
  [key: string]: unknown;
  id: string;
  name: string;
  status: string | null;
  projectCount: number | null;
  providers: ClientProvider[];
}

const providerLabel = (id: string) => id.charAt(0).toUpperCase() + id.slice(1);

const TENANT_STATUS_TONE: Record<string, "ok" | "warn" | "off" | "neutral"> = {
  active: "ok",
  pending_invitation: "warn",
  inactive: "off",
  suspended: "off",
};
const TENANT_STATUS_LABEL: Record<string, string> = {
  active: "Active",
  pending_invitation: "Pending invitation",
  inactive: "Inactive",
  suspended: "Suspended",
};

/** Same row shape the Admin page's Clients table builds from the tenant list. */
export function toClientRows(tenants: Tenant[]): ClientRow[] {
  return tenants.map((t) => ({
    id: t.id,
    name: t.name,
    status: t.status,
    projectCount: t.total_number_of_project,
    providers: t.providers.map((id) => ({ id, name: providerLabel(id) })),
  }));
}

/** Dashboard "Clients" table for a super admin — same columns as the Admin page's Clients table, minus row actions. */
export function createClientColumns(): TableColumn<ClientRow>[] {
  return [
    {
      key: "name",
      label: "Client",
      render: (_, c) => <span className="font-bold">{c.name}</span>,
    },
    {
      key: "status",
      label: "Status",
      render: (_, c) =>
        c.status ? (
          <Chip tone={TENANT_STATUS_TONE[c.status] ?? "neutral"} dot>
            {TENANT_STATUS_LABEL[c.status] ?? c.status}
          </Chip>
        ) : (
          <span className="text-mut">—</span>
        ),
    },
    {
      key: "projectCount",
      label: "Projects",
      render: (_, c) => <span>{c.projectCount ?? "—"}</span>,
    },
    {
      key: "providers",
      label: "Active providers",
      render: (_, c) => (
        <div className="flex gap-1.5">
          {c.providers.map((p) => (
            <Chip key={p.id} tone={p.id === "anthropic" ? "ai" : "info"}>
              {p.name}
            </Chip>
          ))}
        </div>
      ),
    },
  ];
}
