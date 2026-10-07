// FRONTEND-120：出勤 api client（BACKEND-315~322，docs/tasks/api_index.md §7）。
// 移植 ivy FE:src/api/studentAttendance.ts 的函式分組；改為本專案逐生操作 endpoint。
// 到班 / 離班 / 標缺席一律以伺服器時間（不接受 client 指定時間），補登與更正走 amendAttendance。
// 錯誤由 adminHttp 轉成 ApiError：409 already_checked_in / already_checked_out / not_checked_in / student_on_leave /
// not_service_day / student_not_active / attendance_managed_by_leave，422 check_in_required / check_out_required /
// invalid_times / time_not_on_service_date / no_changes。
import type { AxiosResponse } from 'axios'
import type {
  AttendanceStatus,
  CheckInSource,
  CheckOutSource,
  ISODate,
  ISODateTime,
  LeaveType,
} from '@/shared/types/api'
import { adminHttp } from './http'

/** 出勤列上的請假摘要（status 為 leave 時才有） */
export interface AttendanceLeaveBrief {
  id: string
  leave_type: LeaveType
  leave_type_label: string
  start_date: ISODate
  end_date: ISODate
}

export interface AttendanceRow {
  /** 營業日尚未建立出勤列的學生以虛擬列呈現：id 為 null、status 為 expected */
  id: string | null
  student_id: string
  student_no: string
  student_name: string
  grade_level: number
  class_id: string | null
  class_name: string | null
  service_date: ISODate
  status: AttendanceStatus
  check_in_at: ISODateTime | null
  check_in_source: CheckInSource | null
  check_out_at: ISODateTime | null
  check_out_source: CheckOutSource | null
  leave: AttendanceLeaveBrief | null
  note: string | null
  /** 虛擬列為 null */
  updated_at: ISODateTime | null
}

export interface DailyAttendanceParams {
  /** 預設今天 */
  date?: ISODate
  class_id?: string
  /** 只篩 items，summary 不受影響 */
  status?: AttendanceStatus
}

export interface DailySummary {
  total: number
  expected: number
  present: number
  left: number
  absent: number
  leave: number
}

export interface DailyAttendance {
  date: ISODate
  /** 非營業日不會有虛擬列 */
  is_service_day: boolean
  /** 以篩選前的全部列計算 */
  summary: DailySummary
  items: AttendanceRow[]
}

export interface BatchCheckInSkip {
  student_id: string
  /** student_not_found / student_not_active / already_checked_in / already_checked_out / student_on_leave */
  code: string
  /** 繁中說明，可直接顯示 */
  message: string
}

export interface BatchCheckInResult {
  /** 順序與送出的 student_ids 一致 */
  succeeded: AttendanceRow[]
  skipped: BatchCheckInSkip[]
}

/** 改判可設定的狀態；leave 只能由請假的建立 / 取消改變 */
export type AmendableAttendanceStatus = Exclude<AttendanceStatus, 'leave'>

/** 除 reason 外至少要改一個欄位，否則 422 */
export interface AttendanceAmend {
  /** 省略 = 沿用原狀態；改成 expected / absent 會清空到離班時間 */
  status?: AmendableAttendanceStatus
  /** 省略 = 沿用原值，null = 清空；時間必須落在該出勤日（台北）當天 */
  check_in_at?: ISODateTime | null
  check_out_at?: ISODateTime | null
  note?: string | null
  /** 改判原因（寫入稽核紀錄），1~200 字 */
  reason: string
}

export interface MonthlyAttendanceParams {
  /** YYYY-MM */
  month: string
  class_id?: string
}

export interface MonthDay {
  date: ISODate
  /** 0 = 週一 */
  weekday: number
  is_service_day: boolean
}

/** 只計入班日之後、且不晚於今天的營業日；未來日期不計入任何數字 */
export interface MonthlyStats {
  service_days: number
  /** present + left */
  attended: number
  absent: number
  leave: number
  /** status 為 expected 或沒有出勤列 */
  unrecorded: number
}

export interface MonthlyStudentRow {
  student_id: string
  student_no: string
  name: string
  class_name: string | null
  /** 與 days 等長、同順序；null = 非營業日或沒有紀錄 */
  statuses: (AttendanceStatus | null)[]
  stats: MonthlyStats
}

export interface MonthlyAttendance {
  month: string
  class_id: string | null
  class_name: string | null
  /** 當月每一天（含非營業日） */
  days: MonthDay[]
  students: MonthlyStudentRow[]
  /** 全部學生 stats 的加總：service_days 是各生應出勤營業日數的加總（應出勤人次），不是當月營業日數 */
  totals: MonthlyStats
}

const attendancePath = (id: string) => `/admin/attendance/${encodeURIComponent(id)}`

/** 空白備註視為沒有：後端以 coalesce(:note, note) 寫入，送空字串會把既有備註蓋成空白 */
function noteBody(note?: string): { note?: string } {
  return note?.trim() ? { note } : {}
}

export async function fetchDailyAttendance(params: DailyAttendanceParams = {}): Promise<DailyAttendance> {
  const res = await adminHttp.get<DailyAttendance>('/admin/attendance/daily', { params })
  return res.data
}

/** 無備註時 body 為 {} */
export async function checkIn(studentId: string, note?: string): Promise<AttendanceRow> {
  const res = await adminHttp.post<AttendanceRow>(`${attendancePath(studentId)}/check-in`, noteBody(note))
  return res.data
}

export async function checkOut(studentId: string, note?: string): Promise<AttendanceRow> {
  const res = await adminHttp.post<AttendanceRow>(`${attendancePath(studentId)}/check-out`, noteBody(note))
  return res.data
}

/** 1~200 位、不可重複；部分略過仍回 200（見 skipped），非營業日整批 409 not_service_day */
export async function batchCheckIn(studentIds: string[]): Promise<BatchCheckInResult> {
  const res = await adminHttp.post<BatchCheckInResult>('/admin/attendance/batch-check-in', {
    student_ids: studentIds,
  })
  return res.data
}

/** 冪等：已是缺席原樣回傳 */
export async function markAbsent(studentId: string, note?: string): Promise<AttendanceRow> {
  const res = await adminHttp.post<AttendanceRow>(`${attendancePath(studentId)}/mark-absent`, noteBody(note))
  return res.data
}

export async function amendAttendance(attendanceId: string, body: AttendanceAmend): Promise<AttendanceRow> {
  const res = await adminHttp.patch<AttendanceRow>(attendancePath(attendanceId), body)
  return res.data
}

export async function fetchMonthlyAttendance(params: MonthlyAttendanceParams): Promise<MonthlyAttendance> {
  const res = await adminHttp.get<MonthlyAttendance>('/admin/attendance/monthly', { params })
  return res.data
}

/**
 * 回完整 response：呼叫端以 FRONTEND-007 的 saveBlobResponse 依 Content-Disposition 取檔名。
 * 404 class_not_found 等錯誤的 JSON blob 已由 adminHttp 正規化成 ApiError。
 */
export async function exportMonthlyAttendance(params: MonthlyAttendanceParams): Promise<AxiosResponse<Blob>> {
  return adminHttp.get<Blob>('/admin/attendance/monthly/export', { params, responseType: 'blob' })
}
