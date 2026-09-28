import { useState } from "react";
import { toast } from "@/lib/toast";

import Card from "@/components/common/Card";
import Input from "@/components/common/Input";
import TextArea from "@/components/common/TextArea";
import Dropdown, { type DropdownOption } from "@/components/common/Dropdown";
import Button from "@/components/common/Button/Button";
import { useUpdateProjectMutation } from "@/services/api/modules/projects";
import { useGetAppSettingsQuery } from "@/services/api/modules/settings";
import { getErrorMessage } from "@/utils/getErrorMessage";
import { hasRole } from "@/utils/hasRole";
import { USER_ROLE } from "@/types/auth";
import type { Project } from "@/types/project";

export function IdentitySection({ project }: { project: Project }) {
  // Only the client admin role can edit project settings — super_admin and
  // member both get a read-only view here.
  const isAdmin = hasRole(USER_ROLE.CLIENT_ADMIN);

  const { data: settings } = useGetAppSettingsQuery();
  const providerGroups = settings?.project.llm_providers ?? [];

  const [updateProject, { isLoading: isSaving }] = useUpdateProjectMutation();

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [hydratedFor, setHydratedFor] = useState<string | null>(null);

  // Populate the form from the loaded project once per project (not on every
  // render) — the standard "derive state from props" pattern, no effect needed.
  if (hydratedFor !== project.id) {
    setName(project.name ?? "");
    setDescription(project.description ?? "");
    setProvider(project.llm_provider ?? "");
    setModel(project.llm_model ?? "");
    setHydratedFor(project.id);
  }

  const providerOptions: DropdownOption[] = providerGroups.map((g) => ({
    label: g.name,
    value: g.id,
  }));

  const selectedProviderGroup = providerGroups.find((g) => g.id === provider);
  const modelOptions: DropdownOption[] = (
    selectedProviderGroup?.models ?? []
  ).map((m) => ({ label: m.name, value: m.id }));

  const isDirty =
    name !== (project.name ?? "") ||
    description !== (project.description ?? "") ||
    provider !== (project.llm_provider ?? "") ||
    model !== (project.llm_model ?? "");

  const handleCancel = () => {
    setName(project.name ?? "");
    setDescription(project.description ?? "");
    setProvider(project.llm_provider ?? "");
    setModel(project.llm_model ?? "");
  };

  const handleSave = async () => {
    if (!name.trim()) return;
    try {
      await updateProject({
        projectId: project.id,
        body: {
          name: name.trim(),
          description: description.trim() || undefined,
          llm_provider: provider || undefined,
          llm_model: model || undefined,
        },
      }).unwrap();
      toast.success("Project settings saved.");
    } catch (error) {
      toast.error(
        getErrorMessage(
          error,
          "Failed to save project settings. Please try again.",
        ),
      );
    }
  };

  return (
    <>
      <Card className="mb-[18px]">
        <header className="border-b border-[var(--border-primary)] px-[18px] py-3.5">
          <h3 className="text-[15px] font-semibold text-[var(--text-primary)]">
            Identity
          </h3>
          <p className="mt-0.5 text-[12.5px] text-[var(--text-tertiary)]">
            Basic project metadata and the provider this project runs on.
          </p>
        </header>

        <div className="p-[18px]">
          <div className="grid grid-cols-1 gap-[18px] md:grid-cols-2">
            <Input label="Project ID" value={project.id} disabled />
            <Input
              label="Name"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={!isAdmin}
            />
            <Dropdown
              label="Provider"
              width="100%"
              options={providerOptions}
              selected={provider}
              onChange={(o) => {
                setProvider(o.value);
                setModel("");
              }}
              placeholder="Select a provider…"
              disabled={!isAdmin}
            />
            <Dropdown
              label="Model"
              width="100%"
              options={modelOptions}
              selected={model}
              onChange={(o) => setModel(o.value)}
              placeholder="Select a model…"
              disabled={!isAdmin || !provider}
            />
          </div>

          <div className="mt-[18px]">
            <TextArea
              label="Description"
              rows={3}
              required
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What this project is for…"
              disabled={!isAdmin}
            />
          </div>
        </div>
      </Card>

      {isAdmin && (
        <div className="flex justify-end gap-2.5 pb-[18px]">
          <Button
            variant="ghost"
            size="sm"
            disabled={!isDirty || isSaving}
            onClick={handleCancel}
          >
            Cancel
          </Button>
          <Button
            size="sm"
            disabled={!isDirty || !name.trim()}
            loading={isSaving}
            onClick={() => void handleSave()}
          >
            Save changes
          </Button>
        </div>
      )}
    </>
  );
}
