// FRONTEND-180：接送佇列、POS 名單與接送請求操作 api client（BACKEND-429~435，docs/tasks/api_index.md §10）。
// 移植 ivy FE:src/api/dismissalCalls.ts 的函式分組；改為本專案狀態機與路徑，去掉 POS 標記請假 / 娃娃車、portal 端點
// 與 WebSocket（ws 走 FRONTEND-037）。
// 錯誤由 adminHttp 轉成 ApiError：409 pickup_request_exists（details 為 PickupRequestExistsDetails）/ student_not_available /
// not_service_day / invalid_pickup_status（details 為 InvalidPickupStatusDetails）/ guardian_cannot_pickup，
// 422 invalid_guardian / expected_arrival_in_past / expected_arrival_too_late，403 permission_denied（override 缺 pickup:override），
// 404 pickup_request_not_found。
import type {
  ActorCreatedByType,
  AttendanceStatus,
  HHMM,
  HomeworkOverallStatus,
  ISODate,
  ISODateTime,
  LeaveType,
  PickupCompletionMethod,
  PickupOpenStatus,
  PickupReplySource,
  PickupRequestStatus,
  PickupSource,
} from '@/shared/types/api'
import { adminHttp } from './http'

export interface PickupStudent {
  id: string
  student_no: string
  name: string
  grade_level: number
  class_id: string | null
  class_name: string | null
}

/** 發起者類型（與請假的 created_by_type 同值域） */
export type PickupRequesterType = ActorCreatedByType

export interface PickupRequest {
  id: string
  student: PickupStudent
  service_date: ISODate
  source: PickupSource
  requested_by_type: PickupRequesterType
  requested_by_name: string | null
  /** 台北當地時間 */
  expected_arrival_at: HHMM | null
  status: PickupRequestStatus
  homework_status_at_request: HomeworkOverallStatus | null
  current_homework_status: HomeworkOverallStatus | null
  current_ready_eta: HHMM | null
  reply_ready_eta: HHMM | null
  reply_message: string | null
  /** auto = 系統自動回覆，staff = 老師回覆 */
  reply_source: PickupReplySource | null
  replied_at: ISODateTime | null
  replied_by_name: string | null
  /** 非終態，且沒有回覆或只有沒有 ETA 的自動回覆 */
  needs_reply: boolean
  arrived_at: ISODateTime | null
  completed_at: ISODateTime | null
  completed_by_name: string | null
  completion_method: PickupCompletionMethod | null
  picked_up_by_name: string | null
  cancelled_at: ISODateTime | null
  cancel_reason: string | null
  created_at: ISODateTime
}

export interface PickupQueueCounts {
  pending: number
  acknowledged: number
  arrived: number
  /** open 中 needs_reply 為 true 的筆數 */
  needs_reply: number
}

export interface PickupQueue {
  date: ISODate
  /** 非終態：arrived 優先，再 pending、acknowledged；同狀態依預計抵達時間（沒有的排最後）、建立時間 */
  open: PickupRequest[]
  /** 終態（completed / cancelled / expired），新到舊 */
  closed: PickupRequest[]
  counts: PickupQueueCounts
}

export interface RosterOpenRequest {
  id: string
  /** 只有非終態 */
  status: PickupOpenStatus
  expected_arrival_at: HHMM | null
  needs_reply: boolean
}

export interface RosterStudent {
  student_id: string
  student_no: string
  name: string
  grade_level: number
  attendance_status: AttendanceStatus | null
  check_in_at: ISODateTime | null
  check_out_at: ISODateTime | null
  /** 出勤為 leave 時才有（代碼 sick / personal / other，不是中文） */
  leave_type: LeaveType | null
  homework_status: HomeworkOverallStatus | null
  ready_eta: HHMM | null
  open_request: RosterOpenRequest | null
  /** 當日 active 的代理接送授權數 */
  active_authorization_count: number
}

export interface RosterClass {
  /** null 為未分班（class_name 固定為「未分班」，排在最後） */
  class_id: string | null
  class_name: string | null
  students: RosterStudent[]
}

export interface PickupRoster {
  date: ISODate
  classes: RosterClass[]
}

export interface PickupRosterParams {
  /** 預設今天 */
  date?: ISODate
  class_id?: string
}

export interface PickupRequestCreate {
  student_id: string
  /** 台北當地時間；不可早於現在、不可晚於 pickup.window 的 latest_expected_arrival；員工代建不受接送時段限制 */
  expected_arrival_at?: HHMM
}

/** reply_ready_eta 與 reply_message 至少要給一個，否則 422；只給 ETA 時後端自動組訊息 */
export interface PickupReply {
  reply_ready_eta?: HHMM
  reply_message?: string
}

/**
 * guardian：記錄由哪位監護人接走，guardian_id 必填（note 不會被使用）；
 * override：主管強制完成，note 必填並寫入稽核紀錄（guardian_id 不會被使用），需要 pickup:override 權限（後端判斷，缺則 403）。
 * 代理接送的完成走 pickupAuthorizations。
 */
export type PickupCompleteBody =
  | { method: 'guardian'; guardian_id: string }
  | { method: 'override'; note: string }

/** 409 invalid_pickup_status 的 details：請求目前的狀態（例如已被別人完成 / 取消） */
export interface InvalidPickupStatusDetails {
  current_status: PickupRequestStatus
}

/** 409 pickup_request_exists 的 details：同一學生當天已有進行中的請求 */
export interface PickupRequestExistsDetails {
  request_id: string
  status: PickupOpenStatus
}

const requestPath = (id: string) => `/admin/pickup/requests/${encodeURIComponent(id)}`

/** 空白原因視為沒有 */
function reasonBody(reason?: string): { reason?: string } {
  return reason?.trim() ? { reason } : {}
}

/** 沒給日期時不帶 date，由後端以今天為準 */
export async function fetchPickupQueue(date?: ISODate): Promise<PickupQueue> {
  const res = await adminHttp.get<PickupQueue>('/admin/pickup/queue', { params: { date } })
  return res.data
}

export async function fetchPickupRoster(params: PickupRosterParams = {}): Promise<PickupRoster> {
  const res = await adminHttp.get<PickupRoster>('/admin/pickup/roster', { params })
  return res.data
}

/** 員工代建（source = staff） */
export async function createPickupRequest(body: PickupRequestCreate): Promise<PickupRequest> {
  const res = await adminHttp.post<PickupRequest>('/admin/pickup/requests', body)
  return res.data
}

/** 員工覆寫回覆（reply_source = staff）；有 reply_ready_eta 時後端同步寫回該生作業進度的 ready_eta */
export async function replyPickupRequest(id: string, body: PickupReply): Promise<PickupRequest> {
  const res = await adminHttp.post<PickupRequest>(`${requestPath(id)}/reply`, body)
  return res.data
}

/** pending → acknowledged，不帶 body */
export async function acknowledgePickupRequest(id: string): Promise<PickupRequest> {
  const res = await adminHttp.post<PickupRequest>(`${requestPath(id)}/acknowledge`)
  return res.data
}

/** 交付學生：pending / acknowledged / arrived 皆可直接完成，出勤自動改 left */
export async function completePickupRequest(id: string, body: PickupCompleteBody): Promise<PickupRequest> {
  const res = await adminHttp.post<PickupRequest>(`${requestPath(id)}/complete`, body)
  return res.data
}

/** 無原因時 body 為 {} */
export async function cancelPickupRequest(id: string, reason?: string): Promise<PickupRequest> {
  const res = await adminHttp.post<PickupRequest>(`${requestPath(id)}/cancel`, reasonBody(reason))
  return res.data
}
