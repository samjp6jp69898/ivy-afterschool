// PARENT-006：家長端個人資料與小孩 api client（BACKEND-064 / 184 / 185 / 526）。
// 型別逐欄對照 apps/api/app/schemas/parent_children.py；`*_at` 為 UTC ISO8601，HH:MM 為台北當地時間。
import type {
  AttendanceStatus,
  GuardianRelation,
  HHMM,
  HomeworkOverallStatus,
  ISODate,
  ISODateTime,
  LeaveType,
  PickupReplySource,
  PickupRequestStatus,
  StudentStatus,
} from '@/shared/types/api'
import { parentHttp } from './http'

export interface ChildSummary {
  id: string
  name: string
  grade_level: number
  class_name: string | null
  /** 後端以 short_name 優先 */
  school_name: string | null
  photo_url: string | null
  status: StudentStatus
}

export interface ChildDetail extends ChildSummary {
  school_class: string | null
  enrolled_on: ISODate | null
  /** 目前登入家長自己那筆監護人設定 */
  my_guardian: {
    relation: GuardianRelation
    is_primary: boolean
    can_pickup: boolean
    receives_notifications: boolean
  }
}

export interface ParentMe {
  id: string
  display_name: string | null
  picture_url: string | null
  phone: string | null
  children: ChildSummary[]
}

export interface ChildToday {
  student_id: string
  date: ISODate
  is_service_day: boolean
  /** 營業日尚無出勤列時 status 為 expected，非營業日為 null */
  attendance: {
    status: AttendanceStatus | null
    check_in_at: ISODateTime | null
    check_out_at: ISODateTime | null
  }
  on_leave: boolean
  leave: {
    id: string
    leave_type: LeaveType
    leave_type_label: string
    start_date: ISODate
    end_date: ISODate
  } | null
  homework: {
    item_count: number
    done_count: number
    overall_status: HomeworkOverallStatus
    ready_eta: HHMM | null
    note: string | null
  }
  /** 優先取今日非終態那筆，否則取今日最新一筆 */
  pickup_request: {
    id: string
    status: PickupRequestStatus
    expected_arrival_at: HHMM | null
    reply_ready_eta: HHMM | null
    reply_message: string | null
    reply_source: PickupReplySource | null
    completed_at: ISODateTime | null
    picked_up_by_name: string | null
    can_cancel: boolean
    can_mark_arrived: boolean
  } | null
}

export async function getMe(): Promise<ParentMe> {
  const res = await parentHttp.get<ParentMe>('/parent/me')
  return res.data
}

export async function listChildren(): Promise<ChildSummary[]> {
  const res = await parentHttp.get<ChildSummary[]>('/parent/children')
  return res.data
}

export async function getChild(id: string): Promise<ChildDetail> {
  const res = await parentHttp.get<ChildDetail>(`/parent/children/${id}`)
  return res.data
}

/** 首頁今日狀態卡：出勤、請假、作業進度與今日接送請求的聚合 */
export async function getChildToday(id: string): Promise<ChildToday> {
  const res = await parentHttp.get<ChildToday>(`/parent/children/${id}/today`)
  return res.data
}
