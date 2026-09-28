import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "@/lib/toast";
import { PermissionGate } from "@/components/common/PermissionGate";
import { PERMISSION } from "@/constants/permissions";
import { useHasPermission } from "@/hooks/usePermission";
import Modal from "@/components/common/Modal";
import { InfoNote } from "@/components/common/Note";
import { Panel } from "../ProjectWorkspace/Overview/Panel";
import Button from "@/components/common/Button/Button";
import { Chip } from "@/components/common/Chip";
import Input from "@/components/common/Input";
import { Switch } from "@/components/ui/switch";
import { Table, type TableColumn } from "@/components/common/Table";
import {
  useGetTenantLlmProvidersQuery,
  useUpdateTenantLlmProviderMutation,
  useTestTenantLlmProviderMutation,
} from "@/services/api/modules/llmProviders";
import type {
  Tenant,
  TenantLlmProvider,
  TenantLlmProviderTestResult,
} from "@/types";
import { useActiveTenant } from "@/hooks/useActiveTenant";
import { useAppDispatch } from "@/store/hooks";
import { setActiveTenantId } from "@/store/slices/tenantSlice";
import { ClientModal } from "./ClientModal";
import { useUpdateTenantStatusMutation } from "@/services/api/modules/tenants";
import { USER_ROLE } from "@/types/auth";
import { hasRole } from "@/utils/hasRole";
import { PlusIcon } from "@/assets/icons/PlusIcon";

