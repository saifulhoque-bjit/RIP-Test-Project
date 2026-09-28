import { useState } from "react";
import Loader from "@/components/common/Loader";
import {
  useGetProjectsQuery,
  useGetAllProjectsQuery,
  useCreateProjectMutation,
} from "@/services/api/modules/projects";
import { useGetAppSettingsQuery } from "@/services/api/modules/settings";
import { useLazyGetTenantLlmProvidersQuery } from "@/services/api/modules/llmProviders";
import { ProviderKeyModal } from "@/features/Settings/ProviderKeyModal";
import {
  getProviderIssue,
  getProviderSetupIssue,
  getAdminProviderMessage,
  getAdminProviderSetupMessage,
  getMemberProviderMessage,
  providerLabel,
} from "@/utils/llmProviderIssue";
import { PageHeaderPortal } from "@/components/common/PageHeaderPortal";
import { useActiveTenant } from "@/hooks/useActiveTenant";
import { useAppSelector } from "@/store/hooks";
import { USER_ROLE } from "@/types/auth";
import type { Project, ProjectType } from "@/types";
import Button from "@/components/common/Button/Button";
import Input from "@/components/common/Input";
import { toast } from "@/lib/toast";
import { getErrorMessage } from "@/utils/getErrorMessage";
import Dropdown, { type DropdownOption } from "@/components/common/Dropdown";
import TabButtons from "@/components/common/TabButtons";
import SearchInput from "@/components/common/SearchInput";
import ProjectCard from "../../features/Projects/ProjectCard";
import Modal from "@/components/common/Modal/index";
import EmptyState from "@/components/common/EmptyState/EmptyState";
import { PermissionGate } from "@/components/common/PermissionGate";
import { PERMISSION } from "@/constants/permissions";
import { Pagination } from "@/components/common/Pagination";
import { PlusIcon } from "@/assets/icons/PlusIcon";

const PAGE_LIMIT = 20;
const PROJECT_NAME_MAX_LENGTH = 60;

