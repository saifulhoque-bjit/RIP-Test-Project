import { useEffect, useRef, useState } from "react";
import { useAppDispatch } from "@/store/hooks";
import { setActiveTenantId } from "@/store/slices/tenantSlice";
import { useActiveTenant } from "@/hooks/useActiveTenant";
import { cn } from "@/lib/utils";

const initialsOf = (name: string) =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase() || "?";

/** Client (tenant) switcher in the global top bar. */
export default function TenantSwitcher() {
  const dispatch = useAppDispatch();
  const { tenants, activeTenant, isSuperAdmin } = useActiveTenant();
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleOutsideClick = (event: MouseEvent) => {
      if (!wrapperRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handleOutsideClick);
    return () => document.removeEventListener("mousedown", handleOutsideClick);
  }, []);

  // A super_admin has no default client, so the switcher stays mounted with a
  // "Select client" prompt until they pick one — hiding it would leave them no
  // way to choose. Everyone else has their one tenant or nothing to show.
  if (!activeTenant && !isSuperAdmin) return null;

  return (
    <div className="relative" ref={wrapperRef}>
      <button
        type="button"
        onClick={() => isSuperAdmin && setOpen((o) => !o)}
        className={cn(
          "flex items-center gap-2.5 rounded-[9px] border bg-white px-2.5 py-1.5",
          isSuperAdmin &&
            "border-[var(--border-strong)] cursor-pointer hover:bg-[#f7f9fc]",
          !isSuperAdmin && "border-transparent cursor-default",
        )}
      >
        <span
          className={cn(
            "grid h-6 w-6 place-items-center rounded-md text-[11px] font-extrabold text-white",
            activeTenant ? "bg-[var(--navy-800)]" : "bg-[var(--text-quaternary)]",
          )}
        >
          {activeTenant ? initialsOf(activeTenant.name) : "—"}
        </span>
        <span className="text-left">
          <span className="block text-[9px] font-bold uppercase tracking-wide text-[var(--text-quaternary)]">
            Client
          </span>
          <span
            className={cn(
              "block text-[13px] font-bold",
              activeTenant
                ? "text-[var(--text-primary)]"
                : "text-[var(--text-tertiary)]",
            )}
          >
            {activeTenant?.name ?? "Select client"}{" "}
            <span className={cn(!isSuperAdmin && "invisible")}>▾</span>
          </span>
        </span>
      </button>

      {open && isSuperAdmin && (
        <div className="absolute left-0 top-[46px] z-50 w-[260px] overflow-hidden rounded-[10px] border border-[var(--border-primary)] bg-white shadow-[0_16px_40px_rgba(8,21,52,.24)]">
          {tenants.map((t) => {
            const isActive = t.id === activeTenant?.id;
            return (
              <button
                key={t.id}
                type="button"
                disabled={isActive}
                onClick={() => {
                  if (isActive) return;
                  dispatch(setActiveTenantId(t.id));
                  setOpen(false);
                }}
                className={cn(
                  "flex w-full items-center justify-between border-b border-[var(--border-primary)] px-3 py-2.5 text-[13px] hover:bg-[#fafbfd]",
                  isActive &&
                    "bg-[var(--accent-50)] cursor-default hover:bg-[var(--accent-50)]",
                )}
              >
                <span>{t.name}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
