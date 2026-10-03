// FRONTEND-002：後台與家長端共用的 API 型別（docs/tasks/api_index.md 共通約定）。
// 只放兩端共用的型別；後台各資源 response 型別放各自的 api client，家長端型別放 src/parent/api/。
// enum 值域與順序照 docs/domain_spec.md §1，以 as const 陣列定義、導出對應型別。

export interface Page<T> {
  items: T[]
  total: number
}

export interface ApiErrorBody {
  error: { code: string; message: string; details: unknown }
}

/** 422 `validation_error` 的 details 項目 */
export interface ValidationErrorItem {
  loc: (string | number)[]
  msg: string
  type: string
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: unknown

  constructor(status: number, code: string, message: string, details: unknown = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError
}

/** 台北當地時間 `HH:MM` */
export type HHMM = string
/** `YYYY-MM-DD` */
export type ISODate = string
/** UTC ISO8601 */
export type ISODateTime = string

export interface WsEnvelope<T = unknown> {
  type: string
  data: T
  sent_at: ISODateTime
}

export const ATTENDANCE_STATUSES = ['expected', 'present', 'left', 'absent', 'leave'] as const
export type AttendanceStatus = (typeof ATTENDANCE_STATUSES)[number]

export const CHECK_IN_SOURCES = ['manual', 'nfc'] as const
export type CheckInSource = (typeof CHECK_IN_SOURCES)[number]

export const CHECK_OUT_SOURCES = ['manual', 'pickup', 'nfc'] as const
export type CheckOutSource = (typeof CHECK_OUT_SOURCES)[number]

export const LEAVE_TYPES = ['sick', 'personal', 'other'] as const
export type LeaveType = (typeof LEAVE_TYPES)[number]

export const LEAVE_STATUSES = ['active', 'cancelled'] as const
export type LeaveStatus = (typeof LEAVE_STATUSES)[number]

export const ACTOR_CREATED_BY_TYPES = ['parent', 'staff'] as const
export type ActorCreatedByType = (typeof ACTOR_CREATED_BY_TYPES)[number]

export const HOMEWORK_ITEM_STATUSES = ['todo', 'doing', 'correcting', 'done'] as const
export type HomeworkItemStatus = (typeof HOMEWORK_ITEM_STATUSES)[number]

export const HOMEWORK_OVERALL_STATUSES = ['not_started', 'in_progress', 'done'] as const
export type HomeworkOverallStatus = (typeof HOMEWORK_OVERALL_STATUSES)[number]

export const PICKUP_REQUEST_STATUSES = [
  'pending',
  'acknowledged',
  'arrived',
  'completed',
  'cancelled',
  'expired',
] as const
export type PickupRequestStatus = (typeof PICKUP_REQUEST_STATUSES)[number]

export const PICKUP_OPEN_STATUSES = [
  'pending',
  'acknowledged',
  'arrived',
] as const satisfies readonly PickupRequestStatus[]
export type PickupOpenStatus = (typeof PICKUP_OPEN_STATUSES)[number]

export const PICKUP_TERMINAL_STATUSES = [
  'completed',
  'cancelled',
  'expired',
] as const satisfies readonly PickupRequestStatus[]
export type PickupTerminalStatus = (typeof PICKUP_TERMINAL_STATUSES)[number]

export const PICKUP_SOURCES = ['parent', 'staff', 'proxy'] as const
export type PickupSource = (typeof PICKUP_SOURCES)[number]

export const PICKUP_REPLY_SOURCES = ['auto', 'staff'] as const
export type PickupReplySource = (typeof PICKUP_REPLY_SOURCES)[number]

export const PICKUP_COMPLETION_METHODS = ['guardian', 'code', 'visual_match', 'override'] as const
export type PickupCompletionMethod = (typeof PICKUP_COMPLETION_METHODS)[number]

export const AUTHORIZATION_STATUSES = ['active', 'completed', 'cancelled'] as const
export type AuthorizationStatus = (typeof AUTHORIZATION_STATUSES)[number]

/** 回應的 effective_status：過期日的 active 顯示 expired */
export const AUTHORIZATION_EFFECTIVE_STATUSES = [
  'active',
  'completed',
  'cancelled',
  'expired',
] as const
export type AuthorizationEffectiveStatus = (typeof AUTHORIZATION_EFFECTIVE_STATUSES)[number]

export const VERIFICATION_METHODS = ['code', 'visual_match', 'override'] as const
export type VerificationMethod = (typeof VERIFICATION_METHODS)[number]

export const EXAM_STATUSES = ['draft', 'published'] as const
export type ExamStatus = (typeof EXAM_STATUSES)[number]

export const STUDENT_STATUSES = ['active', 'suspended', 'withdrawn'] as const
export type StudentStatus = (typeof STUDENT_STATUSES)[number]

export const GENDERS = ['male', 'female', 'other'] as const
export type Gender = (typeof GENDERS)[number]

export const GUARDIAN_RELATIONS = ['father', 'mother', 'grandparent', 'other'] as const
export type GuardianRelation = (typeof GUARDIAN_RELATIONS)[number]

export const BINDING_STATUSES = ['bound', 'code_issued', 'unbound'] as const
export type BindingStatus = (typeof BINDING_STATUSES)[number]

export const CLASS_STAFF_ROLES = ['lead', 'assistant'] as const
export type ClassStaffRole = (typeof CLASS_STAFF_ROLES)[number]

/** domain_spec M9 的 13 個通知事件 */
export const NOTIFICATION_EVENTS = [
  'attendance.checked_in',
  'attendance.checked_out',
  'leave.created',
  'leave.cancelled',
  'homework.eta_updated',
  'homework.done',
  'pickup.requested',
  'pickup.replied',
  'pickup.arrived',
  'pickup.completed',
  'pickup.cancelled',
  'exam.published',
  'binding.completed',
] as const
export type NotificationEvent = (typeof NOTIFICATION_EVENTS)[number]

export interface Notification {
  id: string
  event: NotificationEvent
  title: string
  body: string
  payload: Record<string, unknown>
  read_at: ISODateTime | null
  created_at: ISODateTime
  /** 前端 hash 路由 */
  deep_link: string | null
}

export interface NotificationPage {
  items: Notification[]
  total: number
  unread_count: number
}
