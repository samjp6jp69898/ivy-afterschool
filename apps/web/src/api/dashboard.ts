// FRONTEND-260：今日儀表板 api client（BACKEND-492）。
import type { ActorCreatedByType, ISODate, ISODateTime } from '@/shared/types/api'
import { adminHttp } from './http'

export interface DashboardAttendanceCounts {
  expected_total: number
  /** present + left */
  arrived: number
  present: number
  left: number
  not_arrived: number
  leave: number
  absent: number
}

export interface DashboardPickupCounts {
  /** 非終態 */
  open: number
  needs_reply: number
  /** 等待交付 */
  arrived: number
  completed: number
}

export interface DashboardHomeworkCounts {
  total: number
  done: number
  in_progress: number
  not_started: number
  /** 0~1；total 為 0 時 0 */
  completion_rate: number
}

export interface DashboardRecentLeave {
  id: string
  student_id: string
  student_name: string
  class_name: string | null
  leave_type_label: string
  start_date: ISODate
  end_date: ISODate
  created_by_type: ActorCreatedByType
  created_at: ISODateTime
}

export interface DashboardToday {
  date: ISODate
  is_service_day: boolean
  attendance: DashboardAttendanceCounts
  pickup: DashboardPickupCounts
  homework: DashboardHomeworkCounts
  /** 最多 10 筆 */
  recent_leaves: DashboardRecentLeave[]
}

export async function fetchDashboardToday(): Promise<DashboardToday> {
  const res = await adminHttp.get<DashboardToday>('/admin/dashboard/today')
  return res.data
}
