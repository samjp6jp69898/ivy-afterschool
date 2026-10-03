// FRONTEND-024：後台 view 的延遲載入集中在這裡，routes.ts 只引用 VIEW_LOADERS.X。
// 初始全部指向 PlaceholderView；各 view task 完成時把自己的 loader 改為 `() => import('@/views/...')`。
import type { Component } from 'vue'

export type ViewName =
  | 'LoginView'
  | 'ChangePasswordView'
  | 'ErrorView'
  | 'DashboardView'
  | 'StudentWorkbenchView'
  | 'ClassesView'
  | 'AttendanceTodayView'
  | 'AttendanceMonthlyView'
  | 'LeavesView'
  | 'HomeworkBoardView'
  | 'PickupPosView'
  | 'PickupAuthorizationsView'
  | 'ExamsView'
  | 'ExamDetailView'
  | 'SystemSettingsView'
  | 'ReferenceDataView'
  | 'StaffAccountsView'
  | 'RolesView'
  | 'AuditLogView'

const placeholder = () => import('@/router/PlaceholderView.vue')

export const VIEW_LOADERS: Record<ViewName, () => Promise<Component>> = {
  LoginView: placeholder,
  ChangePasswordView: placeholder,
  ErrorView: placeholder,
  DashboardView: placeholder,
  StudentWorkbenchView: placeholder,
  ClassesView: placeholder,
  AttendanceTodayView: placeholder,
  AttendanceMonthlyView: placeholder,
  LeavesView: placeholder,
  HomeworkBoardView: placeholder,
  PickupPosView: placeholder,
  PickupAuthorizationsView: placeholder,
  ExamsView: placeholder,
  ExamDetailView: placeholder,
  SystemSettingsView: placeholder,
  ReferenceDataView: placeholder,
  StaffAccountsView: placeholder,
  RolesView: placeholder,
  AuditLogView: placeholder,
}
