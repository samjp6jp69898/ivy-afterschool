// FRONTEND-030：後台側欄選單（單一 manifest，移植 ivy FE:src/constants/navigation 的概念）。
// 每個項目的 permission 必須與 router/routes.ts 的 ROUTE_PERMISSIONS[path] 一致（navigation.spec.ts 檢查）。
import type { PermissionCode } from '@/constants/permissions'

export interface NavItem {
  label: string
  path: string
  /** @element-plus/icons-vue 名稱 */
  icon: string
  permission: PermissionCode
}

export interface NavGroup {
  key: string
  label: string
  items: NavItem[]
}

export const NAVIGATION: readonly NavGroup[] = [
  {
    key: 'home',
    label: '首頁',
    items: [{ label: '今日儀表板', path: '/', icon: 'Odometer', permission: 'dashboard:read' }],
  },
  {
    key: 'daily',
    label: '日常營運',
    items: [
      { label: '今日出勤', path: '/attendance', icon: 'Calendar', permission: 'attendance:read' },
      { label: '作業進度', path: '/homework', icon: 'EditPen', permission: 'homework:read' },
      { label: '接送', path: '/pickup', icon: 'Van', permission: 'pickup:read' },
      { label: '代理接送核驗', path: '/pickup/authorizations', icon: 'Stamp', permission: 'pickup:read' },
      { label: '請假', path: '/leaves', icon: 'Tickets', permission: 'leaves:read' },
    ],
  },
  {
    key: 'students',
    label: '學生與班級',
    items: [
      { label: '學生工作台', path: '/students', icon: 'User', permission: 'students:read' },
      { label: '班級管理', path: '/classes', icon: 'School', permission: 'classes:read' },
      { label: '月出勤報表', path: '/attendance/monthly', icon: 'DataLine', permission: 'attendance:read' },
    ],
  },
  {
    key: 'exams',
    label: '成績',
    items: [{ label: '考試', path: '/exams', icon: 'Trophy', permission: 'exams:read' }],
  },
  {
    key: 'settings',
    label: '設定',
    items: [
      { label: '系統設定', path: '/settings/system', icon: 'Setting', permission: 'settings:read' },
      { label: '參考資料', path: '/settings/reference', icon: 'Collection', permission: 'settings:read' },
      { label: '員工帳號', path: '/settings/accounts', icon: 'UserFilled', permission: 'staff:read' },
      { label: '角色權限', path: '/settings/roles', icon: 'Lock', permission: 'roles:read' },
      { label: '稽核紀錄', path: '/settings/audit', icon: 'List', permission: 'audit:read' },
    ],
  },
]

/** 過濾無權限項目，移除空群組（回傳新陣列，不改 NAVIGATION） */
export function visibleNavigation(perms: ReadonlySet<string>): NavGroup[] {
  return NAVIGATION.map((g) => ({ ...g, items: g.items.filter((i) => perms.has(i.permission)) })).filter(
    (g) => g.items.length > 0,
  )
}