export default function ProjectList() {
  const [currentSkip, setCurrentSkip] = useState<number>(0);
  // `search` (committed) drives the API call; `searchInput` is the raw text
  // the user is typing — kept separate so keystrokes don't trigger a fetch
  // on every character, only on Enter (or when the field is cleared).
  const [search, setSearch] = useState<string>("");
  const [searchInput, setSearchInput] = useState<string>("");
  const [projectCreateModal, setProjectCreateModal] = useState<boolean>(false);
  const [projectName, setProjectName] = useState<string>("");
  const [ingestionType, setIngestionType] = useState<ProjectType>("rfp");
  const [projectDescription, setProjectDescription] = useState<string>("");
  const [selectedLLMProvider, setSelectedLLMProvider] = useState<string>("");
  const [selectedLLMModel, setSelectedLLMModel] = useState<string>("");
  const [projectNameLimitError, setProjectNameLimitError] = useState(false);
  const [isCheckingProviders, setIsCheckingProviders] = useState(false);

  const [createProject, { isLoading: isCreating }] = useCreateProjectMutation();
  const [triggerLlmProviders] = useLazyGetTenantLlmProvidersQuery();
  const [providerNotice, setProviderNotice] = useState<{
    message: string;
    canManage: boolean;
  } | null>(null);
  const { data: settings } = useGetAppSettingsQuery();
  const { activeTenant } = useActiveTenant();
  const user = useAppSelector((s) => s.auth.user);
  const roles = user?.roles ?? [];
  // Admin/super_admin browse every project on a tenant; members only ever
  // see their own assigned projects, scoped server-side.
  const seesAllTenantProjects =
    roles.includes(USER_ROLE.SUPER_ADMIN) ||
    roles.includes(USER_ROLE.CLIENT_ADMIN);
  const isClientAdmin = roles.includes(USER_ROLE.CLIENT_ADMIN);

  const allProviderGroups = settings?.project.llm_providers ?? [];
  // Only offer the LLM providers the active tenant is actually allowed to use.
  const allowedProviders = activeTenant?.providers ?? [];
  const providerGroups = allProviderGroups.filter((providerGroup) =>
    allowedProviders.includes(providerGroup.id),
  );

  const llmProviderOptions: DropdownOption[] = providerGroups.map(
    (providerGroup) => ({
      label: providerGroup.name,
      value: providerGroup.id,
    }),
  );

  const effectiveSelectedLLMProvider =
    selectedLLMProvider || providerGroups[0]?.id || "";

  const selectedProviderGroup =
    providerGroups.find(
      (providerGroup) => providerGroup.id === effectiveSelectedLLMProvider,
    ) || providerGroups[0];

  const llmModelOptions: DropdownOption[] = (
    selectedProviderGroup?.models ?? []
  ).map((model) => ({
    label: model.name,
    value: model.id,
  }));

  const hasSelectedModelInProvider = llmModelOptions.some(
    (option) => option.value === selectedLLMModel,
  );

  const effectiveSelectedLLMModel = hasSelectedModelInProvider
    ? selectedLLMModel
    : llmModelOptions[0]?.value || "";
  const resetCreateModal = () => {
    setProjectName("");
    setProjectNameLimitError(false);
    setIngestionType("rfp");
    setProjectDescription("");
    setSelectedLLMProvider("");
    setSelectedLLMModel("");
    setProjectCreateModal(false);
  };

  const handleNewProject = async () => {
    if (!activeTenant?.id) return;

    setIsCheckingProviders(true);
    try {
      const providersResult = await triggerLlmProviders(
        activeTenant.id,
      ).unwrap();
      const providerItems = providersResult.data?.items ?? [];
      const providerSetupIssue = getProviderSetupIssue(
        providerItems.map((provider) => provider.provider),
        providerItems,
      );

      if (providerSetupIssue) {
        const message = getAdminProviderSetupMessage(providerSetupIssue);
        setProviderNotice({ message, canManage: isClientAdmin });
        return;
      }

      setProjectCreateModal(true);
    } catch (error) {
      const message = getErrorMessage(error, "");
      if (message) toast.error(message);
    } finally {
      setIsCheckingProviders(false);
    }
  };

  const handleCreateProject = async () => {
    if (!projectName.trim() || projectNameLimitError) return;

    setIsCheckingProviders(true);
    try {
      const providersResult = await triggerLlmProviders(
        activeTenant?.id ?? "",
      ).unwrap();
      const providerItems = providersResult.data?.items ?? [];
      const providerIssue = getProviderIssue(
        effectiveSelectedLLMProvider,
        providerItems,
      );

      if (providerIssue) {
        const label = providerLabel(effectiveSelectedLLMProvider);
        const message = isClientAdmin
          ? getAdminProviderMessage(providerIssue, label)
          : getMemberProviderMessage(providerIssue, label);
        resetCreateModal();
        setProviderNotice({ message, canManage: isClientAdmin });
        return;
      }

      await createProject({
        name: projectName.trim(),
        project_type: ingestionType,
        description: projectDescription.trim() || undefined,
        llm_provider: effectiveSelectedLLMProvider || undefined,
        llm_model: effectiveSelectedLLMModel || undefined,
      }).unwrap();
      toast.success("Project created successfully.");
      resetCreateModal();
    } catch (error) {
      toast.error(
        getErrorMessage(error, "Failed to create project. Please try again."),
      );
    } finally {
      setIsCheckingProviders(false);
    }
  };

  const activeTenantId = activeTenant?.id ?? null;

  const allProjectsResult = useGetAllProjectsQuery(
    {
      skip: currentSkip,
      limit: PAGE_LIMIT,
      search: search || undefined,
      tenant_id: activeTenantId ?? undefined,
    },
    {
      skip: !seesAllTenantProjects || !activeTenantId,
      refetchOnMountOrArgChange: true,
    },
  );
  const ownProjectsResult = useGetProjectsQuery(
    { skip: currentSkip, limit: PAGE_LIMIT, search: search || undefined },
    { skip: seesAllTenantProjects, refetchOnMountOrArgChange: true },
  );

  const { data, isLoading, isFetching } = seesAllTenantProjects
    ? allProjectsResult
    : ownProjectsResult;

  const isProjectsLoading =
    isLoading || isFetching || (seesAllTenantProjects && !activeTenantId);

  const projects = data?.data?.items ?? [];
  const projectCount = projects.length;
  const totalProjects = data?.data?.total ?? projectCount;

  return (
    <>
      <PageHeaderPortal>
        <div className="shrink-0 border-b border-[var(--border-primary)] bg-[#eef1f6] px-6 py-4">
          <div className="mb-1.5 text-xs text-[var(--text-quaternary)]">
            {activeTenant?.name ?? "—"}
          </div>
          <div className="flex items-center gap-2.5">
            <h2 className="text-[19px] font-semibold text-[var(--text-primary)] m-0">
              Projects
            </h2>
            <span className="inline-flex items-center rounded-full bg-[var(--accent-50)] px-2 py-0.5 text-[11px] font-semibold text-[var(--accent)]">
              {totalProjects} {totalProjects === 1 ? "project" : "projects"}
            </span>
            <PermissionGate allow={PERMISSION.PROJECT_CREATE}>
              <div className="ml-auto flex gap-2.5">
                <Button
                  size="xs"
                  iconLeading={<PlusIcon className="w-3 h-3" />}
                  loading={isCheckingProviders}
                  disabled={isCheckingProviders || !activeTenant?.id}
                  onClick={() => void handleNewProject()}
                >
                  New project
                </Button>
              </div>
            </PermissionGate>
          </div>
        </div>
      </PageHeaderPortal>

      {/* Control bar */}
      <div className="flex items-center gap-3 mb-5">
        {/* Search input */}
        <SearchInput
          size="sm"
          placeholder="Search by project name"
          value={searchInput}
          onChange={(e) => {
            const value = e.target.value;
            setSearchInput(value);
            if (value === "") {
              setSearch("");
              setCurrentSkip(0);
            }
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              setSearch(searchInput.trim());
              setCurrentSkip(0);
            }
          }}
        />
      </div>

      {/* Project grid / loading / empty */}
      {isProjectsLoading ? (
        <div
          className="flex flex-col justify-center items-center gap-2 text-[var(--text-quaternary)] font-medium py-10"
          role="status"
          aria-live="polite"
        >
          <Loader ariaLabel="Loading projects" subtitle="Loading projects..." />
        </div>
      ) : projects.length === 0 ? (
        <div className="text-center text-[var(--text-secondary)] py-10 text-sm">
          <EmptyState
            title="No Project Found!"
            description="Please add a project to implement AI-based requirement processing for enhanced functionality and automation."
          />
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-[18px]">
          {projects.map((project: Project) => (
            <ProjectCard key={project.id} project={project} />
          ))}
        </div>
      )}

      {totalProjects > PAGE_LIMIT && (
        <Pagination
          skip={currentSkip}
          limit={PAGE_LIMIT}
          total={totalProjects}
          onSkipChange={setCurrentSkip}
          itemLabel="projects"
          className="mt-4"
        />
      )}

      {/* Project Create Modal */}
      <Modal
        isOpen={projectCreateModal}
        onClose={resetCreateModal}
        title="Create New Project"
        className="!h-auto"
        footer={
          <>
            <Button
              variant="ghost"
              size="md"
              fullWidth
              disabled={isCreating}
              onClick={resetCreateModal}
            >
              Cancel
            </Button>
            <Button
              size="md"
              fullWidth
              loading={isCreating}
              disabled={
                !projectName.trim() ||
                projectNameLimitError ||
                !effectiveSelectedLLMProvider ||
                !effectiveSelectedLLMModel ||
                isCheckingProviders ||
                isCreating
              }
              onClick={() => void handleCreateProject()}
            >
              Create
            </Button>
          </>
        }
      >
        <div className="flex flex-col items-center gap-5">
          <Input
            label="Project Name"
            placeholder="New Project"
            required
            maxLength={PROJECT_NAME_MAX_LENGTH}
            error={projectNameLimitError}
            errorMessage={`Project name must be ${PROJECT_NAME_MAX_LENGTH} characters or fewer.`}
            value={projectName}
            onChange={(e) => {
              setProjectName(e.target.value);
              if (e.target.value.length < PROJECT_NAME_MAX_LENGTH) {
                setProjectNameLimitError(false);
              }
            }}
            onKeyDown={(e) => {
              const isTextKey = e.key.length === 1;
              const isModified = e.ctrlKey || e.metaKey || e.altKey;
              if (
                projectName.length >= PROJECT_NAME_MAX_LENGTH &&
                isTextKey &&
                !isModified
              ) {
                setProjectNameLimitError(true);
              }
            }}
            onPaste={(e) => {
              const remainingChars =
                PROJECT_NAME_MAX_LENGTH - projectName.length;
              if (e.clipboardData.getData("text").length > remainingChars) {
                setProjectNameLimitError(true);
              }
            }}
          />
          <div className="w-full">
            <div className="mb-1.5 text-[13px] font-semibold">Ingestion</div>
            <TabButtons
              items={[
                { label: "📄 RFP", value: "rfp" },
                { label: "‹/› Source Code", value: "source_code" },
              ]}
              value={ingestionType}
              onChange={setIngestionType}
            />
          </div>
          <div className="flex gap-3 w-full">
            <Dropdown
              label="Select LLM Provider"
              width="100%"
              options={llmProviderOptions}
              selected={effectiveSelectedLLMProvider}
              onChange={(option) => {
                setSelectedLLMProvider(option?.value || "");
                setSelectedLLMModel("");
              }}
            />
            <Dropdown
              label="Select LLM Model"
              width="100%"
              options={llmModelOptions}
              selected={effectiveSelectedLLMModel}
              onChange={(option) => setSelectedLLMModel(option?.value || "")}
            />
          </div>
        </div>
      </Modal>

      <ProviderKeyModal
        isOpen={!!providerNotice}
        onClose={() => setProviderNotice(null)}
        message={providerNotice?.message ?? ""}
        canManage={providerNotice?.canManage ?? false}
      />
    </>
  );
}
