// PARENT-130：家長端月出勤 api client（BACKEND-323）。
// 型別逐欄對照 apps/api/app/schemas/attendance.py 的 ParentMonthlyAttendanceOut；`*_at` 為 UTC ISO8601。
import type { AttendanceStatus, ISODate, ISODateTime, LeaveType } from '@/shared/types/api'
import { parentHttp } from './http'

export interface AttendanceDay {
  date: ISODate
  is_service_day: boolean
  /** 該日沒有出勤列時為 null（非營業日、營業日尚未建列） */
  status: AttendanceStatus | null
  check_in_at: ISODateTime | null
  check_out_at: ISODateTime | null
  /** 只有 status 為 leave 時有值 */
  leave_type: LeaveType | null
}

export interface MonthlyAttendance {
  student_id: string
  /** YYYY-MM */
  month: string
  days: AttendanceDay[]
  stats: {
    service_days: number
    attended: number
    absent: number
    leave: number
    unrecorded: number
  }
}

/** month 為 YYYY-MM，格式由後端驗證（錯誤回 422） */
export async function getChildAttendance(childId: string, month: string): Promise<MonthlyAttendance> {
  const res = await parentHttp.get<MonthlyAttendance>(`/parent/children/${childId}/attendance`, {
    params: { month },
  })
  return res.data
}
