// FRONTEND-121：請假 api client（BACKEND-351~354，docs/tasks/api_index.md §8）。
// 移植 ivy FE:src/api/studentLeaves.ts::listStudentLeaves；去掉審核。
// 錯誤由 adminHttp 轉成 ApiError：409 leave_overlap（details 為 LeaveOverlapDetails）/ student_not_active /
// leave_not_active / leave_already_ended，422 no_service_days_in_range，404 leave_not_found / attachment_not_found。
import type {
  ActorCreatedByType,
  ISODate,
  ISODateTime,
  LeaveStatus,
  LeaveType,
  Page,
} from '@/shared/types/api'
import { adminHttp } from './http'

export interface LeaveStudent {
  id: string
  student_no: string
  name: string
  class_name: string | null
}

/** 只有 metadata；內容用 fetchLeaveAttachmentUrl 取得短效網址 */
export interface LeaveAttachment {
  id: string
  mime_type: string
  size_bytes: number
  created_at: ISODateTime
}

export interface Leave {
  id: string
  student: LeaveStudent
  leave_type: LeaveType
  leave_type_label: string
  start_date: ISODate
  end_date: ISODate
  reason: string | null
  status: LeaveStatus
  created_by_type: ActorCreatedByType
  created_by_name: string | null
  created_at: ISODateTime
  cancelled_at: ISODateTime | null
  cancelled_by_type: ActorCreatedByType | null
  cancelled_by_name: string | null
  attachments: LeaveAttachment[]
}

export interface LeaveQuery {
  student_id?: string
  /** 學生目前的班級 */
  class_id?: string
  status?: LeaveStatus
  leave_type?: LeaveType
  created_by_type?: ActorCreatedByType
  /** 與請假區間有交集即符合（含頭尾） */
  date_from?: ISODate
  date_to?: ISODate
  page?: number
  /** ≤ 200 */
  page_size?: number
}

export interface LeaveCreate {
  student_id: string
  leave_type: LeaveType
  start_date: ISODate
  /** 單筆最長 61 天 */
  end_date: ISODate
  /** 最長 500 字 */
  reason?: string | null
}

/** remaining：取消今天起尚未到的日子；all：整筆取消（含已開始或已結束的請假，事後更正） */
export type LeaveCancelScope = 'remaining' | 'all'

export interface LeaveAttachmentUrl {
  url: string
  /** 秒 */
  expires_in: number
}

/** 409 leave_overlap 的 details：與新請假重疊的既有請假 */
export interface LeaveOverlapDetails {
  leave_id: string
  start_date: ISODate
  end_date: ISODate
}

const leavePath = (id: string) => `/admin/leaves/${encodeURIComponent(id)}`

export async function listLeaves(q: LeaveQuery = {}): Promise<Page<Leave>> {
  const res = await adminHttp.get<Page<Leave>>('/admin/leaves', { params: q })
  return res.data
}

export async function createLeave(body: LeaveCreate): Promise<Leave> {
  const res = await adminHttp.post<Leave>('/admin/leaves', body)
  return res.data
}

/**
 * 回傳更新後的請假：整筆取消為 cancelled；remaining 且已開始時 status 仍是 active、end_date 縮短為昨天。
 * 409 leave_already_ended（remaining 且已全部過去，要整筆取消請用 all）/ leave_not_active。
 */
export async function cancelLeave(id: string, scope: LeaveCancelScope = 'remaining'): Promise<Leave> {
  const res = await adminHttp.post<Leave>(`${leavePath(id)}/cancel`, { scope })
  return res.data
}

/** 短效網址：每次呼叫都重新請求，不快取 */
export async function fetchLeaveAttachmentUrl(leaveId: string, attachmentId: string): Promise<LeaveAttachmentUrl> {
  const res = await adminHttp.get<LeaveAttachmentUrl>(
    `${leavePath(leaveId)}/attachments/${encodeURIComponent(attachmentId)}`,
  )
  return res.data
}
