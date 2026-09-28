import { baseApi } from "@/services/api/baseApi";
import { API_ENDPOINTS } from "@/services/api/endpoints";
import type {
  AppSettings,
  AppSettingsData,
  EnumCatalog,
  EnumCatalogData,
} from "@/types";

const settingsApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    getAppSettings: build.query<AppSettingsData, void>({
      query: () => ({
        url: API_ENDPOINTS.SETTINGS.GET_CONFIG,
      }),
      transformResponse: (response: AppSettings) => response.data,
      providesTags: [{ type: "Settings", id: "APP_CONFIG" }],
    }),
    getEnumCatalog: build.query<EnumCatalogData, void>({
      query: () => ({
        url: API_ENDPOINTS.SETTINGS.GET_ENUMS,
      }),
      transformResponse: (response: EnumCatalog) => response.data,
      providesTags: [{ type: "Enum", id: "CATALOG" }],
    }),
  }),
  overrideExisting: false,
});

export const { useGetAppSettingsQuery, useGetEnumCatalogQuery } = settingsApi;