interface Provider {
  id: string;
  name: string;
}
interface Client {
  [key: string]: unknown;
  id: string;
  name: string;
  status: string | null;
  projectCount: number | null;
  providers: Provider[];
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

export function ClientsProviders() {
  const dispatch = useAppDispatch();
  const [, setParams] = useSearchParams();
  const { tenants, activeTenant, isLoading, hasError } = useActiveTenant();
  const isClientAdmin = hasRole(USER_ROLE.CLIENT_ADMIN);
  const canInviteUsers = useHasPermission(PERMISSION.USER_INVITE);
  const canEditClients = useHasPermission(PERMISSION.TENANT_UPDATE);
  const canDeactivateClients = useHasPermission(PERMISSION.TENANT_DELETE);
  const hasRowActions =
    canEditClients || canInviteUsers || canDeactivateClients;

  const apiClients = useMemo<Client[]>(
    () =>
      tenants.map((t) => ({
        id: t.id,
        name: t.name,
        status: t.status,
        projectCount: t.total_number_of_project,
        providers: t.providers.map((id) => ({ id, name: providerLabel(id) })),
      })),
    [tenants],
  );

  const clients = apiClients;

  const [isNewClientOpen, setIsNewClientOpen] = useState(false);
  const [editing, setEditing] = useState<Tenant | null>(null);
  const [deactivating, setDeactivating] = useState<Tenant | null>(null);
  const [activating, setActivating] = useState<Tenant | null>(null);
  const [invitePromptFor, setInvitePromptFor] = useState<Tenant | null>(null);
  const [updateTenantStatus, { isLoading: isDeactivating }] =
    useUpdateTenantStatusMutation();
  const [activateTenant, { isLoading: isActivating }] =
    useUpdateTenantStatusMutation();

  // Nothing is selected until the user picks a row — there is no default
  // client. The pick goes through the tenant slice rather than local state
  // because the header switcher is hidden here for a super admin, making this
  // table the only picker the Access & Roles tab can read from.
  const selected = clients.find((c) => c.id === activeTenant?.id);
  const selectClient = (id: string) => dispatch(setActiveTenantId(id));

  const { data: providerStatusResponse } = useGetTenantLlmProvidersQuery(
    selected?.id ?? "",
    { skip: !selected?.id },
  );

  const providerItems = [...(providerStatusResponse?.data?.items ?? [])].sort(
    (a, b) => a.provider.localeCompare(b.provider),
  );

  // Invite a Client Admin into a specific client, whichever row it is on —
  // selecting it first is what points the invite modal at that client.
  const inviteClientAdmin = (tenantId: string) => {
    setInvitePromptFor(null);
    selectClient(tenantId);
    setParams({ tab: "access", invite: "1" }, { replace: true });
  };

  const clientColumns: TableColumn<Client>[] = [
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
    ...(hasRowActions
      ? [
          {
            key: "actions",
            label: "",
            align: "right" as const,
            className: "whitespace-nowrap",
            // Each action stops propagation so it doesn't also trigger the
            // row's select-on-click.
            render: (_: unknown, c: Client) => {
              const tenant = tenants.find((t) => t.id === c.id);
              // Flex + gap rather than a margin on each button, so the spacing
              // holds for any combination the permission gates leave visible.
              return (
                <div className="flex items-center justify-end gap-3">
                  {canInviteUsers && (
                    <Button
                      size="xs"
                      // Ghost, not primary: the panel's "New client" is the
                      // page's primary action, and a filled button repeated
                      // on every row would out-shout it.
                      variant="ghost"
                      iconLeading={<PlusIcon className="w-3 h-3" />}
                      onClick={(e) => {
                        e.stopPropagation();
                        inviteClientAdmin(c.id);
                      }}
                    >
                      Invite
                    </Button>
                  )}
                  {canEditClients && (
                    <button
                      className="text-[12px] font-semibold text-accent hover:underline"
                      onClick={(e) => {
                        e.stopPropagation();
                        if (tenant) setEditing(tenant);
                      }}
                    >
                      Update
                    </button>
                  )}
                  {canDeactivateClients &&
                    (c.status === "inactive" ? (
                      <button
                        className="text-[12px] font-semibold text-success hover:underline"
                        onClick={(e) => {
                          e.stopPropagation();
                          if (tenant) setActivating(tenant);
                        }}
                      >
                        Activate
                      </button>
                    ) : (
                      <button
                        className="text-[12px] font-semibold text-error hover:underline"
                        onClick={(e) => {
                          e.stopPropagation();
                          if (tenant) setDeactivating(tenant);
                        }}
                      >
                        Deactivate
                      </button>
                    ))}
                </div>
              );
            },
          } satisfies TableColumn<Client>,
        ]
      : []),
  ];

  // A brand-new client has nobody who can administer it, so the create flow
  // hands straight off to inviting its first Client Admin. The invite targets
  // whichever client is active, which is why the new one is selected first.
  const handleClientSaved = (tenant: Tenant, mode: "created" | "updated") => {
    if (mode === "updated") {
      setEditing(null);
      toast.success(`Client "${tenant.name}" updated.`);
      return;
    }

    selectClient(tenant.id);
    setIsNewClientOpen(false);
    if (canInviteUsers) {
      // The "Client created" modal is the success message — no toast on top.
      setInvitePromptFor(tenant);
    } else {
      toast.success(`Client "${tenant.name}" created.`);
    }
  };

  const handleDeactivate = async () => {
    if (!deactivating) return;
    try {
      await updateTenantStatus({
        tenantId: deactivating.id,
        status: "inactive",
      }).unwrap();
      toast.success(`Client "${deactivating.name}" deactivated.`);
      setDeactivating(null);
    } catch {
      // Error toast is already shown by the shared RTK Query error handler.
    }
  };

  const handleActivate = async () => {
    if (!activating) return;
    try {
      await activateTenant({
        tenantId: activating.id,
        status: "active",
      }).unwrap();
      toast.success(`Client "${activating.name}" activated.`);
      setActivating(null);
    } catch {
      // Error toast is already shown by the shared RTK Query error handler.
    }
  };

  return (
    <div className="w-full">
      {isClientAdmin && (
        <InfoNote className="mb-4">
          Providers, credentials, and budget are scoped{" "}
          <b>per client (tenant)</b>. A client's API keys are never used by
          another client's projects. Each project under a client picks which of
          the client's enabled providers it runs on.
        </InfoNote>
      )}

      {!isClientAdmin && (
        <Panel
          title="Clients"
          aside={
            <PermissionGate allow={PERMISSION.TENANT_CREATE}>
              <Button
                size="sm"
                iconLeading={<PlusIcon className="w-3 h-3" />}
                onClick={() => setIsNewClientOpen(true)}
              >
                New client
              </Button>
            </PermissionGate>
          }
          bodyClassName="p-0"
        >
          <Table
            columns={clientColumns}
            data={isLoading || hasError ? [] : clients}
            onRowClick={(c) => selectClient(c.id)}
            getRowClassName={(c) =>
              c.id === selected?.id ? "bg-accent-50" : undefined
            }
            emptyMessage={
              isLoading
                ? "Loading clients…"
                : hasError
                  ? "Failed to load clients."
                  : "No clients yet."
            }
            className="!rounded-none !border-none !shadow-none"
          />
        </Panel>
      )}

      {isClientAdmin && (
        <Panel
          title={
            selected
              ? `Providers & Credentials — ${selected.name}`
              : "Providers & Credentials"
          }
        >
          <div className="space-y-3.5">
            {!selected ? (
              <div className="py-6 text-center text-[12.5px] text-mut">
                Select a client to view its providers and credentials.
              </div>
            ) : providerItems.length ? (
              providerItems.map((p) => (
                <ProviderCard
                  key={p.provider}
                  tenantId={selected.id}
                  data={p}
                />
              ))
            ) : (
              <div className="py-6 text-center text-[12.5px] text-mut">
                No providers configured yet.
              </div>
            )}
          </div>
        </Panel>
      )}

      <ClientModal
        isOpen={isNewClientOpen}
        onClose={() => setIsNewClientOpen(false)}
        onSaved={handleClientSaved}
      />

      <ClientModal
        isOpen={!!editing}
        onClose={() => setEditing(null)}
        onSaved={handleClientSaved}
        tenant={editing}
      />

      <Modal
        isOpen={!!invitePromptFor}
        onClose={() => setInvitePromptFor(null)}
        title="Client created"
        subtitle={`"${invitePromptFor?.name}" is ready. Invite a Client Admin to manage it.`}
        footer={
          <>
            <Button variant="ghost" onClick={() => setInvitePromptFor(null)}>
              Not now
            </Button>
            <Button
              onClick={() =>
                invitePromptFor && inviteClientAdmin(invitePromptFor.id)
              }
            >
              Invite
            </Button>
          </>
        }
      >
        <p className="m-0 text-[13px] leading-relaxed text-mut">
          A Client Admin manages this client's users, projects, and
          integrations. Until one is invited, only a Super Admin can administer{" "}
          <b className="text-[var(--text-primary)]">{invitePromptFor?.name}</b>.
        </p>
      </Modal>

      <Modal
        isOpen={!!deactivating}
        onClose={() => setDeactivating(null)}
        title="Deactivate client"
        subtitle={`"${deactivating?.name}" will be marked inactive.`}
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => setDeactivating(null)}
              disabled={isDeactivating}
            >
              Cancel
            </Button>
            <Button
              variant="danger"
              onClick={handleDeactivate}
              loading={isDeactivating}
            >
              Deactivate
            </Button>
          </>
        }
      >
        <p className="m-0 text-[13px] leading-relaxed text-mut">
          This is reversible — the client keeps its projects, users, and
          provider credentials, and stays in this list with an{" "}
          <b className="text-[var(--text-primary)]">Inactive</b> status.
        </p>
      </Modal>

