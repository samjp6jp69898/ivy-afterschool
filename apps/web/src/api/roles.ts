// FRONTEND-053：角色與權限目錄 api client（BACKEND-081~085）。
// 移植 ivy FE:src/api/permissions_admin.ts 的 createRole / updateRole / deleteRole 分組。
// 409 role_in_use / system_role_protected / last_role_manager、403 cannot_grant_permissions（details.permissions）
// 由 adminHttp 轉成 ApiError 往上拋。
import type { ISODateTime } from '@/shared/types/api'
import { adminHttp } from './http'

export interface Role {
  id: string
  code: string
  name: string
  description: string | null
  is_system: boolean
  /** DB 原值，admin 為 ['*'] */
  permissions: string[]
  /** 展開後排序 */
  effective_permissions: string[]
  staff_count: number
  created_at: ISODateTime
  updated_at: ISODateTime
}

export interface PermissionCatalog {
  groups: {
    key: string
    label: string
    permissions: { code: string; label: string }[]
  }[]
}

export interface RoleCreateBody {
  code: string
  name: string
  description: string | null
  permissions: string[]
}

export interface RoleUpdateBody {
  name?: string
  description?: string | null
  permissions?: string[]
}

export async function listRoles(): Promise<Role[]> {
  const res = await adminHttp.get<Role[]>('/admin/roles')
  return res.data
}

export async function createRole(body: RoleCreateBody): Promise<Role> {
  const res = await adminHttp.post<Role>('/admin/roles', body)
  return res.data
}

export async function updateRole(id: string, body: RoleUpdateBody): Promise<Role> {
  const res = await adminHttp.patch<Role>(`/admin/roles/${encodeURIComponent(id)}`, body)
  return res.data
}

export async function deleteRole(id: string): Promise<void> {
  await adminHttp.delete(`/admin/roles/${encodeURIComponent(id)}`)
}

export async function fetchPermissionCatalog(): Promise<PermissionCatalog> {
  const res = await adminHttp.get<PermissionCatalog>('/admin/permissions')
  return res.data
}
