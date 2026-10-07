// PARENT-133：家長端請假 api client（BACKEND-355 / 356 / 357 / 358）。
// 型別逐欄對照 apps/api/app/schemas/leaves.py 的 ParentLeaveOut / ParentLeaveAttachmentOut；`*_at` 為 UTC ISO8601。
import { buildFormData } from '@/shared/http/upload'
import type { ActorCreatedByType, ISODate, ISODateTime, LeaveStatus, LeaveType, Page } from '@/shared/types/api'
import { parentHttp } from './http'

export interface LeaveAttachment {
  id: string
  mime_type: string
  size_bytes: number
  created_at: ISODateTime
  /** 短效簽名網址；簽名失敗時後端回 null（列表、取消、上傳回應都可能出現） */
  url: string | null
}

export interface ParentLeave {
  id: string
  student_id: string
  leave_type: LeaveType
  leave_type_label: string
  start_date: ISODate
  end_date: ISODate
  reason: string | null
  status: LeaveStatus
  created_by_type: ActorCreatedByType
  created_at: ISODateTime
  cancelled_at: ISODateTime | null
  /** active 且 end_date >= 今天（仍有今天或之後的請假日可取消） */
  can_cancel: boolean
  attachments: LeaveAttachment[]
}

const childLeavesPath = (childId: string) => `/parent/children/${encodeURIComponent(childId)}/leaves`
const leavePath = (id: string) => `/parent/leaves/${encodeURIComponent(id)}`

/** 含已取消，依開始日新到舊 */
export async function listChildLeaves(childId: string, page = 1, pageSize = 20): Promise<Page<ParentLeave>> {
  const res = await parentHttp.get<Page<ParentLeave>>(childLeavesPath(childId), {
    params: { page, page_size: pageSize },
  })
  return res.data
}

export async function createLeave(input: {
  student_id: string
  leave_type: LeaveType
  start_date: ISODate
  end_date: ISODate
  reason?: string
}): Promise<ParentLeave> {
  const body: {
    student_id: string
    leave_type: LeaveType
    start_date: ISODate
    end_date: ISODate
    reason?: string
  } = {
    student_id: input.student_id,
    leave_type: input.leave_type,
    start_date: input.start_date,
    end_date: input.end_date,
  }
  // undefined、空字串與純空白都不帶 reason 鍵
  if (input.reason !== undefined && input.reason.trim() !== '') body.reason = input.reason
  const res = await parentHttp.post<ParentLeave>('/parent/leaves', body)
  return res.data
}

/** 未開始整筆取消；已開始則取消今天起的日子（end_date 改為昨天） */
export async function cancelLeave(id: string): Promise<ParentLeave> {
  const res = await parentHttp.post<ParentLeave>(`${leavePath(id)}/cancel`)
  return res.data
}

/** multipart 欄位 `file`；413 / 415 / 409 `attachment_limit_reached` 由呼叫端依 code 處理 */
export async function uploadLeaveAttachment(leaveId: string, file: File): Promise<LeaveAttachment> {
  const res = await parentHttp.post<LeaveAttachment>(`${leavePath(leaveId)}/attachments`, buildFormData({ file }), {
    // 同 pickupPersons：以 per-request multipart/form-data 覆蓋 parentHttp 預設的 application/json，
    // 瀏覽器端 axios 送出前會清掉此標頭，由瀏覽器帶 boundary
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return res.data
}
