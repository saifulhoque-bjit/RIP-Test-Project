import { baseApi } from "@/services/api/baseApi";
import { API_ENDPOINTS } from "@/services/api/endpoints";
import type {
  Notification,
  NotificationListResponse,
  NotificationUnreadCountResponse,
} from "@/types/notification";

const notificationApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    getNotifications: build.query<NotificationListResponse, { skip?: number; limit?: number } | void>({
      query: (params) => ({
        url: API_ENDPOINTS.NOTIFICATIONS.GET_LIST,
        params: {
          skip: params?.skip ?? 0,
          limit: params?.limit ?? 20,
        },
      }),
      providesTags: [{ type: "Notification", id: "LIST" }],
    }),

    getUnreadCount: build.query<NotificationUnreadCountResponse, void>({
      query: () => API_ENDPOINTS.NOTIFICATIONS.UNREAD_COUNT,
      providesTags: [{ type: "Notification", id: "LIST" }],
    }),

    markAllRead: build.mutation<NotificationUnreadCountResponse, void>({
      query: () => ({
        url: API_ENDPOINTS.NOTIFICATIONS.MARK_ALL_READ,
        method: "PATCH",
      }),
      invalidatesTags: [{ type: "Notification", id: "LIST" }],
    }),

    markAsRead: build.mutation<Notification, string>({
      query: (id) => ({
        url: API_ENDPOINTS.NOTIFICATIONS.MARK_READ(id),
        method: "PATCH",
      }),
      invalidatesTags: [{ type: "Notification", id: "LIST" }],
    }),
  }),
  overrideExisting: false,
});

export const {
  useGetNotificationsQuery,
  useGetUnreadCountQuery,
  useMarkAllReadMutation,
  useMarkAsReadMutation,
} = notificationApi;
export default notificationApi;