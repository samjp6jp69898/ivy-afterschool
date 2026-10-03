// FRONTEND-024：後台路由表（docs/domain_spec.md §4）。移植 ivy FE:src/router/index.ts::routes 的
// 「meta 帶權限、預設拒絕」：每條路由恰屬 public / authOnly / permission 其一，沒有的由 authGuard 拒絕。
// view 一律經 VIEW_LOADERS 延遲載入，本檔不直接 import view。
// 路由順序即 firstAllowedPath（FRONTEND-025）的優先順序；catch-all 必須放最後。
import type { RouteRecordRaw } from 'vue-router'
import type { PermissionCode } from '@/constants/permissions'
import { VIEW_LOADERS } from '@/router/viewLoaders'

declare module 'vue-router' {
  interface RouteMeta {
    title: string
    permission?: PermissionCode
    /** 不需登入 */
    public?: boolean
    /** 登入即可，不需權限碼 */
    authOnly?: boolean
    allowWhenMustChangePassword?: boolean
    /** 預設 admin */
    layout?: 'admin' | 'blank'
    /** 平板頁：layout 縮小側欄 */
    tablet?: boolean
  }
}

export const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: VIEW_LOADERS.LoginView,
    meta: { title: '登入', public: true, layout: 'blank' },
  },
  {
    path: '/change-password',
    name: 'change-password',
    component: VIEW_LOADERS.ChangePasswordView,
    meta: { title: '修改密碼', authOnly: true, allowWhenMustChangePassword: true, layout: 'blank' },
  },
  {
    path: '/forbidden',
    name: 'forbidden',
    component: VIEW_LOADERS.ErrorView,
    props: { kind: 'forbidden' },
    meta: { title: '沒有權限', authOnly: true },
  },
  {
    path: '/',
    name: 'dashboard',
    component: VIEW_LOADERS.DashboardView,
    meta: { title: '今日儀表板', permission: 'dashboard:read' },
  },
  {
    path: '/students',
    name: 'students',
    component: VIEW_LOADERS.StudentWorkbenchView,
    meta: { title: '學生工作台', permission: 'students:read' },
  },
  {
    path: '/classes',
    name: 'classes',
    component: VIEW_LOADERS.ClassesView,
    meta: { title: '班級管理', permission: 'classes:read' },
  },
  {
    path: '/attendance',
    name: 'attendance-today',
    component: VIEW_LOADERS.AttendanceTodayView,
    meta: { title: '今日出勤', permission: 'attendance:read', tablet: true },
  },
  {
    path: '/attendance/monthly',
    name: 'attendance-monthly',
    component: VIEW_LOADERS.AttendanceMonthlyView,
    meta: { title: '月出勤報表', permission: 'attendance:read' },
  },
  {
    path: '/leaves',
    name: 'leaves',
    component: VIEW_LOADERS.LeavesView,
    meta: { title: '請假', permission: 'leaves:read' },
  },
  {
    path: '/homework',
    name: 'homework',
    component: VIEW_LOADERS.HomeworkBoardView,
    meta: { title: '作業進度', permission: 'homework:read', tablet: true },
  },
  {
    path: '/pickup',
    name: 'pickup',
    component: VIEW_LOADERS.PickupPosView,
    meta: { title: '接送', permission: 'pickup:read', tablet: true },
  },
  {
    path: '/pickup/authorizations',
    name: 'pickup-authorizations',
    component: VIEW_LOADERS.PickupAuthorizationsView,
    meta: { title: '代理接送核驗', permission: 'pickup:read', tablet: true },
  },
  {
    path: '/exams',
    name: 'exams',
    component: VIEW_LOADERS.ExamsView,
    meta: { title: '考試', permission: 'exams:read' },
  },
  {
    path: '/exams/:id',
    name: 'exam-detail',
    component: VIEW_LOADERS.ExamDetailView,
    props: true,
    meta: { title: '考試成績', permission: 'exams:read' },
  },
  {
    path: '/settings/system',
    name: 'settings-system',
    component: VIEW_LOADERS.SystemSettingsView,
    meta: { title: '系統設定', permission: 'settings:read' },
  },
  {
    path: '/settings/reference',
    name: 'settings-reference',
    component: VIEW_LOADERS.ReferenceDataView,
    meta: { title: '參考資料', permission: 'settings:read' },
  },
  {
    path: '/settings/accounts',
    name: 'settings-accounts',
    component: VIEW_LOADERS.StaffAccountsView,
    meta: { title: '員工帳號', permission: 'staff:read' },
  },
  {
    path: '/settings/roles',
    name: 'settings-roles',
    component: VIEW_LOADERS.RolesView,
    meta: { title: '角色權限', permission: 'roles:read' },
  },
  {
    path: '/settings/audit',
    name: 'settings-audit',
    component: VIEW_LOADERS.AuditLogView,
    meta: { title: '稽核紀錄', permission: 'audit:read' },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: VIEW_LOADERS.ErrorView,
    props: { kind: 'not_found' },
    meta: { title: '找不到頁面', public: true, layout: 'blank' },
  },
]

function collectPermissions(records: RouteRecordRaw[], out: Record<string, PermissionCode>): void {
  for (const r of records) {
    if (r.meta?.permission) out[r.path] = r.meta.permission
    if (r.children) collectPermissions(r.children, out)
  }
}

/** path → permission（供 FRONTEND-030 側欄一致性測試） */
export const ROUTE_PERMISSIONS: Record<string, PermissionCode> = (() => {
  const out: Record<string, PermissionCode> = {}
  collectPermissions(routes, out)
  return out
})()
