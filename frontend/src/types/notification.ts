export type NotificationType = string;

export interface Notification {
  id: string;
  user_id: string;
  title: string;
  message: string;
  notification_type: NotificationType;
  is_read: boolean;
  data: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface NotificationListResponse {
  items: Notification[];
  total: number;
  skip: number;
  limit: number;
}

export interface NotificationListApiResponse {
  success: boolean;
  message: string;
  data: NotificationListResponse;
}

export interface NotificationUnreadCountResponse {
  unread_count: number;
}