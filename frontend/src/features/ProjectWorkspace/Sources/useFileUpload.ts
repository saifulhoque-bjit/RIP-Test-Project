import { useState, useEffect, useMemo } from "react";
import { toast } from "@/lib/toast";
import { API_ENDPOINTS } from "@/services/api/endpoints";
import type { ProjectType, SourceUploadResponse } from "@/types";
import type { CodeBaseFormData } from "./components/CodeBaseUploadForm";

const MAX_FILES = 20;

// File upload error types
export const FILE_UPLOAD_ERRORS = {
  INVALID_TYPE: "INVALID_TYPE",
  INVALID_SIZE: "INVALID_SIZE",
  UNKNOWN: "UNKNOWN",
} as const;

export type FileUploadError =
  (typeof FILE_UPLOAD_ERRORS)[keyof typeof FILE_UPLOAD_ERRORS];

export interface FileWithValidation {
  file: File;
  error?: FileUploadError;
  errorMessage?: string;
}

interface UseFileUploadOptions {
  projectId: string | undefined;
  isIncrementalUpload: boolean;
  projectType: ProjectType | null;
  codeBaseFormData: CodeBaseFormData | null;
  refetchSources: () => void;
  onUploadBegin?: () => void;
  onUploadSuccess: (
    sources: Array<{ sourceId: string; filename: string }>,
    projectType: ProjectType | null,
  ) => void;
  skipProcessing?: boolean;
  userMessage?: string;
  selectedUpdateType?: string;
  contextMode?: "full" | "subset";
}

