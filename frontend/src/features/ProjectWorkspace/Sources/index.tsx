import type { DragEvent, KeyboardEvent } from "react";
import {
  useGetProjectQuery,
  // useUpdateProjectMutation,
} from "@/services/api/modules/projects";
import { selectIsAuthenticated } from "@/store/slices/authSlice";
import { useAppSelector } from "@/store/hooks";
import { useNavigate, useParams } from "react-router-dom";
import { useGetIngestionListQuery } from "@/services/api/modules/sources";
import { useGetTenantLlmProvidersQuery } from "@/services/api/modules/llmProviders";
import { useActiveTenant } from "@/hooks/useActiveTenant";
import { USER_ROLE } from "@/types/auth";
import { ProviderKeyModal } from "@/features/Settings/ProviderKeyModal";
import {
  getProviderIssue,
  getAdminProviderMessage,
  getMemberProviderMessage,
  providerLabel,
} from "@/utils/llmProviderIssue";
import { Table } from "@/components/common/Table";
import { formatDate } from "@/utils/formatDate";
import { formatFileSize } from "@/utils/formatFileSize";
import type { ProjectType } from "@/types";
import Button from "@/components/common/Button/Button";
import {
  useFileUpload,
  FILE_UPLOAD_ERRORS,
  type FileWithValidation,
} from "@/features/ProjectWorkspace/Sources/useFileUpload";
import { useRef, useState } from "react";
import { getAllowedExtensions } from "@/features/ProjectWorkspace/Sources/getAllowedExtensions";
import type { CodeBaseFormData } from "@/features/ProjectWorkspace/Sources/components/CodeBaseUploadForm";
import { useDispatch } from "react-redux";
import { baseApi } from "@/services/api/baseApi";
import type { AppDispatch } from "@/store";
import { Switch } from "@/components/ui/switch";
import UploadFileItem from "@/components/common/UploadFileItem";
import CodeBaseUploadForm from "@/features/ProjectWorkspace/Sources/components/CodeBaseUploadForm";
import { Dropdown } from "@/components/common/Dropdown";
import TextArea from "@/components/common/TextArea";
import FileDragDrop from "@/features/ProjectWorkspace/Sources/components/FileDragDrop";
import IngestionPanel from "@/features/ProjectWorkspace/Sources/components/IngestionPanel";
import { useIsProjectBusy } from "@/hooks/useProjectTaskStatus";
import {
  createSourceColumns,
  type ProjectSourceTableRow,
} from "@/features/ProjectWorkspace/Sources/sourceColumns";
import { contextModeOptions, incrementalUpdateTypes } from "./staticData";
import Modal from "@/components/common/Modal/index";
import { InfoNote } from "@/components/common/Note";
import FieldLabel from "@/components/common/FieldLabel";
import { RadioGroup } from "@/components/common/RadioGroup";

// The native <input accept> attribute only filters the OS file picker
// dialog — it does nothing for drag-and-drop, so every entry point has to
// run this check itself before queuing files for upload.
function splitFilesByExtension(
  files: File[],
  allowedExtensions: string[] | undefined,
): { accepted: File[]; rejected: FileWithValidation[] } {
  if (!allowedExtensions || allowedExtensions.length === 0) {
    return { accepted: files, rejected: [] };
  }

  const allowed = new Set(allowedExtensions.map((ext) => ext.toLowerCase()));
  const accepted: File[] = [];
  const rejected: FileWithValidation[] = [];

  files.forEach((file) => {
    const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
    if (allowed.has(extension)) {
      accepted.push(file);
    } else {
      rejected.push({ file, error: FILE_UPLOAD_ERRORS.INVALID_TYPE });
    }
  });

  return { accepted, rejected };
}

