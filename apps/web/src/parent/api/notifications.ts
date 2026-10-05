// PARENT-170：家長端收件匣 api client（BACKEND-217 / 218 / 520）。
import type { Notification, NotificationPage } from '@/shared/types/api'
import { parentHttp } from './http'

export async function listNotifications(
  opts: { unreadOnly?: boolean; page?: number; pageSize?: number } = {},
): Promise<NotificationPage> {
  const params = {
    // unread_only 只在 true 時帶
    ...(opts.unreadOnly ? { unread_only: true } : {}),
    page: opts.page ?? 1,
    page_size: opts.pageSize ?? 20,
  }
  const res = await parentHttp.get<NotificationPage>('/parent/notifications', { params })
  return res.data
}

export async function markNotificationRead(id: string): Promise<Notification> {
  const res = await parentHttp.post<Notification>(`/parent/notifications/${id}/read`)
  return res.data
}

export async function markAllNotificationsRead(): Promise<{ updated: number }> {
  const res = await parentHttp.post<{ updated: number }>('/parent/notifications/read-all')
  return res.data
}
