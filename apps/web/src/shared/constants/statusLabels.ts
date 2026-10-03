// FRONTEND-005：各狀態值的中文標籤與顏色語意。後台 Element Plus tag 與家長端 M3 chip 共用。
// 值域取 FRONTEND-002 的 enum；完整性由 statusLabels.spec.ts 檢查。
import type {
  AttendanceStatus,
  AuthorizationEffectiveStatus,
  BindingStatus,
  CheckInSource,
  CheckOutSource,
  ClassStaffRole,
  ExamStatus,
  Gender,
  GuardianRelation,
  HomeworkItemStatus,
  HomeworkOverallStatus,
  LeaveStatus,
  LeaveType,
  NotificationEvent,
  PickupCompletionMethod,
  PickupRequestStatus,
  PickupSource,
  StudentStatus,
} from '@/shared/types/api'

export type Tone = 'success' | 'warning' | 'danger' | 'info'

export interface StatusMeta {
  label: string
  tone: Tone
}

export const ATTENDANCE_STATUS_META: Record<AttendanceStatus, StatusMeta> = {
  expected: { label: '預計到班', tone: 'warning' },
  present: { label: '已到班', tone: 'success' },
  left: { label: '已離班', tone: 'info' },
  absent: { label: '缺席', tone: 'danger' },
  leave: { label: '請假', tone: 'info' },
}

export const CHECK_IN_SOURCE_LABELS: Record<CheckInSource, string> = {
  manual: '手動',
  nfc: '刷卡',
}

export const CHECK_OUT_SOURCE_LABELS: Record<CheckOutSource, string> = {
  manual: '手動',
  pickup: '接送完成',
  nfc: '刷卡',
}

export const LEAVE_TYPE_META: Record<LeaveType, StatusMeta> = {
  sick: { label: '病假', tone: 'warning' },
  personal: { label: '事假', tone: 'info' },
  other: { label: '其他', tone: 'info' },
}

export const LEAVE_STATUS_META: Record<LeaveStatus, StatusMeta> = {
  active: { label: '生效中', tone: 'success' },
  cancelled: { label: '已取消', tone: 'info' },
}

export const HOMEWORK_ITEM_STATUS_META: Record<HomeworkItemStatus, StatusMeta> = {
  todo: { label: '未開始', tone: 'info' },
  doing: { label: '進行中', tone: 'warning' },
  correcting: { label: '訂正中', tone: 'danger' },
  done: { label: '完成', tone: 'success' },
}

export const HOMEWORK_OVERALL_STATUS_META: Record<HomeworkOverallStatus, StatusMeta> = {
  not_started: { label: '尚未開始', tone: 'info' },
  in_progress: { label: '進行中', tone: 'warning' },
  done: { label: '已完成', tone: 'success' },
}

export const PICKUP_REQUEST_STATUS_META: Record<PickupRequestStatus, StatusMeta> = {
  pending: { label: '待回覆', tone: 'warning' },
  acknowledged: { label: '已確認', tone: 'info' },
  arrived: { label: '家長已到', tone: 'danger' },
  completed: { label: '已接走', tone: 'success' },
  cancelled: { label: '已取消', tone: 'info' },
  expired: { label: '已逾時', tone: 'info' },
}

export const PICKUP_SOURCE_LABELS: Record<PickupSource, string> = {
  parent: '家長發起',
  staff: '員工代建',
  proxy: '代理接送',
}

export const PICKUP_COMPLETION_METHOD_LABELS: Record<PickupCompletionMethod, string> = {
  guardian: '監護人接送',
  code: '接送碼核驗',
  visual_match: '目視核對',
  override: '強制完成',
}

/** 以回應的 effective_status 查（過期日的 active 為 expired） */
export const AUTHORIZATION_STATUS_META: Record<AuthorizationEffectiveStatus, StatusMeta> = {
  active: { label: '有效', tone: 'success' },
  completed: { label: '已接送', tone: 'info' },
  cancelled: { label: '已取消', tone: 'info' },
  expired: { label: '已過期', tone: 'info' },
}

export const EXAM_STATUS_META: Record<ExamStatus, StatusMeta> = {
  draft: { label: '草稿', tone: 'info' },
  published: { label: '已發布', tone: 'success' },
}

export const STUDENT_STATUS_META: Record<StudentStatus, StatusMeta> = {
  active: { label: '在學', tone: 'success' },
  suspended: { label: '暫停', tone: 'warning' },
  withdrawn: { label: '退班', tone: 'info' },
}

export const GENDER_LABELS: Record<Gender, string> = {
  male: '男',
  female: '女',
  other: '其他',
}

export const GUARDIAN_RELATION_LABELS: Record<GuardianRelation, string> = {
  father: '父親',
  mother: '母親',
  grandparent: '祖父母',
  other: '其他',
}

export const BINDING_STATUS_META: Record<BindingStatus, StatusMeta> = {
  bound: { label: '已綁定', tone: 'success' },
  code_issued: { label: '已發綁定碼', tone: 'warning' },
  unbound: { label: '未綁定', tone: 'info' },
}

export const CLASS_STAFF_ROLE_LABELS: Record<ClassStaffRole, string> = {
  lead: '負責老師',
  assistant: '協助老師',
}

/** 與後端 app/notifications/events.py 的 label 一致 */
export const NOTIFICATION_EVENT_LABELS: Record<NotificationEvent, string> = {
  'attendance.checked_in': '到班通知',
  'attendance.checked_out': '離班通知',
  'leave.created': '學生請假',
  'leave.cancelled': '學生取消請假',
  'homework.eta_updated': '預計可接送時間',
  'homework.done': '作業完成',
  'pickup.requested': '家長發起接送',
  'pickup.replied': '老師回覆接送',
  'pickup.arrived': '家長已抵達',
  'pickup.completed': '接送完成',
  'pickup.cancelled': '接送取消',
  'exam.published': '成績公布',
  'binding.completed': '綁定完成',
}

/** 查狀態標籤；未知值（後端新增而前端未同步）回 `{ label: 值 ?? '—', tone: 'info' }`，不 throw。 */
export function statusMeta<K extends string>(
  map: Record<K, StatusMeta>,
  value: string | null | undefined,
): StatusMeta {
  if (value != null && Object.hasOwn(map, value)) return map[value as K]
  return { label: value ?? '—', tone: 'info' }
}