      <Modal
        isOpen={!!activating}
        onClose={() => setActivating(null)}
        title="Activate client"
        subtitle={`"${activating?.name}" will be marked active.`}
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => setActivating(null)}
              disabled={isActivating}
            >
              Cancel
            </Button>
            <Button
              variant="success"
              onClick={handleActivate}
              loading={isActivating}
            >
              Activate
            </Button>
          </>
        }
      >
        <p className="m-0 text-[13px] leading-relaxed text-mut">
          The client regains access with its existing projects, users, and
          provider credentials, and its status changes to{" "}
          <b className="text-[var(--text-primary)]">Active</b>.
        </p>
      </Modal>
    </div>
  );
}

function ProviderCard({
  tenantId,
  data,
}: {
  tenantId: string;
  data: TenantLlmProvider;
}) {
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [updateProvider, { isLoading: isSaving }] =
    useUpdateTenantLlmProviderMutation();
  const [updateActive, { isLoading: isTogglingActive }] =
    useUpdateTenantLlmProviderMutation();
  const [testProvider, { isLoading: isTesting }] =
    useTestTenantLlmProviderMutation();

  // The test mutation resolves before the list refetch it triggers (via
  // invalidatesTags) lands, so `data` is briefly stale — long enough to flash
  // "Not verified" between the loading spinner and the real result. Hold the
  // test's own response until `data` catches up with it.
  const [pendingResult, setPendingResult] =
    useState<TenantLlmProviderTestResult | null>(null);

  if (
    pendingResult &&
    data.last_tested_at &&
    new Date(data.last_tested_at).getTime() >=
      new Date(pendingResult.tested_at).getTime()
  ) {
    setPendingResult(null);
  }

  const effectiveHasApiKey = pendingResult ? true : data.has_api_key;
  const effectiveVerified = pendingResult
    ? pendingResult.verified
    : data.is_verified;

  const testBusy = isSaving || isTesting;
  const label = providerLabel(data.provider);

  const handleToggleActive = (next: boolean) => {
    updateActive({ tenantId, provider: data.provider, is_active: next });
  };

  const handleTest = async () => {
    try {
      const trimmed = apiKeyInput.trim();
      if (trimmed) {
        await updateProvider({
          tenantId,
          provider: data.provider,
          api_key: trimmed,
        }).unwrap();
        setApiKeyInput("");
      }
      const result = await testProvider({
        tenantId,
        provider: data.provider,
      }).unwrap();
      setPendingResult(result.data);
      if (result.data.verified) {
        toast.success(`${label} connection verified.`);
      } else {
        toast.warning(
          `${label} connection failed. Check the API key and try again.`,
        );
      }
    } catch {
      // Errors are already surfaced by the shared RTK Query error toast.
    }
  };

  const statusChip = testBusy ? (
    <Chip tone="info" dot>
      Testing…
    </Chip>
  ) : !effectiveHasApiKey ? (
    <Chip tone="warn" dot>
      Key not set
    </Chip>
  ) : effectiveVerified ? (
    <Chip tone="ok" dot>
      Verified
    </Chip>
  ) : (
    <Chip tone="err" dot>
      Not verified
    </Chip>
  );

  return (
    <div className="rounded-lg border border-border p-[18px]">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <Chip tone={data.provider === "anthropic" ? "ai" : "info"}>
            {label}
          </Chip>
        </div>
        <Switch
          checked={data.is_active}
          disabled={!effectiveVerified || isTogglingActive}
          onCheckedChange={handleToggleActive}
        />
      </div>
      <div className="mt-3.5 grid grid-cols-1 gap-[18px] md:grid-cols-2">
        <Input
          label="API key"
          placeholder={data.api_key_hint ?? "API Key"}
          required
          value={apiKeyInput}
          onChange={(e) => setApiKeyInput(e.target.value)}
          type="password"
        />
        <div className="flex pt-6 h-full items-center gap-2.5">
          {statusChip}
          <Button
            variant="ghost"
            size="sm"
            onClick={handleTest}
            disabled={(!apiKeyInput.trim() && !effectiveHasApiKey) || testBusy}
            loading={testBusy}
          >
            Test connection
          </Button>
        </div>
      </div>
      {effectiveHasApiKey && !effectiveVerified && data.last_tested_at && (
        <div className="mt-2 text-[11.5px] text-[var(--error)]">
          Connection failed. Check the API key and try again.
        </div>
      )}
    </div>
  );
}