export function useFileUpload({
  projectId,
  isIncrementalUpload,
  projectType,
  codeBaseFormData,
  refetchSources,
  onUploadBegin,
  onUploadSuccess,
  skipProcessing = false,
  userMessage,
  selectedUpdateType,
  contextMode,
}: UseFileUploadOptions) {
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [fileUploadModal, setFileUploadModal] = useState(false);
  const [uploadModalFiles, setUploadModalFiles] = useState<File[]>([]);
  const [fileUploadErrors, setFileUploadErrors] = useState<
    Record<string, { message: string; retryable: boolean }>
  >(() => {
    if (!projectId) return {};
    try {
      const raw = sessionStorage.getItem(`rip_upload_errors_${projectId}`);
      return raw
        ? (JSON.parse(raw) as Record<
            string,
            { message: string; retryable: boolean }
          >)
        : {};
    } catch {
      return {};
    }
  });
  const [fileUploadSuccesses, setFileUploadSuccesses] = useState<Set<string>>(
    new Set(),
  );

  // Persist upload errors to sessionStorage so the sheet survives a browser reload
  useEffect(() => {
    if (!projectId) return;
    const key = `rip_upload_errors_${projectId}`;
    if (Object.keys(fileUploadErrors).length > 0) {
      try {
        sessionStorage.setItem(key, JSON.stringify(fileUploadErrors));
      } catch {
        /* ignore quota errors */
      }
    } else {
      sessionStorage.removeItem(key);
    }
  }, [projectId, fileUploadErrors]);

  const failedUploadCount = Object.keys(fileUploadErrors).length;
  const hasFailedUploads = failedUploadCount > 0;
  const failedUploadFiles = useMemo(
    () =>
      uploadModalFiles.filter((file) =>
        Object.prototype.hasOwnProperty.call(fileUploadErrors, file.name),
      ),
    [uploadModalFiles, fileUploadErrors],
  );

  const resetUploadState = () => {
    setFileUploadErrors({});
    setFileUploadSuccesses(new Set());
  };

  const handleFilesQueuedForUpload = (files: File[]): void => {
    setUploadModalFiles((prev) => {
      const merged = [...prev];

      files.forEach((file) => {
        const exists = merged.some(
          (existing) =>
            existing.name === file.name &&
            existing.size === file.size &&
            existing.lastModified === file.lastModified,
        );
        if (!exists) {
          merged.push(file);
        }
      });

      if (merged.length > MAX_FILES) {
        toast.error("You cannot upload more than 20 files at a time.", {
          toastId: "max-file-upload",
        });
        return prev;
      }

      resetUploadState();
      setFileUploadModal(true);
      return merged;
    });
  };

  const handleFilesSelected = async (files: File[]): Promise<void> => {
    if (!projectId) return;
    resetUploadState();
    setIsUploading(true);
    setUploadProgress(0);
    onUploadBegin?.();

    try {
      const response = await new Promise<SourceUploadResponse>(
        (resolve, reject) => {
          const xhr = new XMLHttpRequest();
          const formData = new FormData();
          files.forEach((file) => formData.append("files", file));
          formData.append("project_id", projectId);
          formData.append("skip_processing", skipProcessing ? "True" : "False");
          formData.append(
            "is_incremental",
            isIncrementalUpload ? "True" : "False",
          );
          if (isIncrementalUpload && userMessage && userMessage.length > 0) {
            formData.append("user_message", userMessage);
          }
          formData.append(
            "source_type",
            isIncrementalUpload && projectType === "rfp"
              ? (selectedUpdateType ?? "")
              : (projectType ?? ""),
          );
          if (isIncrementalUpload && projectType === "rfp") {
            formData.append("context_mode", contextMode ?? "full");
          }

          if (projectType === "source_code") {
            formData.append(
              "source_layout_type",
              codeBaseFormData?.scArchitectureType ?? "auto",
            );
            formData.append(
              "source_language",
              codeBaseFormData?.sourceCodeLanguage ?? "",
            );
            formData.append("frontend_stack", codeBaseFormData?.frontend ?? "");
            formData.append("backend_stack", codeBaseFormData?.backend ?? "");
            formData.append(
              "infrastructure_stack",
              codeBaseFormData?.infrastructure ?? "",
            );
            formData.append(
              "architecture_stack",
              codeBaseFormData?.architecture ?? "",
            );
            formData.append("database_stack", codeBaseFormData?.database ?? "");
            formData.append(
              "coding_standard",
              codeBaseFormData?.codingStandards ?? "",
            );
            formData.append(
              "database_strategy",
              codeBaseFormData?.databaseStrategy ?? "",
            );
            formData.append(
              "architecture",
              codeBaseFormData?.architectureExpectation ?? "",
            );
            formData.append("security", codeBaseFormData?.security ?? "");
          }

          xhr.upload.onprogress = (event) => {
            if (event.lengthComputable) {
              setUploadProgress(Math.round((event.loaded / event.total) * 100));
            }
          };

          xhr.onload = () => {
            if (xhr.status >= 200 && xhr.status < 300) {
              try {
                resolve(JSON.parse(xhr.responseText) as SourceUploadResponse);
              } catch {
                reject(new Error("Invalid response from server"));
              }
            } else {
              let serverMessage = `Upload failed with status ${xhr.status}`;
              try {
                const body = JSON.parse(xhr.responseText) as {
                  message?: string;
                  detail?: string;
                };
                serverMessage = body.message ?? body.detail ?? serverMessage;
              } catch {
                // ignore parse errors — keep the status-based message
              }
              reject(new Error(serverMessage));
            }
          };

          xhr.onerror = () => reject(new Error("Network error during upload"));

          const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "/api";
          xhr.open("POST", `${baseUrl}${API_ENDPOINTS.SOURCES.UPLOAD}`);
          xhr.withCredentials = true;
          xhr.send(formData);
        },
      );

      const { results } = response.data;
      const failedResults = results.filter((r) => r.status_code !== 201);
      const succeededCount = results.filter(
        (r) => r.status_code === 201,
      ).length;

      if (succeededCount > 0) {
        refetchSources();

        const succeededSources = results
          .filter((r) => r.status_code === 201 && r.source_id)
          .map((r) => ({ sourceId: r.source_id!, filename: r.filename }));
        if (succeededSources?.length > 0) {
          onUploadSuccess(succeededSources, projectType);
        }
      }

      if (failedResults.length > 0) {
        const errors: Record<string, { message: string; retryable: boolean }> =
          {};
        failedResults.forEach((r) => {
          errors[r.filename] = {
            message: r.error ?? "Upload failed. Please try again.",
            retryable: r.status_code !== 409,
          };
        });
        setFileUploadErrors(errors);

        const succeededNames = new Set(
          results.filter((r) => r.status_code === 201).map((r) => r.filename),
        );
        setFileUploadSuccesses(succeededNames);

        if (succeededCount > 0) {
          toast.success(
            `${succeededCount} file(s) uploaded. ${failedResults.length} file(s) failed.`,
            { toastId: "partial-upload" },
          );
        } else if (failedResults.length === 1) {
          toast.error(
            failedResults[0].error ?? "Upload failed. Please try again.",
            { toastId: "upload-failed" },
          );
        } else if (failedResults.every((r) => r.status_code === 409)) {
          toast.error(
            `${failedResults.length} file(s) already exist in this project.`,
            { toastId: "upload-failed" },
          );
        } else {
          toast.error(`${failedResults.length} file(s) failed to upload.`, {
            toastId: "upload-failed",
          });
        }
      } else {
        const succeededNames = new Set(
          results.filter((r) => r.status_code === 201).map((r) => r.filename),
        );
        setFileUploadSuccesses(succeededNames);
        setUploadProgress(100);
        setFileUploadModal(false);
        setUploadModalFiles([]);
      }
    } catch (error) {
      // Leave the modal open with the files intact — the user can see the
      // error (toast) and retry, instead of losing the queue silently.
      const errorMessage =
        error instanceof Error
          ? error.message
          : "Failed to upload files. Please try again.";
      toast.error(errorMessage, { toastId: "upload-error" });
    } finally {
      setIsUploading(false);
    }
  };

  const handleFilesRejected = (
    rejectedFiles: FileWithValidation[],
    allowedExtensions?: string[],
  ): void => {
    if (rejectedFiles.some((f) => f.error === "INVALID_TYPE")) {
      const formats = allowedExtensions?.length
        ? allowedExtensions.map((ext) => ext.toUpperCase()).join(", ")
        : "PDF, DOCX, DOC, XLSX, TXT, PNG, JPG, JPEG, ZIP";
      toast.error(`Only ${formats} file format(s) are allowed.`, {
        toastId: "invalid-file-type",
      });
    }
    if (rejectedFiles.some((f) => f.error === "INVALID_SIZE")) {
      toast.error("One or more files exceed the maximum file size of 500 MB.", {
        toastId: "invalid-file-size",
      });
    }
  };

  const handleRemoveUploadFile = (index: number): void => {
    setUploadModalFiles((prev) => {
      const removedFile = prev[index];
      const updated = prev.filter((_, i) => i !== index);
      if (removedFile) {
        setFileUploadErrors((prevErrors) => {
          const newErrors = { ...prevErrors };
          delete newErrors[removedFile.name];
          return newErrors;
        });

        setFileUploadSuccesses((prevSuccesses) => {
          const newSuccesses = new Set(prevSuccesses);
          newSuccesses.delete(removedFile.name);
          return newSuccesses;
        });
      }
      return updated;
    });
  };

  return {
    isUploading,
    uploadProgress,
    fileUploadModal,
    setFileUploadModal,
    uploadModalFiles,
    setUploadModalFiles,
    fileUploadErrors,
    setFileUploadErrors,
    fileUploadSuccesses,
    setFileUploadSuccesses,
    failedUploadCount,
    hasFailedUploads,
    failedUploadFiles,
    handleFilesQueuedForUpload,
    handleFilesSelected,
    handleFilesRejected,
    handleRemoveUploadFile,
  };
}
