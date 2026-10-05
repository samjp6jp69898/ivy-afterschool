// FRONTEND-051：參考資料（科目 / 考試類型 / 學校 / 休息日）api client（BACKEND-119~122）。
// 四個資源共用同一組 endpoint 形狀，以 factory 產生。
import type { ISODate } from '@/shared/types/api'
import { adminHttp } from './http'

export type ReferenceResource = 'subjects' | 'exam-types' | 'schools' | 'closed-days'

export interface Subject {
  id: string
  name: string
  sort_order: number
  is_active: boolean
}

export type ExamType = Subject

export interface School {
  id: string
  name: string
  short_name: string | null
  is_active: boolean
}

export interface ClosedDay {
  id: string
  date: ISODate
  reason: string | null
}

export interface DeleteResult {
  deleted: boolean
  /** 被引用時改為停用 */
  deactivated: boolean
}

export interface ReferenceListParams {
  /** subjects / exam-types / schools */
  active_only?: boolean
  /** closed-days */
  date_from?: ISODate
  date_to?: ISODate
}

export interface NamedItemCreateBody {
  name: string
  sort_order?: number
  is_active?: boolean
}

export type NamedItemUpdateBody = Partial<NamedItemCreateBody>

export interface SchoolCreateBody {
  name: string
  short_name?: string | null
  is_active?: boolean
}

export type SchoolUpdateBody = Partial<SchoolCreateBody>

export interface ClosedDayCreateBody {
  date: ISODate
  reason?: string | null
}

/** date 不可改，要改日期請刪除重建 */
export interface ClosedDayUpdateBody {
  reason?: string | null
}

export interface ReferenceApi<Item, CreateBody, UpdateBody> {
  list(params?: ReferenceListParams): Promise<Item[]>
  create(body: CreateBody): Promise<Item>
  update(id: string, body: UpdateBody): Promise<Item>
  remove(id: string): Promise<DeleteResult>
}

export function createReferenceApi<Item, CreateBody, UpdateBody>(
  resource: ReferenceResource,
): ReferenceApi<Item, CreateBody, UpdateBody> {
  const base = `/admin/${resource}`
  const itemPath = (id: string) => `${base}/${encodeURIComponent(id)}`
  return {
    async list(params = {}) {
      const res = await adminHttp.get<Item[]>(base, { params })
      return res.data
    },
    async create(body) {
      const res = await adminHttp.post<Item>(base, body)
      return res.data
    },
    async update(id, body) {
      const res = await adminHttp.patch<Item>(itemPath(id), body)
      return res.data
    },
    async remove(id) {
      const res = await adminHttp.delete<DeleteResult>(itemPath(id))
      return res.data
    },
  }
}

export const subjectsApi = createReferenceApi<Subject, NamedItemCreateBody, NamedItemUpdateBody>('subjects')
export const examTypesApi = createReferenceApi<ExamType, NamedItemCreateBody, NamedItemUpdateBody>('exam-types')
export const schoolsApi = createReferenceApi<School, SchoolCreateBody, SchoolUpdateBody>('schools')
export const closedDaysApi = createReferenceApi<ClosedDay, ClosedDayCreateBody, ClosedDayUpdateBody>('closed-days')
