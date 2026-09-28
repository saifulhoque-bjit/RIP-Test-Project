import { useState } from "react";

import Modal from "@/components/common/Modal";
import Button from "@/components/common/Button/Button";
import FieldLabel from "@/components/common/FieldLabel";
import Input from "@/components/common/Input";
import { useGetAppSettingsQuery } from "@/services/api/modules/settings";
import {
  useCreateTenantMutation,
  useUpdateTenantMutation,
} from "@/services/api/modules/tenants";
import type { Tenant, UpdateTenantRequest } from "@/types";

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

const sameProviders = (a: string[], b: string[]) =>
  a.length === b.length && [...a].sort().join() === [...b].sort().join();

type ClientModalProps = {
  isOpen: boolean;
  onClose: () => void;
  onSaved: (tenant: Tenant, mode: "created" | "updated") => void;
  tenant?: Tenant | null;
};

/**
 * Create a client, or edit an existing one when `tenant` is passed.
 *
 * The form is remounted (via `key`) every time the modal transitions from
 * closed to open, so field state always starts from the current `tenant`
 * prop and a cancelled edit never leaks into the next one — without an
 * effect resetting state mid-render, and without resetting mid-close, which
 * would flash blank fields during the exit animation.
 */
export function ClientModal({ isOpen, ...rest }: ClientModalProps) {
  const [formInstance, setFormInstance] = useState(0);
  const [wasOpen, setWasOpen] = useState(isOpen);

  if (isOpen && !wasOpen) {
    setWasOpen(true);
    setFormInstance((n) => n + 1);
  } else if (!isOpen && wasOpen) {
    setWasOpen(false);
  }

  return <ClientModalForm key={formInstance} isOpen={isOpen} {...rest} />;
}

/**
 * Edit sends a diff, not the whole form: PATCH /tenants/{id} forbids unknown
 * keys and rejects an empty body, and `providers` replaces the entire enabled
 * set whenever it is present.
 */
function ClientModalForm({ isOpen, onClose, onSaved, tenant }: ClientModalProps) {
  const isEdit = !!tenant;

  const [name, setName] = useState(tenant?.name ?? "");
  const [contactEmail, setContactEmail] = useState(tenant?.contact_email ?? "");
  const [address, setAddress] = useState(tenant?.address ?? "");
  const [providers, setProviders] = useState<string[]>(tenant?.providers ?? []);
  const [touched, setTouched] = useState(false);

  const { data: settings } = useGetAppSettingsQuery();
  const providerGroups = settings?.project.llm_providers ?? [];

  const [createTenant, { isLoading: creating }] = useCreateTenantMutation();
  const [updateTenant, { isLoading: updating }] = useUpdateTenantMutation();
  const submitting = creating || updating;

  const nameValid = !!name.trim();
  const emailValid = EMAIL_RE.test(contactEmail);
  const providersValid = providers.length > 0;
  const valid = nameValid && emailValid && providersValid;

  const toggleProvider = (provider: string) =>
    setProviders((p) =>
      p.includes(provider) ? p.filter((x) => x !== provider) : [...p, provider],
    );

  const handleSubmit = async () => {
    setTouched(true);
    if (!valid) return;

    try {
      if (tenant) {
        const body: UpdateTenantRequest = {};
        if (name.trim() !== tenant.name) body.name = name.trim();
        if (contactEmail.trim() !== (tenant.contact_email ?? ""))
          body.contact_email = contactEmail.trim();
        if (address.trim() !== (tenant.address ?? ""))
          body.address = address.trim();
        if (!sameProviders(providers, tenant.providers))
          body.providers = providers;

        // Nothing edited — the backend would reject an empty body.
        if (Object.keys(body).length === 0) {
          onClose();
          return;
        }

        const response = await updateTenant({
          tenantId: tenant.id,
          body,
        }).unwrap();
        onSaved(response.data, "updated");
        return;
      }

      const response = await createTenant({
        name: name.trim(),
        contact_email: contactEmail.trim(),
        address: address.trim(),
        status: "active",
        providers,
      }).unwrap();
      onSaved(response.data, "created");
    } catch {
      // Error toast is already shown by the shared RTK Query error handler.
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={isEdit ? "Update client" : "New client"}
      subtitle={
        isEdit
          ? "Update this client's details and the providers its projects can run on."
          : "Create a client (tenant) to scope projects, providers, and credentials."
      }
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} loading={submitting}>
            {isEdit ? "Save changes" : "Create client"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <Input
          label="Client name"
          placeholder="e.g. Acme Corp"
          required
          error={touched && !nameValid}
          errorMessage="Client name is required."
          value={name}
          onChange={(e) => setName(e.target.value)}
          autoFocus
        />
        <Input
          label="Contact email"
          placeholder="admin@acme.com"
          required
          error={touched && !emailValid}
          errorMessage="Enter a valid email address."
          value={contactEmail}
          onChange={(e) => setContactEmail(e.target.value)}
        />
        <Input
          label="Address (Optional)"
          placeholder="e.g. 123 Main St, Springfield"
          value={address}
          onChange={(e) => setAddress(e.target.value)}
        />

        <div className="flex flex-col gap-1">
          <FieldLabel label="Providers" required />
          {providerGroups.length === 0 ? (
            <div className="text-[12px] text-mut">
              No providers available yet.
            </div>
          ) : (
            <div className="space-y-2">
              {providerGroups.map((g) => {
                const on = providers.includes(g.id);
                const label = g.name;
                return (
                  <button
                    type="button"
                    key={g.id}
                    onClick={() => toggleProvider(g.id)}
                    aria-pressed={on}
                    className="flex w-full items-center justify-between rounded-md border border-border px-3.5 py-2.5"
                  >
                    <span className="text-[13px] font-semibold">{label}</span>
                    <span
                      aria-hidden="true"
                      className={`grid h-[18px] w-[18px] place-items-center rounded-[5px] border text-[11px] ${on ? "border-accent bg-accent text-white" : "border-border-strong"}`}
                    >
                      {on ? "✓" : ""}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
          {touched && !providersValid && (
            <div className="text-xs text-[var(--error)]">
              Select at least one provider.
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
}

export default ClientModal;
