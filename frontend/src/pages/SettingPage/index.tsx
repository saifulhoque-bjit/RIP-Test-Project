import { useSearchParams } from "react-router-dom";

import { PageHeaderPortal } from "@/components/common/PageHeaderPortal";
import { cn } from "@/lib/utils";
import { ClientsProviders } from "@/features/Settings/ClientsProviders";
import { AccessRoles } from "@/features/Settings/AccessRoles";
import { useHasPermission } from "@/hooks/usePermission";
import { PERMISSION } from "@/constants/permissions";

const ALL_TABS = [
  { key: "clients", label: "Clients & Providers" },
  { key: "access", label: "Access & Roles" },
];

export default function Settings() {
  const [params, setParams] = useSearchParams();

  // Permissions
  const canSeeClientsTab = useHasPermission([
    PERMISSION.TENANT_CREATE,
    PERMISSION.TENANT_VIEW,
    PERMISSION.TENANT_UPDATE,
  ]);

  const tabs = canSeeClientsTab
    ? ALL_TABS
    : ALL_TABS.filter((tab) => tab.key !== "clients");

  const requestedTab = params.get("tab") ?? tabs[0].key;
  const active = tabs.some((tab) => tab.key === requestedTab)
    ? requestedTab
    : tabs[0].key;
  const setActive = (key: string) => setParams({ tab: key }, { replace: true });

  return (
    <>
      <PageHeaderPortal>
        <div className="shrink-0 border-b border-[var(--border-primary)] bg-[#eef1f6] px-6 py-1.5">
          <div
            className="flex gap-6"
            role="tablist"
            aria-orientation="horizontal"
          >
            {tabs.map((tab) => {
              const isActive = tab.key === active;
              return (
                <button
                  key={tab.key}
                  type="button"
                  role="tab"
                  aria-selected={isActive}
                  onClick={() => setActive(tab.key)}
                  className={cn(
                    "border-b-2 px-0.5 py-2 text-[13px] transition-colors",
                    isActive
                      ? "border-accent font-semibold text-accent"
                      : "border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]",
                  )}
                >
                  {tab.label}
                </button>
              );
            })}
          </div>
        </div>
      </PageHeaderPortal>

      {active === "clients" && <ClientsProviders />}
      {active === "access" && <AccessRoles />}
    </>
  );
}
