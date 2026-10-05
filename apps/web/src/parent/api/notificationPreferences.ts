// PARENT-171：家長端 LINE 通知偏好 api client（BACKEND-221 / 222）。
import { parentHttp } from './http'

export interface PreferenceItem {
  event: string
  label: string
  line_enabled: boolean
}

export async function getNotificationPreferences(): Promise<PreferenceItem[]> {
  const res = await parentHttp.get<{ items: PreferenceItem[] }>('/parent/notification-preferences')
  return res.data.items
}

/** 部分更新：只送要改的事件 */
export async function updateNotificationPreferences(
  items: { event: string; line_enabled: boolean }[],
): Promise<PreferenceItem[]> {
  const res = await parentHttp.put<{ items: PreferenceItem[] }>('/parent/notification-preferences', { items })
  return res.data.items
}
