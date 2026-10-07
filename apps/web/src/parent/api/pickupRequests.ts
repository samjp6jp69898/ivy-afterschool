// PARENT-090：家長端接送請求 api client（BACKEND-440 / 441 / 442 / 443）。
// 型別逐欄對照 apps/api/app/schemas/pickup.py 的 ParentPickupRequestOut；`*_at` 為 UTC ISO8601，
// expected_arrival_at / reply_ready_eta 為台北當地時間 HH:MM。
import type {
  HHMM,
  ISODate,
  ISODateTime,
  PickupReplySource,
  PickupRequestStatus,
} from '@/shared/types/api'
import { parentHttp } from './http'

export interface ParentPickupRequest {
  id: string
  student_id: string
  student_name: string
  service_date: ISODate
  status: PickupRequestStatus
  expected_arrival_at: HHMM | null
  reply_ready_eta: HHMM | null
  reply_message: string | null
  /** auto = 系統自動回覆，staff = 老師回覆 */
  reply_source: PickupReplySource | null
  replied_at: ISODateTime | null
  arrived_at: ISODateTime | null
  completed_at: ISODateTime | null
  picked_up_by_name: string | null
  cancelled_at: ISODateTime | null
  created_at: ISODateTime
  /** 非終態 */
  can_cancel: boolean
  /** pending / acknowledged */
  can_mark_arrived: boolean
}

/**
 * 「我要來接」（可帶預計到達時間）或「我已經到了」捷徑（arrived: true，直接建立 arrived 狀態的請求）。
 * 兩種寫法互斥：以 never 讓同時出現 arrived 與 expected_arrival_at 成為型別錯誤。
 * 409 `pickup_request_exists` 的 `error.details.request_id` 為既有進行中請求的 id。
 */
export type PickupRequestCreateInput =
  | { student_id: string; expected_arrival_at?: HHMM | null; arrived?: never }
  | { student_id: string; arrived: true; expected_arrival_at?: never }

export async function createPickupRequest(input: PickupRequestCreateInput): Promise<ParentPickupRequest> {
  const body: { student_id: string; expected_arrival_at?: HHMM; arrived?: true } = {
    student_id: input.student_id,
  }
  // null / undefined 不帶欄位；兩者並存時照送，由後端回 422
  const eta = input.expected_arrival_at
  if (eta !== null && eta !== undefined) body.expected_arrival_at = eta
  if (input.arrived === true) body.arrived = true
  const res = await parentHttp.post<ParentPickupRequest>('/parent/pickup/requests', body)
  return res.data
}

/** 所有小孩今日的請求，含終態 */
export async function listTodayPickupRequests(): Promise<ParentPickupRequest[]> {
  const res = await parentHttp.get<ParentPickupRequest[]>('/parent/pickup/requests/today')
  return res.data
}

/** 我到了 */
export async function markPickupArrived(id: string): Promise<ParentPickupRequest> {
  const res = await parentHttp.post<ParentPickupRequest>(`/parent/pickup/requests/${id}/arrived`)
  return res.data
}

/** 沒有取消原因（含空白）時送空 body，後端 cancel_reason 維持 NULL */
export async function cancelPickupRequest(id: string, reason?: string): Promise<ParentPickupRequest> {
  const body = reason !== undefined && reason.trim() !== '' ? { reason } : {}
  const res = await parentHttp.post<ParentPickupRequest>(`/parent/pickup/requests/${id}/cancel`, body)
  return res.data
}
