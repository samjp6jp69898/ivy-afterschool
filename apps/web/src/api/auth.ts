// FRONTEND-021：員工登入、refresh、登出、改密碼與目前員工（BACKEND-042~050），以及登入頁用的公開設定（BACKEND-125）。
// 移植 ivy FE:src/api/auth.ts 的函式分組；去掉 impersonate、tenant 與帳號管理（帳號管理在 api/staffUsers.ts）。
// 錯誤由 adminHttp 轉成 ApiError 往上拋（429 too_many_attempts、422 weak_password 等）。
import { adminHttp } from './http'

export interface StaffRole {
  id: string
  code: string
  name: string
}

export interface StaffMe {
  id: string
  username: string
  display_name: string
  role: StaffRole
  /** 已排序 */
  permissions: string[]
  must_change_password: boolean
}

export interface PublicConfig {
  liff_id: string
  org_name: string
  logo_url: string | null
}

export interface LoginBody {
  username: string
  password: string
}

export interface ChangePasswordBody {
  current_password: string
  new_password: string
}

interface StaffAuthOut {
  user: StaffMe
}

export async function login(body: LoginBody): Promise<StaffMe> {
  const res = await adminHttp.post<StaffAuthOut>('/admin/auth/login', body)
  return res.data.user
}

export async function refresh(): Promise<StaffMe> {
  const res = await adminHttp.post<StaffAuthOut>('/admin/auth/refresh')
  return res.data.user
}

export async function logout(): Promise<void> {
  await adminHttp.post('/admin/auth/logout')
}

export async function fetchMe(): Promise<StaffMe> {
  const res = await adminHttp.get<StaffMe>('/admin/auth/me')
  return res.data
}

export async function changePassword(body: ChangePasswordBody): Promise<StaffMe> {
  const res = await adminHttp.post<StaffAuthOut>('/admin/auth/change-password', body)
  return res.data.user
}

/** 不需登入；登入頁顯示安親班名稱 / Logo 用 */
export async function fetchPublicConfig(): Promise<PublicConfig> {
  const res = await adminHttp.get<PublicConfig>('/parent/config')
  return res.data
}