export default function Sources() {
  const { id: projectId } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const dispatch = useDispatch<AppDispatch>();
  const isAuthenticated = useAppSelector(selectIsAuthenticated);

  // ─── Fetch Project Details ─────────────────────────────────────
  const { data: projectResponse } = useGetProjectQuery(projectId!, {
    skip: !projectId || !isAuthenticated,
    // User stories can be approved elsewhere (Review page) without this
    // page's cached Project entry being invalidated — force a fresh fetch
    // on every visit so user_stories/approved_user_stories stay accurate.
    refetchOnMountOrArgChange: true,
  });
  const project = projectResponse?.data;
  const files = project?.files ?? 0;

  const projectType: "rfp" | "source_code" =
    project?.project_type === "source_code" ? "source_code" : "rfp";
  const enableIncrementalUpload =
    !!project &&
    projectType == "rfp" &&
    (project.user_stories ?? 0) > 0 &&
    project.user_stories === project.approved_user_stories;

  // ─── LLM provider key check ─────────────────────────────────────
  // Uploading/processing requires the project's LLM provider to have a
  // saved, verified, active key on the tenant — checked upfront so the
  // upload buttons can block before a doomed request reaches the server.
  const { activeTenant } = useActiveTenant();
  const { data: providerStatusResponse } = useGetTenantLlmProvidersQuery(
    activeTenant?.id ?? "",
    { skip: !activeTenant?.id },
  );
  const roles = useAppSelector((s) => s.auth.user?.roles) ?? [];
  const isClientAdmin = roles.includes(USER_ROLE.CLIENT_ADMIN);
  const [providerNotice, setProviderNotice] = useState<{
    message: string;
    canManage: boolean;
  } | null>(null);

  const ensureProviderReady = (): boolean => {
    const issue = getProviderIssue(
      project?.llm_provider,
      providerStatusResponse?.data?.items,
    );
    if (!issue) return true;

    const label = providerLabel(project!.llm_provider!);
    const message = isClientAdmin
      ? getAdminProviderMessage(issue, label)
      : getMemberProviderMessage(issue, label);
    setProviderNotice({ message, canManage: isClientAdmin });
    return false;
  };

  const [selectedCategory, setSelectedCategory] = useState<ProjectType | null>(
    null,
  );
  // ─── Update Project ─────────────────────────────────────
  // const [updateProject] = useUpdateProjectMutation();
  // ─── Allowed for project type: source_code ─────────────────────────────────────
  const [codeBaseFormData, setCodeBaseFormData] =
    useState<CodeBaseFormData | null>(null);
  // Temporary Solution: to skip AI processing
  const [skipProcessing, setSkipProcessing] = useState<boolean>(
    import.meta.env.VITE_ENV === "development",
  );
  // ─── Allowed for incremental process only ─────────────────────────────────────
  const [userMessage, setUserMessage] = useState<string | undefined>();
  const [isDragActive, setIsDragActive] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const [selectedUpdateType, setSelectedUpdateType] = useState<string>(
    incrementalUpdateTypes[0].value,
  );
  const [contextMode, setContextMode] = useState<"full" | "subset">("full");

  // ─── Source data and filters ─────────────────────────────────────
  const {
    data: ingestionResponse,
    isLoading: isIngestionLoading,
    isFetching: isIngestionFetching,
    refetch: refetchSources,
  } = useGetIngestionListQuery(
    { projectId: projectId!, skip: 0, limit: 100 },
    { skip: !projectId || !isAuthenticated },
  );

  // Feedback-driven regeneration runs ("requirement_update_from_feedback")
  // appear in this list too, but they don't ingest any files — filter them
  // out so they don't show as an empty row in the table or count as a real
  // ingested source for retry/incremental-update gating.
  const ingestionItems = (ingestionResponse?.data?.items ?? []).filter(
    (item) => item.sources.length > 0,
  );

  const isSourcesLoading = isIngestionLoading || isIngestionFetching;
  const shouldShowInitialLoader = isIngestionLoading && !ingestionResponse;

  const isIncrementalProcess = files > 0;

  // Any in-progress WebSocket task for this project disables ingestion actions.
  const isProjectBusy = useIsProjectBusy(projectId);

  // ─── File upload logic ─────────────────────────────────────
  const {
    isUploading,
    fileUploadModal,
    setFileUploadModal,
    uploadModalFiles,
    setUploadModalFiles,
    fileUploadErrors,
    setFileUploadErrors,
    fileUploadSuccesses,
    setFileUploadSuccesses,
    handleFilesQueuedForUpload,
    handleFilesRejected,
    handleFilesSelected,
    handleRemoveUploadFile,
  } = useFileUpload({
    projectId,
    isIncrementalUpload: isIncrementalProcess,
    projectType,
    codeBaseFormData,
    refetchSources,
    onUploadBegin: () => {
      setSelectedCategory(null);
    },
    onUploadSuccess: () => {
      dispatch(
        baseApi.util.invalidateTags([
          // Project LIST caches `files`/`status` per project, which drives the
          // "Not started" badge in SubHeader's ProjectSwitcher dropdown (and
          // ProjectList/Dashboard) — the task-type switch in
          // useProjectTasksSocket never invalidates this tag, so it has to be
          // done manually here or those views show stale data after upload.
          // The switcher subscribes only while its dropdown is open, so this
          // drops its cached list rather than refetching it; the next open
          // fetches fresh, which is what the badge needs.
          { type: "Project", id: "LIST" },
          { type: "IngestionJob", id: `LIST-${projectId}` },
        ]),
      );
      navigate(`/projects/${projectId}/pipelines`);
    },
    skipProcessing,
    userMessage,
    selectedUpdateType,
    contextMode,
  });

  const effectiveAllowedExtensions = getAllowedExtensions({
    incrementalProcess: isIncrementalProcess,
    selectedCategory: selectedCategory ?? project?.project_type ?? null,
  });

  const acceptValue = effectiveAllowedExtensions
    ?.map((ext) => `.${ext}`)
    .join(",");

  const queueFilesForUpload = (files: File[]) => {
    const { accepted, rejected } = splitFilesByExtension(
      files,
      effectiveAllowedExtensions,
    );
    if (rejected?.length > 0)
      handleFilesRejected(rejected, effectiveAllowedExtensions);
    if (accepted.length > 0) handleFilesQueuedForUpload(accepted);
  };

  const handleInputChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files ? Array.from(event.target.files) : [];
    if (files.length > 0) {
      queueFilesForUpload(files);
    }
    event.target.value = "";
  };

  const isDisabledState = isUploading;

  const openFileDialog = () => {
    if (!isDisabledState) {
      inputRef.current?.click();
    }
  };

  const handleDropZoneKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openFileDialog();
    }
  };

  const handleDropZoneDragOver = (event: DragEvent<HTMLElement>) => {
    event.preventDefault();
    if (!isDisabledState) {
      setIsDragActive(true);
    }
  };

  const handleDropZoneDragLeave = (event: DragEvent<HTMLElement>) => {
    event.preventDefault();
    setIsDragActive(false);
  };

  const handleDropZoneDrop = (event: DragEvent<HTMLElement>) => {
    event.preventDefault();
    setIsDragActive(false);
    if (isDisabledState) return;

    const files = Array.from(event.dataTransfer.files ?? []);
    if (files.length > 0) {
      queueFilesForUpload(files);
    }
  };

  const sourceRows: ProjectSourceTableRow[] = ingestionItems?.map((item) => ({
    id: item.id,
    sourceName: item.sources.map((s) => s.original_name),
    type: item.source_type,
    size: item.sources.map((s) => formatFileSize(s.file_size_bytes)),
    ingestedDate: formatDate(item.created_at),
    status: item.status,
  }));

  const sourceColumns = createSourceColumns({
    onReviewClick: () => navigate(`/projects/${projectId}/review`),
  });

  const handleCodeBaseFormChange = (formData: CodeBaseFormData) => {
    setCodeBaseFormData(formData);
  };

  // ─── Determine upload scenario ─────────────────────────────────────
  const uploadScenario = isIncrementalProcess
    ? "incremental"
    : selectedCategory === "rfp"
      ? "rfp-initial"
      : selectedCategory === "source_code"
        ? "codebase-initial"
        : null;

  const getModalTitle = (): string => {
    switch (uploadScenario) {
      case "rfp-initial":
        return "Upload RFP Target Documents";
      case "codebase-initial":
        return "Codebase Ingest Configuration";
      case "incremental":
        return "Ingest a requirement update";
      default:
        return "Upload Files";
    }
  };

  // ─── Determine which fields should be shown ─────────────────────────
  const showSkipProcessing =
    import.meta.env.VITE_ENV === "development" &&
    (uploadScenario === "rfp-initial" ||
      uploadScenario === "codebase-initial" ||
      uploadScenario === "incremental");
  const showUpdateTypeDropdown =
    uploadScenario === "incremental" && projectType === "rfp";
  const showContextModeSelection =
    uploadScenario === "incremental" && projectType === "rfp";
  const showCodeBaseForm =
    selectedCategory === "source_code" &&
    (uploadScenario === "codebase-initial" || uploadScenario === "incremental");
  const showUserNote = uploadScenario === "incremental";

  return (
    <>
      <div className="flex flex-col gap-5">
        <section className="flex flex-col gap-4">
          {shouldShowInitialLoader ? (
            <div className="flex min-h-[220px] items-center justify-center rounded-lg border border-[var(--border-primary)] bg-white text-sm text-[var(--text-tertiary)]">
              Loading source files...
            </div>
          ) : (
            <div>
              {sourceRows.length <= 0 && (
                <InfoNote className="mb-4">
                  Ingested material for this project. In MVP,{" "}
                  <b>RFP and incremental updates accept PDF only</b>;
                  source-code projects ingest a single code folder/archive.
                  Incremental updates are processed as a<b> change-set</b>{" "}
                  against the current requirements.
                </InfoNote>
              )}

              <Table
                title="Project sources"
                columns={sourceColumns}
                data={sourceRows}
                emptyMessage={
                  isSourcesLoading
                    ? "Loading source files..."
                    : "No source files found"
                }
                striped
              />
            </div>
          )}
        </section>

        <IngestionPanel
          projectType={projectType}
          hasExistingSource={isIncrementalProcess}
          onUploadInitial={() => {
            if (!ensureProviderReady()) return;
            setSelectedCategory(projectType);
            setFileUploadModal(true);
          }}
          onIngestUpdate={() => {
            if (!ensureProviderReady()) return;
            setFileUploadModal(true);
          }}
          enableIncrementalUpload={enableIncrementalUpload}
          isBusy={isProjectBusy}
        />
      </div>

      <ProviderKeyModal
        isOpen={!!providerNotice}
        onClose={() => setProviderNotice(null)}
        message={providerNotice?.message ?? ""}
        canManage={providerNotice?.canManage ?? false}
      />

      {/* ─── File Upload Modal ───────────────────────────────────────────────────── */}
      {project && (
        <Modal
          isOpen={fileUploadModal}
          onClose={() => {
            setFileUploadModal(false);
            setUploadModalFiles([]);
            setFileUploadErrors({});
            setFileUploadSuccesses(new Set());
          }}
          disableBackdropClose
          title={getModalTitle()}
          footer={
            <span className="flex flex-row justify-center items-end gap-3">
              <Button
                type="button"
                variant="ghost"
                size="md"
                className="flex-1"
                onClick={() => {
                  setFileUploadModal(false);
                  setUploadModalFiles([]);
                  setFileUploadErrors({});
                  setFileUploadSuccesses(new Set());
                }}
              >
                Cancel
              </Button>
              <Button
                type="button"
                size="md"
                variant="primary"
                loading={isUploading}
                disabled={uploadModalFiles.length === 0}
                className="flex-shrink-0"
                onClick={() => void handleFilesSelected(uploadModalFiles)}
              >
                Upload & Queue Extraction
              </Button>
            </span>
          }
          className="!h-auto"
        >
          <div className="flex flex-col items-start gap-4">
            {/* ─── Scenarios 1, 2 & 3: RFP/Codebase Initial and Incremental Upload ── */}
            {showSkipProcessing && (
              <Switch
                label="Skip Processing"
                checked={skipProcessing}
                onCheckedChange={setSkipProcessing}
              />
            )}

            {/* ─── Scenario 3: Incremental Upload ─────────────────────────────────── */}
            {showUpdateTypeDropdown && (
              <Dropdown
                placeholder="Select update type"
                width="100%"
                label="Update type"
                options={incrementalUpdateTypes}
                selected={selectedUpdateType}
                onChange={(option) => setSelectedUpdateType(option.value)}
              />
            )}

            {/* ─── Scenario 3: Incremental Upload (RFP only) ──────────────────────── */}
            {showContextModeSelection && (
              <div className="w-full flex flex-col">
                <FieldLabel label="Context mode" />
                <RadioGroup
                  name="context_mode"
                  options={contextModeOptions}
                  value={contextMode}
                  onChange={setContextMode}
                />
              </div>
            )}

            {/* ─── File Drag & Drop Section (All Scenarios) ──────────────────────── */}
            <FileDragDrop
              openFileDialog={openFileDialog}
              handleDropZoneKeyDown={handleDropZoneKeyDown}
              handleDropZoneDragOver={handleDropZoneDragOver}
              handleDropZoneDragLeave={handleDropZoneDragLeave}
              handleDropZoneDrop={handleDropZoneDrop}
              handleInputChange={handleInputChange}
              isDragActive={isDragActive}
              isDisabledState={isDisabledState}
              uploadScenario={uploadScenario}
              acceptValue={acceptValue}
              inputRef={inputRef}
            />

            {/* ─── Uploaded Files List (All Scenarios) ────────────────────────── */}
            {uploadModalFiles.length > 0 && (
              <ul className="w-full list-none p-0 m-0 flex flex-col items-start gap-3 max-h-[300px] overflow-y-auto">
                {uploadModalFiles.map((file, index) => (
                  <UploadFileItem
                    key={`${file.name}-${index}`}
                    file={file}
                    index={index}
                    onRemove={handleRemoveUploadFile}
                    isSuccess={fileUploadSuccesses.has(file.name)}
                    errorMessage={fileUploadErrors[file.name]?.message}
                  />
                ))}
              </ul>
            )}

            {/* ─── Scenario 3: Incremental Upload - User Note ──────────────────── */}
            {showUserNote && (
              <TextArea
                label="Note (optional)"
                placeholder="Add a note for the uploaded files..."
                rows={3}
                value={userMessage}
                onChange={(e) => setUserMessage(e.target.value)}
              />
            )}

            {/* ─── Scenario 2 & 3: Code Base Upload Form ────────────────────────── */}
            {showCodeBaseForm && (
              <CodeBaseUploadForm onFormChange={handleCodeBaseFormChange} />
            )}
          </div>
        </Modal>
      )}
    </>
  );
}
