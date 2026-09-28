import { baseApi } from "@/services/api/baseApi";
import { API_ENDPOINTS } from "@/services/api/endpoints";

export type ExportStatusFilter = "approved" | "all";
export type ExportBacklogFormat = "json" | "pdf";

export interface ExportRequestBody {
  status_filter: ExportStatusFilter;
  include_backlog: boolean;
  backlog_format: ExportBacklogFormat;
  include_srs: boolean;
  include_domain_knowledge: boolean;
  include_architecture_document: boolean;
}

const exportApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    /**
     * Build and download a project export ZIP (requirements backlog +/or SRS
     * specs, per the caller's selection). The response is a raw binary ZIP —
     * not the standard {success, message, data} envelope — so a custom
     * responseHandler is needed: error responses are still parsed as JSON
     * (so getErrorMessage can read `.data.message`), success responses are
     * read as a Blob for the caller to save to disk.
     */
    exportProject: build.mutation<Blob, { projectId: string; body: ExportRequestBody }>({
      query: ({ projectId, body }) => ({
        url: API_ENDPOINTS.EXPORT.CREATE(projectId),
        method: "POST",
        body,
        responseHandler: async (response: Response) => {
          if (!response.ok) {
            try {
              return await response.json();
            } catch {
              return null;
            }
          }
          return response.blob();
        },
      }),
    }),
  }),
  overrideExisting: false,
});

export const { useExportProjectMutation } = exportApi;
