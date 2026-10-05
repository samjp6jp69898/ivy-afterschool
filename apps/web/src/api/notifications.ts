// FRONTEND-033：員工站內通知 api client（BACKEND-214~216）。
import type { Notification, NotificationPage } from '@/shared/types/api'
import { adminHttp } from './http'

export interface NotificationQuery {
  unread_only?: boolean
  page?: number
  page_size?: number
}

export async function listNotifications(params: NotificationQuery = {}): Promise<NotificationPage> {
  const res = await adminHttp.get<NotificationPage>('/admin/notifications', { params })
  return res.data
}

export async function markNotificationRead(id: string): Promise<Notification> {
  const res = await adminHttp.post<Notification>(`/admin/notifications/${encodeURIComponent(id)}/read`)
  return res.data
}

export async function markAllNotificationsRead(): Promise<{ updated: number }> {
  const res = await adminHttp.post<{ updated: number }>('/admin/notifications/read-all')
  return res.data
}
