// FRONTEND-052：員工帳號 api client（BACKEND-093~098、522、528；docs/tasks/api_index.md §4）。
// 移植 ivy FE:src/api/auth.ts 的 getUsers / createUser / updateUser / resetPassword 分組，改為本專案路徑與欄位。
// 建立 / 重設密碼 / 重新啟用回傳的 temp_password 只在回應中出現一次：這裡原樣回傳給呼叫端，
// 不記 log、不存 storage、不放進任何 store。
// 錯誤由 adminHttp 轉成 ApiError 往上拋：409 username_taken / staff_already_active / cannot_deactivate_self /
// cannot_reset_self / cannot_modify_self_permissions / last_role_manager、403 cannot_manage_staff /
// cannot_grant_permissions（details.permissions）、404 staff_user_not_found。
import type { StaffRole } from '@/api/auth'
import type { ISODateTime, Page } from '@/shared/types/api'
import { adminHttp } from './http'

export interface StaffUser {
  id: string
  username: string
  display_name: string
  phone: string | null
  email: string | null
  role: StaffRole
  /** 角色之外個別加的權限碼 */
  extra_permissions: string[]
  /** 角色權限中被個別拿掉的權限碼 */
  revoked_permissions: string[]
  /** 已排序 */
  effective_permissions: string[]
  is_active: boolean
  must_change_password: boolean
  last_login_at: ISODateTime | null
  created_at: ISODateTime
}

export interface StaffUserCreate {
  username: string
  display_name: string
  phone?: string | null
  email?: string | null
  role_id: string
  extra_permissions: string[]
  revoked_permissions: string[]
}

/** username 不可改；phone / email 給 null 代表清除 */
export interface StaffUserUpdate {
  display_name?: string
  phone?: string | null
  email?: string | null
  role_id?: string
  extra_permissions?: string[]
  revoked_permissions?: string[]
}

export interface StaffUserListParams {
  q?: string
  role_id?: string
  is_active?: boolean
  page?: number
  page_size?: number
}

/** 建立 / 重新啟用：臨時密碼只回這一次 */
export interface StaffUserCreated {
  user: StaffUser
  temp_password: string
}

export interface TempPassword {
  temp_password: string
}

/** 班級負責老師指派下拉用，只含啟用員工 */
export interface StaffOption {
  id: string
  display_name: string
}

const staffPath = (id: string) => `/admin/staff-users/${encodeURIComponent(id)}`

export async function listStaffUsers(params: StaffUserListParams = {}): Promise<Page<StaffUser>> {
  const res = await adminHttp.get<Page<StaffUser>>('/admin/staff-users', { params })
  return res.data
}

export async function getStaffUser(id: string): Promise<StaffUser> {
  const res = await adminHttp.get<StaffUser>(staffPath(id))
  return res.data
}

export async function createStaffUser(body: StaffUserCreate): Promise<StaffUserCreated> {
  const res = await adminHttp.post<StaffUserCreated>('/admin/staff-users', body)
  return res.data
}

export async function updateStaffUser(id: string, body: StaffUserUpdate): Promise<StaffUser> {
  const res = await adminHttp.patch<StaffUser>(staffPath(id), body)
  return res.data
}

/** 目標帳號既有登入立即失效 */
export async function resetStaffPassword(id: string): Promise<TempPassword> {
  const res = await adminHttp.post<TempPassword>(`${staffPath(id)}/reset-password`)
  return res.data
}

/** 目標帳號既有登入立即失效；可用 activateStaffUser 重新啟用 */
export async function deactivateStaffUser(id: string): Promise<StaffUser> {
  const res = await adminHttp.post<StaffUser>(`${staffPath(id)}/deactivate`)
  return res.data
}

/** 重新啟用後 must_change_password 為 true，下次登入須先改密碼 */
export async function activateStaffUser(id: string): Promise<StaffUserCreated> {
  const res = await adminHttp.post<StaffUserCreated>(`${staffPath(id)}/activate`)
  return res.data
}

/** classes:write 或 staff:read 可用 */
export async function listStaffOptions(): Promise<StaffOption[]> {
  const res = await adminHttp.get<StaffOption[]>('/admin/staff-users/options')
  return res.data
}
