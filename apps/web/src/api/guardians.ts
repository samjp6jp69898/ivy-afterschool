// FRONTEND-082：監護人與家長綁定 api client（BACKEND-173~178）。
// 移植 ivy FE:src/api/students.ts 的 listGuardians / createGuardian / updateGuardian / deleteGuardian /
// createGuardianBindingCode；去掉 device setup code、revoke devices。
// 409 guardian_already_bound / guardian_not_bound / student_archived 由 adminHttp 轉成 ApiError。
import type { BindingStatus, GuardianRelation, ISODateTime } from '@/shared/types/api'
import { adminHttp } from './http'

export interface GuardianBinding {
  status: BindingStatus
  /** bound 時 */
  parent_display_name: string | null
  /** code_issued 時：最新未使用且未過期碼的到期時間 */
  code_expires_at: ISODateTime | null
}

export interface Guardian {
  id: string
  student_id: string
  name: string
  relation: GuardianRelation
  phone: string | null
  is_primary: boolean
  can_pickup: boolean
  receives_notifications: boolean
  binding: GuardianBinding
}

export interface GuardianInput {
  name: string
  relation: GuardianRelation
  phone?: string | null
  is_primary: boolean
  can_pickup: boolean
  receives_notifications: boolean
}

export interface IssuedBindingCode {
  guardian_id: string
  /** 明碼，只在產生當下回傳 */
  code: string
  expires_at: ISODateTime
}

const guardianPath = (id: string) => `/admin/guardians/${encodeURIComponent(id)}`
const studentGuardiansPath = (studentId: string) => `/admin/students/${encodeURIComponent(studentId)}/guardians`

export async function listGuardians(studentId: string): Promise<Guardian[]> {
  const res = await adminHttp.get<Guardian[]>(studentGuardiansPath(studentId))
  return res.data
}

export async function createGuardian(studentId: string, body: GuardianInput): Promise<Guardian> {
  const res = await adminHttp.post<Guardian>(studentGuardiansPath(studentId), body)
  return res.data
}

export async function updateGuardian(id: string, body: Partial<GuardianInput>): Promise<Guardian> {
  const res = await adminHttp.patch<Guardian>(guardianPath(id), body)
  return res.data
}

export async function deleteGuardian(id: string): Promise<void> {
  await adminHttp.delete(guardianPath(id))
}

export async function issueBindingCode(id: string): Promise<IssuedBindingCode> {
  const res = await adminHttp.post<IssuedBindingCode>(`${guardianPath(id)}/binding-code`)
  return res.data
}

export async function unbindGuardian(id: string): Promise<Guardian> {
  const res = await adminHttp.post<Guardian>(`${guardianPath(id)}/unbind`)
  return res.data
}
