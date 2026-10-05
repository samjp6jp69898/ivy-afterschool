// FRONTEND-080：班級 api client（BACKEND-141~146）。
// 移植 ivy FE:src/api/classrooms.ts 的函式分組；去掉 grades、teacher-options、enrollment composition。
// 409 class_has_students（details.student_count）/ class_name_taken、422 invalid_staff 由 adminHttp 轉成 ApiError。
import type { ClassStaffRole, ISODateTime } from '@/shared/types/api'
import { adminHttp } from './http'

export interface ClassStaff {
  staff_user_id: string
  display_name: string
  role: ClassStaffRole
}

export interface ClassItem {
  id: string
  name: string
  grade_levels: number[]
  /** 民國學年度 */
  academic_year: number
  sort_order: number
  archived_at: ISODateTime | null
  /** 未封存且 active 的學生數 */
  student_count: number
  staff: ClassStaff[]
}

export interface ClassCreate {
  name: string
  grade_levels: number[]
  academic_year: number
  sort_order: number
}

export type ClassUpdate = Partial<ClassCreate>

export interface ClassListParams {
  academic_year?: number
  include_archived?: boolean
  /** 只列目前員工負責的班 */
  mine?: boolean
}

export interface ClassStaffInput {
  staff_user_id: string
  role: ClassStaffRole
}

const classPath = (id: string) => `/admin/classes/${encodeURIComponent(id)}`

export async function listClasses(params: ClassListParams = {}): Promise<ClassItem[]> {
  const res = await adminHttp.get<ClassItem[]>('/admin/classes', { params })
  return res.data
}

export async function getClass(id: string): Promise<ClassItem> {
  const res = await adminHttp.get<ClassItem>(classPath(id))
  return res.data
}

export async function createClass(body: ClassCreate): Promise<ClassItem> {
  const res = await adminHttp.post<ClassItem>('/admin/classes', body)
  return res.data
}

export async function updateClass(id: string, body: ClassUpdate): Promise<ClassItem> {
  const res = await adminHttp.patch<ClassItem>(classPath(id), body)
  return res.data
}

export async function archiveClass(id: string): Promise<ClassItem> {
  const res = await adminHttp.post<ClassItem>(`${classPath(id)}/archive`)
  return res.data
}

/** 整批覆蓋班級負責員工 */
export async function setClassStaff(id: string, items: ClassStaffInput[]): Promise<ClassItem> {
  const res = await adminHttp.put<ClassItem>(`${classPath(id)}/staff`, { items })
  return res.data
}
