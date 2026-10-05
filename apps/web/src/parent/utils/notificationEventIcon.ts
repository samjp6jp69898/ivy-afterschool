// 通知事件 → Material Symbols 圖示（依事件前綴）。NotificationListItem（PARENT-176）與
// NotificationPrefsView（PARENT-178）共用，兩處的同一類事件一律同一個圖示。
const EVENT_ICONS: [prefix: string, icon: string][] = [
  ['attendance.', 'how_to_reg'],
  ['homework.', 'assignment'],
  ['pickup.', 'directions_walk'],
  ['exam.', 'grading'],
  ['binding.', 'link'],
]

export function notificationEventIcon(event: string): string {
  return EVENT_ICONS.find(([prefix]) => event.startsWith(prefix))?.[1] ?? 'notifications'
}
